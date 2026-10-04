from __future__ import annotations

from pathlib import Path

import pytest

from data.build import build


@pytest.fixture(scope="session")
def built(tmp_path_factory: pytest.TempPathFactory) -> dict:
    """One full synthetic build shared by every test that needs a database (about 10 s)."""
    root = tmp_path_factory.mktemp("rasad")
    db = root / "rasad.db"
    summary = build(db, seed=42, source="synthetic", cache_dir=root / "cache")
    return {"db": db, "summary": summary, "root": root}


@pytest.fixture
def db_path(built: dict) -> Path:
    return built["db"]
