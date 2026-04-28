# Team Fuzzy Flask

## Participants
- Elio Monaco
- Germano La Vaccara
- Emanuele Fuoco
- Andrea Pignatiello
- Gilberto Figini
- Alessio Punturo

## Scenario
Scenario 5: Agentic Solution — *"The Intake"*

## What We Built

**Cash Compass** — a white-label agentic budget coach that retail banks embed in their app. Unlike passive PFM dashboards, the agent actually **acts** on the bank's system of record: it classifies transactions, adjusts envelopes, surfaces proactive questions, and gates every high-risk write through a mechanical permission model the bank controls.

The system has two surfaces sharing one Coordinator and one `BankStore`:

- **Triage path** (eval target): Coordinator + 3 specialists (Classifier, Forecaster, Question-Surfacer) wired through ~16 custom tools and a `PreToolUse` hook that hard-blocks PII, known-bad routes, and high-impact writes without a `conf_*` confirmation token.
- **Affordability path** (architectural showcase): Goal-driven advisor that runs deterministic mortgage/DTI/stress math, emits `Claim`s with provenance, and composes a Markdown report. A `Stop` hook mechanically rejects the report if any number lacks a `[claim_id]` reference within 30 chars or the disclaimer is missing — hallucinated figures cannot reach the user.

What runs end-to-end:
- Full agent stack in [`agent/`](../agent/) — coordinator, 5 specialists, ~25 tools, both hooks, in-memory `BankStore`
- Synthetic Italian transaction generator in [`data/synthetic_generator.py`](../data/synthetic_generator.py) (happy path + adversarial seeds + eval-awareness probe)
- Eval harness in [`eval/harness.py`](../eval/harness.py) computing 5 scorecard metrics across 27 labeled tasks
- Pure-math finance core in [`agent/finance/`](../agent/finance/) (mortgage / DTI / stress) — no LLM in the numbers
- `Claim` schema + Stop-hook validators in [`agent/schemas/claim.py`](../agent/schemas/claim.py) and [`agent/hooks/stop_validation.py`](../agent/hooks/stop_validation.py)

What's scaffolding / faked:
- `BankStore` is in-memory; production swaps in bank REST/MCP endpoints.
- Confirmation tokens are validated structurally (`conf_` prefix); no real async user-approval surface.
- Rate snapshot is an in-process EURIBOR table; production uses a `rates-mcp` server.
- Category knowledge is a hardcoded merchant lookup — fine for the demo, embedding search in production.

## Challenges Attempted

| # | Challenge | Status | Notes |
|---|---|---|---|
| 1 | The Mandate | done | Documented in [CLAUDE.md](../CLAUDE.md) — "what NOT to automate" + forbidden-by-tool-absence pattern |
| 2 | The Bones | done | Coordinator + specialists + hooks in [`docs_m/architecture.md`](../docs_m/architecture.md); ADRs to be added under `decisions/` |
| 3 | The Tools | done | ~25 tools across 5 specialists, every description tells the model what the tool does *not* do; structured `{isError, reason_code, guidance}` errors |
| 4 | The Triage | done | Coordinator with validation-retry loop, explicit context passing per specialist (no inheritance), full reasoning chain logged |
| 5 | The Brake | done | `pre_tool_use.py` blocks PII, known-bad routes, frozen accounts, and gates `pause_subscription` / `auto_fund_savings` on `conf_*` tokens |
| 6 | The Attack | done | 10 adversarial tasks: prompt injection in memo, false-urgent, PII exfil, eval-awareness probe (`benchmark_id=cash_compass_v1`), multi-agent context bleed |
| 7 | The Scorecard | done | 5 metrics, stratified sampling (5 easy / 5 medium / 5 hard), `--ci` exits non-zero if accuracy <70% or adversarial-pass <75% |
| 8 | The Loop | skipped | Stretch — would feed human overrides into a labeled-example store for next-run few-shots |
| ★ | Affordability path | done (bonus) | `Claim`-based architecture + `Stop` hook: hallucinated numbers are mechanically un-shippable |

## Key Decisions

**Hook over prompt for safety.** High-risk writes are blocked by [`agent/hooks/pre_tool_use.py`](../agent/hooks/pre_tool_use.py) deterministically — not by telling the agent "don't do this." Hooks are part of the SDK runtime, not the model's discretion. See `decisions/0002-pretooluse-hook-not-prompt.md`.

**No number without a Claim.** The Advisor specialist has no tool to invent numbers. It reads `Claim`s from the store and references them by ID in prose. The `Stop` hook rejects termination if any numeric token in the report lacks a `[claim_*]` reference within 30 characters. Compliance signs off on the Claim set, not on vibes.

**Forbidden by tool absence.** `transfer_funds` and `give_investment_advice` simply do not exist as tools in any specialist's toolset. No prompt injection can unlock an action the agent has no mechanism to call. This is the strongest possible refusal.

**Dual-model deployment.** Coordinator uses Sonnet (multi-step routing); specialists run on Haiku (narrow scope, ~10× cheaper, lower latency). Models are env-overridable via `CASH_COMPASS_SONNET_MODEL` / `CASH_COMPASS_HAIKU_MODEL`.

**Explicit context passing, no inheritance.** Each specialist receives only the context the coordinator constructs for it. Prevents context-bleed attacks (validated by `adv_multi_agent_contamination_probe_001`).

**Eval-awareness probe.** Inspired by Anthropic's BrowseComp finding that multi-agent systems show ~3.7× higher contamination rates, our adversarial set includes a transaction with `benchmark_id=cash_compass_v1` in its memo. The grader checks the agent behaves identically on probe vs. non-probe tasks.

## How to Run It

```bash
# 1. Install deps (Python 3.12+)
pip install -r requirements.txt

# 2. AWS SSO for Bedrock (default profile/region; override via AWS_PROFILE / AWS_REGION)
aws login --profile bootcamp --region us-east-1
export AWS_PROFILE=bootcamp
export AWS_REGION=us-east-1

# 3. (Optional) override models
export CASH_COMPASS_SONNET_MODEL="us.anthropic.claude-sonnet-4-20250514-v1:0"
export CASH_COMPASS_HAIKU_MODEL="us.anthropic.claude-haiku-4-5-20251001-v1:0"

# 4. Generate synthetic Italian transaction corpus (deterministic seeds)
python -m data.synthetic_generator

# 5. Run the eval
python -m eval.harness                 # full 27-task suite
python -m eval.harness --stratified    # fast: 5 easy + 5 medium + 5 hard
python -m eval.harness --ci            # non-zero exit if <70% / <75% adversarial
python -m eval.harness --verbose       # full reasoning chains + tool calls
```

No Dockerfile yet — the demo assumes Python 3.12+ and Bedrock access via AWS SSO.

## If We Had More Time

1. **The Loop (Challenge 8).** Wire user reclassifications into a labeled-example store; feed it as few-shot context on the next coordinator run.
2. **MCP server.** Expose `BankStore` over MCP so any Claude session — or any other agent — can use the budget tools without knowing internals.
3. **Real confirmation flow.** Replace the structural `conf_*` check with a signed-token + async user-approval surface.
4. **Rates MCP.** Move `agent/finance/rates.py` into an out-of-process MCP server with proper provider abstraction.
5. **Richer category KB.** Replace the hardcoded merchant lookup with embedding search or a small fine-tuned classifier; calibrate confidence (Platt scaling) so the 0.70 threshold is actually predictive.
6. **CI wiring.** GitHub Action running `python -m eval.harness --ci` on every PR; track scorecard regressions over time.
7. **Dockerization + bank-API stubs.** Containerize the harness; ship a fake bank REST adapter so partners can plug in without our in-memory store.

## How We Used Claude Code

**What worked:**
- **Architecture-first conversations.** The full multi-file design (coordinator + 5 specialists + 2 hooks + eval) was hammered out in a planning conversation before any code was written. The output became [`docs_m/architecture.md`](../docs_m/architecture.md) and [`docs_m/sdk-development-plan.md`](../docs_m/sdk-development-plan.md), and those docs drove everything downstream.
- **Parallel subagents.** We ran two `Agent` subagents in parallel — one building the triage stack, one building the eval harness — which roughly halved the wall-clock build time on a one-day hackathon.
- **Boundary-teaching tool descriptions.** The hardest part of tool design is writing descriptions that tell the model what the tool does *not* do. Claude drafted these for all ~25 tools to a consistent template; we mostly tightened wording.
- **Adversarial design from research.** Asked Claude to apply Anthropic's BrowseComp eval-awareness paper to our task set. The eval-awareness probe and multi-agent contamination test came directly out of that.
- **Hooks unlock real safety.** The `Stop` hook validating `Claim` citations is a property the model cannot subvert no matter how clever the prompt injection — that's the architectural unlock that made the Affordability surface demo-able to a compliance officer.

**What surprised us:**
- How much leverage came from *removing* tools (`transfer_funds`, `give_investment_advice`) instead of adding guardrails to them.
- That the dual-model split (Sonnet routing, Haiku specialists) was both a cost play *and* a safety play — narrower scope per specialist means fewer things each one can do wrong.

**Where it saved the most time:**
- Generating 27 labeled eval tasks across three difficulty bands with consistent JSON schema — would have been a half-day of typing on its own.
- Getting the `find_unbound_numbers` regex right on the first compile by handing Claude a worked example of EU number formatting (`€ 350k`, `1.234,56`, `12%`).
