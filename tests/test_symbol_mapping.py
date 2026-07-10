from pathlib import Path

import pytest

from lantern_pkms.structuring.symbol_mapping import SymbolMappingConfig, VLMLine, classify

CONFIG_PATH = Path(__file__).parent.parent / "config" / "symbol-mapping.default.yml"


@pytest.fixture(scope="module")
def config() -> SymbolMappingConfig:
    return SymbolMappingConfig.load(CONFIG_PATH)


def test_default_config_loads(config: SymbolMappingConfig) -> None:
    assert config.version == 1
    assert set(config.symbols) == {
        "bullet",
        "circle",
        "dash",
        "equals",
        "chevron_left",
        "chevron_right",
    }


@pytest.mark.parametrize(
    ("raw_symbol", "expected_entry_type", "expected_state"),
    [
        ("bullet", "task", "open"),
        ("circle", "event", "scheduled"),
        ("dash", "note", None),
        ("equals", "mood", None),
        ("chevron_left", "task", "migrated_backlog"),
        ("chevron_right", "task", "migrated_next_day"),
    ],
)
def test_base_symbol_mapping(
    config: SymbolMappingConfig, raw_symbol: str, expected_entry_type: str, expected_state: str | None
) -> None:
    line = VLMLine(raw_symbol=raw_symbol, text="test", confidence=0.9)
    entry = classify(line, config)
    assert entry.entry_type == expected_entry_type
    assert entry.state == expected_state
    assert not entry.needs_review


def test_crossed_out_bullet_is_complete(config: SymbolMappingConfig) -> None:
    line = VLMLine(raw_symbol="bullet", symbol_crossed_out=True, text="Call dentist", confidence=0.9)
    entry = classify(line, config)
    assert entry.entry_type == "task"
    assert entry.state == "complete"


def test_crossed_out_only_applies_to_tasks(config: SymbolMappingConfig) -> None:
    # symbol_crossed_out.applies_to == "task" in the default config, so a crossed-out
    # circle (event) should NOT become "complete" — crossing out an event doesn't
    # apply per the configured mark, so it should keep its default state.
    line = VLMLine(raw_symbol="circle", symbol_crossed_out=True, text="Dentist", confidence=0.9)
    entry = classify(line, config)
    assert entry.entry_type == "event"
    assert entry.state == "scheduled"


def test_struck_through_text_is_cancelled_for_any_entry_type(config: SymbolMappingConfig) -> None:
    line = VLMLine(raw_symbol="dash", text_struck_through=True, text="Old idea", confidence=0.9)
    entry = classify(line, config)
    assert entry.entry_type == "note"
    assert entry.state == "cancelled"


def test_low_confidence_still_derives_entry_type_and_state(config: SymbolMappingConfig) -> None:
    # See issue #7: a recognized symbol's entry_type/state must survive low
    # confidence — needs_review is an annotation layered on top, not something
    # that discards what's already known (e.g. that this is a task).
    line = VLMLine(raw_symbol="bullet", text="illegible scrawl", confidence=0.3)
    entry = classify(line, config)
    assert entry.needs_review
    assert entry.entry_type == "task"
    assert entry.state == "open"
    assert "confidence" in (entry.review_reason or "")


def test_unrecognized_symbol_routes_to_review(config: SymbolMappingConfig) -> None:
    line = VLMLine(raw_symbol="triangle", text="mystery mark", confidence=0.9)
    entry = classify(line, config)
    assert entry.needs_review
    assert entry.entry_type == "review"
    assert "unrecognized" in (entry.review_reason or "")


def test_unrecognized_symbol_takes_priority_over_low_confidence(config: SymbolMappingConfig) -> None:
    # Both conditions are true. Unrecognized-symbol is checked first now — we
    # genuinely have no entry_type to work with regardless of confidence, so
    # that's the more fundamental problem and the deterministic reported reason.
    line = VLMLine(raw_symbol="triangle", text="???", confidence=0.1)
    entry = classify(line, config)
    assert entry.needs_review
    assert entry.entry_type == "review"
    assert "unrecognized" in (entry.review_reason or "")


def test_low_confidence_crossed_out_bullet_still_marked_complete(config: SymbolMappingConfig) -> None:
    # See issue #7: a crossed-out mark's "complete" state must survive even when
    # the line is also flagged for review due to low confidence.
    line = VLMLine(raw_symbol="bullet", symbol_crossed_out=True, text="Call dentist", confidence=0.4)
    entry = classify(line, config)
    assert entry.needs_review
    assert entry.entry_type == "task"
    assert entry.state == "complete"


def test_line_kind_defaults_to_entry() -> None:
    line = VLMLine(raw_symbol="bullet", text="test", confidence=0.9)
    assert line.kind == "entry"


def test_indent_level_passes_through_classification(config: SymbolMappingConfig) -> None:
    line = VLMLine(raw_symbol="bullet", text="Set up deep dive sessions", confidence=0.9, indent_level=2)
    entry = classify(line, config)
    assert entry.indent_level == 2


def test_indent_level_passes_through_on_review_paths(config: SymbolMappingConfig) -> None:
    low_confidence = VLMLine(raw_symbol="bullet", text="???", confidence=0.1, indent_level=1)
    assert classify(low_confidence, config).indent_level == 1

    unrecognized = VLMLine(raw_symbol="triangle", text="???", confidence=0.9, indent_level=3)
    assert classify(unrecognized, config).indent_level == 3
