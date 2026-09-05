"""Audit ledger routes.

Read-only by construction. There is no route here that writes an entry, and
there is none that edits or deletes one, because the ledger has no such
operations — an append-only store with an HTTP mutation endpoint is not
append-only.

Every query is scoped to the caller's own merchant chain. The chain is
per-merchant precisely so that this scoping is structural rather than a filter
someone could forget to apply.
"""

from fastapi import APIRouter, Depends, Query

from core.rbac import Permission
from dependencies.auth import CurrentUser, require_perm
from modules.audit.ledger import AuditLedger
from schemas.audit import ChainVerificationOut, LedgerEntryOut, LedgerPageOut

router = APIRouter(prefix="/api/v1/audit", tags=["audit"])


def get_ledger() -> AuditLedger:
    return AuditLedger()


@router.get(
    "/entries",
    response_model=LedgerPageOut,
    dependencies=[Depends(require_perm(Permission.AUDIT_READ))],
)
async def list_entries(
    principal: CurrentUser,
    entity_type: str | None = Query(default=None, max_length=64),
    entity_id: str | None = Query(default=None, max_length=64),
    action: str | None = Query(default=None, max_length=128),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    ledger: AuditLedger = Depends(get_ledger),
) -> LedgerPageOut:
    """Read a window of the caller's audit chain."""
    entries, total = await ledger.entries_for(
        principal.merchant_id,
        entity_type=entity_type,
        entity_id=entity_id,
        action=action,
        limit=limit,
        offset=offset,
    )
    return LedgerPageOut(
        entries=[LedgerEntryOut(**entry.to_dict()) for entry in entries],
        total=total,
        limit=limit,
        offset=offset,
        head_hash=await ledger.head(principal.merchant_id),
    )


@router.get(
    "/verify",
    response_model=ChainVerificationOut,
    dependencies=[Depends(require_perm(Permission.AUDIT_READ))],
)
async def verify_chain(
    principal: CurrentUser,
    ledger: AuditLedger = Depends(get_ledger),
) -> ChainVerificationOut:
    """Walk the whole chain and report every break in it.

    A broken chain is reported as ``valid: false`` with a 200, not as an error
    status. The request succeeded; it is the data that is wrong, and an
    investigator needs the list of breaks rather than an exception.
    """
    result = await ledger.verify(principal.merchant_id)
    return ChainVerificationOut(**result.to_dict())
