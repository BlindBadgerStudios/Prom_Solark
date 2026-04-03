from __future__ import annotations

import os
from dataclasses import dataclass



def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    return int(value) if value not in (None, "") else default



def _env_optional_int(name: str) -> int | None:
    value = os.getenv(name)
    return int(value) if value not in (None, "") else None


@dataclass(slots=True)
class AppConfig:
    username: str
    password: str
    plant_id: int | None = None
    poll_interval_seconds: int = 60
    listen_port: int = 10112
    log_level: str = "INFO"
    timeout_seconds: int = 30
    plant_stale_after_seconds: int = 900



def load_config() -> AppConfig:
    username = os.getenv("SOLARK_EMAIL") or os.getenv("SOLARK_USERNAME")
    password = os.getenv("SOLARK_PASSWORD")
    if not username or not password:
        raise RuntimeError("SOLARK_EMAIL (or SOLARK_USERNAME) and SOLARK_PASSWORD must be set")
    return AppConfig(
        username=username,
        password=password,
        plant_id=_env_optional_int("SOLARK_PLANT_ID"),
        poll_interval_seconds=_env_int("POLL_INTERVAL_SECONDS", 60),
        listen_port=_env_int("LISTEN_PORT", 10112),
        log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
        timeout_seconds=_env_int("SOLARK_TIMEOUT_SECONDS", 30),
        plant_stale_after_seconds=_env_int("PLANT_STALE_AFTER_SECONDS", 900),
    )
