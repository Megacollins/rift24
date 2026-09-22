"""Pre-registered forward test of the exploratory 04:00 ET residual rule.

The rule and its model are FROZEN before any forward data exists (data/forward/frozen_model.json, sha256-stamped).
Three append-only steps, each safe to re-run (existing ids are never overwritten):

  freeze    fit the priced-in model once on the fixtures (all sessions, decision 04:00 ET) and stamp it
  snapshot  at/after 04:00 ET: log the frozen rule's stance for every allow-listed rToken, plus OBSERVED spreads
  settle    after the 09:30 ET open: log the realised cash gap and the rule's net P&L

Nothing here changes a decision after the fact; the model is never refit on forward data.
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import hashlib
import json
import math
from pathlib import Path
from statistics import mean, stdev

from . import calendar as cal
from .backtest import Window, build_dataset, last_complete_session
from .data import BAR_MS, DataAdapter
from .residual import (Config, PreOpenView, PricedIn, RunningFit, _price_at, decide_residual, is_large_move,
                       total_cost_bps)

ROOT = Path(__file__).resolve().parents[1]
FWD = ROOT / "data" / "forward"
LEAD_BARS = 22  # 04:00 ET
RULE = "RIFT24_RESIDUAL"
CFG = Config().with_(decision_lead_bars=LEAD_BARS)  # everything else is the base configuration


class ForwardError(RuntimeError):
    pass


# ------------------------------------------------------------------------------- freeze
def _digest(core: dict) -> str:
    return hashlib.sha256(json.dumps(core, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def freeze_model(adapter: DataAdapter, path: Path | None = None, *, overwrite: bool = False) -> dict:
    path = path or FWD / "frozen_model.json"
    if path.exists() and not overwrite:
        raise ForwardError(f"{path} already exists: the model is frozen. A new model means a new registered version.")
    last = last_complete_session(adapter)
    w = Window.ending(last, 90, 30)
    obs, _ = build_dataset(adapter, CFG, w.start - dt.timedelta(days=45), last)
    fit = RunningFit()
    for o in obs:
        if is_large_move(o.view, CFG):
            fit.add(o.view.void_bps, o.outcome.cash_bps)
    est = fit.fit()
    if est is None:
        raise ForwardError("not enough data to fit the model")
    core = {"rule": RULE, "decision_lead_bars": LEAD_BARS, "beta": est.beta, "se": est.se, "n": est.n,
            "fitted_through_session": last.isoformat(), "config": dataclasses.asdict(CFG)}
    doc = {**core, "sha256": _digest(core),
           "note": "Fitted once on the fixtures. Never refit on forward data. Changing any field is a new registered version."}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    return doc


def load_model(path: Path | None = None) -> tuple[PricedIn, dict]:
    path = path or FWD / "frozen_model.json"
    if not path.exists():
        raise ForwardError("no frozen model: run `python -m scripts.forward_test freeze` first")
    doc = json.loads(path.read_text(encoding="utf-8"))
    core = {k: doc[k] for k in ("rule", "decision_lead_bars", "beta", "se", "n", "fitted_through_session", "config")}
    if _digest(core) != doc["sha256"]:
        raise ForwardError("frozen_model.json was modified after freezing (sha256 mismatch)")
    if doc["config"] != dataclasses.asdict(CFG):
        raise ForwardError("Config differs from the frozen registration: register a new version instead of editing")
    return PricedIn(doc["beta"], doc["se"], doc["n"]), doc


# ------------------------------------------------------------------------------- log helpers
def _read(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def _append(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, sort_keys=True) + "\n")


def next_session(now: dt.datetime) -> cal.Session:
    v = cal.surrounding_void(now)
    if v is None:
        raise ForwardError("cash market is open: there is no pre-open decision to take")
    return cal.make_session(v.next_open_utc.astimezone(cal.ET).date())


# ------------------------------------------------------------------------------- snapshot
def take_snapshot(adapter: DataAdapter, now: dt.datetime, model: PricedIn, model_meta: dict,
                  *, log: Path | None = None, dry_run: bool = False) -> list[dict]:
    log = log or FWD / "log.jsonl"
    sess = next_session(now)
    open_ms, close_ms = int(sess.open_utc.timestamp() * 1000), int(sess.close_utc.timestamp() * 1000)
    dec_ms = open_ms - LEAD_BARS * BAR_MS
    dec_utc = dt.datetime.fromtimestamp(dec_ms / 1000, dt.timezone.utc)
    if now < dec_utc:
        raise ForwardError(f"too early: the decision time is {dec_utc.astimezone(cal.ET):%a %d %b %H:%M ET}")
    if now >= sess.open_utc:
        raise ForwardError("too late: the cash market has opened")
    seen = {r["id"] for r in _read(log)}
    spreads = adapter.observed_spread_snapshot()
    out = []
    for ins in adapter.instruments():
        rid = f"{sess.open_date}-{ins.bitget_symbol}"
        if rid in seen:
            continue
        base = _price_at(adapter, ins.bitget_symbol, close_ms, CFG.max_carry_bars)
        dec = _price_at(adapter, ins.bitget_symbol, dec_ms, CFG.max_carry_bars)
        row = {"id": rid, "open_date": sess.open_date.isoformat(), "symbol": ins.bitget_symbol, "rtoken": ins.rtoken,
               "underlying": ins.underlying, "decision_utc": dec_utc.isoformat().replace("+00:00", "Z"),
               "run_at_utc": now.isoformat().replace("+00:00", "Z"), "model_sha256": model_meta["sha256"],
               "kind": sess.kind, "void_hours": sess.void_hours}
        if base is None or dec is None:
            row.update(stance="DO NOTHING", binding_gate=None, reason="no rToken price near close/decision (data missing)")
            out.append(row)
            continue
        sp = spreads.get(ins.bitget_symbol) or {}
        view = PreOpenView(ins.bitget_symbol, ins.rtoken, ins.underlying, sess.open_date, sess.kind, sess.void_hours, dec_utc,
                           base[0], dec[0], (dec_ms - dec[1]) / 60_000, CFG.spread_fallback_bps, "ESTIMATED")
        d = decide_residual(view, model, CFG, now_utc=now)
        row.update(
            base_px=base[0], decision_px=dec[0], void_bps=round(view.void_bps, 2), staleness_min=view.staleness_min,
            stance=d.stance, side=d.side, binding_gate=d.binding_gate, direction=d.direction,
            implied_cash_bps=None if d.implied_cash_bps is None else round(d.implied_cash_bps, 2),
            residual_bps=None if d.residual_bps is None else round(d.residual_bps, 2),
            expected_edge_bps=None if d.expected_edge_bps is None else round(d.expected_edge_bps, 2),
            cost_bps_assumed=d.cost_bps, spread_bps_observed=sp.get("spread_bps"), spread_observed_at_ms=sp.get("ts_ms"),
            gates=[{"gate": g.gate, "passed": g.passed} for g in d.gates],
        )
        out.append(row)
    if not dry_run:
        _append(log, out)
    return out


# ------------------------------------------------------------------------------- settle
def settle(adapter: DataAdapter, now: dt.datetime, *, log: Path | None = None, outcomes: Path | None = None,
           dry_run: bool = False) -> list[dict]:
    log, outcomes = log or FWD / "log.jsonl", outcomes or FWD / "outcomes.jsonl"
    done = {r["id"] for r in _read(outcomes)}
    ins_by = {i.bitget_symbol: i for i in adapter.instruments()}
    out = []
    for e in _read(log):
        if e["id"] in done:
            continue
        sess = cal.make_session(dt.date.fromisoformat(e["open_date"]))
        if now < sess.open_utc + dt.timedelta(minutes=5):
            continue  # cash gap not yet observable
        sym = e["symbol"]
        open_ms = int(sess.open_utc.timestamp() * 1000)
        dec_ms = int(dt.datetime.fromisoformat(e["decision_utc"].replace("Z", "+00:00")).timestamp() * 1000)
        c0, c1 = adapter.cash_day(ins_by[sym].underlying, sess.prev_date), adapter.cash_day(ins_by[sym].underlying, sess.open_date)
        entry, exit_b = adapter.rtoken_bar(sym, dec_ms), adapter.rtoken_bar(sym, open_ms - BAR_MS)
        if not (c0 and c1 and entry and exit_b):
            continue  # data not available yet: try again on the next run
        hold = exit_b.close / entry.open - 1.0
        res = {"id": e["id"], "open_date": e["open_date"], "symbol": sym, "rtoken": e["rtoken"], "stance": e["stance"],
               "settled_at_utc": now.isoformat().replace("+00:00", "Z"),
               "realized_cash_gap_bps": round((c1.open / c0.close - 1) * 1e4, 2), "hold_return_bps": round(hold * 1e4, 2),
               "entry_px": entry.open, "exit_px": exit_b.close}
        if e.get("direction"):
            gross = e["direction"] * hold * 1e4
            res["gross_bps"] = round(gross, 2)
            res["net_bps_assumed_cost"] = round(gross - e["cost_bps_assumed"], 2)
            if e.get("spread_bps_observed") is not None:
                res["net_bps_observed_spread"] = round(gross - (CFG.rt_cost_bps + e["spread_bps_observed"]), 2)
        out.append(res)
    if not dry_run:
        _append(outcomes, out)
    return out


# ------------------------------------------------------------------------------- report
def summarize(log: Path | None = None, outcomes: Path | None = None) -> dict:
    log, outcomes = log or FWD / "log.jsonl", outcomes or FWD / "outcomes.jsonl"
    L, O = _read(log), {r["id"]: r for r in _read(outcomes)}
    trades = [O[e["id"]] | {"weight_group": e["open_date"]} for e in L if e.get("direction") and e["id"] in O]
    by_day: dict[str, list[float]] = {}
    for t in trades:
        by_day.setdefault(t["open_date"], []).append(t["net_bps_assumed_cost"])
    rets = []
    for d, v in sorted(by_day.items()):
        w = min(CFG.max_name_pct, CFG.max_gross_pct / len(v))
        rets.append(sum(w * x / 1e4 for x in v))
    day_means = [mean(v) for v in by_day.values()]
    nets = [t["net_bps_assumed_cost"] for t in trades]
    out = {
        "sessions_logged": len({e["open_date"] for e in L}), "symbol_sessions_logged": len(L),
        "settled": len(O), "stand_down": sum(1 for e in L if not e.get("direction")),
        "trades_taken": len([e for e in L if e.get("direction")]), "trades_settled": len(trades),
        "distinct_trade_days": len(by_day),
        "mean_net_bps_assumed_cost": round(mean(nets), 2) if nets else None,
        "win_rate": round(sum(x > 0 for x in nets) / len(nets), 3) if nets else None,
        "day_level_tstat": None,
        "observed_spread_bps_mean": None,
    }
    if len(day_means) >= 3 and stdev(day_means) > 0:
        out["day_level_tstat"] = round(mean(day_means) / (stdev(day_means) / math.sqrt(len(day_means))), 2)
    sp = [e["spread_bps_observed"] for e in L if e.get("spread_bps_observed") is not None]
    if sp:
        out["observed_spread_bps_mean"] = round(mean(sp), 2)
        out["observed_spread_bps_max"] = max(sp)
    obs_net = [t["net_bps_observed_spread"] for t in trades if "net_bps_observed_spread" in t]
    out["mean_net_bps_observed_spread"] = round(mean(obs_net), 2) if obs_net else None
    out["total_return_pct_on_book"] = round((math.prod(1 + r for r in rets) - 1) * 100, 3) if rets else None
    out["status"] = _verdict(out)
    return out


MIN_TRADES, MIN_DAYS = 30, 15


def _verdict(s: dict) -> str:
    if s["trades_settled"] < MIN_TRADES or s["distinct_trade_days"] < MIN_DAYS:
        return (f"INCONCLUSIVE - {s['trades_settled']}/{MIN_TRADES} trades and {s['distinct_trade_days']}/{MIN_DAYS} trade days so far; "
                "do not read anything into the numbers yet")
    ok = (s["mean_net_bps_assumed_cost"] or 0) > 0 and (s["day_level_tstat"] or 0) >= 1.645
    return "SUPPORTED (pre-registered criterion met)" if ok else "NOT SUPPORTED (pre-registered criterion not met)"
