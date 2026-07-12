from datetime import datetime
from pathlib import Path

import pytest

from lantern_pkms.main import (
    EntryItem,
    HeadingItem,
    append_rendered_lines,
    group_page_items,
    note_already_fully_processed,
    render_entry_text,
    render_heading_text,
    seconds_until_next_run_at,
)
from lantern_pkms.state.db import NoteRecord
from lantern_pkms.structuring.symbol_mapping import ClassifiedEntry, SymbolMappingConfig, VLMLine

SYMBOL_CONFIG_PATH = Path(__file__).parent.parent / "config" / "symbol-mapping.default.yml"


@pytest.fixture(scope="module")
def symbol_config() -> SymbolMappingConfig:
    return SymbolMappingConfig.load(SYMBOL_CONFIG_PATH)


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


def test_seconds_until_next_run_at_later_today() -> None:
    now = datetime(2026, 7, 12, 14, 0, 0)
    assert seconds_until_next_run_at("18:30", now) == 4.5 * 3600


def test_seconds_until_next_run_at_already_passed_today_rolls_to_tomorrow() -> None:
    now = datetime(2026, 7, 12, 14, 0, 0)
    assert seconds_until_next_run_at("02:00", now) == 12 * 3600


def test_seconds_until_next_run_at_exact_match_rolls_to_tomorrow() -> None:
    # See issue #22 — treating "right now" as "already happened" (not "due now")
    # keeps the scheduler loop from computing a zero-second sleep and double-running.
    now = datetime(2026, 7, 12, 2, 0, 0)
    assert seconds_until_next_run_at("02:00", now) == 24 * 3600


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
    assert render_entry_text(c) == "○ Dentist at 2pm"


def test_render_entry_text_event_indents_nested_entries() -> None:
    c = ClassifiedEntry(
        entry_type="event", state="scheduled", text="Call at 3pm", symbol_raw="circle", confidence=0.9,
        needs_review=False, indent_level=1,
    )
    assert render_entry_text(c) == "    ○ Call at 3pm"


def test_render_entry_text_mood() -> None:
    c = ClassifiedEntry(entry_type="mood", state=None, text="Feeling good", symbol_raw="equals", confidence=0.9, needs_review=False)
    assert render_entry_text(c) == "= Feeling good"


def test_render_entry_text_needs_review() -> None:
    c = ClassifiedEntry(
        entry_type="review", state=None, text="???", symbol_raw="bullet", confidence=0.2,
        needs_review=True, review_reason="confidence 0.20 below threshold 0.60",
    )
    text = render_entry_text(c)
    assert text.startswith("- ???")
    assert "0.20" in text


def test_render_entry_text_indents_nested_entries() -> None:
    c = ClassifiedEntry(
        entry_type="mood", state=None, text="Uplifted", symbol_raw="equals", confidence=0.9,
        needs_review=False, indent_level=2,
    )
    assert render_entry_text(c) == "        = Uplifted"


def test_render_entry_text_needs_review_preserves_indent() -> None:
    # See issue #6: a flagged entry stays at its natural nesting depth, rendered
    # inline where it belongs, rather than flattened into a separate section.
    c = ClassifiedEntry(
        entry_type="review", state=None, text="???", symbol_raw="bullet", confidence=0.2,
        needs_review=True, review_reason="flagged", indent_level=3,
    )
    assert render_entry_text(c).startswith("            - ???")


def test_render_entry_text_flagged_task_keeps_checkbox_with_suffix() -> None:
    # See issue #7: a recognized task stays a checkbox even when flagged for
    # review, instead of losing its checkbox-ness to a generic flagged bullet.
    c = ClassifiedEntry(
        entry_type="task", state="open", text="Buy milk", symbol_raw="bullet", confidence=0.4,
        needs_review=True, review_reason="confidence 0.40 below threshold 0.60",
    )
    assert render_entry_text(c) == "- [ ] Buy milk (confidence 0.40 — confidence 0.40 below threshold 0.60)"


def test_render_entry_text_flagged_complete_task_keeps_checkmark() -> None:
    c = ClassifiedEntry(
        entry_type="task", state="complete", text="Call dentist", symbol_raw="bullet", confidence=0.4,
        needs_review=True, review_reason="flagged",
    )
    assert render_entry_text(c).startswith("- [x] Call dentist")


def test_render_entry_text_migrated_next_day() -> None:
    c = ClassifiedEntry(
        entry_type="task", state="migrated_next_day", text="Finish report",
        symbol_raw="chevron_right", confidence=0.9, needs_review=False,
    )
    assert render_entry_text(c) == "> Finish report"


def test_render_entry_text_migrated_backlog() -> None:
    c = ClassifiedEntry(
        entry_type="task", state="migrated_backlog", text="Plan trip",
        symbol_raw="chevron_left", confidence=0.9, needs_review=False,
    )
    assert render_entry_text(c) == "< Plan trip"


def test_render_heading_text_with_end() -> None:
    item = HeadingItem(start_text="9:00 AM", end_text="10:15 AM", confidence=0.9)
    assert render_heading_text(item) == "### 9:00 AM – 10:15 AM"


def test_render_heading_text_without_end() -> None:
    item = HeadingItem(start_text="9:00 AM", confidence=0.9)
    assert render_heading_text(item) == "### 9:00 AM"


def _entry_line(text: str, indent_level: int = 0, raw_symbol: str = "bullet") -> VLMLine:
    return VLMLine(kind="entry", raw_symbol=raw_symbol, text=text, confidence=0.9, indent_level=indent_level)


def _start(text: str) -> VLMLine:
    return VLMLine(kind="time_start", raw_symbol="other", text=text, confidence=0.9)


def _end(text: str) -> VLMLine:
    return VLMLine(kind="time_end", raw_symbol="other", text=text, confidence=0.9)


def test_group_page_items_no_timeboxes_passes_entries_through_in_order(symbol_config: SymbolMappingConfig) -> None:
    lines = [_entry_line("Meeting about a new project"), _entry_line("Uplifted", indent_level=1, raw_symbol="equals")]
    items = group_page_items(lines, symbol_config)
    assert len(items) == 2
    assert all(isinstance(i, EntryItem) for i in items)
    assert [i.entry.text for i in items] == ["Meeting about a new project", "Uplifted"]
    assert items[1].entry.indent_level == 1


def test_group_page_items_single_timebox_produces_heading_before_entries(symbol_config: SymbolMappingConfig) -> None:
    lines = [_start("9:00 AM"), _entry_line("Stand-up"), _entry_line("Follow up"), _end("10:15 AM")]
    items = group_page_items(lines, symbol_config)
    assert isinstance(items[0], HeadingItem)
    assert items[0].start_text == "9:00 AM"
    assert items[0].end_text == "10:15 AM"
    assert [i.entry.text for i in items[1:]] == ["Stand-up", "Follow up"]


def test_group_page_items_multiple_timeboxes(symbol_config: SymbolMappingConfig) -> None:
    lines = [
        _start("9:00 AM"), _entry_line("Stand-up"), _end("9:15 AM"),
        _start("10:00 AM"), _entry_line("Deep work"), _end("11:00 AM"),
    ]
    items = group_page_items(lines, symbol_config)
    assert [type(i).__name__ for i in items] == ["HeadingItem", "EntryItem", "HeadingItem", "EntryItem"]
    assert items[0].start_text == "9:00 AM" and items[0].end_text == "9:15 AM"
    assert items[2].start_text == "10:00 AM" and items[2].end_text == "11:00 AM"


def test_group_page_items_unmatched_start_flushes_at_page_end(symbol_config: SymbolMappingConfig) -> None:
    lines = [_start("9:00 AM"), _entry_line("Stand-up")]
    items = group_page_items(lines, symbol_config)
    assert isinstance(items[0], HeadingItem)
    assert items[0].start_text == "9:00 AM"
    assert items[0].end_text is None
    assert items[1].entry.text == "Stand-up"


def test_group_page_items_second_start_flushes_open_box_without_end(symbol_config: SymbolMappingConfig) -> None:
    lines = [_start("9:00 AM"), _entry_line("Stand-up"), _start("10:00 AM"), _entry_line("Deep work"), _end("11:00 AM")]
    items = group_page_items(lines, symbol_config)
    assert [type(i).__name__ for i in items] == ["HeadingItem", "EntryItem", "HeadingItem", "EntryItem"]
    assert items[0].end_text is None
    assert items[2].start_text == "10:00 AM" and items[2].end_text == "11:00 AM"


def test_group_page_items_entries_before_first_timebox_are_ungrouped(symbol_config: SymbolMappingConfig) -> None:
    # Pre-planned tasks/events area at the top of the page, before any timebox.
    lines = [_entry_line("Pre-planned task"), _start("9:00 AM"), _entry_line("Stand-up"), _end("9:15 AM")]
    items = group_page_items(lines, symbol_config)
    assert isinstance(items[0], EntryItem)
    assert items[0].entry.text == "Pre-planned task"
    assert isinstance(items[1], HeadingItem)


def test_append_rendered_lines_goes_to_default_path() -> None:
    c = ClassifiedEntry(entry_type="task", state="open", text="Buy milk", symbol_raw="bullet", confidence=0.9, needs_review=False)
    rendered: dict = {}
    append_rendered_lines(rendered, "lp-1-1-0", 0, c, "Daily/2026/2026-07-09.md")
    assert list(rendered.keys()) == ["Daily/2026/2026-07-09.md"]
    assert rendered["Daily/2026/2026-07-09.md"][0].text == "- [ ] Buy milk"


def test_append_rendered_lines_migrated_entry_renders_in_place_not_split() -> None:
    # See issue #13: auto-migration to a destination file was paused — a migrated
    # entry stays in its origin note with its literal mark, nothing gets copied.
    c = ClassifiedEntry(
        entry_type="task", state="migrated_next_day", text="Finish report",
        symbol_raw="chevron_right", confidence=0.9, needs_review=False,
    )
    rendered: dict = {}
    append_rendered_lines(rendered, "lp-1-1-0", 0, c, "Daily/2026/2026-07-09.md")

    assert list(rendered.keys()) == ["Daily/2026/2026-07-09.md"]
    origin_line = rendered["Daily/2026/2026-07-09.md"][0]
    assert origin_line.text == "> Finish report"
    assert origin_line.block_id == "lp-1-1-0"
