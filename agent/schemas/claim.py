"""
Claim — the spine of the affordability advisor.

Every numeric value the Advisor renders to the user must be bound to a Claim
with provenance. The Stop hook rejects termination if any number in the report
isn't backed by a Claim ID.

This is what makes our advice mechanically auditable rather than vibes-checked,
and it is the artifact a bank's compliance team would lean on for pre-launch sign-off.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any


@dataclass
class Claim:
    """A single numeric assertion with full provenance.

    Stable IDs follow `claim_<label>_<period>` so the Advisor's inline references
    survive across runs of the same persona.
    """

    id: str                              # e.g. "claim_max_principal_2026_04"
    value: float | int
    unit: str                            # "EUR", "EUR/month", "%", "bps", "ratio"
    label: str                           # "max_affordable_principal", "monthly_dti"
    source_tool: str                     # which tool produced this
    source_args: dict[str, Any] = field(default_factory=dict)
    inputs: list[str] = field(default_factory=list)   # other Claim ids this depends on
    confidence: float = 1.0              # 0..1
    ts: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# --- Validation utilities used by the Stop hook -----------------------------

# Matches numeric tokens that look like advisable figures: 1234, 1.234,56, 12,5%, €350k
_NUMBER_TOKEN = re.compile(
    r"(?<![A-Za-z_\[])"            # not part of an identifier or claim ref
    r"(?:€\s*)?\d[\d.,]*\s*(?:%|EUR|€|bps|k|m|mln|mila)?"
    r"(?![A-Za-z_])",
    re.IGNORECASE,
)
_CLAIM_REF = re.compile(r"\[(claim_[a-z0-9_]+)\]")

# A claim ref binds a number when it appears AFTER the number, within this
# many chars. The Advisor's contract is to write `<figure> [claim_id]` —
# the citation follows the figure. Forward-only proximity catches the failure
# mode where two different numbers share a single citation.
_BINDING_FORWARD_CHARS = 30


def find_unbound_numbers(report_md: str) -> list[str]:
    """Return numeric tokens in the report that have no claim reference nearby.

    A number is considered bound if a `[claim_*]` reference appears within
    `_BINDING_FORWARD_CHARS` characters AFTER it (forward only). Numbers that
    appear without a trailing citation are unbound — this is the failure mode
    where the Advisor invents a figure and hides it next to a legit citation.
    """
    ref_starts = [m.start() for m in _CLAIM_REF.finditer(report_md)]

    unbound = []
    for match in _NUMBER_TOKEN.finditer(report_md):
        token = match.group(0).strip()
        # Skip 4-digit standalone years, list ordinals like "1.", and table separators
        if re.fullmatch(r"\d{4}", token):
            continue
        if re.fullmatch(r"\d+\.", token):
            continue
        end = match.end()
        is_bound = any(end <= rp <= end + _BINDING_FORWARD_CHARS for rp in ref_starts)
        if not is_bound:
            unbound.append(token)
    return unbound


def claim_ids_in_report(report_md: str) -> list[str]:
    return _CLAIM_REF.findall(report_md)
