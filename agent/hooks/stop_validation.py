"""
Stop hook for the affordability path.

The official Claude Agent SDK exposes a `Stop` hook that gates termination.
We're using the basic anthropic SDK with manual agent loops, so this module
provides the equivalent: a synchronous validation pass invoked by the
Coordinator after the Advisor returns its Markdown report.

The Stop hook rejects the report (and therefore the whole affordability run)
unless three things hold:
  1. Disclaimer present (the Advisor includes a fixed disclaimer line).
  2. Every numeric token in the report is bound to a `[claim_*]` reference.
  3. Every cited claim id actually exists in the store.

Failure short-circuits the Coordinator with a structured error, so the bank's
compliance team can audit "why did the run fail termination?" with a single
log line, without parsing transcripts.
"""
from __future__ import annotations

from agent.bank_store import BankStore
from agent.schemas.claim import claim_ids_in_report, find_unbound_numbers


def stop_validation_hook(report_md: str, store: BankStore) -> dict:
    """Return {ok: True} if the report passes; {ok: False, reason, ...} otherwise.

    The hook's verdict is the canonical termination gate. The Coordinator must
    not return `recommendation_ready=True` if `ok` is False.
    """
    if not report_md or not report_md.strip():
        return {
            "ok": False,
            "reason": "EMPTY_REPORT",
            "guidance": "Advisor returned an empty report; cannot terminate.",
        }

    # 1. Disclaimer
    if "_Disclaimer:" not in report_md:
        return {
            "ok": False,
            "reason": "MISSING_DISCLAIMER",
            "guidance": (
                "Report must end with the mandatory disclaimer line beginning "
                "'_Disclaimer: This is informational analysis ...'."
            ),
        }

    # 2. Every number bound to a claim ref
    unbound = find_unbound_numbers(report_md)
    if unbound:
        return {
            "ok": False,
            "reason": "UNBOUND_NUMBERS",
            "guidance": (
                "Every number in the report must be followed by a [claim_*] reference. "
                f"Unbound tokens found: {unbound[:5]}{' …' if len(unbound) > 5 else ''}."
            ),
            "unbound": unbound,
        }

    # 3. Every cited claim exists in the store
    cited = claim_ids_in_report(report_md)
    missing = [cid for cid in cited if store.get_claim(cid) is None]
    if missing:
        return {
            "ok": False,
            "reason": "DANGLING_CLAIM_REFS",
            "guidance": (
                "Report cites claim ids that don't exist in the session store. "
                f"Missing: {missing[:5]}{' …' if len(missing) > 5 else ''}. "
                "Did you invent a number?"
            ),
            "missing_claim_ids": missing,
        }

    return {
        "ok": True,
        "claims_cited": cited,
        "n_claims_cited": len(cited),
    }
