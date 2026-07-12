"""JSON schema for Ollama structured output — one object per detected line on a page.

Deciding what a symbol/mark *means* (task vs. event, complete vs. cancelled) is NOT
the model's job — that's the deterministic pass in structuring/symbol_mapping.py. The
model only reports what it sees: which raw shape, whether it's crossed out, whether
the text is struck through, the transcribed text, and its own confidence.
"""

from __future__ import annotations

RAW_SYMBOLS = ["bullet", "circle", "dash", "equals", "chevron_left", "chevron_right", "other"]

# "entry" is a normal bujo line (the symbol/text/indent fields apply). "time_start" /
# "time_end" mark a timebox boundary (a ruled start/end-time box some page layouts
# use down the left margin) — for those, `text` holds the transcribed time value and
# the symbol/indent fields are irrelevant filler the model should leave at defaults.
LINE_KINDS = ["entry", "time_start", "time_end"]

# A real bujo page rarely nests more than 3-4 levels deep. This is a generation-time
# guardrail (constrains the model's structured output directly), not just a display
# limit — see MAX_INDENT_LEVEL in structuring/symbol_mapping.py for the matching
# defensive clamp applied to whatever the model actually returns, since a schema bound
# alone isn't a hard guarantee (issue #20: qwen3-vl:30b-a3b once returned indent_level
# 18/27/36/45/54 on a real page, exploding the rendered indentation).
MAX_INDENT_LEVEL = 6

PAGE_LINES_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "lines": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "kind": {"type": "string", "enum": LINE_KINDS},
                    "indent_level": {"type": "integer", "minimum": 0, "maximum": MAX_INDENT_LEVEL},
                    "raw_symbol": {"type": "string", "enum": RAW_SYMBOLS},
                    "symbol_crossed_out": {"type": "boolean"},
                    "text_struck_through": {"type": "boolean"},
                    "text": {"type": "string"},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                },
                "required": ["kind", "indent_level", "raw_symbol", "text", "confidence"],
            },
        }
    },
    "required": ["lines"],
}
