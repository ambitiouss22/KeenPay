"""KeenPay AI Runtime — an isolated deployable that reasons and requests.

This package is a separate service. It shares the repository so the two sides
of the contract can be reviewed together, but it is built into its own image,
runs in its own container, and holds none of the Control Plane's secrets.

The invariant the whole package exists to hold:

    The AI reasons and recommends. The deterministic Control Plane owns money.

Concretely, and enforced by code rather than by convention:

* No database driver is imported here and no connection string is read. The
  runtime cannot reach Postgres even if something in it tried.
* No payment-provider credential is read. There is no code path to a provider.
* Every outbound call goes through :class:`ai_runtime.client.ControlPlaneClient`,
  which refuses any request not on an explicit allowlist of method/path pairs.
* The tool registry contains read tools and *request* tools. Capturing,
  refunding and approving are absent, and asking for them by name raises.

``ai_runtime.isolation`` states those rules as data and asserts them at
startup, so a future edit that quietly adds a database URL fails the service's
own boot rather than being discovered in production.
"""

from ai_runtime.config import AIRuntimeSettings, get_ai_settings

__all__ = ["AIRuntimeSettings", "get_ai_settings"]
