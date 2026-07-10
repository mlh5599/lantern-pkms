from pathlib import Path

import pytest

from home_pkms.state.db import (
    STATUS_USER_MODIFIED,
    NoteRecord,
    PageRecord,
    StateDB,
    VaultEntryRecord,
    make_block_id,
)


@pytest.fixture()
def db(tmp_path: Path) -> StateDB:
    with StateDB(tmp_path / "state.db") as db:
        yield db


def test_make_block_id() -> None:
    assert make_block_id("1234", 3, 0) == "hp-1234-3-0"


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
        PageRecord(page_id="1234-1", note_id="1234", page_number=1, page_content_sha256="p1")
    )
    db.upsert_page(
        PageRecord(page_id="1234-2", note_id="1234", page_number=2, page_content_sha256="p2")
    )
    pages = db.get_pages_for_note("1234")
    assert [p.page_number for p in pages] == [1, 2]


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
        PageRecord(page_id=page_id, note_id=note_id, page_number=3, page_content_sha256="p1")
    )


def test_vault_entry_roundtrip(db: StateDB) -> None:
    _seed_note_and_page(db)
    entry = VaultEntryRecord(
        entry_id="hp-1234-3-0",
        page_id="1234-3",
        entry_index=0,
        entry_type="task",
        category="daily",
        text="Buy groceries",
        symbol_raw="bullet",
        obsidian_note_path="Daily/2026/2026-07-09.md",
        obsidian_block_id="hp-1234-3-0",
        updated_at="2026-07-09T06:00:00",
    )
    db.upsert_vault_entry(entry)
    fetched = db.get_vault_entry("hp-1234-3-0")
    assert fetched is not None
    assert fetched.text == "Buy groceries"
    assert fetched.status == "system_owned"


def test_vault_entry_status_transition_persists(db: StateDB) -> None:
    _seed_note_and_page(db)
    entry = VaultEntryRecord(
        entry_id="hp-1234-3-0",
        page_id="1234-3",
        entry_index=0,
        entry_type="task",
        category="daily",
        text="Buy groceries",
        symbol_raw="bullet",
        obsidian_note_path="Daily/2026/2026-07-09.md",
        obsidian_block_id="hp-1234-3-0",
        updated_at="2026-07-09T06:00:00",
        last_written_text="- [ ] Buy groceries ^hp-1234-3-0",
    )
    db.upsert_vault_entry(entry)

    entry.status = STATUS_USER_MODIFIED
    entry.text = "Buy groceries and milk"
    entry.updated_at = "2026-07-10T06:00:00"
    db.upsert_vault_entry(entry)

    fetched = db.get_vault_entry("hp-1234-3-0")
    assert fetched is not None
    assert fetched.status == STATUS_USER_MODIFIED
    assert fetched.text == "Buy groceries and milk"


def test_get_vault_entries_for_page_ordered_by_entry_index(db: StateDB) -> None:
    _seed_note_and_page(db)
    for i in (2, 0, 1):
        db.upsert_vault_entry(
            VaultEntryRecord(
                entry_id=f"hp-1234-3-{i}",
                page_id="1234-3",
                entry_index=i,
                entry_type="task",
                category="daily",
                text=f"entry {i}",
                symbol_raw="bullet",
                obsidian_note_path="Daily/2026/2026-07-09.md",
                obsidian_block_id=f"hp-1234-3-{i}",
                updated_at="t",
            )
        )
    entries = db.get_vault_entries_for_page("1234-3")
    assert [e.entry_index for e in entries] == [0, 1, 2]
