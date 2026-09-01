"""Catalog business logic."""

from repositories.products import ProductRepository


class CatalogService:
    def __init__(self, repo: ProductRepository | None = None) -> None:
        self._repo = repo or ProductRepository()

    async def search(
        self,
        *,
        merchant_id: str,
        q: str | None = None,
        sku: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ):
        limit = min(limit, 50)
        return await self._repo.list_products(
            merchant_id=merchant_id, q=q, sku=sku, limit=limit, offset=offset
        )

    async def get_product(self, *, merchant_id: str, sku: str):
        return await self._repo.get_by_sku(merchant_id=merchant_id, sku=sku)
