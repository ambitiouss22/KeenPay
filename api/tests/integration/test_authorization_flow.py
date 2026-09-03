"""Policy -> Risk -> Authorization, through the real HTTP surface.

The Phase 5 acceptance: no financial action proceeds without a passing decision
and, where required, approval. These tests walk that path as a client would -
asking for an authorization, collecting approvals, spending it - and check the
gate at every step.

Tokens are minted directly rather than obtained by logging in. A quorum needs
two distinct managers, the dev seed has one of each role, and minting is the
honest way to get two. It also keeps these tests independent of whatever the
seed happens to contain this month.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from core.jwt import JWTManager
from modules.authorization.service import AuthorizationService
from policy.models import ActionKind, FinancialAction
from repositories.authorizations import AuthorizationRepository, reset_authorizations
from repositories.orders import OrderRepository

pytestmark = pytest.mark.asyncio

MERCHANT = "merchant_keen"
TENANT = "11111111-1111-1111-1111-111111111111"

#: Context chosen to score zero risk, so a test that wants a signal has to ask
#: for it rather than inheriting one by accident.
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
    """Authorizations live in a module-level dict. Without this, tests would
    see each other's records and pass or fail depending on ordering."""
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
def manager():
    return token("mgr_alice", "manager")


@pytest.fixture
def second_manager():
    return token("mgr_bob", "manager")


@pytest.fixture
def admin():
    return token("adm_root", "admin")


@pytest.fixture
def support():
    return token("sup_carol", "support_agent")


@pytest.fixture
def shopper():
    return token("shopper_dave", "shopper")


def body(amount: int, *, kind: str = "payment", subject: str = "ord_test1", **ctx) -> dict:
    return {
        "kind": kind,
        "amount_paise": amount,
        "subject_id": subject,
        "context": {**QUIET, **ctx},
    }


async def request_auth(client, headers, amount: int, **kwargs):
    return await client.post(
        "/api/v1/authorizations", headers=headers, json=body(amount, **kwargs)
    )


async def paid_order(*, amount_paise: int = 100_000, merchant_id: str = MERCHANT) -> dict:
    """A real captured order, so the refund path has something to reason about."""
    repo = OrderRepository()
    order = await repo.create_pending(
        session_id="00000000-0000-0000-0000-000000000001",
        merchant_id=merchant_id,
        user_id="shopper_dave",
        line_items=[{"sku": "X", "quantity": 1, "unit_price_paise": amount_paise}],
        subtotal_paise=amount_paise,
        discount_amount_paise=0,
        final_amount_paise=amount_paise,
        guardrail_decision_id="00000000-0000-0000-0000-000000000002",
        offer_version=1,
        policy_version="test",
        idempotency_key="key-abcdefgh",
        razorpay_payment_link_id="plink_test",
        razorpay_payment_link_url="https://example.invalid/pay",
    )
    return await repo.mark_paid(order["id"], payment_id="pay_test")


# --- policy evaluate: the dry run -------------------------------------------


async def test_evaluate_reports_an_ordinary_payment_as_auto_approving(client, manager):
    r = await client.post("/api/v1/policy/evaluate", headers=manager, json=body(250_000))
    assert r.status_code == 200, r.text
    payload = r.json()
    assert payload["decision"]["outcome"] == "allow"
    assert payload["required_approvals"] == 0
    assert payload["would_auto_approve"] is True
    assert payload["risk"]["band"] == "low"


async def test_evaluate_warns_before_the_operator_commits(client, manager):
    """The point of a dry run: "this will need two approvals", said early."""
    r = await client.post("/api/v1/policy/evaluate", headers=manager, json=body(35_000_000))
    assert r.json()["required_approvals"] == 2
    assert r.json()["would_auto_approve"] is False


async def test_evaluate_answers_200_even_for_a_denial(client, manager):
    """A complete answer to "what would happen?" is not a client error."""
    r = await client.post("/api/v1/policy/evaluate", headers=manager, json=body(999_000_000))
    assert r.status_code == 200
    assert r.json()["decision"]["outcome"] == "deny"


async def test_a_denied_action_is_not_scored(client, manager):
    """The live path does not score a denial, so the dry run must not report a
    score the real gate would never have computed."""
    r = await client.post("/api/v1/policy/evaluate", headers=manager, json=body(999_000_000))
    assert r.json()["risk"] is None


async def test_evaluate_creates_nothing(client, manager):
    await client.post("/api/v1/policy/evaluate", headers=manager, json=body(35_000_000))
    assert await AuthorizationRepository().list_for_merchant(merchant_id=MERCHANT) == []


async def test_a_shopper_cannot_probe_the_merchants_limits(client, shopper):
    """The response enumerates ceilings and thresholds, which would make an open
    evaluate endpoint a binary search for the largest unattended amount."""
    r = await client.post("/api/v1/policy/evaluate", headers=shopper, json=body(1_000))
    assert r.status_code == 403


async def test_evaluate_requires_authentication(client):
    r = await client.post("/api/v1/policy/evaluate", json=body(1_000))
    assert r.status_code == 401


# --- requesting an authorization --------------------------------------------


async def test_a_routine_payment_authorizes_without_a_human(client, manager):
    r = await request_auth(client, manager, 250_000)
    assert r.status_code == 201, r.text
    record = r.json()
    assert record["status"] == "approved"
    assert record["required_approvals"] == 0
    assert record["approvers"] == []
    assert record["approved_at"] is not None


async def test_an_escalated_payment_waits_for_one_human(client, manager):
    r = await request_auth(client, manager, 15_000_000)
    record = r.json()
    assert record["status"] == "pending"
    assert record["required_approvals"] == 1


async def test_a_high_risk_payment_needs_a_quorum(client, manager):
    r = await request_auth(client, manager, 35_000_000)
    record = r.json()
    assert record["status"] == "pending"
    assert record["required_approvals"] == 2
    assert record["risk"]["band"] == "high"


async def test_a_new_buyer_alone_needs_one_approval(client, manager):
    r = await request_auth(client, manager, 250_000, buyer_age_days=0, buyer_prior_orders=0)
    assert r.json()["required_approvals"] == 1


async def test_a_new_buyer_spending_a_lot_needs_a_quorum(client, manager):
    """Corroboration: neither signal alone reaches high, together they do."""
    r = await request_auth(
        client, manager, 15_000_000, buyer_age_days=0, buyer_prior_orders=0
    )
    assert r.json()["required_approvals"] == 2


async def test_a_denied_action_still_leaves_a_record(client, manager):
    """A refusal that left no trace is invisible to whoever investigates why a
    merchant's payment failed."""
    r = await request_auth(client, manager, 999_000_000)
    assert r.status_code == 201
    record = r.json()
    assert record["status"] == "denied"
    assert record["reasons"]
    assert record["expires_at"] is None, "a denial is terminal, not merely expiring"


async def test_the_record_carries_the_full_decision(client, manager):
    """The audit trail for a money movement. A summary is the thing you find is
    missing a field on the day you need it in a dispute."""
    record = (await request_auth(client, manager, 15_000_000)).json()
    assert record["policy_decision"]["outcome"] == "escalate"
    assert record["policy_decision"]["rule_results"]
    assert record["policy_decision"]["policy_version"]
    assert record["risk"]["components"].keys() >= {"amount", "geography"}


async def test_the_requester_is_taken_from_the_token(client, manager):
    assert (await request_auth(client, manager, 250_000)).json()["requested_by"] == "mgr_alice"


async def test_a_shopper_cannot_request_an_authorization(client, shopper):
    assert (await request_auth(client, shopper, 250_000)).status_code == 403


async def test_support_cannot_request_an_authorization(client, support):
    assert (await request_auth(client, support, 250_000)).status_code == 403


async def test_a_float_amount_is_refused_at_the_edge(client, manager):
    r = await client.post(
        "/api/v1/authorizations",
        headers=manager,
        json={"kind": "payment", "amount_paise": 249.9, "subject_id": "ord_x"},
    )
    assert r.status_code == 422


async def test_an_unknown_action_kind_is_refused(client, manager):
    r = await client.post(
        "/api/v1/authorizations",
        headers=manager,
        json={"kind": "wire_transfer", "amount_paise": 100, "subject_id": "ord_x"},
    )
    assert r.status_code == 422


async def test_the_body_cannot_name_its_own_merchant(client, manager):
    """extra='forbid'. A body that could name its merchant is a cross-tenant
    write waiting to be found."""
    r = await client.post(
        "/api/v1/authorizations",
        headers=manager,
        json={
            "kind": "payment",
            "amount_paise": 100,
            "subject_id": "ord_x",
            "merchant_id": "merchant_acme",
        },
    )
    assert r.status_code == 422


# --- reading ----------------------------------------------------------------


async def test_an_authorization_can_be_read_back(client, manager):
    created = (await request_auth(client, manager, 15_000_000)).json()
    r = await client.get(f"/api/v1/authorizations/{created['id']}", headers=manager)
    assert r.status_code == 200
    assert r.json()["id"] == created["id"]


async def test_support_may_read_but_not_approve(client, manager, support):
    """Separation of duties: an investigator sees everything, approves nothing."""
    created = (await request_auth(client, manager, 15_000_000)).json()
    assert (
        await client.get(f"/api/v1/authorizations/{created['id']}", headers=support)
    ).status_code == 200
    assert (
        await client.post(
            f"/api/v1/authorizations/{created['id']}/approve", headers=support, json={}
        )
    ).status_code == 403


async def test_an_unknown_authorization_is_404(client, manager):
    r = await client.get("/api/v1/authorizations/auth_nope", headers=manager)
    assert r.status_code == 404


# --- approving --------------------------------------------------------------


async def test_one_approval_completes_a_single_approver_requirement(
    client, manager, second_manager
):
    created = (await request_auth(client, manager, 15_000_000)).json()
    r = await client.post(
        f"/api/v1/authorizations/{created['id']}/approve",
        headers=second_manager,
        json={"note": "checked against the invoice"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "approved"
    assert len(r.json()["approvers"]) == 1


async def test_a_quorum_needs_two_distinct_people(client, manager, second_manager, admin):
    created = (await request_auth(client, manager, 35_000_000)).json()
    auth_id = created["id"]

    first = await client.post(
        f"/api/v1/authorizations/{auth_id}/approve", headers=second_manager, json={}
    )
    assert first.status_code == 200
    assert first.json()["status"] == "pending", "one approval is not a quorum of two"

    second = await client.post(
        f"/api/v1/authorizations/{auth_id}/approve", headers=admin, json={}
    )
    assert second.status_code == 200
    assert second.json()["status"] == "approved"
    assert len(second.json()["approvers"]) == 2


async def test_an_approver_is_recorded_by_name_and_role(client, manager, second_manager):
    created = (await request_auth(client, manager, 15_000_000)).json()
    r = await client.post(
        f"/api/v1/authorizations/{created['id']}/approve", headers=second_manager, json={}
    )
    approver = r.json()["approvers"][0]
    assert approver["approver_id"] == "mgr_bob"
    assert approver["role"] == "manager"
    assert approver["at"]


async def test_approving_an_already_approved_record_is_a_conflict(
    client, manager, second_manager, admin
):
    created = (await request_auth(client, manager, 15_000_000)).json()
    await client.post(
        f"/api/v1/authorizations/{created['id']}/approve", headers=second_manager, json={}
    )
    again = await client.post(
        f"/api/v1/authorizations/{created['id']}/approve", headers=admin, json={}
    )
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "AUTHORIZATION_NOT_PENDING"


async def test_approving_a_denied_record_is_refused(client, manager, second_manager):
    """Nobody may approve past a denial - that is the whole difference between
    a denial and an escalation."""
    created = (await request_auth(client, manager, 999_000_000)).json()
    r = await client.post(
        f"/api/v1/authorizations/{created['id']}/approve", headers=second_manager, json={}
    )
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "AUTHORIZATION_DENIED"


# --- refunds, through the guard ---------------------------------------------


async def test_a_small_refund_on_a_paid_order_authorizes(client, manager):
    order = await paid_order(amount_paise=100_000)
    r = await request_auth(client, manager, 50_000, kind="refund", subject=order["id"])
    assert r.status_code == 201, r.text
    assert r.json()["status"] == "approved"
    assert r.json()["action_kind"] == "refund"


async def test_a_refund_larger_than_the_capture_never_reaches_the_gate(client, manager):
    """An authorization for a refund that was never going to be permitted is a
    pending approval sitting in somebody's queue for no reason."""
    order = await paid_order(amount_paise=100_000)
    r = await request_auth(client, manager, 100_001, kind="refund", subject=order["id"])
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "REFUND_NOT_ELIGIBLE"
    assert await AuthorizationRepository().list_for_merchant(merchant_id=MERCHANT) == []


async def test_a_refund_against_an_unknown_order_is_refused(client, manager):
    r = await request_auth(client, manager, 1_000, kind="refund", subject="ord_nope")
    assert r.status_code == 422
    assert "order not found" in r.text


async def test_a_refund_against_another_merchants_order_is_refused(client, manager):
    """Same answer as an unknown order: confirming an id belongs to someone
    maps another merchant's order numbers."""
    order = await paid_order(merchant_id="merchant_acme")
    r = await request_auth(client, manager, 1_000, kind="refund", subject=order["id"])
    assert r.status_code == 422
    assert "order not found" in r.text


async def test_a_large_refund_escalates_sooner_than_a_payment_would(client, manager):
    amount = 3_000_000  # over the refund threshold, under the payment one
    order = await paid_order(amount_paise=amount)
    refund = (
        await request_auth(client, manager, amount, kind="refund", subject=order["id"])
    ).json()
    payment = (await request_auth(client, manager, amount)).json()

    assert refund["status"] == "pending"
    assert payment["status"] == "approved"


# --- spending the authorization ---------------------------------------------
# consume() has no route of its own in this phase: it is the seam the payment
# path calls in phase 6. Exercised directly, because it is the gate.


def action_for(record: dict, *, amount: int | None = None) -> FinancialAction:
    return FinancialAction(
        kind=ActionKind(record["action_kind"]),
        merchant_id=record["merchant_id"],
        amount_paise=amount if amount is not None else record["amount_paise"],
        subject_id=record["subject_id"],
        actor_id=record["requested_by"],
        actor_role=record["requested_by_role"],
    )


async def test_an_approved_authorization_can_be_spent(client, manager):
    record = (await request_auth(client, manager, 250_000)).json()
    spent = await AuthorizationService().consume(
        record["id"], merchant_id=MERCHANT, action=action_for(record)
    )
    assert spent["status"] == "consumed"
    assert spent["consumed_at"] is not None


async def test_spending_twice_is_refused(client, manager):
    """Single use. The second attempt is a double charge."""
    record = (await request_auth(client, manager, 250_000)).json()
    service = AuthorizationService()
    await service.consume(record["id"], merchant_id=MERCHANT, action=action_for(record))

    from core.exceptions import ConflictError

    with pytest.raises(ConflictError) as exc:
        await service.consume(record["id"], merchant_id=MERCHANT, action=action_for(record))
    assert exc.value.code == "AUTHORIZATION_ALREADY_CONSUMED"


async def test_a_quorum_authorization_is_spendable_only_after_both_approvals(
    client, manager, second_manager, admin
):
    from core.exceptions import ConflictError

    record = (await request_auth(client, manager, 35_000_000)).json()
    service = AuthorizationService()

    with pytest.raises(ConflictError):
        await service.consume(record["id"], merchant_id=MERCHANT, action=action_for(record))

    await client.post(
        f"/api/v1/authorizations/{record['id']}/approve", headers=second_manager, json={}
    )
    with pytest.raises(ConflictError):
        await service.consume(record["id"], merchant_id=MERCHANT, action=action_for(record))

    await client.post(
        f"/api/v1/authorizations/{record['id']}/approve", headers=admin, json={}
    )
    spent = await service.consume(
        record["id"], merchant_id=MERCHANT, action=action_for(record)
    )
    assert spent["status"] == "consumed"
