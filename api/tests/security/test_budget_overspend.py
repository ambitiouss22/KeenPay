"""Attacks on the growth path.

Every assertion here is made against evidence rather than against the API's own
account of itself: the campaign row as stored, and the append-only ledger of what
actually moved. A test that asked the reserve endpoint whether it had
double-reserved would be asking the component under test to grade its own work.

The threats, in order of how much they would cost:

* overspend under concurrency - two callers promised the same last rupee
* overspend by retry - a duplicated reservation quietly shrinks every other order
* budget manufactured by releasing more than was reserved
* a buyer agent, or a scoped credential, reaching a merchant's budget at all
"""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path

import pytest

from core.jwt import JWTManager
from repositories.campaigns import CampaignRepository, reset_campaigns
from repositories.idempotency import reset_idempotency
from repositories.opportunities import reset_opportunities

pytestmark = [pytest.mark.asyncio, pytest.mark.security]

MERCHANT = "merchant_keen"
REPO_ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(autouse=True)
def _clean_growth_stores():
    reset_campaigns()
    reset_opportunities()
    reset_idempotency()
    yield
    reset_campaigns()
    reset_opportunities()
    reset_idempotency()


@pytest.fixture
def admin(admin_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {admin_token}"}


async def _campaign(client, admin, *, budget_paise: int) -> str:
    response = await client.post(
        "/api/v1/campaigns",
        headers=admin,
        json={"name": "Adversarial", "budget_paise": budget_paise},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def _stored(campaign_id: str) -> dict:
    """The campaign as the store holds it, not as a response described it."""
    row = await CampaignRepository().get(campaign_id, merchant_id=MERCHANT)
    assert row is not None
    return row


async def _ledger(campaign_id: str) -> list[dict]:
    return await CampaignRepository().ledger_for(campaign_id, merchant_id=MERCHANT)


# --- overspend --------------------------------------------------------------


async def test_concurrent_reserves_cannot_overspend(client, admin):
    """Twenty callers race for a budget that fits exactly ten.

    Each carries its own idempotency key, so nothing here is deduplicated - they
    genuinely contend for the same money. Exactly ten must win, and the stored
    row must agree with how many did.
    """
    budget, per_call, callers = 10_000, 1_000, 20
    campaign_id = await _campaign(client, admin, budget_paise=budget)

    async def reserve_once(n: int):
        return await client.post(
            f"/api/v1/campaigns/{campaign_id}/reserve",
            headers=admin,
            json={"amount_paise": per_call, "idempotency_key": f"race-{n}-{uuid.uuid4().hex}"},
        )

    responses = await asyncio.gather(*(reserve_once(n) for n in range(callers)))
    winners = [r for r in responses if r.status_code == 200]
    losers = [r for r in responses if r.status_code != 200]

    assert len(winners) == budget // per_call
    # Every refusal is a budget refusal. A 500 here would mean the cap held by
    # accident, through a crash, rather than by design.
    assert {r.status_code for r in losers} == {409}
    assert {r.json()["error"]["code"] for r in losers} == {"BUDGET_EXCEEDED"}

    stored = await _stored(campaign_id)
    assert stored["reserved_paise"] == len(winners) * per_call
    assert stored["reserved_paise"] + stored["spent_paise"] <= stored["budget_paise"]
    assert stored["remaining_paise"] == 0


async def test_the_ledger_accounts_for_every_winner(client, admin):
    """The ledger and the counters must tell the same story.

    If a reservation moved the counter without an entry, an overspend would be
    invisible afterwards; if an entry existed without the counter moving, the
    budget would look spent when it was not.
    """
    budget, per_call, callers = 5_000, 1_000, 12
    campaign_id = await _campaign(client, admin, budget_paise=budget)

    async def reserve_once(n: int):
        return await client.post(
            f"/api/v1/campaigns/{campaign_id}/reserve",
            headers=admin,
            json={"amount_paise": per_call, "idempotency_key": f"led-{n}-{uuid.uuid4().hex}"},
        )

    responses = await asyncio.gather(*(reserve_once(n) for n in range(callers)))
    winners = sum(1 for r in responses if r.status_code == 200)

    entries = [e for e in await _ledger(campaign_id) if e["entry_type"] == "reserve"]
    assert len(entries) == winners
    assert sum(e["amount_paise"] for e in entries) == (await _stored(campaign_id))[
        "reserved_paise"
    ]


async def test_a_refused_reservation_leaves_no_ledger_entry(client, admin):
    """A refusal must not be recorded as a movement."""
    campaign_id = await _campaign(client, admin, budget_paise=1_000)
    refused = await client.post(
        f"/api/v1/campaigns/{campaign_id}/reserve",
        headers=admin,
        json={"amount_paise": 1_001, "idempotency_key": f"no-{uuid.uuid4().hex}"},
    )
    assert refused.status_code == 409
    assert await _ledger(campaign_id) == []
    assert (await _stored(campaign_id))["reserved_paise"] == 0


# --- retries ----------------------------------------------------------------


async def test_retrying_a_reservation_does_not_reserve_twice(client, admin):
    """The retry that would otherwise quietly halve every other order's budget."""
    campaign_id = await _campaign(client, admin, budget_paise=10_000)
    body = {"amount_paise": 4_000, "idempotency_key": f"retry-{uuid.uuid4().hex}"}

    first = await client.post(
        f"/api/v1/campaigns/{campaign_id}/reserve", headers=admin, json=body
    )
    second = await client.post(
        f"/api/v1/campaigns/{campaign_id}/reserve", headers=admin, json=body
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json() == first.json()

    assert (await _stored(campaign_id))["reserved_paise"] == 4_000
    assert len([e for e in await _ledger(campaign_id) if e["entry_type"] == "reserve"]) == 1


async def test_concurrent_retries_of_one_key_reserve_once(client, admin):
    """Claim-first: the in-flight duplicate is refused, not queued behind it."""
    campaign_id = await _campaign(client, admin, budget_paise=10_000)
    key = f"dup-{uuid.uuid4().hex}"
    body = {"amount_paise": 3_000, "idempotency_key": key}

    responses = await asyncio.gather(
        *(
            client.post(f"/api/v1/campaigns/{campaign_id}/reserve", headers=admin, json=body)
            for _ in range(5)
        )
    )
    assert {r.status_code for r in responses} <= {200, 409}
    assert (await _stored(campaign_id))["reserved_paise"] == 3_000
    assert len([e for e in await _ledger(campaign_id) if e["entry_type"] == "reserve"]) == 1


async def test_reusing_a_key_for_a_different_amount_is_refused(client, admin):
    """Otherwise a key becomes a way to replay someone else's reservation."""
    campaign_id = await _campaign(client, admin, budget_paise=10_000)
    key = f"reuse-{uuid.uuid4().hex}"

    await client.post(
        f"/api/v1/campaigns/{campaign_id}/reserve",
        headers=admin,
        json={"amount_paise": 1_000, "idempotency_key": key},
    )
    conflicting = await client.post(
        f"/api/v1/campaigns/{campaign_id}/reserve",
        headers=admin,
        json={"amount_paise": 9_000, "idempotency_key": key},
    )
    assert conflicting.status_code == 409
    assert conflicting.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"
    assert (await _stored(campaign_id))["reserved_paise"] == 1_000


# --- manufacturing budget ---------------------------------------------------


async def test_releasing_more_than_reserved_cannot_create_headroom(client, admin):
    campaign_id = await _campaign(client, admin, budget_paise=10_000)
    await client.post(
        f"/api/v1/campaigns/{campaign_id}/reserve",
        headers=admin,
        json={"amount_paise": 2_000, "idempotency_key": f"rel-{uuid.uuid4().hex}"},
    )

    overshoot = await client.post(
        f"/api/v1/campaigns/{campaign_id}/release",
        headers=admin,
        json={"amount_paise": 9_000},
    )
    assert overshoot.status_code == 409
    assert overshoot.json()["error"]["code"] == "RELEASE_EXCEEDS_RESERVED"

    stored = await _stored(campaign_id)
    assert stored["reserved_paise"] == 2_000
    assert stored["remaining_paise"] == 8_000


async def test_a_release_cannot_raise_the_cap(client, admin):
    """Reserve, release, and the budget is back where it started - not higher."""
    campaign_id = await _campaign(client, admin, budget_paise=10_000)
    await client.post(
        f"/api/v1/campaigns/{campaign_id}/reserve",
        headers=admin,
        json={"amount_paise": 10_000, "idempotency_key": f"full-{uuid.uuid4().hex}"},
    )
    await client.post(
        f"/api/v1/campaigns/{campaign_id}/release",
        headers=admin,
        json={"amount_paise": 10_000},
    )
    stored = await _stored(campaign_id)
    assert stored["budget_paise"] == 10_000
    assert stored["remaining_paise"] == 10_000


@pytest.mark.parametrize(
    "amount", [0, -1, -10_000, 10.5, "1000", True], ids=lambda v: repr(v)
)
async def test_a_reservation_that_is_not_a_positive_integer_is_refused(
    client, admin, amount
):
    """Zero, negative, float, string and boolean all stop at the boundary."""
    campaign_id = await _campaign(client, admin, budget_paise=10_000)
    response = await client.post(
        f"/api/v1/campaigns/{campaign_id}/reserve",
        headers=admin,
        json={"amount_paise": amount, "idempotency_key": f"bad-{uuid.uuid4().hex}"},
    )
    assert response.status_code == 422
    assert (await _stored(campaign_id))["reserved_paise"] == 0


async def test_a_campaign_cannot_be_opened_with_no_budget(client, admin):
    response = await client.post(
        "/api/v1/campaigns", headers=admin, json={"name": "Free money", "budget_paise": 0}
    )
    assert response.status_code == 422


async def test_there_is_no_route_that_raises_a_budget(client, admin):
    """A cap that can be raised on request is not a cap.

    Proved against the mounted application rather than against the source: a
    route added later shows up here whether or not anyone remembered this test.
    """
    from main import create_app

    paths = {
        (route.path, method)
        for route in create_app().routes
        for method in getattr(route, "methods", set())
    }
    mutating = {
        (path, method)
        for path, method in paths
        if path.startswith("/api/v1/campaigns") and method in {"PUT", "PATCH", "DELETE"}
    }
    assert mutating == set()


# --- who may touch a budget -------------------------------------------------


@pytest.fixture
async def agent(client, admin):
    """A buyer agent credential, minted the way the runtime gets one."""
    response = await client.post(
        "/api/v1/auth/agent-tokens",
        headers=admin,
        json={
            "agent_id": "agent_buyer_growth",
            "scopes": [
                "catalog:read",
                "session:create",
                "order:read:own",
                "authorization:request",
                "authorization:read",
            ],
        },
    )
    assert response.status_code == 201, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


async def test_an_agent_cannot_reserve_budget(client, admin, agent):
    """The money-adjacent growth route, refused at the Control Plane."""
    campaign_id = await _campaign(client, admin, budget_paise=10_000)
    response = await client.post(
        f"/api/v1/campaigns/{campaign_id}/reserve",
        headers=agent,
        json={"amount_paise": 1_000, "idempotency_key": f"agent-{uuid.uuid4().hex}"},
    )
    assert response.status_code == 403
    assert (await _stored(campaign_id))["reserved_paise"] == 0


async def test_an_agent_cannot_open_a_campaign(client, agent):
    response = await client.post(
        "/api/v1/campaigns", headers=agent, json={"name": "Mine", "budget_paise": 1_000}
    )
    assert response.status_code == 403


async def test_an_agent_cannot_generate_or_read_opportunities(client, agent):
    generated = await client.post(
        "/api/v1/opportunities/generate", headers=agent, json={}
    )
    listed = await client.get("/api/v1/opportunities", headers=agent)
    assert generated.status_code == 403
    assert listed.status_code == 403


async def test_growth_is_not_a_scope_an_agent_credential_can_be_minted_with(
    client, admin
):
    """Refused rather than trimmed, so an operator is told, not quietly given less."""
    response = await client.post(
        "/api/v1/auth/agent-tokens",
        headers=admin,
        json={"agent_id": "agent_greedy", "scopes": ["catalog:read", "growth:manage"]},
    )
    assert response.status_code == 400
    body = response.json()
    assert body["error"]["code"] == "INVALID_AGENT_SCOPE"
    assert "growth:manage" in body["error"]["message"]


async def test_a_forged_growth_scope_grants_nothing(client, admin):
    """Signed with the server's own secret, so the token is genuinely valid.

    Scopes intersect the role; they never widen it. Role ``agent`` has no growth
    permission, so naming one in a scope claim achieves exactly nothing.
    """
    campaign_id = await _campaign(client, admin, budget_paise=10_000)
    forged = JWTManager().create_access_token(
        user_id="agent_evil",
        merchant_id=MERCHANT,
        role="agent",
        audience="keenpay-control-plane",
        scopes=["growth:manage", "growth:read"],
    )
    response = await client.post(
        f"/api/v1/campaigns/{campaign_id}/reserve",
        headers={"Authorization": f"Bearer {forged}"},
        json={"amount_paise": 1_000, "idempotency_key": f"forged-{uuid.uuid4().hex}"},
    )
    assert response.status_code == 403
    assert (await _stored(campaign_id))["reserved_paise"] == 0


async def test_a_shopper_cannot_see_or_spend_a_budget(client, admin, shopper_token):
    campaign_id = await _campaign(client, admin, budget_paise=10_000)
    headers = {"Authorization": f"Bearer {shopper_token}"}
    assert (await client.get("/api/v1/campaigns", headers=headers)).status_code == 403
    assert (
        await client.get(f"/api/v1/campaigns/{campaign_id}/budget", headers=headers)
    ).status_code == 403


async def test_support_may_look_but_not_spend(client, admin, support_token):
    """The read/manage split, exercised by the role it exists for."""
    campaign_id = await _campaign(client, admin, budget_paise=10_000)
    headers = {"Authorization": f"Bearer {support_token}"}

    assert (
        await client.get(f"/api/v1/campaigns/{campaign_id}/budget", headers=headers)
    ).status_code == 200
    reserved = await client.post(
        f"/api/v1/campaigns/{campaign_id}/reserve",
        headers=headers,
        json={"amount_paise": 1_000, "idempotency_key": f"sup-{uuid.uuid4().hex}"},
    )
    assert reserved.status_code == 403
    assert (await _stored(campaign_id))["reserved_paise"] == 0


async def test_an_unauthenticated_caller_reaches_nothing(client, admin):
    campaign_id = await _campaign(client, admin, budget_paise=10_000)
    assert (await client.get("/api/v1/campaigns")).status_code == 401
    assert (
        await client.post(
            f"/api/v1/campaigns/{campaign_id}/reserve",
            json={"amount_paise": 1, "idempotency_key": "x" * 12},
        )
    ).status_code == 401


# --- the database backstop --------------------------------------------------
#
# The in-memory store cannot prove what Postgres does, and these two do not
# pretend to. They guard the two things that make the cap hard in deployment:
# the constraint that refuses an overspending row, and the single statement that
# re-checks the budget under the row lock it takes. Both are easy to lose in a
# refactor and neither is exercised by a suite running without a database.
# The behavioural proof against a real Postgres lives in the tenant-isolation
# suite, which races twenty writers for a budget that fits ten.


def test_the_schema_refuses_an_overspending_row():
    migration = (REPO_ROOT / "db" / "migrations" / "0001_initial.sql").read_text(
        encoding="utf-8"
    )
    assert "campaigns_budget_not_exceeded" in migration
    assert "reserved_paise + spent_paise <= budget_paise" in migration


def test_the_reservation_statement_rechecks_the_budget_under_its_own_lock():
    """A read-then-write reservation double-spends. This is the guard against one.

    The check has to be *inside* the UPDATE's WHERE clause: that is what makes
    Postgres re-evaluate it under the row lock, so the loser of a race matches
    zero rows instead of writing over a stale read.
    """
    source = (REPO_ROOT / "api" / "repositories" / "campaigns.py").read_text(
        encoding="utf-8"
    )
    assert "reserved_paise + spent_paise + :amount <= budget_paise" in source
