"""Campaign service: create a budget, and spend against it safely.

The orchestrator that holds the reservation sequence in one place. Routers call
it; it calls the budget arithmetic, the idempotency store and the repository.
Keeping the order here rather than in a handler is what makes "is the key
claimed before the budget moves?" answerable by reading one function.

**Why a reservation rather than a spend.** A discount is decided long before the
order is paid. Committing the money at decision time and never releasing it
would leak the budget on every abandoned checkout; committing it only at payment
time would let a hundred concurrent checkouts each be promised the last rupee.
Reserving takes the money out of circulation immediately and gives it back
explicitly, so the promised total is always what a merchant would owe if every
open checkout completed.

**Why the key is claimed first.** A retried reserve that claimed its key only on
success would, mid-flight, reserve twice - and a duplicated reservation is
budget silently removed from every other order. Claiming first turns the retry
into a conflict. The key is released again on any failure *before* the budget
moved, because nothing happened and a genuine retry deserves to succeed; it is
never released once the movement is recorded.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from core.exceptions import ConflictError, NotFoundError
from core.logging import get_logger
from core.observability import record_event, span
from modules.campaigns.budget import (
    BudgetExceededError,
    BudgetState,
    assert_campaign_spendable,
    assert_releasable,
    assert_reservable,
    normalise_discount_pct,
    validate_budget_paise,
    validate_reservation_paise,
)
from modules.idempotency.service import IdempotencyService, IdempotencyVerdict
from repositories.campaigns import CampaignRepository

logger = get_logger(__name__)

#: Idempotency scope. Distinct from the payment scopes so a key reused across
#: two different kinds of operation collides loudly instead of replaying one
#: operation's stored response for the other.
RESERVE_ENDPOINT = "campaign_reserve"

#: Cap on how many campaigns one merchant may hold. A merchant with thousands is
#: a script in a loop, and every one of them is a budget somebody has to watch.
MAX_CAMPAIGNS_PER_MERCHANT = 200


class CampaignService:
    def __init__(
        self,
        repo: CampaignRepository | None = None,
        idempotency: IdempotencyService | None = None,
    ) -> None:
        self._repo = repo or CampaignRepository()
        self._idempotency = idempotency or IdempotencyService()

    # --- reads --------------------------------------------------------------

    async def list_campaigns(
        self, *, merchant_id: str, active_only: bool = False, limit: int = 50
    ) -> list[dict[str, Any]]:
        return await self._repo.list_for_merchant(
            merchant_id=merchant_id, active_only=active_only, limit=limit
        )

    async def get(self, campaign_id: str, *, merchant_id: str) -> dict[str, Any]:
        campaign = await self._repo.get(campaign_id, merchant_id=merchant_id)
        if campaign is None:
            raise NotFoundError("CAMPAIGN_NOT_FOUND", f"No campaign {campaign_id!r}")
        return campaign

    async def budget(self, campaign_id: str, *, merchant_id: str) -> dict[str, Any]:
        """The three counters and what they imply, for one campaign."""
        campaign = await self.get(campaign_id, merchant_id=merchant_id)
        return {"campaign_id": campaign["id"], **BudgetState.from_row(campaign).to_dict()}

    # --- writes -------------------------------------------------------------

    async def create(
        self,
        *,
        merchant_id: str,
        name: str,
        budget_paise: int,
        code: str | None = None,
        max_discount_pct: Any = None,
        tenant_id: str | None = None,
        starts_at: datetime | None = None,
        ends_at: datetime | None = None,
    ) -> dict[str, Any]:
        """Open a campaign with a fixed budget.

        The budget is set once, here, and no route raises it afterwards. That is
        a deliberate omission: an endpoint that could top up a campaign is an
        endpoint that turns the hard cap into a suggestion, and "increase the
        budget" is a decision that should leave a trail of its own rather than
        being a side effect of a checkout going through.
        """
        budget = validate_budget_paise(budget_paise)
        pct = normalise_discount_pct(max_discount_pct)

        if starts_at and ends_at and ends_at <= starts_at:
            raise ConflictError(
                "INVALID_CAMPAIGN_WINDOW",
                "ends_at must be after starts_at",
                {"starts_at": starts_at.isoformat(), "ends_at": ends_at.isoformat()},
            )

        existing = await self._repo.list_for_merchant(
            merchant_id=merchant_id, limit=MAX_CAMPAIGNS_PER_MERCHANT + 1
        )
        if len(existing) >= MAX_CAMPAIGNS_PER_MERCHANT:
            raise ConflictError(
                "TOO_MANY_CAMPAIGNS",
                f"a merchant may hold at most {MAX_CAMPAIGNS_PER_MERCHANT} campaigns",
                {"max": MAX_CAMPAIGNS_PER_MERCHANT},
            )

        campaign = await self._repo.create(
            merchant_id=merchant_id,
            name=name,
            budget_paise=budget,
            code=code,
            max_discount_pct=pct,
            tenant_id=tenant_id,
            starts_at=starts_at,
            ends_at=ends_at,
        )
        logger.info(
            "campaign_created",
            campaign_id=campaign["id"],
            merchant_id=merchant_id,
            budget_paise=budget,
        )
        record_event("campaign_created")
        return campaign

    async def reserve(
        self,
        campaign_id: str,
        *,
        merchant_id: str,
        amount_paise: int,
        idempotency_key: str,
        order_id: str | None = None,
        reason: str | None = None,
        request_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Take money out of circulation, or refuse.

        Ordering, and every step is load-bearing:

        1. claim the idempotency key, so a retry cannot reserve twice
        2. read the campaign - absent or another merchant's is a 404
        3. refuse an inactive, unstarted or finished campaign
        4. refuse an amount that is not a positive integer number of paise
        5. reserve atomically; a refusal here means someone else got there first
        """
        body = request_body or {
            "campaign_id": str(campaign_id),
            "amount_paise": amount_paise,
            "order_id": order_id,
        }
        verdict = await self._idempotency.claim(
            merchant_id, RESERVE_ENDPOINT, idempotency_key, body
        )

        if verdict is IdempotencyVerdict.REPLAY:
            replayed = await self._idempotency.replay_response(
                merchant_id, RESERVE_ENDPOINT, idempotency_key
            )
            if replayed is None:
                # The key is spent but its response is gone. Falling through to
                # reserve again is the one thing that must not happen here, so
                # this refuses rather than guessing.
                raise ConflictError(
                    "RESERVE_ALREADY_APPLIED",
                    "this idempotency key has already been used",
                    {"idempotency_key": idempotency_key},
                )
            record_event("campaign_reserve_replayed")
            return replayed[1]
        if verdict is IdempotencyVerdict.IN_PROGRESS:
            raise ConflictError(
                "RESERVE_IN_PROGRESS",
                "another request is already using this idempotency key",
                {"idempotency_key": idempotency_key},
            )
        if verdict is IdempotencyVerdict.CONFLICT:
            raise ConflictError(
                "IDEMPOTENCY_KEY_REUSED",
                "this idempotency key was used for a different request",
                {"idempotency_key": idempotency_key},
            )

        with span("campaign.reserve", campaign_id=str(campaign_id)):
            try:
                campaign = await self.get(campaign_id, merchant_id=merchant_id)
                assert_campaign_spendable(campaign)
                amount = assert_reservable(
                    BudgetState.from_row(campaign), amount_paise, campaign_id=str(campaign_id)
                )

                updated = await self._repo.reserve(
                    campaign_id,
                    merchant_id=merchant_id,
                    amount_paise=amount,
                    order_id=order_id,
                    reason=reason,
                )
            except Exception:
                # Nothing moved, so the key is freed and an honest retry works.
                # This is safe only because no external provider is involved: a
                # reservation is entirely ours, and a failure here means it did
                # not happen rather than that its outcome is unknown.
                await self._idempotency.release(
                    merchant_id, RESERVE_ENDPOINT, idempotency_key
                )
                raise

            if updated is None:
                # The pre-check passed and the write still refused: another
                # request took the headroom in between. This is the path the
                # concurrency guarantee is made of, so it is reported as a
                # budget refusal rather than as a server error.
                await self._idempotency.release(
                    merchant_id, RESERVE_ENDPOINT, idempotency_key
                )
                fresh = await self.get(campaign_id, merchant_id=merchant_id)
                state = BudgetState.from_row(fresh)
                record_event("campaign_reserve_refused")
                raise BudgetExceededError(
                    "BUDGET_EXCEEDED",
                    f"campaign has {state.remaining_paise} paise remaining, "
                    f"cannot reserve {amount}",
                    {
                        "campaign_id": str(campaign_id),
                        "requested_paise": amount,
                        **state.to_dict(),
                    },
                )

            response = {
                "campaign_id": updated["id"],
                "reserved_paise": amount,
                "order_id": order_id,
                "budget": {
                    "campaign_id": updated["id"],
                    **BudgetState.from_row(updated).to_dict(),
                },
            }
            await self._idempotency.complete(
                merchant_id, RESERVE_ENDPOINT, idempotency_key, 200, response
            )
            logger.info(
                "campaign_budget_reserved",
                campaign_id=updated["id"],
                amount_paise=amount,
                remaining_paise=response["budget"]["remaining_paise"],
            )
            record_event("campaign_budget_reserved")
            return response

    async def release(
        self,
        campaign_id: str,
        *,
        merchant_id: str,
        amount_paise: int,
        order_id: str | None = None,
        reason: str | None = None,
    ) -> dict[str, Any]:
        """Put a reservation back.

        Not idempotent by key, and it should not be: releasing is the correction
        for a checkout that did not happen, and a stored replay would hide a
        second, genuine release of a second, genuine abandonment. The guard that
        matters is the arithmetic one - you cannot release more than is reserved -
        and it is enforced twice, here and in the repository's WHERE clause.
        """
        campaign = await self.get(campaign_id, merchant_id=merchant_id)
        amount = assert_releasable(
            BudgetState.from_row(campaign), amount_paise, campaign_id=str(campaign_id)
        )

        updated = await self._repo.release(
            campaign_id,
            merchant_id=merchant_id,
            amount_paise=amount,
            order_id=order_id,
            reason=reason,
        )
        if updated is None:
            fresh = await self.get(campaign_id, merchant_id=merchant_id)
            raise ConflictError(
                "RELEASE_EXCEEDS_RESERVED",
                f"cannot release {amount} paise: only "
                f"{BudgetState.from_row(fresh).reserved_paise} is reserved",
                {"campaign_id": str(campaign_id), "requested_paise": amount},
            )

        record_event("campaign_budget_released")
        return {
            "campaign_id": updated["id"],
            "released_paise": amount,
            "order_id": order_id,
            "budget": {
                "campaign_id": updated["id"],
                **BudgetState.from_row(updated).to_dict(),
            },
        }

    async def settle(
        self,
        campaign_id: str,
        *,
        merchant_id: str,
        amount_paise: int,
        order_id: str | None = None,
    ) -> dict[str, Any]:
        """Turn a reservation into spend once the order is paid.

        No route calls this yet - settlement belongs on the payment-settled path,
        not on a button. It lives here so that the counter that must move when
        money actually leaves is defined alongside the one that holds it, and so
        that the ledger has a spend entry to record rather than an eternally
        reserved balance.
        """
        campaign = await self.get(campaign_id, merchant_id=merchant_id)
        amount = assert_releasable(
            BudgetState.from_row(campaign), amount_paise, campaign_id=str(campaign_id)
        )
        updated = await self._repo.settle(
            campaign_id, merchant_id=merchant_id, amount_paise=amount, order_id=order_id
        )
        if updated is None:  # pragma: no cover - assert_releasable already proved it fits
            raise ConflictError(
                "SETTLE_EXCEEDS_RESERVED",
                f"cannot settle {amount} paise against this campaign",
                {"campaign_id": str(campaign_id), "requested_paise": amount},
            )
        record_event("campaign_budget_settled")
        return {
            "campaign_id": updated["id"],
            "settled_paise": amount,
            "order_id": order_id,
            "budget": {
                "campaign_id": updated["id"],
                **BudgetState.from_row(updated).to_dict(),
            },
        }

    @staticmethod
    def validate_amount(amount_paise: Any) -> int:
        """Exposed so callers outside HTTP get the same refusals."""
        return validate_reservation_paise(amount_paise)


__all__ = ["MAX_CAMPAIGNS_PER_MERCHANT", "RESERVE_ENDPOINT", "CampaignService"]
