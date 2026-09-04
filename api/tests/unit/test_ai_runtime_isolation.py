"""The isolation boundary, asserted rather than assumed.

If these fail, the AI Runtime has acquired a capability it is not supposed to
have. That is a release blocker, not a flaky test: every other guarantee in
this service rests on the runtime being unable to reach storage or a payment
provider at all.
"""

from pathlib import Path

import pytest

from ai_runtime.client import ALLOWLIST, ALLOWLIST_KEYS
from ai_runtime.config import AIRuntimeSettings
from ai_runtime.isolation import (
    FORBIDDEN_MODULES,
    FORBIDDEN_SETTING_PATTERNS,
    FORBIDDEN_TOOL_NAMES,
    IsolationError,
    assert_isolated,
    check_isolation,
    package_import_violations,
)
from ai_runtime.tools.tool_defs import TOOL_SPECS_BY_NAME, ToolKind, assert_no_forbidden_tools


def test_settings_carry_no_database_or_payment_credential():
    """No field may name a datastore or a payment secret."""
    names = [n.lower() for n in AIRuntimeSettings.model_fields]
    for name in names:
        for pattern in FORBIDDEN_SETTING_PATTERNS:
            assert pattern not in name, f"settings field {name!r} matches {pattern!r}"


def test_settings_have_no_jwt_secret():
    """The runtime cannot mint tokens, only present ones it was given."""
    assert "jwt_secret" not in AIRuntimeSettings.model_fields
    assert "passport_signing_key" not in AIRuntimeSettings.model_fields


def test_package_imports_nothing_forbidden():
    """Static scan of every module in the package."""
    assert package_import_violations() == []


def test_scan_catches_a_planted_violation(tmp_path: Path):
    """The scan is checked against a file that does break the rule.

    Without this, a scanner that silently found nothing - a wrong root, a typo
    in the pattern list - would look identical to a clean package.
    """
    (tmp_path / "leaky.py").write_text("import asyncpg\n", encoding="utf-8")
    violations = package_import_violations(tmp_path)
    assert len(violations) == 1
    assert "asyncpg" in violations[0]


def test_scan_catches_from_imports(tmp_path: Path):
    (tmp_path / "leaky.py").write_text(
        "from services.payments import PaymentService\n", encoding="utf-8"
    )
    assert package_import_violations(tmp_path)


@pytest.mark.parametrize("module", ["asyncpg", "sqlalchemy", "redis", "razorpay"])
def test_forbidden_module_list_covers_the_obvious_ones(module):
    assert module in FORBIDDEN_MODULES


def test_no_money_moving_tool_exists():
    """The registry may not contain a tool that captures, refunds or approves."""
    for name in FORBIDDEN_TOOL_NAMES:
        assert name not in TOOL_SPECS_BY_NAME
    assert_no_forbidden_tools()


def test_assert_no_forbidden_tools_rejects_a_planted_name():
    with pytest.raises(RuntimeError, match="money-moving"):
        assert_no_forbidden_tools({"search_products", "capture_payment"})


def test_every_tool_is_read_or_request():
    """There is no third kind of tool, and nothing may invent one."""
    for spec in TOOL_SPECS_BY_NAME.values():
        assert spec.kind in (ToolKind.READ, ToolKind.REQUEST)


def test_allowlist_contains_no_payment_endpoint():
    """The network-level statement of the same rule."""
    for key in ALLOWLIST_KEYS:
        assert "/payments" not in key
        assert "/refund" not in key
        assert "/approve" not in key
        assert "/webhooks" not in key


def test_allowlist_has_no_write_to_the_catalogue():
    """An agent that could edit prices could set the price it pays."""
    writes = {e.key for e in ALLOWLIST if e.method in {"POST", "PUT", "PATCH"}}
    assert not any("/products" in key for key in writes)


def test_check_isolation_reports_a_bad_settings_object():
    class LeakySettings:
        model_fields = {"database_url": None, "control_plane_url": None}

    report = check_isolation(LeakySettings(), include_environment=False, include_imports=False)
    assert not report.ok
    assert any("database_url" in v for v in report.violations)


def test_assert_isolated_raises_on_violation():
    class LeakySettings:
        model_fields = {"razorpay_key_secret": None}

    with pytest.raises(IsolationError):
        assert_isolated(LeakySettings(), include_environment=False, include_imports=False)


def test_assert_isolated_passes_for_the_real_settings():
    report = assert_isolated(AIRuntimeSettings(), include_environment=False)
    assert report.ok
    assert report.checked_settings > 0


def test_environment_check_flags_a_leaked_control_plane_secret(monkeypatch):
    """A container handed DATABASE_URL is misconfigured even if it ignores it."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://someone@somewhere/db")
    report = check_isolation(AIRuntimeSettings(), include_imports=False)
    assert not report.ok
    assert any("DATABASE_URL" in v for v in report.violations)
