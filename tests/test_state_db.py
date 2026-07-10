from pathlib import Path

import pytest

from lantern_pkms.state.db import (
    NoteRecord,
    PageRecord,
    StateDB,
    TargetRecord,
    VaultEntryRecord,
    make_block_id,
)


@pytest.fixture()
def db(tmp_path: Path) -> StateDB:
    with StateDB(tmp_path / "state.db") as db:
        yield db


def test_make_block_id() -> None:
    assert make_block_id("1234", 3, 0) == "lp-1234-3-0"


def test_note_roundtrip(db: StateDB) -> None:
    note = NoteRecord(
        note_id="1234",
        category="daily",
        folder_year=2026,
        file_name="2026-07-09.note",
        content_sha256="abc123",
        first_ingested_at="2026-07-09T06:00:00",
        last_ingested_at="2026-07-09T06:00:00",
    )
    db.upsert_note(note)
    fetched = db.get_note("1234")
    assert fetched is not None
    assert fetched.file_name == "2026-07-09.note"
    assert fetched.status == "active"


def test_note_upsert_preserves_first_ingested_at(db: StateDB) -> None:
    db.upsert_note(
        NoteRecord(
            note_id="1234",
            category="daily",
            folder_year=2026,
            file_name="a.note",
            content_sha256="v1",
            first_ingested_at="2026-07-01T00:00:00",
            last_ingested_at="2026-07-01T00:00:00",
        )
    )
    db.upsert_note(
        NoteRecord(
            note_id="1234",
            category="daily",
            folder_year=2026,
            file_name="a.note",
            content_sha256="v2",
            first_ingested_at="2026-07-09T00:00:00",  # should be ignored on update
            last_ingested_at="2026-07-09T00:00:00",
        )
    )
    fetched = db.get_note("1234")
    assert fetched is not None
    assert fetched.first_ingested_at == "2026-07-01T00:00:00"
    assert fetched.last_ingested_at == "2026-07-09T00:00:00"
    assert fetched.content_sha256 == "v2"


def test_get_missing_note_returns_none(db: StateDB) -> None:
    assert db.get_note("nope") is None


def test_page_roundtrip_and_list_by_note(db: StateDB) -> None:
    db.upsert_note(
        NoteRecord(
            note_id="1234",
            category="daily",
            folder_year=2026,
            file_name="a.note",
            content_sha256="v1",
            first_ingested_at="t",
            last_ingested_at="t",
        )
    )
    db.upsert_page(
        PageRecord(
            page_id="1234-1", note_id="1234", page_number=1, page_content_sha256="p1",
            default_target_path="Daily/2026/2026-07-09.md",
        )
    )
    db.upsert_page(
        PageRecord(
            page_id="1234-2", note_id="1234", page_number=2, page_content_sha256="p2",
            default_target_path="Daily/2026/2026-07-10.md",
        )
    )
    pages = db.get_pages_for_note("1234")
    assert [p.page_number for p in pages] == [1, 2]
    assert pages[0].default_target_path == "Daily/2026/2026-07-09.md"


def _seed_note_and_page(db: StateDB, note_id: str = "1234", page_id: str = "1234-3") -> None:
    db.upsert_note(
        NoteRecord(
            note_id=note_id,
            category="daily",
            folder_year=2026,
            file_name="a.note",
            content_sha256="v1",
            first_ingested_at="t",
            last_ingested_at="t",
        )
    )
    db.upsert_page(
        PageRecord(
            page_id=page_id, note_id=note_id, page_number=3, page_content_sha256="p1",
            default_target_path="Daily/2026/2026-07-09.md",
        )
    )


def test_target_roundtrip(db: StateDB) -> None:
    target = TargetRecord(
        target_key="Daily/2026/2026-07-09.md",
        category="daily",
        entry_date="2026-07-09",
        tip_path="Daily/2026/2026-07-09.md",
        created_at="t0",
        updated_at="t0",
    )
    db.upsert_target(target)
    fetched = db.get_target("Daily/2026/2026-07-09.md")
    assert fetched is not None
    assert fetched.tip_path == "Daily/2026/2026-07-09.md"
    assert fetched.tip_seq == 0
    assert fetched.last_written_hash is None


def test_target_upsert_preserves_created_at(db: StateDB) -> None:
    db.upsert_target(
        TargetRecord(
            target_key="Future/2026/Backlog.md",
            category="backlog",
            tip_path="Future/2026/Backlog.md",
            created_at="t0",
            updated_at="t0",
        )
    )
    db.upsert_target(
        TargetRecord(
            target_key="Future/2026/Backlog.md",
            category="backlog",
            tip_path="Future/2026/Backlog (cont. 1).md",
            tip_seq=1,
            last_written_hash="deadbeef",
            created_at="t9",  # should be ignored on update
            updated_at="t1",
        )
    )
    fetched = db.get_target("Future/2026/Backlog.md")
    assert fetched is not None
    assert fetched.created_at == "t0"
    assert fetched.tip_path == "Future/2026/Backlog (cont. 1).md"
    assert fetched.tip_seq == 1
    assert fetched.last_written_hash == "deadbeef"


def test_get_missing_target_returns_none(db: StateDB) -> None:
    assert db.get_target("nope") is None


def test_replace_page_entries_for_target_upserts_and_prunes(db: StateDB) -> None:
    _seed_note_and_page(db)
    target_key = "Daily/2026/2026-07-09.md"
    db.upsert_target(
        TargetRecord(target_key=target_key, category="daily", tip_path=target_key, created_at="t", updated_at="t")
    )

    def entry(entry_id: str, index: int, text: str) -> VaultEntryRecord:
        return VaultEntryRecord(
            entry_id=entry_id, target_key=target_key, page_id="1234-3", entry_index=index,
            entry_type="task", category="daily", text=text, symbol_raw="bullet", updated_at="t",
        )

    db.replace_page_entries_for_target(
        target_key, "1234-3", [entry("lp-1234-3-0", 0, "a"), entry("lp-1234-3-1", 1, "b")]
    )
    assert [e.text for e in db.get_vault_entries_for_target(target_key)] == ["a", "b"]

    # Re-transcription drops the second line and edits the first — the stale row
    # must be pruned, not left dangling.
    db.replace_page_entries_for_target(target_key, "1234-3", [entry("lp-1234-3-0", 0, "a edited")])
    assert [e.text for e in db.get_vault_entries_for_target(target_key)] == ["a edited"]


def test_replace_page_entries_only_prunes_the_same_page(db: StateDB) -> None:
    """Two different source pages contribute to the same target (e.g. a Backlog
    accumulating entries from many days) — updating one page's entries must not
    prune the other page's rows."""
    _seed_note_and_page(db)  # creates page "1234-3"
    db.upsert_page(
        PageRecord(
            page_id="1234-4", note_id="1234", page_number=4, page_content_sha256="p2",
            default_target_path="Daily/2026/2026-07-10.md",
        )
    )
    target_key = "Future/2026/Backlog.md"
    db.upsert_target(
        TargetRecord(target_key=target_key, category="backlog", tip_path=target_key, created_at="t", updated_at="t")
    )

    def entry(entry_id: str, page_id: str, text: str) -> VaultEntryRecord:
        return VaultEntryRecord(
            entry_id=entry_id, target_key=target_key, page_id=page_id, entry_index=0,
            entry_type="task", category="daily", text=text, symbol_raw="bullet", updated_at="t",
        )

    db.replace_page_entries_for_target(target_key, "1234-3", [entry("lp-a", "1234-3", "first")])
    db.replace_page_entries_for_target(target_key, "1234-4", [entry("lp-b", "1234-4", "second")])
    # Re-transcribing page 1234-4 edits its own entry but must not touch 1234-3's.
    db.replace_page_entries_for_target(target_key, "1234-4", [entry("lp-b", "1234-4", "second, edited")])

    entries = db.get_vault_entries_for_target(target_key)
    assert {e.entry_id: e.text for e in entries} == {"lp-a": "first", "lp-b": "second, edited"}


def test_get_vault_entries_for_target_ordered_by_seq(db: StateDB) -> None:
    _seed_note_and_page(db)
    target_key = "Daily/2026/2026-07-09.md"
    db.upsert_target(
        TargetRecord(target_key=target_key, category="daily", tip_path=target_key, created_at="t", updated_at="t")
    )
    entries = [
        VaultEntryRecord(
            entry_id=f"lp-1234-3-{i}", target_key=target_key, page_id="1234-3", entry_index=i,
            entry_type="task", category="daily", text=f"entry {i}", symbol_raw="bullet", updated_at="t",
        )
        for i in (2, 0, 1)
    ]
    db.replace_page_entries_for_target(target_key, "1234-3", entries)
    fetched = db.get_vault_entries_for_target(target_key)
    # seq reflects insertion order (2, 0, 1), not entry_index — this is rendering
    # order, driven by dict iteration order upstream, not a claim about page order.
    assert [e.entry_index for e in fetched] == [2, 0, 1]


def test_get_contributing_pages_and_origin_pages(db: StateDB) -> None:
    db.upsert_note(
        NoteRecord(
            note_id="1234", category="daily", folder_year=2026, file_name="a.note",
            content_sha256="v1", first_ingested_at="t", last_ingested_at="t",
        )
    )
    # page 3's origin target is the daily note; page 4's origin target is the
    # backlog (a migration destination for page 3 also lands an entry in the backlog).
    db.upsert_page(
        PageRecord(
            page_id="1234-3", note_id="1234", page_number=3, page_content_sha256="p3",
            default_target_path="Daily/2026/2026-07-09.md",
        )
    )
    daily_key = "Daily/2026/2026-07-09.md"
    backlog_key = "Future/2026/Backlog.md"
    for key in (daily_key, backlog_key):
        db.upsert_target(TargetRecord(target_key=key, category="daily", tip_path=key, created_at="t", updated_at="t"))

    db.replace_page_entries_for_target(
        daily_key, "1234-3",
        [VaultEntryRecord(entry_id="lp-a", target_key=daily_key, page_id="1234-3", entry_index=0,
                           entry_type="task", category="daily", text="a", symbol_raw="bullet", updated_at="t")],
    )
    db.replace_page_entries_for_target(
        backlog_key, "1234-3",
        [VaultEntryRecord(entry_id="lp-a-dest", target_key=backlog_key, page_id="1234-3", entry_index=0,
                           entry_type="task", category="daily", text="a", symbol_raw="bullet", updated_at="t")],
    )

    assert db.get_contributing_pages(daily_key) == [("1234", 3)]
    assert db.get_origin_pages(daily_key) == [("1234", 3)]
    # Backlog got an entry from page 3, but page 3's *origin* is the daily note —
    # the backlog shouldn't claim the source image embed for it.
    assert db.get_contributing_pages(backlog_key) == [("1234", 3)]
    assert db.get_origin_pages(backlog_key) == []
