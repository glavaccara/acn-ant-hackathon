# CLAUDE.md — `presentation/`

Context for future agents working inside this folder. The full project conventions live in [`../CLAUDE.md`](../CLAUDE.md); this file only documents what is specific to the **pitch package**.

## Purpose
This folder is the **client-facing deliverable** for the Cash Compass hackathon entry (Scenario 5, Team Fuzzy Flask). Its job is to sell the product to a retail-bank decision-maker and to be hand-off-able to a reader who has never seen the codebase.

It is *not* application code. Do not import anything from here in `agent/` or `eval/`.

## Files
- `index.html` — single-file HTML pitch deck (no external assets, runs offline). Slides covered:
  1. Cover
  2. Problem (banks losing to fintechs; passive PFM)
  3. Product (Cash Compass — two surfaces)
  4. Triage path demo
  5. Affordability path demo (Claim-based, Stop-hook gated)
  6. Architecture diagram (ASCII / CSS)
  7. Why Claude Code — wins
  8. Why Claude Code — drawbacks (be honest)
  9. Evidence: eval scorecard + safety mechanisms
  10. Roadmap & Ask
- `README.md` — hackathon-template README (Team / Scenario / What We Built / Challenges / Decisions / How to Run / If We Had More Time / How We Used Claude Code).
- `CLAUDE.md` — this file.

## Editing rules
- **Self-contained HTML.** `index.html` must work when opened directly from disk via `file://`. No CDN scripts, no external fonts, no remote images. Inline everything (CSS, SVG, any JS).
- **Keep the deck honest.** The "drawbacks" slide is load-bearing for credibility with a banking audience. Do not soften it into marketing fluff. If a Claude Code limitation is real (cost, latency, non-determinism, eval cost, vendor lock-in), it stays in the deck.
- **No new product claims.** Every figure or capability cited in the pitch must be backed by something that actually exists in the repo. If you add a slide, verify against `agent/`, `eval/`, or `docs_m/` first — same discipline as the Advisor's Claim rule.
- **README mirrors hackathon template.** Do not restructure the README sections; judges grade against the template shape.
- **Markdown links use repo-relative paths** (`../agent/hooks/pre_tool_use.py`), not absolute paths, so the docs work both in GitHub and locally.

## What NOT to do here
- Don't import or re-implement product code in this folder.
- Don't add a build step (no Vite, no bundler). The deliverable is a static folder a non-engineer can email.
- Don't generate PDFs unless explicitly asked — printing from the browser is enough.
- Don't add tracking, analytics, or remote calls of any kind. This is a client-facing artifact for a regulated-industry audience.

## How to preview
```bash
open presentation/index.html        # macOS
xdg-open presentation/index.html    # Linux
```
Arrow keys / space to navigate slides.
