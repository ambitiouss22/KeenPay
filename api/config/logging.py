"""Backwards-compatible alias for :mod:`core.logging`.

The implementation moved to ``core/logging.py`` in Phase 3, alongside the other
cross-cutting primitives. This module stays so existing
``from config.logging import configure_logging`` imports keep working, and
re-exports rather than duplicating - two logging configurations that drift
apart is exactly the failure this avoids.
"""

from core.logging import configure_logging, get_logger

__all__ = ["configure_logging", "get_logger"]
