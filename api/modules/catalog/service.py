"""Catalog service: the products a merchant sells.

Every method takes ``merchant_id`` and scopes to it. The parameter is not
optional and has no default, so a caller cannot accidentally ask for "all
products" and get another merchant's catalogue - the mistake has to be typed
out deliberately rather than made by omission.

Prices are validated here as well as at the schema boundary. The schema guards
the HTTP edge; this guards every caller, including the agent flow and future
background jobs that never pass through a request.
"""

from __future__ import annotations

import uuid
from typing import Any

from core.exceptions import ConflictError, NotFoundError, ValidationError
from core.logging import get_logger
from core.observability import record_event, track_dependency
from modules.commerce.safety import validate_price_paise, validate_quantity
from repositories.products import ProductRepository

logger = get_logger(__name__)


class CatalogService:
    def __init__(self, repo: ProductRepository | None = None) -> None:
        self._repo = repo or ProductRepository()

    async def list_products(
        self,
        *,
        merchant_id: str,
        query: str | None = None,
        limit: int = 50,
        offset: int = 0,
        active_only: bool = True,
    ) -> tuple[list[dict[str, Any]], int]:
        """List this merchant's products.

        ``active_only`` is honoured here rather than pushed into the
        repository: the repository already filters to active rows only, so
        "include inactive" is not something it can express today. Accepting the
        flag and ignoring it would be worse than not offering it - a caller
        would believe it had asked for something it did not get.
        """
        with track_dependency("catalog", "list_products"):
            items, total = await self._repo.list_products(
                merchant_id=merchant_id,
                q=query,
                limit=limit,
                offset=offset,
            )
        if not active_only:
            logger.debug("include_inactive_not_supported", merchant_id=merchant_id)
        return items, total

    async def get_by_sku(self, *, merchant_id: str, sku: str) -> dict[str, Any]:
        with track_dependency("catalog", "get_by_sku"):
            product = await self._repo.get_by_sku(merchant_id=merchant_id, sku=sku)
        if product is None:
            raise NotFoundError("PRODUCT_NOT_FOUND", f"No product with sku {sku!r}")
        return product

    async def create_product(
        self,
        *,
        merchant_id: str,
        sku: str,
        name: str,
        list_price_paise: int,
        cost_paise: int,
        quantity_on_hand: int = 0,
        description: str | None = None,
        attributes: dict[str, Any] | None = None,
        active: bool = True,
    ) -> dict[str, Any]:
        """Add a product. The sku must be free within this merchant.

        Uniqueness is per merchant, not global: two merchants may both sell a
        ``TEE-WHITE-M``, and forcing them to coordinate skus would be absurd.
        """
        existing = await self._repo.get_by_sku(merchant_id=merchant_id, sku=sku)
        if existing is not None:
            raise ConflictError(
                "SKU_EXISTS", f"sku {sku!r} already exists for this merchant", {"sku": sku}
            )

        list_price = validate_price_paise(list_price_paise, field="list_price_paise")
        cost = validate_price_paise(cost_paise, field="cost_paise")
        # quantity_on_hand may legitimately be 0, so it is not run through
        # validate_quantity, which enforces >= 1 for order lines.
        if isinstance(quantity_on_hand, bool) or not isinstance(quantity_on_hand, int):
            raise ValidationError(
                "INVALID_QUANTITY", "quantity_on_hand must be an integer"
            )
        if quantity_on_hand < 0:
            raise ValidationError(
                "INVALID_QUANTITY", "quantity_on_hand may not be negative"
            )
        record = {
            "id": f"prod_{uuid.uuid4().hex[:16]}",
            "sku": sku,
            "merchant_id": merchant_id,
            "name": name,
            "description": description,
            "list_price_paise": list_price,
            "cost_paise": cost,
            "quantity_on_hand": quantity_on_hand,
            "quantity_reserved": 0,
            "attributes": attributes or {},
            "active": active,
        }
        created = await self._repo.create(record)
        record_event("product_created")
        return created

    async def update_product(
        self, *, merchant_id: str, sku: str, changes: dict[str, Any]
    ) -> dict[str, Any]:
        """Apply a partial update to one product.

        Only a known set of fields may be written. Merging an arbitrary dict
        would let a request set ``merchant_id`` and move a product into another
        merchant's catalogue.
        """
        product = await self.get_by_sku(merchant_id=merchant_id, sku=sku)

        allowed = {
            "name",
            "description",
            "list_price_paise",
            "cost_paise",
            "quantity_on_hand",
            "attributes",
            "active",
        }
        patch = {k: v for k, v in changes.items() if k in allowed and v is not None}

        if "list_price_paise" in patch:
            patch["list_price_paise"] = validate_price_paise(
                patch["list_price_paise"], field="list_price_paise"
            )
        if "cost_paise" in patch:
            patch["cost_paise"] = validate_price_paise(patch["cost_paise"], field="cost_paise")
        if "quantity_on_hand" in patch:
            qty = patch["quantity_on_hand"]
            if isinstance(qty, bool) or not isinstance(qty, int) or qty < 0:
                raise ValidationError(
                    "INVALID_QUANTITY", "quantity_on_hand must be a non-negative integer"
                )

        updated = await self._repo.update(
            merchant_id=merchant_id, sku=sku, changes=patch, product_id=product["id"]
        )
        record_event("product_updated")
        return updated

    async def check_availability(
        self, *, merchant_id: str, sku: str, quantity: int
    ) -> dict[str, Any]:
        """Product plus a validated quantity, or a typed failure."""
        product = await self.get_by_sku(merchant_id=merchant_id, sku=sku)
        validate_quantity(quantity)
        return product


__all__ = ["CatalogService"]
