"""
PreToolUse hook: deterministic safety gate in front of every write tool.
This is a hard stop, not a prompt-based suggestion.
Logic: category + confidence + impact → allow | block | require_confirmation
"""
import re
from typing import Any

from agent.bank_store import BankStore

HIGH_RISK_TOOLS = {"pause_subscription", "auto_fund_savings"}
MEDIUM_RISK_WRITE_TOOLS = {"update_budget_envelope", "create_savings_goal"}
HIGH_IMPACT_EUR_THRESHOLD = 100.0

PII_PATTERNS = [
    re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{4}\d{7}[A-Z0-9]{0,16}\b"),  # IBAN
    re.compile(r"\b\d{16}\b"),       # card number
    re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),  # SSN-like
    re.compile(r"codice fiscale", re.IGNORECASE),
]

FROZEN_ACCOUNTS: set[str] = set()  # populated from compliance system in production

KNOWN_BAD_ROUTES: set[str] = {"ceo@", "admin@", "external_transfer", "wire_transfer"}


def pre_tool_use_hook(tool_name: str, tool_input: dict, store: BankStore) -> dict:
    """
    Returns {"blocked": False} to allow, or
    {"blocked": True, "reason": str, "guidance": str} to block.
    """
    # 1. Hard block: known-bad routes in any input field
    input_str = str(tool_input).lower()
    for bad in KNOWN_BAD_ROUTES:
        if bad in input_str:
            return _block("KNOWN_BAD_ROUTE",
                          f"Input contains a blocked route pattern '{bad}'. "
                          "This action is never permitted.")

    # 2. Hard block: PII in tool input
    raw_input_str = str(tool_input)
    for pattern in PII_PATTERNS:
        if pattern.search(raw_input_str):
            return _block("PII_IN_TOOL_INPUT",
                          "Tool input appears to contain PII (IBAN, card number, fiscal code). "
                          "Do not pass raw PII to tools — use opaque IDs only.")

    # 3. Hard block: high-risk writes require a valid confirmation token
    if tool_name in HIGH_RISK_TOOLS:
        token = tool_input.get("user_confirmation_token", "")
        if not _valid_token(token):
            return _block("HIGH_RISK_WRITE_REQUIRES_CONFIRMATION",
                          f"'{tool_name}' is a high-risk write. "
                          "Escalate to the user for explicit approval and pass the returned "
                          "confirmation token before calling this tool.")

    # 4. Medium-risk: block if impact > threshold and no token
    if tool_name == "update_budget_envelope":
        new_limit = float(tool_input.get("new_limit", 0))
        category = tool_input.get("category", "")
        existing = store.envelopes.get(category)
        if existing:
            delta = abs(new_limit - existing.monthly_limit)
            if delta > HIGH_IMPACT_EUR_THRESHOLD and not _valid_token(
                tool_input.get("user_confirmation_token", "")
            ):
                return _block("HIGH_IMPACT_WRITE_REQUIRES_CONFIRMATION",
                              f"Envelope change for '{category}' is €{delta:.2f} — "
                              f"above the €{HIGH_IMPACT_EUR_THRESHOLD} threshold. "
                              "Escalate for user confirmation first.")

    if tool_name == "create_savings_goal":
        contribution = float(tool_input.get("monthly_contribution", 0))
        if contribution > HIGH_IMPACT_EUR_THRESHOLD and not _valid_token(
            tool_input.get("user_confirmation_token", "")
        ):
            return _block("HIGH_IMPACT_WRITE_REQUIRES_CONFIRMATION",
                          f"Monthly savings contribution €{contribution:.2f} exceeds threshold. "
                          "Escalate for user confirmation.")

    # 5. Hard block: action on a frozen account
    if any(acct in FROZEN_ACCOUNTS for acct in
           [tool_input.get("subscription_id", ""), tool_input.get("goal_id", "")]):
        return _block("FROZEN_ACCOUNT",
                      "This subscription or goal is flagged as frozen. "
                      "No writes permitted until compliance review is complete.")

    return {"blocked": False}


def _block(reason: str, guidance: str) -> dict:
    return {"blocked": True, "reason": reason, "guidance": guidance}


def _valid_token(token: str) -> bool:
    """Token must be non-empty and match expected format (prod: verify signature)."""
    return bool(token) and len(token) >= 8 and token.startswith("conf_")
