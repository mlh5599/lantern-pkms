from datetime import date
from pathlib import Path

import pytest

from home_pkms.taxonomy import TaxonomyConfig, slugify, source_page_path, sources_dir

CONFIG_PATH = Path(__file__).parent.parent / "config" / "taxonomy.default.yml"


@pytest.fixture(scope="module")
def taxonomy() -> TaxonomyConfig:
    return TaxonomyConfig.load(CONFIG_PATH)


def test_default_config_loads(taxonomy: TaxonomyConfig) -> None:
    assert taxonomy.source_root == "NOTE/Note/Journal"
    assert set(taxonomy.categories) == {"daily", "monthly", "future", "collections", "other"}


def test_categorize_path_daily(taxonomy: TaxonomyConfig) -> None:
    assert taxonomy.categorize_path("/NOTE/Note/Journal/Daily/2026/2026-07-09.note") == (
        "daily",
        2026,
        "2026-07-09",
    )


def test_categorize_path_title_based_category(taxonomy: TaxonomyConfig) -> None:
    assert taxonomy.categorize_path("/NOTE/Note/Journal/Collections/2026/Wines.note") == (
        "collections",
        2026,
        "Wines",
    )


def test_categorize_path_index_note_skipped(taxonomy: TaxonomyConfig) -> None:
    assert taxonomy.categorize_path("/NOTE/Note/Journal/Index.note") is None


def test_categorize_path_outside_source_root_skipped(taxonomy: TaxonomyConfig) -> None:
    assert taxonomy.categorize_path("/NOTE/Note/Scratch.note") is None
    assert taxonomy.categorize_path("/DOCUMENT/Document/Archive/file.note") is None


def test_categorize_path_missing_year_subfolder_skipped(taxonomy: TaxonomyConfig) -> None:
    # No exceptions to the <category>/<year>/<file> shape — a note directly under
    # a category folder with no year subfolder is skipped, not guessed at.
    assert taxonomy.categorize_path("/NOTE/Note/Journal/Future/Future Log.note") is None


def test_categorize_path_unrecognized_folder_skipped(taxonomy: TaxonomyConfig) -> None:
    assert taxonomy.categorize_path("/NOTE/Note/Journal/Random/2026/whatever.note") is None


def test_parse_entry_date_daily(taxonomy: TaxonomyConfig) -> None:
    assert taxonomy.parse_entry_date("daily", 2026, "2026-07-09") == date(2026, 7, 9)


def test_parse_entry_date_daily_with_suffix(taxonomy: TaxonomyConfig) -> None:
    assert taxonomy.parse_entry_date("daily", 2026, "2026-07-09 - Daily") == date(2026, 7, 9)


def test_parse_entry_date_monthly(taxonomy: TaxonomyConfig) -> None:
    assert taxonomy.parse_entry_date("monthly", 2026, "2026-07") == date(2026, 7, 1)


def test_parse_entry_date_none_for_non_calendar_category(taxonomy: TaxonomyConfig) -> None:
    assert taxonomy.parse_entry_date("future", 2026, "Future Log") is None


def test_default_target_path_daily(taxonomy: TaxonomyConfig) -> None:
    path = taxonomy.default_target_path("daily", 2026, "ignored", date(2026, 7, 9))
    assert path == "Daily/2026/2026-07-09.md"


def test_default_target_path_monthly(taxonomy: TaxonomyConfig) -> None:
    path = taxonomy.default_target_path("monthly", 2026, "ignored", date(2026, 7, 1))
    assert path == "Monthly/2026/2026-07.md"


def test_default_target_path_title_based(taxonomy: TaxonomyConfig) -> None:
    path = taxonomy.default_target_path("future", 2026, "Future Log", None)
    assert path == "Future/2026/future-log.md"


def test_backlog_path_uses_configured_backlog_category(taxonomy: TaxonomyConfig) -> None:
    assert taxonomy.backlog_path(2026) == "Future/2026/Backlog.md"


def test_slugify() -> None:
    assert slugify("2026 Future Log") == "2026-future-log"
    assert slugify("  Weird!!  Title??  ") == "weird-title"
    assert slugify("") == "untitled"


def test_sources_dir_and_source_page_path() -> None:
    assert sources_dir("1234") == "Sources/Supernote/1234"
    assert source_page_path("1234", 3) == "Sources/Supernote/1234/page-03.png"


def test_custom_taxonomy_config_is_fully_driven_by_config(tmp_path: Path) -> None:
    # Prove this isn't hardcoded — a totally different folder-naming convention
    # should work identically with no code changes.
    custom_yaml = """
version: 1
source_root: "NOTE/Note/MyBuJo"
index_note: "TOC.note"
categories:
  logs:
    source_folder: "Logs"
    vault_folder: "DailyLogs"
    date_format: "daily"
  projects:
    source_folder: "Projects"
    vault_folder: "Projects"
    date_format: "none"
backlog_category: "projects"
backlog_file_name: "Someday"
"""
    config_path = tmp_path / "custom-taxonomy.yml"
    config_path.write_text(custom_yaml)
    custom = TaxonomyConfig.load(config_path)

    assert custom.categorize_path("/NOTE/Note/MyBuJo/Logs/2026/2026-07-09.note") == (
        "logs",
        2026,
        "2026-07-09",
    )
    assert custom.default_target_path("logs", 2026, "ignored", date(2026, 7, 9)) == "DailyLogs/2026/2026-07-09.md"
    assert custom.backlog_path(2026) == "Projects/2026/Someday.md"
    assert custom.categorize_path("/NOTE/Note/MyBuJo/TOC.note") is None
