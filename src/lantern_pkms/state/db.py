"""SQLite state tracking for idempotent ingestion.

Schema: notes -> pages -> vault_entries, plus targets (one row per logical vault
note/chain). A "target" is a taxonomy-resolved destination (e.g. a Daily note or
the Backlog) that accumulates entries from many pages over time; vault_entries
records what's been transcribed, targets tracks which file on disk is the current
chain tip and whether it's still safe to regenerate. See vault/writer.py for the
touch-detection and fork logic that reads these records.
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
    default_target_path TEXT NOT NULL,
    htr_json TEXT,
    htr_confidence_avg REAL,
    review_needed INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_pages_note_id ON pages(note_id);

CREATE TABLE IF NOT EXISTS targets (
    target_key TEXT PRIMARY KEY,
    category TEXT NOT NULL,
    entry_date TEXT,
    tip_path TEXT NOT NULL,
    tip_seq INTEGER NOT NULL DEFAULT 0,
    last_written_hash TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS vault_entries (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_id TEXT NOT NULL UNIQUE,
    target_key TEXT NOT NULL REFERENCES targets(target_key),
    page_id TEXT NOT NULL REFERENCES pages(page_id),
    entry_index INTEGER NOT NULL,
    entry_type TEXT NOT NULL,
    entry_date TEXT,
    category TEXT NOT NULL,
    migration_state TEXT,
    text TEXT NOT NULL,
    symbol_raw TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_vault_entries_target_key ON vault_entries(target_key);
CREATE INDEX IF NOT EXISTS idx_vault_entries_page_id ON vault_entries(page_id);
"""


def make_block_id(note_id: str, page_number: int, entry_index: int) -> str:
    """Stable id for an entry, e.g. 'lp-1234-3-0' — used as vault_entries' primary
    key, not rendered into the vault file (see vault/writer.py)."""
    return f"lp-{note_id}-{page_number}-{entry_index}"


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
    default_target_path: str
    htr_json: str | None = None
    htr_confidence_avg: float | None = None
    review_needed: bool = False


@dataclass
class TargetRecord:
    target_key: str
    category: str
    tip_path: str
    created_at: str
    updated_at: str
    entry_date: str | None = None
    tip_seq: int = 0
    last_written_hash: str | None = None


@dataclass
class VaultEntryRecord:
    entry_id: str
    target_key: str
    page_id: str
    entry_index: int
    entry_type: str
    category: str
    text: str
    symbol_raw: str
    updated_at: str
    entry_date: str | None = None
    migration_state: str | None = None


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
                default_target_path, htr_json, htr_confidence_avg, review_needed
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(page_id) DO UPDATE SET
                page_content_sha256 = excluded.page_content_sha256,
                default_target_path = excluded.default_target_path,
                htr_json = excluded.htr_json,
                htr_confidence_avg = excluded.htr_confidence_avg,
                review_needed = excluded.review_needed
            """,
            (
                page.page_id,
                page.note_id,
                page.page_number,
                page.page_content_sha256,
                page.default_target_path,
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

    # -- targets -------------------------------------------------------------------

    def upsert_target(self, target: TargetRecord) -> None:
        existing = self.get_target(target.target_key)
        created_at = existing.created_at if existing else target.created_at
        self._conn.execute(
            """
            INSERT INTO targets (
                target_key, category, entry_date, tip_path, tip_seq,
                last_written_hash, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(target_key) DO UPDATE SET
                category = excluded.category,
                entry_date = excluded.entry_date,
                tip_path = excluded.tip_path,
                tip_seq = excluded.tip_seq,
                last_written_hash = excluded.last_written_hash,
                updated_at = excluded.updated_at
            """,
            (
                target.target_key,
                target.category,
                target.entry_date,
                target.tip_path,
                target.tip_seq,
                target.last_written_hash,
                created_at,
                target.updated_at,
            ),
        )
        self._conn.commit()

    def get_target(self, target_key: str) -> TargetRecord | None:
        row = self._conn.execute(
            "SELECT * FROM targets WHERE target_key = ?", (target_key,)
        ).fetchone()
        return _row_to_target(row) if row else None

    # -- vault_entries -------------------------------------------------------------

    def replace_page_entries_for_target(
        self, target_key: str, page_id: str, entries: list[VaultEntryRecord]
    ) -> None:
        """Upsert this page's current entries for `target_key`, pruning any of this
        page's previously-recorded entries for this target that are no longer
        present (a line dropped from a re-transcribed page disappears from the next
        regeneration of an untouched tip)."""
        keep_ids = [e.entry_id for e in entries]
        for entry in entries:
            self._conn.execute(
                """
                INSERT INTO vault_entries (
                    entry_id, target_key, page_id, entry_index, entry_type,
                    entry_date, category, migration_state, text,
                    symbol_raw, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(entry_id) DO UPDATE SET
                    target_key = excluded.target_key,
                    page_id = excluded.page_id,
                    entry_index = excluded.entry_index,
                    entry_type = excluded.entry_type,
                    entry_date = excluded.entry_date,
                    category = excluded.category,
                    migration_state = excluded.migration_state,
                    text = excluded.text,
                    symbol_raw = excluded.symbol_raw,
                    updated_at = excluded.updated_at
                """,
                (
                    entry.entry_id,
                    entry.target_key,
                    entry.page_id,
                    entry.entry_index,
                    entry.entry_type,
                    entry.entry_date,
                    entry.category,
                    entry.migration_state,
                    entry.text,
                    entry.symbol_raw,
                    entry.updated_at,
                ),
            )
        if keep_ids:
            placeholders = ",".join("?" * len(keep_ids))
            self._conn.execute(
                f"""
                DELETE FROM vault_entries
                WHERE page_id = ? AND target_key = ? AND entry_id NOT IN ({placeholders})
                """,
                (page_id, target_key, *keep_ids),
            )
        else:
            self._conn.execute(
                "DELETE FROM vault_entries WHERE page_id = ? AND target_key = ?",
                (page_id, target_key),
            )
        self._conn.commit()

    def get_vault_entries_for_target(self, target_key: str) -> list[VaultEntryRecord]:
        rows = self._conn.execute(
            "SELECT * FROM vault_entries WHERE target_key = ? ORDER BY seq", (target_key,)
        ).fetchall()
        return [_row_to_entry(r) for r in rows]

    def get_contributing_pages(self, target_key: str) -> list[tuple[str, int]]:
        """Distinct (note_id, page_number) pairs that have contributed any entry to
        this target — for `source_notes` frontmatter provenance."""
        rows = self._conn.execute(
            """
            SELECT DISTINCT p.note_id, p.page_number
            FROM vault_entries v JOIN pages p ON v.page_id = p.page_id
            WHERE v.target_key = ?
            ORDER BY p.note_id, p.page_number
            """,
            (target_key,),
        ).fetchall()
        return [(r["note_id"], r["page_number"]) for r in rows]

    def get_origin_pages(self, target_key: str) -> list[tuple[str, int]]:
        """Distinct (note_id, page_number) pairs for which this target is the page's
        *origin* (not a migration destination) — for source-image embeds only."""
        rows = self._conn.execute(
            """
            SELECT DISTINCT p.note_id, p.page_number
            FROM vault_entries v JOIN pages p ON v.page_id = p.page_id
            WHERE v.target_key = ? AND p.default_target_path = ?
            ORDER BY p.note_id, p.page_number
            """,
            (target_key, target_key),
        ).fetchall()
        return [(r["note_id"], r["page_number"]) for r in rows]


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
        default_target_path=row["default_target_path"],
        htr_json=row["htr_json"],
        htr_confidence_avg=row["htr_confidence_avg"],
        review_needed=bool(row["review_needed"]),
    )


def _row_to_target(row: sqlite3.Row) -> TargetRecord:
    return TargetRecord(
        target_key=row["target_key"],
        category=row["category"],
        entry_date=row["entry_date"],
        tip_path=row["tip_path"],
        tip_seq=row["tip_seq"],
        last_written_hash=row["last_written_hash"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _row_to_entry(row: sqlite3.Row) -> VaultEntryRecord:
    return VaultEntryRecord(
        entry_id=row["entry_id"],
        target_key=row["target_key"],
        page_id=row["page_id"],
        entry_index=row["entry_index"],
        entry_type=row["entry_type"],
        entry_date=row["entry_date"],
        category=row["category"],
        migration_state=row["migration_state"],
        text=row["text"],
        symbol_raw=row["symbol_raw"],
        updated_at=row["updated_at"],
    )
