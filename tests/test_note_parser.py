import io

from PIL import Image

from home_pkms.supernote import note_parser


class _FakeMetadata:
    def get_total_pages(self) -> int:
        return 3


class _FakeConverter:
    def __init__(self, metadata) -> None:
        self.metadata = metadata

    def convert(self, page_number: int, visibility_overlay=None) -> Image.Image:
        return Image.new("RGB", (10, 10), color=(page_number, 0, 0))


def test_parse_note_bytes_wires_up_parser_and_converter(monkeypatch) -> None:
    captured = {}

    def fake_parse_metadata(stream, policy):
        captured["policy"] = policy
        captured["data"] = stream.read()
        return _FakeMetadata()

    monkeypatch.setattr(note_parser.sn_parser, "parse_metadata", fake_parse_metadata)
    monkeypatch.setattr(note_parser, "ImageConverter", _FakeConverter)

    notebook = note_parser.parse_note_bytes(b"fake-note-bytes", policy="loose")

    assert captured["policy"] == "loose"
    assert captured["data"] == b"fake-note-bytes"
    assert notebook.total_pages == 3


def test_render_page_png_returns_valid_png(monkeypatch) -> None:
    monkeypatch.setattr(note_parser.sn_parser, "parse_metadata", lambda stream, policy: _FakeMetadata())
    monkeypatch.setattr(note_parser, "ImageConverter", _FakeConverter)

    notebook = note_parser.parse_note_bytes(b"fake-note-bytes")
    png_bytes = notebook.render_page_png(1)

    image = Image.open(io.BytesIO(png_bytes))
    assert image.format == "PNG"
    assert image.size == (10, 10)
