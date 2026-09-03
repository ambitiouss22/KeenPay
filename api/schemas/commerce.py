"""Cart, checkout and order schemas.

Money crosses this boundary only as integer paise. Pydantic would happily
coerce ``249.9`` into an ``int`` field by truncation, turning a price into a
different price without complaint, so the money fields are ``StrictInt``: a
float is rejected at the edge rather than silently rounded.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, StrictInt


class CartItemOut(BaseModel):
    item_id: str
    sku: str
    name: str
    unit_price_paise: int
    quantity: int
    line_total_paise: int


class CartOut(BaseModel):
    id: str
    merchant_id: str
    status: Literal["open", "checked_out", "abandoned"]
    items: list[CartItemOut] = Field(default_factory=list)
    subtotal_paise: int = 0
    item_count: int = 0
    line_count: int = 0
    created_at: datetime | None = None
    updated_at: datetime | None = None


class CartCreateRequest(BaseModel):
    """No fields: the merchant and user come from the token, never the body."""


class AddItemRequest(BaseModel):
    sku: str = Field(min_length=1, max_length=64)
    quantity: StrictInt = Field(ge=1, le=1000)
    # Note there is deliberately no price field. The catalogue sets the price;
    # accepting one here would let a client name what it pays.


class CheckoutRequest(BaseModel):
    idempotency_key: str = Field(min_length=8, max_length=128)
    discount_paise: StrictInt = Field(default=0, ge=0)


class OrderLineOut(BaseModel):
    sku: str
    name: str
    unit_price_paise: int
    quantity: int
    line_total_paise: int


class OrderOut(BaseModel):
    id: str
    cart_id: str | None = None
    merchant_id: str
    status: str
    currency: str = "INR"
    line_items: list[OrderLineOut] = Field(default_factory=list)
    subtotal_paise: int
    discount_amount_paise: int = 0
    final_amount_paise: int


class ProductCreateRequest(BaseModel):
    sku: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=255)
    list_price_paise: StrictInt = Field(ge=0)
    cost_paise: StrictInt = Field(ge=0)
    quantity_on_hand: StrictInt = Field(default=0, ge=0)
    description: str | None = Field(default=None, max_length=2000)
    attributes: dict[str, Any] = Field(default_factory=dict)
    active: bool = True


class ProductUpdateRequest(BaseModel):
    """Every field optional: a PUT here is a partial update."""

    name: str | None = Field(default=None, min_length=1, max_length=255)
    list_price_paise: StrictInt | None = Field(default=None, ge=0)
    cost_paise: StrictInt | None = Field(default=None, ge=0)
    quantity_on_hand: StrictInt | None = Field(default=None, ge=0)
    description: str | None = Field(default=None, max_length=2000)
    attributes: dict[str, Any] | None = None
    active: bool | None = None


__all__ = [
    "AddItemRequest",
    "CartCreateRequest",
    "CartItemOut",
    "CartOut",
    "CheckoutRequest",
    "OrderLineOut",
    "OrderOut",
    "ProductCreateRequest",
    "ProductUpdateRequest",
]
