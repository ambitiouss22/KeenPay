"""The ledger's whole value is that editing it is detectable.

These tests attack the chain the way someone covering up a fraudulent payment
would: change an amount, drop the inconvenient entry, reorder two entries,
splice in a new one. Each attack must be caught, and caught with an error that
says where.
"""

from __future__ import annotations

import pytest

from modules.audit.ledger import (
    GENESIS_HASH,
    AuditLedger,
    compute_entry_hash,
    reset_ledger,
    verify_exported_chain,
)

pytestmark = pytest.mark.asyncio

MERCHANT = "merchant_keen"
OTHER = "merchant_rival"


@pytest.fixture
def ledger() -> AuditLedger:
    reset_ledger()
    return AuditLedger()


async def _seed(ledger: AuditLedger, count: int = 3, merchant: str = MERCHANT):
    for index in range(count):
        await ledger.append(
            merchant_id=merchant,
            entity_type="payment",
            entity_id="pay_1",
            actor="payment_engine",
            action=f"STEP_{index}",
            payload={"index": index, "amount_paise": 10000 + index},
        )


# --- shape ------------------------------------------------------------------


async def test_first_entry_points_at_genesis(ledger):
    entry = await ledger.append(
        merchant_id=MERCHANT,
        entity_type="payment",
        entity_id="pay_1",
        actor="payment_engine",
        action="PAYMENT_CREATED",
        payload={},
    )
    assert entry.seq == 1
    assert entry.prev_hash == GENESIS_HASH


async def test_each_entry_points_at_the_one_before(ledger):
    await _seed(ledger, 4)
    chain = await ledger.chain(MERCHANT)

    assert [e.seq for e in chain] == [1, 2, 3, 4]
    for index in range(1, len(chain)):
        assert chain[index].prev_hash == chain[index - 1].entry_hash


async def test_head_is_the_last_entry_hash(ledger):
    await _seed(ledger, 3)
    chain = await ledger.chain(MERCHANT)
    assert await ledger.head(MERCHANT) == chain[-1].entry_hash


async def test_head_of_an_empty_chain_is_genesis(ledger):
    assert await ledger.head("merchant_with_no_history") == GENESIS_HASH


async def test_chains_are_isolated_per_merchant(ledger):
    """One merchant's activity must not appear in, or shift, another's chain."""
    await _seed(ledger, 2, merchant=MERCHANT)
    await _seed(ledger, 2, merchant=OTHER)

    ours = await ledger.chain(MERCHANT)
    theirs = await ledger.chain(OTHER)

    assert [e.seq for e in ours] == [1, 2]
    assert [e.seq for e in theirs] == [1, 2]
    assert {e.merchant_id for e in ours} == {MERCHANT}
    assert await ledger.head(MERCHANT) != await ledger.head(OTHER)


# --- verification -----------------------------------------------------------


async def test_an_untouched_chain_verifies(ledger):
    await _seed(ledger, 5)
    result = await ledger.verify(MERCHANT)
    assert result.valid
    assert result.errors == []
    assert result.entry_count == 5


async def test_an_empty_chain_verifies(ledger):
    result = await ledger.verify("merchant_with_no_history")
    assert result.valid
    assert result.entry_count == 0
    assert result.head_hash == GENESIS_HASH


async def test_editing_a_payload_is_detected(ledger):
    """The attack this whole module exists to stop: changing an amount."""
    await _seed(ledger, 3)
    chain = await ledger.chain(MERCHANT)
    original_hash = chain[1].entry_hash

    from modules.audit import ledger as ledger_module

    ledger_module._CHAINS[MERCHANT][1]["payload"]["amount_paise"] = 1

    result = await ledger.verify(MERCHANT)
    assert not result.valid
    assert any("do not match their hash" in error for error in result.errors)
    # The stored hash is untouched; it is the contents that no longer produce it.
    assert ledger_module._CHAINS[MERCHANT][1]["entry_hash"] == original_hash


async def test_rewriting_a_payload_and_its_hash_still_breaks_the_chain(ledger):
    """A thorough forger recomputes the hash. The link to the next entry gives it away."""
    await _seed(ledger, 3)
    from modules.audit import ledger as ledger_module

    target = ledger_module._CHAINS[MERCHANT][1]
    target["payload"] = {"index": 1, "amount_paise": 1}
    body = {key: value for key, value in target.items() if key != "entry_hash"}
    body["recorded_at"] = target["recorded_at"].isoformat()
    target["entry_hash"] = compute_entry_hash(body)

    result = await ledger.verify(MERCHANT)
    assert not result.valid
    assert any("chain is broken here" in error for error in result.errors)


async def test_deleting_an_entry_is_detected(ledger):
    await _seed(ledger, 4)
    from modules.audit import ledger as ledger_module

    del ledger_module._CHAINS[MERCHANT][2]

    result = await ledger.verify(MERCHANT)
    assert not result.valid
    assert any("seq is" in error for error in result.errors)


async def test_reordering_entries_is_detected(ledger):
    await _seed(ledger, 4)
    from modules.audit import ledger as ledger_module

    chain = ledger_module._CHAINS[MERCHANT]
    chain[1], chain[2] = chain[2], chain[1]

    result = await ledger.verify(MERCHANT)
    assert not result.valid


async def test_appending_a_forged_entry_is_detected(ledger):
    """An entry appended out of band cannot know the real previous hash."""
    await _seed(ledger, 2)
    from modules.audit import ledger as ledger_module

    forged = dict(ledger_module._CHAINS[MERCHANT][-1])
    forged["seq"] = 3
    forged["action"] = "PAYMENT_REFUNDED"
    forged["prev_hash"] = GENESIS_HASH
    ledger_module._CHAINS[MERCHANT].append(forged)

    result = await ledger.verify(MERCHANT)
    assert not result.valid


async def test_verify_reports_every_break_not_only_the_first(ledger):
    await _seed(ledger, 5)
    from modules.audit import ledger as ledger_module

    ledger_module._CHAINS[MERCHANT][1]["payload"] = {"tampered": True}
    ledger_module._CHAINS[MERCHANT][3]["payload"] = {"tampered": True}

    result = await ledger.verify(MERCHANT)
    hash_errors = [e for e in result.errors if "do not match their hash" in e]
    assert len(hash_errors) == 2


# --- offline verification ---------------------------------------------------


async def test_an_exported_chain_verifies_without_the_ledger(ledger):
    await _seed(ledger, 3)
    exported = [entry.to_dict() for entry in await ledger.chain(MERCHANT)]

    valid, errors = verify_exported_chain(exported)
    assert valid, errors


async def test_a_tampered_export_fails_offline_verification(ledger):
    await _seed(ledger, 3)
    exported = [entry.to_dict() for entry in await ledger.chain(MERCHANT)]
    exported[1]["payload"]["amount_paise"] = 1

    valid, errors = verify_exported_chain(exported)
    assert not valid
    assert errors


async def test_an_empty_export_verifies():
    valid, errors = verify_exported_chain([])
    assert valid
    assert errors == []


# --- querying ---------------------------------------------------------------


async def test_entries_can_be_filtered_by_entity(ledger):
    await ledger.append(
        merchant_id=MERCHANT,
        entity_type="payment",
        entity_id="pay_1",
        actor="payment_engine",
        action="PAYMENT_CREATED",
        payload={},
    )
    await ledger.append(
        merchant_id=MERCHANT,
        entity_type="order",
        entity_id="ord_1",
        actor="webhook",
        action="PAYMENT_CAPTURED",
        payload={},
    )

    entries, total = await ledger.entries_for(
        MERCHANT, entity_type="payment", entity_id="pay_1"
    )
    assert total == 1
    assert entries[0].action == "PAYMENT_CREATED"


async def test_filtering_paginates_over_the_filtered_set(ledger):
    await _seed(ledger, 5)
    entries, total = await ledger.entries_for(MERCHANT, limit=2, offset=2)
    assert total == 5
    assert [e.seq for e in entries] == [3, 4]


async def test_entries_for_another_merchant_are_never_returned(ledger):
    await _seed(ledger, 2, merchant=OTHER)
    entries, total = await ledger.entries_for(MERCHANT)
    assert total == 0
    assert entries == []
