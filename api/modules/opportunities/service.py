"""Opportunity service: generate suggestions, and persist them once.

The orchestrator for the GROW read path. Routers call it; it reads the
catalogue and (optionally) a cart, runs the deterministic rules, and stores the
result under ids derived from the suggestions themselves.

**Where the AI fits.** The runtime may send recommendations along with the
request. They are hints about *what to look at*, never about what anything is
worth. Each one is put through the same rules as everything else: the sku must
exist, be this merchant's, be active and be in stock, and the pairing must be one
the rules would score. A hint that survives is scored by the rules, not by
whatever the model claimed - there is no field on the request that can set a
score, a price or a discount, so no wording of a recommendation can reach money.
Hints that do not survive are returned in ``rejected`` with a reason, because a
silently dropped recommendation looks to the caller exactly like one that was
never sent.

That is the architecture invariant expressed at this boundary: the model widens
the search, the Control Plane decides the answer, and acting on a suggestion
still costs a campaign reservation with its own permission and its own cap.

**Why persistence is deterministic.** An opportunity's id is a hash of what it
means, so re-running generation returns the rows that already exist rather than
creating a second copy. The response is built from the *stored* rows, so a caller
never sees a suggestion the store does not hold.
"""

from __future__ import annotations

from typing import Any

from core.exceptions import ValidationError
from core.logging import get_logger
from core.observability import record_event, span
from modules.catalog.service import CatalogService
from modules.commerce.flow import PurchaseFlow
from modules.opportunities import rules
from repositories.opportunities import OpportunityRepository

logger = get_logger(__name__)

#: How much of the catalogue one generation considers. A merchant with more
#: products than this gets suggestions from the first page rather than a request
#: that walks the whole catalogue on every call.
CATALOG_SCAN_LIMIT = 200

#: Most suggestions one call may return.
MAX_SUGGESTIONS = 50

#: Most hints one request may carry. The runtime is bounded on its own side too;
#: this is the bound that holds when the caller is something else.
MAX_HINTS = 20


class OpportunityService:
    def __init__(
        self,
        *,
        catalog: CatalogService | None = None,
        flow: PurchaseFlow | None = None,
        repo: OpportunityRepository | None = None,
    ) -> None:
        self._catalog = catalog or CatalogService()
        self._flow = flow or PurchaseFlow()
        self._repo = repo or OpportunityRepository()

    async def generate(
        self,
        *,
        merchant_id: str,
        user_id: str | None = None,
        tenant_id: str | None = None,
        cart_id: str | None = None,
        kinds: list[str] | None = None,
        max_suggestions: int = 10,
        recommendations: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Produce and store this merchant's growth suggestions."""
        limit = self._validate_limit(max_suggestions)
        wanted = self._validate_kinds(kinds)
        hints = self._validate_hints(recommendations or [])

        with span("opportunities.generate", merchant_id=merchant_id, cart_id=cart_id or ""):
            catalog, _total = await self._catalog.list_products(
                merchant_id=merchant_id, limit=CATALOG_SCAN_LIMIT, offset=0
            )

            if cart_id:
                # Resolved through the flow, which scopes to this merchant and
                # answers 404 for anything else. Reading the cart repository
                # directly here would be a second place that has to remember.
                cart = await self._flow.get_cart(cart_id, merchant_id=merchant_id)
                anchors = rules.anchors_from_cart(cart["items"])
                subject_id = f"cart:{cart_id}"
            else:
                anchors = rules.anchors_from_catalog(catalog)
                subject_id = f"catalog:{merchant_id}"

            suggested = rules.generate(
                catalog=catalog, anchors=anchors, kinds=wanted, limit=limit
            )
            promoted, rejected = self._apply_hints(
                hints, catalog=catalog, anchors=anchors, wanted=wanted, existing=suggested
            )

            merged = sorted(
                [*suggested, *promoted], key=rules.Suggestion.sort_key
            )[:limit]

            records = [
                {
                    "id": rules.opportunity_id(
                        rules.fingerprint(
                            merchant_id=merchant_id,
                            kind=s.kind,
                            subject_id=subject_id,
                            sku=s.sku,
                        )
                    ),
                    "tenant_id": tenant_id,
                    "session_id": None,
                    "user_id": user_id,
                    "kind": s.kind,
                    "score": str(s.score),
                    "acted_on": False,
                    "payload": s.payload(merchant_id=merchant_id, subject_id=subject_id),
                }
                for s in merged
            ]
            stored = await self._repo.insert_missing(records)

        logger.info(
            "opportunities_generated",
            merchant_id=merchant_id,
            subject_id=subject_id,
            generated=len(stored),
            rejected=len(rejected),
        )
        record_event("opportunities_generated")
        return {
            "subject_id": subject_id,
            "items": [self.to_public(row) for row in stored],
            "generated": len(stored),
            "rejected": rejected,
        }

    async def list_opportunities(
        self,
        *,
        merchant_id: str,
        kind: str | None = None,
        acted_on: bool | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[dict[str, Any]], int]:
        if kind is not None and kind not in rules.KINDS:
            raise ValidationError(
                "UNKNOWN_OPPORTUNITY_KIND",
                f"kind must be one of {', '.join(rules.KINDS)}",
                {"kind": kind},
            )
        rows, total = await self._repo.list_for_merchant(
            merchant_id=merchant_id,
            kind=kind,
            acted_on=acted_on,
            limit=limit,
            offset=offset,
        )
        return [self.to_public(row) for row in rows], total

    # --- helpers ------------------------------------------------------------

    @staticmethod
    def to_public(row: dict[str, Any]) -> dict[str, Any]:
        """Flatten a stored row into the shape the API answers with.

        The money-shaped field here is ``list_price_paise``, and it is the
        catalogue's own price. There is deliberately no discount field to
        flatten: an opportunity that could name a discount would be an
        opportunity that could spend a budget.
        """
        payload = row.get("payload") or {}
        return {
            "id": str(row["id"]),
            "kind": row["kind"],
            "subject_id": payload.get("subject_id"),
            "sku": payload.get("sku"),
            "name": payload.get("name"),
            "list_price_paise": payload.get("list_price_paise"),
            "anchor_sku": payload.get("anchor_sku"),
            "rationale": payload.get("rationale"),
            "score": float(row["score"]) if row.get("score") is not None else None,
            "acted_on": bool(row.get("acted_on")),
            "created_at": row.get("created_at"),
        }

    def _apply_hints(
        self,
        hints: list[dict[str, str]],
        *,
        catalog: list[dict[str, Any]],
        anchors: list[rules.Anchor],
        wanted: list[str],
        existing: list[rules.Suggestion],
    ) -> tuple[list[rules.Suggestion], list[dict[str, str]]]:
        """Score each hint by the rules, or say why it was refused."""
        by_sku = {str(p["sku"]): p for p in catalog}
        already = {(s.kind, s.sku) for s in existing}

        promoted: list[rules.Suggestion] = []
        rejected: list[dict[str, str]] = []

        for hint in hints:
            kind, sku = hint["kind"], hint["sku"]

            if kind not in rules.KINDS:
                rejected.append(self._reject(kind, sku, "unknown kind"))
                continue
            if kind not in wanted:
                rejected.append(self._reject(kind, sku, "kind not requested"))
                continue
            if (kind, sku) in already:
                # Already produced by the rules; the hint adds nothing and must
                # not become a second copy at a different score.
                continue

            candidate = by_sku.get(sku)
            if candidate is None:
                # Same answer for "no such sku" and "another merchant's sku".
                # Distinguishing them would turn this endpoint into a catalogue
                # oracle for anyone holding a growth credential.
                rejected.append(self._reject(kind, sku, "not in this catalogue"))
                continue
            if not rules.sellable(candidate):
                rejected.append(self._reject(kind, sku, "inactive or out of stock"))
                continue

            best = None
            for anchor in anchors:
                score = rules.score_candidate(kind, anchor, candidate)
                if score is not None and (best is None or score > best[0]):
                    best = (score, anchor)

            if best is None:
                rejected.append(
                    self._reject(kind, sku, "does not qualify under the rules")
                )
                continue

            score, anchor = best
            promoted.append(
                rules.Suggestion(
                    kind=kind,
                    sku=sku,
                    name=str(candidate.get("name") or sku),
                    list_price_paise=int(candidate["list_price_paise"]),
                    score=score,
                    anchor_sku=anchor.sku,
                    rationale=f"suggested for review; {anchor.sku} is the closest match",
                )
            )
            already.add((kind, sku))

        return promoted, rejected

    @staticmethod
    def _reject(kind: str, sku: str, reason: str) -> dict[str, str]:
        return {"kind": kind, "sku": sku, "reason": reason}

    @staticmethod
    def _validate_limit(value: Any) -> int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValidationError(
                "INVALID_LIMIT", "max_suggestions must be an integer"
            )
        if value < 1 or value > MAX_SUGGESTIONS:
            raise ValidationError(
                "INVALID_LIMIT",
                f"max_suggestions must be between 1 and {MAX_SUGGESTIONS}, got {value}",
                {"max": MAX_SUGGESTIONS, "value": value},
            )
        return value

    @staticmethod
    def _validate_kinds(kinds: list[str] | None) -> list[str]:
        if not kinds:
            return list(rules.KINDS)
        unknown = [k for k in kinds if k not in rules.KINDS]
        if unknown:
            raise ValidationError(
                "UNKNOWN_OPPORTUNITY_KIND",
                f"unknown kind(s): {', '.join(sorted(unknown))}",
                {"allowed": list(rules.KINDS)},
            )
        # De-duplicated, and ordered by the canonical tuple rather than by the
        # request, so two callers asking for the same set generate the same rows.
        return [k for k in rules.KINDS if k in set(kinds)]

    @staticmethod
    def _validate_hints(recommendations: list[dict[str, Any]]) -> list[dict[str, str]]:
        if len(recommendations) > MAX_HINTS:
            raise ValidationError(
                "TOO_MANY_RECOMMENDATIONS",
                f"at most {MAX_HINTS} recommendations may be sent, got "
                f"{len(recommendations)}",
                {"max": MAX_HINTS},
            )
        hints: list[dict[str, str]] = []
        for item in recommendations:
            kind = str(item.get("kind", ""))
            sku = str(item.get("sku", ""))
            if not sku:
                raise ValidationError(
                    "INVALID_RECOMMENDATION", "each recommendation must name a sku"
                )
            hints.append({"kind": kind, "sku": sku})
        return hints


__all__ = [
    "CATALOG_SCAN_LIMIT",
    "MAX_HINTS",
    "MAX_SUGGESTIONS",
    "OpportunityService",
]
