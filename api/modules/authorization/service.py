"""The authorization gate: Policy -> Risk -> Authorization, in that order.

This is the module Phase 5 exists for. Nothing moves money without passing
through :meth:`AuthorizationService.consume`, and ``consume`` succeeds only for
a record that policy allowed, risk sized, and - where required - humans
approved.

The order is not arbitrary:

1. **Policy** answers the categorical question. A denial ends it. Risk is not
   consulted, because no score excuses a broken rule, and running the scorer
   anyway would invite someone to later write ``if risk is low: proceed``.
2. **Risk** sizes the graded question, and its only output is *how many people
   must say yes*. It cannot allow and it cannot refuse.
3. **Authorization** records the verdict, collects approvals, and is spent
   exactly once against exactly one action.

Four properties this module is written to guarantee, each with an adversarial
test behind it:

``no self-approval``      the requester may never approve their own request
``no double-counting``    one approver cannot fill a two-person quorum alone
``single use``            an approval is spent once, then never again
``scope binding``         an approval is spent only on the action it was for

The last one is the subtle one. Without it, an attacker requests authorization
for a ₹10 refund, gets it approved, and then presents that authorization while
charging ₹10,00,000. Binding the approval to a fingerprint of the action's
identifying and financial fields is what makes the approval mean *this* money
movement rather than *a* money movement.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from config.policy import MerchantPolicy, load_merchant_policy
from core.exceptions import AuthorizationError, ConflictError, NotFoundError
from core.logging import get_logger
from core.observability import record_event, span
from core.rbac import Permission, has_permission
from modules.risk.service import RiskAssessment, RiskBand, RiskService
from policy.engine import PolicyEngine
from policy.models import FinancialAction, PolicyDecision, PolicyOutcome
from repositories.authorizations import AuthorizationRepository

logger = get_logger(__name__)

#: Statuses from which nothing further can happen.
_TERMINAL = frozenset({"denied", "consumed", "expired", "revoked"})


class AuthorizationService:
    """Creates, approves and spends authorizations for financial actions."""

    def __init__(
        self,
        *,
        policy_engine: PolicyEngine | None = None,
        risk: RiskService | None = None,
        repo: AuthorizationRepository | None = None,
    ) -> None:
        self._policy = policy_engine or PolicyEngine()
        self._risk = risk or RiskService()
        self._repo = repo or AuthorizationRepository()

    # --- 1. request ---------------------------------------------------------

    async def request(
        self,
        action: FinancialAction,
        *,
        tenant_id: str | None = None,
        policy: MerchantPolicy | None = None,
    ) -> dict[str, Any]:
        """Run the gates and open an authorization record.

        Always produces a record, including for a denial. A refusal that left
        no trace would be invisible to anyone investigating why a merchant's
        payment failed, and the record is the only proof the gate ran at all.
        A denied record is created already terminal: there is no state it can
        move to, so no later call can rescue it.
        """
        with span("authorization.request", kind=action.kind.value):
            policy = policy or load_merchant_policy(action.merchant_id)

            decision = self._policy.evaluate_action(action, policy=policy)

            if decision.denied:
                # Risk is deliberately not scored. A denial is categorical, and
                # attaching a score to it would invite a future reader to weigh
                # the two against each other.
                record = await self._repo.create(
                    **self._base_fields(action, tenant_id, decision, policy),
                    risk={},
                    status="denied",
                    required_approvals=0,
                    reasons=decision.reasons,
                )
                logger.warning(
                    "authorization_denied",
                    authorization_id=record["id"],
                    merchant_id=action.merchant_id,
                    kind=action.kind.value,
                    amount_paise=action.amount_paise,
                    reasons=decision.reasons,
                )
                record_event("authorization_denied")
                return record

            assessment = self._risk.assess(action, policy=policy)
            required = self.required_approvals(decision, assessment, policy)
            status = "approved" if required == 0 else "pending"

            record = await self._repo.create(
                **self._base_fields(action, tenant_id, decision, policy),
                risk=assessment.to_dict(),
                status=status,
                required_approvals=required,
                reasons=decision.reasons,
            )
            logger.info(
                "authorization_requested",
                authorization_id=record["id"],
                merchant_id=action.merchant_id,
                kind=action.kind.value,
                amount_paise=action.amount_paise,
                outcome=decision.outcome.value,
                risk_band=assessment.band.value,
                required_approvals=required,
                status=status,
            )
            record_event("authorization_requested")
            return record

    @staticmethod
    def required_approvals(
        decision: PolicyDecision, risk: RiskAssessment, policy: MerchantPolicy
    ) -> int:
        """How many humans must say yes. Deterministic, and total.

        =====================  ===========  ===================================
        policy outcome         risk band    approvals
        =====================  ===========  ===================================
        allow                  low          0 - proceeds unattended
        allow                  medium       1
        allow / escalate       high         quorum (2 by default)
        escalate               low/medium   1
        =====================  ===========  ===================================

        A denial never reaches this function; it has no approval count because
        it has no path to approval.

        The quorum for high risk is the one row that matters. One approval
        means one compromised account is enough to move flagged money; two
        means an attacker needs two, and the second person is looking at a
        request the first has already blessed - which is exactly when someone
        notices it is strange.
        """
        if risk.band is RiskBand.HIGH:
            return max(2, policy.quorum_approvals)
        if decision.outcome is PolicyOutcome.ESCALATE:
            return 1
        if risk.band is RiskBand.MEDIUM:
            return 1
        return 0

    # --- 2. read ------------------------------------------------------------

    async def get(self, auth_id: str, *, merchant_id: str) -> dict[str, Any]:
        """Fetch one record, expiring it lazily if its time has passed.

        Expiry is applied on read rather than by a sweeper. A background job
        that fell behind would leave records that *look* approved past their
        deadline, and every caller would have to remember to check the clock
        themselves. Doing it here means there is no window in which a stale
        approval reads as live.
        """
        record = await self._repo.get(auth_id, merchant_id=merchant_id)
        if record is None:
            raise NotFoundError("AUTHORIZATION_NOT_FOUND", f"No authorization {auth_id!r}")

        if self._is_past_expiry(record):
            expired = await self._repo.mark_expired(auth_id, merchant_id=merchant_id)
            return expired or record
        return record

    # --- 3. approve ---------------------------------------------------------

    async def approve(
        self,
        auth_id: str,
        *,
        merchant_id: str,
        approver_id: str,
        approver_role: str,
    ) -> dict[str, Any]:
        """Add one approval, and promote to approved once the quorum is met."""
        with span("authorization.approve", authorization_id=auth_id):
            record = await self.get(auth_id, merchant_id=merchant_id)

            if not has_permission(approver_role, Permission.AUTHORIZATION_APPROVE):
                # Checked here as well as at the router. A future internal
                # caller that never passes through a route must meet the same
                # bar, and "the router checks it" is not a property this
                # module can verify.
                raise AuthorizationError(
                    "APPROVER_NOT_PERMITTED",
                    f"role '{approver_role}' may not approve authorizations",
                )

            if record["status"] == "denied":
                raise ConflictError(
                    "AUTHORIZATION_DENIED",
                    "policy denied this action; it cannot be approved",
                    {"reasons": record.get("reasons", [])},
                )
            if record["status"] == "expired" or self._is_past_expiry(record):
                raise ConflictError(
                    "AUTHORIZATION_EXPIRED", "this authorization is no longer valid"
                )
            if record["status"] != "pending":
                raise ConflictError(
                    "AUTHORIZATION_NOT_PENDING",
                    f"authorization is {record['status']}, not pending",
                    {"status": record["status"]},
                )

            if approver_id == record["requested_by"]:
                # Four eyes. Without this, a quorum of two is a quorum of one
                # holding two hats, and every guarantee above it is decoration.
                raise AuthorizationError(
                    "SELF_APPROVAL_FORBIDDEN",
                    "the requester of an authorization may not approve it",
                )

            if any(a["approver_id"] == approver_id for a in record["approvers"]):
                raise ConflictError(
                    "DUPLICATE_APPROVAL",
                    "this approver has already approved; a quorum needs distinct people",
                )

            updated = await self._repo.append_approval(
                auth_id,
                merchant_id=merchant_id,
                approver_id=approver_id,
                approver_role=approver_role,
            )
            if updated is None:
                # Someone transitioned it between the read and the write.
                raise ConflictError(
                    "AUTHORIZATION_NOT_PENDING", "authorization changed state concurrently"
                )

            logger.info(
                "authorization_approval_added",
                authorization_id=auth_id,
                approver_id=approver_id,
                approvals=len(updated["approvers"]),
                required=updated["required_approvals"],
                status=updated["status"],
            )
            record_event(
                "authorization_approved"
                if updated["status"] == "approved"
                else "authorization_approval_added"
            )
            return updated

    async def revoke(self, auth_id: str, *, merchant_id: str) -> dict[str, Any]:
        """Withdraw an authorization before it is spent."""
        record = await self.get(auth_id, merchant_id=merchant_id)
        revoked = await self._repo.revoke(auth_id, merchant_id=merchant_id)
        if revoked is None:
            raise ConflictError(
                "AUTHORIZATION_NOT_REVOCABLE",
                f"authorization is {record['status']} and cannot be revoked",
            )
        record_event("authorization_revoked")
        return revoked

    # --- 4. spend -----------------------------------------------------------

    async def consume(
        self, auth_id: str, *, merchant_id: str, action: FinancialAction
    ) -> dict[str, Any]:
        """Spend the authorization on exactly this action.

        **This is the gate.** Every code path that moves money calls it, and it
        is the only place that can turn an approval into permission to act. It
        refuses unless all five hold:

        * the record exists inside this merchant,
        * its status is ``approved`` - not pending, not denied, not revoked,
        * it has not expired,
        * it has not already been spent,
        * the action presented now fingerprints identically to the one that
          was approved.

        The fingerprint check is what makes the other four worth having. An
        approval that could be presented against a different amount would be
        a signed blank cheque.
        """
        with span("authorization.consume", authorization_id=auth_id):
            record = await self.get(auth_id, merchant_id=merchant_id)

            if record["status"] == "consumed":
                raise ConflictError(
                    "AUTHORIZATION_ALREADY_CONSUMED",
                    "this authorization has already been used",
                    {"consumed_at": str(record.get("consumed_at"))},
                )
            if record["status"] == "expired" or self._is_past_expiry(record):
                raise ConflictError(
                    "AUTHORIZATION_EXPIRED", "this authorization is no longer valid"
                )
            if record["status"] != "approved":
                raise ConflictError(
                    "AUTHORIZATION_NOT_APPROVED",
                    f"authorization is {record['status']}; it has not been approved",
                    {
                        "status": record["status"],
                        "approvals": len(record.get("approvers", [])),
                        "required_approvals": record.get("required_approvals"),
                    },
                )

            if action.fingerprint() != record["action_fingerprint"]:
                # The amount, subject, kind or currency differs from what was
                # approved. Refusing is the entire point of binding the scope.
                logger.warning(
                    "authorization_scope_mismatch",
                    authorization_id=auth_id,
                    merchant_id=merchant_id,
                    presented_amount_paise=action.amount_paise,
                    approved_amount_paise=record["amount_paise"],
                )
                record_event("authorization_scope_mismatch")
                raise ConflictError(
                    "AUTHORIZATION_SCOPE_MISMATCH",
                    "this authorization was granted for a different action",
                    {
                        "approved_amount_paise": record["amount_paise"],
                        "presented_amount_paise": action.amount_paise,
                    },
                )

            consumed = await self._repo.mark_consumed(auth_id, merchant_id=merchant_id)
            if consumed is None:
                # Lost the race with a concurrent spend of the same record.
                raise ConflictError(
                    "AUTHORIZATION_ALREADY_CONSUMED",
                    "this authorization was used concurrently",
                )

            logger.info(
                "authorization_consumed",
                authorization_id=auth_id,
                merchant_id=merchant_id,
                kind=consumed["action_kind"],
                amount_paise=consumed["amount_paise"],
            )
            record_event("authorization_consumed")
            return consumed

    # --- helpers ------------------------------------------------------------

    @staticmethod
    def _is_past_expiry(record: dict[str, Any]) -> bool:
        expires_at = record.get("expires_at")
        if expires_at is None:
            return False
        if expires_at.tzinfo is None:  # pragma: no cover - stored tz-aware
            expires_at = expires_at.replace(tzinfo=UTC)
        return datetime.now(UTC) >= expires_at

    @staticmethod
    def _base_fields(
        action: FinancialAction,
        tenant_id: str | None,
        decision: PolicyDecision,
        policy: MerchantPolicy,
    ) -> dict[str, Any]:
        return {
            "merchant_id": action.merchant_id,
            "tenant_id": tenant_id,
            "action_kind": action.kind.value,
            "amount_paise": action.amount_paise,
            "currency": action.currency,
            "subject_id": action.subject_id,
            "action_fingerprint": action.fingerprint(),
            "requested_by": action.actor_id,
            "requested_by_role": action.actor_role,
            "policy_decision": decision.model_dump(mode="json"),
            "ttl_seconds": policy.authorization_ttl_seconds,
        }


__all__ = ["AuthorizationService"]
