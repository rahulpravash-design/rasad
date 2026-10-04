from __future__ import annotations

import pytest

from gate import signing

REPORT = {
    "report_id": "r1",
    "post_id": "HANLE-03",
    "ts": "2026-10-10T06:20Z",
    "class": "rations",
    "opening": 420,
    "received": 0,
    "consumed": 31,
    "closing": 389,
    "nonce": "00ff00ff00ff00ff",
}


@pytest.fixture
def key():
    return signing.private_key_from_seed("HANLE-03", 42)


@pytest.fixture
def signed(key):
    return {**REPORT, "sig": signing.sign(REPORT, key)}


def test_valid_signature_verifies(key, signed):
    assert signing.verify(signed, signing.public_key_b64(key))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("report_id", "r2"),
        ("post_id", "HANLE-02"),
        ("ts", "2026-10-10T06:21Z"),
        ("class", "fuel"),
        ("opening", 421),
        ("received", 1),
        ("consumed", 30),
        ("closing", 390),
        ("nonce", "00ff00ff00ff00fe"),
    ],
)
def test_changing_any_signed_field_breaks_the_signature(key, signed, field, value):
    assert not signing.verify({**signed, field: value}, signing.public_key_b64(key))


def test_another_posts_key_does_not_verify(signed):
    other = signing.public_key_b64(signing.private_key_from_seed("HANLE-02", 42))
    assert not signing.verify(signed, other)


def test_malformed_signatures_fail_cleanly_instead_of_raising(key, signed):
    pub = signing.public_key_b64(key)
    for bad in ("", "not base64!!", "AAAA", None, 12):
        assert not signing.verify({**signed, "sig": bad}, pub)
    assert not signing.verify({k: v for k, v in signed.items() if k != "sig"}, pub)


def test_canonical_bytes_ignore_key_order_and_the_signature_itself(signed):
    shuffled = dict(reversed(list(signed.items())))
    assert signing.canonical_bytes(shuffled) == signing.canonical_bytes(REPORT)
    assert b" " not in signing.canonical_bytes(REPORT)


def test_keys_are_deterministic_per_seed_and_post_and_differ_between_them():
    a = signing.public_key_b64(signing.private_key_from_seed("HANLE-03", 42))
    assert a == signing.public_key_b64(signing.private_key_from_seed("HANLE-03", 42))
    assert a != signing.public_key_b64(signing.private_key_from_seed("HANLE-02", 42))
    assert a != signing.public_key_b64(signing.private_key_from_seed("HANLE-03", 7))
