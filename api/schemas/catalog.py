"""Catalog API schemas."""

from typing import Any

from pydantic import BaseModel, Field


class ProductAttributes(BaseModel):
    model_config = {"extra": "allow"}
    color: str | None = None
    size: str | None = None


class ProductOut(BaseModel):
    id: str
    sku: str
    name: str
    description: str | None = None
    list_price_paise: int = Field(ge=0)
    cost_paise: int = Field(ge=0)
    quantity_on_hand: int = Field(ge=0)
    quantity_available: int = Field(ge=0)
    attributes: dict[str, Any] = Field(default_factory=dict)
    active: bool = True


class ProductListResponse(BaseModel):
    items: list[ProductOut]
    total: int
    limit: int
    offset: int
