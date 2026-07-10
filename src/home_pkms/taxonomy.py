"""Configurable Supernote-folder <-> Lantern-vault category taxonomy.

Externalized (config/taxonomy.default.yml) so this pipeline isn't hardcoded to any
one person's bullet-journal folder naming convention. Every category note must live
at <source_root>/<source_folder>/<year>/<file>.note — no exceptions, no fallback
guessing. If your Supernote folders don't fit that shape, reorganize them (or adjust
the config's folder names) rather than expecting this to guess around irregularities
— that's a deliberate simplicity/predictability tradeoff, not an oversight.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

import yaml
from pydantic import BaseModel

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def slugify(title: str) -> str:
    slug = _SLUG_RE.sub("-", title.strip().lower()).strip("-")
    return slug or "untitled"


class CategoryDef(BaseModel):
    source_folder: str
    vault_folder: str
    date_format: str = "none"  # "daily" | "monthly" | "none"


class TaxonomyConfig(BaseModel):
    version: int
    source_root: str
    index_note: str
    categories: dict[str, CategoryDef]
    backlog_category: str
    backlog_file_name: str = "Backlog"

    @classmethod
    def load(cls, path: Path) -> "TaxonomyConfig":
        data = yaml.safe_load(path.read_text())
        return cls.model_validate(data)

    def _folder_to_category(self) -> dict[str, str]:
        return {c.source_folder.lower(): name for name, c in self.categories.items()}

    def categorize_path(self, path_display: str) -> tuple[str, int, str] | None:
        """('/<source_root>/Daily/2026/2026-07-09.note') -> ('daily', 2026, '2026-07-09').

        None if it's the index note, outside source_root, or doesn't fit the
        required <category>/<year>/<file>.note shape.
        """
        prefix = self.source_root.strip("/") + "/"
        normalized = path_display.strip("/")
        if not normalized.startswith(prefix):
            return None
        rest = normalized[len(prefix):]
        parts = [p for p in rest.split("/") if p]

        if len(parts) == 1 and parts[0] == self.index_note:
            return None
        if len(parts) != 3:
            return None

        folder, year_str, filename = parts
        category = self._folder_to_category().get(folder.lower())
        if category is None:
            return None
        try:
            year = int(year_str)
        except ValueError:
            return None

        title = filename[:-5] if filename.endswith(".note") else filename
        return category, year, title

    def parse_entry_date(self, category: str, year: int, title: str) -> date | None:
        """Best-effort date extraction from filename, per the category's date_format."""
        date_format = self.categories[category].date_format
        if date_format == "daily":
            try:
                return date.fromisoformat(title[:10])
            except ValueError:
                return date(year, 1, 1)
        if date_format == "monthly":
            try:
                y, m = title[:7].split("-")
                return date(int(y), int(m), 1)
            except ValueError:
                return date(year, 1, 1)
        return None

    def default_target_path(
        self, category: str, year: int, title: str, entry_date: date | None
    ) -> str:
        cat = self.categories[category]
        if cat.date_format == "daily" and entry_date is not None:
            return f"{cat.vault_folder}/{entry_date.year}/{entry_date.isoformat()}.md"
        if cat.date_format == "monthly" and entry_date is not None:
            return f"{cat.vault_folder}/{entry_date.year}/{entry_date.year:04d}-{entry_date.month:02d}.md"
        return f"{cat.vault_folder}/{year}/{slugify(title)}.md"

    def backlog_path(self, year: int) -> str:
        cat = self.categories[self.backlog_category]
        return f"{cat.vault_folder}/{year}/{self.backlog_file_name}.md"


def sources_dir(note_id: str) -> str:
    return f"Sources/Supernote/{note_id}"


def source_page_path(note_id: str, page_number: int) -> str:
    return f"{sources_dir(note_id)}/page-{page_number:02d}.png"
