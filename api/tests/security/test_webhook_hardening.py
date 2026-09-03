"""The webhook is a public endpoint, so it must survive arbitrary input.

Two defects this locks down, both found by posting junk at it:

1. An invalid signature answered **HTTP 200**. To Razorpay a 2xx means
   "delivered, stop retrying", so a merely mistyped webhook secret would
   discard every payment notification and leave orders unpaid - a failure that
   looks exactly like success from the sender's side.

2. Anything that was not a JSON object raised an unhandled exception. Empty
   bodies, non-JSON, ``null`` and arrays all produced a 500 and a traceback,
   from an endpoint that by design accepts unauthenticated requests.
"""

from __future__ import annotations

import hashlib
import hmac
import json

import pytest
from httpx import ASGITransport, AsyncClient

pytestmark = pytest.mark.asyncio

SECRET = "whsec_unit_test"
VALID_BODY = json.dumps(
    {"event": "payment_link.paid", "event_id": "evt_unit_1", "payload": {}}
).encode()


def sign(body: bytes, secret: str = SECRET) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


@pytest.fixture
def live_app(monkeypatch):
    """The app with signature verification actually switched on.

    RAZORPAY_MOCK short-circuits verification to always pass, which is right
    for local development and useless for testing the check itself.
    """
    monkeypatch.setenv("RAZORPAY_MOCK", "false")
    monkeypatch.setenv("RAZORPAY_WEBHOOK_SECRET", SECRET)

    from config.settings import get_settings

    get_settings.cache_clear()
    from main import create_app

    app = create_app()
    yield app
    get_settings.cache_clear()


@pytest.fixture
async def live(live_app):
    async with AsyncClient(
        transport=ASGITransport(app=live_app), base_url="http://test"
    ) as c:
        yield c


# --- signature --------------------------------------------------------------


async def test_valid_signature_is_accepted(live):
    r = await live.post(
        "/webhooks/razorpay",
        content=VALID_BODY,
        headers={"X-Razorpay-Signature": sign(VALID_BODY)},
    )
    assert r.status_code == 200


async def test_missing_signature_is_401_not_200(live):
    r = await live.post("/webhooks/razorpay", content=VALID_BODY)
    assert r.status_code == 401, "a 2xx here tells Razorpay to stop retrying"
    assert r.json()["error"]["code"] == "INVALID_SIGNATURE"


async def test_wrong_signature_is_401(live):
    r = await live.post(
        "/webhooks/razorpay",
        content=VALID_BODY,
        headers={"X-Razorpay-Signature": "deadbeef"},
    )
    assert r.status_code == 401


async def test_signature_from_the_wrong_secret_is_401(live):
    r = await live.post(
        "/webhooks/razorpay",
        content=VALID_BODY,
        headers={"X-Razorpay-Signature": sign(VALID_BODY, "not-the-secret")},
    )
    assert r.status_code == 401


async def test_signature_is_bound_to_the_body(live):
    """A signature lifted from one payload must not validate another."""
    tampered = json.dumps({"event": "payment_link.paid", "event_id": "evt_2"}).encode()
    r = await live.post(
        "/webhooks/razorpay",
        content=tampered,
        headers={"X-Razorpay-Signature": sign(VALID_BODY)},
    )
    assert r.status_code == 401


# --- malformed bodies -------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [b"", b"garbage", b"null", b"[1,2,3]", b'"a string"', b"123", b"{", b"\xff\xfe"],
)
async def test_malformed_bodies_are_400_never_500(live, raw):
    r = await live.post(
        "/webhooks/razorpay", content=raw, headers={"X-Razorpay-Signature": sign(raw)}
    )
    assert r.status_code < 500, f"{raw!r} produced {r.status_code}"
    assert r.status_code == 400


async def test_a_very_large_body_does_not_crash(live):
    raw = b'{"event":"x","event_id":"big","pad":"' + b"a" * 200_000 + b'"}'
    r = await live.post(
        "/webhooks/razorpay", content=raw, headers={"X-Razorpay-Signature": sign(raw)}
    )
    assert r.status_code < 500
