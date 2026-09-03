"""Cross-tenant isolation, proven against a real Postgres.

These tests are the acceptance criteria for Phase 1. They are deliberately not
unit tests with a mocked database: row-level security is a property of Postgres,
and the only way to know it holds is to ask Postgres.

They must run as ``keenpay_app``. That role is NOBYPASSRLS, so the policies
apply to it. Running them as the migration role or as a superuser would pass
trivially and prove nothing, because table owners bypass RLS by default — a test
that connects as the owner is a test that cannot fail.

Set the DSN explicitly::

    KEENPAY_TEST_DATABASE_URL=postgresql+asyncpg://keenpay_app:pw@localhost:5432/keenpay

The suite skips when no database is reachable so CI stays green without one, but
a skip is not a pass. Run these locally with `docker compose up -d postgres`
before trusting the isolation guarantees.
"""

from __future__ import annotations

import asyncio
import os
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, ProgrammingError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

TEST_DSN = os.getenv(
    "KEENPAY_TEST_DATABASE_URL",
    os.getenv("DATABASE_URL", "postgresql+asyncpg://keenpay_app@localhost:5432/keenpay"),
)

# Seeded by db/seeds/dev_products.sql.
KEEN_SLUG = "merchant_keen"
ACME_SLUG = "merchant_acme"
ACME_CANARY_SKU = "ACME-ANVIL"
KEEN_CAMPAIGN = uuid.UUID("11111111-1111-1111-1111-111111111111")


# -----------------------------------------------------------------------------
# Fixtures
# -----------------------------------------------------------------------------


def _database_unreachable() -> str | None:
    """Probe the test database once, synchronously, at import time.

    Deliberately not done inside the fixture. ``pytest.skip()`` raised from an
    async-generator fixture does not become a skip — pytest-asyncio surfaces it
    as a setup error, so a machine with no Postgres would see this module fail
    rather than skip, and CI would go red for the wrong reason.

    Returns the failure reason, or ``None`` when the database is usable.
    """

    async def probe() -> None:
        eng = create_async_engine(TEST_DSN, connect_args={"timeout": 5})
        try:
            async with eng.connect() as conn:
                await conn.execute(text("SELECT 1 FROM tenants LIMIT 1"))
        finally:
            await eng.dispose()

    try:
        asyncio.run(probe())
    except Exception as exc:  # noqa: BLE001 — any failure means "cannot test here"
        return f"{type(exc).__name__}: {exc}"
    return None


_SKIP_REASON = _database_unreachable()

if _SKIP_REASON is not None:
    pytest.skip(
        f"No migrated test database at {TEST_DSN.split('@')[-1]} ({_SKIP_REASON}). "
        "Run db/init.sql, db/migrations/0001_initial.sql and db/seeds/dev_products.sql, "
        "then set KEENPAY_TEST_DATABASE_URL to a keenpay_app DSN.",
        allow_module_level=True,
    )


@pytest.fixture
async def sessions():
    """Session factory for one test.

    The engine is per-test rather than per-module: the concurrency test needs
    several real connections at once, and a module-scoped engine tied to a
    different event loop is the usual cause of "attached to a different loop"
    failures in async pytest suites.
    """
    eng = create_async_engine(TEST_DSN)
    try:
        yield async_sessionmaker(eng, expire_on_commit=False)
    finally:
        await eng.dispose()


async def _tenant_ids(factory) -> dict[str, uuid.UUID]:
    """Resolve slugs to tenant ids. ``tenants`` is not tenant-scoped."""
    async with factory() as s:
        rows = await s.execute(text("SELECT slug, id FROM tenants"))
        return {slug: tid for slug, tid in rows}


async def _pinned(factory, tenant_id: uuid.UUID):
    """Open a transaction pinned to ``tenant_id``, exactly as the app does."""
    session = factory()
    await session.begin()
    await session.execute(
        text("SELECT set_config('app.tenant_id', :v, true)"), {"v": str(tenant_id)}
    )
    return session


# -----------------------------------------------------------------------------
# Schema posture — catches a future migration that forgets to protect a table
# -----------------------------------------------------------------------------


async def test_every_tenant_table_has_rls_enabled(sessions):
    async with sessions() as s:
        rows = await s.execute(
            text(
                """
                SELECT c.relname
                  FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                 WHERE n.nspname = 'public' AND c.relkind = 'r'
                   AND NOT c.relrowsecurity
                   AND EXISTS (SELECT 1 FROM information_schema.columns
                                WHERE table_schema = 'public'
                                  AND table_name = c.relname
                                  AND column_name = 'tenant_id')
                """
            )
        )
        unprotected = [r[0] for r in rows]
    assert unprotected == [], f"tables carry tenant_id but have no RLS: {unprotected}"


async def test_every_rls_table_has_the_isolation_policy(sessions):
    async with sessions() as s:
        rows = await s.execute(
            text(
                """
                SELECT c.relname
                  FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                 WHERE n.nspname = 'public' AND c.relkind = 'r' AND c.relrowsecurity
                   AND NOT EXISTS (SELECT 1 FROM pg_policies p
                                    WHERE p.schemaname = 'public'
                                      AND p.tablename = c.relname
                                      AND p.policyname = 'tenant_isolation')
                """
            )
        )
        missing = [r[0] for r in rows]
    assert missing == [], f"RLS on but no tenant_isolation policy: {missing}"


async def test_no_keenpay_role_can_bypass_rls(sessions):
    """BYPASSRLS on any KeenPay role would defeat every policy at once."""
    async with sessions() as s:
        rows = await s.execute(
            text("SELECT rolname FROM pg_roles WHERE rolbypassrls AND rolname LIKE 'keenpay%'")
        )
        bypassers = [r[0] for r in rows]
    assert bypassers == [], f"roles can bypass RLS: {bypassers}"


async def test_suite_is_not_running_as_a_privileged_role(sessions):
    """Guard against the test that cannot fail.

    Connected as the table owner or a superuser, every isolation assertion below
    would pass without RLS doing anything at all.
    """
    async with sessions() as s:
        is_super = await s.scalar(text("SELECT usesuper FROM pg_user WHERE usename = current_user"))
        current = await s.scalar(text("SELECT current_user"))
    assert not is_super, f"tests are running as superuser {current!r}; RLS would be bypassed"
    assert current != "keenpay_migration", (
        "tests are running as the schema owner, which bypasses RLS. "
        "Point KEENPAY_TEST_DATABASE_URL at keenpay_app."
    )


# -----------------------------------------------------------------------------
# Read isolation
# -----------------------------------------------------------------------------


async def test_unpinned_session_sees_no_rows(sessions):
    """Fail closed: forgetting to pin returns nothing, never everything."""
    async with sessions() as s:
        count = await s.scalar(text("SELECT count(*) FROM products"))
    assert count == 0, "an unpinned session could read rows — RLS is not fail-closed"


async def test_each_tenant_sees_only_its_own_products(sessions):
    ids = await _tenant_ids(sessions)

    keen = await _pinned(sessions, ids[KEEN_SLUG])
    try:
        rows = await keen.execute(text("SELECT sku FROM products"))
        keen_skus = {r[0] for r in rows}
    finally:
        await keen.rollback()
        await keen.close()

    acme = await _pinned(sessions, ids[ACME_SLUG])
    try:
        rows = await acme.execute(text("SELECT sku FROM products"))
        acme_skus = {r[0] for r in rows}
    finally:
        await acme.rollback()
        await acme.close()

    assert keen_skus, "merchant_keen should see its seeded catalog"
    assert acme_skus, "merchant_acme should see its seeded catalog"
    assert keen_skus & acme_skus == set(), (
        f"tenants share rows: {keen_skus & acme_skus}"
    )
    assert ACME_CANARY_SKU not in keen_skus, (
        "merchant_keen can see merchant_acme's canary product — isolation is broken"
    )


async def test_naming_the_other_tenants_row_directly_still_returns_nothing(sessions):
    """Knowing the primary key is not a way around the policy."""
    ids = await _tenant_ids(sessions)

    # Fetch the id as its owning tenant; an unpinned session cannot see it either.
    acme = await _pinned(sessions, ids[ACME_SLUG])
    try:
        acme_product = await acme.scalar(
            text("SELECT id FROM products WHERE sku = :sku"), {"sku": ACME_CANARY_SKU}
        )
    finally:
        await acme.rollback()
        await acme.close()

    assert acme_product, "seed fixture missing: merchant_acme canary product"

    keen = await _pinned(sessions, ids[KEEN_SLUG])
    try:
        found = await keen.scalar(
            text("SELECT count(*) FROM products WHERE id = :id"), {"id": acme_product}
        )
    finally:
        await keen.rollback()
        await keen.close()

    assert found == 0, "a tenant read another tenant's row by primary key"


async def test_users_do_not_leak_across_tenants(sessions):
    ids = await _tenant_ids(sessions)
    keen = await _pinned(sessions, ids[KEEN_SLUG])
    try:
        rows = await keen.execute(text("SELECT email FROM users"))
        emails = {r[0] for r in rows}
    finally:
        await keen.rollback()
        await keen.close()

    assert emails, "merchant_keen should see its own users"
    assert not any(e.endswith("@acme.test") for e in emails), (
        f"merchant_keen can see Acme users: {emails}"
    )


# -----------------------------------------------------------------------------
# Write isolation
# -----------------------------------------------------------------------------


async def test_cannot_insert_a_row_belonging_to_another_tenant(sessions):
    """The WITH CHECK half of the policy: no smuggling rows across the fence."""
    ids = await _tenant_ids(sessions)
    keen = await _pinned(sessions, ids[KEEN_SLUG])
    try:
        with pytest.raises((ProgrammingError, DBAPIError)) as exc:
            await keen.execute(
                text(
                    """
                    INSERT INTO products
                        (id, tenant_id, merchant_id, sku, name, list_price_paise, cost_paise)
                    VALUES (:id, :tenant_id, 'merchant_acme', :sku, 'Smuggled', 100, 50)
                    """
                ),
                {
                    "id": f"prod_smuggle_{uuid.uuid4().hex[:8]}",
                    "tenant_id": str(ids[ACME_SLUG]),
                    "sku": f"SMUGGLE-{uuid.uuid4().hex[:6]}",
                },
            )
        assert "row-level security" in str(exc.value).lower()
    finally:
        await keen.rollback()
        await keen.close()


async def test_cannot_update_another_tenants_row(sessions):
    """An UPDATE that matches no visible row changes nothing, silently."""
    ids = await _tenant_ids(sessions)

    acme = await _pinned(sessions, ids[ACME_SLUG])
    try:
        before = await acme.scalar(
            text("SELECT list_price_paise FROM products WHERE sku = :sku"),
            {"sku": ACME_CANARY_SKU},
        )
    finally:
        await acme.rollback()
        await acme.close()

    keen = await _pinned(sessions, ids[KEEN_SLUG])
    try:
        result = await keen.execute(
            text("UPDATE products SET list_price_paise = 1 WHERE sku = :sku"),
            {"sku": ACME_CANARY_SKU},
        )
        assert result.rowcount == 0, "a tenant updated another tenant's row"
        await keen.commit()
    finally:
        await keen.close()

    acme = await _pinned(sessions, ids[ACME_SLUG])
    try:
        after = await acme.scalar(
            text("SELECT list_price_paise FROM products WHERE sku = :sku"),
            {"sku": ACME_CANARY_SKU},
        )
    finally:
        await acme.rollback()
        await acme.close()

    assert after == before, f"Acme's price changed from {before} to {after}"


async def test_cannot_delete_another_tenants_row(sessions):
    ids = await _tenant_ids(sessions)
    keen = await _pinned(sessions, ids[KEEN_SLUG])
    try:
        result = await keen.execute(
            text("DELETE FROM products WHERE sku = :sku"), {"sku": ACME_CANARY_SKU}
        )
        assert result.rowcount == 0, "a tenant deleted another tenant's row"
    finally:
        await keen.rollback()
        await keen.close()


# -----------------------------------------------------------------------------
# Repository layer
# -----------------------------------------------------------------------------


async def test_repository_refuses_to_run_unpinned(sessions):
    """An unpinned repository raises instead of quietly returning nothing."""
    from core.rls import TenantNotPinnedError
    from db.repositories import OrderRepository

    async with sessions() as s:
        with pytest.raises(TenantNotPinnedError):
            await OrderRepository(s).tenant_id()


async def test_repository_reads_are_tenant_scoped(sessions):
    from db.repositories import ProductRepository

    ids = await _tenant_ids(sessions)
    keen = await _pinned(sessions, ids[KEEN_SLUG])
    try:
        products = await ProductRepository(keen).list_active()
        skus = {p["sku"] for p in products}
    finally:
        await keen.rollback()
        await keen.close()

    assert skus, "repository returned nothing for a tenant with a seeded catalog"
    assert ACME_CANARY_SKU not in skus


# -----------------------------------------------------------------------------
# atomic_reserve
# -----------------------------------------------------------------------------


async def test_atomic_reserve_allows_spend_within_budget(sessions):
    from db.repositories import CampaignRepository

    ids = await _tenant_ids(sessions)
    keen = await _pinned(sessions, ids[KEEN_SLUG])
    try:
        repo = CampaignRepository(keen)
        before = await repo.get(KEEN_CAMPAIGN)
        reservation = await repo.atomic_reserve(KEEN_CAMPAIGN, 1000)
        assert reservation.reserved == 1000
        after = await repo.get(KEEN_CAMPAIGN)
        assert after["reserved_paise"] == before["reserved_paise"] + 1000
    finally:
        await keen.rollback()  # leave the fixture untouched for other tests
        await keen.close()


async def test_atomic_reserve_refuses_to_exceed_budget(sessions):
    from db.repositories import CampaignRepository, InsufficientBudgetError

    ids = await _tenant_ids(sessions)
    keen = await _pinned(sessions, ids[KEEN_SLUG])
    try:
        repo = CampaignRepository(keen)
        campaign = await repo.get(KEEN_CAMPAIGN)
        over = campaign["budget_paise"] + 1
        with pytest.raises(InsufficientBudgetError):
            await repo.atomic_reserve(KEEN_CAMPAIGN, over)
    finally:
        await keen.rollback()
        await keen.close()


async def test_atomic_reserve_cannot_touch_another_tenants_campaign(sessions):
    """Acme's campaign is not merely filtered out — it does not exist here."""
    from db.repositories import CampaignRepository, InsufficientBudgetError

    ids = await _tenant_ids(sessions)
    acme_campaign = uuid.UUID("22222222-2222-2222-2222-222222222222")

    keen = await _pinned(sessions, ids[KEEN_SLUG])
    try:
        repo = CampaignRepository(keen)
        assert await repo.get(acme_campaign) is None
        with pytest.raises(InsufficientBudgetError, match="No campaign"):
            await repo.atomic_reserve(acme_campaign, 100)
    finally:
        await keen.rollback()
        await keen.close()


async def test_concurrent_reserves_cannot_overspend(sessions):
    """The test that a read-then-write implementation fails.

    Twenty callers race for a budget that fits exactly ten. Each runs on its own
    connection and its own transaction, so they genuinely contend. Exactly ten
    must win: the UPDATE re-checks the budget under a row lock, so the losers
    match zero rows rather than reading a stale balance and writing over it.
    """
    from db.repositories import CampaignRepository, InsufficientBudgetError

    ids = await _tenant_ids(sessions)
    tenant = ids[KEEN_SLUG]

    budget = 10_000
    per_call = 1_000
    callers = 20
    expected_winners = budget // per_call

    # Fixed id, reset rather than recreated. The campaign cannot be deleted
    # afterwards: budget_ledger references it ON DELETE RESTRICT, and the ledger
    # rows themselves are append-only by design. Resetting the counters is the
    # honest way to make this rerunnable — the ledger keeps accumulating, which
    # is exactly what an append-only ledger is for.
    campaign_id = uuid.UUID("33333333-3333-3333-3333-333333333333")
    setup = await _pinned(sessions, tenant)
    try:
        await setup.execute(
            text(
                """
                INSERT INTO campaigns (id, tenant_id, name, budget_paise)
                VALUES (:id, :tenant_id, 'Concurrency fixture', :budget)
                ON CONFLICT (id) DO UPDATE
                   SET reserved_paise = 0, spent_paise = 0, budget_paise = :budget
                """
            ),
            {"id": str(campaign_id), "tenant_id": str(tenant), "budget": budget},
        )
        await setup.commit()
    finally:
        await setup.close()

    async def reserve_once() -> bool:
        session = await _pinned(sessions, tenant)
        try:
            await CampaignRepository(session).atomic_reserve(campaign_id, per_call)
            await session.commit()
            return True
        except InsufficientBudgetError:
            await session.rollback()
            return False
        except DBAPIError:
            # Serialisation or check-constraint failure is also a correct refusal.
            await session.rollback()
            return False
        finally:
            await session.close()

    results = await asyncio.gather(*(reserve_once() for _ in range(callers)))
    winners = sum(results)

    check = await _pinned(sessions, tenant)
    try:
        row = (
            await check.execute(
                text(
                    "SELECT reserved_paise, spent_paise, budget_paise "
                    "FROM campaigns WHERE id = :id"
                ),
                {"id": str(campaign_id)},
            )
        ).mappings().first()
        await check.rollback()
    finally:
        await check.close()

    assert winners == expected_winners, (
        f"{winners} of {callers} reservations succeeded, expected exactly "
        f"{expected_winners} for a {budget} paise budget at {per_call} each"
    )
    assert row["reserved_paise"] + row["spent_paise"] <= row["budget_paise"], (
        "campaign is overspent: "
        f"{row['reserved_paise']} reserved + {row['spent_paise']} spent "
        f"> {row['budget_paise']} budget"
    )
