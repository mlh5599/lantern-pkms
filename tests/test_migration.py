from datetime import date

import pytest

from lantern_pkms.structuring.migration import compute_migration, is_migration_state


def test_non_migration_state_returns_none() -> None:
    assert compute_migration("open", date(2026, 7, 9)) is None
    assert compute_migration("complete", date(2026, 7, 9)) is None
    assert compute_migration(None, date(2026, 7, 9)) is None


def test_migrated_next_day() -> None:
    dest = compute_migration("migrated_next_day", date(2026, 7, 9))
    assert dest is not None
    assert dest.kind == "next_day"
    assert dest.target_date == date(2026, 7, 10)


def test_migrated_next_day_across_month_boundary() -> None:
    dest = compute_migration("migrated_next_day", date(2026, 7, 31))
    assert dest is not None
    assert dest.target_date == date(2026, 8, 1)


def test_migrated_backlog_has_no_date() -> None:
    dest = compute_migration("migrated_backlog", date(2026, 7, 9))
    assert dest is not None
    assert dest.kind == "backlog"
    assert dest.target_date is None


def test_migrated_next_day_requires_origin_date() -> None:
    with pytest.raises(ValueError):
        compute_migration("migrated_next_day", None)


def test_is_migration_state() -> None:
    assert is_migration_state("migrated_backlog")
    assert is_migration_state("migrated_next_day")
    assert not is_migration_state("open")
    assert not is_migration_state(None)
