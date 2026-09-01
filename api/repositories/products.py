"""Product catalog persistence."""

from __future__ import annotations

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
        count_sql = text(f"SELECT COUNT(*) FROM products WHERE {where}")
        total = (await self._session.execute(count_sql, params)).scalar_one()
        sql = text(
            f"""
            SELECT id, sku, merchant_id, name, description, list_price_paise, cost_paise,
                   quantity_on_hand, quantity_reserved, attributes, active,
                   (quantity_on_hand - quantity_reserved) AS quantity_available
            FROM products WHERE {where}
            ORDER BY name LIMIT :limit OFFSET :offset
            """
        )
        rows = (await self._session.execute(sql, params)).mappings().all()
        return [dict(r) for r in rows], int(total)

    async def get_by_sku(self, *, merchant_id: str, sku: str) -> dict[str, Any] | None:
        items, _ = await self.list_products(merchant_id=merchant_id, sku=sku, limit=1, offset=0)
        return items[0] if items else None

    async def stock_map(self, *, merchant_id: str, skus: list[str]) -> dict[str, int]:
        items, _ = await self.list_products(merchant_id=merchant_id, limit=100, offset=0)
        return {p["sku"]: p["quantity_available"] for p in items if p["sku"] in skus}
