"""Deterministic mapping from raw VLM-detected marks to bullet-journal semantics.

This is intentionally not an LLM step: once the vision model has extracted what
symbol/marks are on the page, deciding what they *mean* is a fixed lookup table,
externalized in config/symbol-mapping.default.yml so Mike can tune it without a
rebuild. See the "HTR + structuring pipeline" section of the lantern-pkms v1 plan.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel

from lantern_pkms.htr.schema import MAX_INDENT_LEVEL


class SymbolDef(BaseModel):
    glyph: str
    entry_type: str
    default_state: str | None = None


class MarkDef(BaseModel):
    state: str
    applies_to: str | None = None


class SymbolMappingConfig(BaseModel):
    version: int
    confidence_threshold: float
    symbols: dict[str, SymbolDef]
    marks: dict[str, MarkDef]

    @classmethod
    def load(cls, path: Path) -> "SymbolMappingConfig":
        data = yaml.safe_load(path.read_text())
        return cls.model_validate(data)


class VLMLine(BaseModel):
    """One line as reported by the VLM's structured-output pass.

    `kind` distinguishes a normal bujo line ("entry") from a timebox boundary
    marker ("time_start"/"time_end") — see htr/prompts.py. `indent_level` is the
    line's visual nesting depth, used downstream to preserve which entries belong
    under which parent instead of flattening everything into category buckets
    (see issue #2).
    """

    kind: Literal["entry", "time_start", "time_end"] = "entry"
    indent_level: int = 0
    raw_symbol: str
    symbol_crossed_out: bool = False
    text_struck_through: bool = False
    text: str
    confidence: float


class ClassifiedEntry(BaseModel):
    """A VLMLine after the deterministic symbol-mapping pass."""

    entry_type: str
    state: str | None
    text: str
    symbol_raw: str
    confidence: float
    needs_review: bool
    indent_level: int = 0
    review_reason: str | None = None


def classify(line: VLMLine, config: SymbolMappingConfig) -> ClassifiedEntry:
    """Map one transcribed line to its bullet-journal semantics.

    An unrecognized symbol routes to needs_review with no entry_type — there's
    nothing to classify it as. Low confidence is different: entry_type/state are
    still derived from the recognized symbol (including crossed-out/struck-through
    marks) regardless of confidence, and needs_review is layered on top as an
    annotation rather than discarding what's already known — see issue #7, where
    gating classification on confidence was silently losing a correctly-identified
    task's checkbox state (and a crossed-out mark's "complete" state) whenever
    confidence merely dipped.
    """
    indent_level = max(0, min(line.indent_level, MAX_INDENT_LEVEL))

    symbol = config.symbols.get(line.raw_symbol)
    if symbol is None:
        return ClassifiedEntry(
            entry_type="review",
            state=None,
            text=line.text,
            symbol_raw=line.raw_symbol,
            confidence=line.confidence,
            needs_review=True,
            indent_level=indent_level,
            review_reason=f"unrecognized symbol {line.raw_symbol!r}",
        )

    state = symbol.default_state

    if line.symbol_crossed_out:
        mark = config.marks.get("symbol_crossed_out")
        if mark and (mark.applies_to is None or mark.applies_to == symbol.entry_type):
            state = mark.state

    if line.text_struck_through:
        mark = config.marks.get("text_struck_through")
        if mark and (mark.applies_to is None or mark.applies_to == symbol.entry_type):
            state = mark.state

    low_confidence = line.confidence < config.confidence_threshold
    return ClassifiedEntry(
        entry_type=symbol.entry_type,
        state=state,
        text=line.text,
        symbol_raw=line.raw_symbol,
        confidence=line.confidence,
        needs_review=low_confidence,
        indent_level=indent_level,
        review_reason=(
            f"confidence {line.confidence:.2f} below threshold {config.confidence_threshold:.2f}"
            if low_confidence
            else None
        ),
    )
