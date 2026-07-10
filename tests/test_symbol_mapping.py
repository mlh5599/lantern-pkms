from pathlib import Path

import pytest

from home_pkms.structuring.symbol_mapping import SymbolMappingConfig, VLMLine, classify

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


def test_low_confidence_routes_to_review(config: SymbolMappingConfig) -> None:
    line = VLMLine(raw_symbol="bullet", text="illegible scrawl", confidence=0.3)
    entry = classify(line, config)
    assert entry.needs_review
    assert entry.entry_type == "review"
    assert "confidence" in (entry.review_reason or "")


def test_unrecognized_symbol_routes_to_review(config: SymbolMappingConfig) -> None:
    line = VLMLine(raw_symbol="triangle", text="mystery mark", confidence=0.9)
    entry = classify(line, config)
    assert entry.needs_review
    assert "unrecognized" in (entry.review_reason or "")


def test_confidence_check_takes_priority_over_unknown_symbol(config: SymbolMappingConfig) -> None:
    # Both conditions are true; confidence should be checked first so the reported
    # reason is deterministic.
    line = VLMLine(raw_symbol="triangle", text="???", confidence=0.1)
    entry = classify(line, config)
    assert entry.needs_review
    assert "confidence" in (entry.review_reason or "")
