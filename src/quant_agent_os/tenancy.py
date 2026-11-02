"""Tenants (decision 0013): the unit of isolation for data, runs, policy, and toolsets.

A tenant comes from the caller's credential (an API key's entry, a token's claim), never from a
request. Without any tenant configuration everything is the `DEFAULT_TENANT`, so single-team
deployments behave as before.
"""

from __future__ import annotations

import re

DEFAULT_TENANT = "local"
TENANT_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")


class TenantError(ValueError):
    """Raised for a malformed tenant id."""


def check_tenant(tenant: str) -> str:
    """Return ``tenant`` or raise `TenantError` describing a valid id."""
    if not TENANT_ID.fullmatch(tenant):
        raise TenantError(
            f"Tenant id '{tenant}' must be lowercase letters, digits, or '-', start with a "
            "letter or digit, and be at most 63 characters."
        )
    return tenant
