"""Audit log service."""

from repositories.audit import AuditRepository


class AuditService:
    def __init__(self, repo: AuditRepository | None = None) -> None:
        self._repo = repo or AuditRepository()

    async def log_guardrail(
        self,
        *,
        session_id: str,
        merchant_id: str,
        decision_id: str,
        offer_version: int,
        input_snapshot: dict,
        output_snapshot: dict,
    ) -> None:
        await self._repo.append(
            session_id=session_id,
            order_id=None,
            merchant_id=merchant_id,
            actor="policy_engine",
            action="GUARDRAIL_EVALUATED",
            decision_id=decision_id,
            offer_version=offer_version,
            input_snapshot=input_snapshot,
            output_snapshot=output_snapshot,
        )

    async def log_payment_link(
        self,
        *,
        session_id: str,
        order_id: str,
        merchant_id: str,
        decision_id: str,
        offer_version: int,
        output_snapshot: dict,
    ) -> None:
        await self._repo.append(
            session_id=session_id,
            order_id=order_id,
            merchant_id=merchant_id,
            actor="system",
            action="PAYMENT_LINK_CREATED",
            decision_id=decision_id,
            offer_version=offer_version,
            output_snapshot=output_snapshot,
        )

    async def list_session_audit(self, session_id: str, *, limit: int = 50, offset: int = 0):
        return await self._repo.list_for_session(session_id, limit=limit, offset=offset)
