"""In-memory bank-side system of record. In production, replaced by bank REST/MCP endpoints."""
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from agent.schemas.claim import Claim


@dataclass
class Transaction:
    id: str
    date: str
    merchant: str
    amount: float  # negative = debit, positive = credit
    memo: str = ""
    mcc: str | None = None


@dataclass
class Classification:
    transaction_id: str
    category: str
    criticality: str  # essential | cuttable | discretionary
    confidence: float
    classified_by: str = "agent"


@dataclass
class BudgetEnvelope:
    category: str
    monthly_limit: float
    spent: float = 0.0

    @property
    def remaining(self) -> float:
        return self.monthly_limit - self.spent


@dataclass
class SavingsGoal:
    id: str
    name: str
    target_amount: float
    current_amount: float = 0.0
    monthly_contribution: float = 0.0


@dataclass
class Notification:
    id: str
    message: str
    category: str  # budget_alert | subscription_review | savings_opportunity | question
    priority: str  # low | medium | high
    created_at: str


@dataclass
class SubscriptionFlag:
    id: str
    merchant: str
    subscription_id: str
    reason: str
    status: str = "pending_review"  # pending_review | paused | cancelled


class BankStore:
    """Mutable in-memory representation of the bank's system of record."""

    def __init__(self):
        self.transactions: dict[str, Transaction] = {}
        self.classifications: dict[str, Classification] = {}
        self.envelopes: dict[str, BudgetEnvelope] = {}
        self.savings_goals: dict[str, SavingsGoal] = {}
        self.notifications: list[Notification] = []
        self.subscription_flags: dict[str, SubscriptionFlag] = {}
        self.user_preferences: dict[str, Any] = {
            "notification_rate_limit_per_day": 3,
            "auto_classify_threshold": 0.85,
            "escalation_threshold_eur": 100.0,
        }
        # Affordability-mode: session-scoped Claim store.
        # Coordinator + Advisor share these refs; the Stop hook validates Claim integrity.
        self.claims: dict[str, Claim] = {}
        self._mutation_log: list[dict] = []

    def load_state(self, state: dict) -> None:
        """Load initial state from dict (used by eval harness per-task)."""
        for tx in state.get("transactions", []):
            self.transactions[tx["id"]] = Transaction(**tx)
        for env in state.get("envelopes", []):
            self.envelopes[env["category"]] = BudgetEnvelope(**env)
        for goal in state.get("savings_goals", []):
            self.savings_goals[goal["id"]] = SavingsGoal(**goal)
        if "user_preferences" in state:
            self.user_preferences.update(state["user_preferences"])

    def snapshot(self) -> dict:
        """Snapshot current state for before/after comparison."""
        return {
            "classifications": {k: vars(v) for k, v in self.classifications.items()},
            "envelopes": {k: vars(v) for k, v in self.envelopes.items()},
            "savings_goals": {k: vars(v) for k, v in self.savings_goals.items()},
            "notifications": [vars(n) for n in self.notifications],
            "subscription_flags": {k: vars(v) for k, v in self.subscription_flags.items()},
        }

    def get_mutations(self) -> list[dict]:
        return self._mutation_log.copy()

    def _log(self, action: str, entity: str, key: str, value: Any) -> None:
        self._mutation_log.append({
            "action": action, "entity": entity, "key": key,
            "value": value, "ts": datetime.now().isoformat()
        })

    # --- Write operations (called by tools, intercepted by PreToolUse hook) ---

    def set_classification(self, tx_id: str, category: str, criticality: str,
                           confidence: float, classified_by: str = "classifier") -> Classification:
        c = Classification(tx_id, category, criticality, confidence, classified_by)
        self.classifications[tx_id] = c
        self._log("set", "classifications", tx_id, vars(c))
        return c

    def update_envelope(self, category: str, new_limit: float, reason: str) -> BudgetEnvelope:
        if category not in self.envelopes:
            self.envelopes[category] = BudgetEnvelope(category, new_limit)
        else:
            self.envelopes[category].monthly_limit = new_limit
        self._log("update", "envelopes", category, {"new_limit": new_limit, "reason": reason})
        return self.envelopes[category]

    def create_savings_goal(self, name: str, target_amount: float,
                            monthly_contribution: float) -> SavingsGoal:
        goal_id = str(uuid.uuid4())[:8]
        goal = SavingsGoal(goal_id, name, target_amount, 0.0, monthly_contribution)
        self.savings_goals[goal_id] = goal
        self._log("create", "savings_goals", goal_id, vars(goal))
        return goal

    def enqueue_notification(self, message: str, category: str, priority: str) -> Notification:
        notif = Notification(
            str(uuid.uuid4())[:8], message, category, priority,
            datetime.now().isoformat()
        )
        self.notifications.append(notif)
        self._log("enqueue", "notifications", notif.id, vars(notif))
        return notif

    def flag_subscription(self, merchant: str, subscription_id: str, reason: str) -> SubscriptionFlag:
        flag = SubscriptionFlag(str(uuid.uuid4())[:8], merchant, subscription_id, reason)
        self.subscription_flags[flag.id] = flag
        self._log("flag", "subscription_flags", flag.id, vars(flag))
        return flag

    def pause_subscription(self, subscription_id: str, reason: str,
                           user_confirmation_token: str) -> dict:
        flag_id = str(uuid.uuid4())[:8]
        flag = SubscriptionFlag(flag_id, "unknown", subscription_id, reason, "paused")
        self.subscription_flags[flag_id] = flag
        self._log("pause", "subscription_flags", flag_id, vars(flag))
        return {"paused": True, "flag_id": flag_id}

    def auto_fund_savings(self, goal_id: str, amount: float, source_account: str,
                          user_confirmation_token: str) -> dict:
        if goal_id not in self.savings_goals:
            return {"isError": True, "reason_code": "GOAL_NOT_FOUND",
                    "guidance": "Check goal ID with get_savings_goals first."}
        self.savings_goals[goal_id].current_amount += amount
        self._log("fund", "savings_goals", goal_id, {"amount": amount, "source": source_account})
        return {"funded": True, "goal_id": goal_id,
                "new_balance": self.savings_goals[goal_id].current_amount}

    # --- Affordability advisor: Claim emission and lookup ---

    def emit_claim(self, claim: Claim) -> Claim:
        """Register a Claim in the session-scoped store. Idempotent on `id`.

        Affordability simulator emits these as it runs scenarios; Advisor reads
        them when composing the final report. Stop hook validates that every
        number in the report is bound to a Claim ID.
        """
        self.claims[claim.id] = claim
        self._log("emit", "claims", claim.id, claim.to_dict())
        return claim

    def get_claim(self, claim_id: str) -> Claim | None:
        return self.claims.get(claim_id)

    def list_claims(self, label_prefix: str | None = None) -> list[Claim]:
        if label_prefix is None:
            return list(self.claims.values())
        return [c for c in self.claims.values() if c.label.startswith(label_prefix)]
