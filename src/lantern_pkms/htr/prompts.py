"""Prompt construction for the page-transcription VLM call.

The symbol vocabulary (glyph -> raw_symbol name) is threaded in from the same
config/symbol-mapping.default.yml the deterministic structuring pass uses, so the
prompt's shape vocabulary and the downstream mapping table can never drift apart —
both read from one file.
"""

from __future__ import annotations

from lantern_pkms.structuring.symbol_mapping import SymbolMappingConfig

_BASE_INSTRUCTIONS = """\
You are transcribing one page of a handwritten bullet journal. Your only job is \
faithful text recognition — report exactly what is on the page, in exactly the order \
it appears, top to bottom. Do NOT group, reorder, categorize, or otherwise reorganize \
lines by what kind of mark they start with. A line about a mood written directly under \
an event must stay directly after that event in your output, not be moved elsewhere. \
Do NOT reword, summarize, or "clean up" the handwriting — transcribe it verbatim.

For each distinct line/entry on the page, in reading order, report:

- kind: "entry" for a normal bullet-journal line, or "time_start"/"time_end" if the \
line is a timebox boundary marker (see below). Most lines are "entry".
- indent_level: how many indentation "stops" this line is nested at, starting from 0 \
for a line that starts at the page's left margin. A line indented one step in from its \
parent bullet is indent_level 1, one step further is 2, and so on — this is how the \
page captures that one note belongs under another, so read it carefully off the actual \
horizontal starting position of the line, not the symbol type. This is a small count of \
discrete nesting steps, NOT a pixel or character offset — real bujo pages almost never \
nest more than 3-4 levels deep, so if you find yourself reporting a large number, you \
are measuring the wrong thing; re-read the indentation as "which ancestor bullet is this \
under" instead.
- raw_symbol: which leading mark it starts with (see the list below). Use "other" \
if it doesn't match any of them. Irrelevant filler (e.g. "other") for time_start/time_end.
- symbol_crossed_out: true if the leading mark itself has a line/X drawn through it.
- text_struck_through: true if the line's text has a strikethrough drawn across it.
- text: the transcribed text of the line, without the leading mark. For a \
time_start/time_end line, this is the transcribed time value instead (e.g. "9:00 AM").
- confidence: your own confidence in this transcription, from 0.0 to 1.0. Be honest — \
illegible or ambiguous handwriting should get a low score, not a guess dressed up as \
a confident answer.

Do not interpret what the marks *mean* (e.g. don't decide if a task is "done" or \
"cancelled") — only report what you observe. Meaning is derived separately.

Some pages use a ruled timebox layout down the left margin: a full-page-width rule, \
then a handwritten start time, then a rule the width of just the left column — marking \
the start of a block of notes. The same block is closed later by a mirrored pattern: a \
column-width rule, then a handwritten end time, then a full-page-width rule. When you \
see this layout, emit a "time_start" item at the point the start time appears and a \
"time_end" item at the point the end time appears, interleaved with the "entry" items \
between them, in the same top-to-bottom reading order as everything else. Many pages do \
NOT use this layout at all — if you don't see ruled start/end time boxes, do not invent \
any time_start/time_end items; just report the entries.

Symbols to recognize:
"""


def build_transcription_prompt(config: SymbolMappingConfig) -> str:
    symbol_lines = "\n".join(
        f'- "{name}": glyph {sym.glyph!r}' for name, sym in config.symbols.items()
    )
    return _BASE_INSTRUCTIONS + symbol_lines
