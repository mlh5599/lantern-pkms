"""Integration-style test for main._ingest_page.

Regression test: this is the exact path that broke live against real notes —
_ingest_page called sync_target() (which inserts a vault_entries row referencing
pages.page_id) before it called state.upsert_page() (which creates that pages
row), so every single page hit `sqlite3.IntegrityError: FOREIGN KEY constraint
failed` since foreign keys are enforced (state/db.py sets PRAGMA foreign_keys =
ON). No existing test caught this because test_vault_writer_idempotency.py and
test_state_db.py always pre-create the pages row by hand before exercising
sync_target/replace_page_entries_for_target directly — none of them go through
_ingest_page's own ordering of those two calls, which is exactly where the bug
was. Uses a
real StateDB (so FK enforcement is actually active) and a fake HTR client, so
it needs no live Supernote/Ollama, unlike the rest of the orchestration layer
(see main.py's module docstring).
"""

from pathlib import Path

import pytest

from lantern_pkms.config import Settings
from lantern_pkms.htr.ollama_client import OllamaError
from lantern_pkms.htr.prompts import build_transcription_prompt
from lantern_pkms.main import _ingest_page
from lantern_pkms.state.db import NoteRecord, StateDB
from lantern_pkms.structuring.symbol_mapping import SymbolMappingConfig, VLMLine
from lantern_pkms.supernote.client import SupernoteEntry
from lantern_pkms.taxonomy import TaxonomyConfig

CONFIG_DIR = Path(__file__).parent.parent / "config"


class _FakeNotebook:
    def render_page_png(self, page_number: int) -> bytes:
        return b"\x89PNG-fake-page-bytes"


class _FakeHTRClient:
    def transcribe_page(self, image_png_bytes: bytes, prompt: str) -> list[VLMLine]:
        return [VLMLine(raw_symbol="bullet", text="Buy groceries", confidence=0.9)]


class _FakeFailingHTRClient:
    def __init__(self) -> None:
        self.call_count = 0

    def transcribe_page(self, image_png_bytes: bytes, prompt: str) -> list[VLMLine]:
        self.call_count += 1
        raise OllamaError("Ollama response was not valid JSON: truncated")


@pytest.fixture()
def symbol_config() -> SymbolMappingConfig:
    return SymbolMappingConfig.load(CONFIG_DIR / "symbol-mapping.default.yml")


@pytest.fixture()
def taxonomy() -> TaxonomyConfig:
    return TaxonomyConfig.load(CONFIG_DIR / "taxonomy.default.yml")


def test_ingest_page_writes_page_row_before_vault_entry(tmp_path, symbol_config, taxonomy) -> None:
    vault_path = tmp_path / "Lantern"
    vault_path.mkdir()
    settings = Settings(
        ollama_host="ollama.example.com:11434",
        supernote_cloud_url="https://supernote.example.com",
        supernote_username="user",
        supernote_password="pass",
        vault_path=vault_path,
    )
    entry = SupernoteEntry(
        id="1234",
        name="2026-07-09 - Daily.note",
        path_display="/NOTE/Note/Journal/Daily/2026/2026-07-09 - Daily.note",
        is_folder=False,
        content_hash="h1",
        size=100,
        last_update_time_ms=1,
        parent_path="/NOTE/Note/Journal/Daily/2026",
    )

    with StateDB(tmp_path / "state.db") as state:
        # Mirrors what _ingest_note does before it ever calls _ingest_page in its loop.
        state.upsert_note(
            NoteRecord(
                note_id="1234",
                category="daily",
                folder_year=2026,
                file_name=entry.name,
                content_sha256="h1",
                first_ingested_at="t0",
                last_ingested_at="t0",
            )
        )
        _ingest_page(
            entry=entry,
            notebook=_FakeNotebook(),
            page_number=0,
            category="daily",
            year=2026,
            entry_date=None,
            title="2026-07-09",
            settings=settings,
            state=state,
            htr_client=_FakeHTRClient(),
            prompt=build_transcription_prompt(symbol_config),
            symbol_config=symbol_config,
            taxonomy=taxonomy,
        )

        page = state.get_page("1234-0")
        assert page is not None
        assert page.htr_confidence_avg == pytest.approx(0.9)


def _seed_settings_note_and_entry(tmp_path, state: StateDB) -> tuple[Settings, SupernoteEntry]:
    vault_path = tmp_path / "Lantern"
    vault_path.mkdir()
    settings = Settings(
        ollama_host="ollama.example.com:11434",
        supernote_cloud_url="https://supernote.example.com",
        supernote_username="user",
        supernote_password="pass",
        vault_path=vault_path,
    )
    entry = SupernoteEntry(
        id="1234",
        name="2026-07-09 - Daily.note",
        path_display="/NOTE/Note/Journal/Daily/2026/2026-07-09 - Daily.note",
        is_folder=False,
        content_hash="h1",
        size=100,
        last_update_time_ms=1,
        parent_path="/NOTE/Note/Journal/Daily/2026",
    )
    state.upsert_note(
        NoteRecord(
            note_id="1234",
            category="daily",
            folder_year=2026,
            file_name=entry.name,
            content_sha256="h1",
            first_ingested_at="t0",
            last_ingested_at="t0",
        )
    )
    return settings, entry


def test_ingest_page_records_visible_failure_when_htr_fails(tmp_path, symbol_config, taxonomy) -> None:
    with StateDB(tmp_path / "state.db") as state:
        settings, entry = _seed_settings_note_and_entry(tmp_path, state)

        with pytest.raises(OllamaError):
            _ingest_page(
                entry=entry,
                notebook=_FakeNotebook(),
                page_number=0,
                category="daily",
                year=2026,
                entry_date=None,
                title="2026-07-09",
                settings=settings,
                state=state,
                htr_client=_FakeFailingHTRClient(),
                prompt=build_transcription_prompt(symbol_config),
                symbol_config=symbol_config,
                taxonomy=taxonomy,
            )

        page = state.get_page("1234-0")
        assert page is not None
        assert page.page_content_sha256.startswith("htr-failed:")
        assert page.review_needed is True

        vault_file = settings.vault_path / "Journal/Daily/2026/2026-07-09.md"
        assert vault_file.exists()
        assert "HTR failed to transcribe this page" in vault_file.read_text()


def test_ingest_page_retries_htr_failure_on_next_run_instead_of_skipping(tmp_path, symbol_config, taxonomy) -> None:
    with StateDB(tmp_path / "state.db") as state:
        settings, entry = _seed_settings_note_and_entry(tmp_path, state)
        client = _FakeFailingHTRClient()

        for _ in range(2):
            with pytest.raises(OllamaError):
                _ingest_page(
                    entry=entry,
                    notebook=_FakeNotebook(),
                    page_number=0,
                    category="daily",
                    year=2026,
                    entry_date=None,
                    title="2026-07-09",
                    settings=settings,
                    state=state,
                    htr_client=client,
                    prompt=build_transcription_prompt(symbol_config),
                    symbol_config=symbol_config,
                    taxonomy=taxonomy,
                )

        # The sentinel page hash never matches the real one, so the skip-if-
        # unchanged check at the top of _ingest_page never short-circuits this
        # page — it must have actually retried HTR both times, not skipped.
        assert client.call_count == 2
