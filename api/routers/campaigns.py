"""Campaigns and their budget.

Four routes, split across two permissions. Reading a budget and spending it are
different acts, and the split is what lets a merchant give someone visibility
into growth without giving them the ability to commit money.

``POST /campaigns``                    opens a campaign with a fixed cap
``GET  /campaigns``                    lists this merchant's campaigns
``GET  /campaigns/{id}/budget``        the three counters
``POST /campaigns/{id}/reserve``       takes budget out of circulation
``POST /campaigns/{id}/release``       puts an unused reservation back

``merchant_id`` always comes from the verified token. No route accepts it, so a
caller cannot name another merchant's campaign - and a campaign it does not own
answers 404 rather than 403, because a 403 on a real id confirms the id is real.

Release exists because reserve does. A cap that only ever ratchets downward is
not a budget: every abandoned checkout would permanently shrink it, and the
merchant's only recovery would be to open a new campaign.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from core.rbac import Permission
from dependencies.auth import CurrentUser, require_perm
from modules.campaigns.service import CampaignService
from schemas.campaigns import (
    BudgetMoveRequest,
    BudgetOut,
    CampaignCreateRequest,
    CampaignListResponse,
    CampaignOut,
    ReleaseOut,
    ReserveOut,
    ReserveRequest,
)

router = APIRouter(prefix="/api/v1/campaigns", tags=["campaigns"])


def get_campaign_service() -> CampaignService:
    return CampaignService()


ServiceDep = Annotated[CampaignService, Depends(get_campaign_service)]


@router.post(
    "",
    response_model=CampaignOut,
    status_code=201,
    dependencies=[Depends(require_perm(Permission.GROWTH_MANAGE))],
)
async def create_campaign(
    body: CampaignCreateRequest, principal: CurrentUser, svc: ServiceDep
) -> CampaignOut:
    """Open a campaign. The budget set here is the cap, and it is not editable."""
    campaign = await svc.create(
        merchant_id=principal.merchant_id,
        tenant_id=principal.tenant_id,
        name=body.name,
        budget_paise=body.budget_paise,
        code=body.code,
        max_discount_pct=body.max_discount_pct,
        starts_at=body.starts_at,
        ends_at=body.ends_at,
    )
    return CampaignOut(**campaign)


@router.get(
    "",
    response_model=CampaignListResponse,
    dependencies=[Depends(require_perm(Permission.GROWTH_READ))],
)
async def list_campaigns(
    principal: CurrentUser,
    svc: ServiceDep,
    active_only: bool = Query(default=False),
    limit: int = Query(default=50, ge=1, le=200),
) -> CampaignListResponse:
    items = await svc.list_campaigns(
        merchant_id=principal.merchant_id, active_only=active_only, limit=limit
    )
    return CampaignListResponse(
        items=[CampaignOut(**c) for c in items], total=len(items)
    )


@router.get(
    "/{campaign_id}/budget",
    response_model=BudgetOut,
    dependencies=[Depends(require_perm(Permission.GROWTH_READ))],
)
async def get_budget(
    campaign_id: str, principal: CurrentUser, svc: ServiceDep
) -> BudgetOut:
    """What is left, what is promised, what is gone."""
    return BudgetOut(**await svc.budget(campaign_id, merchant_id=principal.merchant_id))


@router.post(
    "/{campaign_id}/reserve",
    response_model=ReserveOut,
    dependencies=[Depends(require_perm(Permission.GROWTH_MANAGE))],
)
async def reserve_budget(
    campaign_id: str,
    body: ReserveRequest,
    principal: CurrentUser,
    svc: ServiceDep,
) -> ReserveOut:
    """Take budget out of circulation for one order.

    Answers 409 when the campaign cannot fund it, whether because the money has
    run out or because someone else reserved it first. Both are the same fact
    from the caller's side: there is nothing left to promise.

    The idempotency key is required. Retrying with the same key returns the
    original reservation rather than making a second one.
    """
    result = await svc.reserve(
        campaign_id,
        merchant_id=principal.merchant_id,
        amount_paise=body.amount_paise,
        idempotency_key=body.idempotency_key,
        order_id=body.order_id,
        reason=body.reason,
        request_body=body.model_dump(),
    )
    return ReserveOut(**result)


@router.post(
    "/{campaign_id}/release",
    response_model=ReleaseOut,
    dependencies=[Depends(require_perm(Permission.GROWTH_MANAGE))],
)
async def release_budget(
    campaign_id: str,
    body: BudgetMoveRequest,
    principal: CurrentUser,
    svc: ServiceDep,
) -> ReleaseOut:
    """Return an unused reservation to the pool.

    Refuses to release more than is currently reserved, which is what stops a
    release from manufacturing budget that was never funded.
    """
    result = await svc.release(
        campaign_id,
        merchant_id=principal.merchant_id,
        amount_paise=body.amount_paise,
        order_id=body.order_id,
        reason=body.reason,
    )
    return ReleaseOut(**result)
