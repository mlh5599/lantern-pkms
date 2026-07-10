from pathlib import Path

import pytest

from lantern_pkms.state.db import NoteRecord, PageRecord, StateDB
from lantern_pkms.vault.writer import RenderedLine, sync_target


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
            PageRecord(
                page_id="1234-3", note_id="1234", page_number=3, page_content_sha256="p1",
                default_target_path="Daily/2026/2026-07-09.md",
            )
        )
        yield db


@pytest.fixture()
def vault(tmp_path: Path) -> Path:
    v = tmp_path / "Lantern"
    v.mkdir()
    return v


def _task_line(text: str = "Buy groceries", block_id: str = "lp-1234-3-0") -> RenderedLine:
    return RenderedLine(
        block_id=block_id, text=f"- [ ] {text}", entry_type="task", entry_index=0
    )


def _common(state: StateDB, target_key: str = "Daily/2026/2026-07-09.md") -> dict:
    return dict(
        target_key=target_key,
        category="daily",
        entry_date="2026-07-09",
        page_id="1234-3",
        state=state,
    )


def test_new_target_creates_full_file(vault: Path, state: StateDB) -> None:
    outcome = sync_target(vault_root=vault, lines=[_task_line()], now_iso="2026-07-09T06:00:00", **_common(state))
    assert outcome.created_file
    assert not outcome.forked
    text = (vault / "Daily/2026/2026-07-09.md").read_text()
    assert "- [ ] Buy groceries" in text
    assert "^lp-1234-3-0" not in text  # block ids are a DB key only, never rendered
    assert "lantern_pkms" in text
    assert "target_key: Daily/2026/2026-07-09.md" in text


def test_rerun_unchanged_produces_zero_diff_modulo_timestamp(vault: Path, state: StateDB) -> None:
    kwargs = dict(vault_root=vault, lines=[_task_line()], **_common(state))
    sync_target(now_iso="2026-07-09T06:00:00", **kwargs)
    text_after_first = (vault / "Daily/2026/2026-07-09.md").read_text()

    outcome = sync_target(now_iso="2026-07-09T07:00:00", **kwargs)
    text_after_second = (vault / "Daily/2026/2026-07-09.md").read_text()

    assert not outcome.forked
    assert text_after_first != text_after_second  # last_synced differs
    stripped_first = text_after_first.replace("2026-07-09T06:00:00", "X")
    stripped_second = text_after_second.replace("2026-07-09T07:00:00", "X")
    assert stripped_first == stripped_second


def test_source_change_on_untouched_tip_fully_regenerates(vault: Path, state: StateDB) -> None:
    common = _common(state)
    sync_target(vault_root=vault, lines=[_task_line("Buy groceries")], now_iso="t1", **common)
    sync_target(vault_root=vault, lines=[_task_line("Buy groceries and milk")], now_iso="t2", **common)

    text = (vault / "Daily/2026/2026-07-09.md").read_text()
    assert "- [ ] Buy groceries and milk" in text
    assert "- [ ] Buy groceries\n" not in text


def test_sibling_and_nested_entries_render_with_no_blank_line_between(vault: Path, state: StateDB) -> None:
    lines = [
        RenderedLine(block_id="lp-1234-3-0", text="- Team sync", entry_type="event", entry_index=0),
        RenderedLine(
            block_id="lp-1234-3-1", text="    - = Focused and productive",
            entry_type="mood", entry_index=1,
        ),
        RenderedLine(block_id="lp-1234-3-2", text="- Weekly planning meeting", entry_type="event", entry_index=2),
        RenderedLine(
            block_id="lp-1234-3-3", text="    - Follow up on open action items",
            entry_type="task", entry_index=3,
        ),
    ]
    sync_target(vault_root=vault, lines=lines, now_iso="t1", **_common(state))

    text = (vault / "Daily/2026/2026-07-09.md").read_text()
    assert (
        "- Team sync\n"
        "    - = Focused and productive\n"
        "- Weekly planning meeting\n"
        "    - Follow up on open action items"
    ) in text


def test_needs_review_entry_renders_inline_not_in_a_separate_section(vault: Path, state: StateDB) -> None:
    """See issue #6: a low-confidence entry stays right where it was written,
    annotated inline, instead of being pulled into a separate section."""
    lines = [
        RenderedLine(block_id="lp-1234-3-0", text="- Team sync", entry_type="event", entry_index=0),
        RenderedLine(
            block_id="lp-1234-3-1", text="    - garbled text (confidence 0.30 — below threshold 0.60)",
            entry_type="task", entry_index=1, needs_review=True,
        ),
        RenderedLine(block_id="lp-1234-3-2", text="- Weekly planning meeting", entry_type="event", entry_index=2),
    ]
    sync_target(vault_root=vault, lines=lines, now_iso="t1", **_common(state))

    text = (vault / "Daily/2026/2026-07-09.md").read_text()
    assert "Needs Review" not in text
    assert (
        "- Team sync\n"
        "    - garbled text (confidence 0.30 — below threshold 0.60)\n"
        "- Weekly planning meeting"
    ) in text


def test_dropped_line_disappears_from_next_regeneration(vault: Path, state: StateDB) -> None:
    common = _common(state)
    two_lines = [_task_line("Keep me", "lp-1234-3-0"), _task_line("Drop me", "lp-1234-3-1")]
    sync_target(vault_root=vault, lines=two_lines, now_iso="t1", **common)
    text = (vault / "Daily/2026/2026-07-09.md").read_text()
    assert "Keep me" in text and "Drop me" in text

    sync_target(vault_root=vault, lines=[_task_line("Keep me", "lp-1234-3-0")], now_iso="t2", **common)
    text = (vault / "Daily/2026/2026-07-09.md").read_text()
    assert "Keep me" in text
    assert "Drop me" not in text


def test_human_edit_anywhere_triggers_fork(vault: Path, state: StateDB) -> None:
    common = _common(state)
    sync_target(vault_root=vault, lines=[_task_line("Buy groceries")], now_iso="t1", **common)

    path = vault / "Daily/2026/2026-07-09.md"
    edited = path.read_text().replace("- [ ] Buy groceries", "- [x] Buy groceries and eggs")
    path.write_text(edited)

    outcome = sync_target(vault_root=vault, lines=[_task_line("Buy groceries and milk")], now_iso="t2", **common)

    assert outcome.forked
    assert outcome.forked_from == "Daily/2026/2026-07-09.md"
    assert outcome.resolved_path == "Daily/2026/2026-07-09 (cont. 1).md"

    # Old file: human edit preserved verbatim, plus exactly one backlink annotation.
    old_text = path.read_text()
    assert "Buy groceries and eggs" in old_text
    assert "continued_in: Daily/2026/2026-07-09 (cont. 1).md" in old_text
    assert old_text.count("Continued in") == 1

    # New file: full accumulated entry set, with a mirror backlink.
    new_path = vault / "Daily/2026/2026-07-09 (cont. 1).md"
    new_text = new_path.read_text()
    assert "Buy groceries and milk" in new_text
    assert "continued_from: Daily/2026/2026-07-09.md" in new_text
    assert "Continued from" in new_text


def test_untouched_edit_after_edit_forks_do_not_reoccur(vault: Path, state: StateDB) -> None:
    """Once forked, further untouched syncs update the new tip in place — no
    re-fork just because the target's history includes a prior fork."""
    common = _common(state)
    sync_target(vault_root=vault, lines=[_task_line("A")], now_iso="t1", **common)
    (vault / "Daily/2026/2026-07-09.md").write_text(
        (vault / "Daily/2026/2026-07-09.md").read_text().replace("A", "A (edited)")
    )
    sync_target(vault_root=vault, lines=[_task_line("B")], now_iso="t2", **common)

    outcome = sync_target(vault_root=vault, lines=[_task_line("B updated")], now_iso="t3", **common)
    assert not outcome.forked
    assert outcome.resolved_path == "Daily/2026/2026-07-09 (cont. 1).md"
    assert "B updated" in (vault / "Daily/2026/2026-07-09 (cont. 1).md").read_text()


def test_repeated_edits_grow_a_chain(vault: Path, state: StateDB) -> None:
    common = _common(state)
    sync_target(vault_root=vault, lines=[_task_line("A")], now_iso="t1", **common)

    def edit(path: Path) -> None:
        path.write_text(path.read_text() + "\nedited by human\n")

    edit(vault / "Daily/2026/2026-07-09.md")
    outcome1 = sync_target(vault_root=vault, lines=[_task_line("B")], now_iso="t2", **common)
    assert outcome1.resolved_path == "Daily/2026/2026-07-09 (cont. 1).md"

    edit(vault / "Daily/2026/2026-07-09 (cont. 1).md")
    outcome2 = sync_target(vault_root=vault, lines=[_task_line("C")], now_iso="t3", **common)
    assert outcome2.resolved_path == "Daily/2026/2026-07-09 (cont. 2).md"

    tip3 = vault / "Daily/2026/2026-07-09 (cont. 2).md"
    assert "continued_from: Daily/2026/2026-07-09 (cont. 1).md" in tip3.read_text()
    tip2 = vault / "Daily/2026/2026-07-09 (cont. 1).md"
    assert "continued_in: Daily/2026/2026-07-09 (cont. 2).md" in tip2.read_text()


def test_fork_path_skips_on_disk_collision(vault: Path, state: StateDB) -> None:
    common = _common(state)
    sync_target(vault_root=vault, lines=[_task_line("A")], now_iso="t1", **common)
    (vault / "Daily/2026/2026-07-09.md").write_text(
        (vault / "Daily/2026/2026-07-09.md").read_text() + "\nedited\n"
    )
    # Simulate a crashed prior run that already wrote "(cont. 1)" but never
    # committed the DB's tip_seq update.
    (vault / "Daily/2026/2026-07-09 (cont. 1).md").write_text("orphaned file from a crashed run\n")

    outcome = sync_target(vault_root=vault, lines=[_task_line("B")], now_iso="t2", **common)
    assert outcome.resolved_path == "Daily/2026/2026-07-09 (cont. 2).md"


def test_missing_tip_with_no_frontmatter_match_forks(vault: Path, state: StateDB) -> None:
    common = _common(state)
    sync_target(vault_root=vault, lines=[_task_line("A")], now_iso="t1", **common)
    (vault / "Daily/2026/2026-07-09.md").unlink()

    outcome = sync_target(vault_root=vault, lines=[_task_line("B")], now_iso="t2", **common)
    assert outcome.forked
    assert outcome.resolved_path == "Daily/2026/2026-07-09 (cont. 1).md"


def test_renamed_but_untouched_tip_is_found_via_frontmatter_fallback(vault: Path, state: StateDB) -> None:
    common = _common(state)
    sync_target(vault_root=vault, lines=[_task_line("A")], now_iso="t1", **common)

    old_path = vault / "Daily/2026/2026-07-09.md"
    new_path = vault / "Daily/2026/2026-07-09-renamed.md"
    old_path.rename(new_path)

    outcome = sync_target(vault_root=vault, lines=[_task_line("A updated")], now_iso="t2", **common)

    assert not outcome.forked
    assert not old_path.exists()
    assert outcome.resolved_path == "Daily/2026/2026-07-09-renamed.md"
    assert "A updated" in new_path.read_text()


def test_entries_from_multiple_pages_accumulate_into_one_target(vault: Path, state: StateDB) -> None:
    state.upsert_page(
        PageRecord(
            page_id="1234-4", note_id="1234", page_number=4, page_content_sha256="p2",
            default_target_path="Daily/2026/2026-07-09.md",
        )
    )
    target_key = "Daily/2026/2026-07-09.md"
    sync_target(
        vault_root=vault, target_key=target_key, category="daily", entry_date="2026-07-09",
        page_id="1234-3", lines=[_task_line("From page 3", "lp-1234-3-0")], now_iso="t1", state=state,
    )
    sync_target(
        vault_root=vault, target_key=target_key, category="daily", entry_date="2026-07-09",
        page_id="1234-4", lines=[_task_line("From page 4", "lp-1234-4-0")], now_iso="t2", state=state,
    )

    text = (vault / "Daily/2026/2026-07-09.md").read_text()
    assert "From page 3" in text
    assert "From page 4" in text


def test_image_embeds_only_from_origin_pages(vault: Path, state: StateDB) -> None:
    """A migration destination gets the entry's live task line but not the source
    page's image embed — only the page's *default* (origin) target does."""
    backlog_key = "Future/2026/Backlog.md"
    daily_key = "Daily/2026/2026-07-09.md"

    # Origin target for page 1234-3 is the daily note.
    sync_target(
        vault_root=vault, target_key=daily_key, category="daily", entry_date="2026-07-09",
        page_id="1234-3", lines=[_task_line("Migrated task origin marker", "lp-1234-3-0")],
        now_iso="t1", state=state,
    )
    # Same page also contributes an entry to the backlog (migration destination).
    sync_target(
        vault_root=vault, target_key=backlog_key, category="daily", entry_date="2026-07-09",
        page_id="1234-3", lines=[_task_line("Migrated task", "lp-1234-3-0-dest")],
        now_iso="t2", state=state,
    )

    daily_text = (vault / daily_key).read_text()
    backlog_text = (vault / backlog_key).read_text()
    assert "Source Pages" in daily_text
    assert "Source Pages" not in backlog_text


def test_personal_content_anywhere_in_untouched_tip_is_preserved_until_next_sync_forks(
    vault: Path, state: StateDB
) -> None:
    common = _common(state)
    sync_target(vault_root=vault, lines=[_task_line()], now_iso="t1", **common)

    path = vault / "Daily/2026/2026-07-09.md"
    text = path.read_text() + "\n\n## My own reflections\nToday was a good day.\n"
    path.write_text(text)

    outcome = sync_target(vault_root=vault, lines=[_task_line("Buy groceries and milk")], now_iso="t2", **common)

    assert outcome.forked
    old_text = path.read_text()
    assert "## My own reflections" in old_text
    assert "Today was a good day." in old_text
