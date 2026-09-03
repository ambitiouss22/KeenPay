"""Policy evaluation, as a dry run.

One endpoint, and it deliberately creates nothing. ``POST /policy/evaluate``
answers "what would the gate say?" - which rules fire, what the risk score
comes to, how many approvals it would need - without opening an authorization
or moving anything.

Two audiences. An operator console that wants to warn "this refund will need
two approvals" before the operator commits to asking. And whoever is changing
merchant policy, who needs to replay real traffic shapes against a new limit
before it goes live, which is otherwise done by deploying it and watching what
breaks.

Access is limited on purpose. The response enumerates a merchant's limits -
ceilings, escalation thresholds, daily caps - so an unrestricted evaluate
endpoint is a binary-search oracle for finding the largest amount that slips
through unattended. ``POLICY_EVALUATE`` is held by manager, admin and service
accounts, and never by a shopper.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from config.policy import load_merchant_policy
from core.rbac import Permission
from dependencies.auth import CurrentUser, require_perm
from modules.authorization.service import AuthorizationService
from modules.risk.service import RiskService
from policy.engine import PolicyEngine
from policy.models import ActionKind, FinancialAction
from schemas.policy import ActionRequest, PolicyEvaluateResponse

router = APIRouter(prefix="/api/v1/policy", tags=["policy"])


def build_action(body: ActionRequest, *, merchant_id: str, actor_id: str, role: str):
    """Assemble the action from the body plus the *token*.

    Merchant and actor come from the verified principal, never from the
    request. This function is the single place that mapping happens, so a new
    route cannot accidentally take either from a body field.
    """
    ctx = body.context
    return FinancialAction(
        kind=ActionKind(body.kind),
        merchant_id=merchant_id,
        amount_paise=body.amount_paise,
        currency=body.currency,
        subject_id=body.subject_id,
        actor_id=actor_id,
        actor_role=role,
        today_total_paise=ctx.today_total_paise,
        actions_last_hour=ctx.actions_last_hour,
        captured_paise=ctx.captured_paise,
        already_refunded_paise=ctx.already_refunded_paise,
        buyer_age_days=ctx.buyer_age_days,
        buyer_prior_orders=ctx.buyer_prior_orders,
        buyer_country=ctx.buyer_country.upper(),
        ip_country=ctx.ip_country.upper(),
    )


@router.post(
    "/evaluate",
    response_model=PolicyEvaluateResponse,
    dependencies=[Depends(require_perm(Permission.POLICY_EVALUATE))],
)
async def evaluate(body: ActionRequest, principal: CurrentUser) -> PolicyEvaluateResponse:
    """Evaluate an action without creating an authorization.

    Answers 200 whatever the verdict, including a denial. The verdict is the
    payload, not the status: a caller asking "what would happen?" got a
    complete answer, and returning 4xx for "policy would refuse this" would
    make a successful query indistinguishable from a malformed one.
    """
    action = build_action(
        body,
        merchant_id=principal.merchant_id,
        actor_id=principal.user_id,
        role=principal.role,
    )

    decision = PolicyEngine().evaluate_action(action)
    if decision.denied:
        # Mirrors the real path exactly: on a denial the scorer is not run, so
        # the dry run must not report a score the live gate would never have
        # computed.
        return PolicyEvaluateResponse(
            decision=decision.model_dump(mode="json"),
            risk=None,
            required_approvals=0,
            would_auto_approve=False,
        )

    assessment = RiskService().assess(action)
    required = AuthorizationService.required_approvals(
        decision, assessment, load_merchant_policy(principal.merchant_id)
    )
    return PolicyEvaluateResponse(
        decision=decision.model_dump(mode="json"),
        risk=assessment.to_dict(),
        required_approvals=required,
        would_auto_approve=required == 0,
    )
