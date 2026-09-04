"""The live Razorpay client's contract, without touching Razorpay."""

from unittest.mock import patch

import pytest

from services.razorpay import RazorpayProvider, RazorpayService


def test_the_live_client_refuses_to_start_without_keys():
    """Better a startup error than silently talking to nothing."""
    with patch("services.razorpay.get_settings") as settings:
        settings.return_value.razorpay_key_id = ""
        settings.return_value.razorpay_key_secret = ""
        with pytest.raises(ValueError):
            RazorpayProvider()


def test_the_live_client_starts_with_keys():
    with patch("services.razorpay.get_settings") as settings:
        settings.return_value.razorpay_key_id = "rzp_test_key"
        settings.return_value.razorpay_key_secret = "secret"
        assert RazorpayProvider() is not None


def test_the_mock_is_chosen_when_no_keys_are_configured():
    from modules.payments.provider import get_provider
    from services.razorpay_mock import RazorpayMockProvider

    assert isinstance(get_provider(), RazorpayMockProvider)


def test_a_webhook_signature_verifies():
    import hashlib
    import hmac

    secret = "whsec_test"
    body = b'{"event":"payment.captured"}'
    signature = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()

    assert RazorpayService(webhook_secret=secret).verify_webhook_signature(body, signature) is True


def test_a_tampered_body_does_not_verify():
    import hashlib
    import hmac

    secret = "whsec_test"
    signature = hmac.new(secret.encode(), b"original", hashlib.sha256).hexdigest()

    service = RazorpayService(webhook_secret=secret)
    assert service.verify_webhook_signature(b"tampered", signature) is False


@pytest.mark.parametrize("signature", [None, "", "deadbeef"])
def test_a_missing_or_wrong_signature_does_not_verify(signature):
    service = RazorpayService(webhook_secret="whsec_test")
    assert service.verify_webhook_signature(b"body", signature) is False


def test_an_unset_secret_refuses_everything():
    """Fail closed: no secret must not mean no checking."""
    service = RazorpayService(webhook_secret="")
    assert service.verify_webhook_signature(b"body", "anything") is False
