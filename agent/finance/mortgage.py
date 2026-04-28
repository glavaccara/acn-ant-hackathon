"""
Pure mortgage / DTI / stress-test math. No LLM, no IO.

Tested cross-checks against a public mortgage calculator gave the same monthly
payment to 2 decimals for a €350k principal, 25y term, 4.5% nominal rate.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class MortgageResult:
    monthly_payment_eur: float
    total_interest_eur: float
    total_paid_eur: float
    rate_pct: float
    term_years: int
    type: str  # "fixed" | "variable_euribor"
    margin_bps: int = 0  # only meaningful for variable


def compute_mortgage(
    principal: float,
    rate_pct: float,
    term_years: int,
    type: str = "fixed",
    margin_bps: int = 0,
) -> MortgageResult:
    """Standard amortizing mortgage with monthly payments.

    For variable_euribor, `rate_pct` is the spot indexed rate; the bank's margin
    in basis points is added on top. We do NOT forecast the curve — production
    callers should re-run this under stress for forward-looking risk.
    """
    if principal <= 0 or term_years <= 0:
        raise ValueError("principal and term_years must be positive")

    effective_rate = rate_pct + (margin_bps / 100.0) if type == "variable_euribor" else rate_pct
    monthly_rate = effective_rate / 100.0 / 12.0
    n_payments = term_years * 12

    if monthly_rate == 0:
        monthly_payment = principal / n_payments
    else:
        monthly_payment = (
            principal * monthly_rate
            * (1 + monthly_rate) ** n_payments
            / ((1 + monthly_rate) ** n_payments - 1)
        )

    total_paid = monthly_payment * n_payments
    total_interest = total_paid - principal

    return MortgageResult(
        monthly_payment_eur=round(monthly_payment, 2),
        total_interest_eur=round(total_interest, 2),
        total_paid_eur=round(total_paid, 2),
        rate_pct=round(effective_rate, 4),
        term_years=term_years,
        type=type,
        margin_bps=margin_bps,
    )


@dataclass
class DTIResult:
    dti_ratio: float                  # 0..1
    monthly_income_eur: float
    monthly_debt_eur: float
    headroom_eur: float               # income * recommended_max - debt
    recommended_max_dti: float = 0.36  # standard EU/IT mortgage guideline


def compute_dti(
    monthly_income_eur: float,
    monthly_debt_eur: float,
    recommended_max_dti: float = 0.36,
) -> DTIResult:
    """Debt-to-income ratio — the canonical affordability gate for mortgages.

    `monthly_debt_eur` should include the prospective mortgage payment plus any
    existing recurring debt (other loans, credit lines).
    """
    if monthly_income_eur <= 0:
        raise ValueError("monthly_income_eur must be positive")

    dti = monthly_debt_eur / monthly_income_eur
    headroom = monthly_income_eur * recommended_max_dti - monthly_debt_eur

    return DTIResult(
        dti_ratio=round(dti, 4),
        monthly_income_eur=round(monthly_income_eur, 2),
        monthly_debt_eur=round(monthly_debt_eur, 2),
        headroom_eur=round(headroom, 2),
        recommended_max_dti=recommended_max_dti,
    )


@dataclass
class StressResult:
    scenario_label: str
    rate_shock_bps: int
    income_shock_pct: float
    stressed_monthly_payment_eur: float
    stressed_dti: float
    survives: bool                    # stressed_dti <= recommended_max
    headroom_after_shock_eur: float


def stress_test(
    base_mortgage: MortgageResult,
    monthly_income_eur: float,
    other_monthly_debt_eur: float = 0.0,
    rate_shock_bps: int = 0,
    income_shock_pct: float = 0.0,
    recommended_max_dti: float = 0.36,
    label: str | None = None,
) -> StressResult:
    """Re-evaluate affordability under rate or income shock.

    Rate shock recomputes the mortgage with `rate_pct + shock`. Income shock is
    a multiplicative haircut on monthly income. Both can be combined.
    """
    shocked_rate = base_mortgage.rate_pct + (rate_shock_bps / 100.0)
    shocked = compute_mortgage(
        principal=_principal_from_payment(base_mortgage),
        rate_pct=shocked_rate,
        term_years=base_mortgage.term_years,
        type=base_mortgage.type,
        margin_bps=0,  # shock already applied to rate_pct
    )
    shocked_income = monthly_income_eur * (1 - income_shock_pct / 100.0)
    shocked_debt = shocked.monthly_payment_eur + other_monthly_debt_eur
    dti = shocked_debt / shocked_income if shocked_income > 0 else float("inf")

    return StressResult(
        scenario_label=label or f"shock_+{rate_shock_bps}bps_-{income_shock_pct}%",
        rate_shock_bps=rate_shock_bps,
        income_shock_pct=income_shock_pct,
        stressed_monthly_payment_eur=shocked.monthly_payment_eur,
        stressed_dti=round(dti, 4),
        survives=dti <= recommended_max_dti,
        headroom_after_shock_eur=round(
            shocked_income * recommended_max_dti - shocked_debt, 2
        ),
    )


def _principal_from_payment(m: MortgageResult) -> float:
    """Recover principal from a MortgageResult — needed because stress tests
    rebuild the loan at a different rate but same principal."""
    monthly_rate = m.rate_pct / 100.0 / 12.0
    n = m.term_years * 12
    if monthly_rate == 0:
        return m.monthly_payment_eur * n
    return (
        m.monthly_payment_eur
        * ((1 + monthly_rate) ** n - 1)
        / (monthly_rate * (1 + monthly_rate) ** n)
    )
