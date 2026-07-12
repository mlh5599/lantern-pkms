"""Settings — env vars only, per the plan's "public repo never contains a hostname,
IP, or credential" rule. Non-secret values come from Ansible vars.yml; secrets from
OpenBao. Locally, export LANTERN_PKMS_* env vars or use a .env file (untracked).
"""

from __future__ import annotations

import re
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_RUN_AT_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="LANTERN_PKMS_", env_file=".env", extra="ignore")

    ollama_host: str
    ollama_model: str = "qwen3-vl:8b"

    supernote_cloud_url: str
    supernote_username: str
    supernote_password: str

    vault_path: Path
    state_db_path: Path = Path("/data/state.db")
    symbol_mapping_path: Path = Path("/config/symbol-mapping.yml")
    taxonomy_config_path: Path = Path("/config/taxonomy.yml")

    poll_interval_minutes: int = 1440  # nightly by default — not real-time, CPU-bound
    # Optional fixed daily wall-clock time ("HH:MM", 24h, container-local — set the
    # standard TZ env var to control what "local" means), e.g. "02:00". When set,
    # this takes priority over poll_interval_minutes for the gap *between* runs (the
    # first run on startup always happens immediately either way) — see
    # main.py's seconds_until_next_run_at(). None keeps the plain interval behavior.
    run_at: str | None = None
    metrics_port: int = 9090

    @field_validator("run_at")
    @classmethod
    def _validate_run_at(cls, value: str | None) -> str | None:
        if value is not None and not _RUN_AT_RE.match(value):
            raise ValueError(f"run_at must be 'HH:MM' in 24h time, got {value!r}")
        return value
