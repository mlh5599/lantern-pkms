"""Ingestion scheduler loop — Supernote -> HTR -> Lantern.

This is orchestration glue over already-independently-tested pieces (client,
note_parser, ollama_client, symbol_mapping, migration, vault.writer, taxonomy). It's
the least independently-testable part of lantern-pkms since a real end-to-end run needs
live Supernote credentials, a running Ollama, and a real vault — that's exactly what
Phase 0 (scripts/htr_bench.py) is for. Pure helper functions below (text rendering)
are unit tested; the orchestration functions are exercised by Phase 0, not by the
test suite.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from datetime import date, datetime

from pydantic import BaseModel

from lantern_pkms.config import Settings
from lantern_pkms.htr.ollama_client import OllamaError, OllamaHTRClient
from lantern_pkms.htr.prompts import build_transcription_prompt
from lantern_pkms.metrics import (
    htr_low_confidence_flagged_total,
    htr_pages_processed_total,
    last_successful_run_timestamp,
    notes_ingested_total,
    pipeline_errors_total,
    start_metrics_server,
)
from lantern_pkms.state.db import NoteRecord, PageRecord, StateDB, make_block_id
from lantern_pkms.structuring.migration import compute_migration, is_migration_state
from lantern_pkms.structuring.symbol_mapping import (
    ClassifiedEntry,
    SymbolMappingConfig,
    VLMLine,
    classify,
)
from lantern_pkms.supernote.client import SupernoteClient, SupernoteEntry
from lantern_pkms.supernote.note_parser import ParsedNotebook, parse_note_bytes
from lantern_pkms.taxonomy import TaxonomyConfig, source_page_path
from lantern_pkms.vault.writer import RenderedLine, sync_target

logger = logging.getLogger("lantern_pkms")

# Markdown nesting unit for indent_level -> list indentation (see render_entry_text).
INDENT_UNIT = "    "


class EntryItem(BaseModel):
    """A page item that renders as a normal bujo outline line."""

    entry: ClassifiedEntry


class HeadingItem(BaseModel):
    """A page item that renders as a timebox heading grouping the entries after it.

    See group_page_items(): produced from a time_start/time_end marker pair (or a
    lone time_start with no matching end before the page runs out).
    """

    start_text: str
    end_text: str | None = None
    confidence: float


PageItem = EntryItem | HeadingItem


# --------------------------------------------------------------------------------
# Pure helpers (unit tested)
# --------------------------------------------------------------------------------


def note_already_fully_processed(
    existing: NoteRecord | None, content_hash: str, has_pages: bool
) -> bool:
    """Whether a note can be safely skipped this run.

    Matching content_sha256 alone is NOT sufficient — if a prior run recorded the
    note (e.g. right before being killed/restarted) but never got as far as
    processing any of its pages, a content-hash-only check would skip it forever,
    since the source never actually changes. Real bug, found deploying this for the
    first time: a mid-run container restart left 34 notes recorded with zero pages
    processed, and every subsequent run silently treated all of them as "already
    synced." Requiring at least one recorded page is what makes this self-healing —
    an incompletely-processed note gets retried on the very next run with no manual
    state cleanup needed.
    """
    return existing is not None and existing.content_sha256 == content_hash and has_pages


def render_entry_text(c: ClassifiedEntry) -> str:
    prefix = INDENT_UNIT * c.indent_level
    if c.entry_type == "review":
        reason = c.review_reason or "flagged"
        return f"{prefix}- {c.text} (confidence {c.confidence:.2f} — {reason})"

    suffix = ""
    if c.needs_review:
        reason = c.review_reason or "flagged"
        suffix = f" (confidence {c.confidence:.2f} — {reason})"

    if c.entry_type == "task":
        if c.state == "complete":
            return f"{prefix}- [x] {c.text}{suffix}"
        if c.state == "cancelled":
            return f"{prefix}- [-] ~~{c.text}~~ (cancelled){suffix}"
        if c.state in ("migrated_backlog", "migrated_next_day"):
            # Reachable only if entry_date was None when this was routed in
            # append_rendered_lines, so no destination could be resolved —
            # surface that rather than silently rendering a bare checkbox.
            return f"{prefix}- [ ] {c.text} (migrated — no destination resolved){suffix}"
        return f"{prefix}- [ ] {c.text}{suffix}"
    if c.entry_type == "mood":
        return f"{prefix}- = {c.text}{suffix}"
    if c.state == "cancelled":
        return f"{prefix}- ~~{c.text}~~ (cancelled){suffix}"
    return f"{prefix}- {c.text}{suffix}"


def render_heading_text(item: HeadingItem) -> str:
    if item.end_text:
        return f"### {item.start_text} – {item.end_text}"
    return f"### {item.start_text}"


def group_page_items(vlm_lines: list[VLMLine], symbol_config: SymbolMappingConfig) -> list[PageItem]:
    """Turn one page's ordered VLM lines into a rendering-ready ordered item list.

    Preserves original page order and nesting instead of bucketing entries by
    category (see issue #2 — grouping a mood/task into a separate Tasks/Events/Mood
    section from the event it was written under loses what it's actually about).

    Timebox markers (kind="time_start"/"time_end") aren't rendered directly — they
    open/close a HeadingItem that groups the entries between them. The heading has
    to render *before* those entries, but the end time isn't known until the
    closing "time_end" marker is reached (it's physically at the bottom of the
    ruled box), so entries are buffered until the box closes. An unmatched
    time_start (a second time_start before a time_end, or the page ending with a
    box still open) flushes what's buffered under a start-only heading rather than
    dropping it. A stray time_end with nothing open is a no-op.
    """
    items: list[PageItem] = []
    buffer: list[EntryItem] = []
    open_start: VLMLine | None = None

    def flush(end_text: str | None) -> None:
        nonlocal buffer, open_start
        if open_start is not None:
            items.append(
                HeadingItem(start_text=open_start.text, end_text=end_text, confidence=open_start.confidence)
            )
        items.extend(buffer)
        buffer = []
        open_start = None

    for line in vlm_lines:
        if line.kind == "time_start":
            if open_start is not None:
                flush(None)
            open_start = line
        elif line.kind == "time_end":
            flush(line.text)
        else:
            item = EntryItem(entry=classify(line, symbol_config))
            (buffer if open_start is not None else items).append(item)

    if open_start is not None:
        flush(None)

    return items


def append_rendered_lines(
    rendered_by_target: dict[str, list[RenderedLine]],
    block_id: str,
    entry_index: int,
    c: ClassifiedEntry,
    year: int,
    entry_date: date | None,
    default_path: str,
    taxonomy: TaxonomyConfig,
) -> None:
    """Route one classified entry to its target file(s), splitting migrations across
    an origin cross-reference and a destination live entry (see the vault writer's
    docstring: "a migrated task is one canonical entry that moves").
    """
    if is_migration_state(c.state) and entry_date is not None:
        dest = compute_migration(c.state, entry_date)
        assert dest is not None  # is_migration_state already guarantees this
        if dest.kind == "next_day":
            dest_path = taxonomy.default_target_path("daily", dest.target_date.year, "", dest.target_date)
        else:
            dest_path = taxonomy.backlog_path(year)

        link_target = dest_path.removesuffix(".md")
        origin_text = f"{INDENT_UNIT * c.indent_level}- [ ] ~~{c.text}~~ → migrated to [[{link_target}]]"
        rendered_by_target.setdefault(default_path, []).append(
            RenderedLine(block_id=block_id, text=origin_text, entry_type="task", entry_index=entry_index)
        )

        origin_link = default_path.removesuffix(".md")
        dest_entry = ClassifiedEntry(
            entry_type="task", state="open", text=c.text, symbol_raw=c.symbol_raw,
            confidence=c.confidence, needs_review=False,
        )
        dest_text = f"{render_entry_text(dest_entry)} (migrated from [[{origin_link}]])"
        rendered_by_target.setdefault(dest_path, []).append(
            RenderedLine(block_id=f"{block_id}-dest", text=dest_text, entry_type="task", entry_index=entry_index)
        )
        return

    rendered_by_target.setdefault(default_path, []).append(
        RenderedLine(
            block_id=block_id, text=render_entry_text(c), entry_type=c.entry_type,
            entry_index=entry_index, needs_review=c.needs_review,
        )
    )


def append_heading_line(
    rendered_by_target: dict[str, list[RenderedLine]],
    block_id: str,
    entry_index: int,
    item: HeadingItem,
    default_path: str,
) -> None:
    rendered_by_target.setdefault(default_path, []).append(
        RenderedLine(
            block_id=block_id, text=render_heading_text(item),
            entry_type="heading", entry_index=entry_index,
        )
    )


# --------------------------------------------------------------------------------
# Orchestration (exercised by Phase 0, not the unit test suite)
# --------------------------------------------------------------------------------


def run_once(settings: Settings) -> None:
    symbol_config = SymbolMappingConfig.load(settings.symbol_mapping_path)
    taxonomy = TaxonomyConfig.load(settings.taxonomy_config_path)
    prompt = build_transcription_prompt(symbol_config)

    with StateDB(settings.state_db_path) as state:
        with SupernoteClient(settings.supernote_cloud_url) as sn_client:
            sn_client.login(settings.supernote_username, settings.supernote_password)
            sn_client.sync_start()
            try:
                with OllamaHTRClient(settings.ollama_host, model=settings.ollama_model) as htr_client:
                    entries = sn_client.list_folder("/", recursive=True)
                    for entry in entries:
                        if entry.is_folder or not entry.name.endswith(".note"):
                            continue
                        try:
                            _ingest_note(entry, settings, state, sn_client, htr_client, prompt, symbol_config, taxonomy)
                        except Exception:
                            pipeline_errors_total.inc()
                            logger.exception("failed to ingest note %s", entry.path_display)
                sn_client.sync_end(success=True)
            except Exception:
                sn_client.sync_end(success=False)
                raise

    last_successful_run_timestamp.set_to_current_time()


def _ingest_note(
    entry: SupernoteEntry,
    settings: Settings,
    state: StateDB,
    sn_client: SupernoteClient,
    htr_client: OllamaHTRClient,
    prompt: str,
    symbol_config: SymbolMappingConfig,
    taxonomy: TaxonomyConfig,
) -> None:
    categorized = taxonomy.categorize_path(entry.path_display)
    if categorized is None:
        return  # index note or a path that doesn't fit the configured taxonomy
    category, year, title = categorized

    data = sn_client.download(entry.id)
    content_hash = hashlib.sha256(data).hexdigest()

    existing = state.get_note(entry.id)
    already_has_pages = bool(state.get_pages_for_note(entry.id))
    if note_already_fully_processed(existing, content_hash, already_has_pages):
        return  # unchanged AND already fully processed — nothing new to sync

    now_iso = datetime.now().astimezone().isoformat()
    state.upsert_note(
        NoteRecord(
            note_id=entry.id,
            category=category,
            folder_year=year,
            file_name=entry.name,
            content_sha256=content_hash,
            supernote_gmt_modified=str(entry.last_update_time_ms) if entry.last_update_time_ms else None,
            first_ingested_at=now_iso,
            last_ingested_at=now_iso,
        )
    )
    # Resolved once per note (collision-checked, stable across renames) — see
    # StateDB.upsert_note/_resolve_source_folder_name and issue #8.
    source_folder_name = state.get_note(entry.id).source_folder_name

    notebook = parse_note_bytes(data, policy="loose")
    entry_date = taxonomy.parse_entry_date(category, year, title)

    for page_number in range(notebook.total_pages):
        _ingest_page(
            entry, notebook, page_number, category, year, entry_date, title,
            source_folder_name, settings, state, htr_client, prompt, symbol_config, taxonomy,
        )

    notes_ingested_total.inc()


def _ingest_page(
    entry: SupernoteEntry,
    notebook: ParsedNotebook,
    page_number: int,
    category: str,
    year: int,
    entry_date: date | None,
    title: str,
    source_folder_name: str,
    settings: Settings,
    state: StateDB,
    htr_client: OllamaHTRClient,
    prompt: str,
    symbol_config: SymbolMappingConfig,
    taxonomy: TaxonomyConfig,
) -> None:
    page_id = f"{entry.id}-{page_number}"
    png_bytes = notebook.render_page_png(page_number)
    page_hash = hashlib.sha256(png_bytes).hexdigest()

    existing_page = state.get_page(page_id)
    if existing_page is not None and existing_page.page_content_sha256 == page_hash:
        return  # page unchanged — skip re-running HTR

    # Both only depend on png_bytes/taxonomy, not on transcription succeeding —
    # computed up front so a failed HTR call (below) can still write a visible
    # placeholder to the right vault file and still save the source scan.
    image_rel_path = source_page_path(source_folder_name, page_number)
    image_abs_path = settings.vault_path / image_rel_path
    image_abs_path.parent.mkdir(parents=True, exist_ok=True)
    image_abs_path.write_bytes(png_bytes)
    default_path = taxonomy.default_target_path(category, year, title, entry_date)

    try:
        vlm_lines = htr_client.transcribe_page(png_bytes, prompt)
    except OllamaError:
        logger.exception("HTR failed for page %s after retries — flagging for review", page_id)
        _record_htr_failure(state, entry, page_number, page_id, page_hash, default_path, category, entry_date, settings)
        raise  # still counted/logged as a pipeline error by run_once()'s per-note catch

    items = group_page_items(vlm_lines, symbol_config)

    # vault_entries.page_id is a foreign key into pages(page_id) — the pages row must
    # exist before sync_target() below inserts any vault_entries referencing it, or
    # the insert fails with a FOREIGN KEY constraint error.
    confidences = [item.entry.confidence if isinstance(item, EntryItem) else item.confidence for item in items]
    avg_confidence = sum(confidences) / len(confidences) if confidences else None
    review_needed = any(isinstance(item, EntryItem) and item.entry.needs_review for item in items)
    state.upsert_page(
        PageRecord(
            page_id=page_id,
            note_id=entry.id,
            page_number=page_number,
            page_content_sha256=page_hash,
            default_target_path=default_path,
            htr_json=json.dumps([item.model_dump() for item in items]),
            htr_confidence_avg=avg_confidence,
            review_needed=review_needed,
        )
    )

    rendered_by_target: dict[str, list[RenderedLine]] = {}
    for i, item in enumerate(items):
        block_id = make_block_id(entry.id, page_number, i)
        if isinstance(item, EntryItem):
            append_rendered_lines(rendered_by_target, block_id, i, item.entry, year, entry_date, default_path, taxonomy)
        else:
            append_heading_line(rendered_by_target, block_id, i, item, default_path)

    flagged_count = sum(1 for lines in rendered_by_target.values() for line in lines if line.needs_review)
    if flagged_count:
        htr_low_confidence_flagged_total.inc(flagged_count)

    for target_path, lines in rendered_by_target.items():
        sync_target(
            vault_root=settings.vault_path,
            target_key=target_path,
            category=category,
            entry_date=entry_date.isoformat() if entry_date else None,
            page_id=page_id,
            lines=lines,
            state=state,
        )

    htr_pages_processed_total.inc()


def _record_htr_failure(
    state: StateDB,
    entry: SupernoteEntry,
    page_number: int,
    page_id: str,
    page_hash: str,
    default_path: str,
    category: str,
    entry_date: date | None,
    settings: Settings,
) -> None:
    """Make a page that fails HTR even after retries visible in the vault instead
    of silently vanishing (issue #4). The sentinel page hash is never equal to a
    real one, so _ingest_page's skip-if-unchanged check always retries this page
    on the next run. The placeholder line reuses entry_index 0's block id, so
    once HTR succeeds, replace_page_entries_for_target's normal upsert overwrites
    it in place with real content rather than leaving an orphaned entry.
    """
    state.upsert_page(
        PageRecord(
            page_id=page_id,
            note_id=entry.id,
            page_number=page_number,
            page_content_sha256=f"htr-failed:{page_hash}",
            default_target_path=default_path,
            review_needed=True,
        )
    )
    failure_line = RenderedLine(
        block_id=make_block_id(entry.id, page_number, 0),
        text="- ⚠️ HTR failed to transcribe this page — see ingestion logs",
        entry_type="review",
        entry_index=0,
        needs_review=True,
    )
    sync_target(
        vault_root=settings.vault_path,
        target_key=default_path,
        category=category,
        entry_date=entry_date.isoformat() if entry_date else None,
        page_id=page_id,
        lines=[failure_line],
        state=state,
    )


def run() -> None:
    logging.basicConfig(level=logging.INFO)
    settings = Settings()
    start_metrics_server(settings.metrics_port)
    logger.info("lantern-pkms starting, poll interval %d minutes", settings.poll_interval_minutes)
    while True:
        try:
            run_once(settings)
        except Exception:
            pipeline_errors_total.inc()
            logger.exception("run_once() failed")
        time.sleep(settings.poll_interval_minutes * 60)


if __name__ == "__main__":
    run()
