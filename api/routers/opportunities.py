"""Growth opportunities.

``POST /opportunities/generate``  runs the rules and stores the result
``GET  /opportunities``           reads what has been stored

Generating writes rows, so it carries the manage permission; reading carries the
read one. The asymmetry is worth the extra permission because generation is the
route an automated caller hits repeatedly, and it is the one that can be made to
do work on request.

Nothing here can move money. The most an opportunity says is "consider this
sku"; funding a discount on it means reserving campaign budget, which is a
different route, a different permission and a hard cap.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from core.rbac import Permission
from dependencies.auth import CurrentUser, require_perm
from modules.opportunities.service import OpportunityService
from schemas.opportunities import (
    OpportunityGenerateRequest,
    OpportunityGenerateResponse,
    OpportunityKind,
    OpportunityListResponse,
    OpportunityOut,
    RejectedHint,
)

router = APIRouter(prefix="/api/v1/opportunities", tags=["opportunities"])


def get_opportunity_service() -> OpportunityService:
    return OpportunityService()


ServiceDep = Annotated[OpportunityService, Depends(get_opportunity_service)]


@router.post(
    "/generate",
    response_model=OpportunityGenerateResponse,
    status_code=201,
    dependencies=[Depends(require_perm(Permission.GROWTH_MANAGE))],
)
async def generate_opportunities(
    body: OpportunityGenerateRequest, principal: CurrentUser, svc: ServiceDep
) -> OpportunityGenerateResponse:
    """Produce suggestions for this merchant and persist them.

    Calling it twice for the same subject returns the same rows: ids are derived
    from what a suggestion means, so a re-run stores nothing new rather than
    filling the list with copies.

    Any ``recommendations`` in the body are hints. Each is put through the same
    rules as everything else and dropped, with a reason, if those rules would not
    have produced it.
    """
    result = await svc.generate(
        merchant_id=principal.merchant_id,
        user_id=principal.user_id,
        tenant_id=principal.tenant_id,
        cart_id=body.cart_id,
        kinds=list(body.kinds) if body.kinds else None,
        max_suggestions=body.max_suggestions,
        recommendations=[hint.model_dump() for hint in body.recommendations],
    )
    return OpportunityGenerateResponse(
        subject_id=result["subject_id"],
        items=[OpportunityOut(**item) for item in result["items"]],
        generated=result["generated"],
        rejected=[RejectedHint(**r) for r in result["rejected"]],
    )


@router.get(
    "",
    response_model=OpportunityListResponse,
    dependencies=[Depends(require_perm(Permission.GROWTH_READ))],
)
async def list_opportunities(
    principal: CurrentUser,
    svc: ServiceDep,
    kind: OpportunityKind | None = Query(default=None),
    acted_on: bool | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> OpportunityListResponse:
    items, total = await svc.list_opportunities(
        merchant_id=principal.merchant_id,
        kind=kind,
        acted_on=acted_on,
        limit=limit,
        offset=offset,
    )
    return OpportunityListResponse(
        items=[OpportunityOut(**i) for i in items],
        total=total,
        limit=limit,
        offset=offset,
    )
