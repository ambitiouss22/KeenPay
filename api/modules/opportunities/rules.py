"""The rules that turn a catalogue and a cart into suggestions.

Pure functions over plain dicts. No I/O, no clock, no randomness - the same
inputs produce the same suggestions, in the same order, with the same ids, on
every machine and every run. That is not a nicety here: the identity of a
suggestion is derived from its content, so a rule that returned results in
dictionary order would produce different rows on different runs and the store
would fill with near-duplicates.

**What a suggestion may and may not contain.** It names a sku, carries the
catalogue's own price for it, and scores how well it fits. It carries no
discount, no price override and no budget reference. That boundary is the whole
reason this module is separate from ``modules.campaigns``: a suggestion is an
idea, and an idea must not be able to move money. Discounting a suggested item
means reserving campaign budget through the campaign path, which has its own
permission, its own hard cap and its own ledger.

**On the scoring curves.** Both are simple, bounded, monotone-where-it-matters
heuristics, chosen so that the ranking is explainable in one sentence and stable
under small catalogue changes. They are not a model, and nothing downstream may
treat a score as a probability. What the rest of the system depends on is that
scoring is deterministic and confined to ``[0, 1]``; the shape of the curve is
free to improve without any other component noticing.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Any

#: The two kinds of growth suggestion. A closed set: an unknown kind from an AI
#: hint is rejected rather than stored, so the vocabulary cannot be widened by
#: whatever a language model happened to emit.
UPSELL = "upsell"
CROSS_SELL = "cross_sell"
KINDS: tuple[str, ...] = (UPSELL, CROSS_SELL)

#: An upsell must cost more than the item it replaces, but not absurdly more.
#: Beyond this the suggestion stops being an upsell and becomes a different
#: purchase, which is not a thing a shopper accepts at checkout.
MAX_UPLIFT_RATIO = Decimal("2.0")

#: The price ratio a cross-sell is scored against: an add-on tends to cost a
#: fraction of the item it accompanies. Suggestions further from this in either
#: direction score lower, and past twice the distance they score zero.
CROSS_SELL_TARGET_RATIO = Decimal("0.30")

#: When no cart is named, the rules run against the merchant's highest-priced
#: active products instead. Bounded so a large catalogue does not turn one
#: request into thousands of rows.
DEFAULT_ANCHOR_COUNT = 3

#: Namespace for derived ids. Fixed forever: changing it re-issues every
#: opportunity in the system under a new id, which is exactly the duplication
#: deriving ids was meant to prevent.
_ID_NAMESPACE = uuid.UUID("6ba7b811-9dad-11d1-80b4-00c04fd430c8")

_QUANTUM = Decimal("0.0001")


@dataclass(frozen=True)
class Anchor:
    """The item a suggestion is made *about*."""

    sku: str
    unit_price_paise: int
    family: str


@dataclass(frozen=True)
class Suggestion:
    """One growth idea. Deliberately carries no money authority.

    ``list_price_paise`` is the catalogue's price, copied so a reader does not
    have to look it up again. It is not a quoted price and not an offer - see
    the module docstring.
    """

    kind: str
    sku: str
    name: str
    list_price_paise: int
    score: Decimal
    anchor_sku: str
    rationale: str

    def sort_key(self) -> tuple[Decimal, str, str]:
        """Total order. Score alone leaves ties to iteration order."""
        return (-self.score, self.kind, self.sku)

    def payload(self, *, merchant_id: str, subject_id: str) -> dict[str, Any]:
        return {
            "merchant_id": merchant_id,
            "subject_id": subject_id,
            "sku": self.sku,
            "name": self.name,
            "list_price_paise": self.list_price_paise,
            "anchor_sku": self.anchor_sku,
            "rationale": self.rationale,
            "fingerprint": fingerprint(
                merchant_id=merchant_id,
                kind=self.kind,
                subject_id=subject_id,
                sku=self.sku,
            ),
        }


def fingerprint(*, merchant_id: str, kind: str, subject_id: str, sku: str) -> str:
    """What makes two suggestions the same suggestion.

    Deliberately excludes price and score. A catalogue price change does not make
    "upsell this cart to the large size" a *different* idea, and treating it as
    one would spawn a fresh row every time a merchant edited a price - each of
    them needing to be dismissed separately.
    """
    return f"keenpay:opportunity:v1:{merchant_id}:{kind}:{subject_id}:{sku}"


def opportunity_id(fingerprint_value: str) -> str:
    """A stable uuid for a fingerprint.

    Derived rather than allocated, which is what lets the store insert-or-ignore
    instead of having to search for an equivalent row before every write.
    """
    return str(uuid.uuid5(_ID_NAMESPACE, fingerprint_value))


def family_of(product: Mapping[str, Any]) -> str:
    """Which group of variants a product belongs to.

    An explicit ``attributes.family`` wins. Failing that, the sku up to its last
    separator: ``HOODIE-NAVY-M`` and ``HOODIE-NAVY-L`` are the same garment in two
    sizes, and a merchant who names skus that way should not have to annotate
    every row to get sensible upsells. A sku with no separator is its own family,
    so an unstructured catalogue produces no upsells rather than nonsense ones.
    """
    attributes = product.get("attributes") or {}
    declared = attributes.get("family") if isinstance(attributes, Mapping) else None
    if isinstance(declared, str) and declared.strip():
        return declared.strip()

    sku = str(product.get("sku", ""))
    head, sep, _tail = sku.rpartition("-")
    return head if sep and head else sku


def sellable(product: Mapping[str, Any]) -> bool:
    """Whether a product may be suggested at all.

    Inactive or out-of-stock items are excluded here rather than filtered later.
    Suggesting something a merchant cannot sell wastes the suggestion and, worse,
    teaches them that the list is not worth reading.
    """
    if not product.get("active", True):
        return False
    available = product.get("quantity_available")
    if available is None:
        available = product.get("quantity_on_hand", 0)
    return int(available or 0) > 0


def anchors_from_cart(cart_items: Iterable[Mapping[str, Any]]) -> list[Anchor]:
    """The cart's lines, in a stable order, as the things to suggest around."""
    anchors = [
        Anchor(
            sku=str(item["sku"]),
            unit_price_paise=int(item["unit_price_paise"]),
            family=family_of(item),
        )
        for item in cart_items
    ]
    anchors.sort(key=lambda a: (-a.unit_price_paise, a.sku))
    return anchors


def anchors_from_catalog(
    catalog: Sequence[Mapping[str, Any]], *, count: int = DEFAULT_ANCHOR_COUNT
) -> list[Anchor]:
    """Fallback anchors when no cart is named.

    The highest-priced sellable products, which are the ones whose upsells and
    add-ons are worth a merchant's attention. Ties break on sku so the choice is
    reproducible.
    """
    ranked = sorted(
        (p for p in catalog if sellable(p)),
        key=lambda p: (-int(p["list_price_paise"]), str(p["sku"])),
    )
    return [
        Anchor(
            sku=str(p["sku"]),
            unit_price_paise=int(p["list_price_paise"]),
            family=family_of(p),
        )
        for p in ranked[:count]
    ]


def score_upsell(anchor: Anchor, candidate: Mapping[str, Any]) -> Decimal | None:
    """Rank a candidate as a step up from ``anchor``, or reject it.

    ``None`` means "not an upsell", and the reasons are all disqualifying rather
    than merely unattractive: same item, same or lower price, different family,
    or more than twice the price. Everything that survives is scored so that the
    *nearest* tier up ranks highest - the next size, not the flagship - because
    the small step is the one a shopper actually takes.
    """
    if anchor.unit_price_paise <= 0:
        return None
    if str(candidate.get("sku")) == anchor.sku:
        return None
    if family_of(candidate) != anchor.family:
        return None

    price = int(candidate["list_price_paise"])
    if price <= anchor.unit_price_paise:
        return None

    uplift = (Decimal(price) - Decimal(anchor.unit_price_paise)) / Decimal(
        anchor.unit_price_paise
    )
    if uplift > MAX_UPLIFT_RATIO:
        return None

    # 1 / (1 + uplift): bounded in (0, 1], falls smoothly as the step widens.
    return _quantise(Decimal(1) / (Decimal(1) + uplift))


def score_cross_sell(anchor: Anchor, candidate: Mapping[str, Any]) -> Decimal | None:
    """Rank a candidate as an addition to ``anchor``, or reject it.

    A cross-sell must be something else - a different family, not merely a
    different sku, or the suggestion is an upsell wearing the wrong label. Price
    is scored against ``CROSS_SELL_TARGET_RATIO``: a token-priced item and one
    that costs as much as the anchor both score zero, and an add-on priced near
    a third of the anchor scores highest.
    """
    if anchor.unit_price_paise <= 0:
        return None
    if str(candidate.get("sku")) == anchor.sku:
        return None
    if family_of(candidate) == anchor.family:
        return None

    ratio = Decimal(int(candidate["list_price_paise"])) / Decimal(anchor.unit_price_paise)
    distance = abs(ratio - CROSS_SELL_TARGET_RATIO) / CROSS_SELL_TARGET_RATIO
    if distance >= 1:
        return None

    score = _quantise(Decimal(1) - distance)
    # A score that rounds to zero is refused rather than stored. A near-free
    # item sits just inside the distance bound but is not a suggestion anybody
    # benefits from seeing, and a zero-scored row would sort last forever while
    # still occupying the list.
    return score if score > 0 else None


def score_candidate(
    kind: str, anchor: Anchor, candidate: Mapping[str, Any]
) -> Decimal | None:
    """Dispatch to the rule for ``kind``. Unknown kinds score nothing."""
    if not sellable(candidate):
        return None
    if kind == UPSELL:
        return score_upsell(anchor, candidate)
    if kind == CROSS_SELL:
        return score_cross_sell(anchor, candidate)
    return None


def _rationale(kind: str, anchor: Anchor) -> str:
    if kind == UPSELL:
        return f"a step up from {anchor.sku} in the same range"
    return f"commonly bought alongside {anchor.sku}"


def generate(
    *,
    catalog: Sequence[Mapping[str, Any]],
    anchors: Sequence[Anchor],
    kinds: Sequence[str] = KINDS,
    limit: int = 10,
) -> list[Suggestion]:
    """Every suggestion the rules produce, best first, capped at ``limit``.

    One suggestion per (kind, sku): a candidate that fits two anchors is kept
    once, at its best score, attributed to the anchor that scored it. Keeping
    both would show a merchant the same idea twice and let a popular candidate
    crowd out everything else.
    """
    best: dict[tuple[str, str], Suggestion] = {}

    for kind in kinds:
        if kind not in KINDS:
            continue
        for anchor in anchors:
            for candidate in catalog:
                score = score_candidate(kind, anchor, candidate)
                if score is None:
                    continue
                sku = str(candidate["sku"])
                key = (kind, sku)
                suggestion = Suggestion(
                    kind=kind,
                    sku=sku,
                    name=str(candidate.get("name") or sku),
                    list_price_paise=int(candidate["list_price_paise"]),
                    score=score,
                    anchor_sku=anchor.sku,
                    rationale=_rationale(kind, anchor),
                )
                incumbent = best.get(key)
                # Strictly greater, so an equal score keeps the first anchor -
                # and anchors arrive in a defined order, which makes the choice
                # reproducible rather than dependent on catalogue ordering.
                if incumbent is None or suggestion.score > incumbent.score:
                    best[key] = suggestion

    ranked = sorted(best.values(), key=Suggestion.sort_key)
    return ranked[:limit]


def _quantise(value: Decimal) -> Decimal:
    """Four decimal places: what the ``score`` column stores.

    Rounded at the point of computation rather than on write, so the number a
    caller sorts by is the number that was stored. Rounding later would let two
    scores compare unequal in memory and equal in the database.
    """
    return value.quantize(_QUANTUM, rounding=ROUND_HALF_EVEN)


__all__ = [
    "CROSS_SELL",
    "CROSS_SELL_TARGET_RATIO",
    "DEFAULT_ANCHOR_COUNT",
    "KINDS",
    "MAX_UPLIFT_RATIO",
    "UPSELL",
    "Anchor",
    "Suggestion",
    "anchors_from_cart",
    "anchors_from_catalog",
    "family_of",
    "fingerprint",
    "generate",
    "opportunity_id",
    "score_candidate",
    "score_cross_sell",
    "score_upsell",
    "sellable",
]
