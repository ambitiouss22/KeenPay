"""Session and chat routes."""

from fastapi import APIRouter, Depends, HTTPException, Query

from core.exceptions import KeenPayError
from core.rbac import Permission
from dependencies.auth import CurrentUser, require_perm
from schemas.session import (
    ChatMessageRequest,
    ChatMessageResponse,
    ConfirmPaymentRequest,
    ConfirmPaymentResponse,
    SessionCreateRequest,
    SessionCreateResponse,
    SessionOut,
)
from services.audit import AuditService
from services.session import SessionService

router = APIRouter(prefix="/api/v1/sessions", tags=["sessions"])


def get_session_service() -> SessionService:
    return SessionService()


def get_audit_service() -> AuditService:
    return AuditService()


@router.post(
    "",
    response_model=SessionCreateResponse,
    status_code=201,
    dependencies=[Depends(require_perm(Permission.SESSION_CREATE))],
)
async def create_session(
    body: SessionCreateRequest,
    principal: CurrentUser,
    svc: SessionService = Depends(get_session_service),
):
    record = await svc.create_session(
        merchant_id=body.merchant_id or principal.merchant_id,
        user_id=principal.user_id,
        metadata=body.metadata,
    )
    return SessionCreateResponse(
        session_id=record["id"],
        status=record["status"],
        created_at=record["created_at"],
        ws_url=f"/ws/v1/session?session_id={record['id']}",
    )


@router.get(
    "/{session_id}",
    response_model=SessionOut,
    dependencies=[Depends(require_perm(Permission.SESSION_READ_OWN))],
)
async def get_session(
    session_id: str,
    principal: CurrentUser,
    svc: SessionService = Depends(get_session_service),
):
    record = await svc.get_session(session_id)
    if not record:
        raise HTTPException(
            status_code=404, detail={"error": {"code": "SESSION_NOT_FOUND", "message": "Not found"}}
        )
    return SessionOut(
        session_id=record["id"],
        status=record["status"],
        negotiation_round=record.get("negotiation_round", 0),
        proposed_offer=record.get("proposed_offer"),
        approved_offer=record.get("approved_offer"),
        guardrail_decision=record.get("guardrail_decision"),
        final_amount_paise=record.get("final_amount_paise"),
    )


@router.post(
    "/{session_id}/messages",
    response_model=ChatMessageResponse,
    dependencies=[Depends(require_perm(Permission.SESSION_READ_OWN))],
)
async def post_message(
    session_id: str,
    body: ChatMessageRequest,
    principal: CurrentUser,
    svc: SessionService = Depends(get_session_service),
):
    try:
        result = await svc.process_message(
            session_id=session_id,
            text=body.text,
            merchant_id=principal.merchant_id,
        )
    except KeenPayError as exc:
        raise HTTPException(
            status_code=404, detail={"error": {"code": exc.code, "message": exc.message}}
        ) from exc
    return ChatMessageResponse(**result)


@router.post(
    "/{session_id}/confirm",
    response_model=ConfirmPaymentResponse,
    dependencies=[Depends(require_perm(Permission.SESSION_READ_OWN))],
)
async def confirm_payment(
    session_id: str,
    body: ConfirmPaymentRequest,
    principal: CurrentUser,
    svc: SessionService = Depends(get_session_service),
):
    if not body.confirmed:
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": "NOT_CONFIRMED", "message": "Payment not confirmed"}},
        )
    try:
        result = await svc.confirm_payment(
            session_id=session_id,
            merchant_id=principal.merchant_id,
            user_id=principal.user_id,
            idempotency_key=body.idempotency_key,
        )
    except KeenPayError as exc:
        status = 409 if exc.code == "GUARDRAIL_NOT_APPROVED" else 400
        raise HTTPException(
            status_code=status,
            detail={"error": {"code": exc.code, "message": exc.message}},
        ) from exc
    return ConfirmPaymentResponse(**result)


@router.get(
    "/{session_id}/audit",
    dependencies=[Depends(require_perm(Permission.SESSION_READ_OWN))],
)
async def session_audit(
    session_id: str,
    principal: CurrentUser,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    audit: AuditService = Depends(get_audit_service),
):
    items, total = await audit.list_session_audit(session_id, limit=limit, offset=offset)
    return {"items": items, "total": total}
