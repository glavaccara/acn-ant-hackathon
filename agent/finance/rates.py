"""
EURIBOR + fixed-rate snapshot source.

For the hackathon this is an in-process mock returning a static snapshot.
In production, replace `get_rate_snapshot()` with a call to `mcp_servers/rates_mcp.py`
(the MCP server file is provided as the architectural artifact; the production
client would point at it). Keeping the data source separate from the finance math
lets us swap MCP in without touching `finance/mortgage.py`.

We expose only `{spot, term_structure_snapshot}` and a `stress_test_shock_bps`
parameter — we never claim to forecast forward rates. Rate forecasting is
explicitly out of scope per the Mandate.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class RateSnapshot:
    spot_euribor_pct: float                     # 3-month EURIBOR
    term_structure: dict[int, float]            # {term_years: fixed_rate_pct}
    bank_margin_bps_default: int                # added on top of EURIBOR for variable
    snapshot_date: str
    source: str                                 # "mock_static_2026q2" | "rates-mcp"
    disclaimer: str


# Static snapshot — representative late-2026 EU rate environment.
# Replace with rates-mcp call in production.
_STATIC_SNAPSHOT = RateSnapshot(
    spot_euribor_pct=2.85,
    term_structure={
        10: 3.40,
        15: 3.65,
        20: 3.85,
        25: 3.95,
        30: 4.05,
    },
    bank_margin_bps_default=120,                # 1.20% over EURIBOR — realistic IT bank
    snapshot_date="2026-04-15",
    source="mock_static_2026q2",
    disclaimer=(
        "Spot rate snapshot only. We do not forecast forward rate curves. "
        "All forward-looking advice should be stress-tested for rate shocks."
    ),
)


def get_rate_snapshot() -> RateSnapshot:
    """Return the current rate snapshot.

    In production this would call the rates-mcp server. For the hackathon
    we serve a static snapshot — same interface, swappable backend.
    """
    return _STATIC_SNAPSHOT


def get_fixed_rate(term_years: int) -> tuple[float, str]:
    """Look up the fixed rate for a given term, with the closest match if exact term not in snapshot."""
    snap = get_rate_snapshot()
    if term_years in snap.term_structure:
        return snap.term_structure[term_years], snap.snapshot_date
    nearest = min(snap.term_structure.keys(), key=lambda k: abs(k - term_years))
    return snap.term_structure[nearest], snap.snapshot_date


def get_variable_rate(term_years: int = 0) -> tuple[float, int, str]:
    """Return (spot_euribor, default_margin_bps, snapshot_date) for variable mortgages."""
    snap = get_rate_snapshot()
    return snap.spot_euribor_pct, snap.bank_margin_bps_default, snap.snapshot_date
