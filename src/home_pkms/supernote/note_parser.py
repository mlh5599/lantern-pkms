"""Wraps the `supernote` package's .note parser — parser only, no networking.

Base install (`pip install supernote`, pinned exactly in pyproject.toml) is a fork of
the established supernote-tool, forked to drop svg dependencies not found in some
containers. We deliberately don't use its optional [client]/[server] extras — see
the plan's "Supernote access" section and supernote/client.py's module docstring for
why the sync client is hand-rolled instead.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from supernote import parser as sn_parser
from supernote.converter import ImageConverter


@dataclass
class ParsedNotebook:
    total_pages: int
    _converter: Any  # supernote.converter.ImageConverter

    def render_page_png(self, page_number: int) -> bytes:
        """Render one page (0-indexed) to PNG bytes."""
        image = self._converter.convert(page_number)
        buf = io.BytesIO()
        image.save(buf, format="PNG")
        return buf.getvalue()


def parse_note_bytes(data: bytes, policy: str = "strict") -> ParsedNotebook:
    """Parse raw .note file bytes. policy='loose' tolerates unknown signatures."""
    stream = io.BytesIO(data)
    metadata = sn_parser.parse_metadata(stream, policy=policy)
    converter = ImageConverter(metadata)
    return ParsedNotebook(total_pages=metadata.get_total_pages(), _converter=converter)


def parse_note_file(path: Path, policy: str = "strict") -> ParsedNotebook:
    return parse_note_bytes(path.read_bytes(), policy=policy)
