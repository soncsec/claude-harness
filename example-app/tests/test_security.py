"""Unit tests for password hashing and tokens."""

from __future__ import annotations

from notes_api import security

FAST = 2**10  # low cost for tests only


def test_password_round_trip():
    stored = security.hash_password("a long enough password", n=FAST)
    assert security.verify_password("a long enough password", stored)


def test_wrong_password_fails():
    stored = security.hash_password("a long enough password", n=FAST)
    assert not security.verify_password("a long enough passworD", stored)


def test_same_password_gets_different_hashes():
    first = security.hash_password("same password here", n=FAST)
    second = security.hash_password("same password here", n=FAST)
    assert first != second  # random salt per hash


def test_hash_records_its_parameters():
    stored = security.hash_password("a long enough password", n=FAST)
    scheme, n, r, p, salt, key = stored.split("$")
    assert (scheme, n, r, p) == ("scrypt", str(FAST), "8", "1")
    assert len(bytes.fromhex(salt)) == security.SALT_BYTES
    assert len(bytes.fromhex(key)) == security.KEY_BYTES


def test_default_cost_meets_owasp_minimum():
    assert security.SCRYPT_N >= 2**17
    assert security.SCRYPT_R >= 8


def test_malformed_hash_is_rejected_not_crashing():
    assert not security.verify_password("anything", "not-a-hash")
    assert not security.verify_password("anything", "bcrypt$1$2$3$aa$bb")
    assert not security.verify_password("anything", "scrypt$x$8$1$zz$zz")


def test_tokens_are_random_and_only_digest_is_stored():
    token_a, digest_a = security.new_token()
    token_b, digest_b = security.new_token()
    assert token_a != token_b
    assert digest_a == security.token_digest(token_a)
    assert token_a not in digest_a
    assert len(token_a) >= 43  # 32 random bytes, URL-safe base64
