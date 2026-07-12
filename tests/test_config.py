import pytest
from pydantic import ValidationError

from lantern_pkms.config import Settings

_REQUIRED = dict(
    ollama_host="http://localhost:11434",
    supernote_cloud_url="https://supernote.example.com",
    supernote_username="user",
    supernote_password="pass",
    vault_path="/vault",
)


def test_run_at_defaults_to_none() -> None:
    settings = Settings(**_REQUIRED)
    assert settings.run_at is None


def test_run_at_accepts_valid_hh_mm() -> None:
    settings = Settings(**_REQUIRED, run_at="02:00")
    assert settings.run_at == "02:00"


@pytest.mark.parametrize("bad_value", ["2:00", "25:00", "10:60", "not-a-time", "10:00:00", ""])
def test_run_at_rejects_malformed_values(bad_value: str) -> None:
    with pytest.raises(ValidationError):
        Settings(**_REQUIRED, run_at=bad_value)
