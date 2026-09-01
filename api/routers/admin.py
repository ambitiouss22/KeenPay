"""Admin routes — escalations (HITL)."""

from fastapi import APIRouter, Depends

from core.rbac import Permission
from dependencies.auth import CurrentUser, require_perm

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])


@router.get("/escalations", dependencies=[Depends(require_perm(Permission.ESCALATION_READ))])
async def list_escalations(principal: CurrentUser):
    return {"items": [], "total": 0}


@router.post(
    "/escalations/{ticket_id}/resolve",
    dependencies=[Depends(require_perm(Permission.ESCALATION_RESOLVE))],
)
async def resolve_escalation(ticket_id: str, principal: CurrentUser):
    return {"ticket_id": ticket_id, "status": "resolved", "resolved_by": principal.user_id}
