"""Wraps the `supernote` package's .note parser — parser only, no networking.

Base install (`pip install supernote`, pinned exactly in pyproject.toml) is a fork of
the established supernote-tool, forked to drop svg dependencies not found in some
containers. We deliberately don't use its optional [client]/[server] extras — see
the plan's "Supernote access" section and supernote/client.py's module docstring for
why the sync client is hand-rolled instead.

Two real upstream bugs found and worked around here — both against a real note
whose firmware wrote a signature (SN_FILE_VER_20260016) newer than this library has
ever seen, on a device running current firmware as of 2026-07:

1. We were calling the wrong entry point. `parser.parse_metadata()` returns a raw
   SupernoteMetadata with no `get_page()` — it happened to look like it worked
   (SupernoteMetadata also has `get_total_pages()`) right up until the first real
   render call crashed with AttributeError. `parser.load()` is the correct entry
   point — it wraps the metadata in a `Notebook`, which is what both
   `ImageConverter` and our own total_pages access actually need.
2. `SupernoteXParser._check_signature_compatible()` (the method policy="loose" is
   supposed to rely on for exactly this situation — an unrecognized but
   pattern-matching newer signature) hardcodes `fobj.seek(0, ...)` instead of
   `self.SN_SIGNATURE_OFFSET`. That offset is correct for the base non-X parser
   (offset=0) but wrong for this X-series subclass (offset=4, since X-series files
   start with a 4-byte "note" prefix before the signature) — so the compatibility
   regex was being matched against `"note" + partial-signature` garbage and always
   failed, silently defeating policy="loose" for any signature not in the hardcoded
   SN_SIGNATURES list. Patched below with a corrected version that respects the
   subclass's own offset. Verified against a real page: without this patch,
   rendering doesn't error, it just silently produces a blank page (background/grid
   only, no ink) — a startling failure mode if you're not looking closely, since
   nothing raises and the page count is still correct.

Both should be revisited (and probably removed) once/if a `supernote` release
upstream fixes them — check `SupernoteXParser.SN_SIGNATURES` for whether
`SN_FILE_VER_20260016` (or later) has been added natively before assuming this
patch is still needed.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from supernote import parser as sn_parser
from supernote.converter import ImageConverter
from supernote.parser import SupernoteXParser


def _fixed_check_signature_compatible(self: SupernoteXParser, fobj) -> bool:
    """Corrected loose-mode compatibility check — see module docstring, bug 2."""
    latest_signature = self.SN_SIGNATURES[-1]
    try:
        fobj.seek(self.SN_SIGNATURE_OFFSET, io.SEEK_SET)
        signature = fobj.read(len(latest_signature)).decode()
    except Exception:
        return False
    return bool(re.match(self.SN_SIGNATURE_PATTERN, signature))


SupernoteXParser._check_signature_compatible = _fixed_check_signature_compatible


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
    """Parse raw .note file bytes. policy='loose' tolerates unknown signatures
    (only actually effective since the module-level patch above — see docstring).
    """
    stream = io.BytesIO(data)
    notebook = sn_parser.load(stream, policy=policy)
    converter = ImageConverter(notebook)
    return ParsedNotebook(total_pages=notebook.get_total_pages(), _converter=converter)


def parse_note_file(path: Path, policy: str = "strict") -> ParsedNotebook:
    return parse_note_bytes(path.read_bytes(), policy=policy)
