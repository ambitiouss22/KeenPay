"""Authorizations: request, read, approve.

Three routes, and the split between them is the separation of duties that
Phase 5 exists to enforce.

``POST /authorizations``          asks for permission to move money
``GET  /authorizations/{id}``     reads the record and its approval state
``POST /authorizations/{id}/approve``  adds one human's blessing

Each carries a different permission, so the party that asks is not necessarily
the party that approves, and an investigator can read everything while
approving nothing. The service enforces the rest - no self-approval, no
approver counted twice - because a rule that lived only in a route would not
apply to the background jobs that call the same service in later phases.

**On status codes.** ``POST /authorizations`` answers 201 even when policy
denied the action. The record was created; the verdict is in its ``status``
field. A denial is not a malformed request, and answering 4xx would conflate
"you asked wrongly" with "the answer is no" - two things a client must handle
differently. Nothing depends on the client reading the code correctly, because
the gate is ``consume``, not the HTTP response.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends

from core.exceptions import ValidationError
from core.rbac import Permission
from dependencies.auth import CurrentUser, require_perm
from modules.authorization.service import AuthorizationService
from modules.refunds.guard import RefundGuard
from policy.models import ActionKind, FinancialAction
from repositories.orders import OrderRepository
from routers.policy import build_action
from schemas.policy import ActionRequest, AuthorizationApproveRequest, AuthorizationOut

router = APIRouter(prefix="/api/v1/authorizations", tags=["authorizations"])


def get_authorization_service() -> AuthorizationService:
    return AuthorizationService()


ServiceDep = Annotated[AuthorizationService, Depends(get_authorization_service)]


async def _refund_action(
    body: ActionRequest, *, merchant_id: str, actor_id: str, role: str
) -> FinancialAction:
    """Build a refund action from the *order*, not from the request body.

    The refund guard runs first, and an ineligible refund never reaches the
    authorization gate at all. That ordering is deliberate: an authorization
    record for a refund that was never going to be permitted is a pending
    approval sitting in somebody's queue, and approving it would achieve
    nothing except to teach approvers that the queue contains noise.

    Captured and already-refunded amounts are read off the stored order. They
    are the two numbers the refund ceiling is computed from, so a caller able
    to supply them could name a larger capture and refund against it.
    """
    order = await OrderRepository().get(body.subject_id)
    verdict = RefundGuard().evaluate(
        order=order, merchant_id=merchant_id, amount_paise=body.amount_paise
    )
    if not verdict.eligible:
        raise ValidationError(
            "REFUND_NOT_ELIGIBLE",
            "; ".join(verdict.reasons) or "refund is not eligible",
            verdict.to_dict(),
        )

    assert order is not None  # noqa: S101 - eligibility already proved it exists
    return RefundGuard.to_action(
        order=order,
        merchant_id=merchant_id,
        amount_paise=body.amount_paise,
        actor_id=actor_id,
        actor_role=role,
        actions_last_hour=body.context.actions_last_hour,
        today_total_paise=body.context.today_total_paise,
    )


@router.post(
    "",
    response_model=AuthorizationOut,
    status_code=201,
    dependencies=[Depends(require_perm(Permission.AUTHORIZATION_REQUEST))],
)
async def create_authorization(
    body: ActionRequest, principal: CurrentUser, service: ServiceDep
) -> Any:
    """Run Policy -> Risk -> Authorization and record the outcome.

    The returned ``status`` is one of:

    ``approved``  low risk and every rule passed; nothing else is needed
    ``pending``   ``required_approvals`` humans must still say yes
    ``denied``    a rule was broken; no approval can rescue it
    """
    if body.kind == ActionKind.REFUND.value:
        action = await _refund_action(
            body,
            merchant_id=principal.merchant_id,
            actor_id=principal.user_id,
            role=principal.role,
        )
    else:
        action = build_action(
            body,
            merchant_id=principal.merchant_id,
            actor_id=principal.user_id,
            role=principal.role,
        )

    return await service.request(action, tenant_id=principal.tenant_id)


@router.get(
    "/{authorization_id}",
    response_model=AuthorizationOut,
    dependencies=[Depends(require_perm(Permission.AUTHORIZATION_READ))],
)
async def get_authorization(
    authorization_id: str, principal: CurrentUser, service: ServiceDep
) -> Any:
    """Read one record, scoped to the caller's merchant.

    A record belonging to another merchant answers 404, the same as one that
    does not exist. Distinguishing them would let an attacker confirm which
    authorization ids are real by reading the status code.
    """
    return await service.get(authorization_id, merchant_id=principal.merchant_id)


@router.post(
    "/{authorization_id}/approve",
    response_model=AuthorizationOut,
    dependencies=[Depends(require_perm(Permission.AUTHORIZATION_APPROVE))],
)
async def approve_authorization(
    authorization_id: str,
    body: AuthorizationApproveRequest,
    principal: CurrentUser,
    service: ServiceDep,
) -> Any:
    """Add this caller's approval.

    The approver is the token holder. There is no field to name someone else,
    which is what makes the four-eyes rule enforceable rather than advisory:
    an approval can only ever be attributed to whoever presented the
    credential.

    Returns the record with the approval appended. When that approval completes
    the quorum, the status is ``approved`` and the authorization becomes
    spendable - once.
    """
    return await service.approve(
        authorization_id,
        merchant_id=principal.merchant_id,
        approver_id=principal.user_id,
        approver_role=principal.role,
    )
