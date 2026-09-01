"""Unit tests for password hashing."""

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
