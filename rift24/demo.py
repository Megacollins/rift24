"""Pre-render data/demo_state.json (and app/demo_state.js) from the Python engine. The static UI reads only this.

Anti-leak structure: everything knowable at decision time sits under `pre`; everything that only becomes known at
or after the cash open sits under `reveal`. The UI must not render `reveal` before the user reaches that step.
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from . import calendar as cal
from .backtest import BacktestResult, Window, _strategy_slice
from .data import BAR_MS, DataAdapter
from .events import load_events
from .residual import Config, autopsy

ROOT = Path(__file__).resolve().parents[1]

MAIN = ("A_DO_NOTHING", "B_ALWAYS_FADE", "RIFT24")


def _iso(ms: int) -> str:
    return dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc).isoformat().replace("+00:00", "Z")


def _slim(res: BacktestResult, s: str, name: str, keep_trades: bool = False) -> dict:
    m = res.metrics[s]
    out = {
        "name": name,
        "FULL": m["FULL"], "IS": m["IS"], "OOS": m["OOS"], "OOS_IS": m["OOS_IS"],
        "daily": [[d.isoformat(), round(r, 7)] for d, r in res.daily[s]],
        "rolling": [[x["date"], x["sharpe"]] for x in m["rolling_30d_sharpe_series"]],
    }
    if keep_trades:
        out["trades"] = [
            {"date": t.open_date.isoformat(), "symbol": t.rtoken, "period": t.period, "stance": t.stance,
             "side": "BUY" if t.direction > 0 else "SELL/SHORT", "void_bps": round(t.void_bps, 1),
             "cash_gap_bps": round(t.cash_bps, 1), "net_bps": round(t.net_bps, 1)}
            for t in res.trades[s]
        ]
    return out


def _gate_dicts(dec):
    return [{"gate": g.gate, "name": g.name, "passed": g.passed, "detail": g.detail} for g in dec.gates]


def _row(r, cfg: Config, events_by_rtoken: dict) -> dict:
    v, o = r.obs.view, r.obs.outcome
    dec, pre = r.decisions["RIFT24_RESIDUAL"], r.decisions["RIFT24"]
    m = r.model
    aut = autopsy(v, o, dec, cfg)
    return {
        "pre": {
            "rtoken": v.rtoken, "symbol": v.symbol, "underlying": v.underlying,
            "void_bps": round(v.void_bps, 1),
            "implied_cash_bps": None if dec.implied_cash_bps is None else round(dec.implied_cash_bps, 1),
            "residual_bps": None if dec.residual_bps is None else round(dec.residual_bps, 1),
            "cost_bps": round(dec.cost_bps, 1),
            "spread_bps": v.spread_bps, "spread_source": v.spread_source,
            "expected_edge_bps": None if dec.expected_edge_bps is None else round(dec.expected_edge_bps, 1),
            "net_residual_bps": None if dec.net_residual_bps is None else round(dec.net_residual_bps, 1),
            "stance": dec.stance, "side": dec.side, "binding_gate": dec.binding_gate,
            "gates": _gate_dicts(dec), "explanation": dec.explanation,
            "prespecified_fade": {"stance": pre.stance, "binding_gate": pre.binding_gate},
            "model": None if m is None else {"beta": round(m.beta, 3), "se": round(m.se, 3), "n": m.n, "t_overshoot": round(m.t_overshoot, 2)},
            "data_status": {
                "label": "FIXTURE SNAPSHOT - OBSERVED prices", "staleness_min": v.staleness_min,
                "base_px": round(v.base_px, 4), "decision_px": round(v.dec_px, 4),
            },
            "events": events_by_rtoken.get(v.rtoken, []),
        },
        "reveal": {
            "realized_cash_gap_bps": aut["realized_cash_gap_bps"],
            "priced_in_ratio": aut["priced_in_ratio"],
            "realized_residual_bps": aut["realized_residual_bps"],
            "verdict": aut["verdict"],
            "hold_return_bps": aut["hold_return_bps"],
            "net_bps": aut.get("net_bps"),
            "gross_bps": aut.get("gross_bps"),
            "entry_px": round(o.entry_px, 4), "exit_px": round(o.exit_px, 4),
            "cash_prev_close": round(o.cash_prev_close, 4), "cash_open": round(o.cash_open, 4),
            "exit_stance": "FLATTEN" if dec.traded else "DO NOTHING",
        },
    }


def build_demo_state(adapter: DataAdapter, cfg: Config, window: Window, res: BacktestResult, sens: list[dict],
                     study: dict, notice: str | None) -> dict:
    ins = adapter.instruments()
    st = adapter.status()
    evs = load_events(ins)
    events_by: dict[str, list[str]] = {}
    for e in evs:
        for t in e.mapping.tickers:
            if e.mapping.relevance[t] == "direct":  # macro headlines are shown on the board, not pinned to index rows
                events_by.setdefault(t, []).append(e.id)

    sel_lead = study["selected_lead_bars"]
    featured = max(r.obs.view.open_date for r in res.rows if r.period and r.obs.view.kind == "weekend")
    sess = cal.make_session(featured)
    open_ms, close_ms = int(sess.open_utc.timestamp() * 1000), int(sess.close_utc.timestamp() * 1000)

    # ---- desk: one snapshot per decision time
    times = []
    for lead, r_ in sorted(study["results_by_lead"].items(), key=lambda kv: -kv[0]):
        rows = [x for x in r_.rows if x.obs.view.open_date == featured]
        if not rows:
            continue
        dec_utc = rows[0].obs.view.decision_utc
        status = cal.status_at(dec_utc)
        times.append({
            "lead_bars": lead, "label_et": [g for g in study["grid"] if g["decision_lead_bars"] == lead][0]["decision_time"],
            "decision_utc": dec_utc.isoformat().replace("+00:00", "Z"), "session_status": status.value,
            "hours_before_open": lead * 0.25,
            "model": None if rows[0].model is None else {"beta": round(rows[0].model.beta, 3), "n": rows[0].model.n, "se": round(rows[0].model.se, 3)},
            "rows": sorted((_row(x, cfg, events_by) for x in rows), key=lambda z: -abs(z["pre"]["void_bps"])),
        })

    # ---- replay paths (15m closes as bps vs the prior-close price)
    sel_rows = {x.obs.view.symbol: x for x in study["result"].rows if x.obs.view.open_date == featured}
    dec_ms = int(next(iter(sel_rows.values())).obs.view.decision_utc.timestamp() * 1000)
    end_ms = open_ms  # every trade closes at the last pre-open bar; nothing after the open is plotted
    pre_paths, post_paths = {}, {}
    for i in ins:
        base = sel_rows[i.bitget_symbol].obs.view.base_px
        pre, post = [], []
        t = close_ms
        while t < end_ms:
            b = adapter.rtoken_bar(i.bitget_symbol, t)
            if b is not None:
                pt = [b.end_ms, round((b.close / base - 1) * 1e4, 1)]
                (pre if b.end_ms <= dec_ms else post).append(pt)
            t += BAR_MS
        pre_paths[i.rtoken], post_paths[i.rtoken] = pre, post

    # ---- every weekend in the window (the featured one is chosen by rule, so show all of them)
    weekends = []
    resid = study["result"]
    by_day = {d: r for d, r in resid.daily["RIFT24_RESIDUAL"]}
    for d in sorted({x.obs.view.open_date for x in resid.rows if x.period and x.obs.view.kind == "weekend"}):
        rs = [x for x in resid.rows if x.obs.view.open_date == d]
        big = max(rs, key=lambda x: abs(x.obs.view.void_bps))
        ntr = sum(1 for x in rs if x.decisions["RIFT24_RESIDUAL"].traded)
        weekends.append({
            "open_date": d.isoformat(), "period": rs[0].period, "void_hours": round(rs[0].obs.view.void_hours, 1),
            "rift24_trades": ntr, "rift24_day_return_pct": round(by_day[d] * 100, 3),
            "largest_void": {"rtoken": big.obs.view.rtoken, "bps": round(big.obs.view.void_bps, 0)},
        })

    m_main = {s: _slim(res, s, n) for s, n in (("A_DO_NOTHING", "A  DO NOTHING"), ("B_ALWAYS_FADE", "B  ALWAYS FADE"), ("RIFT24", "RIFT24 (pre-specified fade)"))}
    m_expl = {
        "B_ALWAYS_FADE": _slim(resid, "B_ALWAYS_FADE", "B  ALWAYS FADE (same time)"),
        "C_ALWAYS_FOLLOW": _slim(resid, "C_ALWAYS_FOLLOW", "C  ALWAYS FOLLOW (naive mirror)"),
        "RIFT24_RESIDUAL": _slim(resid, "RIFT24_RESIDUAL", "RIFT24 residual (exploratory)", keep_trades=True),
    }
    d = res.diagnostics
    return {
        "meta": {
            "project": "Rift24", "tagline": "Rift24 finds what the 24/7 rToken market hasn't priced yet.",
            "track": "Alpha Factory - Quantitative Strategies", "sub_theme": "After-Hours Information Pricing",
            "paper_only": "Paper/backtest only. No live-money trading.",
            "data": {"mode": st.mode, "label": st.label, "provenance": st.provenance, "fetched_at_utc": st.fetched_at_utc,
                     "last_bar_end_utc": st.last_bar_end_utc, "notice": notice},
            "legend": {"OBSERVED": "read from exchange / cash-market data", "ESTIMATED": "depends on a modelled assumption",
                       "TARGETED": "goal not yet built or measured"},
            "featured_session": featured.isoformat(), "featured_rule": "the most recent weekend in the data, chosen by date - not by performance",
            "selected_decision_lead_bars": sel_lead,
        },
        "calendar": {"holidays": {k.isoformat(): v for k, v in cal.HOLIDAYS.items()},
                     "early_closes": {k.isoformat(): v for k, v in cal.EARLY_CLOSES.items()}},
        "allowlist": [{"rtoken": i.rtoken, "symbol": i.bitget_symbol, "underlying": i.underlying, "kind": i.kind} for i in ins],
        "events": [{
            "id": e.id, "headline": e.headline, "source": e.source, "url": e.url, "provenance": e.provenance,
            "summary": e.summary, "published_note": e.published_note,
            "mapping": {"tickers": list(e.mapping.tickers), "relevance": e.mapping.relevance, "method": e.mapping.method,
                        "matched_terms": {k: list(v) for k, v in e.mapping.matched_terms.items()},
                        "explanation": e.mapping.explanation, "note": e.mapping.note},
        } for e in evs],
        "desk": {
            "session": {"open_date": featured.isoformat(), "kind": sess.kind, "void_hours": sess.void_hours,
                        "prev_close_utc": sess.close_utc.isoformat().replace("+00:00", "Z"),
                        "open_utc": sess.open_utc.isoformat().replace("+00:00", "Z")},
            "times": times,
        },
        "replay": {
            "session": featured.isoformat(), "decision_utc": _iso(dec_ms), "prev_close_utc": _iso(close_ms), "open_utc": _iso(open_ms),
            "pre": {"paths": pre_paths},
            "reveal": {"paths": post_paths},
            "weekends": weekends,
        },
        "backtest": {
            "window": {"start": window.start.isoformat(), "is_end": window.is_end.isoformat(), "end": window.end.isoformat(),
                       "total_days": window.total_days, "is_days": window.is_days, "oos_days": window.oos_days},
            "config": {"rt_cost_bps": cfg.rt_cost_bps, "spread_fallback_bps": cfg.spread_fallback_bps,
                       "min_move_cost_multiple": cfg.min_move_cost_multiple, "edge_cost_multiple": cfg.edge_cost_multiple,
                       "min_train_obs": cfg.min_train_obs, "min_tstat": cfg.min_tstat, "min_tstat_two_sided": cfg.min_tstat_two_sided,
                       "max_name_pct": cfg.max_name_pct},
            "pre_specified": {"hypothesis": "Fading large rToken void moves (signal = -R_rtoken) earns positive risk-adjusted returns after costs.",
                              "decision_time": "09:15 ET", "strategies": m_main,
                              "verdict": "REJECTED" if (res.metrics["B_ALWAYS_FADE"]["FULL"]["mean_trade_net_bps"] or 0) <= 0 else "NOT REJECTED"},
            "diagnostics": {k: d[k] for k in ("sessions_scored", "symbol_sessions_scored", "symbol_sessions_large_move",
                                              "weekend_sessions_scored", "priced_in_beta_large_moves", "large_move_overshoot_share",
                                              "large_move_overshoot_beyond_cost_share",
                                              "median_priced_in_ratio_large_moves", "convergence_capture", "stand_down",
                                              "always_fade_gross", "excluded_symbol_sessions_in_window", "data_validation") if k in d},
            "sensitivities": sens,
            "exploratory": {
                "label": study["label"], "selection_rule": study["selection_rule"],
                "selected_decision_time": study["selected_decision_time"], "grid": study["grid"],
                "strategies": m_expl, "robustness": study["robustness"],
                "side_split": {k: v for k, v in study["result"].diagnostics["side_split"].items() if k.startswith("RIFT24_RESIDUAL")},
                "stand_down": study["result"].diagnostics["stand_down"],
            },
        },
    }


def write_state(state: dict, root: Path = ROOT) -> list[Path]:
    txt = json.dumps(state, separators=(",", ":"))
    p1, p2 = root / "data" / "demo_state.json", root / "app" / "demo_state.js"
    p1.write_text(txt + "\n", encoding="utf-8")
    p2.write_text("/* generated by scripts/build_demo_state.py - do not edit */\nwindow.RIFT24_STATE = " + txt + ";\n", encoding="utf-8")
    return [p1, p2]
