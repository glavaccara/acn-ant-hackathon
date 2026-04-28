"""
Cash Compass demo web app.

Run:
    python webapp.py            # http://localhost:5000

Bank-app-style surface for the agent. Three primary actions on the homepage:
  1. Review my budget  — Forecaster runs a budget review on the dining envelope
  2. Subscriptions     — Question-Surfacer scans for unused / near-limit alerts
  3. Can I afford it?  — Affordability advisor with a free-form goal input

All actions are async (fetch -> JSON) so the page never reloads. Results are
rendered as in-app notification cards with a "Show raw JSON" toggle for judges.
"""
from __future__ import annotations

import json
import logging
import time
import traceback
from dataclasses import asdict, is_dataclass
from typing import Any

from flask import Flask, jsonify, render_template_string, request

from agent.bank_store import BankStore
from agent.coordinator import Coordinator

logging.basicConfig(level=logging.WARNING)

app = Flask(__name__)


PERSONA = {
    "name": "Marco Rossi",
    "bank": "Banca Demo",
    "balance_eur": 8_420.50,
    "monthly_income_eur": 2_200.00,
    "month_spent_eur": 628.90,
    "month_saved_eur": 1_571.10,
}


DEFAULTS = {
    "monthly_income_eur": 2200.0,
    "dining_limit_eur": 80.0,
    "dining_spent_eur": 145.0,
    "groceries_limit_eur": 350.0,
    "groceries_spent_eur": 318.40,
    "netflix_unused_days": 73,
}


def _g(o: dict | None, key: str) -> float | int:
    """Read an override or fall back to default."""
    if o and key in o and o[key] not in (None, ""):
        try:
            return float(o[key]) if isinstance(DEFAULTS[key], float) else int(o[key])
        except (ValueError, TypeError):
            return DEFAULTS[key]
    return DEFAULTS[key]


def seed_marco_for_budget_review(store: BankStore, overrides: dict | None = None) -> None:
    state = {
        "transactions": [
            {"id": "tx_din_1", "date": "2026-04-08", "merchant": "Bar del Corso",
             "amount": -8.50, "memo": "caffe", "mcc": "5812"},
            {"id": "tx_din_2", "date": "2026-04-14", "merchant": "Pizzeria Da Mario",
             "amount": -34.00, "memo": "cena", "mcc": "5812"},
            {"id": "tx_din_3", "date": "2026-04-22", "merchant": "Ristorante La Bella",
             "amount": -68.50, "memo": "cena fuori", "mcc": "5812"},
        ],
        "envelopes": [
            {"category": "dining",
             "monthly_limit": _g(overrides, "dining_limit_eur"),
             "spent": _g(overrides, "dining_spent_eur")},
            {"category": "groceries",
             "monthly_limit": _g(overrides, "groceries_limit_eur"),
             "spent": _g(overrides, "groceries_spent_eur")},
        ],
    }
    store.load_state(state)


def seed_marco_for_subscriptions(store: BankStore, overrides: dict | None = None) -> None:
    state = {
        "transactions": [
            {"id": "tx_net", "date": "2026-04-07", "merchant": "Netflix",
             "amount": -17.99, "memo": "subscription", "mcc": "5999"},
        ],
        "envelopes": [
            {"category": "dining",
             "monthly_limit": _g(overrides, "dining_limit_eur"),
             "spent": _g(overrides, "dining_spent_eur")},
            {"category": "groceries",
             "monthly_limit": _g(overrides, "groceries_limit_eur"),
             "spent": _g(overrides, "groceries_spent_eur")},
        ],
    }
    store.load_state(state)


def seed_marco_for_affordability(store: BankStore, overrides: dict | None = None) -> None:
    income = _g(overrides, "monthly_income_eur")
    months = ["2025-11", "2025-12", "2026-01", "2026-02", "2026-03", "2026-04"]
    txns = []
    for m in months:
        txns.extend([
            {"id": f"sal_{m}", "date": f"{m}-15", "merchant": "STIPENDIO",
             "amount": income, "memo": "stipendio", "mcc": None},
            {"id": f"groc_{m}", "date": f"{m}-02", "merchant": "Esselunga",
             "amount": -320.00, "memo": "spesa", "mcc": "5411"},
            {"id": f"util_{m}", "date": f"{m}-03", "merchant": "ENEL Energia",
             "amount": -94.12, "memo": "bolletta", "mcc": "4900"},
            {"id": f"tel_{m}", "date": f"{m}-05", "merchant": "TIM",
             "amount": -29.99, "memo": "telefonia", "mcc": "4813"},
            {"id": f"trn_{m}", "date": f"{m}-18", "merchant": "Trenitalia",
             "amount": -42.00, "memo": "biglietto", "mcc": "4112"},
        ])
    store.load_state({"transactions": txns})
    for m in months:
        store.set_classification(f"sal_{m}", "income", "essential", 0.99, "seed")
        store.set_classification(f"groc_{m}", "groceries", "essential", 0.97, "seed")
        store.set_classification(f"util_{m}", "utilities", "essential", 0.99, "seed")
        store.set_classification(f"tel_{m}", "telecom", "essential", 0.95, "seed")
        store.set_classification(f"trn_{m}", "transport", "essential", 0.95, "seed")


# --- Result serialization -----------------------------------------------------

def _safe(obj: Any) -> Any:
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj
    if is_dataclass(obj):
        return _safe(asdict(obj))
    if isinstance(obj, dict):
        return {k: _safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_safe(v) for v in obj]
    return str(obj)


def coord_to_json(coord_result: Any, store: BankStore, elapsed_s: float, surface: str) -> dict:
    return {
        "surface": surface,
        "elapsed_s": round(elapsed_s, 2),
        "escalation_required": coord_result.escalation_required,
        "error": coord_result.error,
        "validation_retries": coord_result.validation_retries,
        "routing_decision": _safe(coord_result.routing_decision),
        "specialist_results": _safe(coord_result.specialist_results),
        "mutations": _safe(coord_result.mutations),
        "claims": [_safe(c) for c in store.claims.values()],
        "stop_verdicts": [
            _safe(s["verdict"])
            for s in coord_result.reasoning_chain
            if s.get("step", "").startswith("stop_validation")
        ],
        "advisor_report_md": (
            coord_result.specialist_results.get("advisor", {})
            .get("result", {})
            .get("report_md")
        ),
        # Envelopes & subscriptions, post-run, for visualization
        "envelopes": [
            {"category": e.category, "monthly_limit": e.monthly_limit,
             "spent": e.spent, "remaining": e.remaining}
            for e in store.envelopes.values()
        ],
        "notifications": [vars(n) for n in store.notifications],
        "savings_goals": [vars(g) for g in store.savings_goals.values()],
    }


def _run(surface: str, request_payload: dict, seed_fn, overrides: dict | None = None) -> Any:
    store = BankStore()
    seed_fn(store, overrides)
    coord = Coordinator(store)
    t0 = time.time()
    try:
        coord_result = coord.process(request_payload)
    except Exception as e:
        return {
            "surface": surface,
            "elapsed_s": round(time.time() - t0, 2),
            "escalation_required": True,
            "error": f"{type(e).__name__}: {e}",
            "validation_retries": 0, "routing_decision": {}, "specialist_results": {},
            "mutations": [], "claims": [], "stop_verdicts": [],
            "advisor_report_md": None,
            "envelopes": [], "notifications": [], "savings_goals": [],
            "traceback": traceback.format_exc(),
        }
    return coord_to_json(coord_result, store, time.time() - t0, surface)


BUDGET_PRESETS = {
    "dining_over": {
        "label": "Review my budget",
        "request": {
            "type": "budget_review",
            "categories": ["dining"],
            "context": "Dining spend has exceeded limit by €65 for 3 months straight",
        },
    },
    "savings_goal": {
        "label": "Spot a savings opportunity",
        "request": {
            "type": "budget_review",
            "categories": ["groceries"],
            "detected_surplus": 1200.0,
        },
    },
}

QUESTION_PRESETS = {
    "dining_near_limit": {
        "label": "Subscriptions to review",
        "request": {"type": "surface_questions", "focus": "budget_alerts"},
    },
}


# --- Routes -------------------------------------------------------------------

@app.route("/", methods=["GET"])
def index():
    return render_template_string(PAGE, persona=PERSONA)


@app.post("/api/budget")
def api_budget():
    body = request.get_json(silent=True) or {}
    preset_key = body.get("preset", "dining_over")
    overrides = body.get("overrides") or {}
    preset = BUDGET_PRESETS.get(preset_key) or BUDGET_PRESETS["dining_over"]
    payload = _run("budget", preset["request"], seed_marco_for_budget_review, overrides)
    payload["title"] = preset["label"]
    return jsonify(payload)


@app.post("/api/questions")
def api_questions():
    body = request.get_json(silent=True) or {}
    preset_key = body.get("preset", "dining_near_limit")
    overrides = body.get("overrides") or {}
    preset = QUESTION_PRESETS.get(preset_key) or QUESTION_PRESETS["dining_near_limit"]
    payload = _run("questions", preset["request"], seed_marco_for_subscriptions, overrides)
    payload["title"] = preset["label"]
    return jsonify(payload)


@app.post("/api/affordability")
def api_affordability():
    body = request.get_json(silent=True) or {}
    goal = (body.get("goal") or "").strip() or "Posso permettermi una casa da 350.000 EUR con mutuo a 25 anni?"
    overrides = body.get("overrides") or {}
    payload = _run("affordability",
                   {"type": "affordability_advise", "goal": goal},
                   seed_marco_for_affordability, overrides)
    payload["title"] = f"Can I afford it? — {goal}"
    return jsonify(payload)


# --- HTML / CSS / JS ----------------------------------------------------------

PAGE = r"""
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Cash Compass — your AI budget coach</title>
<style>
  :root {
    --bg:#f5f7fb; --card:#ffffff; --ink:#0f172a; --muted:#64748b; --line:#e2e8f0;
    --primary:#0b69ff; --primary-700:#0a55cc; --good:#10b981; --warn:#f59e0b; --bad:#ef4444;
    --soft:#eef3fb;
  }
  * { box-sizing: border-box; }
  body { background: var(--bg); color: var(--ink);
         font: 15px/1.55 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; margin: 0; }
  .wrap { max-width: 1100px; margin: 0 auto; padding: 28px 22px 80px; }

  .topbar { display:flex; align-items:center; justify-content:space-between; margin-bottom:18px; }
  .brand { display:flex; align-items:center; gap:10px; }
  .brand .logo { width:30px; height:30px; border-radius:8px;
    background: linear-gradient(135deg, var(--primary), #5b9bff);
    color:#fff; display:grid; place-items:center; font-weight:800; font-size:14px; }
  .brand .name { font-weight:700; }
  .brand .bank { color: var(--muted); font-size:13px; }
  .topbar .demo-tag { color: var(--muted); font-size:12px; }

  .hero { background: linear-gradient(135deg, #0b3a8a 0%, #0b69ff 100%); color:#fff;
    border-radius:18px; padding:22px 26px; margin-bottom:22px;
    display:grid; grid-template-columns:1.4fr 1fr; gap:24px; align-items:center; }
  .hero .hello { font-size:14px; opacity:.85; margin-bottom:4px; }
  .hero h1 { font-size:24px; margin:0 0 12px 0; font-weight:700; }
  .hero .stats { display:flex; gap:18px; flex-wrap:wrap; }
  .hero .stat { background: rgba(255,255,255,.12); padding:10px 14px; border-radius:10px; min-width:130px; }
  .hero .stat .k { font-size:11px; text-transform:uppercase; letter-spacing:.04em; opacity:.85; }
  .hero .stat .v { font-size:18px; font-weight:700; }
  .hero .balance { text-align:right; }
  .hero .balance .v { font-size:28px; font-weight:800; }
  .hero .balance .k { font-size:12px; text-transform:uppercase; letter-spacing:.04em; opacity:.8; }

  .section-title { font-size:13px; text-transform:uppercase; letter-spacing:.06em;
    color:var(--muted); margin:6px 0 10px 0; font-weight:700; }

  .actions { display:grid; grid-template-columns:1fr 1fr 1fr; gap:16px; margin-bottom:22px; }
  .tile { background:var(--card); border:1px solid var(--line); border-radius:14px;
    padding:18px; display:flex; flex-direction:column; gap:10px; }
  .tile h3 { font-size:16px; margin:0; }
  .tile .desc { color:var(--muted); font-size:13px; min-height:38px; }
  .tile input[type=text] { background:var(--soft); color:var(--ink); border:1px solid var(--line);
    border-radius:10px; padding:10px 12px; font:14px/1.4 inherit; width:100%; }
  .tile button { background:var(--primary); color:#fff; border:none; border-radius:10px;
    padding:11px 14px; font-weight:700; cursor:pointer; font-size:14px; }
  .tile button:hover { background:var(--primary-700); }
  .tile button:disabled { background:var(--muted); cursor:not-allowed; }
  .tile .icon { width:36px; height:36px; border-radius:10px; background:var(--soft);
    color:var(--primary); display:grid; place-items:center; font-weight:800; }
  .tile .quick { display:flex; flex-wrap:wrap; gap:6px; }
  .tile .quick a { color:var(--primary); background:var(--soft); border-radius:999px;
    padding:4px 10px; text-decoration:none; font-size:12px; font-weight:600; cursor:pointer;
    border:1px solid transparent; }
  .tile .quick a:hover { border-color:var(--primary); }

  .result-card { background:var(--card); border:1px solid var(--line); border-radius:14px;
    padding:22px; margin-bottom:18px; }
  .result-card .head { display:flex; align-items:center; justify-content:space-between;
    gap:10px; margin-bottom:14px; }
  .result-card .head h2 { font-size:18px; margin:0; }
  .pill { display:inline-block; padding:3px 10px; border-radius:999px; font-size:12px; font-weight:700; }
  .pill.good { background: rgba(16,185,129,.12); color: var(--good); }
  .pill.bad  { background: rgba(239,68,68,.12);  color: var(--bad); }
  .pill.warn { background: rgba(245,158,11,.12); color: var(--warn); }
  .pill.muted{ background: var(--soft); color: var(--muted); }
  .pill.high { background: rgba(239,68,68,.12); color: var(--bad); }
  .pill.medium { background: rgba(245,158,11,.12); color: var(--warn); }
  .pill.low { background: rgba(11,105,255,.12); color: var(--primary); }

  .meta { color: var(--muted); font-size:12px; }

  /* Envelope progress bars */
  .env-grid { display:flex; flex-direction:column; gap:12px; margin-top:8px; }
  .env-row { background:var(--soft); border-radius:10px; padding:12px 14px; }
  .env-row .top { display:flex; justify-content:space-between; align-items:center; margin-bottom:6px; }
  .env-row .cat { font-weight:700; text-transform:capitalize; }
  .env-row .nums { font-size:13px; color:var(--muted); }
  .bar { height:10px; background:#e2e8f0; border-radius:6px; overflow:hidden; position:relative; }
  .bar > div { height:100%; background:var(--primary); border-radius:6px; transition:width .4s ease; }
  .bar.warn > div { background:var(--warn); }
  .bar.bad  > div { background:var(--bad); }
  .bar.bad  { background: rgba(239,68,68,.15); }

  /* Recommendation cards */
  .rec-list { display:flex; flex-direction:column; gap:10px; margin-top:8px; }
  .rec { background:var(--soft); border-left:3px solid var(--primary); border-radius:10px;
    padding:12px 14px; display:flex; gap:12px; align-items:flex-start; }
  .rec.escal { border-left-color: var(--warn); }
  .rec.risk  { border-left-color: var(--bad); }
  .rec.good  { border-left-color: var(--good); }
  .rec .icon { font-weight:800; color: var(--primary); }
  .rec.escal .icon { color: var(--warn); }
  .rec.risk  .icon { color: var(--bad); }
  .rec.good  .icon { color: var(--good); }
  .rec .body .title { font-weight:700; margin-bottom:2px; }
  .rec .body .sub { color: var(--muted); font-size:13px; }

  /* Notifications panel */
  .notif-list { display:flex; flex-direction:column; gap:10px; margin-top:8px; }
  .notif { background:#fff; border:1px solid var(--line); border-radius:10px;
    padding:12px 14px; box-shadow: 0 1px 0 rgba(15,23,42,.04); display:flex; gap:12px; }
  .notif .dot { width:10px; height:10px; border-radius:50%; margin-top:6px; flex-shrink:0; }
  .notif .dot.high { background: var(--bad); }
  .notif .dot.medium { background: var(--warn); }
  .notif .dot.low { background: var(--primary); }
  .notif .body .msg { font-weight:600; }
  .notif .body .meta { margin-top:2px; }

  /* Markdown report */
  .report { background:#fbfdff; border:1px solid var(--line); border-radius:12px;
    padding:20px 22px; margin-top:14px; line-height:1.65; white-space:pre-wrap; }
  .report .claim-ref { color:var(--primary); font-family:ui-monospace,monospace; font-size:12px; }

  /* Stop verdict */
  .stop { margin-top:12px; padding:10px 14px; border-radius:10px;
    background:var(--soft); border-left:3px solid var(--good); }
  .stop.bad { border-left-color: var(--bad); }
  .stop .title { font-weight:700; }
  .stop .why { color:var(--muted); font-size:13px; margin-top:4px; }

  /* Claims grid */
  .claims-grid { display:grid; grid-template-columns:2fr 1.5fr 1fr 0.8fr;
    gap:6px 14px; font-size:13px; margin-top:8px; }
  .claims-grid .h { color:var(--muted); font-size:11px; text-transform:uppercase; }
  .claims-grid .id { font-family:ui-monospace,monospace; color:var(--primary); }
  .claims-grid .v { text-align:right; font-weight:700; }

  /* Buttons inside result */
  .btn-row { display:flex; gap:8px; align-items:center; margin-top:14px; flex-wrap:wrap; }
  .btn-secondary { background:var(--soft); color:var(--ink); border:1px solid var(--line);
    border-radius:10px; padding:8px 12px; font-weight:600; cursor:pointer; font-size:13px; }
  .btn-secondary:hover { border-color:var(--primary); color:var(--primary); }

  /* JSON panel */
  pre { white-space:pre-wrap; word-break:break-word;
    font:12px/1.5 ui-monospace,Menlo,monospace;
    background:#0f172a; color:#e2e8f0; padding:12px; border-radius:8px;
    overflow:auto; max-height:380px; margin-top:8px; }

  /* Loader */
  .loader { display:none; align-items:center; gap:10px; color:var(--muted); margin-top:18px; }
  .loader.show { display:flex; }
  .spinner { width:16px; height:16px; border:2px solid var(--line); border-top-color:var(--primary);
    border-radius:50%; animation: spin .9s linear infinite; }
  @keyframes spin { to { transform: rotate(360deg); } }

  /* Account-snapshot overrides */
  .overrides-grid { display:grid; grid-template-columns: repeat(3, 1fr); gap:14px 18px; }
  .ovr { display:flex; flex-direction:column; gap:4px; }
  .ovr label { font-size:12px; color:var(--muted); font-weight:600; }
  .ovr-input { display:flex; align-items:stretch; border:1px solid var(--line);
    border-radius:10px; overflow:hidden; background:var(--soft); }
  .ovr-input .prefix { padding:8px 10px; color:var(--muted); background:var(--soft); font-weight:700; }
  .ovr-input input { flex:1; border:none; outline:none; background:#fff;
    padding:8px 10px; font:14px/1.4 inherit; color:var(--ink); }
  .ovr-input input:focus { box-shadow: inset 0 0 0 2px var(--primary); }
  .ovr .meta { font-size:11px; }
  @media (max-width:760px) {
    .overrides-grid { grid-template-columns: 1fr 1fr; }
  }

  .footer { color:var(--muted); font-size:12px; text-align:center; margin-top:32px;
    padding:14px; border-top:1px solid var(--line); }

  @media (max-width:760px) {
    .hero { grid-template-columns:1fr; }
    .actions { grid-template-columns:1fr; }
    .claims-grid { grid-template-columns: 1fr 1fr; }
  }
</style>
</head>
<body>
  <div class="wrap">

    <div class="topbar">
      <div class="brand">
        <div class="logo">CC</div>
        <div>
          <div class="name">Cash Compass</div>
          <div class="bank">{{ persona.bank }} · powered by Claude</div>
        </div>
      </div>
      <div class="demo-tag">Demo · synthetic data · no real money moves</div>
    </div>

    <div class="hero">
      <div>
        <div class="hello">Bentornato,</div>
        <h1>{{ persona.name }}</h1>
        <div class="stats">
          <div class="stat"><div class="k">Income this month</div><div class="v">€{{ "%.0f"|format(persona.monthly_income_eur) }}</div></div>
          <div class="stat"><div class="k">Spent</div><div class="v">€{{ "%.0f"|format(persona.month_spent_eur) }}</div></div>
          <div class="stat"><div class="k">Saved</div><div class="v">€{{ "%.0f"|format(persona.month_saved_eur) }}</div></div>
        </div>
      </div>
      <div class="balance">
        <div class="k">Balance</div>
        <div class="v">€{{ "%.2f"|format(persona.balance_eur) }}</div>
      </div>
    </div>

    <div class="section-title" style="display:flex; justify-content:space-between; align-items:center;">
      <span>Account snapshot · edit and re-run to see the agent's output change</span>
      <button id="reset-btn" class="btn-secondary" type="button">Reset to defaults</button>
    </div>
    <div class="card" style="background:var(--card); border:1px solid var(--line); border-radius:14px; padding:18px; margin-bottom:22px;">
      <div class="overrides-grid">
        <div class="ovr">
          <label>Monthly income</label>
          <div class="ovr-input"><span class="prefix">€</span><input type="number" id="ovr-income" step="50" /></div>
          <div class="meta">drives the affordability path</div>
        </div>
        <div class="ovr">
          <label>Dining envelope · limit</label>
          <div class="ovr-input"><span class="prefix">€</span><input type="number" id="ovr-dining-limit" step="10" /></div>
          <div class="meta">monthly cap</div>
        </div>
        <div class="ovr">
          <label>Dining envelope · spent</label>
          <div class="ovr-input"><span class="prefix">€</span><input type="number" id="ovr-dining-spent" step="5" /></div>
          <div class="meta">try setting > limit to trigger escalation</div>
        </div>
        <div class="ovr">
          <label>Groceries envelope · limit</label>
          <div class="ovr-input"><span class="prefix">€</span><input type="number" id="ovr-groc-limit" step="10" /></div>
          <div class="meta">monthly cap</div>
        </div>
        <div class="ovr">
          <label>Groceries envelope · spent</label>
          <div class="ovr-input"><span class="prefix">€</span><input type="number" id="ovr-groc-spent" step="5" /></div>
          <div class="meta">try near-limit to trigger an alert</div>
        </div>
        <div class="ovr">
          <label>Netflix · unused days</label>
          <div class="ovr-input"><input type="number" id="ovr-netflix-days" step="1" /><span class="prefix">d</span></div>
          <div class="meta">≥45d recommends flagging</div>
        </div>
      </div>
    </div>

    <div class="section-title">What can I help with?</div>
    <div class="actions">

      <div class="tile">
        <div class="icon">€</div>
        <h3>Review my budget</h3>
        <div class="desc">Check this month's envelopes; suggest adjustments where you keep going over.</div>
        <button id="btn-budget" data-preset="dining_over">Run budget review</button>
        <div class="quick">
          <a id="link-savings" data-preset="savings_goal">Spot a savings opportunity</a>
        </div>
      </div>

      <div class="tile">
        <div class="icon">!</div>
        <h3>Subscriptions to review</h3>
        <div class="desc">Question-Surfacer scans for unused or near-limit alerts and queues a notification.</div>
        <button id="btn-questions" data-preset="dining_near_limit">Scan now</button>
      </div>

      <div class="tile">
        <div class="icon">?</div>
        <h3>Can I afford it?</h3>
        <div class="desc">Ask a financial goal. The advisor cites every number with a Claim ID.</div>
        <input type="text" id="goal-input" placeholder="Posso permettermi una casa da 350.000 EUR a 25 anni?" />
        <button id="btn-affordability">Ask</button>
        <div class="quick">
          <a id="link-aff-200" data-goal="Posso permettermi una casa da 200.000 EUR con mutuo a 30 anni?">€200k house, 30y</a>
          <a id="link-aff-inv" data-goal="Dovrei comprare azioni Apple per investire i miei risparmi?">Investment advice (refusal)</a>
        </div>
      </div>
    </div>

    <div id="loader" class="loader">
      <div class="spinner"></div>
      <div id="loader-text">Running the agent on Bedrock…</div>
    </div>

    <div id="result-container"></div>

    <div class="footer">
      Cash Compass · two surfaces, one Coordinator · all numbers in the affordability path are Claim-backed and verified by the Stop hook before reaching you.
    </div>
  </div>

<script>
const RC = document.getElementById('result-container');
const LOADER = document.getElementById('loader');
const LOADER_T = document.getElementById('loader-text');

function el(tag, attrs, children) {
  const e = document.createElement(tag);
  if (attrs) for (const k in attrs) {
    if (k === 'class') e.className = attrs[k];
    else if (k === 'html') e.innerHTML = attrs[k];
    else if (k === 'style') e.setAttribute('style', attrs[k]);
    else e.setAttribute(k, attrs[k]);
  }
  if (children) for (const c of [].concat(children)) {
    if (c == null) continue;
    if (typeof c === 'string') e.appendChild(document.createTextNode(c));
    else e.appendChild(c);
  }
  return e;
}
function fmt(n, digits) {
  if (typeof n !== 'number') return n;
  return n.toLocaleString('it-IT', { minimumFractionDigits: digits, maximumFractionDigits: digits });
}
function escapeHtml(s) {
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

// Defaults must mirror Python's DEFAULTS dict
const DEFAULT_OVERRIDES = {
  monthly_income_eur: 2200,
  dining_limit_eur: 80,
  dining_spent_eur: 145,
  groceries_limit_eur: 350,
  groceries_spent_eur: 318.40,
  netflix_unused_days: 73,
};
const OVR_INPUTS = {
  monthly_income_eur:   'ovr-income',
  dining_limit_eur:     'ovr-dining-limit',
  dining_spent_eur:     'ovr-dining-spent',
  groceries_limit_eur:  'ovr-groc-limit',
  groceries_spent_eur:  'ovr-groc-spent',
  netflix_unused_days:  'ovr-netflix-days',
};

function applyDefaults() {
  for (const k in OVR_INPUTS) {
    const inp = document.getElementById(OVR_INPUTS[k]);
    if (inp) inp.value = DEFAULT_OVERRIDES[k];
  }
}
function collectOverrides() {
  const o = {};
  for (const k in OVR_INPUTS) {
    const inp = document.getElementById(OVR_INPUTS[k]);
    if (inp && inp.value !== '') {
      const v = parseFloat(inp.value);
      if (!isNaN(v)) o[k] = v;
    }
  }
  return o;
}

async function callApi(path, body) {
  RC.innerHTML = '';
  LOADER.classList.add('show');
  LOADER_T.textContent = 'Running the agent on Bedrock…';
  document.querySelectorAll('button').forEach(b => b.disabled = true);
  const fullBody = Object.assign({ overrides: collectOverrides() }, body || {});
  try {
    const res = await fetch(path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(fullBody),
    });
    const data = await res.json();
    renderResult(data);
  } catch (e) {
    RC.appendChild(renderError(String(e)));
  } finally {
    LOADER.classList.remove('show');
    document.querySelectorAll('button').forEach(b => b.disabled = false);
  }
}

function renderError(msg) {
  return el('div', { class: 'result-card' }, [
    el('div', { class: 'head' }, [el('h2', null, 'Error'), el('span', { class: 'pill bad' }, msg)]),
    el('div', { class: 'meta' }, 'See the browser console for details.'),
  ]);
}

function renderHead(data) {
  let pillCls = 'good', pillTxt = 'ok';
  if (data.error) { pillCls = 'bad'; pillTxt = data.error; }
  else if (data.escalation_required) { pillCls = 'warn'; pillTxt = 'escalated to user'; }
  return el('div', { class: 'head' }, [
    el('h2', null, data.title || data.surface),
    el('div', null, [
      el('span', { class: 'pill ' + pillCls }, pillTxt),
      el('span', { class: 'pill muted', style: 'margin-left:6px;' }, data.elapsed_s + 's'),
    ]),
  ]);
}

// ---------- Budget renderer ----------
function renderBudget(data) {
  const card = el('div', { class: 'result-card' });
  card.appendChild(renderHead(data));

  // Envelopes as progress bars
  if (data.envelopes && data.envelopes.length) {
    card.appendChild(el('div', { class: 'section-title' }, 'Your envelopes'));
    const grid = el('div', { class: 'env-grid' });
    for (const e of data.envelopes) {
      const pct = e.monthly_limit > 0 ? Math.min(150, (e.spent / e.monthly_limit) * 100) : 0;
      const cls = pct > 100 ? 'bad' : (pct > 85 ? 'warn' : '');
      const remainTxt = e.remaining < 0
        ? `€${fmt(Math.abs(e.remaining), 2)} over`
        : `€${fmt(e.remaining, 2)} left`;
      grid.appendChild(el('div', { class: 'env-row' }, [
        el('div', { class: 'top' }, [
          el('div', { class: 'cat' }, e.category),
          el('div', { class: 'nums' },
            `€${fmt(e.spent, 2)} of €${fmt(e.monthly_limit, 2)} · ${remainTxt}`),
        ]),
        el('div', { class: 'bar ' + cls }, [
          el('div', { style: 'width:' + Math.min(100, pct) + '%;' }),
        ]),
      ]));
    }
    card.appendChild(grid);
  }

  // Forecaster's structured recommendations
  const fc = (data.specialist_results.forecaster || {}).result || {};
  const recs = el('div', { class: 'rec-list' });
  let recCount = 0;
  if (Array.isArray(fc.envelopes_updated)) for (const u of fc.envelopes_updated) {
    recs.appendChild(rec('good', 'Envelope adjusted',
      `${u.category || ''} → new limit €${u.new_limit || u.limit || '?'}. ${u.reason || ''}`));
    recCount++;
  }
  if (Array.isArray(fc.goals_created)) for (const g of fc.goals_created) {
    recs.appendChild(rec('good', 'Savings goal created',
      `${g.name || g.id || 'goal'} · target €${g.target_amount || '?'} · €${g.monthly_contribution || '?'}/month`));
    recCount++;
  }
  if (Array.isArray(fc.escalations_required)) for (const esc of fc.escalations_required) {
    const reason = typeof esc === 'string' ? esc : (esc.reason || JSON.stringify(esc));
    recs.appendChild(rec('escal', 'Escalation required',
      reason + ' — needs your confirmation before applying.'));
    recCount++;
  }
  if (fc.forecast_summary && typeof fc.forecast_summary === 'object') {
    const fs = fc.forecast_summary;
    const summary = Object.entries(fs).map(([k, v]) =>
      `${k}: ${typeof v === 'object' ? JSON.stringify(v) : v}`).join(' · ');
    if (summary) {
      recs.appendChild(rec('good', 'Forecast summary', summary));
      recCount++;
    }
  }
  if (recCount === 0 && data.mutations && data.mutations.length === 0) {
    recs.appendChild(rec('good', 'Nothing to action right now',
      "The agent looked at your envelopes and did not find anything that needs a change or your attention."));
  }
  if (recs.children.length) {
    card.appendChild(el('div', { class: 'section-title' }, 'Recommendations'));
    card.appendChild(recs);
  }

  // Mutations
  if (data.mutations && data.mutations.length) {
    card.appendChild(el('div', { class: 'section-title' }, 'What Cash Compass did on your account'));
    card.appendChild(renderMutations(data.mutations));
  }

  card.appendChild(buttonRow(data));
  return card;
}

// ---------- Question-Surfacer renderer ----------
function renderQuestions(data) {
  const card = el('div', { class: 'result-card' });
  card.appendChild(renderHead(data));

  const qs = (data.specialist_results.question_surfacer || {}).result || {};
  const list = el('div', { class: 'notif-list' });

  // Notifications enqueued during the run (from bank_store)
  const notifs = data.notifications || [];
  for (const n of notifs) {
    list.appendChild(notif(n.priority || 'low', n.message || '', n.category || ''));
  }
  // Anything the specialist returned in `questions` but not yet enqueued
  if (Array.isArray(qs.questions)) {
    for (const q of qs.questions) {
      if (notifs.some(n => n.message === q.message)) continue;
      list.appendChild(notif(q.priority || 'low', q.message, q.category || ''));
    }
  }
  if (!list.children.length) {
    list.appendChild(rec('good', 'Nothing needs your attention',
      'No new notifications were queued in this scan.'));
  }
  card.appendChild(el('div', { class: 'section-title' }, 'Questions for you'));
  card.appendChild(list);

  if (data.envelopes && data.envelopes.length) {
    card.appendChild(el('div', { class: 'section-title' }, 'Your envelopes (snapshot)'));
    const g = el('div', { class: 'env-grid' });
    for (const e of data.envelopes) {
      const pct = e.monthly_limit > 0 ? Math.min(150, (e.spent / e.monthly_limit) * 100) : 0;
      const cls = pct > 100 ? 'bad' : (pct > 85 ? 'warn' : '');
      g.appendChild(el('div', { class: 'env-row' }, [
        el('div', { class: 'top' }, [
          el('div', { class: 'cat' }, e.category),
          el('div', { class: 'nums' },
            `€${fmt(e.spent, 2)} of €${fmt(e.monthly_limit, 2)}`),
        ]),
        el('div', { class: 'bar ' + cls }, [el('div', { style: 'width:' + Math.min(100, pct) + '%;' })]),
      ]));
    }
    card.appendChild(g);
  }

  card.appendChild(buttonRow(data));
  return card;
}

// ---------- Affordability renderer ----------
function renderAffordability(data) {
  const card = el('div', { class: 'result-card' });
  card.appendChild(renderHead(data));

  if (data.advisor_report_md) {
    const html = escapeHtml(data.advisor_report_md)
      .replace(/\[(claim_[a-z0-9_]+)\]/g, '<span class="claim-ref">[$1]</span>');
    card.appendChild(el('div', { class: 'section-title' }, 'Advisor report'));
    const rep = el('div', { class: 'report', html });
    card.appendChild(rep);
  } else if (data.error || data.escalation_required) {
    card.appendChild(rec('escal', 'No report produced',
      data.error || 'The Stop hook rejected the report. See the technical detail.'));
  }

  // Stop verdicts
  if (data.stop_verdicts && data.stop_verdicts.length) {
    for (const v of data.stop_verdicts) {
      const stop = el('div', { class: 'stop' + (v.ok ? '' : ' bad') });
      stop.appendChild(el('div', { class: 'title' },
        v.ok ? `Stop hook: passed (${v.n_claims_cited || 0} claims cited)`
             : `Stop hook: rejected — ${v.reason || ''}`));
      if (v.guidance) stop.appendChild(el('div', { class: 'why' }, v.guidance));
      card.appendChild(stop);
    }
  }

  // Claims table
  if (data.claims && data.claims.length) {
    card.appendChild(el('div', { class: 'section-title' }, `Claims emitted (${data.claims.length})`));
    const grid = el('div', { class: 'claims-grid' }, [
      el('div', { class: 'h' }, 'id'),
      el('div', { class: 'h' }, 'label'),
      el('div', { class: 'h' }, 'value'),
      el('div', { class: 'h' }, 'conf'),
    ]);
    for (const c of data.claims) {
      grid.appendChild(el('div', { class: 'id' }, c.id));
      grid.appendChild(el('div', null, c.label || ''));
      grid.appendChild(el('div', { class: 'v' }, `${c.value} ${c.unit || ''}`));
      grid.appendChild(el('div', { class: 'meta' }, String(c.confidence ?? '')));
    }
    card.appendChild(grid);
  }

  card.appendChild(buttonRow(data));
  return card;
}

function rec(kind, title, sub) {
  return el('div', { class: 'rec ' + kind }, [
    el('div', { class: 'icon' }, kind === 'escal' ? '!' : (kind === 'risk' ? '×' : '✓')),
    el('div', { class: 'body' }, [
      el('div', { class: 'title' }, title),
      el('div', { class: 'sub' }, sub),
    ]),
  ]);
}

function notif(priority, msg, category) {
  const dotCls = priority === 'high' ? 'high' : (priority === 'medium' ? 'medium' : 'low');
  return el('div', { class: 'notif' }, [
    el('div', { class: 'dot ' + dotCls }),
    el('div', { class: 'body' }, [
      el('div', { class: 'msg' }, msg),
      el('div', { class: 'meta' }, [
        el('span', { class: 'pill ' + dotCls }, priority || 'low'),
        el('span', { style: 'margin-left:8px;' }, category || ''),
      ]),
    ]),
  ]);
}

function renderMutations(mutations) {
  const list = el('div', { class: 'rec-list' });
  for (const m of mutations) {
    const v = (typeof m.value === 'object' && m.value)
      ? Object.entries(m.value).slice(0, 4).map(([k, vv]) => `${k}=${vv}`).join(', ')
      : (m.value || '');
    list.appendChild(rec('good',
      `${m.action} → ${m.entity} · ${m.key}`,
      v));
  }
  return list;
}

function buttonRow(data) {
  const row = el('div', { class: 'btn-row' });
  const btn = el('button', { class: 'btn-secondary' }, 'Show raw JSON');
  const json = el('pre', { style: 'display:none;' }, JSON.stringify(data, null, 2));
  btn.onclick = () => {
    if (json.style.display === 'none') { json.style.display = 'block'; btn.textContent = 'Hide raw JSON'; }
    else { json.style.display = 'none'; btn.textContent = 'Show raw JSON'; }
  };
  row.appendChild(btn);
  row.appendChild(el('span', { class: 'meta' }, `validation_retries: ${data.validation_retries}`));
  const wrap = el('div', null, [row, json]);
  return wrap;
}

function renderResult(data) {
  RC.innerHTML = '';
  let card;
  if (data.surface === 'budget') card = renderBudget(data);
  else if (data.surface === 'questions') card = renderQuestions(data);
  else if (data.surface === 'affordability') card = renderAffordability(data);
  else card = el('div', { class: 'result-card' }, [renderHead(data), buttonRow(data)]);
  RC.appendChild(card);
  card.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

// --- Wire up controls ---
document.getElementById('btn-budget').addEventListener('click', e => {
  callApi('/api/budget', { preset: e.currentTarget.dataset.preset });
});
document.getElementById('link-savings').addEventListener('click', e => {
  e.preventDefault();
  callApi('/api/budget', { preset: e.currentTarget.dataset.preset });
});
document.getElementById('btn-questions').addEventListener('click', e => {
  callApi('/api/questions', { preset: e.currentTarget.dataset.preset });
});
document.getElementById('btn-affordability').addEventListener('click', () => {
  const goal = document.getElementById('goal-input').value;
  callApi('/api/affordability', { goal });
});
document.getElementById('goal-input').addEventListener('keydown', e => {
  if (e.key === 'Enter') document.getElementById('btn-affordability').click();
});
document.getElementById('link-aff-200').addEventListener('click', e => {
  e.preventDefault();
  callApi('/api/affordability', { goal: e.currentTarget.dataset.goal });
});
document.getElementById('link-aff-inv').addEventListener('click', e => {
  e.preventDefault();
  callApi('/api/affordability', { goal: e.currentTarget.dataset.goal });
});
document.getElementById('reset-btn').addEventListener('click', () => applyDefaults());

// Initialize the override inputs with defaults on first load.
applyDefaults();
</script>
</body>
</html>
"""


if __name__ == "__main__":
    print("Cash Compass demo: http://localhost:5000")
    print("(Demos call AWS Bedrock; make sure 'aws login --profile bootcamp' is current.)")
    app.run(host="127.0.0.1", port=5000, debug=False)
