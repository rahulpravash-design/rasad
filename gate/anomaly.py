"""Statistical layer of the gate: an Isolation Forest per supply class over four features.

    per_soldier       consumption per soldier on the day
    z28               z-score of consumption against the post's own previous 28 days
    ratio_formation   consumption per soldier over the median of the formation's other posts
    temp_residual     consumption per soldier relative to what temperature and altitude predict

A report whose forest score exceeds its class threshold is FLAGGED, never rejected: a surge is
statistically unusual and perfectly genuine. Thresholds are set so that a fixed share of clean
training reports (`gate.anomaly_false_alarm_target`) would be flagged.

Scoring one report at a time with scikit-learn costs milliseconds, which is fine for the API and far
too slow for 150k reports, so batch work uses two passes: `featurize` every report in time order,
score the whole matrix per class at once, then run the gate with a `Deferred` scorer that looks the
precomputed answers up.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import joblib
import numpy as np
from sklearn.ensemble import IsolationForest

from gate.rules import FLAG, GateContext, Reason


def model_path(db_path: Path) -> Path:
    """The trained gate model lives next to the database it was trained for."""
    return db_path.with_name(db_path.stem + ".gate.joblib")


FEATURES = ("per_soldier", "z28", "ratio_formation", "temp_residual")
TS_FORMAT = "%Y-%m-%dT%H:%MZ"


@dataclass(frozen=True)
class PostInfo:
    formation: str
    troops: int
    altitude_m: int


@dataclass
class Features:
    vector: np.ndarray
    consumed: int
    window_mean: float | None  # the post's own 28-day mean, None if too little history
    z28: float
    ratio_formation: float
    temp_residual: float


@dataclass
class AnomalyScorer:
    posts: dict[str, PostInfo]
    temps: dict[tuple[str, str], float]  # (post_id, YYYY-MM-DD) -> mean temperature
    temp_model: dict[str, tuple[float, float, float]]  # class -> (intercept, cold slope, alt slope)
    history_days: int = 28
    min_history: int = 7
    temp_ref_c: float = 15.0
    models: dict[str, IsolationForest] = field(default_factory=dict)
    thresholds: dict[str, float] = field(default_factory=dict)
    # Scores of the clean training reports, kept to sweep the false-alarm/detection trade-off.
    train_scores: dict[str, np.ndarray] = field(default_factory=dict)

    # ------------------------------------------------------------------ features

    def _expected_rate(self, cls: str, post: PostInfo, temp_c: float) -> float:
        a, b, c = self.temp_model[cls]
        return (
            a + b * max(0.0, self.temp_ref_c - temp_c) + c * max(0.0, post.altitude_m - 2500) / 1000
        )

    def featurize(self, report: Mapping[str, Any], ctx: GateContext) -> Features | None:
        """Features for one report given what the gate has accepted so far (None: unknown post)."""
        post = self.posts.get(report["post_id"])
        if post is None:
            return None
        cls, consumed = report["class"], report["consumed"]
        rate = consumed / post.troops

        ts = datetime.strptime(report["ts"], TS_FORMAT)
        floor = (ts - timedelta(days=self.history_days)).strftime(TS_FORMAT)
        recent = [
            c for t, c in ctx.history(report["post_id"], cls, self.history_days) if t >= floor
        ]
        mean = std_eff = None
        z = 0.0
        if len(recent) >= self.min_history:
            mean = float(np.mean(recent))
            # A floor on the spread stops tiny integer counts (medical) giving huge z-scores.
            std_eff = max(float(np.std(recent)), 0.05 * mean + 0.5)
            z = (consumed - mean) / std_eff

        peers = ctx.peer_rates(report["post_id"], cls)
        median = float(np.median(peers)) if len(peers) >= 3 else 0.0
        ratio = rate / median if median > 0 else 1.0

        temp = self.temps.get((report["post_id"], report["ts"][:10]))
        expected = self._expected_rate(cls, post, temp) if temp is not None else 0.0
        residual = (rate - expected) / expected if expected > 1e-9 else 0.0

        return Features(np.array([rate, z, ratio, residual]), consumed, mean, z, ratio, residual)

    # ------------------------------------------------------------------ scoring

    def score_batch(self, cls: str, matrix: np.ndarray) -> np.ndarray:
        """Anomaly score per row: higher is more unusual (about 0.4 typical, above 0.6 odd)."""
        return -self.models[cls].score_samples(matrix)

    def reasons(self, cls: str, score: float, f: Features) -> list[Reason]:
        if score <= self.thresholds[cls]:
            return []
        if f.window_mean is not None and f.z28 > 3 and f.window_mean > 0:
            detail = (
                f"consumption {f.consumed} is {f.consumed / f.window_mean:.1f}x this post's "
                f"{self.history_days}-day average ({f.window_mean:.0f}), z={f.z28:+.1f}"
            )
        elif f.ratio_formation > 2:
            detail = f"consumption per soldier is {f.ratio_formation:.1f}x the formation median"
        else:
            detail = "unusual combination of consumption, history, peers and temperature"
        return [Reason("anomalous_consumption", FLAG, f"{detail} (anomaly score {score:.2f})")]

    def score(self, report: Mapping[str, Any], ctx: GateContext) -> tuple[float, list[Reason]]:
        """Scorer protocol for single live reports."""
        f = self.featurize(report, ctx)
        if f is None:
            return 0.0, []
        s = float(self.score_batch(report["class"], f.vector.reshape(1, -1))[0])
        return round(s, 4), self.reasons(report["class"], s, f)

    # ------------------------------------------------------------------ training

    def fit(
        self,
        rows: Iterable[tuple[str, Features]],
        classes: Iterable[str],
        false_alarm_target: float,
        n_trees: int,
        seed: int,
    ) -> dict[str, int]:
        """Fit one forest per class on clean (class, features) rows and set each threshold at the
        quantile that would flag `false_alarm_target` of them. Returns rows used per class."""
        by_class: dict[str, list[np.ndarray]] = {c: [] for c in classes}
        for cls, f in rows:
            by_class[cls].append(f.vector)
        used = {}
        for i, (cls, vectors) in enumerate(sorted(by_class.items())):
            x = np.vstack(vectors)
            model = IsolationForest(
                n_estimators=n_trees, max_samples=256, contamination="auto", random_state=seed + i
            ).fit(x)
            self.models[cls] = model
            scores = -model.score_samples(x)
            self.thresholds[cls] = float(np.quantile(scores, 1.0 - false_alarm_target))
            self.train_scores[cls] = scores.astype(np.float32)
            used[cls] = len(x)
        return used

    def save(self, path: Path) -> None:
        joblib.dump(self, path)

    @staticmethod
    def load(path: Path) -> AnomalyScorer:
        return joblib.load(path)


def fit_temp_model(
    posts: dict[str, PostInfo],
    temps: dict[tuple[str, str], float],
    train_rows: Iterable[Mapping[str, Any]],
    temp_ref_c: float,
) -> dict[str, tuple[float, float, float]]:
    """Per class: consumption per soldier ~ intercept + cold slope + altitude slope, by least
    squares on the training reports. Only used to make a temperature-adjusted residual."""
    cols: dict[str, list[tuple[float, float, float, float]]] = {}
    for r in train_rows:
        post = posts[r["post_id"]]
        t = temps.get((r["post_id"], r["ts"][:10]))
        if t is None:
            continue
        cols.setdefault(r["class"], []).append(
            (
                1.0,
                max(0.0, temp_ref_c - t),
                max(0.0, post.altitude_m - 2500) / 1000,
                r["consumed"] / post.troops,
            )
        )
    out = {}
    for cls, rows in cols.items():
        m = np.array(rows)
        coef, *_ = np.linalg.lstsq(m[:, :3], m[:, 3], rcond=None)
        out[cls] = (float(coef[0]), float(coef[1]), float(coef[2]))
    return out


class Deferred:
    """Scorer that looks up answers computed in batch: report_id -> (score, reasons)."""

    def __init__(self, table: dict[str, tuple[float, list[Reason]]]) -> None:
        self.table = table

    def score(self, report: Mapping[str, Any], ctx: GateContext) -> tuple[float, list[Reason]]:
        return self.table.get(report["report_id"], (0.0, []))


def precompute(
    scorer: AnomalyScorer, items: Iterable[tuple[dict[str, Any], Features | None]]
) -> dict[str, tuple[float, list[Reason]]]:
    """Batch-score (report, features) pairs into a Deferred table."""
    by_class: dict[str, list[tuple[str, Features]]] = {}
    for report, f in items:
        if f is not None:
            by_class.setdefault(report["class"], []).append((report["report_id"], f))
    table: dict[str, tuple[float, list[Reason]]] = {}
    for cls, rows in by_class.items():
        scores = scorer.score_batch(cls, np.vstack([f.vector for _, f in rows]))
        for (rid, f), s in zip(rows, scores, strict=True):
            table[rid] = (round(float(s), 4), scorer.reasons(cls, float(s), f))
    return table
