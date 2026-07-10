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

from lantern_pkms.config import Settings
from lantern_pkms.htr.ollama_client import OllamaHTRClient
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
from lantern_pkms.structuring.migration import MIGRATED_NEXT_DAY, compute_migration, is_migration_state
from lantern_pkms.structuring.symbol_mapping import ClassifiedEntry, SymbolMappingConfig, classify
from lantern_pkms.supernote.client import SupernoteClient, SupernoteEntry
from lantern_pkms.supernote.note_parser import ParsedNotebook, parse_note_bytes
from lantern_pkms.taxonomy import TaxonomyConfig, source_page_path
from lantern_pkms.vault.writer import RenderedLine, sync_page

logger = logging.getLogger("lantern_pkms")

_SECTION_FOR_ENTRY_TYPE = {
    "task": "Tasks",
    "event": "Events",
    "note": "Notes",
    "mood": "Mood",
    "review": "Needs Review",
}


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
    if c.needs_review:
        reason = c.review_reason or "flagged"
        return f"- {c.text} (confidence {c.confidence:.2f} — {reason})"
    if c.entry_type == "task":
        if c.state == "complete":
            return f"- [x] {c.text}"
        if c.state == "cancelled":
            return f"- [-] ~~{c.text}~~ (cancelled)"
        return f"- [ ] {c.text}"
    if c.entry_type == "mood":
        return f"- = {c.text}"
    if c.state == "cancelled":
        return f"- ~~{c.text}~~ (cancelled)"
    return f"- {c.text}"


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
    section = _SECTION_FOR_ENTRY_TYPE.get(c.entry_type, "Notes") if not c.needs_review else "Needs Review"

    if is_migration_state(c.state) and entry_date is not None:
        dest = compute_migration(c.state, entry_date)
        assert dest is not None  # is_migration_state already guarantees this
        if dest.kind == "next_day":
            dest_path = taxonomy.default_target_path("daily", dest.target_date.year, "", dest.target_date)
        else:
            dest_path = taxonomy.backlog_path(year)

        marker = ">" if c.state == MIGRATED_NEXT_DAY else "<"
        link_target = dest_path.removesuffix(".md")
        origin_text = f"- [{marker}] ~~{c.text}~~ → migrated to [[{link_target}]]"
        rendered_by_target.setdefault(default_path, []).append(
            RenderedLine(block_id=block_id, section="Tasks", text=origin_text, entry_type="task", entry_index=entry_index)
        )

        dest_entry = ClassifiedEntry(
            entry_type="task", state="open", text=c.text, symbol_raw=c.symbol_raw,
            confidence=c.confidence, needs_review=False,
        )
        rendered_by_target.setdefault(dest_path, []).append(
            RenderedLine(
                block_id=f"{block_id}-dest", section="Tasks", text=render_entry_text(dest_entry),
                entry_type="task", entry_index=entry_index,
            )
        )
        return

    rendered_by_target.setdefault(default_path, []).append(
        RenderedLine(block_id=block_id, section=section, text=render_entry_text(c), entry_type=c.entry_type, entry_index=entry_index)
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

    notebook = parse_note_bytes(data, policy="loose")
    entry_date = taxonomy.parse_entry_date(category, year, title)

    for page_number in range(notebook.total_pages):
        _ingest_page(entry, notebook, page_number, category, year, entry_date, title, settings, state, htr_client, prompt, symbol_config, taxonomy)

    notes_ingested_total.inc()


def _ingest_page(
    entry: SupernoteEntry,
    notebook: ParsedNotebook,
    page_number: int,
    category: str,
    year: int,
    entry_date: date | None,
    title: str,
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

    vlm_lines = htr_client.transcribe_page(png_bytes, prompt)
    classified = [classify(line, symbol_config) for line in vlm_lines]

    image_rel_path = source_page_path(entry.id, page_number)
    image_abs_path = settings.vault_path / image_rel_path
    image_abs_path.parent.mkdir(parents=True, exist_ok=True)
    image_abs_path.write_bytes(png_bytes)

    default_path = taxonomy.default_target_path(category, year, title, entry_date)

    rendered_by_target: dict[str, list[RenderedLine]] = {}
    for i, c in enumerate(classified):
        block_id = make_block_id(entry.id, page_number, i)
        append_rendered_lines(rendered_by_target, block_id, i, c, year, entry_date, default_path, taxonomy)

    flagged_count = 0
    for target_path, lines in rendered_by_target.items():
        outcome = sync_page(
            vault_root=settings.vault_path,
            default_rel_path=target_path,
            note_id=entry.id,
            page_id=page_id,
            page_number=page_number,
            entry_date=entry_date.isoformat() if entry_date else None,
            category=category,
            lines=lines,
            state=state,
            source_image_rel_path=image_rel_path if target_path == default_path else None,
        )
        flagged_count += len(outcome.flagged_conflicts)
    if flagged_count:
        htr_low_confidence_flagged_total.inc(flagged_count)

    avg_confidence = sum(c.confidence for c in classified) / len(classified) if classified else None
    state.upsert_page(
        PageRecord(
            page_id=page_id,
            note_id=entry.id,
            page_number=page_number,
            page_content_sha256=page_hash,
            htr_json=json.dumps([c.model_dump() for c in classified]),
            htr_confidence_avg=avg_confidence,
            review_needed=any(c.needs_review for c in classified),
        )
    )
    htr_pages_processed_total.inc()


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
