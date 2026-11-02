"""Tenant ids (decision 0013): lowercase, short, and safe in schema, path, and file names."""

import pytest

from quant_agent_os.tenancy import DEFAULT_TENANT, TenantError, check_tenant


@pytest.mark.parametrize("tenant", [DEFAULT_TENANT, "acme", "a", "acme-research-2"])
def test_valid_tenant_ids_pass(tenant: str) -> None:
    assert check_tenant(tenant) == tenant


@pytest.mark.parametrize("tenant", ["", "Acme", "acme_corp", "-acme", "a/b", "../x", "a" * 64])
def test_malformed_tenant_ids_are_refused(tenant: str) -> None:
    with pytest.raises(TenantError, match="Tenant id"):
        check_tenant(tenant)
