"""Serialise backtest results (reports/backtest.json), print the metrics table, and render reports/thesis.md.

Label legend used everywhere:
  OBSERVED  read directly from exchange / cash-market data (prices, void moves, cash gaps, realised convergence)
  ESTIMATED depends on a modelled assumption (costs, spread haircut, net P&L, Sharpe of net returns, implied gaps)
  TARGETED  a goal that is not yet built or measured
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import json
from pathlib import Path

from .backtest import STRATEGIES, BacktestResult
from .data import DataAdapter

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "reports"

LEGEND = {
    "OBSERVED": "read directly from exchange / cash-market data",
    "ESTIMATED": "depends on a modelled assumption (costs, spread haircut, implied cash gap, net-of-cost returns)",
    "TARGETED": "goal not yet built or measured",
}
NAMES = {"A_DO_NOTHING": "A DO NOTHING", "B_ALWAYS_FADE": "B ALWAYS FADE", "RIFT24": "RIFT24"}


def _f(x, nd=2, pct=False, none="n/a"):
    if x is None:
        return none
    return f"{x:.{nd}f}{'%' if pct else ''}"


def method_block(res: BacktestResult, adapter: DataAdapter) -> dict:
    cfg, w = res.cfg, res.window
    return {
        "date_range": {"first_session": w.start.isoformat(), "last_session": w.end.isoformat(), "calendar_days": w.total_days},
        "split": {
            "in_sample": {"from": w.start.isoformat(), "to": w.is_end.isoformat(), "calendar_days": w.is_days, "label": "IN-SAMPLE"},
            "out_of_sample": {"from": (w.is_end + dt.timedelta(days=1)).isoformat(), "to": w.end.isoformat(),
                              "calendar_days": w.oos_days, "label": "OUT-OF-SAMPLE"},
            "note": "60/30 chronological split of session open dates. There is nothing to fit in-sample: the only learned "
                    "quantity (priced-in beta) is refit walk-forward on already-known outcomes, so IS and OOS are scored by "
                    "the identical rule. OOS is simply the most recent 30 days.",
        },
        "instruments": [i.rtoken for i in adapter.instruments()],
        "sampling_frequency": "15-minute rToken candles (Bitget spot); daily regular-session open/close for the underlying (Yahoo Finance)",
        "sessions": {
            "construction": "prior regular close (16:00 ET, 13:00 on early-close days) -> next regular open (09:30 ET), holidays "
                            "and DST handled in rift24/calendar.py; verified against the set of days Yahoo has bars for",
            "timeline_et": {"base_price": "close of the 15m bar ending at the prior cash close",
                            "decision": f"{cfg.decision_lead_bars * 15} min before the open (09:15 ET): rToken price = close of the bar ending then",
                            "entry": "open of the 15m bar starting at the decision time (next-bar fill; cash market still closed)",
                            "exit": ("close of the last 15m bar BEFORE the cash open (cash still closed)" if cfg.exit_after_open_bars == 0
                                     else f"close of bar #{cfg.exit_after_open_bars} starting at the cash open (regular session)")},
            "gate_1_note": "Decisions, entries AND exits all happen while cash is closed: the exit is the close of the last pre-open bar. "
                           "No position is opened or closed in the regular session. Post-open exits are reported only as sensitivities.",
            "design_correction": "An earlier draft exited 15 min after the open. Inspecting the weekend replay showed that exit mixed in regular-session "
                                 "price action (e.g. rMETA +348 bps in the first bar after the open) and broke gate 1 in spirit. Exits moved pre-open; "
                                 "results were recomputed; the old exit is kept as a sensitivity row.",
        },
        "costs": {
            "round_trip_taker_haircut_bps": cfg.rt_cost_bps,
            "spread_haircut_bps": cfg.spread_fallback_bps,
            "spread_source": "ESTIMATED fallback (+4 bps). Historical bid/ask is not public; no spread is presented as observed in the backtest.",
            "extra_slippage_bps": cfg.slippage_bps,
            "total_bps_per_round_trip": cfg.rt_cost_bps + cfg.spread_fallback_bps + cfg.slippage_bps,
            "observed_exchange_taker_fee": "OBSERVED: Bitget spot takerFeeRate = 0.001 per side (20 bps round trip) on all 10 symbols - "
                                           "worse than the 8 bps default; see sensitivities",
        },
        "missing_data": {
            "rule": f"15m bars with no trades are absent. Price = last trade carried forward at most {cfg.max_carry_bars} bars "
                    f"(gate 6 rejects > {cfg.stale_minutes:g} min old prices for Rift24). Entry/exit bars must contain a trade or the "
                    "session is excluded for ALL strategies; sessions without a cash daily bar are excluded.",
            "excluded_in_window": res.diagnostics.get("excluded_symbol_sessions_in_window", {}),
        },
        "position_sizing": f"equal weight across the day's trades, capped at {cfg.max_name_pct:.0%} per name, gross <= {cfg.max_gross_pct:.0%} of book (unlevered)",
        "metric_conventions": {
            "sharpe": "mean/std(ddof=1) of per-session book returns x sqrt(252), rf=0; stand-down sessions count as 0",
            "sortino": "mean / sqrt(mean(min(r,0)^2)) x sqrt(252), target 0",
            "max_drawdown": "peak-to-trough of compounded equity",
            "turnover": "sum of entry+exit notional / book",
            "rolling_sharpe": "trailing 30 calendar days, >= 15 sessions",
            "oos_is_alert": "OOS Sharpe < 0.5 x IS Sharpe",
        },
        "config": dataclasses.asdict(cfg),
    }


def _strategy_block(res: BacktestResult, s: str, name: str) -> dict:
    m = res.metrics[s]
    return {
        "name": name,
        "FULL": m["FULL"], "IN_SAMPLE": m["IS"], "OUT_OF_SAMPLE": m["OOS"], "OOS_IS": m["OOS_IS"],
        "rolling_30d_sharpe_series": m["rolling_30d_sharpe_series"],
        "daily_returns": [{"date": d.isoformat(), "ret": round(r, 8)} for d, r in res.daily[s]],
        "trades": [
            {"date": t.open_date.isoformat(), "symbol": t.rtoken, "period": t.period, "stance": t.stance,
             "side": "BUY" if t.direction > 0 else "SELL/SHORT",
             "weight": round(t.weight, 4), "void_bps": round(t.void_bps, 1), "cash_gap_bps": round(t.cash_bps, 1),
             "gross_bps": round(t.gross_bps, 1), "cost_bps": round(t.cost_bps, 1), "net_bps": round(t.net_bps, 1)}
            for t in res.trades[s]
        ],
    }


def exploratory_block(study: dict) -> dict:
    sel = study["result"]
    return {
        "label": study["label"],
        "selection_rule": study["selection_rule"],
        "selected_decision_time_et": study["selected_decision_time"],
        "decision_time_grid": study["grid"],
        "window_of_selected_run": {"decision_lead_bars": study["selected_lead_bars"]},
        "strategies": {
            "B_ALWAYS_FADE": _strategy_block(sel, "B_ALWAYS_FADE", "B ALWAYS FADE (same decision time)"),
            "C_ALWAYS_FOLLOW": _strategy_block(sel, "C_ALWAYS_FOLLOW", "C ALWAYS FOLLOW (naive mirror of B)"),
            "RIFT24_RESIDUAL": _strategy_block(sel, "RIFT24_RESIDUAL", "RIFT24 residual (two-sided, all 8 gates)"),
        },
        "robustness": study["robustness"],
        "side_split": {k: v for k, v in sel.diagnostics["side_split"].items()},
        "diagnostics": {k: sel.diagnostics[k] for k in ("stand_down", "priced_in_beta_large_moves", "convergence_capture") if k in sel.diagnostics},
        "caveats": [
            "Selected after the pre-specified test came back null, on the same sample: a hypothesis for follow-up, not a validated edge.",
            "Only ~13 weekends / 62 sessions; the trades cluster on earnings-type gap days (see robustness.concentration).",
            "Sharpe standard errors are large (sharpe_se); with a few dozen trades the OOS number carries little statistical weight.",
            "The 04:00 ET entry trades a thin rToken book: real fills, depth and spread at that hour are not observed here.",
            "SELL/SHORT trades need a short-capable venue; spot rTokens cannot be shorted (see side_split for the BUY-only view).",
        ],
    }


def backtest_json(res: BacktestResult, adapter: DataAdapter, sens: list[dict], notice: str | None,
                  study: dict | None = None) -> dict:
    st = adapter.status()
    out = {
        "project": "Rift24",
        "track": "Alpha Factory - Quantitative Strategies",
        "sub_theme": "After-Hours Information Pricing",
        "paper_backtest_only": "Paper/backtest only. No live-money trading.",
        "label_legend": LEGEND,
        "data": {
            "mode": st.mode, "status": st.label, "provenance": st.provenance, "notice": notice,
            "fetched_at_utc": st.fetched_at_utc, "last_bar_end_utc": st.last_bar_end_utc,
            "rtoken_prices": "OBSERVED", "cash_prices": "OBSERVED", "spread": "ESTIMATED (+4 bps fallback)",
            "fees": "ESTIMATED (8 bps default; exchange's own published fee is 20 bps round trip)",
            "no_synthetic_data": True,
        },
        "methodology": method_block(res, adapter),
        "pre_specified_test": {
            "hypothesis": "H1: fading large rToken void moves (signal = -R_rtoken) earns positive risk-adjusted returns after costs",
            "decision_time": "09:15 ET (fixed before any result was seen)",
            "verdict": "REJECTED" if (res.metrics["B_ALWAYS_FADE"]["FULL"]["mean_trade_net_bps"] or 0) <= 0 else "NOT REJECTED",
        },
        "results_label": "ESTIMATED - simulated net-of-cost returns on OBSERVED prices",
        "strategies": {s: _strategy_block(res, s, NAMES[s]) for s in STRATEGIES},
        "diagnostics": {"label": "OBSERVED unless the key says otherwise", **res.diagnostics},
        "sensitivities": {
            "label": "ESTIMATED - robustness checks on the pre-specified test, not used to choose the base configuration",
            "rows": sens},
        "targeted": [
            "Observed (not assumed) after-hours spreads once historical order-book snapshots are collected",
            "Bitget Signal / news-briefing live event feed on the event board",
            "Longer history as rToken listings age (sample here is ~90 days, ~13 weekends)",
            "A clean, untouched out-of-sample window for the exploratory residual strategy (forward test)",
        ],
        "reproduce": "python -m scripts.run_backtest",
    }
    if study:
        out["exploratory"] = exploratory_block(study)
    return out


def print_table(res: BacktestResult, sens: list[dict] | None = None, study: dict | None = None) -> str:
    w, cfg = res.window, res.cfg
    L: list[str] = []
    add = L.append
    add("=" * 108)
    add("RIFT24 BACKTEST  -  Alpha Factory / After-Hours Information Pricing   [paper/backtest only]")
    add("=" * 108)
    add(f"Window   : {w.start} -> {w.end}  ({w.total_days} calendar days)   IN-SAMPLE {w.start} -> {w.is_end} ({w.is_days} d)"
        f"   OUT-OF-SAMPLE {w.is_end + dt.timedelta(days=1)} -> {w.end} ({w.oos_days} d)")
    d = res.diagnostics
    add(f"Universe : {len({r.obs.view.symbol for r in res.rows})} rTokens | {d['sessions_scored']} sessions "
        f"({d['weekend_sessions_scored']} weekend voids) | {d['symbol_sessions_scored']} symbol-sessions "
        f"| {d['symbol_sessions_large_move']} pass the min-move gate")
    add(f"Costs    : {cfg.rt_cost_bps:g} bps round-trip + {cfg.spread_fallback_bps:g} bps spread (ESTIMATED fallback) "
        f"= {cfg.rt_cost_bps + cfg.spread_fallback_bps:g} bps | gate3 >= {cfg.min_move_cost_multiple:g}x cost | gate5 edge >= {cfg.edge_cost_multiple:g}x cost "
        f"& t >= {cfg.min_tstat:g}")
    add("Sharpe  : mean/std of per-session book returns x sqrt(252), rf=0, stand-down sessions = 0. Sortino: downside dev vs 0.")
    add("")
    hdr = f"{'':<16}{'period':<6}{'trades':>7}{'return%':>9}{'Sharpe':>8}{'+/-se':>7}{'Sortino':>9}{'MDD%':>7}{'turnover':>10}{'win%':>7}{'net bps/tr':>11}{'standdown':>10}"
    add(hdr)
    add("-" * len(hdr))
    for s in ("A_DO_NOTHING", "B_ALWAYS_FADE", "RIFT24"):
        for label, key in (("IS", "IS"), ("OOS", "OOS"), ("FULL", "FULL")):
            m = res.metrics[s][key]
            add(f"{NAMES[s] if key == 'IS' else '':<16}{label:<6}{m['trade_count']:>7}{_f(m['total_return_pct']):>9}"
                f"{_f(m['sharpe']):>8}{_f(m['sharpe_se']):>7}{_f(m['sortino']):>9}{_f(m['max_drawdown_pct']):>7}"
                f"{_f(m['turnover_x_book']):>10}{_f(None if m['win_rate'] is None else m['win_rate'] * 100, 1):>7}"
                f"{_f(m['mean_trade_net_bps'], 1):>11}{_f(None if m['stand_down_rate_sessions'] is None else m['stand_down_rate_sessions'] * 100, 1, pct=True):>10}")
        add("")
    add("OOS/IS Sharpe ratio (alert if OOS < 0.5 x IS):")
    for s in ("B_ALWAYS_FADE", "RIFT24"):
        o = res.metrics[s]["OOS_IS"]
        add(f"  {NAMES[s]:<14} ratio={_f(o['ratio'])}   {o['note']}")
    add("Rolling 30-day Sharpe (windows / min / median / max / last / share>0):")
    for s in ("B_ALWAYS_FADE", "RIFT24"):
        r = res.metrics[s]["FULL"]["rolling_30d_sharpe"]
        add(f"  {NAMES[s]:<14} {r['windows']} / {_f(r['min'])} / {_f(r['median'])} / {_f(r['max'])} / {_f(r['last'])} / {_f(r['share_positive'])}")
    add("")
    sd = d["stand_down"]
    add(f"Stand-down: Rift24 traded {sd['rift24_trades']} of {sd['symbol_sessions']} symbol-sessions "
        f"(stand-down {_f(sd['rift24_stand_down_rate_all_symbol_sessions'] * 100, 1, pct=True)}; "
        f"{_f(None if sd['rift24_stand_down_rate_among_large_moves'] is None else sd['rift24_stand_down_rate_among_large_moves'] * 100, 1, pct=True)} of large moves). "
        f"Always-fade traded {sd['always_fade_trades']}.")
    add(f"  binding gate: {sd['binding_gate_counts']}")
    add(f"  trades Rift24 REFUSED but always-fade took: n={sd['avoided_trades_n']}, mean net {_f(sd['avoided_trades_mean_net_bps'], 1)} bps"
        f" | trades both took: mean net {_f(sd['taken_trades_mean_net_bps'], 1)} bps")
    add("")
    add("Autopsy - is the void move over-stated vs the realised cash gap? (OBSERVED, large moves; beta<1 = over-shoot)")
    for k, v in d["priced_in_beta_large_moves"].items():
        add(f"  {k:<10} " + ("n/a" if v is None else f"beta={v['beta']:.2f}  95%CI [{v['ci95'][0]:.2f}, {v['ci95'][1]:.2f}]  n={v['n']}"))
    if "convergence_capture" in d:
        add(f"  overshoot share {d['large_move_overshoot_share']:.0%} (beyond cost {d['large_move_overshoot_beyond_cost_share']:.0%}); "
            f"median priced-in ratio {d['median_priced_in_ratio_large_moves']}; convergence capture slope {d['convergence_capture']['slope']}")
    if sens:
        add("")
        add("Sensitivities (robustness, NOT tuning):   OOS Sharpe  [trades]   B always-fade | Rift24")
        for r in sens:
            b, k = r["B_ALWAYS_FADE"], r["RIFT24"]
            add(f"  {r['label']:<58} {_f(b['OOS_sharpe']):>6} [{b['OOS_trades']:>3}] | {_f(k['OOS_sharpe']):>6} [{k['OOS_trades']:>3}]")
    if study:
        add("")
        add("EXPLORATORY / POST-HOC  (suggested by the null above, same sample - NOT a validated edge)")
        add("Priced-in curve: void moves vs the eventual cash gap, decision moved earlier in the void (OBSERVED, large moves)")
        add(f"  {'decision':<26}{'beta [95% CI]':<20}{'fade net bps':>13}{'follow net bps':>16}{'resid trades':>14}{'IS SR':>7}{'OOS SR':>8}")
        for g in study["grid"]:
            b = g["beta_large_moves"]
            r = g["residual"]
            add(f"  {g['decision_time']:<26}{b['beta']:.2f} [{b['ci95'][0]:.2f},{b['ci95'][1]:.2f}]".ljust(48)
                + f"{_f(g['always_fade']['mean_net_bps'], 1):>13}{_f(g['always_follow']['mean_net_bps'], 1):>16}"
                f"{r['trades']:>8} ({r['OOS_trades']} OOS){_f(r['IS_sharpe']):>7}{_f(r['OOS_sharpe']):>8}")
        add(f"  selected decision time (max IN-SAMPLE Sharpe): {study['selected_decision_time']}")
        sel = study["result"]
        add("")
        add(f"  At {study['selected_decision_time']}:      period trades  return%  Sharpe  +/-se  Sortino   MDD%  win%  net bps/tr")
        for s_, nm in (("B_ALWAYS_FADE", "B always-fade"), ("C_ALWAYS_FOLLOW", "C always-follow"), ("RIFT24_RESIDUAL", "RIFT24 residual")):
            for key in ("IS", "OOS"):
                m = sel.metrics[s_][key]
                add(f"    {nm if key == 'IS' else '':<18}{key:<5}{m['trade_count']:>7}{_f(m['total_return_pct']):>9}{_f(m['sharpe']):>8}"
                    f"{_f(m['sharpe_se']):>7}{_f(m['sortino']):>9}{_f(m['max_drawdown_pct']):>7}"
                    f"{_f(None if m['win_rate'] is None else m['win_rate'] * 100, 0):>6}{_f(m['mean_trade_net_bps'], 1):>11}")
        o = sel.metrics["RIFT24_RESIDUAL"]["OOS_IS"]
        add(f"    OOS/IS Sharpe ratio (residual): {_f(o['ratio'])}  {o['note']}")
        rb = study["robustness"]
        dc, cc = rb["day_clustered"], rb.get("concentration", {})
        add(f"    robustness: {dc['trades']} trades on {dc['distinct_days']} distinct days; day-level mean {dc.get('day_level_mean_net_bps')} bps (t={dc.get('day_level_tstat')});"
            f" top-3 days = {cc.get('top3_share_of_total_pnl')} of P&L; return without them {cc.get('total_return_pct_without_top3_days')}% (Sharpe {cc.get('sharpe_without_top3_days')})")
        for k, v in rb["cost_stress"].items():
            add(f"    cost stress [{k}]: {v['trades']} trades ({v['OOS_trades']} OOS), IS SR {_f(v['IS_sharpe'])}, OOS SR {_f(v['OOS_sharpe'])}")
        sp = sel.diagnostics["side_split"]
        add(f"    side split (residual): BUY {sp['RIFT24_RESIDUAL:BUY']['trades']} trades mean {sp['RIFT24_RESIDUAL:BUY']['mean_net_bps']} bps | "
            f"SELL/SHORT {sp['RIFT24_RESIDUAL:SELL/SHORT']['trades']} trades mean {sp['RIFT24_RESIDUAL:SELL/SHORT']['mean_net_bps']} bps")
    add("=" * 108)
    return "\n".join(L)


def write_json(obj: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, sort_keys=False) + "\n", encoding="utf-8")


def write_thesis(res, adapter, sens, study, path):
    from .thesis import write_thesis as _w
    _w(res, adapter, sens, study, path)
