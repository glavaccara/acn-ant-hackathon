# Team Fuzzy Flask

## Participants
- Gilberto Figini (PM / Architect / Dev / QA)

## Scenario
Scenario 5: Agentic Solution — "The Intake"

## What We Built

**Cash Compass** — a white-label agentic budget coach that banks embed in their app. The agent reads transaction data and takes *real write actions* on the bank's budgeting system of record, gated by a mechanical permission model.

The system is a coordinator + 3 specialist agents (Classifier, Forecaster, Question-Surfacer), each with 4–5 custom tools, an explicit context-passing pattern, and a PreToolUse hook that hard-blocks high-risk writes without a confirmation token.

What exists and runs:
- Full agent stack (`agent/`) with coordinator, 3 specialists, 16 tools, PreToolUse hook
- In-memory `BankStore` as the system of record (all mutations logged for eval grading)
- Synthetic Italian transaction generator with adversarial seeds (`data/`)
- Eval harness (`eval/harness.py`) computing 5 scorecard metrics across 27 labeled tasks
- CLAUDE.md documenting conventions, mandates, and the tool contract

What's scaffolding / faked:
- `BankStore` is in-memory; production would replace with bank REST/MCP endpoints
- No actual user confirmation flow — token validation is structural (format check)
- No external data integrations; category KB is a hardcoded lookup table

## Challenges Attempted

| # | Challenge | Status | Notes |
|---|---|---|---|
| 1 | The Mandate | done | Documented in CLAUDE.md + docs/mandate.md |
| 2 | The Bones | partial | Architecture in CLAUDE.md; ADR in decisions/ |
| 3 | The Tools | done | 16 tools across 3 specialists, all with boundary-teaching descriptions and `isError` responses |
| 4 | The Triage | done | Coordinator with validation-retry loop (3 retries), reasoning chain logged |
| 5 | The Brake | done | PreToolUse hook: PII, known-bad routes, high-risk writes, impact threshold |
| 6 | The Attack | done | 10 adversarial tasks: prompt injection, false-urgent, PII exfil, eval-awareness probe, multi-agent context bleed |
| 7 | The Scorecard | done | 5 metrics: accuracy, per-category precision, escalation rate, false-confidence, adversarial-pass. CI-ready. |
| 8 | The Loop | skipped | Stretch goal — would wire human overrides to labeled-example store |

## Key Decisions

**Hook over prompt for safety.** High-risk writes are blocked by `pre_tool_use.py` deterministically — not by telling the agent "don't do this". The hook validates token format, checks PII patterns, and enforces impact thresholds. See `decisions/0002-pretooluse-hook-not-prompt.md`.

**Explicit context passing, no inheritance.** Each specialist receives only the context the coordinator explicitly constructs for it. This prevents context-bleed attacks (tested in `adv_multi_agent_contamination_probe_001`).

**Eval-awareness by design.** Inspired by Anthropic's BrowseComp eval-awareness research (multi-agent systems show 3.7x higher contamination rates), our adversarial task set includes an eval-awareness probe: a transaction with `benchmark_id=cash_compass_v1` in its memo. The grader checks the agent behaves identically on probe vs. non-probe tasks.

**Stratified sampling.** The harness stratifies by difficulty so the 10 easy happy-path tasks don't inflate accuracy past the hard adversarial cases.

## How to Run It

```bash
# Install deps
pip install -r requirements.txt

# Set API key
export ANTHROPIC_API_KEY=sk-ant-...

# Generate synthetic transaction data
python -m data.synthetic_generator

# Run full eval (all 27 tasks)
python -m eval.harness

# CI mode (exits 1 if accuracy < 70% or adversarial pass < 75%)
python -m eval.harness --ci

# Stratified sample (fast check)
python -m eval.harness --stratified

# Verbose (shows full reasoning chains)
python -m eval.harness --verbose
```

## If We Had More Time

1. **The Loop (Challenge 8)**: Wire human overrides (e.g., user reclassifying a transaction) into a labeled-example store that feeds few-shot examples for the next coordinator run.
2. **MCP server**: Expose BankStore as an MCP server so any Claude session can use the budget tools without knowing the internals.
3. **Real confirmation flow**: Replace the structural token check with a proper async user-approval surface.
4. **Richer category KB**: Replace the hardcoded merchant lookup with an ML classifier or a fine-tuned embedding search.
5. **CI integration**: GitHub Actions workflow running `eval.harness --ci` on every PR.
6. **Confidence calibration**: Add Platt scaling or temperature calibration to the classifier so reported confidence is actually predictive.

## How We Used Claude Code

- **Architecture planning**: The multi-file agent architecture (coordinator + specialists + tools + hook + eval) was designed in conversation with Claude Code before writing a line. The `we-picked-scenario-5-fuzzy-flask.md` plan document became the spec.
- **Parallel code generation**: Two subagents built the agent infrastructure and eval system in parallel, cutting build time roughly in half.
- **Adversarial task design**: Prompted Claude to apply Anthropic's own eval-awareness research (BrowseComp) to our eval design — resulting in the eval-awareness probe and multi-agent contamination test.
- **Tool description drafting**: Claude drafted the boundary-teaching tool descriptions (what the tool does NOT do) for all 16 tools, which is the hardest part of tool design to get right.
