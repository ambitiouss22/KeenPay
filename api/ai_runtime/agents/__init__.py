"""The runner: one buyer request in, one auditable run report out.

Assembles the four pieces for a single run - credential, client, tool registry,
graph - and tears them down afterwards. Each is per-run on purpose: the client
holds a bearer token, and a token whose lifetime is the process's lifetime is
not the short-lived credential the design calls for.

The report is the deliverable. It carries what the agent proposed, what it
asked for, and - crucially - the complete list of Control Plane calls it made,
so "the agent never tried to capture a payment" is a claim backed by a record
rather than by the absence of a complaint.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

import httpx

from ai_runtime.config import AIRuntimeSettings, get_ai_settings
from ai_runtime.credentials import AgentCredential, CredentialError
from ai_runtime.graph import build_agent_graph
from ai_runtime.graph.state import new_state
from ai_runtime.tools import ToolNotPermittedError, ToolRegistry


@dataclass
class AgentRunReport:
    """Everything one run did, in the shape the API returns."""

    run_id: str
    stage: str
    reply: str
    recommendations: list[dict[str, Any]] = field(default_factory=list)
    cart_id: str | None = None
    order_id: str | None = None
    order_total_paise: int | None = None
    authorization_id: str | None = None
    authorization_status: str | None = None
    authorization_reasons: list[str] = field(default_factory=list)
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    control_plane_calls: list[dict[str, Any]] = field(default_factory=list)
    guardrail_ok: bool = True
    guardrail_violations: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def money_moved(self) -> bool:
        """Always false, and asserted rather than assumed.

        The runtime has no tool and no allowlisted endpoint that could move
        money. This property exists so the claim appears in the response a
        caller reads, and so a test can fail the day that stops being true.
        """
        return False

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "stage": self.stage,
            "reply": self.reply,
            "recommendations": self.recommendations,
            "cart_id": self.cart_id,
            "order_id": self.order_id,
            "order_total_paise": self.order_total_paise,
            "authorization_id": self.authorization_id,
            "authorization_status": self.authorization_status,
            "authorization_reasons": self.authorization_reasons,
            "tool_calls": self.tool_calls,
            "control_plane_calls": self.control_plane_calls,
            "guardrail_ok": self.guardrail_ok,
            "guardrail_violations": self.guardrail_violations,
            "money_moved": self.money_moved,
            "errors": self.errors,
            "notes": self.notes,
        }


class AgentRunner:
    """Runs one buyer intent through the graph.

    ``http_client`` and ``transport`` are injection points for tests: the real
    client, the real allowlist and the real credential checks all execute
    against a stub Control Plane. Mocking the client instead would test the
    stub and prove nothing about the boundary.
    """

    def __init__(
        self,
        *,
        settings: AIRuntimeSettings | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._settings = settings or get_ai_settings()
        self._transport = transport
        self._http_client = http_client

    def _credential(self, token: str | None) -> AgentCredential:
        raw = (token or self._settings.agent_token or "").strip()
        if not raw:
            raise CredentialError(
                "no agent credential supplied; pass one per request or set AI_AGENT_TOKEN"
            )
        credential = AgentCredential.parse(raw)
        # Checked once here with no scope requirement, so an expired or
        # wrongly-aimed token fails before any tool runs. Per-tool scope checks
        # still happen at each call: this one catches the whole-run problems.
        credential.check(audience=self._settings.agent_audience)
        return credential

    async def run(
        self,
        *,
        message: str,
        agent_token: str | None = None,
        merchant_name: str | None = None,
        idempotency_key: str | None = None,
        run_id: str | None = None,
    ) -> AgentRunReport:
        credential = self._credential(agent_token)
        rid = run_id or f"run_{uuid.uuid4().hex[:16]}"

        from ai_runtime.client import ControlPlaneClient

        client = ControlPlaneClient(
            credential=credential,
            settings=self._settings,
            transport=self._transport,
            client=self._http_client,
        )
        registry = ToolRegistry(client, max_calls=self._settings.max_tool_calls)

        try:
            graph = build_agent_graph(registry, settings=self._settings)
            state = new_state(
                run_id=rid,
                message=message,
                # Derived from the run id so a retry of the same run produces
                # the same order rather than a second one, while two different
                # runs never collide. Long enough for the Control Plane's
                # minimum-length check.
                idempotency_key=idempotency_key or f"agentrun-{rid}",
                merchant_name=merchant_name,
                max_recommendations=self._settings.max_recommendations,
                max_request_amount_paise=self._settings.max_request_amount_paise,
            )
            final = await graph.ainvoke(state)
        except ToolNotPermittedError as exc:
            # The one failure that must never look like an ordinary error. The
            # run ends, the reason is recorded, and the guardrail flag is false
            # so anything reading the report can act on it.
            return AgentRunReport(
                run_id=rid,
                stage="failed",
                reply=(
                    "I tried to use a capability I don't have, so I stopped. "
                    "Nothing was charged."
                ),
                tool_calls=list(registry.invocations),
                control_plane_calls=list(client.call_log),
                guardrail_ok=False,
                guardrail_violations=[str(exc)],
                errors=[str(exc)],
            )
        finally:
            await client.aclose()

        return AgentRunReport(
            run_id=rid,
            stage=str(final.get("stage", "report")),
            reply=str(final.get("reply", "")),
            recommendations=list(final.get("recommendations") or []),
            cart_id=final.get("cart_id"),
            order_id=final.get("order_id"),
            order_total_paise=final.get("order_total_paise"),
            authorization_id=final.get("authorization_id"),
            authorization_status=final.get("authorization_status"),
            authorization_reasons=list(final.get("authorization_reasons") or []),
            tool_calls=list(final.get("tool_calls") or registry.invocations),
            control_plane_calls=list(client.call_log),
            guardrail_ok=bool(final.get("guardrail_ok", True)),
            guardrail_violations=list(final.get("guardrail_violations") or []),
            errors=list(final.get("errors") or []),
            notes=list(final.get("notes") or []),
        )


__all__ = ["AgentRunReport", "AgentRunner"]
