"""Red team: every way to move money without a valid authorization.

Phase 5's acceptance is a negative claim - *no* financial action proceeds
without a passing decision and, where required, approval. A negative claim
cannot be demonstrated by the happy path, so this file is written as an
attacker: each test is an attempt to get money out, and passes only when the
attempt fails.

The attacks are grouped by what they try to subvert:

* the gate itself - spend without ever being approved
* the scope - spend an approval on a different, larger action
* the quorum - be both approvers
* the tenant boundary - approve or spend someone else's authorization
* the clock - use an approval after it died
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient

from core.exceptions import ConflictError, NotFoundError
from core.jwt import JWTManager
from modules.authorization.service import AuthorizationService
from policy.models import ActionKind, FinancialAction
from repositories.authorizations import _AUTHORIZATIONS, reset_authorizations

pytestmark = [pytest.mark.asyncio, pytest.mark.security]

MERCHANT = "merchant_keen"
OTHER_MERCHANT = "merchant_acme"
TENANT = "11111111-1111-1111-1111-111111111111"

QUIET = {
    "today_total_paise": 0,
    "actions_last_hour": 0,
    "buyer_age_days": 900,
    "buyer_prior_orders": 40,
    "buyer_country": "IN",
    "ip_country": "IN",
}


@pytest.fixture(autouse=True)
def _clean():
    reset_authorizations()
    yield
    reset_authorizations()


@pytest.fixture
def app():
    from main import create_app

    return create_app()


@pytest.fixture
async def client(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


def token(user_id: str, role: str, merchant_id: str = MERCHANT) -> dict:
    jwt = JWTManager().create_access_token(
        user_id=user_id, merchant_id=merchant_id, role=role, tenant_id=TENANT
    )
    return {"Authorization": f"Bearer {jwt}"}


@pytest.fixture
def mallory():
    """Our attacker. A real manager at this merchant - the interesting case is
    not an outsider but an insider with legitimate credentials."""
    return token("mgr_mallory", "manager")


@pytest.fixture
def bob():
    return token("mgr_bob", "manager")


@pytest.fixture
def admin():
    return token("adm_root", "admin")


def body(amount: int, *, kind: str = "payment", subject: str = "ord_target") -> dict:
    return {"kind": kind, "amount_paise": amount, "subject_id": subject, "context": QUIET}


async def request_auth(client, headers, amount: int, **kw):
    return await client.post(
        "/api/v1/authorizations", headers=headers, json=body(amount, **kw)
    )


def action(
    *, amount: int, subject: str = "ord_target", kind: ActionKind = ActionKind.PAYMENT
) -> FinancialAction:
    return FinancialAction(
        kind=kind,
        merchant_id=MERCHANT,
        amount_paise=amount,
        subject_id=subject,
        actor_id="mgr_mallory",
        actor_role="manager",
    )


def expire(auth_id: str) -> None:
    """Drag an authorization's expiry into the past.

    Reaches into the store rather than sleeping: the TTL is fifteen minutes and
    a test that waited for it would be a test nobody runs.
    """
    _AUTHORIZATIONS[auth_id]["expires_at"] = datetime.now(UTC) - timedelta(seconds=1)


# --- attacking the gate itself ----------------------------------------------


async def test_a_forged_authorization_id_cannot_be_spent(client):
    with pytest.raises(NotFoundError):
        await AuthorizationService().consume(
            "auth_deadbeefdeadbeef", merchant_id=MERCHANT, action=action(amount=250_000)
        )


async def test_a_pending_authorization_cannot_be_spent(client, mallory):
    """The core claim. Escalated money does not move because someone asked."""
    record = (await request_auth(client, mallory, 15_000_000)).json()
    assert record["status"] == "pending"

    with pytest.raises(ConflictError) as exc:
        await AuthorizationService().consume(
            record["id"], merchant_id=MERCHANT, action=action(amount=15_000_000)
        )
    assert exc.value.code == "AUTHORIZATION_NOT_APPROVED"


async def test_a_denied_authorization_cannot_be_spent(client, mallory):
    record = (await request_auth(client, mallory, 999_000_000)).json()
    with pytest.raises(ConflictError) as exc:
        await AuthorizationService().consume(
            record["id"], merchant_id=MERCHANT, action=action(amount=999_000_000)
        )
    assert exc.value.code == "AUTHORIZATION_NOT_APPROVED"


async def test_a_revoked_authorization_cannot_be_spent(client, mallory):
    record = (await request_auth(client, mallory, 250_000)).json()
    service = AuthorizationService()
    await service.revoke(record["id"], merchant_id=MERCHANT)

    with pytest.raises(ConflictError):
        await service.consume(
            record["id"], merchant_id=MERCHANT, action=action(amount=250_000)
        )


async def test_an_authorization_cannot_be_spent_twice(client, mallory):
    record = (await request_auth(client, mallory, 250_000)).json()
    service = AuthorizationService()
    await service.consume(record["id"], merchant_id=MERCHANT, action=action(amount=250_000))

    with pytest.raises(ConflictError) as exc:
        await service.consume(
            record["id"], merchant_id=MERCHANT, action=action(amount=250_000)
        )
    assert exc.value.code == "AUTHORIZATION_ALREADY_CONSUMED"


# --- attacking the scope ----------------------------------------------------


async def test_an_approval_for_a_small_amount_cannot_pay_a_large_one(client, mallory):
    """The signed blank cheque. Without fingerprint binding, every other
    guarantee in this file is decoration: get ten rupees approved, spend ten
    lakh."""
    record = (await request_auth(client, mallory, 1_000)).json()
    assert record["status"] == "approved"

    with pytest.raises(ConflictError) as exc:
        await AuthorizationService().consume(
            record["id"], merchant_id=MERCHANT, action=action(amount=100_000_000)
        )
    assert exc.value.code == "AUTHORIZATION_SCOPE_MISMATCH"


async def test_shaving_the_amount_upward_by_one_paisa_is_caught(client, mallory):
    """No tolerance. A gate that allowed "close enough" would be probed for
    exactly how close."""
    record = (await request_auth(client, mallory, 250_000)).json()
    with pytest.raises(ConflictError):
        await AuthorizationService().consume(
            record["id"], merchant_id=MERCHANT, action=action(amount=250_001)
        )


async def test_an_approval_cannot_be_moved_to_another_order(client, mallory):
    record = (await request_auth(client, mallory, 250_000, subject="ord_target")).json()
    with pytest.raises(ConflictError) as exc:
        await AuthorizationService().consume(
            record["id"],
            merchant_id=MERCHANT,
            action=action(amount=250_000, subject="ord_someone_elses"),
        )
    assert exc.value.code == "AUTHORIZATION_SCOPE_MISMATCH"


async def test_a_payment_approval_cannot_be_spent_as_a_refund(client, mallory):
    record = (await request_auth(client, mallory, 250_000)).json()
    with pytest.raises(ConflictError):
        await AuthorizationService().consume(
            record["id"],
            merchant_id=MERCHANT,
            action=action(amount=250_000, kind=ActionKind.REFUND),
        )


async def test_a_failed_scope_check_leaves_the_authorization_unspent(client, mallory):
    """A rejected attempt must not burn the record - otherwise a single
    malformed retry becomes a denial of service on a legitimate payment."""
    record = (await request_auth(client, mallory, 250_000)).json()
    service = AuthorizationService()
    with pytest.raises(ConflictError):
        await service.consume(
            record["id"], merchant_id=MERCHANT, action=action(amount=999_999)
        )

    still_good = await service.consume(
        record["id"], merchant_id=MERCHANT, action=action(amount=250_000)
    )
    assert still_good["status"] == "consumed"


# --- attacking the quorum ---------------------------------------------------


async def test_the_requester_cannot_approve_their_own_request(client, mallory):
    """Four eyes. Without this a quorum of two is one person wearing two hats."""
    record = (await request_auth(client, mallory, 15_000_000)).json()
    r = await client.post(
        f"/api/v1/authorizations/{record['id']}/approve", headers=mallory, json={}
    )
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "SELF_APPROVAL_FORBIDDEN"


async def test_self_approval_is_refused_even_for_an_admin(client, admin):
    """Admin is a role, not an exemption from separation of duties."""
    record = (await request_auth(client, admin, 15_000_000)).json()
    r = await client.post(
        f"/api/v1/authorizations/{record['id']}/approve", headers=admin, json={}
    )
    assert r.status_code == 403


async def test_one_approver_cannot_fill_a_quorum_alone(client, mallory, bob):
    """Clicking approve twice must not satisfy a two-person requirement."""
    record = (await request_auth(client, mallory, 35_000_000)).json()
    assert record["required_approvals"] == 2

    first = await client.post(
        f"/api/v1/authorizations/{record['id']}/approve", headers=bob, json={}
    )
    assert first.status_code == 200
    assert first.json()["status"] == "pending"

    second = await client.post(
        f"/api/v1/authorizations/{record['id']}/approve", headers=bob, json={}
    )
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "DUPLICATE_APPROVAL"

    after = await client.get(f"/api/v1/authorizations/{record['id']}", headers=bob)
    assert after.json()["status"] == "pending"
    assert len(after.json()["approvers"]) == 1


async def test_a_role_without_the_permission_cannot_approve(client, mallory):
    for role in ("shopper", "support_agent", "service"):
        record = (await request_auth(client, mallory, 15_000_000)).json()
        r = await client.post(
            f"/api/v1/authorizations/{record['id']}/approve",
            headers=token(f"user_{role}", role),
            json={},
        )
        assert r.status_code == 403, f"{role} was allowed to approve"


async def test_a_service_account_cannot_approve_its_own_requests(client):
    """A leaked service key that could approve what it asked for would make the
    whole gate ceremonial."""
    svc = token("svc_worker", "service")
    record = (await request_auth(client, svc, 15_000_000)).json()
    r = await client.post(
        f"/api/v1/authorizations/{record['id']}/approve", headers=svc, json={}
    )
    assert r.status_code == 403


async def test_an_approver_cannot_be_named_in_the_body(client, mallory, bob):
    """No field names the approver; it is always the token holder. Otherwise
    four-eyes is advisory."""
    record = (await request_auth(client, mallory, 15_000_000)).json()
    r = await client.post(
        f"/api/v1/authorizations/{record['id']}/approve",
        headers=bob,
        json={"approver_id": "mgr_alice"},
    )
    assert r.status_code == 422


# --- attacking the tenant boundary ------------------------------------------


async def test_another_merchant_cannot_read_the_authorization(client, mallory):
    record = (await request_auth(client, mallory, 15_000_000)).json()
    outsider = token("mgr_eve", "manager", merchant_id=OTHER_MERCHANT)
    r = await client.get(f"/api/v1/authorizations/{record['id']}", headers=outsider)
    assert r.status_code == 404, "existence must not be confirmed across tenants"


async def test_another_merchants_admin_cannot_approve(client, mallory):
    """Admin is scoped to a merchant, not to the deployment."""
    record = (await request_auth(client, mallory, 15_000_000)).json()
    outsider = token("adm_eve", "admin", merchant_id=OTHER_MERCHANT)
    r = await client.post(
        f"/api/v1/authorizations/{record['id']}/approve", headers=outsider, json={}
    )
    assert r.status_code == 404


async def test_another_merchant_cannot_spend_the_authorization(client, mallory):
    record = (await request_auth(client, mallory, 250_000)).json()
    with pytest.raises(NotFoundError):
        await AuthorizationService().consume(
            record["id"], merchant_id=OTHER_MERCHANT, action=action(amount=250_000)
        )


async def test_a_forbidden_and_a_missing_authorization_answer_identically(client, mallory):
    """Any difference is an oracle for enumerating real ids.

    The message echoes the id that was asked for, which is the caller's own
    input and so tells them nothing. Normalising it away is what leaves the
    part that would leak: everything else must match byte for byte.
    """
    record = (await request_auth(client, mallory, 15_000_000)).json()
    real_id = record["id"]
    fake_id = "auth_0000000000000000"
    outsider = token("mgr_eve", "manager", merchant_id=OTHER_MERCHANT)

    def comparable(response, asked_for: str):
        error = dict(response.json()["error"])
        error.pop("request_id", None)
        error["message"] = error["message"].replace(asked_for, "<id>")
        return response.status_code, error

    real = await client.get(f"/api/v1/authorizations/{real_id}", headers=outsider)
    fake = await client.get(f"/api/v1/authorizations/{fake_id}", headers=outsider)

    assert comparable(real, real_id) == comparable(fake, fake_id)
    # And the 404 is genuinely an authorization decision, not a lookup miss:
    # the owner sees the same record perfectly well.
    assert (
        await client.get(f"/api/v1/authorizations/{real_id}", headers=mallory)
    ).status_code == 200


# --- attacking the clock ----------------------------------------------------


async def test_an_expired_authorization_cannot_be_approved(client, mallory, bob):
    """A stale approval is an approval granted against facts that no longer
    hold."""
    record = (await request_auth(client, mallory, 15_000_000)).json()
    expire(record["id"])

    r = await client.post(
        f"/api/v1/authorizations/{record['id']}/approve", headers=bob, json={}
    )
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "AUTHORIZATION_EXPIRED"


async def test_an_expired_authorization_cannot_be_spent(client, mallory):
    record = (await request_auth(client, mallory, 250_000)).json()
    assert record["status"] == "approved"
    expire(record["id"])

    with pytest.raises(ConflictError) as exc:
        await AuthorizationService().consume(
            record["id"], merchant_id=MERCHANT, action=action(amount=250_000)
        )
    assert exc.value.code == "AUTHORIZATION_EXPIRED"


async def test_expiry_is_applied_on_read_not_by_a_sweeper(client, mallory):
    """A background job that fell behind would leave records that read as
    approved past their deadline."""
    record = (await request_auth(client, mallory, 250_000)).json()
    expire(record["id"])
    r = await client.get(f"/api/v1/authorizations/{record['id']}", headers=mallory)
    assert r.json()["status"] == "expired"


# --- attacking the policy layer ---------------------------------------------


async def test_a_role_cannot_be_claimed_in_the_body(client):
    """The actor's role comes from the token. A shopper naming itself admin in
    the body must not become one."""
    shopper = token("shopper_dave", "shopper")
    r = await client.post(
        "/api/v1/authorizations",
        headers=shopper,
        json={
            "kind": "payment",
            "amount_paise": 1_000,
            "subject_id": "ord_x",
            "actor_role": "admin",
        },
    )
    assert r.status_code in (403, 422)


async def test_a_manager_cannot_authorize_a_payout(client, mallory):
    """Payouts move money to a bank account. Admin only, enforced in the rules
    and not merely by which button a UI shows."""
    r = await client.post(
        "/api/v1/authorizations", headers=mallory, json=body(1_000, kind="payout")
    )
    assert r.json()["status"] == "denied"


async def test_splitting_an_amount_does_not_evade_the_daily_cap(client, mallory):
    """The cap counts the action being requested, so the last one over the line
    is refused rather than being the one that slips through."""
    from config.policy import load_merchant_policy

    policy = load_merchant_policy(MERCHANT)
    r = await client.post(
        "/api/v1/authorizations",
        headers=mallory,
        json={
            "kind": "payment",
            "amount_paise": 1_000,
            "subject_id": "ord_x",
            "context": {**QUIET, "today_total_paise": policy.daily_total_cap_paise},
        },
    )
    assert r.json()["status"] == "denied"


async def test_a_tampered_token_is_refused_before_any_gate_runs(client):
    good = JWTManager().create_access_token(
        user_id="mgr_mallory", merchant_id=MERCHANT, role="manager", tenant_id=TENANT
    )
    forged = good[:-4] + ("aaaa" if not good.endswith("aaaa") else "bbbb")
    r = await client.post(
        "/api/v1/authorizations",
        headers={"Authorization": f"Bearer {forged}"},
        json=body(250_000),
    )
    assert r.status_code == 401


async def test_an_unauthenticated_caller_reaches_nothing(client):
    assert (await client.post("/api/v1/authorizations", json=body(1))).status_code == 401
    assert (await client.get("/api/v1/authorizations/auth_x")).status_code == 401
    assert (
        await client.post("/api/v1/authorizations/auth_x/approve", json={})
    ).status_code == 401
