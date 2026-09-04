"""The tool registry: the only bridge between a plan and the Control Plane.

A tool call arrives as a name and a dict of arguments, both of which may have
come from a language model and neither of which can be trusted. The registry
does four things in order, and stops at the first failure:

1. Resolves the name against :data:`TOOL_SPECS_BY_NAME`. An unknown name -
   including every money-moving name a prompt-injected model might invent -
   is a typed error, never a call.
2. Validates arguments against the tool's declared schema. A missing required
   field, a wrong type, an out-of-range quantity, an unexpected extra key: all
   refused here, so a hallucinated argument cannot reach the network.
3. Translates arguments into an allowlisted request. Nothing about money is
   taken from the arguments except an amount the Control Plane re-derives and
   re-checks anyway.
4. Records the call. Every invocation lands in :attr:`ToolRegistry.invocations`
   whether it succeeded or not, which is what lets a run report state what the
   agent actually did rather than what it was asked to do.

Every result is a :class:`ToolResult` rather than an exception, because a
failed tool call is normal control flow for an agent: the graph reads the
error, adapts, and carries on. Exceptions are reserved for the case that must
never be recoverable - a call to something outside the allowlist.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from ai_runtime.client import ControlPlaneClient, ControlPlaneError, EndpointNotAllowedError
from ai_runtime.tools.tool_defs import (
    TOOL_SPECS,
    TOOL_SPECS_BY_NAME,
    ToolKind,
    ToolSpec,
    assert_no_forbidden_tools,
    tool_schemas,
)


class ToolNotPermittedError(RuntimeError):
    """A tool outside the registry was requested by name.

    Not a :class:`ToolResult`, because this is not something the agent should
    be able to retry its way around. It means something asked for a capability
    the service does not have, and the run should end saying so.
    """


@dataclass
class ToolResult:
    """The outcome of one tool call, in the shape a plan step consumes."""

    tool: str
    ok: bool
    data: Any = None
    error: str | None = None
    status_code: int | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"tool": self.tool, "ok": self.ok}
        if self.ok:
            out["data"] = self.data
        else:
            out["error"] = self.error
            if self.status_code is not None:
                out["status_code"] = self.status_code
        return out


def _type_ok(value: Any, expected: str) -> bool:
    if expected == "integer":
        # bool is an int in Python; a quantity of ``True`` is a bug, not a 1.
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "string":
        return isinstance(value, str)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    return True


def validate_arguments(spec: ToolSpec, arguments: dict[str, Any]) -> dict[str, Any]:
    """Check arguments against the tool's schema and return the cleaned dict.

    Raises :class:`ValueError` with a message naming the offending field. The
    message is handed back to the model in a :class:`ToolResult`, so it is
    written to be actionable: "quantity must be an integer" tells a model what
    to send next in a way that "invalid input" does not.
    """
    if not isinstance(arguments, dict):
        raise ValueError("arguments must be an object")

    unexpected = sorted(set(arguments) - set(spec.parameters))
    if unexpected:
        raise ValueError(f"unexpected argument(s): {', '.join(unexpected)}")

    missing = [name for name in spec.required if arguments.get(name) in (None, "")]
    if missing:
        raise ValueError(f"missing required argument(s): {', '.join(missing)}")

    cleaned: dict[str, Any] = {}
    for name, value in arguments.items():
        schema = spec.parameters.get(name, {})
        expected = schema.get("type")
        if value is None:
            continue
        if expected and not _type_ok(value, expected):
            raise ValueError(f"{name} must be of type {expected}")
        if expected == "integer":
            low, high = schema.get("minimum"), schema.get("maximum")
            if low is not None and value < low:
                raise ValueError(f"{name} must be at least {low}")
            if high is not None and value > high:
                raise ValueError(f"{name} must be at most {high}")
        if expected == "string":
            min_len = schema.get("minLength")
            if min_len is not None and len(value) < min_len:
                raise ValueError(f"{name} must be at least {min_len} characters")
        cleaned[name] = value

    for name, schema in spec.parameters.items():
        if name not in cleaned and "default" in schema:
            cleaned[name] = schema["default"]

    return cleaned


class ToolRegistry:
    """Binds tool specs to one credentialed client for the length of a run."""

    def __init__(self, client: ControlPlaneClient, *, max_calls: int = 12) -> None:
        assert_no_forbidden_tools()
        self._client = client
        self._max_calls = max_calls
        self.invocations: list[dict[str, Any]] = []

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(TOOL_SPECS_BY_NAME)

    @property
    def call_count(self) -> int:
        return len(self.invocations)

    @staticmethod
    def schemas() -> list[dict[str, Any]]:
        return tool_schemas()

    @staticmethod
    def describe() -> list[dict[str, Any]]:
        """Human- and UI-facing description, including each tool's kind."""
        return [
            {
                "name": spec.name,
                "kind": spec.kind.value,
                "description": spec.description,
                "endpoint": spec.endpoint,
                "scopes": sorted(spec.required_scopes),
            }
            for spec in TOOL_SPECS
        ]

    async def call(self, name: str, arguments: dict[str, Any] | None = None) -> ToolResult:
        spec = TOOL_SPECS_BY_NAME.get(name)
        if spec is None:
            # Deliberately fatal. A model asking for ``capture_payment`` is not
            # making a recoverable mistake; it is doing the one thing this
            # service exists to make impossible, and the run should say so.
            raise ToolNotPermittedError(
                f"tool {name!r} is not available to the AI Runtime. "
                f"Available tools: {', '.join(self.names)}"
            )

        if self.call_count >= self._max_calls:
            return ToolResult(
                tool=name,
                ok=False,
                error=f"tool call budget of {self._max_calls} exhausted for this run",
            )

        try:
            cleaned = validate_arguments(spec, arguments or {})
        except ValueError as exc:
            result = ToolResult(tool=name, ok=False, error=str(exc))
            self._record(name, arguments or {}, result)
            return result

        try:
            result = await self._dispatch(spec, cleaned)
        except EndpointNotAllowedError:
            raise
        except ControlPlaneError as exc:
            result = ToolResult(
                tool=name, ok=False, error=str(exc), status_code=exc.status_code
            )

        self._record(name, cleaned, result)
        return result

    async def _dispatch(self, spec: ToolSpec, args: dict[str, Any]) -> ToolResult:
        if spec.name == "search_products":
            query: dict[str, Any] = {"limit": args.get("limit", 10)}
            if args.get("query"):
                query["q"] = args["query"]
            return self._wrap(spec, await self._client.call(spec.endpoint, query=query))

        if spec.name == "get_product":
            return self._wrap(
                spec, await self._client.call(spec.endpoint, path_params={"sku": args["sku"]})
            )

        if spec.name == "create_cart":
            # Empty body, not omitted: the Control Plane's cart route takes no
            # fields, and sending an object makes that explicit at the wire.
            return self._wrap(spec, await self._client.call(spec.endpoint, body={}))

        if spec.name == "view_cart":
            return self._wrap(
                spec,
                await self._client.call(
                    spec.endpoint, path_params={"cart_id": args["cart_id"]}
                ),
            )

        if spec.name == "add_to_cart":
            return self._wrap(
                spec,
                await self._client.call(
                    spec.endpoint,
                    path_params={"cart_id": args["cart_id"]},
                    body={"sku": args["sku"], "quantity": args["quantity"]},
                ),
            )

        if spec.name == "checkout_cart":
            return self._wrap(
                spec,
                await self._client.call(
                    spec.endpoint,
                    path_params={"cart_id": args["cart_id"]},
                    # discount is not an agent-controlled field. Omitting it
                    # leaves the Control Plane's default of zero, so an agent
                    # cannot discount an order into existence.
                    body={"idempotency_key": args["idempotency_key"]},
                ),
            )

        if spec.name == "request_authorization":
            return self._wrap(
                spec,
                await self._client.call(
                    spec.endpoint,
                    body={
                        "kind": "payment",
                        "amount_paise": args["amount_paise"],
                        "subject_id": args["order_id"],
                        "currency": "INR",
                    },
                ),
            )

        if spec.name == "check_authorization":
            return self._wrap(
                spec,
                await self._client.call(
                    spec.endpoint,
                    path_params={"authorization_id": args["authorization_id"]},
                ),
            )

        raise ToolNotPermittedError(  # pragma: no cover - unreachable while specs match
            f"tool {spec.name!r} has no dispatch implementation"
        )

    @staticmethod
    def _wrap(spec: ToolSpec, response: Any) -> ToolResult:
        if response.ok:
            return ToolResult(
                tool=spec.name, ok=True, data=response.body, status_code=response.status_code
            )
        message = "request failed"
        body = response.body
        if isinstance(body, dict):
            err = body.get("error")
            if isinstance(err, dict):
                message = err.get("message") or err.get("code") or message
        return ToolResult(
            tool=spec.name, ok=False, error=message, status_code=response.status_code
        )

    def _record(self, name: str, arguments: dict[str, Any], result: ToolResult) -> None:
        self.invocations.append(
            {
                "id": f"call_{uuid.uuid4().hex[:12]}",
                "tool": name,
                "kind": TOOL_SPECS_BY_NAME[name].kind.value,
                "arguments": arguments,
                "ok": result.ok,
                "error": result.error,
                "status_code": result.status_code,
            }
        )


@dataclass
class ToolAudit:
    """A read of what a run did, used by the guardrail node and by tests."""

    invocations: list[dict[str, Any]] = field(default_factory=list)

    @property
    def tools_used(self) -> list[str]:
        return [i["tool"] for i in self.invocations]

    @property
    def request_kind_calls(self) -> list[str]:
        return [i["tool"] for i in self.invocations if i["kind"] == ToolKind.REQUEST.value]

    def used_only_permitted(self) -> bool:
        return all(name in TOOL_SPECS_BY_NAME for name in self.tools_used)


__all__ = [
    "TOOL_SPECS",
    "TOOL_SPECS_BY_NAME",
    "ToolAudit",
    "ToolKind",
    "ToolNotPermittedError",
    "ToolRegistry",
    "ToolResult",
    "ToolSpec",
    "validate_arguments",
]
