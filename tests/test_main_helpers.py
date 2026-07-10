from datetime import date
from pathlib import Path

import pytest

from lantern_pkms.main import append_rendered_lines, note_already_fully_processed, render_entry_text
from lantern_pkms.state.db import NoteRecord
from lantern_pkms.structuring.symbol_mapping import ClassifiedEntry
from lantern_pkms.taxonomy import TaxonomyConfig

CONFIG_PATH = Path(__file__).parent.parent / "config" / "taxonomy.default.yml"


@pytest.fixture(scope="module")
def taxonomy() -> TaxonomyConfig:
    return TaxonomyConfig.load(CONFIG_PATH)


def _note_record(content_sha256: str = "abc123") -> NoteRecord:
    return NoteRecord(
        note_id="1234",
        category="daily",
        folder_year=2026,
        file_name="2026-07-09.note",
        content_sha256=content_sha256,
        first_ingested_at="t",
        last_ingested_at="t",
    )


def test_note_never_seen_before_is_not_skipped() -> None:
    assert note_already_fully_processed(None, "abc123", has_pages=False) is False


def test_note_unchanged_but_never_had_pages_processed_is_not_skipped() -> None:
    # Regression test for the real bug: a note recorded (e.g. right before a mid-run
    # crash/restart) but never actually processed must be retried, not skipped
    # forever just because its content hash still matches.
    existing = _note_record(content_sha256="abc123")
    assert note_already_fully_processed(existing, "abc123", has_pages=False) is False


def test_note_unchanged_and_has_pages_is_skipped() -> None:
    existing = _note_record(content_sha256="abc123")
    assert note_already_fully_processed(existing, "abc123", has_pages=True) is True


def test_note_content_changed_is_not_skipped_even_with_pages() -> None:
    existing = _note_record(content_sha256="old-hash")
    assert note_already_fully_processed(existing, "new-hash", has_pages=True) is False


def test_render_entry_text_open_task() -> None:
    c = ClassifiedEntry(entry_type="task", state="open", text="Buy milk", symbol_raw="bullet", confidence=0.9, needs_review=False)
    assert render_entry_text(c) == "- [ ] Buy milk"


def test_render_entry_text_complete_task() -> None:
    c = ClassifiedEntry(entry_type="task", state="complete", text="Call dentist", symbol_raw="bullet", confidence=0.9, needs_review=False)
    assert render_entry_text(c) == "- [x] Call dentist"


def test_render_entry_text_cancelled_task() -> None:
    c = ClassifiedEntry(entry_type="task", state="cancelled", text="Old idea", symbol_raw="bullet", confidence=0.9, needs_review=False)
    assert render_entry_text(c) == "- [-] ~~Old idea~~ (cancelled)"


def test_render_entry_text_event() -> None:
    c = ClassifiedEntry(entry_type="event", state="scheduled", text="Dentist at 2pm", symbol_raw="circle", confidence=0.9, needs_review=False)
    assert render_entry_text(c) == "- Dentist at 2pm"


def test_render_entry_text_mood() -> None:
    c = ClassifiedEntry(entry_type="mood", state=None, text="Feeling good", symbol_raw="equals", confidence=0.9, needs_review=False)
    assert render_entry_text(c) == "- = Feeling good"


def test_render_entry_text_needs_review() -> None:
    c = ClassifiedEntry(
        entry_type="review", state=None, text="???", symbol_raw="bullet", confidence=0.2,
        needs_review=True, review_reason="confidence 0.20 below threshold 0.60",
    )
    text = render_entry_text(c)
    assert text.startswith("- ???")
    assert "0.20" in text


def test_append_rendered_lines_non_migration_goes_to_default_path(taxonomy: TaxonomyConfig) -> None:
    c = ClassifiedEntry(entry_type="task", state="open", text="Buy milk", symbol_raw="bullet", confidence=0.9, needs_review=False)
    rendered: dict = {}
    append_rendered_lines(rendered, "lp-1-1-0", 0, c, 2026, date(2026, 7, 9), "Daily/2026/2026-07-09.md", taxonomy)
    assert list(rendered.keys()) == ["Daily/2026/2026-07-09.md"]
    assert rendered["Daily/2026/2026-07-09.md"][0].text == "- [ ] Buy milk"


def test_append_rendered_lines_migrated_next_day_splits_across_two_files(taxonomy: TaxonomyConfig) -> None:
    c = ClassifiedEntry(
        entry_type="task", state="migrated_next_day", text="Finish report",
        symbol_raw="chevron_right", confidence=0.9, needs_review=False,
    )
    rendered: dict = {}
    append_rendered_lines(rendered, "lp-1-1-0", 0, c, 2026, date(2026, 7, 9), "Daily/2026/2026-07-09.md", taxonomy)

    assert set(rendered.keys()) == {"Daily/2026/2026-07-09.md", "Daily/2026/2026-07-10.md"}
    origin_line = rendered["Daily/2026/2026-07-09.md"][0]
    assert "[>]" in origin_line.text
    assert "migrated to" in origin_line.text
    assert origin_line.block_id == "lp-1-1-0"

    dest_line = rendered["Daily/2026/2026-07-10.md"][0]
    assert dest_line.text == "- [ ] Finish report"
    assert dest_line.block_id == "lp-1-1-0-dest"


def test_append_rendered_lines_migrated_backlog_targets_future_backlog_file(taxonomy: TaxonomyConfig) -> None:
    c = ClassifiedEntry(
        entry_type="task", state="migrated_backlog", text="Plan trip",
        symbol_raw="chevron_left", confidence=0.9, needs_review=False,
    )
    rendered: dict = {}
    append_rendered_lines(rendered, "lp-1-1-0", 0, c, 2026, date(2026, 7, 9), "Daily/2026/2026-07-09.md", taxonomy)

    assert "Future/2026/Backlog.md" in rendered
    origin_line = rendered["Daily/2026/2026-07-09.md"][0]
    assert "[<]" in origin_line.text
