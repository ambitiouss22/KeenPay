"""Password and API key hashing utilities."""

import hashlib
import secrets

import bcrypt

# bcrypt only ever reads the first 72 bytes of a secret. Truncate explicitly so
# behaviour is identical on every bcrypt release instead of raising on long
# input. (This used to go through passlib, but passlib 1.7.4 is unmaintained
# and its bcrypt backend breaks on bcrypt >= 4.1 -- exactly what CI installs.)
_BCRYPT_MAX_BYTES = 72


def _prepare(plain: str) -> bytes:
    return plain.encode("utf-8")[:_BCRYPT_MAX_BYTES]


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(_prepare(plain), bcrypt.gensalt()).decode("ascii")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(_prepare(plain), hashed.encode("ascii"))
    except (ValueError, TypeError):
        # Stored value is not a usable bcrypt hash -- treat as a failed login
        # rather than a 500.
        return False


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def generate_api_key() -> tuple[str, str, str]:
    """Return (full_key, prefix, hash)."""
    raw = f"kp_{secrets.token_urlsafe(32)}"
    prefix = raw[:12]
    return raw, prefix, hash_token(raw)
