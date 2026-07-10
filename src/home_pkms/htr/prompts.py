"""Prompt construction for the page-transcription VLM call.

The symbol vocabulary (glyph -> raw_symbol name) is threaded in from the same
config/symbol-mapping.default.yml the deterministic structuring pass uses, so the
prompt's shape vocabulary and the downstream mapping table can never drift apart —
both read from one file.
"""

from __future__ import annotations

from home_pkms.structuring.symbol_mapping import SymbolMappingConfig

_BASE_INSTRUCTIONS = """\
You are transcribing one page of a handwritten bullet journal. For each distinct \
line/entry on the page, report:

- raw_symbol: which leading mark it starts with (see the list below). Use "other" \
if it doesn't match any of them.
- symbol_crossed_out: true if the leading mark itself has a line/X drawn through it.
- text_struck_through: true if the line's text has a strikethrough drawn across it.
- text: the transcribed text of the line, without the leading mark.
- confidence: your own confidence in this transcription, from 0.0 to 1.0. Be honest — \
illegible or ambiguous handwriting should get a low score, not a guess dressed up as \
a confident answer.

Do not interpret what the marks *mean* (e.g. don't decide if a task is "done" or \
"cancelled") — only report what you observe. Meaning is derived separately.

Symbols to recognize:
"""


def build_transcription_prompt(config: SymbolMappingConfig) -> str:
    symbol_lines = "\n".join(
        f'- "{name}": glyph {sym.glyph!r}' for name, sym in config.symbols.items()
    )
    return _BASE_INSTRUCTIONS + symbol_lines
