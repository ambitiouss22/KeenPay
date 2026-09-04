"""The AI Runtime service.

A small FastAPI app with four routes and one job: take a buyer's intent, run it
through the agent graph, and return an auditable report. It listens on its own
port, in its own container, and its only outbound destination is the Control
Plane's public API.

``GET /agent/tools`` deserves a note. Publishing the agent's complete tool set,
each one labelled read or request, means an operator can see what the agent can
do without reading this repository - and can see that capture, refund and
approve are not on the list. A capability that is easy to audit is one people
actually audit.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, Request
from fastapi.responses import JSONResponse

from ai_runtime.agents import AgentRunner
from ai_runtime.client import ALLOWLIST
from ai_runtime.config import get_ai_settings
from ai_runtime.credentials import CredentialError
from ai_runtime.graph import LANGGRAPH_AVAILABLE
from ai_runtime.isolation import FORBIDDEN_TOOL_NAMES, IsolationError, check_isolation
from ai_runtime.schemas import (
    AgentRunRequest,
    AgentRunResponse,
    RuntimeHealthOut,
    ToolListResponse,
)
from ai_runtime.tools import ToolRegistry


def _error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status, content={"error": {"code": code, "message": message}}
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Check the boundary before serving a single request.

    In production the environment check is fatal: a container that has been
    handed the Control Plane's database URL is misconfigured, and starting
    anyway would leave everyone believing in an isolation that no longer holds.
    Outside production it is reported instead, because a developer running both
    services from one shell legitimately has those variables in scope.
    """
    settings = get_ai_settings()
    production = settings.app_env == "production"
    report = check_isolation(settings, include_environment=production)
    if production and not report.ok:
        raise IsolationError("; ".join(report.violations))

    app.state.isolation = report
    app.state.graph_engine = "langgraph" if LANGGRAPH_AVAILABLE else "sequential"
    yield


def create_app() -> FastAPI:
    settings = get_ai_settings()
    app = FastAPI(
        title="KeenPay AI Runtime",
        version=settings.app_version,
        description=(
            "Isolated reasoning service. Recommends and requests; never moves money."
        ),
        lifespan=lifespan,
    )

    @app.exception_handler(CredentialError)
    async def credential_error(_request: Request, exc: CredentialError) -> JSONResponse:
        return _error(401, "AGENT_CREDENTIAL_REJECTED", str(exc))

    @app.get("/health/live")
    async def live() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/health", response_model=RuntimeHealthOut)
    @app.get("/health/ready", response_model=RuntimeHealthOut)
    async def health(request: Request) -> RuntimeHealthOut:
        """Readiness reports the isolation verdict, not just liveness.

        A runtime that has lost its boundary is not ready to serve, whatever
        its process is doing.
        """
        report = getattr(request.app.state, "isolation", None)
        violations = list(report.violations) if report else []
        isolated = bool(report.ok) if report else True
        return RuntimeHealthOut(
            status="ok" if isolated else "degraded",
            version=settings.app_version,
            isolated=isolated,
            graph_engine=getattr(request.app.state, "graph_engine", "sequential"),
            violations=violations,
        )

    @app.get("/agent/tools", response_model=ToolListResponse)
    async def tools() -> ToolListResponse:
        return ToolListResponse(
            tools=ToolRegistry.describe(),  # type: ignore[arg-type]
            forbidden=list(FORBIDDEN_TOOL_NAMES),
            allowlisted_endpoints=[f"{e.method} {e.path_template}" for e in ALLOWLIST],
        )

    @app.post("/agent/run", response_model=AgentRunResponse)
    async def run_agent(
        body: AgentRunRequest,
        authorization: str | None = Header(default=None),
        x_agent_token: str | None = Header(default=None, alias="X-Agent-Token"),
    ) -> AgentRunResponse:
        """Run one buyer intent.

        The agent credential is taken from ``Authorization: Bearer`` (or
        ``X-Agent-Token``) and never from the body. A token in a JSON field
        ends up in request logs and in whatever stored the request; a header is
        the one place the rest of the stack already knows not to log.
        """
        token = x_agent_token
        if not token and authorization and authorization.lower().startswith("bearer "):
            token = authorization[7:]

        runner = AgentRunner(settings=settings)
        report = await runner.run(
            message=body.message,
            agent_token=token,
            merchant_name=body.merchant_name,
            idempotency_key=body.idempotency_key,
        )
        return AgentRunResponse(**report.to_dict())

    return app


app = create_app()


__all__ = ["app", "create_app", "lifespan"]
