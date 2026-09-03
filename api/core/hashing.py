"""Password and API key hashing utilities.

Passwords are hashed with Argon2id. It is the Password Hashing Competition
winner and the current OWASP first choice: unlike bcrypt it is deliberately
memory-hard, which removes the attacker's advantage on GPUs and ASICs, where
raw hash throughput is cheap but memory bandwidth is not.

Existing bcrypt hashes still verify. Replacing a hash requires the plaintext,
which only exists during a login, so the upgrade happens there: verify against
whatever scheme produced the stored hash, and when it is the old one, re-hash
and let the caller persist it. Users migrate as they sign in, and nobody is
locked out. :func:`needs_rehash` is what the login path asks.

passlib is deliberately absent. Version 1.7.4 (2020, unmaintained) reads
``bcrypt.__about__``, removed in bcrypt 4.1, so it raises on any current
bcrypt. These functions call the backends directly.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

import bcrypt
from argon2 import PasswordHasher
from argon2 import exceptions as argon2_exceptions
from argon2.profiles import RFC_9106_LOW_MEMORY

# RFC 9106's "second recommended" profile: 64 MiB, 3 passes. The high-memory
# profile (2 GiB) is meant for offline key derivation; on an API host, where a
# login burst means many concurrent hashes, it is self-inflicted memory
# exhaustion. 64 MiB per hash is the sane server-side point on that curve.
_hasher = PasswordHasher.from_parameters(RFC_9106_LOW_MEMORY)

# bcrypt truncates silently at 72 bytes: everything past it is ignored, so two
# different long passwords can share a hash. Argon2 has no such limit, but the
# cap stays for verifying legacy hashes, and an explicit ceiling also stops a
# megabyte-long password from becoming a CPU denial of service.
_BCRYPT_MAX_BYTES = 72
_MAX_PASSWORD_BYTES = 1024


class PasswordTooLongError(ValueError):
    """The supplied password exceeds the accepted length."""


def _encode(plain: str) -> bytes:
    raw = plain.encode("utf-8")
    if len(raw) > _MAX_PASSWORD_BYTES:
        raise PasswordTooLongError(f"password exceeds {_MAX_PASSWORD_BYTES} bytes")
    return raw


def _looks_like_bcrypt(hashed: str) -> bool:
    return hashed.startswith(("$2a$", "$2b$", "$2x$", "$2y$"))


def _looks_like_argon2(hashed: str) -> bool:
    return hashed.startswith("$argon2")


def hash_password(plain: str) -> str:
    """Hash a password with Argon2id."""
    return _hasher.hash(_encode(plain))


def verify_password(plain: str, hashed: str) -> bool:
    """Check a password against an Argon2id or legacy bcrypt hash.

    Returns ``False`` for a malformed or unrecognised hash rather than raising,
    so a corrupt row is a failed login instead of a 500 that reveals which
    accounts have bad data.
    """
    if not hashed:
        return False

    try:
        raw = _encode(plain)
    except PasswordTooLongError:
        return False

    if _looks_like_argon2(hashed):
        try:
            return _hasher.verify(hashed, raw)
        except (
            argon2_exceptions.VerifyMismatchError,
            argon2_exceptions.VerificationError,
            argon2_exceptions.InvalidHashError,
        ):
            return False

    if _looks_like_bcrypt(hashed):
        try:
            return bcrypt.checkpw(raw[:_BCRYPT_MAX_BYTES], hashed.encode("ascii"))
        except (ValueError, TypeError):
            return False

    return False


def needs_rehash(hashed: str) -> bool:
    """Whether a stored hash should be replaced after a successful login.

    True for any bcrypt hash, and for an Argon2 hash produced with weaker
    parameters than the current profile — so raising the cost later migrates
    users automatically rather than needing a backfill.
    """
    if not hashed:
        return True
    if _looks_like_bcrypt(hashed):
        return True
    if _looks_like_argon2(hashed):
        try:
            return _hasher.check_needs_rehash(hashed)
        except argon2_exceptions.InvalidHashError:
            return True
    return True


def hash_token(raw: str) -> str:
    """Hash an opaque token — refresh tokens, API keys.

    Plain SHA-256, not a password hash, and that is deliberate. These tokens
    are 256+ bits of ``secrets`` output, so there is no dictionary to attack
    and no work factor worth paying on every request. What matters is that a
    database leak does not yield usable credentials, and a one-way hash gives
    that.
    """
    return hashlib.sha256(raw.encode()).hexdigest()


def verify_token(raw: str, expected_hash: str) -> bool:
    """Constant-time comparison of a token against its stored hash.

    ``==`` on hex digests leaks the position of the first difference through
    timing. The leak is small over a network but free to remove.
    """
    return hmac.compare_digest(hash_token(raw), expected_hash or "")


def generate_api_key() -> tuple[str, str, str]:
    """Return (full_key, prefix, hash).

    The prefix is stored in the clear so a lookup can find the row by it; the
    full key is only ever stored hashed.
    """
    raw = f"kp_{secrets.token_urlsafe(32)}"
    prefix = raw[:12]
    return raw, prefix, hash_token(raw)


__all__ = [
    "PasswordTooLongError",
    "generate_api_key",
    "hash_password",
    "hash_token",
    "needs_rehash",
    "verify_password",
    "verify_token",
]
