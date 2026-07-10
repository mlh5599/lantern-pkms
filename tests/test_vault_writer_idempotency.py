from pathlib import Path

import pytest

from lantern_pkms.state.db import (
    STATUS_SYSTEM_OWNED,
    STATUS_USER_DELETED,
    STATUS_USER_MODIFIED,
    NoteRecord,
    PageRecord,
    StateDB,
)
from lantern_pkms.vault.writer import RenderedLine, parse_note, sync_page


@pytest.fixture()
def state(tmp_path: Path) -> StateDB:
    with StateDB(tmp_path / "state.db") as db:
        db.upsert_note(
            NoteRecord(
                note_id="1234",
                category="daily",
                folder_year=2026,
                file_name="2026-07-09.note",
                content_sha256="v1",
                first_ingested_at="t0",
                last_ingested_at="t0",
            )
        )
        db.upsert_page(
            PageRecord(page_id="1234-3", note_id="1234", page_number=3, page_content_sha256="p1")
        )
        yield db


@pytest.fixture()
def vault(tmp_path: Path) -> Path:
    v = tmp_path / "Lantern"
    v.mkdir()
    return v


def _task_line(text: str = "Buy groceries", block_id: str = "lp-1234-3-0") -> RenderedLine:
    return RenderedLine(
        block_id=block_id, section="Tasks", text=f"- [ ] {text}", entry_type="task", entry_index=0
    )


def test_new_file_created_with_task(vault: Path, state: StateDB) -> None:
    outcome = sync_page(
        vault_root=vault,
        default_rel_path="Daily/2026/2026-07-09.md",
        note_id="1234",
        page_id="1234-3",
        page_number=3,
        entry_date="2026-07-09",
        category="daily",
        lines=[_task_line()],
        state=state,
        now_iso="2026-07-09T06:00:00",
    )
    assert outcome.created_file
    assert outcome.created == ["lp-1234-3-0"]
    text = (vault / "Daily/2026/2026-07-09.md").read_text()
    assert "- [ ] Buy groceries ^lp-1234-3-0" in text
    assert "## Tasks" in text
    assert "lantern_pkms" in text


def test_rerun_unchanged_produces_zero_diff(vault: Path, state: StateDB) -> None:
    kwargs = dict(
        vault_root=vault,
        default_rel_path="Daily/2026/2026-07-09.md",
        note_id="1234",
        page_id="1234-3",
        page_number=3,
        entry_date="2026-07-09",
        category="daily",
        lines=[_task_line()],
        state=state,
    )
    sync_page(now_iso="2026-07-09T06:00:00", **kwargs)
    text_after_first = (vault / "Daily/2026/2026-07-09.md").read_text()

    sync_page(now_iso="2026-07-09T06:00:00", **kwargs)
    text_after_second = (vault / "Daily/2026/2026-07-09.md").read_text()

    assert text_after_first == text_after_second


def test_source_change_updates_still_system_owned_line(vault: Path, state: StateDB) -> None:
    common = dict(
        vault_root=vault,
        default_rel_path="Daily/2026/2026-07-09.md",
        note_id="1234",
        page_id="1234-3",
        page_number=3,
        entry_date="2026-07-09",
        category="daily",
        state=state,
    )
    sync_page(lines=[_task_line("Buy groceries")], now_iso="t1", **common)
    sync_page(lines=[_task_line("Buy groceries and milk")], now_iso="t2", **common)

    text = (vault / "Daily/2026/2026-07-09.md").read_text()
    assert "Buy groceries and milk ^lp-1234-3-0" in text
    assert "Buy groceries ^lp-1234-3-0" not in text.replace("Buy groceries and milk", "")


def test_human_edited_line_is_never_overwritten(vault: Path, state: StateDB) -> None:
    common = dict(
        vault_root=vault,
        default_rel_path="Daily/2026/2026-07-09.md",
        note_id="1234",
        page_id="1234-3",
        page_number=3,
        entry_date="2026-07-09",
        category="daily",
        state=state,
    )
    sync_page(lines=[_task_line("Buy groceries")], now_iso="t1", **common)

    path = vault / "Daily/2026/2026-07-09.md"
    text = path.read_text()
    edited = text.replace(
        "- [ ] Buy groceries ^lp-1234-3-0", "- [x] Buy groceries and eggs ^lp-1234-3-0"
    )
    path.write_text(edited)

    # Source re-transcribes the *same* text as before (page unchanged) — should not
    # touch the human-edited line, and since the source text is unchanged, no conflict
    # should be flagged either.
    outcome = sync_page(lines=[_task_line("Buy groceries")], now_iso="t2", **common)

    final = path.read_text()
    assert "- [x] Buy groceries and eggs ^lp-1234-3-0" in final
    assert "Buy groceries and eggs" in final  # human edit preserved verbatim
    assert not outcome.flagged_conflicts
    assert outcome.locked_unchanged == ["lp-1234-3-0"]

    entry = state.get_vault_entry("lp-1234-3-0")
    assert entry is not None
    assert entry.status == STATUS_USER_MODIFIED


def test_checkbox_toggle_locks_line(vault: Path, state: StateDB) -> None:
    common = dict(
        vault_root=vault,
        default_rel_path="Daily/2026/2026-07-09.md",
        note_id="1234",
        page_id="1234-3",
        page_number=3,
        entry_date="2026-07-09",
        category="daily",
        state=state,
    )
    sync_page(lines=[_task_line("Call dentist")], now_iso="t1", **common)

    path = vault / "Daily/2026/2026-07-09.md"
    path.write_text(path.read_text().replace("- [ ] Call dentist", "- [x] Call dentist"))

    sync_page(lines=[_task_line("Call dentist")], now_iso="t2", **common)

    assert "- [x] Call dentist ^lp-1234-3-0" in path.read_text()
    entry = state.get_vault_entry("lp-1234-3-0")
    assert entry is not None
    assert entry.status == STATUS_USER_MODIFIED


def test_conflicting_source_change_after_human_edit_is_flagged_not_dropped(
    vault: Path, state: StateDB
) -> None:
    common = dict(
        vault_root=vault,
        default_rel_path="Daily/2026/2026-07-09.md",
        note_id="1234",
        page_id="1234-3",
        page_number=3,
        entry_date="2026-07-09",
        category="daily",
        state=state,
    )
    sync_page(lines=[_task_line("Buy groceries")], now_iso="t1", **common)

    path = vault / "Daily/2026/2026-07-09.md"
    path.write_text(path.read_text().replace("Buy groceries", "Buy groceries (fixed typo)"))

    # Now the *source* also changes for the same block — a real conflict.
    outcome = sync_page(lines=[_task_line("Buy groceries and bread")], now_iso="t2", **common)

    final = path.read_text()
    assert "Buy groceries (fixed typo) ^lp-1234-3-0" in final  # human edit still untouched
    assert "Buy groceries and bread" in final  # but the new source text is visible somewhere
    assert "Needs Review" in final
    assert outcome.flagged_conflicts == ["lp-1234-3-0"]

    # Running again with the same (still-diverged) source text should not re-flag.
    outcome2 = sync_page(lines=[_task_line("Buy groceries and bread")], now_iso="t3", **common)
    assert not outcome2.flagged_conflicts
    assert outcome2.locked_unchanged == ["lp-1234-3-0"]
    # No duplicate conflict entries.
    assert final.count("new source text") <= (vault / "Daily/2026/2026-07-09.md").read_text().count(
        "new source text"
    )


def test_deleted_line_is_never_resurrected(vault: Path, state: StateDB) -> None:
    common = dict(
        vault_root=vault,
        default_rel_path="Daily/2026/2026-07-09.md",
        note_id="1234",
        page_id="1234-3",
        page_number=3,
        entry_date="2026-07-09",
        category="daily",
        lines=[_task_line("Throwaway note")],
        state=state,
    )
    sync_page(now_iso="t1", **common)

    path = vault / "Daily/2026/2026-07-09.md"
    text = path.read_text()
    without_line = "\n".join(
        line for line in text.splitlines() if "lp-1234-3-0" not in line
    )
    path.write_text(without_line)

    outcome = sync_page(now_iso="t2", **common)

    assert "lp-1234-3-0" not in path.read_text()
    assert outcome.skipped_deleted == ["lp-1234-3-0"]
    entry = state.get_vault_entry("lp-1234-3-0")
    assert entry is not None
    assert entry.status == STATUS_USER_DELETED

    # And it must stay deleted on a third run too.
    outcome2 = sync_page(now_iso="t3", **common)
    assert "lp-1234-3-0" not in path.read_text()
    assert outcome2.skipped_deleted == ["lp-1234-3-0"]


def test_personal_content_outside_managed_region_is_untouched(vault: Path, state: StateDB) -> None:
    common = dict(
        vault_root=vault,
        default_rel_path="Daily/2026/2026-07-09.md",
        note_id="1234",
        page_id="1234-3",
        page_number=3,
        entry_date="2026-07-09",
        category="daily",
        lines=[_task_line()],
        state=state,
    )
    sync_page(now_iso="t1", **common)

    path = vault / "Daily/2026/2026-07-09.md"
    text = path.read_text()
    text += "\n\n## My own reflections\nToday was a good day.\n"
    path.write_text(text)

    sync_page(now_iso="t2", **common)

    final = path.read_text()
    assert "## My own reflections" in final
    assert "Today was a good day." in final


def test_rename_tolerance_locates_file_via_frontmatter(vault: Path, state: StateDB) -> None:
    common = dict(
        vault_root=vault,
        default_rel_path="Daily/2026/2026-07-09.md",
        note_id="1234",
        page_id="1234-3",
        page_number=3,
        entry_date="2026-07-09",
        category="daily",
        lines=[_task_line()],
        state=state,
    )
    sync_page(now_iso="t1", **common)

    old_path = vault / "Daily/2026/2026-07-09.md"
    new_path = vault / "Daily/2026/2026-07-09-renamed.md"
    old_path.rename(new_path)

    common_without_lines = {k: v for k, v in common.items() if k != "lines"}
    outcome = sync_page(lines=[_task_line("Buy groceries updated")], now_iso="t2", **common_without_lines)

    assert not old_path.exists()
    assert new_path.exists()
    assert outcome.resolved_path == "Daily/2026/2026-07-09-renamed.md"
    assert "Buy groceries updated" in new_path.read_text()


def test_parse_note_roundtrip_preserves_sections() -> None:
    text = (
        "---\nlantern_pkms: {}\n---\n\n"
        "<!-- lantern-pkms:begin -->\n"
        "## Tasks\n- [ ] A ^lp-1-1-0\n\n"
        "## Events\n- 10:00 B ^lp-1-1-1\n"
        "<!-- lantern-pkms:end -->\n"
    )
    parsed = parse_note(text)
    assert parsed.lines_by_block["lp-1-1-0"].section == "Tasks"
    assert parsed.lines_by_block["lp-1-1-1"].section == "Events"
