"""The verified data gate: one report in, one verdict out.

    VERIFIED  every check passed
    FLAGGED   might be true, needs a person (stock discontinuity, unmatched receipt, anomaly)
    REJECTED  cannot be true (bad signature, broken arithmetic, replay, impossible timestamp)

Gate.evaluate() reads the context and changes nothing, so the same report can be judged without
side effects (the attack evaluation relies on that). Callers commit the result themselves.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

from gate import rules
from gate.rules import FLAG, REJECT, GateContext, Reason

VERIFIED, FLAGGED, REJECTED = "VERIFIED", "FLAGGED", "REJECTED"


@dataclass(frozen=True)
class Verdict:
    verdict: str
    reasons: list[Reason] = field(default_factory=list)
    score: float | None = None  # anomaly score when a scorer ran (higher = more unusual)

    @property
    def messages(self) -> list[str]:
        return [r.message for r in self.reasons]

    @property
    def codes(self) -> list[str]:
        return [r.code for r in self.reasons]


class Scorer(Protocol):
    """Soft statistical check; see gate/anomaly.py."""

    def score(self, report: Mapping[str, Any], ctx: GateContext) -> tuple[float, list[Reason]]: ...


class Gate:
    def __init__(self, classes: set[str], scorer: Scorer | None = None) -> None:
        self.classes = classes
        self.scorer = scorer

    def evaluate(self, report: Mapping[str, Any], ctx: GateContext) -> Verdict:
        reasons = rules.check_schema(report, self.classes)
        if reasons:  # nothing else is safe to read from a malformed report
            return Verdict(REJECTED, reasons)

        previous = ctx.previous(report["post_id"], report["class"])
        reasons += rules.check_signature(report, ctx.public_key(report["post_id"]))
        reasons += rules.check_balance(report)
        reasons += rules.check_timestamp(report, previous, ctx.now)
        reasons += rules.check_replay(report, ctx)
        if any(r.severity == REJECT for r in reasons):
            return Verdict(REJECTED, reasons)

        reasons += rules.check_continuity(report, previous)
        reasons += rules.check_receipt(report, ctx)
        score = None
        if self.scorer is not None:
            score, anomaly_reasons = self.scorer.score(report, ctx)
            reasons += anomaly_reasons
        if any(r.severity == FLAG for r in reasons):
            return Verdict(FLAGGED, reasons, score)
        return Verdict(VERIFIED, reasons, score)
