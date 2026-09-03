"""Product management.

Separate from ``routers/catalog.py`` on purpose. Catalog is the read path a
shopper uses; this is the write path a merchant uses, and they need different
permissions. Merging them would mean one permission covering both browsing and
editing prices.

``merchant_id`` always comes from the verified token. There is no route here
that accepts it as a parameter - a merchant may only ever edit its own
catalogue, and the way to guarantee that is to give the caller no way to name
another one.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from core.rbac import Permission
from dependencies.auth import CurrentUser, require_perm
from modules.catalog.service import CatalogService
from schemas.catalog import ProductListResponse, ProductOut
from schemas.commerce import ProductCreateRequest, ProductUpdateRequest

router = APIRouter(prefix="/api/v1/products", tags=["products"])


def get_catalog_service() -> CatalogService:
    return CatalogService()


CatalogDep = Annotated[CatalogService, Depends(get_catalog_service)]


@router.get(
    "",
    response_model=ProductListResponse,
    dependencies=[Depends(require_perm(Permission.CATALOG_READ))],
)
async def list_products(
    principal: CurrentUser,
    svc: CatalogDep,
    q: str | None = Query(default=None, max_length=200),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    include_inactive: bool = Query(default=False),
) -> ProductListResponse:
    items, total = await svc.list_products(
        merchant_id=principal.merchant_id,
        query=q,
        limit=limit,
        offset=offset,
        active_only=not include_inactive,
    )
    return ProductListResponse(
        items=[ProductOut(**i) for i in items], total=total, limit=limit, offset=offset
    )


@router.get(
    "/{sku}",
    response_model=ProductOut,
    dependencies=[Depends(require_perm(Permission.CATALOG_READ))],
)
async def get_product(sku: str, principal: CurrentUser, svc: CatalogDep) -> ProductOut:
    product = await svc.get_by_sku(merchant_id=principal.merchant_id, sku=sku)
    return ProductOut(**product)


@router.post(
    "",
    response_model=ProductOut,
    status_code=201,
    dependencies=[Depends(require_perm(Permission.ADMIN_POLICY))],
)
async def create_product(
    body: ProductCreateRequest, principal: CurrentUser, svc: CatalogDep
) -> ProductOut:
    """Add a product to the caller's own catalogue."""
    product = await svc.create_product(
        merchant_id=principal.merchant_id,
        sku=body.sku,
        name=body.name,
        list_price_paise=body.list_price_paise,
        cost_paise=body.cost_paise,
        quantity_on_hand=body.quantity_on_hand,
        description=body.description,
        attributes=body.attributes,
        active=body.active,
    )
    return ProductOut(**product)


@router.put(
    "/{sku}",
    response_model=ProductOut,
    dependencies=[Depends(require_perm(Permission.ADMIN_POLICY))],
)
async def update_product(
    sku: str, body: ProductUpdateRequest, principal: CurrentUser, svc: CatalogDep
) -> ProductOut:
    """Partial update. Absent fields are left alone rather than nulled."""
    product = await svc.update_product(
        merchant_id=principal.merchant_id,
        sku=sku,
        changes=body.model_dump(exclude_unset=True),
    )
    return ProductOut(**product)
