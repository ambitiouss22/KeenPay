"""Product catalog persistence."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from config.settings import get_settings

_MEMORY_PRODUCTS: list[dict[str, Any]] = [
    {
        "id": "prod_hoodie_navy_m",
        "sku": "HOODIE-NAVY-M",
        "merchant_id": "merchant_keen",
        "name": "Navy Hoodie (M)",
        "description": "Soft cotton blend hoodie",
        "list_price_paise": 249900,
        "cost_paise": 120000,
        "quantity_on_hand": 50,
        "quantity_reserved": 0,
        "attributes": {"color": "navy", "size": "M"},
        "active": True,
    },
    {
        "id": "prod_hoodie_navy_l",
        "sku": "HOODIE-NAVY-L",
        "merchant_id": "merchant_keen",
        "name": "Navy Hoodie (L)",
        "description": "Soft cotton blend hoodie",
        "list_price_paise": 249900,
        "cost_paise": 120000,
        "quantity_on_hand": 35,
        "quantity_reserved": 0,
        "attributes": {"color": "navy", "size": "L"},
        "active": True,
    },
    {
        "id": "prod_tee_white_m",
        "sku": "TEE-WHITE-M",
        "merchant_id": "merchant_keen",
        "name": "White Tee (M)",
        "description": "Classic crew neck",
        "list_price_paise": 99900,
        "cost_paise": 45000,
        "quantity_on_hand": 100,
        "quantity_reserved": 0,
        "attributes": {"color": "white", "size": "M"},
        "active": True,
    },
]


def _enrich(product: dict[str, Any]) -> dict[str, Any]:
    available = product["quantity_on_hand"] - product.get("quantity_reserved", 0)
    return {**product, "quantity_available": available}


class ProductRepository:
    def __init__(self, session: AsyncSession | None = None) -> None:
        self._session = session
        self._memory = get_settings().use_in_memory_store or session is None

    async def list_products(
        self,
        *,
        merchant_id: str,
        q: str | None = None,
        sku: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[list[dict[str, Any]], int]:
        if self._memory:
            items = [p for p in _MEMORY_PRODUCTS if p["merchant_id"] == merchant_id and p["active"]]
            if sku:
                items = [p for p in items if p["sku"] == sku]
            if q:
                ql = q.lower()
                items = [
                    p
                    for p in items
                    if ql in p["name"].lower()
                    or ql in p["sku"].lower()
                    or ql in (p.get("description") or "").lower()
                ]
            total = len(items)
            return [_enrich(p) for p in items[offset : offset + limit]], total

        assert self._session is not None
        clauses = ["merchant_id = :merchant_id", "active = TRUE"]
        params: dict[str, Any] = {"merchant_id": merchant_id, "limit": limit, "offset": offset}
        if sku:
            clauses.append("sku = :sku")
            params["sku"] = sku
        if q:
            clauses.append("search_vector @@ plainto_tsquery('english', :q)")
            params["q"] = q
        where = " AND ".join(clauses)
        # `where` is assembled from the hardcoded `clauses` list above; every
        # user value is passed separately as a bound parameter (:merchant_id,
        # :sku, :q). Nothing user-controlled reaches the SQL text.
        count_sql = text(f"SELECT COUNT(*) FROM products WHERE {where}")  # noqa: S608  # nosec B608
        total = (await self._session.execute(count_sql, params)).scalar_one()
        sql = text(
            "SELECT id, sku, merchant_id, name, description, list_price_paise, cost_paise, "  # noqa: S608
            "quantity_on_hand, quantity_reserved, attributes, active, "
            "(quantity_on_hand - quantity_reserved) AS quantity_available "
            f"FROM products WHERE {where} "  # nosec B608
            "ORDER BY name LIMIT :limit OFFSET :offset"
        )
        rows = (await self._session.execute(sql, params)).mappings().all()
        return [dict(r) for r in rows], int(total)

    async def create(self, record: dict[str, Any]) -> dict[str, Any]:
        """Insert a product.

        The in-memory store is a module-level list, so a write here is visible
        to every repository instance - which is what makes the dev store behave
        like a database rather than like per-request state.
        """
        if self._session is None or get_settings().use_in_memory_store:
            _MEMORY_PRODUCTS.append(record)
            return _enrich(record)

        await self._session.execute(
            text(
                """
                INSERT INTO products (
                    id, sku, merchant_id, name, description, list_price_paise,
                    cost_paise, quantity_on_hand, quantity_reserved, attributes, active
                ) VALUES (
                    :id, :sku, :merchant_id, :name, :description, :list_price_paise,
                    :cost_paise, :quantity_on_hand, 0, CAST(:attributes AS jsonb), :active
                )
                """
            ),
            {**record, "attributes": json.dumps(record.get("attributes") or {})},
        )
        return _enrich(record)

    async def update(
        self,
        *,
        merchant_id: str,
        sku: str,
        changes: dict[str, Any],
        product_id: str | None = None,
    ) -> dict[str, Any]:
        """Apply a partial update and return the stored row.

        ``changes`` is filtered by the caller to a known field set; the column
        list here is the second gate, so a key that slipped through cannot
        become part of the statement.
        """
        columns = {
            "name",
            "description",
            "list_price_paise",
            "cost_paise",
            "quantity_on_hand",
            "attributes",
            "active",
        }
        patch = {k: v for k, v in changes.items() if k in columns}

        if self._session is None or get_settings().use_in_memory_store:
            for product in _MEMORY_PRODUCTS:
                if product["merchant_id"] == merchant_id and product["sku"] == sku:
                    product.update(patch)
                    return _enrich(product)
            raise KeyError(sku)

        if patch:
            assignments = ", ".join(f"{k} = :{k}" for k in patch)
            await self._session.execute(
                text(
                    f"UPDATE products SET {assignments} "  # noqa: S608  # nosec B608
                    "WHERE merchant_id = :merchant_id AND sku = :sku"
                ),
                {**patch, "merchant_id": merchant_id, "sku": sku},
            )
        row = await self.get_by_sku(merchant_id=merchant_id, sku=sku)
        if row is None:
            raise KeyError(sku)
        return row

    async def get_by_sku(self, *, merchant_id: str, sku: str) -> dict[str, Any] | None:
        items, _ = await self.list_products(merchant_id=merchant_id, sku=sku, limit=1, offset=0)
        return items[0] if items else None

    async def stock_map(self, *, merchant_id: str, skus: list[str]) -> dict[str, int]:
        items, _ = await self.list_products(merchant_id=merchant_id, limit=100, offset=0)
        return {p["sku"]: p["quantity_available"] for p in items if p["sku"] in skus}
