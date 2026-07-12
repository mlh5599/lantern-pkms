"""Settings — env vars only, per the plan's "public repo never contains a hostname,
IP, or credential" rule. Non-secret values come from Ansible vars.yml; secrets from
OpenBao. Locally, export LANTERN_PKMS_* env vars or use a .env file (untracked).
"""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="LANTERN_PKMS_", env_file=".env", extra="ignore")

    ollama_host: str
    ollama_model: str = "qwen3-vl:30b-a3b"

    supernote_cloud_url: str
    supernote_username: str
    supernote_password: str

    vault_path: Path
    state_db_path: Path = Path("/data/state.db")
    symbol_mapping_path: Path = Path("/config/symbol-mapping.yml")
    taxonomy_config_path: Path = Path("/config/taxonomy.yml")

    poll_interval_minutes: int = 1440  # nightly by default — not real-time, CPU-bound
    metrics_port: int = 9090
