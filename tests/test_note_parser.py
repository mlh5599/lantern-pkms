import io
import re

from PIL import Image

from lantern_pkms.supernote import note_parser
from supernote.parser import SupernoteXParser


class _FakeNotebook:
    def get_total_pages(self) -> int:
        return 3


class _FakeConverter:
    def __init__(self, notebook) -> None:
        self.notebook = notebook

    def convert(self, page_number: int, visibility_overlay=None) -> Image.Image:
        return Image.new("RGB", (10, 10), color=(page_number, 0, 0))


def test_parse_note_bytes_wires_up_parser_and_converter(monkeypatch) -> None:
    # Regression test: must call sn_parser.load(), not the lower-level
    # parse_metadata() — the latter returns an object with no get_page(), which
    # only breaks at render time, not at parse time. See module docstring bug 1.
    captured = {}

    def fake_load(stream, policy):
        captured["policy"] = policy
        captured["data"] = stream.read()
        return _FakeNotebook()

    monkeypatch.setattr(note_parser.sn_parser, "load", fake_load)
    monkeypatch.setattr(note_parser, "ImageConverter", _FakeConverter)

    notebook = note_parser.parse_note_bytes(b"fake-note-bytes", policy="loose")

    assert captured["policy"] == "loose"
    assert captured["data"] == b"fake-note-bytes"
    assert notebook.total_pages == 3


def test_render_page_png_returns_valid_png(monkeypatch) -> None:
    monkeypatch.setattr(note_parser.sn_parser, "load", lambda stream, policy: _FakeNotebook())
    monkeypatch.setattr(note_parser, "ImageConverter", _FakeConverter)

    notebook = note_parser.parse_note_bytes(b"fake-note-bytes")
    png_bytes = notebook.render_page_png(1)

    image = Image.open(io.BytesIO(png_bytes))
    assert image.format == "PNG"
    assert image.size == (10, 10)


def test_signature_compatibility_patch_respects_subclass_offset() -> None:
    # Regression test for bug 2 (see module docstring): the upstream method
    # hardcodes offset 0, which only happens to be correct for the base non-X
    # parser. This asserts our patched version reads from the X-parser's own
    # SN_SIGNATURE_OFFSET (4, for the "note" prefix) instead, so a real newer
    # firmware signature is correctly recognized as pattern-compatible.
    parser = SupernoteXParser()
    assert parser.SN_SIGNATURE_OFFSET == 4

    # "note" (4-byte prefix) + a signature that matches the pattern but isn't in
    # the hardcoded SN_SIGNATURES list.
    stream = io.BytesIO(b"noteSN_FILE_VER_20260016.\x01\x00\x00extra-page-data")
    assert parser._check_signature_compatible(stream) is True

    # A signature that doesn't match the expected pattern at all should still
    # correctly report incompatible.
    stream_bad = io.BytesIO(b"notesomething-unrelated-entirely")
    assert parser._check_signature_compatible(stream_bad) is False


def test_signature_pattern_matches_real_captured_signature() -> None:
    assert re.match(SupernoteXParser.SN_SIGNATURE_PATTERN, "SN_FILE_VER_20260016")
