"""Transaction passport routes."""

from fastapi import APIRouter, Depends, HTTPException

from core.rbac import Permission
from dependencies.auth import CurrentUser, require_perm
from modules.passport.service import PassportService
from schemas.passport import PassportOut, PassportVerifyOut, PassportVerifyRequest

router = APIRouter(prefix="/api/v1/passport", tags=["passport"])


def get_passport_service() -> PassportService:
    return PassportService()


@router.post(
    "/verify",
    response_model=PassportVerifyOut,
    dependencies=[Depends(require_perm(Permission.AUDIT_READ))],
)
async def verify(
    body: PassportVerifyRequest,
    service: PassportService = Depends(get_passport_service),
) -> PassportVerifyOut:
    """Check a passport that was issued earlier.

    Registered before the ``/{payment_id}`` route on purpose: FastAPI matches
    in declaration order, and a parameterised path declared first would swallow
    ``/verify`` as a payment id.

    An invalid passport is a 200 with ``valid: false``. The request was
    perfectly well formed — the answer is simply "no", and returning an error
    status would make a legitimate negative result look like a client bug.
    """
    valid, errors = service.verify({"body": body.body, "signature": body.signature})
    return PassportVerifyOut(valid=valid, errors=errors)


@router.get(
    "/{payment_id}",
    response_model=PassportOut,
    dependencies=[Depends(require_perm(Permission.AUDIT_READ))],
)
async def issue(
    payment_id: str,
    principal: CurrentUser,
    service: PassportService = Depends(get_passport_service),
) -> PassportOut:
    """Issue the signed passport for one payment."""
    passport = await service.issue(payment_id, merchant_id=principal.merchant_id)
    if not passport:
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": "PAYMENT_NOT_FOUND", "message": "Not found"}},
        )
    return PassportOut(**passport)
