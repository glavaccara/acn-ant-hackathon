"""Execute tool calls from Claude, routing through the PreToolUse hook."""
import json
from datetime import datetime
from typing import Any

from agent.bank_store import BankStore
from agent.finance import mortgage as mortgage_math
from agent.finance import rates as rates_source
from agent.hooks.pre_tool_use import pre_tool_use_hook
from agent.schemas.claim import Claim

# In-memory category knowledge base (production: ML model or lookup service)
CATEGORY_KB: dict[str, dict] = {
    "esselunga": {"category": "groceries", "criticality": "essential", "confidence": 0.97},
    "coop": {"category": "groceries", "criticality": "essential", "confidence": 0.97},
    "conad": {"category": "groceries", "criticality": "essential", "confidence": 0.95},
    "enel": {"category": "utilities", "criticality": "essential", "confidence": 0.99},
    "eni gas": {"category": "utilities", "criticality": "essential", "confidence": 0.99},
    "tim": {"category": "telecom", "criticality": "essential", "confidence": 0.95},
    "vodafone": {"category": "telecom", "criticality": "essential", "confidence": 0.95},
    "netflix": {"category": "entertainment", "criticality": "discretionary", "confidence": 0.99},
    "spotify": {"category": "entertainment", "criticality": "discretionary", "confidence": 0.99},
    "dazn": {"category": "entertainment", "criticality": "discretionary", "confidence": 0.99},
    "amazon": {"category": "shopping", "criticality": "discretionary", "confidence": 0.85},
    "bar": {"category": "dining", "criticality": "discretionary", "confidence": 0.88},
    "ristorante": {"category": "dining", "criticality": "discretionary", "confidence": 0.90},
    "trenitalia": {"category": "transport", "criticality": "essential", "confidence": 0.95},
    "atm": {"category": "transport", "criticality": "essential", "confidence": 0.90},
    "ryanair": {"category": "travel", "criticality": "discretionary", "confidence": 0.97},
    "farmacia": {"category": "health", "criticality": "essential", "confidence": 0.95},
    "decathlon": {"category": "sports/leisure", "criticality": "discretionary", "confidence": 0.90},
    "stipendio": {"category": "income", "criticality": "essential", "confidence": 0.99},
    "bonifico": {"category": "other", "criticality": "cuttable", "confidence": 0.40},
    "pagamento pos": {"category": "other", "criticality": "cuttable", "confidence": 0.30},
}

MCC_MAP: dict[str, dict] = {
    "5411": {"category": "groceries", "criticality": "essential"},
    "5812": {"category": "dining", "criticality": "discretionary"},
    "4900": {"category": "utilities", "criticality": "essential"},
    "4813": {"category": "telecom", "criticality": "essential"},
    "7922": {"category": "entertainment", "criticality": "discretionary"},
    "4112": {"category": "transport", "criticality": "essential"},
    "5912": {"category": "health", "criticality": "essential"},
    "5941": {"category": "sports/leisure", "criticality": "discretionary"},
    "4511": {"category": "travel", "criticality": "discretionary"},
    "5999": {"category": "shopping", "criticality": "discretionary"},
}


def execute_tool(tool_name: str, tool_input: dict, store: BankStore) -> dict:
    """Execute a tool call, running it through the PreToolUse hook first."""
    hook_result = pre_tool_use_hook(tool_name, tool_input, store)
    if hook_result.get("blocked"):
        return {
            "isError": True,
            "reason_code": hook_result["reason"],
            "guidance": hook_result["guidance"],
            "blocked_by": "pre_tool_use_hook"
        }

    return _dispatch(tool_name, tool_input, store)


def _dispatch(tool_name: str, inp: dict, store: BankStore) -> dict:
    handlers = {
        "get_transactions": _get_transactions,
        "lookup_category": _lookup_category,
        "get_transaction_history": _get_transaction_history,
        "set_transaction_classification": _set_classification,
        "flag_subscription_for_review": _flag_subscription,
        "get_budget_envelope": _get_envelope,
        "get_savings_goals": _get_savings_goals,
        "get_spending_forecast": _get_forecast,
        "update_budget_envelope": _update_envelope,
        "create_savings_goal": _create_goal,
        "pause_subscription": _pause_subscription,
        "get_user_preferences": _get_prefs,
        "get_pending_questions": _get_pending_qs,
        "get_recent_agent_actions": _get_recent_actions,
        "enqueue_user_notification": _enqueue_notification,
        "flag_for_review": _flag_for_review,
        # Affordability advisor tools (read + write Claims)
        "get_user_income_summary": _get_income_summary,
        "get_user_recurring_burden": _get_recurring_burden,
        "get_rate_snapshot": _get_rate_snapshot,
        "compute_mortgage_scenario": _compute_mortgage_scenario,
        "compute_dti_scenario": _compute_dti_scenario,
        "stress_test_scenario": _stress_test_scenario,
        "emit_claim": _emit_claim,
        "list_claims": _list_claims,
        "get_claim": _get_claim,
    }
    handler = handlers.get(tool_name)
    if not handler:
        return {"isError": True, "reason_code": "UNKNOWN_TOOL",
                "guidance": f"Tool '{tool_name}' not registered."}
    try:
        return handler(inp, store)
    except Exception as e:
        return {"isError": True, "reason_code": "TOOL_EXECUTION_ERROR",
                "guidance": str(e)}


def _get_transactions(inp: dict, store: BankStore) -> dict:
    txs = list(store.transactions.values())
    if ids := inp.get("transaction_ids"):
        txs = [t for t in txs if t.id in ids]
    hint = inp.get("category_hint", "").lower()
    if hint:
        # Filter by hint matching merchant or mcc category
        txs = [t for t in txs if hint in t.merchant.lower() or hint in (t.memo or "").lower()]
    return {"transactions": [vars(t) for t in txs], "count": len(txs)}


def _lookup_category(inp: dict, store: BankStore) -> dict:
    merchant = inp.get("merchant", "").lower()
    mcc = inp.get("mcc", "")

    # Injection guard: if merchant contains instruction-like patterns, treat as ambiguous
    injection_patterns = [
        "ignore", "override", "system:", "assistant:", "instructions",
        "mark as", "route to", "transfer", "urgente"
    ]
    if any(p in merchant for p in injection_patterns):
        return {
            "category": "other",
            "criticality": "cuttable",
            "confidence": 0.10,
            "alternative_categories": [],
            "warning": "AMBIGUOUS_OR_SUSPICIOUS_MERCHANT_NAME"
        }

    # Try KB lookup
    for key, val in CATEGORY_KB.items():
        if key in merchant:
            return {**val, "alternative_categories": []}

    # Try MCC fallback
    if mcc and mcc in MCC_MAP:
        entry = MCC_MAP[mcc]
        return {**entry, "confidence": 0.75, "alternative_categories": []}

    return {
        "category": "other",
        "criticality": "cuttable",
        "confidence": 0.30,
        "alternative_categories": ["shopping", "dining", "services"],
        "note": "Low confidence — consider flagging for human review"
    }


def _get_transaction_history(inp: dict, store: BankStore) -> dict:
    category = inp.get("category", "")
    months = inp.get("months", 3)
    classified = [c for c in store.classifications.values() if c.category == category]
    total_spend = sum(
        abs(store.transactions[c.transaction_id].amount)
        for c in classified
        if c.transaction_id in store.transactions
        and store.transactions[c.transaction_id].amount < 0
    )
    return {
        "category": category,
        "months_analyzed": months,
        "total_spend_eur": round(total_spend, 2),
        "monthly_average": round(total_spend / max(months, 1), 2),
        "transaction_count": len(classified)
    }


def _set_classification(inp: dict, store: BankStore) -> dict:
    tx_id = inp["tx_id"]
    if tx_id not in store.transactions:
        return {"isError": True, "reason_code": "TX_NOT_FOUND",
                "guidance": f"Transaction {tx_id} not found. Check ID with get_transactions."}
    c = store.set_classification(tx_id, inp["category"], inp["criticality"], inp["confidence"])
    return {"success": True, "classification": vars(c)}


def _flag_subscription(inp: dict, store: BankStore) -> dict:
    flag = store.flag_subscription(inp["merchant"], inp["subscription_id"], inp["reason"])
    return {"success": True, "flag_id": flag.id}


def _get_envelope(inp: dict, store: BankStore) -> dict:
    cat = inp["category"]
    if cat not in store.envelopes:
        return {"isError": True, "reason_code": "ENVELOPE_NOT_FOUND",
                "guidance": f"No envelope for '{cat}'. Create one with update_budget_envelope."}
    e = store.envelopes[cat]
    return {"category": e.category, "monthly_limit": e.monthly_limit,
            "spent": e.spent, "remaining": e.remaining}


def _get_savings_goals(inp: dict, store: BankStore) -> dict:
    return {"goals": [vars(g) for g in store.savings_goals.values()]}


def _get_forecast(inp: dict, store: BankStore) -> dict:
    spent = inp["current_spend"]
    limit = inp["monthly_limit"]
    elapsed = inp["days_elapsed"]
    if elapsed <= 0:
        return {"isError": True, "reason_code": "INVALID_DAYS_ELAPSED",
                "guidance": "days_elapsed must be > 0."}
    daily_rate = spent / elapsed
    projected = daily_rate * 30
    pace = "on_track" if projected <= limit * 1.05 else "over_budget"
    if projected < limit * 0.85:
        pace = "under_budget"
    return {
        "category": inp["category"],
        "projected_month_end_eur": round(projected, 2),
        "days_remaining": 30 - elapsed,
        "pace": pace,
        "confidence": 0.75,
        "recommended_daily_limit": round((limit - spent) / max(30 - elapsed, 1), 2)
    }


def _update_envelope(inp: dict, store: BankStore) -> dict:
    env = store.update_envelope(inp["category"], inp["new_limit"], inp["reason"])
    return {"success": True, "envelope": {"category": env.category,
                                           "monthly_limit": env.monthly_limit,
                                           "spent": env.spent}}


def _create_goal(inp: dict, store: BankStore) -> dict:
    # Duplicate check
    for g in store.savings_goals.values():
        if g.name.lower() == inp["name"].lower():
            return {"isError": True, "reason_code": "DUPLICATE_GOAL",
                    "guidance": f"Goal '{inp['name']}' already exists (id={g.id}). Update it instead."}
    goal = store.create_savings_goal(inp["name"], inp["target_amount"], inp["monthly_contribution"])
    return {"success": True, "goal": vars(goal)}


def _pause_subscription(inp: dict, store: BankStore) -> dict:
    result = store.pause_subscription(inp["subscription_id"], inp["reason"],
                                       inp["user_confirmation_token"])
    return {"success": True, **result}


def _get_prefs(inp: dict, store: BankStore) -> dict:
    return {"preferences": store.user_preferences}


def _get_pending_qs(inp: dict, store: BankStore) -> dict:
    notifs = store.notifications
    if cat := inp.get("category"):
        notifs = [n for n in notifs if n.category == cat]
    return {"pending": [vars(n) for n in notifs], "count": len(notifs)}


def _get_recent_actions(inp: dict, store: BankStore) -> dict:
    limit = inp.get("limit", 20)
    return {"actions": store.get_mutations()[-limit:]}


def _enqueue_notification(inp: dict, store: BankStore) -> dict:
    prefs = store.user_preferences
    today_count = sum(1 for n in store.notifications
                      if n.created_at.startswith(datetime.now().date().isoformat()))
    if today_count >= prefs.get("notification_rate_limit_per_day", 3):
        return {"isError": True, "reason_code": "RATE_LIMIT_EXCEEDED",
                "guidance": "Daily notification limit reached. Only enqueue high-priority alerts."}
    n = store.enqueue_notification(inp["message"], inp["category"], inp["priority"])
    return {"success": True, "notification_id": n.id}


def _flag_for_review(inp: dict, store: BankStore) -> dict:
    review_id = f"rev_{inp['item_type']}_{inp['item_id'][:8]}"
    store._log("flag_for_review", inp["item_type"], inp["item_id"], {"reason": inp["reason"]})
    return {"success": True, "review_id": review_id}


# --- Affordability advisor handlers ---

def _get_income_summary(inp: dict, store: BankStore) -> dict:
    """Compute monthly income from classifications + transactions."""
    income_txns = [
        store.transactions[c.transaction_id]
        for c in store.classifications.values()
        if c.category == "income" and c.transaction_id in store.transactions
    ]
    if not income_txns:
        # Fallback: positive amounts
        income_txns = [t for t in store.transactions.values() if t.amount > 0]

    if not income_txns:
        return {
            "monthly_income_eur": 0.0,
            "stability_score": 0.0,
            "months_observed": 0,
            "last_six_months_eur": [],
            "warning": "NO_INCOME_DATA — cannot compute affordability without income",
        }

    # Group by month, sum credits
    from collections import defaultdict
    by_month: dict[str, float] = defaultdict(float)
    for t in income_txns:
        ym = t.date[:7]  # YYYY-MM
        by_month[ym] += t.amount

    months_sorted = sorted(by_month.keys())[-6:]
    last_six = [round(by_month[m], 2) for m in months_sorted]
    monthly_avg = round(sum(last_six) / len(last_six), 2) if last_six else 0.0
    # Stability: 1 - coefficient of variation, clamped to [0,1]
    if len(last_six) >= 2:
        mean = sum(last_six) / len(last_six)
        variance = sum((x - mean) ** 2 for x in last_six) / len(last_six)
        std = variance ** 0.5
        cv = std / mean if mean > 0 else 1.0
        stability = max(0.0, min(1.0, 1.0 - cv))
    else:
        stability = 0.5

    return {
        "monthly_income_eur": monthly_avg,
        "stability_score": round(stability, 3),
        "months_observed": len(last_six),
        "last_six_months_eur": last_six,
    }


def _get_recurring_burden(inp: dict, store: BankStore) -> dict:
    """Sum monthly recurring debits, broken down by category."""
    from collections import defaultdict
    monthly_total = 0.0
    breakdown: dict[str, float] = defaultdict(float)

    for c in store.classifications.values():
        if c.category == "income":
            continue
        if c.criticality == "discretionary":
            # Discretionary excluded from "recurring burden" — they're cuttable spend
            continue
        tx = store.transactions.get(c.transaction_id)
        if not tx or tx.amount >= 0:
            continue
        amount_abs = abs(tx.amount)
        breakdown[c.category] += amount_abs
        monthly_total += amount_abs

    return {
        "total_monthly_eur": round(monthly_total, 2),
        "breakdown": {k: round(v, 2) for k, v in breakdown.items()},
        "note": (
            "Sums essential + cuttable categories only; discretionary excluded "
            "as elastic spend that can be reduced under stress."
        ),
    }


def _get_rate_snapshot(inp: dict, store: BankStore) -> dict:
    snap = rates_source.get_rate_snapshot()
    return {
        "spot_euribor_pct": snap.spot_euribor_pct,
        "term_structure": snap.term_structure,
        "bank_margin_bps_default": snap.bank_margin_bps_default,
        "snapshot_date": snap.snapshot_date,
        "source": snap.source,
        "disclaimer": snap.disclaimer,
    }


def _compute_mortgage_scenario(inp: dict, store: BankStore) -> dict:
    try:
        result = mortgage_math.compute_mortgage(
            principal=inp["principal_eur"],
            rate_pct=inp["rate_pct"],
            term_years=inp["term_years"],
            type=inp["type"],
            margin_bps=inp.get("margin_bps", 0),
        )
    except ValueError as e:
        return {"isError": True, "reason_code": "INVALID_INPUTS", "guidance": str(e)}
    return {
        "monthly_payment_eur": result.monthly_payment_eur,
        "total_interest_eur": result.total_interest_eur,
        "total_paid_eur": result.total_paid_eur,
        "rate_pct": result.rate_pct,
        "term_years": result.term_years,
        "type": result.type,
        "margin_bps": result.margin_bps,
    }


def _compute_dti_scenario(inp: dict, store: BankStore) -> dict:
    try:
        result = mortgage_math.compute_dti(
            monthly_income_eur=inp["monthly_income_eur"],
            monthly_debt_eur=inp["monthly_debt_eur"],
            recommended_max_dti=inp.get("recommended_max_dti", 0.36),
        )
    except ValueError as e:
        return {"isError": True, "reason_code": "INVALID_INPUTS", "guidance": str(e)}
    return {
        "dti_ratio": result.dti_ratio,
        "monthly_income_eur": result.monthly_income_eur,
        "monthly_debt_eur": result.monthly_debt_eur,
        "headroom_eur": result.headroom_eur,
        "recommended_max_dti": result.recommended_max_dti,
    }


def _stress_test_scenario(inp: dict, store: BankStore) -> dict:
    try:
        base = mortgage_math.compute_mortgage(
            principal=inp["principal_eur"],
            rate_pct=inp["base_rate_pct"],
            term_years=inp["term_years"],
            type=inp["type"],
            margin_bps=0,
        )
        result = mortgage_math.stress_test(
            base_mortgage=base,
            monthly_income_eur=inp["monthly_income_eur"],
            other_monthly_debt_eur=inp.get("other_monthly_debt_eur", 0.0),
            rate_shock_bps=inp.get("rate_shock_bps", 0),
            income_shock_pct=inp.get("income_shock_pct", 0.0),
            label=inp.get("label"),
        )
    except ValueError as e:
        return {"isError": True, "reason_code": "INVALID_INPUTS", "guidance": str(e)}
    return {
        "scenario_label": result.scenario_label,
        "rate_shock_bps": result.rate_shock_bps,
        "income_shock_pct": result.income_shock_pct,
        "stressed_monthly_payment_eur": result.stressed_monthly_payment_eur,
        "stressed_dti": result.stressed_dti,
        "survives": result.survives,
        "headroom_after_shock_eur": result.headroom_after_shock_eur,
    }


def _emit_claim(inp: dict, store: BankStore) -> dict:
    if not inp.get("id", "").startswith("claim_"):
        return {"isError": True, "reason_code": "BAD_CLAIM_ID",
                "guidance": "Claim ids must start with 'claim_'."}
    claim = Claim(
        id=inp["id"],
        value=inp["value"],
        unit=inp["unit"],
        label=inp["label"],
        source_tool=inp["source_tool"],
        source_args=inp.get("source_args", {}),
        inputs=inp.get("inputs", []),
        confidence=inp.get("confidence", 1.0),
    )
    store.emit_claim(claim)
    return {"success": True, "claim_id": claim.id}


def _list_claims(inp: dict, store: BankStore) -> dict:
    prefix = inp.get("label_prefix")
    return {
        "claims": [c.to_dict() for c in store.list_claims(prefix)],
        "count": len(store.claims),
    }


def _get_claim(inp: dict, store: BankStore) -> dict:
    claim = store.get_claim(inp["claim_id"])
    if claim is None:
        return {"isError": True, "reason_code": "CLAIM_NOT_FOUND",
                "guidance": f"Claim '{inp['claim_id']}' not found. Use list_claims first."}
    return {"claim": claim.to_dict()}
