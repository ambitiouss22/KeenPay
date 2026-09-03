"""Unit tests for the RLS helper layer.

No database here — these cover the pure logic that guards what reaches
``set_config``. The isolation guarantees themselves are proven in
``tests/integration/test_tenant_isolation.py`` against a real Postgres, because
that is the only place they can be.
"""

from __future__ import annotations

import uuid

import pytest

from core.rls import TENANT_SETTING, TenantNotPinnedError, coerce_tenant_id


class TestCoerceTenantId:
    def test_accepts_a_uuid_object(self):
        tid = uuid.uuid4()
        assert coerce_tenant_id(tid) is tid

    def test_accepts_a_uuid_string(self):
        tid = uuid.uuid4()
        assert coerce_tenant_id(str(tid)) == tid

    @pytest.mark.parametrize(
        "value",
        [
            "",
            "not-a-uuid",
            "1; DROP TABLE products",
            "' OR '1'='1",
            "11111111-1111-1111-1111-11111111111",  # one char short
            None,
            123,
        ],
    )
    def test_rejects_anything_that_is_not_a_uuid(self, value):
        """The tenant id reaches Postgres as text, so this is where a crafted
        string would have to be stopped. It is stopped."""
        with pytest.raises(ValueError):
            coerce_tenant_id(value)

    def test_error_names_the_offending_value(self):
        with pytest.raises(ValueError, match="Not a valid tenant id"):
            coerce_tenant_id("nope")


class TestSettingName:
    def test_guc_name_matches_the_policy(self):
        """If this constant drifts from the migration's policy text, every
        query silently returns nothing. Pin it."""
        assert TENANT_SETTING == "app.tenant_id"


class TestTenantNotPinnedError:
    def test_is_a_runtime_error(self):
        assert issubclass(TenantNotPinnedError, RuntimeError)

    def test_message_explains_the_fix(self):
        err = TenantNotPinnedError("No tenant pinned on this session.")
        assert "tenant" in str(err).lower()
