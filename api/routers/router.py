"""Router registry.

One list of what the API exposes, instead of a dozen ``include_router`` calls
buried in ``create_app``. Mounting order stops being incidental, feature-gated
routers state their own condition, and adding a router in a later phase is a
one-line entry here rather than an edit to application startup.

Ordering note: FastAPI matches in registration order, so a literal path must be
registered before a parameterised one that could also match it. Keeping the
sequence in a single visible list is what makes that reviewable.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from fastapi import APIRouter, FastAPI

from config.settings import Settings


@dataclass(frozen=True)
class Mount:
    """One router and the condition under which it is mounted."""

    import_path: str
    #: Attribute holding the APIRouter inside that module.
    attr: str = "router"
    #: Settings flag that must be true. None means always mount.
    flag: str | None = None
    description: str = ""

    def enabled(self, settings: Settings) -> bool:
        return self.flag is None or bool(getattr(settings, self.flag, False))


#: Everything the application serves, in mount order.
REGISTRY: list[Mount] = [
    Mount("routers.health", description="liveness, readiness, health"),
    Mount("routers.auth", description="login, refresh, api keys"),
    Mount("routers.catalog", description="product browse (v1)"),
    Mount("routers.products", description="product management (phase 4)"),
    Mount("routers.carts", description="carts and checkout (phase 4)"),
    Mount("routers.sessions", description="agentic checkout sessions"),
    Mount("routers.orders", description="orders"),
    Mount("routers.admin", description="escalations, policy"),
    Mount("routers.webhooks", description="razorpay callbacks"),
    Mount("routers.metrics", flag="enable_metrics", description="prometheus scrape"),
    Mount("routers.dev", flag="enable_dev_routes", description="local-only helpers"),
    Mount("ws.session", flag="enable_trace_streaming", description="session websocket"),
]


@dataclass
class MountReport:
    """What actually got mounted. Returned so startup can log it."""

    mounted: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)


def _load(mount: Mount) -> APIRouter | None:
    module = __import__(mount.import_path, fromlist=[mount.attr])
    return getattr(module, mount.attr, None)


def register_routers(app: FastAPI, settings: Settings) -> MountReport:
    """Mount every enabled router and report the outcome.

    A router listed here but not yet written is recorded as missing rather than
    raising. That is what lets later phases pre-register their routers - the
    app still boots today, and the entry starts working the moment the file
    lands. An import that exists but is *broken* still raises: a typo in a real
    router must not be silently swallowed into a half-mounted API.
    """
    report = MountReport()

    for mount in REGISTRY:
        if not mount.enabled(settings):
            report.skipped.append(mount.import_path)
            continue
        try:
            router = _load(mount)
        except ModuleNotFoundError as exc:
            # Only tolerate the router's own module being absent. A missing
            # dependency *inside* an existing router is a real failure.
            if exc.name and (
                exc.name == mount.import_path or mount.import_path.startswith(f"{exc.name}.")
            ):
                report.missing.append(mount.import_path)
                continue
            raise

        if router is None:
            report.missing.append(mount.import_path)
            continue

        app.include_router(router)
        report.mounted.append(mount.import_path)

    return report


__all__ = ["REGISTRY", "Mount", "MountReport", "register_routers"]
