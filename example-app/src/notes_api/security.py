"""Password hashing and login tokens, built on the standard library only.

Passwords: scrypt with a random salt per user, parameters stored in the hash so
they can be raised later without breaking existing users.
Tokens: random, URL-safe; only their SHA-256 digest is stored, so a leaked
database does not leak usable tokens.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

# OWASP Password Storage Cheat Sheet minimum for scrypt: N=2^17, r=8, p=1.
SCRYPT_N = 2**17
SCRYPT_R = 8
SCRYPT_P = 1
SALT_BYTES = 16
KEY_BYTES = 32
MAX_MEMORY = 256 * 1024 * 1024  # N=2^17, r=8 needs 128 MiB

TOKEN_BYTES = 32


def _scrypt(password: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    return hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=n, r=r, p=p, maxmem=MAX_MEMORY, dklen=KEY_BYTES
    )


def hash_password(password: str, *, n: int = SCRYPT_N) -> str:
    """Return a self-describing hash: scrypt$N$r$p$salt$key (hex)."""
    salt = secrets.token_bytes(SALT_BYTES)
    key = _scrypt(password, salt, n, SCRYPT_R, SCRYPT_P)
    return f"scrypt${n}${SCRYPT_R}${SCRYPT_P}${salt.hex()}${key.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """Check a password against a stored hash in constant time."""
    try:
        scheme, n, r, p, salt_hex, key_hex = stored.split("$")
        if scheme != "scrypt":
            return False
        key = _scrypt(password, bytes.fromhex(salt_hex), int(n), int(r), int(p))
        expected = bytes.fromhex(key_hex)
    except ValueError:
        return False
    return hmac.compare_digest(key, expected)


def new_token() -> tuple[str, str]:
    """Return (token for the client, digest to store)."""
    token = secrets.token_urlsafe(TOKEN_BYTES)
    return token, token_digest(token)


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
