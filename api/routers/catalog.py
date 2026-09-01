"""Catalog routes."""

from fastapi import APIRouter, Depends, HTTPException, Query

from core.rbac import Permission
from dependencies.auth import CurrentUser, require_perm
from schemas.catalog import ProductListResponse, ProductOut
from services.catalog import CatalogService

router = APIRouter(prefix="/api/v1/catalog", tags=["catalog"])


def get_catalog_service() -> CatalogService:
    return CatalogService()


@router.get(
    "/products",
    response_model=ProductListResponse,
    dependencies=[Depends(require_perm(Permission.CATALOG_READ))],
)
async def list_products(
    principal: CurrentUser,
    q: str | None = Query(default=None),
    sku: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=50),
    offset: int = Query(default=0, ge=0),
    catalog: CatalogService = Depends(get_catalog_service),
):
    items, total = await catalog.search(
        merchant_id=principal.merchant_id, q=q, sku=sku, limit=limit, offset=offset
    )
    return ProductListResponse(
        items=[ProductOut(**i) for i in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/products/{sku}",
    response_model=ProductOut,
    dependencies=[Depends(require_perm(Permission.CATALOG_READ))],
)
async def get_product(
    sku: str,
    principal: CurrentUser,
    catalog: CatalogService = Depends(get_catalog_service),
):
    product = await catalog.get_product(merchant_id=principal.merchant_id, sku=sku)
    if not product:
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": "PRODUCT_NOT_FOUND", "message": "Product not found"}},
        )
    return ProductOut(**product)
