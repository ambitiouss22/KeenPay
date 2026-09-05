"""Provider event ingestion: verify, dedupe, then act.

The order of those three is the whole design, and it is not negotiable:

1. **Verify before parsing.** The endpoint is public by necessity — anyone on
   the internet can post to it. Parsing first means arbitrary bytes reach the
   JSON decoder and the handler logic before anything has established the
   sender is the provider.

2. **Dedupe before acting.** A provider retries until it gets a 2xx, and is
   free to redeliver an event it already delivered. Without a claim on the
   event id, a redelivered ``payment_link.paid`` marks an order paid twice and
   a redelivered refund event double-counts money.

3. **Compare the amount before believing it.** The event says what was paid;
   the order says what was owed. A mismatch is not something to reconcile
   quietly — it becomes ``payment_disputed`` and a human looks at it. Trusting
   the event's amount is how a forged or replayed event with a smaller total
   settles a larger order.

The HTTP status codes are chosen for what they make the *sender* do. A bad
signature is 401, never 200: to the provider a 2xx means "delivered, stop
retrying", so answering 200 to an event we rejected turns a mistyped secret
into silently discarded payments — a failure that looks exactly like success.
Conversely an event we understood and deliberately did nothing about is a 200,
because retrying it would not change the outcome.

This module holds no FastAPI types. The route is a thin adapter over it, so the
rules above can be tested directly rather than through a client.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from typing import Any

import structlog

from config.settings import get_settings
from modules.audit.ledger import AuditLedger
from repositories.orders import OrderRepository
from repositories.webhooks import WebhookRepository

logger = structlog.get_logger(__name__)

#: Bodies larger than this are refused unread. Razorpay events are a few
#: kilobytes; anything approaching a megabyte is either a bug or an attempt to
#: make the process do expensive work for free.
MAX_BODY_BYTES = 1_048_576

#: How far an event's own timestamp may sit from now. A captured signature
#: replayed days later is still a valid signature — freshness is what stops it
#: from also being a valid event. Events that carry no timestamp are not
#: rejected: the provider does not always send one, and refusing those would
#: drop legitimate traffic.
MAX_CLOCK_SKEW_SECONDS = 300

#: Events we act on. Anything else is acknowledged and ignored, which is the
#: correct answer for an event type we have no rule for: 200 stops the retries,
#: and the raw body is stored either way.
HANDLED_EVENTS = frozenset(
    {
        "payment_link.paid",
        "payment.captured",
        "payment_link.expired",
        "payment.failed",
    }
)


class WebhookVerdict(str, Enum):
    """What happened to one delivery."""

    PROCESSED = "processed"
    DUPLICATE = "duplicate"
    IGNORED = "ignored"
    ORDER_NOT_FOUND = "order_not_found"
    AMOUNT_MISMATCH = "amount_mismatch"
    INVALID_SIGNATURE = "invalid_signature"
    MALFORMED = "malformed"
    STALE = "stale"
    TOO_LARGE = "too_large"


#: Verdict -> HTTP status. Kept as one table so the "what does this make the
#: sender do" decision is visible in a single place instead of scattered
#: across branches.
_STATUS: dict[WebhookVerdict, int] = {
    WebhookVerdict.PROCESSED: 200,
    WebhookVerdict.DUPLICATE: 200,
    WebhookVerdict.IGNORED: 200,
    WebhookVerdict.ORDER_NOT_FOUND: 200,
    # 409, not 200: a mismatch is unresolved, and the response should say so
    # loudly enough to show up in the provider's dashboard.
    WebhookVerdict.AMOUNT_MISMATCH: 409,
    WebhookVerdict.INVALID_SIGNATURE: 401,
    WebhookVerdict.MALFORMED: 400,
    WebhookVerdict.STALE: 400,
    WebhookVerdict.TOO_LARGE: 413,
}

_ERROR_CODES: dict[WebhookVerdict, str] = {
    WebhookVerdict.AMOUNT_MISMATCH: "WEBHOOK_AMOUNT_MISMATCH",
    # Kept as INVALID_SIGNATURE rather than renamed: it is the code this
    # endpoint has always returned, and a client matching on it should not
    # break because the handler moved into a module.
    WebhookVerdict.INVALID_SIGNATURE: "INVALID_SIGNATURE",
    WebhookVerdict.MALFORMED: "INVALID_PAYLOAD",
    WebhookVerdict.STALE: "WEBHOOK_STALE",
    WebhookVerdict.TOO_LARGE: "WEBHOOK_TOO_LARGE",
}


@dataclass(frozen=True)
class WebhookOutcome:
    """The result of one delivery, ready to become a response."""

    verdict: WebhookVerdict
    message: str = ""
    event_id: str = ""
    order_id: str | None = None

    @property
    def status_code(self) -> int:
        return _STATUS[self.verdict]

    @property
    def ok(self) -> bool:
        """Whether the response body is an acknowledgement rather than an error."""
        return self.status_code < 400

    def body(self) -> dict[str, Any]:
        """The JSON a caller should receive.

        Successes use the acknowledgement shape the provider expects; failures
        use the same error envelope as the rest of the API, so a client never
        has to parse two contracts.
        """
        if self.ok:
            return {
                "received": True,
                "status": self.verdict.value,
                "event_id": self.event_id,
                "order_id": self.order_id,
            }
        return {
            "error": {
                "code": _ERROR_CODES.get(self.verdict, "WEBHOOK_REJECTED"),
                "message": self.message or self.verdict.value.replace("_", " "),
            }
        }


def verify_signature(body: bytes, signature: str | None, secret: str | None) -> bool:
    """Constant-time HMAC-SHA256 over the raw request bytes.

    The raw bytes matter. Re-serializing the parsed JSON changes key order and
    whitespace, and the signature stops matching — so the check has to happen
    on what arrived, not on what we made of it.

    An empty secret refuses everything. The tempting alternative, treating "no
    secret configured" as "no checking required", turns a misconfigured
    deployment into an open endpoint that accepts forged payment events.
    """
    if not secret or not signature:
        return False
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def _event_timestamp(payload: dict[str, Any]) -> datetime | None:
    """Read the event's own creation time, if it carries one."""
    raw = payload.get("created_at")
    if isinstance(raw, bool) or not isinstance(raw, int):
        return None
    try:
        return datetime.fromtimestamp(raw, tz=UTC)
    except (OverflowError, OSError, ValueError):
        return None


def _entity(payload: dict[str, Any], name: str) -> dict[str, Any]:
    """Pull one entity out of the provider's nested envelope, safely.

    Every level is checked because none of them is ours. A payload that puts a
    string where an object belongs must be a 400, not a ``AttributeError`` in
    the middle of the handler.
    """
    container = payload.get("payload")
    if not isinstance(container, dict):
        return {}
    wrapper = container.get(name)
    if not isinstance(wrapper, dict):
        return {}
    entity = wrapper.get("entity")
    return entity if isinstance(entity, dict) else {}


def _amount_of(entity: dict[str, Any]) -> int | None:
    """The integer paise amount an entity claims, or None if it claims none."""
    raw = entity.get("amount")
    if isinstance(raw, bool) or not isinstance(raw, int):
        return None
    return raw


class WebhookProcessor:
    """Verifies, deduplicates and applies one provider event."""

    def __init__(
        self,
        *,
        orders: OrderRepository | None = None,
        events: WebhookRepository | None = None,
        ledger: AuditLedger | None = None,
        secret: str | None = None,
    ) -> None:
        self._orders = orders or OrderRepository()
        self._events = events or WebhookRepository()
        self._ledger = ledger or AuditLedger()
        self._secret_override = secret

    # --- entry point --------------------------------------------------------

    async def handle(
        self,
        raw_body: bytes,
        signature: str | None,
        *,
        header_event_id: str | None = None,
    ) -> WebhookOutcome:
        """Run one delivery through verify -> dedupe -> act."""
        if len(raw_body) > MAX_BODY_BYTES:
            return WebhookOutcome(
                WebhookVerdict.TOO_LARGE,
                f"Body exceeds {MAX_BODY_BYTES} bytes",
            )

        if not self._signature_ok(raw_body, signature):
            logger.warning("webhook_bad_signature", has_signature=bool(signature))
            return WebhookOutcome(
                WebhookVerdict.INVALID_SIGNATURE, "Bad webhook signature"
            )

        payload = self._parse(raw_body)
        if payload is None:
            logger.warning("webhook_unparseable_body", bytes=len(raw_body))
            return WebhookOutcome(
                WebhookVerdict.MALFORMED, "Body must be a JSON object"
            )

        sent_at = _event_timestamp(payload)
        if sent_at is not None:
            skew = abs((datetime.now(UTC) - sent_at).total_seconds())
            if skew > MAX_CLOCK_SKEW_SECONDS:
                logger.warning("webhook_stale", skew_seconds=int(skew))
                return WebhookOutcome(
                    WebhookVerdict.STALE,
                    f"Event is {int(skew)}s from now; the limit is "
                    f"{MAX_CLOCK_SKEW_SECONDS}s",
                )

        event_id = self._event_id(payload, header_event_id)
        if not event_id:
            # Without an id the event cannot be deduplicated, and an event that
            # cannot be deduplicated will be applied again on every retry.
            return WebhookOutcome(
                WebhookVerdict.MALFORMED, "Event carries no id to deduplicate on"
            )

        event_type = payload.get("event")
        event_type = event_type if isinstance(event_type, str) else ""

        claimed = await self._events.claim(
            event_id,
            event_type=event_type,
            payload=payload,
            signature_valid=True,
            raw_body=raw_body,
        )
        if not claimed:
            logger.info("webhook_duplicate", event_id=event_id, event_type=event_type)
            return WebhookOutcome(WebhookVerdict.DUPLICATE, event_id=event_id)

        outcome = await self._apply(event_type, payload, event_id)
        await self._events.mark_processed(
            event_id,
            result={"verdict": outcome.verdict.value, "message": outcome.message},
            order_id=outcome.order_id,
        )
        return outcome

    async def reapply(self, event: dict[str, Any]) -> WebhookOutcome:
        """Re-run the handler for an event already verified and already claimed.

        The retry sweeper's entry point. It deliberately skips verification and
        deduplication: the stored row exists only because the signature passed
        and the claim succeeded, and pushing it back through :meth:`handle`
        would be refused by its own dedupe — correct behaviour there, useless
        here.
        """
        return await self._apply(
            event.get("event_type") or "",
            event.get("payload") or {},
            event.get("event_id") or "",
        )

    # --- verification -------------------------------------------------------

    def _signature_ok(self, raw_body: bytes, signature: str | None) -> bool:
        """Check the signature, honouring the mock-mode shortcut.

        The shortcut exists so local development does not need a real webhook
        secret. It is keyed off the same flag that swaps in the mock provider,
        so a deployment that talks to real Razorpay can never be in it.
        """
        if self._secret_override is not None:
            return verify_signature(raw_body, signature, self._secret_override)

        settings = get_settings()
        if settings.razorpay_mock:
            return True
        return verify_signature(raw_body, signature, settings.razorpay_webhook_secret)

    @staticmethod
    def _parse(raw_body: bytes) -> dict[str, Any] | None:
        """Decode the body, or None if it is not a JSON object."""
        try:
            payload = json.loads(raw_body)
        except (ValueError, UnicodeDecodeError):
            return None
        return payload if isinstance(payload, dict) else None

    @staticmethod
    def _event_id(payload: dict[str, Any], header_event_id: str | None) -> str:
        """The id this event is deduplicated on.

        The header wins when present because it is what the provider documents
        as the delivery id; the body fields are the fallback for senders and
        fixtures that only carry one there.
        """
        for candidate in (header_event_id, payload.get("event_id"), payload.get("id")):
            if isinstance(candidate, str) and candidate:
                return candidate
        return ""

    # --- handlers -----------------------------------------------------------

    async def _apply(
        self, event_type: str, payload: dict[str, Any], event_id: str
    ) -> WebhookOutcome:
        """Dispatch a verified, first-seen event."""
        if event_type not in HANDLED_EVENTS:
            logger.info("webhook_ignored", event_id=event_id, event_type=event_type)
            return WebhookOutcome(WebhookVerdict.IGNORED, event_id=event_id)

        if event_type in ("payment_link.paid", "payment.captured"):
            return await self._on_paid(payload, event_id, event_type)
        if event_type == "payment_link.expired":
            return await self._on_expired(payload, event_id)
        return await self._on_failed(payload, event_id)

    async def _on_paid(
        self, payload: dict[str, Any], event_id: str, event_type: str
    ) -> WebhookOutcome:
        """Settle an order, but only for exactly the amount it is owed."""
        link = _entity(payload, "payment_link")
        payment = _entity(payload, "payment")

        order = await self._find_order(link, payment)
        if not order:
            # 200, not 404. The event is well formed and correctly signed; we
            # simply have no order for it, and asking the provider to retry
            # forever will not produce one.
            logger.info("webhook_order_not_found", event_id=event_id, event_type=event_type)
            return WebhookOutcome(WebhookVerdict.ORDER_NOT_FOUND, event_id=event_id)

        # The payment entity is the authority on what was actually paid; the
        # link entity is the fallback for events that carry only one of them.
        paid_paise = _amount_of(payment)
        if paid_paise is None:
            paid_paise = _amount_of(link)

        owed_paise = int(order.get("final_amount_paise") or 0)
        order_id = order["id"]
        merchant_id = order.get("merchant_id", "")

        if paid_paise is None or paid_paise != owed_paise:
            await self._ledger.append(
                merchant_id=merchant_id,
                entity_type="order",
                entity_id=order_id,
                actor="webhook",
                action="PAYMENT_DISPUTED",
                payload={
                    "event_id": event_id,
                    "event": event_type,
                    "expected_paise": owed_paise,
                    "received_paise": paid_paise,
                },
                correlation_id=event_id,
            )
            await self._mark_disputed(order_id)
            logger.error(
                "webhook_amount_mismatch",
                event_id=event_id,
                order_id=order_id,
                expected=owed_paise,
                received=paid_paise,
            )
            return WebhookOutcome(
                WebhookVerdict.AMOUNT_MISMATCH,
                f"Event settles {paid_paise} against an order owed {owed_paise}",
                event_id=event_id,
                order_id=order_id,
            )

        provider_payment_id = payment.get("id") or link.get("id") or "pay_unknown"
        await self._orders.mark_paid(order_id, payment_id=str(provider_payment_id))
        await self._ledger.append(
            merchant_id=merchant_id,
            entity_type="order",
            entity_id=order_id,
            actor="webhook",
            action="PAYMENT_CAPTURED",
            payload={
                "event_id": event_id,
                "event": event_type,
                "provider_payment_id": str(provider_payment_id),
                "amount_paise": paid_paise,
            },
            correlation_id=event_id,
        )
        logger.info("webhook_order_paid", event_id=event_id, order_id=order_id)
        return WebhookOutcome(
            WebhookVerdict.PROCESSED, event_id=event_id, order_id=order_id
        )

    async def _on_expired(self, payload: dict[str, Any], event_id: str) -> WebhookOutcome:
        """Record that a link lapsed. A paid order is never un-paid by this."""
        link = _entity(payload, "payment_link")
        order = await self._find_order(link, {})
        if not order:
            return WebhookOutcome(WebhookVerdict.ORDER_NOT_FOUND, event_id=event_id)

        order_id = order["id"]
        if order.get("status") == "paid":
            # An expiry that arrives after a capture is out-of-order delivery,
            # not a reversal. Reverting here would unpay a settled order.
            logger.info("webhook_expiry_after_payment", event_id=event_id, order_id=order_id)
            return WebhookOutcome(WebhookVerdict.IGNORED, event_id=event_id, order_id=order_id)

        await self._set_status(order_id, "expired")
        await self._ledger.append(
            merchant_id=order.get("merchant_id", ""),
            entity_type="order",
            entity_id=order_id,
            actor="webhook",
            action="PAYMENT_LINK_EXPIRED",
            payload={"event_id": event_id},
            correlation_id=event_id,
        )
        return WebhookOutcome(WebhookVerdict.PROCESSED, event_id=event_id, order_id=order_id)

    async def _on_failed(self, payload: dict[str, Any], event_id: str) -> WebhookOutcome:
        """Record a failed attempt and leave the order where it is.

        A failed attempt is not a failed order: the shopper can try again on
        the same link, so moving the order to a terminal state here would
        cancel a sale that is still live.
        """
        payment = _entity(payload, "payment")
        link = _entity(payload, "payment_link")
        order = await self._find_order(link, payment)
        if not order:
            return WebhookOutcome(WebhookVerdict.ORDER_NOT_FOUND, event_id=event_id)

        order_id = order["id"]
        await self._ledger.append(
            merchant_id=order.get("merchant_id", ""),
            entity_type="order",
            entity_id=order_id,
            actor="webhook",
            action="PAYMENT_ATTEMPT_FAILED",
            payload={
                "event_id": event_id,
                "provider_payment_id": payment.get("id"),
                "error_code": payment.get("error_code"),
                "error_description": payment.get("error_description"),
            },
            correlation_id=event_id,
        )
        return WebhookOutcome(WebhookVerdict.PROCESSED, event_id=event_id, order_id=order_id)

    # --- order lookup -------------------------------------------------------

    async def _find_order(
        self, link: dict[str, Any], payment: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Resolve the order this event belongs to.

        The payment link id is the primary key into our own records. The
        ``reference_id`` fallback covers events for payments created through
        the payment engine rather than a hosted link.
        """
        link_id = link.get("id")
        if isinstance(link_id, str) and link_id:
            order = await self._orders.get_by_payment_link(link_id)
            if order:
                return order

        for key in ("reference_id", "receipt", "order_id", "notes"):
            candidate = payment.get(key) or link.get(key)
            if isinstance(candidate, dict):
                candidate = candidate.get("order_id")
            if isinstance(candidate, str) and candidate.startswith("ord_"):
                order = await self._orders.get(candidate)
                if order:
                    return order
        return None

    async def _mark_disputed(self, order_id: str) -> None:
        await self._set_status(order_id, "payment_disputed")

    async def _set_status(self, order_id: str, status: str) -> None:
        """Move an order to a non-paid status.

        The order repository owns ``mark_paid`` and nothing else today, so a
        dedicated setter is used when it exists and the in-memory record is
        updated directly when it does not. A missing setter must not stop a
        dispute from being recorded — the ledger entry above is the part that
        matters, and it is already written.
        """
        setter = getattr(self._orders, "set_status", None)
        if setter is not None:
            await setter(order_id, status)
            return
        order = await self._orders.get(order_id)
        if order is not None:
            order["status"] = status


__all__ = [
    "HANDLED_EVENTS",
    "MAX_BODY_BYTES",
    "MAX_CLOCK_SKEW_SECONDS",
    "WebhookOutcome",
    "WebhookProcessor",
    "WebhookVerdict",
    "verify_signature",
]
