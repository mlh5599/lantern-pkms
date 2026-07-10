"""SQLite state tracking for idempotent ingestion.

Schema mirrors the plan: notes -> pages -> vault_entries. This module only persists
records — the decision logic for whether a vault_entry write is safe (ownership
handoff, once-per-divergence flagging) lives in vault/writer.py, which reads the
current row before deciding what to write next.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType

SCHEMA = """
CREATE TABLE IF NOT EXISTS notes (
    note_id TEXT PRIMARY KEY,
    category TEXT NOT NULL,
    folder_year INTEGER NOT NULL,
    file_name TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    supernote_gmt_modified TEXT,
    first_ingested_at TEXT NOT NULL,
    last_ingested_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active'
);

CREATE TABLE IF NOT EXISTS pages (
    page_id TEXT PRIMARY KEY,
    note_id TEXT NOT NULL REFERENCES notes(note_id),
    page_number INTEGER NOT NULL,
    page_content_sha256 TEXT NOT NULL,
    htr_json TEXT,
    htr_confidence_avg REAL,
    review_needed INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_pages_note_id ON pages(note_id);

CREATE TABLE IF NOT EXISTS vault_entries (
    entry_id TEXT PRIMARY KEY,
    page_id TEXT NOT NULL REFERENCES pages(page_id),
    entry_index INTEGER NOT NULL,
    entry_type TEXT NOT NULL,
    entry_date TEXT,
    category TEXT NOT NULL,
    migration_state TEXT,
    text TEXT NOT NULL,
    symbol_raw TEXT NOT NULL,
    obsidian_note_path TEXT NOT NULL,
    obsidian_block_id TEXT NOT NULL,
    needs_review INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'system_owned',
    last_written_text TEXT,
    last_seen_source_text TEXT
);
CREATE INDEX IF NOT EXISTS idx_vault_entries_page_id ON vault_entries(page_id);
"""

# vault_entries.status values
STATUS_SYSTEM_OWNED = "system_owned"
STATUS_USER_MODIFIED = "user_modified"
STATUS_USER_DELETED = "user_deleted"


def make_block_id(note_id: str, page_number: int, entry_index: int) -> str:
    """Stable block id for an entry, e.g. 'hp-1234-3-0' (Obsidian ref: ^hp-1234-3-0)."""
    return f"hp-{note_id}-{page_number}-{entry_index}"


@dataclass
class NoteRecord:
    note_id: str
    category: str
    folder_year: int
    file_name: str
    content_sha256: str
    first_ingested_at: str
    last_ingested_at: str
    supernote_gmt_modified: str | None = None
    status: str = "active"


@dataclass
class PageRecord:
    page_id: str
    note_id: str
    page_number: int
    page_content_sha256: str
    htr_json: str | None = None
    htr_confidence_avg: float | None = None
    review_needed: bool = False


@dataclass
class VaultEntryRecord:
    entry_id: str
    page_id: str
    entry_index: int
    entry_type: str
    category: str
    text: str
    symbol_raw: str
    obsidian_note_path: str
    obsidian_block_id: str
    updated_at: str
    entry_date: str | None = None
    migration_state: str | None = None
    needs_review: bool = False
    status: str = STATUS_SYSTEM_OWNED
    last_written_text: str | None = None
    last_seen_source_text: str | None = None


class StateDB:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._conn = sqlite3.connect(path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    def __enter__(self) -> "StateDB":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        self._conn.commit()
        self._conn.close()

    # -- notes -----------------------------------------------------------------

    def upsert_note(self, note: NoteRecord) -> None:
        existing = self.get_note(note.note_id)
        first_ingested_at = existing.first_ingested_at if existing else note.first_ingested_at
        self._conn.execute(
            """
            INSERT INTO notes (
                note_id, category, folder_year, file_name, content_sha256,
                supernote_gmt_modified, first_ingested_at, last_ingested_at, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(note_id) DO UPDATE SET
                category = excluded.category,
                folder_year = excluded.folder_year,
                file_name = excluded.file_name,
                content_sha256 = excluded.content_sha256,
                supernote_gmt_modified = excluded.supernote_gmt_modified,
                last_ingested_at = excluded.last_ingested_at,
                status = excluded.status
            """,
            (
                note.note_id,
                note.category,
                note.folder_year,
                note.file_name,
                note.content_sha256,
                note.supernote_gmt_modified,
                first_ingested_at,
                note.last_ingested_at,
                note.status,
            ),
        )
        self._conn.commit()

    def get_note(self, note_id: str) -> NoteRecord | None:
        row = self._conn.execute("SELECT * FROM notes WHERE note_id = ?", (note_id,)).fetchone()
        return _row_to_note(row) if row else None

    # -- pages -------------------------------------------------------------------

    def upsert_page(self, page: PageRecord) -> None:
        self._conn.execute(
            """
            INSERT INTO pages (
                page_id, note_id, page_number, page_content_sha256,
                htr_json, htr_confidence_avg, review_needed
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(page_id) DO UPDATE SET
                page_content_sha256 = excluded.page_content_sha256,
                htr_json = excluded.htr_json,
                htr_confidence_avg = excluded.htr_confidence_avg,
                review_needed = excluded.review_needed
            """,
            (
                page.page_id,
                page.note_id,
                page.page_number,
                page.page_content_sha256,
                page.htr_json,
                page.htr_confidence_avg,
                int(page.review_needed),
            ),
        )
        self._conn.commit()

    def get_page(self, page_id: str) -> PageRecord | None:
        row = self._conn.execute("SELECT * FROM pages WHERE page_id = ?", (page_id,)).fetchone()
        return _row_to_page(row) if row else None

    def get_pages_for_note(self, note_id: str) -> list[PageRecord]:
        rows = self._conn.execute(
            "SELECT * FROM pages WHERE note_id = ? ORDER BY page_number", (note_id,)
        ).fetchall()
        return [_row_to_page(r) for r in rows]

    # -- vault_entries -------------------------------------------------------------

    def upsert_vault_entry(self, entry: VaultEntryRecord) -> None:
        self._conn.execute(
            """
            INSERT INTO vault_entries (
                entry_id, page_id, entry_index, entry_type, entry_date, category,
                migration_state, text, symbol_raw, obsidian_note_path,
                obsidian_block_id, needs_review, updated_at, status,
                last_written_text, last_seen_source_text
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(entry_id) DO UPDATE SET
                entry_type = excluded.entry_type,
                entry_date = excluded.entry_date,
                category = excluded.category,
                migration_state = excluded.migration_state,
                text = excluded.text,
                symbol_raw = excluded.symbol_raw,
                obsidian_note_path = excluded.obsidian_note_path,
                obsidian_block_id = excluded.obsidian_block_id,
                needs_review = excluded.needs_review,
                updated_at = excluded.updated_at,
                status = excluded.status,
                last_written_text = excluded.last_written_text,
                last_seen_source_text = excluded.last_seen_source_text
            """,
            (
                entry.entry_id,
                entry.page_id,
                entry.entry_index,
                entry.entry_type,
                entry.entry_date,
                entry.category,
                entry.migration_state,
                entry.text,
                entry.symbol_raw,
                entry.obsidian_note_path,
                entry.obsidian_block_id,
                int(entry.needs_review),
                entry.updated_at,
                entry.status,
                entry.last_written_text,
                entry.last_seen_source_text,
            ),
        )
        self._conn.commit()

    def get_vault_entry(self, entry_id: str) -> VaultEntryRecord | None:
        row = self._conn.execute(
            "SELECT * FROM vault_entries WHERE entry_id = ?", (entry_id,)
        ).fetchone()
        return _row_to_entry(row) if row else None

    def get_vault_entries_for_page(self, page_id: str) -> list[VaultEntryRecord]:
        rows = self._conn.execute(
            "SELECT * FROM vault_entries WHERE page_id = ? ORDER BY entry_index", (page_id,)
        ).fetchall()
        return [_row_to_entry(r) for r in rows]


def _row_to_note(row: sqlite3.Row) -> NoteRecord:
    return NoteRecord(
        note_id=row["note_id"],
        category=row["category"],
        folder_year=row["folder_year"],
        file_name=row["file_name"],
        content_sha256=row["content_sha256"],
        supernote_gmt_modified=row["supernote_gmt_modified"],
        first_ingested_at=row["first_ingested_at"],
        last_ingested_at=row["last_ingested_at"],
        status=row["status"],
    )


def _row_to_page(row: sqlite3.Row) -> PageRecord:
    return PageRecord(
        page_id=row["page_id"],
        note_id=row["note_id"],
        page_number=row["page_number"],
        page_content_sha256=row["page_content_sha256"],
        htr_json=row["htr_json"],
        htr_confidence_avg=row["htr_confidence_avg"],
        review_needed=bool(row["review_needed"]),
    )


def _row_to_entry(row: sqlite3.Row) -> VaultEntryRecord:
    return VaultEntryRecord(
        entry_id=row["entry_id"],
        page_id=row["page_id"],
        entry_index=row["entry_index"],
        entry_type=row["entry_type"],
        entry_date=row["entry_date"],
        category=row["category"],
        migration_state=row["migration_state"],
        text=row["text"],
        symbol_raw=row["symbol_raw"],
        obsidian_note_path=row["obsidian_note_path"],
        obsidian_block_id=row["obsidian_block_id"],
        needs_review=bool(row["needs_review"]),
        updated_at=row["updated_at"],
        status=row["status"],
        last_written_text=row["last_written_text"],
        last_seen_source_text=row["last_seen_source_text"],
    )
