"""Reconciliation routes.

Running a pass is a privileged action, not a read. It talks to the provider on
behalf of the merchant and can move payments out of UNKNOWN, so it sits behind
the same roles that may move money — a support agent can see the outcome and
start nothing.
"""

from fastapi import APIRouter, Depends, Query

from dependencies.auth import CurrentUser, require_roles
from modules.reconciliation.worker import ReconciliationEngine
from schemas.webhooks import ReconciliationRunOut, ReconciliationStatusOut

router = APIRouter(prefix="/api/v1/reconciliation", tags=["reconciliation"])

CAN_READ = ("manager", "admin", "service", "support_agent")
CAN_RUN = ("manager", "admin", "service")


def get_reconciliation_engine() -> ReconciliationEngine:
    return ReconciliationEngine()


@router.get(
    "/status",
    response_model=ReconciliationStatusOut,
    dependencies=[Depends(require_roles(*CAN_READ))],
)
async def reconciliation_status(
    principal: CurrentUser,
    engine: ReconciliationEngine = Depends(get_reconciliation_engine),
) -> ReconciliationStatusOut:
    """How many payments are still unresolved, and how the last pass went."""
    status = await engine.status(principal.merchant_id)
    return ReconciliationStatusOut(**status)


@router.post(
    "/run",
    response_model=ReconciliationRunOut,
    dependencies=[Depends(require_roles(*CAN_RUN))],
)
async def run_reconciliation(
    principal: CurrentUser,
    trigger: str = Query(default="manual", max_length=32),
    engine: ReconciliationEngine = Depends(get_reconciliation_engine),
) -> ReconciliationRunOut:
    """Reconcile this merchant's UNKNOWN payments against the provider now."""
    report = await engine.run(principal.merchant_id, trigger=trigger)
    return ReconciliationRunOut(**report.to_dict())
