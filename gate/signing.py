"""Ed25519 signing and verification of field reports.

The signature covers the canonical bytes of the report: the nine report fields as JSON with sorted
keys and no whitespace. `sig` itself is never part of what is signed.

KEYS ARE SIMULATED. A real post would hold its private key on a device and the gate would never see
it. Here the simulator (data generator, attack injector) derives each post's private key from
`SEED` and the post id so that builds are reproducible. Private keys are computed in memory when
needed and never written to disk or committed; the gate itself only ever uses the public keys in
the `post_keys` table.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
from collections.abc import Mapping
from functools import lru_cache
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

SIGNED_FIELDS = (
    "report_id",
    "post_id",
    "ts",
    "class",
    "opening",
    "received",
    "consumed",
    "closing",
    "nonce",
)


def canonical_bytes(report: Mapping[str, Any]) -> bytes:
    """Deterministic bytes to sign: signed fields only, sorted keys, compact separators."""
    body = {k: report[k] for k in SIGNED_FIELDS}
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def private_key_from_seed(label: str, seed: int) -> Ed25519PrivateKey:
    """Simulation key for `label` (post id or attacker name); deterministic in (seed, label)."""
    raw = hashlib.sha256(f"rasad-sim-key|{seed}|{label}".encode()).digest()
    return Ed25519PrivateKey.from_private_bytes(raw)


def public_key_b64(private_key: Ed25519PrivateKey) -> str:
    raw = private_key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    return base64.b64encode(raw).decode()


def sign(report: Mapping[str, Any], private_key: Ed25519PrivateKey) -> str:
    return base64.b64encode(private_key.sign(canonical_bytes(report))).decode()


@lru_cache(maxsize=1024)
def _public_key(public_b64: str) -> Ed25519PublicKey:
    return Ed25519PublicKey.from_public_bytes(base64.b64decode(public_b64, validate=True))


def verify(report: Mapping[str, Any], public_b64: str) -> bool:
    """True only for a well-formed signature that matches these exact fields and this key."""
    try:
        signature = base64.b64decode(report["sig"], validate=True)
        _public_key(public_b64).verify(signature, canonical_bytes(report))
    except (InvalidSignature, KeyError, TypeError, ValueError, binascii.Error):
        return False
    return True
