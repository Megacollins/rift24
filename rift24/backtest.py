"""Walk-forward backtest of the three strategies on identical mechanics.

  A  DO NOTHING    - no trades.
  B  ALWAYS FADE   - fade every large void move (gates 1, 2, 3, 8).
  R  RIFT24        - B plus the edge / freshness / cost gates (all eight).

Walk-forward rule (no look-ahead): the priced-in model used for the decision at time t is fitted only on
sessions whose outcome was completely known before t (`Outcome.known_utc <= decision_utc`). Sessions that
share today's decision time are never in the training set, and neither are warm-up sessions from the future.
"""
from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass, field
from statistics import mean, median

from . import calendar as cal
from . import metrics as M
from .data import DataAdapter
from .residual import (
    Config,
    Decision,
    Exclusion,
    Observation,
    PricedIn,
    RunningFit,
    autopsy,
    build_observation,
    decide,
    decide_always_fade,
    decide_always_follow,
    decide_residual,
    is_large_move,
    total_cost_bps,
)

STRATEGIES = ("A_DO_NOTHING", "B_ALWAYS_FADE", "RIFT24")
EXPLORATORY = ("C_ALWAYS_FOLLOW", "RIFT24_RESIDUAL")  # post-hoc study: never the pre-specified result


@dataclass(frozen=True)
class Window:
    start: dt.date  # first session open date scored
    is_end: dt.date  # last in-sample session open date
    end: dt.date  # last out-of-sample session open date

    @classmethod
    def ending(cls, end: dt.date, total_days: int = 90, oos_days: int = 30) -> "Window":
        start = end - dt.timedelta(days=total_days - 1)
        is_end = end - dt.timedelta(days=oos_days)
        return cls(start, is_end, end)

    def period(self, d: dt.date) -> str | None:
        if d < self.start or d > self.end:
            return None
        return "IS" if d <= self.is_end else "OOS"

    @property
    def total_days(self) -> int:
        return (self.end - self.start).days + 1

    @property
    def oos_days(self) -> int:
        return (self.end - self.is_end).days

    @property
    def is_days(self) -> int:
        return (self.is_end - self.start).days + 1


@dataclass(frozen=True)
class Trade:
    strategy: str
    symbol: str
    rtoken: str
    open_date: dt.date
    period: str
    stance: str
    direction: int
    weight: float
    void_bps: float
    cash_bps: float
    gross_bps: float
    cost_bps: float
    net_bps: float
    expected_edge_bps: float | None


@dataclass
class Row:
    """One symbol-session: what each strategy decided, and what happened."""

    obs: Observation
    period: str | None
    model: PricedIn | None
    decisions: dict[str, Decision]


@dataclass
class BacktestResult:
    cfg: Config
    window: Window
    rows: list[Row]
    exclusions: list[Exclusion]
    trades: dict[str, list[Trade]]
    daily: dict[str, list[tuple[dt.date, float]]]
    metrics: dict[str, dict[str, dict]]  # strategy -> FULL/IS/OOS -> summary
    diagnostics: dict = field(default_factory=dict)
    data_validation: dict = field(default_factory=dict)


def last_complete_session(adapter: DataAdapter) -> dt.date:
    """Latest trading day whose regular close has passed inside the data, with a cash daily bar for every ticker."""
    last_end = min(adapter.last_bar_end_ms(i.bitget_symbol) or 0 for i in adapter.instruments())
    d = dt.datetime.fromtimestamp(last_end / 1000, dt.timezone.utc).astimezone(cal.ET).date()
    while True:
        if (cal.is_trading_day(d) and cal.session_close(d).timestamp() * 1000 <= last_end
                and all(adapter.cash_day(i.underlying, d) for i in adapter.instruments())):
            return d
        d -= dt.timedelta(days=1)


# ------------------------------------------------------------------------------- dataset


def build_dataset(adapter: DataAdapter, cfg: Config, first_open: dt.date, last_open: dt.date):
    obs: list[Observation] = []
    excl: list[Exclusion] = []
    for s in cal.build_sessions(first_open, last_open):
        for ins in adapter.instruments():
            r = build_observation(adapter, ins, s, cfg)
            (obs if isinstance(r, Observation) else excl).append(r)
    obs.sort(key=lambda o: (o.view.decision_utc, o.view.symbol))
    return obs, excl


def walk_forward(observations: list[Observation], cfg: Config, window: Window) -> list[Row]:
    """Decide every observation using only outcomes known strictly before its decision time."""
    by_known = sorted(observations, key=lambda o: o.outcome.known_utc)
    fit, ptr = RunningFit(), 0
    rows: list[Row] = []
    for o in observations:  # already sorted by decision time
        while ptr < len(by_known) and by_known[ptr].outcome.known_utc <= o.view.decision_utc:
            k = by_known[ptr]
            if is_large_move(k.view, cfg):
                fit.add(k.view.void_bps, k.outcome.cash_bps)
            ptr += 1
        est = fit.fit()
        rows.append(Row(o, window.period(o.view.open_date), est, {
            "B_ALWAYS_FADE": decide_always_fade(o.view, est, cfg),
            "RIFT24": decide(o.view, est, cfg),
            "C_ALWAYS_FOLLOW": decide_always_follow(o.view, est, cfg),
            "RIFT24_RESIDUAL": decide_residual(o.view, est, cfg),
        }))
    return rows


# ------------------------------------------------------------------------------- simulation


def _trades_for(strategy: str, rows: list[Row], cfg: Config) -> list[Trade]:
    by_day: dict[dt.date, list[Row]] = {}
    for r in rows:
        if r.period and r.decisions[strategy].traded:
            by_day.setdefault(r.obs.view.open_date, []).append(r)
    out: list[Trade] = []
    for d, day in sorted(by_day.items()):
        w = min(cfg.max_name_pct, cfg.max_gross_pct / len(day))  # Gate 7 + unlevered book
        for r in day:
            dec, v, o = r.decisions[strategy], r.obs.view, r.obs.outcome
            gross = dec.direction * o.hold_ret * 1e4
            out.append(Trade(strategy, v.symbol, v.rtoken, d, r.period, dec.stance, dec.direction, w, v.void_bps,
                             o.cash_bps, gross, dec.cost_bps, gross - dec.cost_bps, dec.expected_edge_bps))
    return out


def _daily(trades: list[Trade], days: list[dt.date]) -> list[tuple[dt.date, float]]:
    pnl: dict[dt.date, float] = {}
    for t in trades:
        pnl[t.open_date] = pnl.get(t.open_date, 0.0) + t.weight * t.net_bps / 1e4
    return [(d, pnl.get(d, 0.0)) for d in days]


def _period_metrics(trades: list[Trade], daily: list[tuple[dt.date, float]], window: Window) -> dict[str, dict]:
    out = {}
    for label in ("FULL", "IS", "OOS"):
        sel = [(d, r) for d, r in daily if label == "FULL" or window.period(d) == label]
        tr = [t for t in trades if label == "FULL" or t.period == label]
        out[label] = M.summarize(
            [d for d, _ in sel], [r for _, r in sel], [t.net_bps for t in tr],
            sum(2 * t.weight for t in tr), len(sel),
        )
    is_sr, oos_sr = out["IS"]["sharpe"], out["OOS"]["sharpe"]
    out["OOS_IS"] = M.oos_is(is_sr, oos_sr)
    out["rolling_30d_sharpe_series"] = M.rolling_sharpe([d for d, _ in daily], [r for _, r in daily])
    return out


def run_backtest(adapter: DataAdapter, cfg: Config, window: Window, warmup_start: dt.date | None = None) -> BacktestResult:
    first = warmup_start or (window.start - dt.timedelta(days=45))
    observations, exclusions = build_dataset(adapter, cfg, first, window.end)
    rows = walk_forward(observations, cfg, window)
    days = cal.trading_days(window.start, window.end)

    trades = {"A_DO_NOTHING": [], "B_ALWAYS_FADE": _trades_for("B_ALWAYS_FADE", rows, cfg),
              "RIFT24": _trades_for("RIFT24", rows, cfg), "C_ALWAYS_FOLLOW": _trades_for("C_ALWAYS_FOLLOW", rows, cfg),
              "RIFT24_RESIDUAL": _trades_for("RIFT24_RESIDUAL", rows, cfg)}
    daily = {k: _daily(v, days) for k, v in trades.items()}
    metrics = {k: _period_metrics(trades[k], daily[k], window) for k in STRATEGIES + EXPLORATORY}

    res = BacktestResult(cfg, window, rows, exclusions, trades, daily, metrics)
    res.data_validation = validate_open_alignment(adapter, rows)
    res.diagnostics = diagnostics(res)
    return res


# ------------------------------------------------------------------------------- diagnostics


def validate_open_alignment(adapter: DataAdapter, rows: list[Row]) -> dict:
    """Independent data check: the rToken's first trade in the 09:30 bar should equal the cash open print.
    Two unrelated sources (Bitget candles, Yahoo daily open) agreeing to a few bps validates the session alignment
    and the cash series. OBSERVED."""
    diffs = []
    for r in rows:
        if not r.period:
            continue
        v = r.obs.view
        open_ms = int(cal.session_open(v.open_date).timestamp() * 1000)
        b = adapter.rtoken_bar(v.symbol, open_ms)
        c = adapter.cash_day(v.underlying, v.open_date)
        if b is not None and c is not None:
            diffs.append(abs(b.open / c.open - 1) * 1e4)
    if not diffs:
        return {}
    diffs.sort()
    return {
        "label": "OBSERVED: |rToken 09:30 bar open / Yahoo cash open - 1|, bps, scored symbol-sessions with a 09:30 trade",
        "n": len(diffs), "median_bps": round(diffs[len(diffs) // 2], 2),
        "p90_bps": round(diffs[int(len(diffs) * 0.9)], 2), "share_within_10_bps": round(sum(d <= 10 for d in diffs) / len(diffs), 3),
    }


def _ols_origin(pairs: list[tuple[float, float]]) -> dict | None:
    rf = RunningFit()
    for x, y in pairs:
        rf.add(x, y)
    f = rf.fit()
    if f is None:
        return None
    return {"beta": round(f.beta, 3), "se": round(f.se, 3), "ci95": [round(f.beta - 1.96 * f.se, 3), round(f.beta + 1.96 * f.se, 3)],
            "n": f.n, "t_overshoot": round(f.t_overshoot, 2)}


def diagnostics(res: BacktestResult) -> dict:
    cfg, w = res.cfg, res.window
    scored = [r for r in res.rows if r.period]
    large = [r for r in scored if is_large_move(r.obs.view, cfg)]
    out: dict = {}

    out["sessions_scored"] = len({r.obs.view.open_date for r in scored})
    out["symbol_sessions_scored"] = len(scored)
    out["symbol_sessions_large_move"] = len(large)
    out["weekend_sessions_scored"] = len({r.obs.view.open_date for r in scored if r.obs.view.kind == "weekend"})
    out["symbol_sessions_by_kind"] = {k: sum(1 for r in scored if r.obs.view.kind == k) for k in ("overnight", "weekend", "holiday")}
    reasons: dict[str, int] = {}
    for e in res.exclusions:
        if w.start <= e.open_date <= w.end:
            reasons[e.reason] = reasons.get(e.reason, 0) + 1
    out["excluded_symbol_sessions_in_window"] = reasons

    def pairs(rs):
        return [(r.obs.view.void_bps, r.obs.outcome.cash_bps) for r in rs]

    # --- autopsy: does the void move over-state the eventual cash gap? (in-window; NOT used by any decision)
    out["priced_in_beta_large_moves"] = {
        "ALL": _ols_origin(pairs(large)),
        "IS": _ols_origin(pairs([r for r in large if r.period == "IS"])),
        "OOS": _ols_origin(pairs([r for r in large if r.period == "OOS"])),
        "weekend": _ols_origin(pairs([r for r in large if r.obs.view.kind == "weekend"])),
        "overnight": _ols_origin(pairs([r for r in large if r.obs.view.kind == "overnight"])),
    }
    out["priced_in_beta_all_moves"] = _ols_origin(pairs(scored))
    out["data_validation"] = res.data_validation
    if large:
        over = [1 for r in large if (r.obs.view.void_bps - r.obs.outcome.cash_bps) * math.copysign(1, r.obs.view.void_bps) > 0]
        over_c = [1 for r in large if (r.obs.view.void_bps - r.obs.outcome.cash_bps) * math.copysign(1, r.obs.view.void_bps)
                  > total_cost_bps(r.obs.view, cfg)]
        ratios = [r.obs.view.void_bps / r.obs.outcome.cash_bps for r in large if abs(r.obs.outcome.cash_bps) >= 5.0]
        out["large_move_overshoot_share"] = round(len(over) / len(large), 3)
        out["large_move_overshoot_beyond_cost_share"] = round(len(over_c) / len(large), 3)
        out["median_priced_in_ratio_large_moves"] = round(median(ratios), 3) if ratios else None
        out["median_abs_void_bps_large_moves"] = round(median(abs(r.obs.view.void_bps) for r in large), 1)
        # Do the trade mechanics actually capture convergence? realised hold return vs (cash gap - printed move)
        xs = [r.obs.outcome.cash_bps - r.obs.view.void_bps for r in large]
        ys = [r.obs.outcome.hold_ret * 1e4 for r in large]
        sxx = sum(x * x for x in xs)
        out["convergence_capture"] = {
            "definition": "regress realised rToken return over the hold window on (R_cash - R_void), through origin, large-move sessions",
            "slope": round(sum(x * y for x, y in zip(xs, ys)) / sxx, 3) if sxx else None,
            "n": len(xs),
        }
    # --- stand-down accounting
    r_dec = [r for r in scored]
    trades_r = [r for r in r_dec if r.decisions["RIFT24"].traded]
    trades_b = [r for r in r_dec if r.decisions["B_ALWAYS_FADE"].traded]
    avoided = [r for r in trades_b if not r.decisions["RIFT24"].traded]
    taken = [r for r in trades_b if r.decisions["RIFT24"].traded]

    def net(r):
        d = r.decisions["B_ALWAYS_FADE"]
        return d.direction * r.obs.outcome.hold_ret * 1e4 - d.cost_bps

    binding: dict[str, int] = {}
    for r in scored:
        g = r.decisions["RIFT24"].binding_gate
        key = "TRADED" if g is None else f"Gate {g}"
        binding[key] = binding.get(key, 0) + 1
    out["stand_down"] = {
        "symbol_sessions": len(scored),
        "rift24_trades": len(trades_r),
        "always_fade_trades": len(trades_b),
        "rift24_stand_down_rate_all_symbol_sessions": round(1 - len(trades_r) / len(scored), 4) if scored else None,
        "rift24_stand_down_rate_among_large_moves": round(1 - len(trades_r) / len(large), 4) if large else None,
        "binding_gate_counts": dict(sorted(binding.items())),
        "avoided_trades_n": len(avoided),
        "avoided_trades_mean_net_bps": round(mean(net(r) for r in avoided), 2) if avoided else None,
        "taken_trades_mean_net_bps": round(mean(net(r) for r in taken), 2) if taken else None,
    }
    # --- side split: spot cannot short, so the SELL/SHORT leg needs a short-capable venue (perp/margin)
    split = {}
    for s in ("B_ALWAYS_FADE", "RIFT24", "C_ALWAYS_FOLLOW", "RIFT24_RESIDUAL"):
        for side, sign in (("BUY", 1), ("SELL/SHORT", -1)):
            ts = [t for t in res.trades[s] if t.direction == sign]
            split[f"{s}:{side}"] = {"trades": len(ts), "mean_net_bps": round(mean(t.net_bps for t in ts), 2) if ts else None,
                                    "win_rate": round(sum(t.net_bps > 0 for t in ts) / len(ts), 3) if ts else None}
    out["side_split"] = split
    # --- is there ANY gross edge before costs? (always-fade trades, all directions)
    tb = res.trades["B_ALWAYS_FADE"]
    if len(tb) > 2:
        g = [t.gross_bps for t in tb]
        sd = (sum((x - mean(g)) ** 2 for x in g) / (len(g) - 1)) ** 0.5
        out["always_fade_gross"] = {
            "label": "OBSERVED price moves, before any cost", "trades": len(g), "mean_gross_bps": round(mean(g), 2),
            "tstat": round(mean(g) / (sd / len(g) ** 0.5), 2) if sd > 0 else None,
            "mean_cost_bps": round(mean(t.cost_bps for t in tb), 2),
        }
    return out


# ------------------------------------------------------------------------------- sensitivities


PRICING_LEADS = (1, 4, 12, 22, 54)  # 15-min bars before the open: 09:15, 08:30, 06:30, 04:00, 20:00(prior evening) ET


def _lead_label(lead: int) -> str:
    mins = 9 * 60 + 30 - lead * 15
    if mins >= 0:
        return f"{mins // 60:02d}:{mins % 60:02d} ET"
    return f"{(mins + 1440) // 60:02d}:{(mins + 1440) % 60:02d} ET (prior evening)"


def _strategy_slice(r: BacktestResult, s: str) -> dict:
    m = r.metrics[s]
    return {"trades": m["FULL"]["trade_count"], "IS_trades": m["IS"]["trade_count"], "OOS_trades": m["OOS"]["trade_count"],
            "IS_sharpe": m["IS"]["sharpe"], "OOS_sharpe": m["OOS"]["sharpe"], "mean_net_bps": m["FULL"]["mean_trade_net_bps"],
            "OOS_mean_net_bps": m["OOS"]["mean_trade_net_bps"], "OOS_return_pct": m["OOS"]["total_return_pct"]}


def pricing_curve(adapter: DataAdapter, base: Config, window: Window) -> tuple[list[dict], dict[int, BacktestResult]]:
    """POST-HOC analysis motivated by the null result: how much of the cash gap is already priced in, and by when?

    Re-runs the same walk-forward engine with the decision moved earlier in the void. Reported in full."""
    rows, results = [], {}
    for lead in PRICING_LEADS:
        r = run_backtest(adapter, base.with_(decision_lead_bars=lead), window)
        results[lead] = r
        large = [x for x in r.rows if x.period and is_large_move(x.obs.view, r.cfg)]
        rows.append({
            "decision_lead_bars": lead,
            "decision_time": _lead_label(lead),
            "hours_before_open": lead * 0.25,
            "symbol_sessions_scored": len([x for x in r.rows if x.period]),
            "large_move_sessions": len(large),
            "beta_large_moves": _ols_origin([(x.obs.view.void_bps, x.obs.outcome.cash_bps) for x in large]),
            "beta_large_moves_OOS": _ols_origin([(x.obs.view.void_bps, x.obs.outcome.cash_bps) for x in large if x.period == "OOS"]),
            "always_fade": {**_strategy_slice(r, "B_ALWAYS_FADE"), "mean_gross_bps": r.diagnostics.get("always_fade_gross", {}).get("mean_gross_bps")},
            "always_follow": _strategy_slice(r, "C_ALWAYS_FOLLOW"),
            "residual": _strategy_slice(r, "RIFT24_RESIDUAL"),
        })
    return rows, results


def _day_stats(trades: list[Trade]) -> dict:
    by: dict[dt.date, list[float]] = {}
    for t in trades:
        by.setdefault(t.open_date, []).append(t.net_bps)
    days = [mean(v) for v in by.values()]
    out = {"trades": len(trades), "distinct_days": len(by)}
    if len(days) >= 3:
        m = mean(days)
        sd = (sum((x - m) ** 2 for x in days) / (len(days) - 1)) ** 0.5
        out["day_level_mean_net_bps"] = round(m, 1)
        out["day_level_tstat"] = round(m / (sd / len(days) ** 0.5), 2) if sd > 0 else None
    return out


def robustness(adapter: DataAdapter, res: BacktestResult, strategy: str) -> dict:
    """Skeptic's checks on one strategy: is it a handful of days, and does it survive a harsher cost stress?"""
    tr = res.trades[strategy]
    out: dict = {"strategy": strategy, "day_clustered": _day_stats(tr)}
    if tr:
        pnl: dict[dt.date, float] = {}
        for t in tr:
            pnl[t.open_date] = pnl.get(t.open_date, 0.0) + t.weight * t.net_bps / 1e4
        best = sorted(pnl.items(), key=lambda kv: -kv[1])
        total = sum(pnl.values())
        top3 = [d for d, _ in best[:3]]
        rets = [(d, 0.0 if d in top3 else r) for d, r in res.daily[strategy]]
        out["concentration"] = {
            "top3_days": [d.isoformat() for d in top3],
            "top3_share_of_total_pnl": round(sum(v for _, v in best[:3]) / total, 3) if total > 0 else None,
            "total_return_pct": M.total_return([r for _, r in res.daily[strategy]]) * 100,
            "total_return_pct_without_top3_days": round(M.total_return([r for _, r in rets]) * 100, 3),
            "sharpe_without_top3_days": None if M.sharpe([r for _, r in rets]) is None else round(M.sharpe([r for _, r in rets]), 3),
            "winning_days_share": round(sum(1 for v in pnl.values() if v > 0) / len(pnl), 3),
        }
        out["concentration"]["total_return_pct"] = round(out["concentration"]["total_return_pct"], 3)
    stress = {}
    for label, kw in (("fees 20 bps + spread 4 bps", {"rt_cost_bps": 20.0}),
                      ("fees 8 bps + thin-book spread 25 bps", {"spread_fallback_bps": 25.0}),
                      ("fees 20 bps + thin-book spread 25 bps", {"rt_cost_bps": 20.0, "spread_fallback_bps": 25.0})):
        r = run_backtest(adapter, res.cfg.with_(**kw), res.window)
        stress[label] = _strategy_slice(r, strategy)
    out["cost_stress"] = stress
    return out


def exploratory_study(adapter: DataAdapter, base: Config, window: Window) -> dict:
    """Post-hoc, two-sided residual strategy. The decision time is chosen by IN-SAMPLE Sharpe among a fixed grid."""
    curve, results = pricing_curve(adapter, base, window)
    cands = [(r["residual"]["IS_sharpe"], r["decision_lead_bars"]) for r in curve if r["residual"]["IS_sharpe"] is not None]
    lead = max(cands)[1] if cands else PRICING_LEADS[0]
    sel = results[lead]
    return {
        "label": "EXPLORATORY / POST-HOC - suggested by the pre-specified null result on this same sample",
        "selection_rule": "decision time = argmax IN-SAMPLE Sharpe of the residual strategy over the fixed grid below; OOS was "
                          "then evaluated at that time. The author had already seen OOS numbers for the grid, so OOS is NOT clean.",
        "grid": curve,
        "selected_lead_bars": lead,
        "selected_decision_time": _lead_label(lead),
        "result": sel,
        "results_by_lead": results,
        "robustness": robustness(adapter, sel, "RIFT24_RESIDUAL"),
    }


def sensitivities(adapter: DataAdapter, base: Config, window: Window) -> list[dict]:
    """Robustness grid. These are NOT used to choose the base configuration."""
    grid = [
        ("BASE (8 bps + 4 bps spread)", {}),
        ("Observed exchange fee: 20 bps round trip + 4 bps spread", {"rt_cost_bps": 20.0}),
        ("Costs x0: no fees, no spread (upper bound; also drops the Gate-3 threshold to 0)", {"rt_cost_bps": 0.0, "spread_fallback_bps": 0.0}),
        ("Exit 15 min AFTER the open (includes regular-session drift)", {"exit_after_open_bars": 1}),
        ("Exit 30 min after the open", {"exit_after_open_bars": 2}),
        ("Exit 60 min after the open", {"exit_after_open_bars": 4}),
        ("Gate 5 without significance test (t >= 0)", {"min_tstat": 0.0}),
        ("Gate 3 threshold 1x cost", {"min_move_cost_multiple": 1.0}),
        ("Gate 3 threshold 4x cost", {"min_move_cost_multiple": 4.0}),
    ]
    rows = []
    for label, kw in grid:
        r = run_backtest(adapter, base.with_(**kw), window)
        row = {"label": label, "overrides": kw}
        for s in ("B_ALWAYS_FADE", "RIFT24"):
            m = r.metrics[s]
            row[s] = {"IS_sharpe": m["IS"]["sharpe"], "OOS_sharpe": m["OOS"]["sharpe"], "trades": m["FULL"]["trade_count"],
                      "OOS_trades": m["OOS"]["trade_count"], "mean_net_bps": m["FULL"]["mean_trade_net_bps"],
                      "OOS_return_pct": m["OOS"]["total_return_pct"]}
        rows.append(row)
    return rows
