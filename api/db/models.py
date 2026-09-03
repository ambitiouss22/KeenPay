"""Canonical SQLAlchemy models for KeenPay.

This module is the source of truth for the database shape. ``db/migrations/
0001_initial.sql`` is written to match it exactly; if the two ever disagree,
this file wins and the migration is the bug.

IMPORTANT — import style. Always import this module as ``db.models`` (bare),
never as ``api.db.models``. Both resolve to this file because pytest puts the
repo root *and* ``api/`` on sys.path, but they create two distinct module
objects, and the second one to load re-registers every table on a fresh
declarative registry. SQLAlchemy raises ``InvalidRequestError: Table 'orders'
is already defined`` when that happens. One spelling, everywhere.

Tenancy. Every tenant-owned table carries ``tenant_id UUID NOT NULL`` and is
protected by a row-level security policy of the form::

    USING (tenant_id = current_setting('app.tenant_id')::uuid)

``merchant_id`` is kept alongside it. It is the human-readable slug the existing
v1 code paths still filter on; ``tenant_id`` is the key the database enforces.
The two are kept in step through ``tenants.slug``.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    ARRAY,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import INET, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Declarative base. ``Base.metadata`` is what the migration mirrors."""


# -----------------------------------------------------------------------------
# Enums
#
# create_type=False: the migration owns CREATE TYPE. If SQLAlchemy also tried to
# emit it, metadata.create_all() against an already-migrated database would fail
# on the duplicate type.
# -----------------------------------------------------------------------------

OrderStatus = Enum(
    "pending", "paid", "expired", "cancelled", "payment_disputed",
    name="order_status", create_type=False,
)

NegotiationSessionStatus = Enum(
    "active", "negotiating", "awaiting_confirmation", "payment_pending",
    "paid", "escalated", "closed",
    name="negotiation_session_status", create_type=False,
)

GuardrailOutcome = Enum(
    "APPROVED", "REJECTED", "ESCALATED",
    name="guardrail_outcome", create_type=False,
)

AuditActor = Enum(
    "agent", "policy_engine", "user", "system", "webhook", "human",
    name="audit_actor", create_type=False,
)

UserRole = Enum(
    "shopper", "support_agent", "manager", "admin", "service",
    name="user_role", create_type=False,
)

AuthEventType = Enum(
    "login_success", "login_failed", "token_issued", "token_refreshed",
    "token_revoked", "api_key_used", "password_changed", "account_locked",
    name="auth_event_type", create_type=False,
)

PaymentStatus = Enum(
    "created", "authorized", "captured", "failed", "refunded",
    name="payment_status", create_type=False,
)


# -----------------------------------------------------------------------------
# Column helpers
# -----------------------------------------------------------------------------

def _pk_uuid() -> Mapped[uuid.UUID]:
    return mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )


def _created_at() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


def _updated_at() -> Mapped[datetime]:
    return mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


def tenant_fk() -> Mapped[uuid.UUID]:
    """``tenant_id`` foreign key. Present on every row-level-secured table.

    A model without this column is a model without a tenant policy. That should
    be obvious on sight, which is why the column is spelled out per table rather
    than injected by a base class.
    """
    return mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )


# -----------------------------------------------------------------------------
# Tenants — the root of the isolation model
# -----------------------------------------------------------------------------


class Tenant(Base):
    """One merchant organisation. Not itself tenant-scoped.

    ``slug`` is the legacy ``merchant_id`` value ('merchant_keen'), kept so the
    v1 code paths that filter on merchant_id keep working while the database
    enforces isolation on ``id``.
    """

    __tablename__ = "tenants"

    id: Mapped[uuid.UUID] = _pk_uuid()
    slug: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    settings: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()


# -----------------------------------------------------------------------------
# Auth
# -----------------------------------------------------------------------------


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("merchant_id", "email", name="users_email_merchant_unique"),
        Index("idx_users_tenant_role", "tenant_id", "role"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[uuid.UUID] = tenant_fk()
    merchant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    password_hash: Mapped[str | None] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(UserRole, nullable=False, server_default="shopper")
    display_name: Mapped[str | None] = mapped_column(String(255))
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failed_login_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"

    id: Mapped[uuid.UUID] = _pk_uuid()
    tenant_id: Mapped[uuid.UUID] = tenant_fk()
    user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    family_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, server_default=func.gen_random_uuid()
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    replaced_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("refresh_tokens.id")
    )
    user_agent: Mapped[str | None] = mapped_column(String(512))
    ip_address: Mapped[str | None] = mapped_column(INET)
    created_at: Mapped[datetime] = _created_at()


class ApiKey(Base):
    __tablename__ = "api_keys"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[uuid.UUID] = tenant_fk()
    merchant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    key_prefix: Mapped[str] = mapped_column(String(16), nullable=False)
    key_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    role: Mapped[str] = mapped_column(UserRole, nullable=False, server_default="service")
    scopes: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, server_default="{}")
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[str | None] = mapped_column(String(64), ForeignKey("users.id"))
    created_at: Mapped[datetime] = _created_at()
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuthAuditLog(Base):
    """Append-only. UPDATE and DELETE are blocked by a database trigger."""

    __tablename__ = "auth_audit_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    tenant_id: Mapped[uuid.UUID] = tenant_fk()
    merchant_id: Mapped[str | None] = mapped_column(String(64))
    event_type: Mapped[str] = mapped_column(AuthEventType, nullable=False)
    user_id: Mapped[str | None] = mapped_column(String(64), ForeignKey("users.id"))
    api_key_id: Mapped[str | None] = mapped_column(String(64), ForeignKey("api_keys.id"))
    ip_address: Mapped[str | None] = mapped_column(INET)
    user_agent: Mapped[str | None] = mapped_column(String(512))
    meta: Mapped[dict] = mapped_column("metadata", JSONB, nullable=False, server_default="{}")
    created_at: Mapped[datetime] = _created_at()


# -----------------------------------------------------------------------------
# Catalog
# -----------------------------------------------------------------------------


class Product(Base):
    """Catalog row. ``cost_paise`` feeds the minimum-margin policy rule.

    ``search_vector`` exists in the database as a generated tsvector column but
    is deliberately absent here: SQLAlchemy would try to write it on INSERT.
    Query it with raw SQL when full-text search is needed.
    """

    __tablename__ = "products"
    __table_args__ = (
        UniqueConstraint("merchant_id", "sku", name="products_sku_merchant_unique"),
        CheckConstraint(
            "quantity_reserved <= quantity_on_hand", name="products_reserved_lte_on_hand"
        ),
        CheckConstraint("list_price_paise >= 0", name="products_list_price_non_negative"),
        CheckConstraint("cost_paise >= 0", name="products_cost_non_negative"),
        Index("idx_products_tenant_active", "tenant_id", "active"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[uuid.UUID] = tenant_fk()
    merchant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    sku: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    list_price_paise: Mapped[int] = mapped_column(Integer, nullable=False)
    cost_paise: Mapped[int] = mapped_column(Integer, nullable=False)
    wholesale_paise: Mapped[int | None] = mapped_column(Integer)
    quantity_on_hand: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    quantity_reserved: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    attributes: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()


# -----------------------------------------------------------------------------
# Agentic checkout
# -----------------------------------------------------------------------------


class NegotiationSession(Base):
    __tablename__ = "negotiation_sessions"
    __table_args__ = (
        CheckConstraint("currency = 'INR'", name="negotiation_sessions_currency_inr"),
        Index("idx_negotiation_sessions_tenant_created", "tenant_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = _pk_uuid()
    tenant_id: Mapped[uuid.UUID] = tenant_fk()
    merchant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    user_id: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(
        NegotiationSessionStatus, nullable=False, server_default="active"
    )
    negotiation_round: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    offer_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")

    parsed_intent: Mapped[dict | None] = mapped_column(JSONB)
    search_results: Mapped[list] = mapped_column(JSONB, nullable=False, server_default="[]")
    selected_line_items: Mapped[list] = mapped_column(JSONB, nullable=False, server_default="[]")

    proposed_offer: Mapped[dict | None] = mapped_column(JSONB)
    approved_offer: Mapped[dict | None] = mapped_column(JSONB)

    guardrail_decision: Mapped[str | None] = mapped_column(GuardrailOutcome)
    guardrail_decision_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    guardrail_detail: Mapped[dict | None] = mapped_column(JSONB)
    rejection_reasons: Mapped[list] = mapped_column(JSONB, nullable=False, server_default="[]")

    user_confirmed_payment: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    user_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    final_amount_paise: Mapped[int | None] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, server_default="INR")

    anomaly_flags: Mapped[list] = mapped_column(JSONB, nullable=False, server_default="[]")
    security_block: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")

    langgraph_thread_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)

    meta: Mapped[dict] = mapped_column("metadata", JSONB, nullable=False, server_default="{}")
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class LangGraphCheckpoint(Base):
    __tablename__ = "langgraph_checkpoints"

    thread_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    checkpoint_ns: Mapped[str] = mapped_column(String(256), primary_key=True, server_default="")
    checkpoint_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[uuid.UUID] = tenant_fk()
    parent_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    checkpoint: Mapped[dict] = mapped_column(JSONB, nullable=False)
    meta: Mapped[dict] = mapped_column("metadata", JSONB, nullable=False, server_default="{}")
    created_at: Mapped[datetime] = _created_at()


# -----------------------------------------------------------------------------
# Commerce
# -----------------------------------------------------------------------------


class Order(Base):
    """Frozen purchase. Amounts and line items are immutable after insert."""

    __tablename__ = "orders"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="orders_idempotency_unique"),
        CheckConstraint("currency = 'INR'", name="orders_currency_inr"),
        CheckConstraint("final_amount_paise > 0", name="orders_final_amount_positive"),
        CheckConstraint("offer_version >= 1", name="orders_offer_version_min"),
        Index("idx_orders_tenant_status", "tenant_id", "status", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[uuid.UUID] = tenant_fk()
    merchant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("negotiation_sessions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    user_id: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(OrderStatus, nullable=False, server_default="pending")

    subtotal_paise: Mapped[int] = mapped_column(Integer, nullable=False)
    discount_amount_paise: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    final_amount_paise: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, server_default="INR")

    line_items: Mapped[list] = mapped_column(JSONB, nullable=False)

    guardrail_decision_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    offer_version: Mapped[int] = mapped_column(Integer, nullable=False)
    policy_version: Mapped[str] = mapped_column(String(32), nullable=False)

    razorpay_payment_link_id: Mapped[str | None] = mapped_column(String(64))
    razorpay_payment_link_url: Mapped[str | None] = mapped_column(Text)
    razorpay_payment_id: Mapped[str | None] = mapped_column(String(64))
    razorpay_order_id: Mapped[str | None] = mapped_column(String(64))
    payment_link_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)

    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class OrderItem(Base):
    """Normalised line item.

    ``orders.line_items`` JSONB stays the write path for v1; this table is the
    queryable projection later phases report from. Both are populated so neither
    has to be backfilled later.
    """

    __tablename__ = "order_items"

    id: Mapped[uuid.UUID] = _pk_uuid()
    tenant_id: Mapped[uuid.UUID] = tenant_fk()
    order_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, index=True
    )
    product_id: Mapped[str | None] = mapped_column(String(64), ForeignKey("products.id"))
    sku: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_price_paise: Mapped[int] = mapped_column(Integer, nullable=False)
    line_total_paise: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = _created_at()


class Cart(Base):
    __tablename__ = "carts"

    id: Mapped[uuid.UUID] = _pk_uuid()
    tenant_id: Mapped[uuid.UUID] = tenant_fk()
    session_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("negotiation_sessions.id", ondelete="CASCADE")
    )
    user_id: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="open")
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()


class CartItem(Base):
    __tablename__ = "cart_items"
    __table_args__ = (UniqueConstraint("cart_id", "sku", name="cart_items_cart_sku_unique"),)

    id: Mapped[uuid.UUID] = _pk_uuid()
    tenant_id: Mapped[uuid.UUID] = tenant_fk()
    cart_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("carts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    product_id: Mapped[str | None] = mapped_column(String(64), ForeignKey("products.id"))
    sku: Mapped[str] = mapped_column(String(64), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_price_paise: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = _created_at()


class InventoryHold(Base):
    __tablename__ = "inventory_holds"
    __table_args__ = (
        UniqueConstraint("session_id", "sku", name="inventory_holds_active_unique"),
    )

    id: Mapped[uuid.UUID] = _pk_uuid()
    tenant_id: Mapped[uuid.UUID] = tenant_fk()
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("negotiation_sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    product_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("products.id", ondelete="RESTRICT"), nullable=False
    )
    sku: Mapped[str] = mapped_column(String(64), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created_at()


# -----------------------------------------------------------------------------
# Payments
# -----------------------------------------------------------------------------


class Payment(Base):
    """Settled payment against an order. One row per successful capture."""

    __tablename__ = "payments"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="payments_idempotency_unique"),
        Index("idx_payments_tenant_order", "tenant_id", "order_id"),
    )

    id: Mapped[uuid.UUID] = _pk_uuid()
    tenant_id: Mapped[uuid.UUID] = tenant_fk()
    order_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("orders.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(PaymentStatus, nullable=False, server_default="created")
    amount_paise: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, server_default="INR")
    provider: Mapped[str] = mapped_column(String(32), nullable=False, server_default="razorpay")
    provider_payment_id: Mapped[str | None] = mapped_column(String(64))
    provider_order_id: Mapped[str | None] = mapped_column(String(64))
    method: Mapped[str | None] = mapped_column(String(32))
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    captured_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failure_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()


class PaymentAttempt(Base):
    """Every attempt, including the failures. Payments keeps only what stuck."""

    __tablename__ = "payment_attempts"

    id: Mapped[uuid.UUID] = _pk_uuid()
    tenant_id: Mapped[uuid.UUID] = tenant_fk()
    order_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, index=True
    )
    payment_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("payments.id", ondelete="SET NULL"), index=True
    )
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    amount_paise: Mapped[int] = mapped_column(BigInteger, nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_detail: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    created_at: Mapped[datetime] = _created_at()


class Authorization(Base):
    """Scoped, expiring permission for an agent to spend on a user's behalf."""

    __tablename__ = "authorizations"

    id: Mapped[uuid.UUID] = _pk_uuid()
    tenant_id: Mapped[uuid.UUID] = tenant_fk()
    session_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("negotiation_sessions.id", ondelete="CASCADE")
    )
    user_id: Mapped[str | None] = mapped_column(String(64))
    scope: Mapped[str] = mapped_column(String(64), nullable=False)
    max_amount_paise: Mapped[int] = mapped_column(BigInteger, nullable=False)
    consumed_amount_paise: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default="0"
    )
    granted_at: Mapped[datetime] = _created_at()
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    constraints: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")


class WebhookEvent(Base):
    __tablename__ = "webhook_events"
    __table_args__ = (UniqueConstraint("event_id", name="webhook_events_event_id_unique"),)

    id: Mapped[uuid.UUID] = _pk_uuid()
    tenant_id: Mapped[uuid.UUID] = tenant_fk()
    event_id: Mapped[str] = mapped_column(String(128), nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    signature_valid: Mapped[bool] = mapped_column(Boolean, nullable=False)
    processed: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    process_result: Mapped[dict | None] = mapped_column(JSONB)
    order_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("orders.id", ondelete="SET NULL")
    )
    received_at: Mapped[datetime] = _created_at()
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Reconciliation(Base):
    """Nightly sweep: what the provider says versus what we recorded."""

    __tablename__ = "reconciliation"

    id: Mapped[uuid.UUID] = _pk_uuid()
    tenant_id: Mapped[uuid.UUID] = tenant_fk()
    order_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("orders.id", ondelete="SET NULL"), index=True
    )
    payment_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("payments.id", ondelete="SET NULL"), index=True
    )
    provider_reference: Mapped[str | None] = mapped_column(String(128))
    expected_amount_paise: Mapped[int | None] = mapped_column(BigInteger)
    actual_amount_paise: Mapped[int | None] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default="pending")
    discrepancy: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created_at()


# -----------------------------------------------------------------------------
# Growth
# -----------------------------------------------------------------------------


class Campaign(Base):
    """Discount budget envelope.

    ``reserved_paise`` is moved only through ``CampaignRepository.atomic_reserve``,
    which relies on the check constraint below to make overspend impossible even
    under concurrent writers.
    """

    __tablename__ = "campaigns"
    __table_args__ = (
        CheckConstraint(
            "reserved_paise + spent_paise <= budget_paise", name="campaigns_budget_not_exceeded"
        ),
        Index("idx_campaigns_tenant_active", "tenant_id", "active"),
    )

    id: Mapped[uuid.UUID] = _pk_uuid()
    tenant_id: Mapped[uuid.UUID] = tenant_fk()
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    code: Mapped[str | None] = mapped_column(String(64))
    budget_paise: Mapped[int] = mapped_column(BigInteger, nullable=False)
    reserved_paise: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    spent_paise: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    max_discount_pct: Mapped[float | None] = mapped_column(Numeric(5, 2))
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    starts_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()


class BudgetLedger(Base):
    """Append-only double entry for campaign budget. Sums must reconcile."""

    __tablename__ = "budget_ledger"

    id: Mapped[uuid.UUID] = _pk_uuid()
    tenant_id: Mapped[uuid.UUID] = tenant_fk()
    campaign_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("campaigns.id", ondelete="RESTRICT"), nullable=False,
        index=True,
    )
    order_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("orders.id", ondelete="SET NULL")
    )
    entry_type: Mapped[str] = mapped_column(String(16), nullable=False)
    amount_paise: Mapped[int] = mapped_column(BigInteger, nullable=False)
    balance_after_paise: Mapped[int | None] = mapped_column(BigInteger)
    reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created_at()


class Opportunity(Base):
    __tablename__ = "opportunities"

    id: Mapped[uuid.UUID] = _pk_uuid()
    tenant_id: Mapped[uuid.UUID] = tenant_fk()
    session_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("negotiation_sessions.id", ondelete="CASCADE")
    )
    user_id: Mapped[str | None] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    score: Mapped[float | None] = mapped_column(Numeric(5, 4))
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    acted_on: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    created_at: Mapped[datetime] = _created_at()


# -----------------------------------------------------------------------------
# Audit and integrity
# -----------------------------------------------------------------------------


class AuditLog(Base):
    """Append-only ledger. UPDATE and DELETE blocked by trigger."""

    __tablename__ = "audit_logs"
    __table_args__ = (Index("idx_audit_logs_tenant_created", "tenant_id", "created_at"),)

    id: Mapped[uuid.UUID] = _pk_uuid()
    tenant_id: Mapped[uuid.UUID] = tenant_fk()
    merchant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    session_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("negotiation_sessions.id", ondelete="SET NULL")
    )
    order_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("orders.id", ondelete="SET NULL")
    )
    actor: Mapped[str] = mapped_column(AuditActor, nullable=False)
    action: Mapped[str] = mapped_column(String(128), nullable=False)
    decision_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    offer_version: Mapped[int | None] = mapped_column(Integer)
    idempotency_key: Mapped[str | None] = mapped_column(String(128))
    trace_event_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    input_snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    output_snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    trace_metadata: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    ip_address: Mapped[str | None] = mapped_column(INET)
    user_agent: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created_at()


class PassportRecord(Base):
    """Hash-chained transaction passport entry.

    ``prev_hash`` points at the previous record for the same tenant, so tampering
    with any row breaks every hash after it.
    """

    __tablename__ = "passport_records"
    __table_args__ = (
        UniqueConstraint("tenant_id", "sequence_number", name="passport_records_tenant_seq_unique"),
    )

    id: Mapped[uuid.UUID] = _pk_uuid()
    tenant_id: Mapped[uuid.UUID] = tenant_fk()
    order_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("orders.id", ondelete="SET NULL"), index=True
    )
    session_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("negotiation_sessions.id", ondelete="SET NULL")
    )
    sequence_number: Mapped[int] = mapped_column(BigInteger, nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    prev_hash: Mapped[str | None] = mapped_column(String(64))
    record_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = _created_at()


class EscalationTicket(Base):
    __tablename__ = "escalation_tickets"

    id: Mapped[uuid.UUID] = _pk_uuid()
    tenant_id: Mapped[uuid.UUID] = tenant_fk()
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("negotiation_sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    priority: Mapped[str] = mapped_column(String(4), nullable=False)
    reason_code: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="open")
    assigned_to: Mapped[str | None] = mapped_column(String(64))
    proposed_offer_snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False)
    policy_snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False)
    resolution: Mapped[str | None] = mapped_column(String(32))
    override_discount_pct: Mapped[float | None] = mapped_column(Numeric(5, 2))
    resolver_note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created_at()
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# -----------------------------------------------------------------------------
# Infrastructure
# -----------------------------------------------------------------------------


class Outbox(Base):
    """Transactional outbox. Rows are written in the same transaction as the
    state change they describe, then published by a worker. Removes the
    write-then-publish race where a crash loses the event.
    """

    __tablename__ = "outbox"

    id: Mapped[uuid.UUID] = _pk_uuid()
    tenant_id: Mapped[uuid.UUID] = tenant_fk()
    aggregate_type: Mapped[str] = mapped_column(String(64), nullable=False)
    aggregate_id: Mapped[str] = mapped_column(String(64), nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created_at()


class IdempotencyKey(Base):
    """Cached response for a replayed request.

    Unique on (tenant_id, key) rather than key alone: two tenants sending the
    same client-generated key must not collide.
    """

    __tablename__ = "idempotency_keys"
    __table_args__ = (
        UniqueConstraint("tenant_id", "key", name="idempotency_keys_tenant_key_unique"),
    )

    id: Mapped[uuid.UUID] = _pk_uuid()
    tenant_id: Mapped[uuid.UUID] = tenant_fk()
    key: Mapped[str] = mapped_column(String(128), nullable=False)
    endpoint: Mapped[str] = mapped_column(String(128), nullable=False)
    request_hash: Mapped[str | None] = mapped_column(String(64))
    response_status: Mapped[int | None] = mapped_column(Integer)
    response_body: Mapped[dict | None] = mapped_column(JSONB)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = _created_at()


class Event(Base):
    """Generic domain event stream for analytics and replay."""

    __tablename__ = "events"
    __table_args__ = (
        Index("idx_events_tenant_type_created", "tenant_id", "event_type", "created_at"),
    )

    id: Mapped[uuid.UUID] = _pk_uuid()
    tenant_id: Mapped[uuid.UUID] = tenant_fk()
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    subject_type: Mapped[str | None] = mapped_column(String(64))
    subject_id: Mapped[str | None] = mapped_column(String(64))
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    created_at: Mapped[datetime] = _created_at()


# -----------------------------------------------------------------------------
# Registry used by the RLS verification helpers and the migration test
# -----------------------------------------------------------------------------

#: Every table that must carry tenant_id and a row-level security policy.
TENANT_SCOPED_TABLES: tuple[str, ...] = tuple(
    sorted(
        table.name
        for table in Base.metadata.tables.values()
        if "tenant_id" in table.columns
    )
)

#: Tables intentionally outside tenant scope.
GLOBAL_TABLES: tuple[str, ...] = ("tenants",)

__all__ = [
    "ApiKey",
    "AuditLog",
    "AuthAuditLog",
    "Authorization",
    "Base",
    "BudgetLedger",
    "Campaign",
    "Cart",
    "CartItem",
    "EscalationTicket",
    "Event",
    "GLOBAL_TABLES",
    "IdempotencyKey",
    "InventoryHold",
    "LangGraphCheckpoint",
    "NegotiationSession",
    "Opportunity",
    "Order",
    "OrderItem",
    "Outbox",
    "PassportRecord",
    "Payment",
    "PaymentAttempt",
    "Product",
    "Reconciliation",
    "RefreshToken",
    "TENANT_SCOPED_TABLES",
    "Tenant",
    "User",
    "WebhookEvent",
]
