"""Hash-chained, append-only audit ledger.

An audit log you can edit proves nothing. The property this module provides is
narrower and more useful: every entry commits to the one before it, so altering
or removing any entry invalidates every hash after it. A reader who kept only
the head hash can detect that the history changed without holding the history.

Three deliberate choices:

**One chain per merchant.** A global chain would leak activity volume across
tenants — the sequence numbers alone tell one merchant how busy another is —
and would make a single merchant's export unverifiable without everyone else's
entries.

**The hash covers the link.** ``prev_hash`` is inside the hashed body, not
beside it. Hashing only the payload would let entries be reordered while every
individual hash still checked out.

**Serialization is canonical and pinned.** Sorted keys, no whitespace, ISO-8601
in UTC. A hash chain is only reproducible if two runs serialize the same entry
to the same bytes, so the format is part of the contract, not an implementation
detail: changing it invalidates every chain ever written.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

#: What the first entry in a chain points at. A literal rather than ``None``
#: so verification has no special case for the head, and so an entry claiming
#: to be first is a claim that can be checked.
GENESIS_HASH = "0" * 64

_CHAINS: dict[str, list[dict[str, Any]]] = {}


def reset_ledger() -> None:
    """Drop every chain. For test isolation only."""
    _CHAINS.clear()


def _canonical(body: dict[str, Any]) -> str:
    """Serialize deterministically. Any change here breaks existing chains."""
    return json.dumps(body, sort_keys=True, separators=(",", ":"), default=str)


def compute_entry_hash(body: dict[str, Any]) -> str:
    """Hash the hashable body of an entry."""
    return hashlib.sha256(_canonical(body).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class LedgerEntry:
    """One immutable step in a merchant's history."""

    seq: int
    merchant_id: str
    entity_type: str
    entity_id: str
    actor: str
    action: str
    payload: dict[str, Any]
    recorded_at: datetime
    prev_hash: str
    entry_hash: str
    correlation_id: str | None = None

    def hashable_body(self) -> dict[str, Any]:
        """Exactly the fields the entry hash commits to.

        ``entry_hash`` itself is excluded, and nothing else is. Leaving a field
        out would make it silently editable.
        """
        return {
            "seq": self.seq,
            "merchant_id": self.merchant_id,
            "entity_type": self.entity_type,
            "entity_id": self.entity_id,
            "actor": self.actor,
            "action": self.action,
            "payload": self.payload,
            "recorded_at": self.recorded_at.astimezone(UTC).isoformat(),
            "prev_hash": self.prev_hash,
            "correlation_id": self.correlation_id,
        }

    def recompute(self) -> str:
        """The hash this entry's contents actually produce."""
        return compute_entry_hash(self.hashable_body())

    def to_dict(self) -> dict[str, Any]:
        """Serialize for transport, hash included."""
        return {**self.hashable_body(), "entry_hash": self.entry_hash}


@dataclass
class ChainVerification:
    """The result of walking one chain end to end."""

    merchant_id: str
    valid: bool
    entry_count: int
    head_hash: str
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "merchant_id": self.merchant_id,
            "valid": self.valid,
            "entry_count": self.entry_count,
            "head_hash": self.head_hash,
            "errors": list(self.errors),
        }


def _to_entry(raw: dict[str, Any]) -> LedgerEntry:
    return LedgerEntry(
        seq=raw["seq"],
        merchant_id=raw["merchant_id"],
        entity_type=raw["entity_type"],
        entity_id=raw["entity_id"],
        actor=raw["actor"],
        action=raw["action"],
        payload=raw["payload"],
        recorded_at=raw["recorded_at"],
        prev_hash=raw["prev_hash"],
        entry_hash=raw["entry_hash"],
        correlation_id=raw.get("correlation_id"),
    )


class AuditLedger:
    """Append-only, hash-chained history, one chain per merchant."""

    async def append(
        self,
        *,
        merchant_id: str,
        entity_type: str,
        entity_id: str,
        actor: str,
        action: str,
        payload: dict[str, Any] | None = None,
        correlation_id: str | None = None,
    ) -> LedgerEntry:
        """Add one entry to a merchant's chain and return it.

        There is no update and no delete, here or anywhere else in this module.
        A correction is a new entry stating the correction.
        """
        chain = _CHAINS.setdefault(merchant_id, [])
        prev_hash = chain[-1]["entry_hash"] if chain else GENESIS_HASH
        seq = len(chain) + 1

        body = {
            "seq": seq,
            "merchant_id": merchant_id,
            "entity_type": entity_type,
            "entity_id": entity_id,
            "actor": actor,
            "action": action,
            "payload": payload or {},
            "recorded_at": datetime.now(UTC).isoformat(),
            "prev_hash": prev_hash,
            "correlation_id": correlation_id,
        }
        entry_hash = compute_entry_hash(body)

        stored = {
            **body,
            "recorded_at": datetime.fromisoformat(body["recorded_at"]),
            "entry_hash": entry_hash,
        }
        chain.append(stored)
        return _to_entry(stored)

    async def chain(self, merchant_id: str) -> list[LedgerEntry]:
        """Every entry for one merchant, in order."""
        return [_to_entry(raw) for raw in _CHAINS.get(merchant_id, [])]

    async def entries_for(
        self,
        merchant_id: str,
        *,
        entity_type: str | None = None,
        entity_id: str | None = None,
        action: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[LedgerEntry], int]:
        """Filtered slice of a chain, plus the unpaginated total."""
        entries = [
            raw
            for raw in _CHAINS.get(merchant_id, [])
            if (entity_type is None or raw["entity_type"] == entity_type)
            and (entity_id is None or raw["entity_id"] == entity_id)
            and (action is None or raw["action"] == action)
        ]
        total = len(entries)
        window = entries[offset : offset + limit]
        return [_to_entry(raw) for raw in window], total

    async def head(self, merchant_id: str) -> str:
        """The hash a verifier only needs to keep."""
        chain = _CHAINS.get(merchant_id, [])
        return chain[-1]["entry_hash"] if chain else GENESIS_HASH

    async def verify(self, merchant_id: str) -> ChainVerification:
        """Walk the chain and report every break.

        Every entry is checked rather than stopping at the first failure: an
        investigator needs the extent of the tampering, not just its existence.
        """
        entries = await self.chain(merchant_id)
        errors: list[str] = []
        expected_prev = GENESIS_HASH

        for index, entry in enumerate(entries):
            expected_seq = index + 1
            if entry.seq != expected_seq:
                errors.append(
                    f"entry {index}: seq is {entry.seq}, expected {expected_seq} "
                    "(an entry was inserted or removed)"
                )

            recomputed = entry.recompute()
            if recomputed != entry.entry_hash:
                errors.append(
                    f"entry {entry.seq}: contents do not match their hash "
                    f"(stored {entry.entry_hash[:12]}…, computed {recomputed[:12]}…)"
                )

            if entry.prev_hash != expected_prev:
                errors.append(
                    f"entry {entry.seq}: prev_hash {entry.prev_hash[:12]}… does not point at "
                    f"{expected_prev[:12]}… (the chain is broken here)"
                )

            # Follow the stored hash, not the recomputed one: the goal is to
            # report every break, and re-deriving would hide breaks downstream
            # of an edited entry.
            expected_prev = entry.entry_hash

        return ChainVerification(
            merchant_id=merchant_id,
            valid=not errors,
            entry_count=len(entries),
            head_hash=entries[-1].entry_hash if entries else GENESIS_HASH,
            errors=errors,
        )


def verify_exported_chain(
    entries: list[dict[str, Any]], *, contiguous: bool = True
) -> tuple[bool, list[str]]:
    """Verify a chain from its serialized form alone.

    This is the offline check. It takes the output of :meth:`LedgerEntry.to_dict`
    and needs no database, no application and no key — anyone handed the export
    can confirm the entries were not edited after they were written.

    ``contiguous`` says what kind of export this is, and the distinction is not
    cosmetic. A whole chain must run 1, 2, 3… with every link intact, and a gap
    in it is proof that an entry was removed. A *subset* — the entries about one
    payment, lifted out of a merchant's full history — legitimately skips
    sequence numbers, so demanding contiguity there would reject every honest
    export. What holds either way, and is checked either way, is that each
    entry's contents still produce its own hash and that consecutively numbered
    entries link to each other. A subset is protected from being *edited* by
    this check, and from being *pruned* by whatever signs the document carrying
    it.
    """
    errors: list[str] = []
    previous_seq: int | None = None
    previous_hash = GENESIS_HASH

    for index, raw in enumerate(entries):
        body = {key: value for key, value in raw.items() if key != "entry_hash"}
        stored_hash = raw.get("entry_hash", "")
        recomputed = compute_entry_hash(body)
        seq = raw.get("seq")

        if recomputed != stored_hash:
            errors.append(f"entry {seq}: contents do not match their hash")

        if contiguous:
            if seq != index + 1:
                errors.append(f"entry {index}: seq is {seq}, expected {index + 1}")
            if raw.get("prev_hash") != previous_hash:
                errors.append(f"entry {seq}: prev_hash does not point at the entry before")
        elif not isinstance(seq, int) or (previous_seq is not None and seq <= previous_seq):
            errors.append(f"entry {index}: seq {seq} is out of order")
        elif previous_seq is not None and seq == previous_seq + 1:
            # Adjacent in the original chain, so the link must hold here too.
            if raw.get("prev_hash") != previous_hash:
                errors.append(f"entry {seq}: prev_hash does not point at entry {previous_seq}")

        previous_seq = seq if isinstance(seq, int) else previous_seq
        previous_hash = stored_hash

    return not errors, errors


__all__ = [
    "GENESIS_HASH",
    "AuditLedger",
    "ChainVerification",
    "LedgerEntry",
    "compute_entry_hash",
    "reset_ledger",
    "verify_exported_chain",
]
