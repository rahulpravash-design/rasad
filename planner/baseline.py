"""Fixed-scale stocking baseline: what a post gets without forecasts or closure awareness.

Order up to troops x the class's base scale x cover days (no altitude or cold uplift), by truck
only, on a fixed review cycle; when the pass is closed, nothing moves.
"""

from __future__ import annotations

COVER_DAYS = 30


def target_units(troops: int, base_rate: float, cover_days: int = COVER_DAYS) -> float:
    return troops * base_rate * cover_days
