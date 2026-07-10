"""Deterministic mapping from raw VLM-detected marks to bullet-journal semantics.

This is intentionally not an LLM step: once the vision model has extracted what
symbol/marks are on the page, deciding what they *mean* is a fixed lookup table,
externalized in config/symbol-mapping.default.yml so Mike can tune it without a
rebuild. See the "HTR + structuring pipeline" section of the lantern-pkms v1 plan.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel


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
    """One line as reported by the VLM's structured-output pass."""

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
    review_reason: str | None = None


def classify(line: VLMLine, config: SymbolMappingConfig) -> ClassifiedEntry:
    """Map one transcribed line to its bullet-journal semantics.

    Low confidence and unrecognized symbols both route to needs_review rather than
    being guessed into a normal category — see the plan's "never silently merge or
    guess" rule for low-confidence output.
    """
    if line.confidence < config.confidence_threshold:
        return ClassifiedEntry(
            entry_type="review",
            state=None,
            text=line.text,
            symbol_raw=line.raw_symbol,
            confidence=line.confidence,
            needs_review=True,
            review_reason=f"confidence {line.confidence:.2f} below threshold "
            f"{config.confidence_threshold:.2f}",
        )

    symbol = config.symbols.get(line.raw_symbol)
    if symbol is None:
        return ClassifiedEntry(
            entry_type="review",
            state=None,
            text=line.text,
            symbol_raw=line.raw_symbol,
            confidence=line.confidence,
            needs_review=True,
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

    return ClassifiedEntry(
        entry_type=symbol.entry_type,
        state=state,
        text=line.text,
        symbol_raw=line.raw_symbol,
        confidence=line.confidence,
        needs_review=False,
    )
