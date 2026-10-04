"""The gate instance the API uses: the rules plus the trained anomaly scorer, when one exists."""

from __future__ import annotations

from api.settings import get_settings
from config.loader import load_consumption
from gate.anomaly import AnomalyScorer, model_path
from gate.pipeline import Gate

_cached: tuple[tuple[str, float], Gate] | None = None


def get_gate() -> Gate:
    """Reloaded whenever the model file changes (a rebuild) or DB_PATH points somewhere else. With
    no model file the gate still runs, on rules alone."""
    global _cached
    path = model_path(get_settings().db_path)
    key = (str(path), path.stat().st_mtime if path.exists() else 0.0)
    if _cached is None or _cached[0] != key:
        scorer = AnomalyScorer.load(path) if path.exists() else None
        _cached = (key, Gate(set(load_consumption()["classes"]), scorer=scorer))
    return _cached[1]
