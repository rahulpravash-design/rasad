"""Runtime settings from environment variables (see .env.example). Read at call time, not import
time, so tests and `make` targets can override them."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from config.loader import ROOT


@dataclass(frozen=True)
class Settings:
    db_path: Path
    seed: int
    offline_mode: bool
    weather_source: str


def _bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


def get_settings() -> Settings:
    db_path = Path(os.environ.get("DB_PATH", "data/cache/rasad.db"))
    if not db_path.is_absolute():
        db_path = ROOT / db_path
    return Settings(
        db_path=db_path,
        seed=int(os.environ.get("SEED", "42")),
        offline_mode=_bool(os.environ.get("OFFLINE_MODE", "true")),
        weather_source=os.environ.get("WEATHER_SOURCE", "auto"),
    )
