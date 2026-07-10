"""Migration-destination logic for bullet-journal '<' (backlog) / '>' (next day) marks.

Pure logic only — resolving a destination into an actual vault path is
vault/paths.py's job, kept separate so this stays testable without any filesystem
or vault-layout knowledge.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Literal

MIGRATED_BACKLOG = "migrated_backlog"
MIGRATED_NEXT_DAY = "migrated_next_day"

_MIGRATION_STATES = {MIGRATED_BACKLOG, MIGRATED_NEXT_DAY}


@dataclass(frozen=True)
class MigrationDestination:
    kind: Literal["next_day", "backlog"]
    target_date: date | None  # set only for next_day


def is_migration_state(state: str | None) -> bool:
    return state in _MIGRATION_STATES


def compute_migration(state: str | None, origin_date: date | None) -> MigrationDestination | None:
    """Return the destination for a migrated entry, or None if not a migration.

    origin_date is required for next_day migrations (needed to compute the target
    date) but not for backlog migrations, which have no calendar anchor.
    """
    if state == MIGRATED_NEXT_DAY:
        if origin_date is None:
            raise ValueError("origin_date is required to compute a next_day migration")
        return MigrationDestination(kind="next_day", target_date=origin_date + timedelta(days=1))
    if state == MIGRATED_BACKLOG:
        return MigrationDestination(kind="backlog", target_date=None)
    return None
