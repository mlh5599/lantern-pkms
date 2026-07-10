"""Idempotent, human-edit-safe writer for Lantern vault notes.

This is the highest-risk correctness surface in lantern-pkms (see the plan's "Human-edit
safety (ownership handoff)" section). Core guarantees:

- A line is never overwritten once a human has edited it (first divergence from what
  the system last wrote permanently hands that line's ownership to the human).
- A line the system wrote is never resurrected once a human deletes it.
- Files are located by frontmatter content (lantern_pkms.source_notes), not a trusted
  stored path, so renaming/reorganizing a file in Obsidian doesn't break tracking.
- Frontmatter is merged under a single `lantern_pkms:` namespace key; every other
  frontmatter field is exclusively the human's and is never inspected or altered.
- A real conflict (human edited a line, and the source later changed too) is flagged
  under "Needs Review" rather than silently dropped — once per new divergence.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import yaml

from lantern_pkms.state.db import (
    STATUS_SYSTEM_OWNED,
    STATUS_USER_DELETED,
    STATUS_USER_MODIFIED,
    StateDB,
    VaultEntryRecord,
)

FRONTMATTER_KEY = "lantern_pkms"
BEGIN_MARK = "<!-- lantern-pkms:begin -->"
END_MARK = "<!-- lantern-pkms:end -->"

SECTION_ORDER = ["Tasks", "Events", "Notes", "Mood", "Needs Review"]
_SECTION_HEADERS = {s: ("## ⚠️ Needs Review" if s == "Needs Review" else f"## {s}") for s in SECTION_ORDER}
_HEADER_TO_SECTION = {v: k for k, v in _SECTION_HEADERS.items()}

_BLOCK_REF_RE = re.compile(r"\^([A-Za-z0-9_-]+)\s*$")
_FRONTMATTER_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n?", re.DOTALL)


@dataclass
class RenderedLine:
    """One entry ready to be merged into a vault file for a given page."""

    block_id: str
    section: str  # one of SECTION_ORDER
    text: str  # markdown line content WITHOUT the trailing " ^block_id"
    entry_type: str
    entry_index: int


@dataclass
class ManagedLine:
    section: str
    text: str  # full rendered line, including " ^block_id"


@dataclass
class ParsedNote:
    frontmatter: dict
    pre_text: str
    post_text: str
    lines_by_block: dict[str, ManagedLine] = field(default_factory=dict)


@dataclass
class SyncOutcome:
    created: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    locked_unchanged: list[str] = field(default_factory=list)
    flagged_conflicts: list[str] = field(default_factory=list)
    skipped_deleted: list[str] = field(default_factory=list)
    resolved_path: str = ""
    created_file: bool = False


def block_ref(block_id: str) -> str:
    return f"^{block_id}"


# --------------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------------


def _split_frontmatter(text: str) -> tuple[dict, str]:
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return {}, text
    raw = m.group(1)
    body = text[m.end() :]
    data = yaml.safe_load(raw) or {}
    if not isinstance(data, dict):
        data = {}
    return data, body


def _extract_managed_region(body: str) -> tuple[str, str, str]:
    begin = body.find(BEGIN_MARK)
    end = body.find(END_MARK)
    if begin == -1 or end == -1 or end < begin:
        # No managed region yet — treat entire body as "post" content so a fresh
        # managed region gets inserted at the top, above any existing human content.
        return "", "", body
    pre = body[:begin]
    managed = body[begin + len(BEGIN_MARK) : end]
    post = body[end + len(END_MARK) :]
    return pre, managed, post


def parse_note(text: str) -> ParsedNote:
    frontmatter, body = _split_frontmatter(text)
    pre, managed_text, post = _extract_managed_region(body)

    lines_by_block: dict[str, ManagedLine] = {}
    section = SECTION_ORDER[0]
    for raw_line in managed_text.splitlines():
        stripped = raw_line.strip()
        if stripped in _HEADER_TO_SECTION:
            section = _HEADER_TO_SECTION[stripped]
            continue
        if not stripped:
            continue
        m = _BLOCK_REF_RE.search(stripped)
        if m:
            lines_by_block[m.group(1)] = ManagedLine(section=section, text=raw_line.rstrip())

    return ParsedNote(frontmatter=frontmatter, pre_text=pre, post_text=post, lines_by_block=lines_by_block)


# --------------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------------


def _render_managed_region(lines_by_block: dict[str, ManagedLine]) -> str:
    by_section: dict[str, list[str]] = {s: [] for s in SECTION_ORDER}
    for line in lines_by_block.values():
        by_section.setdefault(line.section, []).append(line.text)

    parts = [BEGIN_MARK]
    for section in SECTION_ORDER:
        texts = by_section.get(section) or []
        if not texts:
            continue
        parts.append(_SECTION_HEADERS[section])
        parts.extend(texts)
        parts.append("")
    if parts[-1] == "":
        parts.pop()
    parts.append(END_MARK)
    return "\n".join(parts)


def _render_note(parsed: ParsedNote, now_iso: str) -> str:
    frontmatter = dict(parsed.frontmatter)
    lantern_pkms_meta = dict(frontmatter.get(FRONTMATTER_KEY) or {})
    lantern_pkms_meta["last_synced"] = now_iso
    frontmatter[FRONTMATTER_KEY] = lantern_pkms_meta

    fm_yaml = yaml.safe_dump(frontmatter, sort_keys=False, allow_unicode=True).strip()
    managed = _render_managed_region(parsed.lines_by_block)

    pre = parsed.pre_text.strip("\n")
    post = parsed.post_text.strip("\n")

    sections = [f"---\n{fm_yaml}\n---"]
    if pre:
        sections.append(pre)
    sections.append(managed)
    if post:
        sections.append(post)
    return "\n\n".join(sections) + "\n"


def render_conflict_note(block_id: str, new_source_text: str) -> str:
    return f"- Source changed after you edited `^{block_id}` — new source text: “{new_source_text}”"


# --------------------------------------------------------------------------------
# File location (by content, not trusted path — see module docstring)
# --------------------------------------------------------------------------------


def locate_existing_file(vault_root: Path, note_id: str) -> Path | None:
    """Find the vault file whose frontmatter already references this note_id, if any.

    Cheap full-vault frontmatter scan — acceptable at vault scale (a few thousand
    files, once per run). Returns None if no file references this note yet.
    """
    for path in vault_root.rglob("*.md"):
        try:
            text = path.read_text()
        except OSError:
            continue
        frontmatter, _ = _split_frontmatter(text)
        lantern_pkms_meta = frontmatter.get(FRONTMATTER_KEY) or {}
        for source in lantern_pkms_meta.get("source_notes") or []:
            if str(source.get("supernote_id")) == str(note_id):
                return path
    return None


# --------------------------------------------------------------------------------
# Core sync
# --------------------------------------------------------------------------------


def _new_note_template() -> str:
    return f"---\n{FRONTMATTER_KEY}: {{}}\n---\n\n{BEGIN_MARK}\n{END_MARK}\n"


def _add_source_note_ref(parsed: ParsedNote, note_id: str, page_number: int) -> None:
    meta = dict(parsed.frontmatter.get(FRONTMATTER_KEY) or {})
    sources = list(meta.get("source_notes") or [])
    for source in sources:
        if str(source.get("supernote_id")) == str(note_id):
            pages = set(source.get("pages") or [])
            pages.add(page_number)
            source["pages"] = sorted(pages)
            break
    else:
        sources.append({"supernote_id": note_id, "pages": [page_number]})
    meta["source_notes"] = sources
    parsed.frontmatter[FRONTMATTER_KEY] = meta


def _ensure_source_image_embed(parsed: ParsedNote, image_rel_path: str) -> None:
    embed = f"![[{image_rel_path}]]"
    if embed in parsed.post_text:
        return
    if "## Source Pages" not in parsed.post_text:
        parsed.post_text = (parsed.post_text.rstrip("\n") + "\n\n## Source Pages\n").lstrip("\n")
    parsed.post_text = parsed.post_text.rstrip("\n") + f"\n{embed}\n"


def sync_page(
    vault_root: Path,
    default_rel_path: str,
    note_id: str,
    page_id: str,
    page_number: int,
    entry_date: str | None,
    category: str,
    lines: list[RenderedLine],
    state: StateDB,
    now_iso: str | None = None,
    source_image_rel_path: str | None = None,
) -> SyncOutcome:
    """Merge freshly transcribed lines for one page into its target vault file.

    Never overwrites a human-edited line (see module docstring). Safe to call
    repeatedly with the same or updated `lines` — that's the whole point.
    """
    now_iso = now_iso or datetime.now().astimezone().isoformat()

    existing_path = locate_existing_file(vault_root, note_id)
    resolved_path = existing_path or (vault_root / default_rel_path)
    created_file = not resolved_path.exists()

    text = resolved_path.read_text() if not created_file else _new_note_template()
    parsed = parse_note(text)

    outcome = SyncOutcome(resolved_path=str(resolved_path.relative_to(vault_root)), created_file=created_file)

    for line in lines:
        rendered_text = f"{line.text} {block_ref(line.block_id)}"
        prior = state.get_vault_entry(line.block_id)
        current = parsed.lines_by_block.get(line.block_id)

        if current is None and prior is None:
            parsed.lines_by_block[line.block_id] = ManagedLine(section=line.section, text=rendered_text)
            _upsert_state(
                state, line, page_id, entry_date, category, rendered_text,
                STATUS_SYSTEM_OWNED, resolved_path, vault_root, now_iso,
                last_seen_source_text=rendered_text,
            )
            outcome.created.append(line.block_id)
            continue

        if current is None and prior is not None:
            if prior.status != STATUS_USER_DELETED:
                _upsert_state(
                    state, line, page_id, entry_date, category, prior.last_written_text,
                    STATUS_USER_DELETED, resolved_path, vault_root, now_iso,
                    last_seen_source_text=prior.last_seen_source_text,
                )
            outcome.skipped_deleted.append(line.block_id)
            continue

        still_system_owned = prior is not None and current.text == prior.last_written_text

        if still_system_owned:
            parsed.lines_by_block[line.block_id] = ManagedLine(section=line.section, text=rendered_text)
            _upsert_state(
                state, line, page_id, entry_date, category, rendered_text,
                STATUS_SYSTEM_OWNED, resolved_path, vault_root, now_iso,
                last_seen_source_text=rendered_text,
            )
            outcome.updated.append(line.block_id)
            continue

        # Human has touched this line (current text present and doesn't match what we
        # last wrote) — permanently locked. Flag a genuinely new divergence once.
        prior_last_seen = prior.last_seen_source_text if prior else None
        if rendered_text != prior_last_seen:
            review_block_id = f"{line.block_id}-conflict-{abs(hash(rendered_text)) % 10_000}"
            conflict_line = f"{render_conflict_note(line.block_id, line.text)} {block_ref(review_block_id)}"
            parsed.lines_by_block[review_block_id] = ManagedLine(section="Needs Review", text=conflict_line)
            outcome.flagged_conflicts.append(line.block_id)
        else:
            outcome.locked_unchanged.append(line.block_id)

        _upsert_state(
            state, line, page_id, entry_date, category,
            prior.last_written_text if prior else current.text,
            STATUS_USER_MODIFIED, resolved_path, vault_root, now_iso,
            last_seen_source_text=rendered_text,
        )

    _add_source_note_ref(parsed, note_id, page_number)
    if source_image_rel_path:
        _ensure_source_image_embed(parsed, source_image_rel_path)

    resolved_path.parent.mkdir(parents=True, exist_ok=True)
    resolved_path.write_text(_render_note(parsed, now_iso))

    return outcome


def _upsert_state(
    state: StateDB,
    line: RenderedLine,
    page_id: str,
    entry_date: str | None,
    category: str,
    last_written_text: str | None,
    status: str,
    resolved_path: Path,
    vault_root: Path,
    now_iso: str,
    last_seen_source_text: str | None,
) -> None:
    state.upsert_vault_entry(
        VaultEntryRecord(
            entry_id=line.block_id,
            page_id=page_id,
            entry_index=line.entry_index,
            entry_type=line.entry_type,
            entry_date=entry_date,
            category=category,
            text=line.text,
            symbol_raw="",
            obsidian_note_path=str(resolved_path.relative_to(vault_root)),
            obsidian_block_id=line.block_id,
            needs_review=False,
            updated_at=now_iso,
            status=status,
            last_written_text=last_written_text,
            last_seen_source_text=last_seen_source_text,
        )
    )
