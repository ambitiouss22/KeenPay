"""Settings for the AI Runtime.

Read the field list as a statement of what this service is allowed to know.
There is no ``database_url``, no ``razorpay_key_secret``, no ``jwt_secret``.
Their absence is the isolation boundary: a component that never holds a
credential cannot leak or misuse it, and no amount of prompt injection can
make it produce one it was never given.

The ``AI_`` prefix keeps the runtime's environment separate from the Control
Plane's. A shared ``.env`` accidentally mounted into this container therefore
grants it nothing - ``DATABASE_URL`` is not ``AI_DATABASE_URL``, and there is
no field for either.

The agent credential is *received*, never minted. Minting requires the signing
key, and holding that key would let this service forge any identity it liked,
including a merchant admin. It is handed a short-lived, audience-restricted
token instead, and :mod:`ai_runtime.credentials` refuses to use one that is
expired, aimed at a different audience, or missing the scope a tool needs.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class AIRuntimeSettings(BaseSettings):
    """Everything the AI Runtime is permitted to configure.

    Adding a field here is a security decision, not a convenience one. The
    isolation tests assert that no field name matches the forbidden patterns
    in :mod:`ai_runtime.isolation`.
    """

    model_config = SettingsConfigDict(
        env_prefix="AI_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: str = "development"
    app_version: str = "0.1.0"
    log_level: str = "INFO"
    host: str = "0.0.0.0"  # noqa: S104  # nosec B104 - container-internal, fronted by proxy
    port: int = 8100

    #: Base URL of the Control Plane. The only network destination this
    #: service is allowed to reach; see ``client.ControlPlaneClient``.
    control_plane_url: str = "http://localhost:8000"
    control_plane_timeout_seconds: float = 10.0
    control_plane_max_retries: int = 2

    #: The audience the agent credential must name. A token minted for a
    #: different service is rejected before it is ever sent, so a credential
    #: that leaks sideways from another system cannot be replayed here.
    agent_audience: str = "keenpay-control-plane"

    #: Optional default credential for local runs. In deployment the token
    #: arrives per request instead, which keeps its lifetime short.
    #:
    #: Credentials are minted by the Control Plane, which holds the signing key
    #: this service deliberately does not::
    #:
    #:     POST /api/v1/auth/agent-tokens  {"agent_id": ..., "scopes": [...]}
    #:
    #: It answers with a token whose role is ``agent``, whose audience is the
    #: value below, and whose scopes are a subset of what an agent may ever
    #: hold. Capture, refund and approve are not among them, and asking for one
    #: is refused rather than quietly dropped.
    agent_token: str = ""

    #: Ceilings on a single run, so a looping plan costs bounded time and a
    #: bounded number of Control Plane calls rather than unbounded ones.
    max_tool_calls: int = 12
    max_plan_steps: int = 8
    max_recommendations: int = 5

    #: Reasoning model. Empty (the default) selects the deterministic planner,
    #: which is what tests and offline demos run on: no network to a model
    #: provider, identical output for identical input.
    llm_model: str = ""
    llm_api_key: str = ""
    llm_timeout_seconds: float = 15.0

    #: Hard ceiling on what any single run may ask to be authorized. A second
    #: belt on top of the Control Plane's own policy engine, which remains the
    #: authority; this one simply keeps an obviously wrong plan from ever
    #: reaching it.
    max_request_amount_paise: int = Field(default=50_000_00, ge=0)

    @property
    def uses_llm(self) -> bool:
        return bool(self.llm_model and self.llm_api_key)


@lru_cache
def get_ai_settings() -> AIRuntimeSettings:
    return AIRuntimeSettings()


__all__ = ["AIRuntimeSettings", "get_ai_settings"]
