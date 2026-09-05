"""Reconciliation run history.

A reconciliation pass that leaves no record is indistinguishable from one that
never ran, which is exactly the question asked after money goes missing. Each
pass writes a run row, every disagreement it found writes a diff row, and both
survive the process that produced them.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

_RUNS: dict[str, dict[str, Any]] = {}


def reset_reconciliation() -> None:
    """Drop every run. For test isolation only."""
    _RUNS.clear()


class ReconciliationRepository:
    """Runs and the diffs each one produced."""

    async def start_run(self, merchant_id: str, *, trigger: str = "scheduled") -> dict[str, Any]:
        """Open a run and return it."""
        run_id = f"rec_{uuid4().hex[:12]}"
        record = {
            "id": run_id,
            "merchant_id": merchant_id,
            "trigger": trigger,
            "status": "running",
            "checked": 0,
            "resolved_captured": 0,
            "resolved_failed": 0,
            "still_unknown": 0,
            "diffs": [],
            "started_at": datetime.now(UTC),
            "finished_at": None,
        }
        _RUNS[run_id] = record
        return dict(record)

    async def record_diff(
        self,
        run_id: str,
        *,
        payment_id: str,
        kind: str,
        local: Any,
        provider: Any,
        detail: str = "",
    ) -> None:
        """Note one disagreement between our ledger and the provider's."""
        run = _RUNS.get(run_id)
        if not run:
            return
        run["diffs"].append(
            {
                "payment_id": payment_id,
                "kind": kind,
                "local": local,
                "provider": provider,
                "detail": detail,
                "at": datetime.now(UTC),
            }
        )

    async def finish_run(
        self,
        run_id: str,
        *,
        checked: int,
        resolved_captured: int,
        resolved_failed: int,
        still_unknown: int,
        status: str = "completed",
    ) -> dict[str, Any] | None:
        """Close a run with its counters."""
        run = _RUNS.get(run_id)
        if not run:
            return None
        run["checked"] = checked
        run["resolved_captured"] = resolved_captured
        run["resolved_failed"] = resolved_failed
        run["still_unknown"] = still_unknown
        run["status"] = status
        run["finished_at"] = datetime.now(UTC)
        return dict(run)

    async def get(self, run_id: str, *, merchant_id: str) -> dict[str, Any] | None:
        """Fetch one run, scoped to its merchant."""
        run = _RUNS.get(run_id)
        if run and run.get("merchant_id") == merchant_id:
            return dict(run)
        return None

    async def latest(self, merchant_id: str) -> dict[str, Any] | None:
        """The most recently started run for one merchant."""
        runs = [r for r in _RUNS.values() if r.get("merchant_id") == merchant_id]
        if not runs:
            return None
        return dict(max(runs, key=lambda r: r["started_at"]))

    async def list_runs(self, merchant_id: str, *, limit: int = 20) -> list[dict[str, Any]]:
        """Runs for one merchant, newest first."""
        runs = [dict(r) for r in _RUNS.values() if r.get("merchant_id") == merchant_id]
        runs.sort(key=lambda r: r["started_at"], reverse=True)
        return runs[:limit]


__all__ = ["ReconciliationRepository", "reset_reconciliation"]
