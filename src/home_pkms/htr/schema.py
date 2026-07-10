"""JSON schema for Ollama structured output — one object per detected line on a page.

Deciding what a symbol/mark *means* (task vs. event, complete vs. cancelled) is NOT
the model's job — that's the deterministic pass in structuring/symbol_mapping.py. The
model only reports what it sees: which raw shape, whether it's crossed out, whether
the text is struck through, the transcribed text, and its own confidence.
"""

from __future__ import annotations

RAW_SYMBOLS = ["bullet", "circle", "dash", "equals", "chevron_left", "chevron_right", "other"]

PAGE_LINES_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "lines": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "raw_symbol": {"type": "string", "enum": RAW_SYMBOLS},
                    "symbol_crossed_out": {"type": "boolean"},
                    "text_struck_through": {"type": "boolean"},
                    "text": {"type": "string"},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                },
                "required": ["raw_symbol", "text", "confidence"],
            },
        }
    },
    "required": ["lines"],
}
