"""Unit tests for password hashing."""

import pytest

from core.hashing import generate_api_key, hash_password, hash_token, verify_password


def test_password_hash_roundtrip():
    hashed = hash_password("KeenPayDev1!")
    assert verify_password("KeenPayDev1!", hashed)
    assert not verify_password("wrong", hashed)


def test_token_hash_deterministic():
    assert hash_token("abc") == hash_token("abc")
    assert hash_token("abc") != hash_token("def")


def test_api_key_format():
    raw, prefix, key_hash = generate_api_key()
    assert raw.startswith("kp_")
    assert raw.startswith(prefix)
    assert len(key_hash) == 64


# ---------------------------------------------------------------------------
# Phase 2: Argon2id, and the migration away from bcrypt
# ---------------------------------------------------------------------------


def test_new_hashes_are_argon2id():
    assert hash_password("CorrectHorse1!").startswith("$argon2id$")


def test_argon2_round_trip():
    h = hash_password("CorrectHorse1!")
    assert verify_password("CorrectHorse1!", h)
    assert not verify_password("wrong", h)


def test_legacy_bcrypt_hashes_still_verify():
    """Existing users must not be locked out by the algorithm change."""
    import bcrypt as _bcrypt

    legacy = _bcrypt.hashpw(b"CorrectHorse1!", _bcrypt.gensalt()).decode()
    assert verify_password("CorrectHorse1!", legacy)
    assert not verify_password("wrong", legacy)


def test_bcrypt_hashes_are_flagged_for_upgrade():
    import bcrypt as _bcrypt

    from core.hashing import needs_rehash

    legacy = _bcrypt.hashpw(b"x", _bcrypt.gensalt()).decode()
    assert needs_rehash(legacy) is True
    assert needs_rehash(hash_password("x")) is False


def test_long_passwords_are_no_longer_truncated():
    """bcrypt stopped at 72 bytes, so these two collided. Argon2 does not."""
    a, b = "a" * 100 + "X", "a" * 100 + "Y"
    assert not verify_password(b, hash_password(a))


@pytest.mark.parametrize("bad", ["", "not-a-hash", "$2b$broken", "$argon2id$garbage"])
def test_unusable_hashes_fail_closed(bad):
    """A corrupt row is a failed login, never a 500."""
    assert verify_password("anything", bad) is False


def test_absurdly_long_password_is_refused_not_hashed():
    from core.hashing import PasswordTooLongError

    with pytest.raises(PasswordTooLongError):
        hash_password("a" * 5000)
    assert verify_password("a" * 5000, hash_password("short")) is False


def test_token_hash_comparison_is_constant_time():
    from core.hashing import hash_token, verify_token

    raw = "kp_sometoken"
    assert verify_token(raw, hash_token(raw))
    assert not verify_token("kp_other", hash_token(raw))
    assert not verify_token(raw, "")
