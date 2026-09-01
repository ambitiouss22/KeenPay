"""Datetime helpers — always UTC in API layer."""

from datetime import UTC, datetime


def utc_now() -> datetime:
    return datetime.now(UTC)


def to_iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.isoformat().replace("+00:00", "Z")
