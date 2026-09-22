"""Performance metrics. Pure functions, no I/O.

Conventions (repeated verbatim in reports/backtest.json and the README):

  * Return series  : one return per U.S. trading session (by session *open* date), as a fraction of the
                     simulated book. Sessions on which a strategy did not trade contribute exactly 0.0 -
                     stand-down days are inside the denominator, not dropped.
  * Sharpe         : mean / sample-std (ddof=1) of that series x sqrt(252). Risk-free rate = 0.
  * Sortino        : mean / downside-deviation x sqrt(252); downside deviation = sqrt(mean(min(r, 0)^2))
                     over ALL sessions (target = 0). Undefined (None) if there is no negative return.
  * Max drawdown   : peak-to-trough decline of the compounded equity curve, as a positive fraction.
  * Turnover       : total traded notional (entry + exit legs) / book, and the same per session.
  * Rolling Sharpe : trailing 30 CALENDAR days of session returns, needs >= 15 sessions in the window.
  * OOS/IS ratio   : OOS Sharpe / IS Sharpe; alert when OOS < 0.5 x IS. Not meaningful if IS Sharpe <= 0.
"""
from __future__ import annotations

import datetime as dt
import math
from statistics import mean, median, stdev

ANN = 252
ROLL_DAYS = 30
ROLL_MIN_OBS = 15


def sharpe(rets: list[float]) -> float | None:
    if len(rets) < 2:
        return None
    sd = stdev(rets)
    if sd == 0 or not math.isfinite(sd):
        return None
    return mean(rets) / sd * math.sqrt(ANN)


def sharpe_se(rets: list[float]) -> float | None:
    """Lo (2002) large-sample standard error of the annualised Sharpe ratio (iid approximation)."""
    sr = sharpe(rets)
    if sr is None:
        return None
    n = len(rets)
    sr_d = sr / math.sqrt(ANN)
    return math.sqrt((1 + 0.5 * sr_d**2) / n) * math.sqrt(ANN)


def sortino(rets: list[float]) -> float | None:
    if len(rets) < 2 or not any(r < 0 for r in rets):
        return None
    dd = math.sqrt(sum(min(r, 0.0) ** 2 for r in rets) / len(rets))
    return mean(rets) / dd * math.sqrt(ANN) if dd > 0 else None


def equity_curve(rets: list[float]) -> list[float]:
    eq, out = 1.0, []
    for r in rets:
        eq *= 1 + r
        out.append(eq)
    return out


def max_drawdown(rets: list[float]) -> float:
    peak, mdd = 1.0, 0.0
    for e in equity_curve(rets):
        peak = max(peak, e)
        mdd = max(mdd, 1 - e / peak)
    return mdd


def total_return(rets: list[float]) -> float:
    e = equity_curve(rets)
    return (e[-1] - 1.0) if e else 0.0


def rolling_sharpe(dates: list[dt.date], rets: list[float]) -> list[dict]:
    out = []
    for i, d in enumerate(dates):
        lo = d - dt.timedelta(days=ROLL_DAYS)
        w = [r for dd, r in zip(dates[: i + 1], rets[: i + 1]) if dd > lo]
        s = sharpe(w) if len(w) >= ROLL_MIN_OBS else None
        out.append({"date": d.isoformat(), "sharpe": None if s is None else round(s, 3), "n": len(w)})
    return out


def rolling_summary(series: list[dict]) -> dict:
    vals = [x["sharpe"] for x in series if x["sharpe"] is not None]
    if not vals:
        return {"windows": 0, "min": None, "median": None, "max": None, "last": None, "share_positive": None}
    return {
        "windows": len(vals),
        "min": round(min(vals), 3),
        "median": round(median(vals), 3),
        "max": round(max(vals), 3),
        "last": vals[-1],
        "share_positive": round(sum(v > 0 for v in vals) / len(vals), 3),
    }


def oos_is(is_sr: float | None, oos_sr: float | None) -> dict:
    if is_sr is None or oos_sr is None:
        return {"ratio": None, "alert": None, "note": "Sharpe undefined in at least one period (no variance / no trades)"}
    if is_sr <= 0:
        return {"ratio": None, "alert": None,
                "note": f"IS Sharpe {is_sr:.2f} <= 0: ratio not meaningful; read OOS Sharpe directly"}
    ratio = oos_sr / is_sr
    alert = oos_sr < 0.5 * is_sr
    return {"ratio": round(ratio, 3), "alert": alert,
            "note": "ALERT: OOS Sharpe < 0.5 x IS Sharpe" if alert else "OOS Sharpe >= 0.5 x IS Sharpe"}


def _r(x, nd=3):
    return None if x is None else round(x, nd)


def summarize(dates: list[dt.date], rets: list[float], trade_nets_bps: list[float], traded_notional: float,
              sessions_total: int) -> dict:
    """One period, one strategy. `traded_notional` is in units of book (entry+exit legs)."""
    n = len(rets)
    wins = sum(1 for x in trade_nets_bps if x > 0)
    tm = mean(trade_nets_bps) if trade_nets_bps else None
    t_stat = None
    if len(trade_nets_bps) >= 3 and stdev(trade_nets_bps) > 0:
        t_stat = tm / (stdev(trade_nets_bps) / math.sqrt(len(trade_nets_bps)))
    return {
        "sessions": n,
        "session_days_traded": sum(1 for r in rets if r != 0.0),
        "trade_count": len(trade_nets_bps),
        "total_return_pct": _r(total_return(rets) * 100, 3),
        "sharpe": _r(sharpe(rets), 3),
        "sharpe_se": _r(sharpe_se(rets), 3),
        "sortino": _r(sortino(rets), 3),
        "max_drawdown_pct": _r(max_drawdown(rets) * 100, 3),
        "turnover_x_book": _r(traded_notional, 3),
        "turnover_per_session": _r(traded_notional / n, 4) if n else None,
        "win_rate": _r(wins / len(trade_nets_bps), 4) if trade_nets_bps else None,
        "mean_trade_net_bps": _r(tm, 2),
        "trade_net_bps_tstat": _r(t_stat, 2),
        "stand_down_rate_sessions": _r(1 - sum(1 for r in rets if r != 0.0) / n, 4) if n else None,
        "rolling_30d_sharpe": rolling_summary(rolling_sharpe(dates, rets)),
    }
