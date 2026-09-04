"""The isolation boundary, written as data and checked at boot.

Isolation stated only in prose decays. Someone adds a database URL "just for a
health check", the comment saying there is no database stays where it was, and
six weeks later the AI service holds a Postgres credential nobody meant to
give it.

So the rules live here as lists, one assertion function reads them, and the
service refuses to start when they are broken. The same lists are what the
security tests assert against, which means a test and the runtime cannot drift
apart - there is only one copy.
"""

from __future__ import annotations

import ast
import os
from dataclasses import dataclass, field
from pathlib import Path

#: Substrings that must not appear in any AI Runtime settings field name.
#: Each names a capability this service is not allowed to have.
FORBIDDEN_SETTING_PATTERNS: tuple[str, ...] = (
    "database",
    "postgres",
    "dsn",
    "razorpay",
    "webhook_secret",
    "jwt_secret",
    "signing_key",
    "passport",
    "redis",
)

#: Modules the runtime must never import. Reaching any of them would mean it
#: had acquired a path to storage or to a payment provider that does not go
#: through the Control Plane's own rules.
FORBIDDEN_MODULES: tuple[str, ...] = (
    "asyncpg",
    "sqlalchemy",
    "psycopg",
    "psycopg2",
    "redis",
    "razorpay",
    "db.models",
    "db.session",
    "db.repositories",
    "database",
    "repositories.payments",
    "repositories.orders",
    "services.payments",
    "services.razorpay",
    "modules.payments.provider",
)

#: Tool names that must not exist in the registry. These move money or bless
#: the movement of money, which is the Control Plane's job and a human's.
FORBIDDEN_TOOL_NAMES: tuple[str, ...] = (
    "capture_payment",
    "create_payment",
    "refund_payment",
    "approve_authorization",
    "settle_payment",
    "payout",
    "execute_sql",
    "query_database",
    "read_secret",
)


class IsolationError(RuntimeError):
    """The runtime is configured in a way that breaks its own boundary.

    Raised at startup, deliberately fatal. A partially isolated AI service is
    worse than none: it invites reliance on a guarantee that no longer holds.
    """


@dataclass(frozen=True)
class IsolationReport:
    """What the check found. Logged at startup so the boundary is visible."""

    ok: bool
    checked_settings: int = 0
    violations: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "checked_settings": self.checked_settings,
            "violations": list(self.violations),
        }


def _settings_violations(settings_obj: object) -> list[str]:
    fields = getattr(type(settings_obj), "model_fields", {})
    out: list[str] = []
    for name in fields:
        lowered = name.lower()
        for pattern in FORBIDDEN_SETTING_PATTERNS:
            if pattern in lowered:
                out.append(f"settings field {name!r} matches forbidden pattern {pattern!r}")
    return out


def _imported_names(tree: ast.AST) -> set[str]:
    """Every module name the source names in an import statement."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                # Relative import: stays inside this package by construction.
                continue
            if node.module:
                names.add(node.module)
                names.update(f"{node.module}.{a.name}" for a in node.names)
    return names


def package_import_violations(package_dir: Path | None = None) -> list[str]:
    """Scan this package's own source for forbidden imports.

    A static scan rather than a ``sys.modules`` observation, and the difference
    matters. This service is tested in the same interpreter as the Control
    Plane, where SQLAlchemy is legitimately loaded by the other side; a runtime
    observation would flag that and teach everyone to ignore the check. What is
    worth failing on is *this package* naming a database driver or a payment
    provider in an import statement, which is exactly what the scan finds - in
    any process, including CI, before a single line has run.
    """
    root = package_dir or Path(__file__).resolve().parent
    out: list[str] = []
    for path in sorted(root.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, SyntaxError) as exc:  # pragma: no cover - unreadable source
            out.append(f"could not scan {path.name}: {exc}")
            continue
        for name in sorted(_imported_names(tree)):
            for forbidden in FORBIDDEN_MODULES:
                if name == forbidden or name.startswith(f"{forbidden}."):
                    out.append(f"{path.name} imports forbidden module {name!r}")
    return out


def _environment_violations() -> list[str]:
    """Flag Control Plane secrets that leaked into this container's env.

    The runtime does not read these - there is no field for them - but their
    presence means a deployment mounted the wrong env file, and the next
    careless edit would be able to use them. Better to fail the boot now.

    ``AI_``-prefixed names are exempt: those are this service's own namespace.
    """
    leaked = ("DATABASE_URL", "RAZORPAY_KEY_SECRET", "RAZORPAY_WEBHOOK_SECRET", "JWT_SECRET")
    return [
        f"environment carries Control Plane secret {name!r}"
        for name in leaked
        if os.environ.get(name)
    ]


def check_isolation(
    settings_obj: object,
    *,
    include_environment: bool = True,
    include_imports: bool = True,
) -> IsolationReport:
    """Inspect the runtime and report every boundary violation found.

    Returns rather than raises so a caller can log the whole list at once. A
    check that stopped at the first problem would hide the second, and the
    person fixing a misconfigured deployment would go round the loop twice.
    """
    violations = _settings_violations(settings_obj)
    if include_imports:
        violations += package_import_violations()
    if include_environment:
        violations += _environment_violations()

    return IsolationReport(
        ok=not violations,
        checked_settings=len(getattr(type(settings_obj), "model_fields", {})),
        violations=violations,
    )


def assert_isolated(settings_obj: object, **kwargs: bool) -> IsolationReport:
    """Check, and refuse to continue when the boundary is broken."""
    report = check_isolation(settings_obj, **kwargs)
    if not report.ok:
        raise IsolationError("; ".join(report.violations))
    return report


__all__ = [
    "FORBIDDEN_MODULES",
    "FORBIDDEN_SETTING_PATTERNS",
    "FORBIDDEN_TOOL_NAMES",
    "IsolationError",
    "IsolationReport",
    "assert_isolated",
    "check_isolation",
    "package_import_violations",
]
