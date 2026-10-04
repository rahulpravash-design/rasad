"""Tamper injector: four ways a report can be false.

    forged_signature      a consistent report signed with someone else's key
    replay                an earlier genuine report sent again, verbatim
    inflated_consumption  an insider with a valid key overstates what was consumed
    deflated_stock        an insider with a valid key understates the stock on hand

Each attack starts from the genuine report for the same post, class and day, and (except replay)
keeps the arithmetic consistent, so the cheap checks cannot catch it by accident: forged is caught
only by the signature, replay only by replay and timestamp checks, and the two insider attacks are
validly signed and balanced, which is what makes them hard.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from typing import Any

import numpy as np
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from data.stock import ID_NAMESPACE
from gate import signing

FORGED, REPLAY, INFLATED, DEFLATED = (
    "forged_signature",
    "replay",
    "inflated_consumption",
    "deflated_stock",
)
ATTACK_TYPES = (FORGED, REPLAY, INFLATED, DEFLATED)
ATTACKER = "attacker"


def _fresh(genuine: Mapping[str, Any], attack: str, rng: np.random.Generator) -> dict[str, Any]:
    """Copy of the genuine report with a new id and nonce, so it is not a replay of the original."""
    salt = int(rng.integers(0, 2**62))
    report = {k: genuine[k] for k in signing.SIGNED_FIELDS}
    report["report_id"] = str(
        uuid.uuid5(ID_NAMESPACE, f"attack|{attack}|{genuine['report_id']}|{salt}")
    )
    report["nonce"] = f"{int(rng.integers(0, 2**62)):016x}"
    return report


def make_attack(
    attack: str,
    genuine: Mapping[str, Any],
    rng: np.random.Generator,
    post_key: Ed25519PrivateKey,
    seed: int,
    earlier: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any] | None:
    """Build one tampered report, or None when the attack cannot be built from this report (replay
    with no earlier report, deflating a post that holds no stock)."""
    if attack == REPLAY:
        if not earlier:
            return None
        return dict(earlier[int(rng.integers(0, len(earlier)))])

    report = _fresh(genuine, attack, rng)
    if attack == FORGED:
        signer = signing.private_key_from_seed(ATTACKER, seed)
    elif attack == INFLATED:
        available = report["opening"] + report["received"]
        factor = float(rng.uniform(1.8, 3.0))
        report["consumed"] = min(
            available, max(report["consumed"] + 1, round(report["consumed"] * factor))
        )
        report["closing"] = available - report["consumed"]
        signer = post_key
    elif attack == DEFLATED:
        if report["opening"] < 1:
            return None
        cut = max(1, round(report["opening"] * float(rng.uniform(0.25, 0.5))))
        cut = min(cut, report["opening"], report["closing"]) if report["closing"] else 0
        if cut < 1:
            return None
        report["opening"] -= cut
        report["closing"] -= cut
        signer = post_key
    else:
        raise ValueError(f"unknown attack type {attack!r}; expected one of {ATTACK_TYPES}")

    report["sig"] = signing.sign(report, signer)
    return report
