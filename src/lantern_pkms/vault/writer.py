"""Idempotent, human-edit-safe writer for Lantern vault notes.

Whole-file granularity: a vault note is either fully system-owned (byte-identical,
modulo the `last_synced` timestamp, to what the system last wrote) or fully
human-owned. An untouched note is safe to blow away and fully regenerate from every
entry accumulated for its target in SQLite (state/db.py) — it's a projection, not an
incrementally-merged artifact. The moment a note is touched, it's frozen forever:
sync never writes to it again. Instead, new content forks into a new file (e.g.
"Backlog (cont. 1).md") that links back to it, and future syncs write to that new
file — the current "chain tip" — until it too gets edited.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import yaml

from lantern_pkms.state.db import StateDB, TargetRecord, VaultEntryRecord

FRONTMATTER_KEY = "lantern_pkms"

SECTION_ORDER = ["Entries", "Needs Review"]
_SECTION_HEADERS = {"Needs Review": "## ⚠️ Needs Review"}

_FRONTMATTER_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n?", re.DOTALL)


@dataclass
class RenderedLine:
    """One entry ready to be recorded against a target for the page being synced."""

    block_id: str  # stable id, used as the DB key only — not rendered into the file
    section: str  # one of SECTION_ORDER
    text: str  # markdown line content, rendered as-is
    entry_type: str
    entry_index: int


@dataclass
class SyncOutcome:
    resolved_path: str
    created_file: bool = False
    forked: bool = False
    forked_from: str | None = None


# --------------------------------------------------------------------------------
# Frontmatter helpers
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


def _content_hash(text: str) -> str:
    """Hash a rendered file's content for touch-detection, with the auto-updated
    `last_synced` timestamp stripped so its own churn never looks like a human edit."""
    frontmatter, body = _split_frontmatter(text)
    meta = dict(frontmatter.get(FRONTMATTER_KEY) or {})
    meta.pop("last_synced", None)
    frontmatter = dict(frontmatter)
    frontmatter[FRONTMATTER_KEY] = meta
    canonical = yaml.safe_dump(frontmatter, sort_keys=True, allow_unicode=True) + "\x00" + body
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------------
# Rendering — pure projection of accumulated entries for a target
# --------------------------------------------------------------------------------


def render_target_file(
    target_key: str,
    entries: list[VaultEntryRecord],
    contributing_pages: list[tuple[str, int]],
    origin_pages: list[tuple[str, int]],
    now_iso: str,
    continued_from: str | None = None,
) -> str:
    """`contributing_pages` (all pages with any entry landing here, for provenance)
    and `origin_pages` (only pages whose *default* target this is, for source-image
    embeds — a migration destination doesn't get the source page's image) are
    intentionally separate; see state/db.py's get_contributing_pages/get_origin_pages."""
    meta: dict = {"target_key": target_key, "last_synced": now_iso}
    if continued_from:
        meta["continued_from"] = continued_from
    meta["source_notes"] = _render_source_notes(contributing_pages)
    frontmatter = {FRONTMATTER_KEY: meta}
    fm_yaml = yaml.safe_dump(frontmatter, sort_keys=False, allow_unicode=True).strip()

    by_section: dict[str, list[str]] = {s: [] for s in SECTION_ORDER}
    for entry in entries:
        by_section.setdefault(entry.section, []).append(entry.text)

    body_parts: list[str] = []
    if continued_from:
        continued_stem = Path(continued_from).stem
        body_parts.append(f"*Continued from [[{continued_stem}]]*")
    for section in SECTION_ORDER:
        texts = by_section.get(section) or []
        if not texts:
            continue
        header = _SECTION_HEADERS.get(section)
        if header:
            body_parts.append(header)
        body_parts.extend(texts)

    if origin_pages:
        from lantern_pkms.taxonomy import source_page_path

        embeds = "\n".join(f"![[{source_page_path(nid, pn)}]]" for nid, pn in origin_pages)
        body_parts.append(f"## Source Pages\n{embeds}")

    body = "\n\n".join(body_parts)
    sections = [f"---\n{fm_yaml}\n---"]
    if body:
        sections.append(body)
    return "\n\n".join(sections) + "\n"


def _render_source_notes(contributing_pages: list[tuple[str, int]]) -> list[dict]:
    by_note: dict[str, set[int]] = {}
    for note_id, page_number in contributing_pages:
        by_note.setdefault(note_id, set()).add(page_number)
    return [
        {"supernote_id": note_id, "pages": sorted(pages)} for note_id, pages in sorted(by_note.items())
    ]


# --------------------------------------------------------------------------------
# File location
# --------------------------------------------------------------------------------


def _locate_by_frontmatter(vault_root: Path, target_key: str) -> Path | None:
    """Narrow fallback scan: only used when the DB's tip_path is missing on disk, to
    tolerate a rename of an untouched tip (README's "renames don't break tracking").
    """
    for path in vault_root.rglob("*.md"):
        try:
            text = path.read_text()
        except OSError:
            continue
        frontmatter, _ = _split_frontmatter(text)
        meta = frontmatter.get(FRONTMATTER_KEY) or {}
        if meta.get("target_key") == target_key:
            return path
    return None


def _fork_path(vault_root: Path, target_key: str, tip_seq: int) -> tuple[str, int]:
    stem, _, ext = target_key.rpartition(".")
    seq = tip_seq + 1
    while True:
        candidate = f"{stem} (cont. {seq}).{ext}"
        if not (vault_root / candidate).exists():
            return candidate, seq
        seq += 1


def _annotate_forked_from_tip(vault_root: Path, old_rel_path: str, new_rel_path: str) -> None:
    """One-time backlink write into a note that just got forked away from. Safe
    despite "never touch a human-edited file": once forked, no future sync ever
    reads or touch-checks this file's tip_path again."""
    old_abs = vault_root / old_rel_path
    text = old_abs.read_text()
    frontmatter, body = _split_frontmatter(text)
    meta = dict(frontmatter.get(FRONTMATTER_KEY) or {})
    meta["continued_in"] = new_rel_path
    frontmatter = dict(frontmatter)
    frontmatter[FRONTMATTER_KEY] = meta
    fm_yaml = yaml.safe_dump(frontmatter, sort_keys=False, allow_unicode=True).strip()
    new_stem = Path(new_rel_path).stem
    body = body.rstrip("\n") + f"\n\n---\n*Continued in [[{new_stem}]]*\n"
    old_abs.write_text(f"---\n{fm_yaml}\n---\n\n{body.lstrip(chr(10))}")


# --------------------------------------------------------------------------------
# Core sync
# --------------------------------------------------------------------------------


def sync_target(
    vault_root: Path,
    target_key: str,
    category: str,
    entry_date: str | None,
    page_id: str,
    lines: list[RenderedLine],
    state: StateDB,
    now_iso: str | None = None,
) -> SyncOutcome:
    """Record this page's entries for `target_key`, then bring the chain tip's vault
    file up to date: fully regenerate it if untouched, or fork a new tip if a human
    has edited it since the last sync. Safe to call repeatedly (idempotent)."""
    now_iso = now_iso or datetime.now().astimezone().isoformat()

    target = state.get_target(target_key)
    if target is None:
        # vault_entries.target_key is a foreign key into targets(target_key) — the
        # targets row must exist before any vault_entries insert below.
        target = TargetRecord(
            target_key=target_key,
            category=category,
            entry_date=entry_date,
            tip_path=target_key,
            tip_seq=0,
            last_written_hash=None,
            created_at=now_iso,
            updated_at=now_iso,
        )
        state.upsert_target(target)

    entries = [
        VaultEntryRecord(
            entry_id=line.block_id,
            target_key=target_key,
            page_id=page_id,
            entry_index=line.entry_index,
            entry_type=line.entry_type,
            entry_date=entry_date,
            category=category,
            text=line.text,
            symbol_raw="",
            section=line.section,
            updated_at=now_iso,
        )
        for line in lines
    ]
    state.replace_page_entries_for_target(target_key, page_id, entries)

    tip_abs = vault_root / target.tip_path
    touched = False
    forked_from: str | None = None

    if target.last_written_hash is not None:
        if not tip_abs.exists():
            fallback = _locate_by_frontmatter(vault_root, target_key)
            if fallback is not None:
                target.tip_path = str(fallback.relative_to(vault_root))
                tip_abs = fallback
            else:
                touched = True  # can't verify identity — fork rather than guess
        if not touched:
            touched = _content_hash(tip_abs.read_text()) != target.last_written_hash

    created_file = target.last_written_hash is None

    if touched:
        new_rel_path, new_seq = _fork_path(vault_root, target_key, target.tip_seq)
        old_tip_abs = vault_root / target.tip_path
        if old_tip_abs.exists():
            # Only annotate a backlink if the old tip is actually still there — it
            # may be missing entirely (moved/deleted outside Obsidian), in which
            # case there's nothing to annotate.
            _annotate_forked_from_tip(vault_root, target.tip_path, new_rel_path)
            forked_from = target.tip_path
        target.tip_path = new_rel_path
        target.tip_seq = new_seq
        tip_abs = vault_root / target.tip_path
        created_file = True

    all_entries = state.get_vault_entries_for_target(target_key)
    contributing_pages = state.get_contributing_pages(target_key)
    origin_pages = state.get_origin_pages(target_key)

    rendered = render_target_file(
        target_key=target_key,
        entries=all_entries,
        contributing_pages=contributing_pages,
        origin_pages=origin_pages,
        now_iso=now_iso,
        continued_from=forked_from if touched else _current_continued_from(target, vault_root),
    )

    tip_abs.parent.mkdir(parents=True, exist_ok=True)
    tip_abs.write_text(rendered)

    target.category = category
    target.entry_date = entry_date
    target.last_written_hash = _content_hash(rendered)
    target.updated_at = now_iso
    state.upsert_target(target)

    return SyncOutcome(
        resolved_path=target.tip_path,
        created_file=created_file,
        forked=touched,
        forked_from=forked_from,
    )


def _current_continued_from(target: TargetRecord, vault_root: Path) -> str | None:
    """Preserve an existing `continued_from` when re-rendering an untouched tip that
    was itself created by a prior fork."""
    tip_abs = vault_root / target.tip_path
    if not tip_abs.exists():
        return None
    frontmatter, _ = _split_frontmatter(tip_abs.read_text())
    meta = frontmatter.get(FRONTMATTER_KEY) or {}
    return meta.get("continued_from")
