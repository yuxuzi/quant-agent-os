"""Single-period Brinson-Fachler attribution of active return to groups.

Conventions:
- Inputs are mappings from group (sector, country, strategy) to weight or return over one
  period. A group missing from one side has weight 0 there, and return 0 if never given.
- Total returns are ``R = sum(w_g r_g)`` on each side. Each side's weights must sum to the same
  total (normally 1, with cash as a group), or the effects would not add up; otherwise ValueError.
- Brinson-Fachler effects for group g: allocation ``(wp - wb) (rb_g - Rb)``, selection
  ``wb (rp_g - rb_g)``, and interaction ``(wp - wb) (rp_g - rb_g)``. Their sum over groups is
  exactly the active return ``Rp - Rb``, checked by a test.
- One period only. Linking periods (Carino, Menchero) is not done: summing single-period effects
  across periods does not give the compounded active return.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

WEIGHT_TOLERANCE = 1e-9


@dataclass(frozen=True)
class GroupAttribution:
    """One group's allocation, selection, and interaction effects, and their sum."""

    allocation: float
    selection: float
    interaction: float
    total: float


@dataclass(frozen=True)
class Attribution:
    """Active return split into effects by group; the totals sum to ``active_return``."""

    groups: dict[str, GroupAttribution]
    allocation: float
    selection: float
    interaction: float
    portfolio_return: float
    benchmark_return: float
    active_return: float


def brinson(
    groups: Sequence[str],
    portfolio_weights: Mapping[str, float],
    portfolio_returns: Mapping[str, float],
    benchmark_weights: Mapping[str, float],
    benchmark_returns: Mapping[str, float],
) -> Attribution:
    """Brinson-Fachler effects for each of ``groups``; a key outside ``groups`` is an error."""
    known = set(groups)
    if len(known) != len(groups):
        repeated = sorted({g for g in groups if list(groups).count(g) > 1})
        raise ValueError(f"Each group is named once; repeated: {', '.join(repeated)}.")
    for name, mapping in (
        ("portfolio_weights", portfolio_weights),
        ("portfolio_returns", portfolio_returns),
        ("benchmark_weights", benchmark_weights),
        ("benchmark_returns", benchmark_returns),
    ):
        unknown = sorted(set(mapping) - known)
        if unknown:
            raise ValueError(f"{name} names groups not in groups: {', '.join(unknown)}.")
    wp = {g: float(portfolio_weights.get(g, 0.0)) for g in groups}
    wb = {g: float(benchmark_weights.get(g, 0.0)) for g in groups}
    rp = {g: float(portfolio_returns.get(g, 0.0)) for g in groups}
    rb = {g: float(benchmark_returns.get(g, 0.0)) for g in groups}
    if abs(sum(wp.values()) - sum(wb.values())) > WEIGHT_TOLERANCE:
        raise ValueError(
            f"Portfolio weights sum to {sum(wp.values()):g} and benchmark weights to "
            f"{sum(wb.values()):g}; they must match, so add cash as a group."
        )
    total_p = sum(wp[g] * rp[g] for g in groups)
    total_b = sum(wb[g] * rb[g] for g in groups)
    effects = {}
    for g in groups:
        allocation = (wp[g] - wb[g]) * (rb[g] - total_b)
        selection = wb[g] * (rp[g] - rb[g])
        interaction = (wp[g] - wb[g]) * (rp[g] - rb[g])
        effects[g] = GroupAttribution(
            allocation, selection, interaction, allocation + selection + interaction
        )
    return Attribution(
        groups=effects,
        allocation=sum(e.allocation for e in effects.values()),
        selection=sum(e.selection for e in effects.values()),
        interaction=sum(e.interaction for e in effects.values()),
        portfolio_return=total_p,
        benchmark_return=total_b,
        active_return=total_p - total_b,
    )
