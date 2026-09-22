"""SYNTHETIC test worlds. Used only by unit tests to exercise the engine's logic with known answers.
Nothing here is market data and nothing here is ever written to a report or shown in the demo."""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from rift24 import calendar as cal
from rift24.data import BAR_MS, Bar, DailyBar, DataAdapter, DataStatus

SYMBOL, TICKER = "RNVDAUSDT", "NVDA"
BASE_PX = 100.0
CASH_CLOSE = 100.0


@dataclass
class Spec:
    """One session: bps moves. void = decision vs base; hold = entry->exit; cash = cash open vs prior cash close."""

    date: dt.date
    void: float
    cash: float
    hold: float = 0.0


class MemoryAdapter(DataAdapter):
    mode = "FIXTURE"

    def __init__(self):
        super().__init__()
        self.bars: dict[str, dict[int, Bar]] = {}
        self.cashd: dict[str, dict[dt.date, DailyBar]] = {}

    def rtoken_bar(self, symbol, ts_ms):
        return self.bars.get(symbol, {}).get(ts_ms)

    def cash_day(self, ticker, date):
        return self.cashd.get(ticker, {}).get(date)

    def last_bar_end_ms(self, symbol):
        b = self.bars.get(symbol)
        return (max(b) + BAR_MS) if b else None

    def status(self):
        return DataStatus("FIXTURE", "SYNTHETIC TEST WORLD", "synthetic", None, None)


def _put(a: MemoryAdapter, symbol: str, start_ms: int, o: float, c: float):
    a.bars.setdefault(symbol, {})[start_ms] = Bar(start_ms, o, max(o, c), min(o, c), c)


def add_session(a: MemoryAdapter, spec: Spec, symbol: str = SYMBOL, ticker: str = TICKER, base: float = BASE_PX):
    s = cal.make_session(spec.date)
    close_ms, open_ms = int(s.close_utc.timestamp() * 1000), int(s.open_utc.timestamp() * 1000)
    dec_px = base * (1 + spec.void / 1e4)
    entry_px = dec_px
    exit_px = entry_px * (1 + spec.hold / 1e4)
    _put(a, symbol, close_ms - BAR_MS, base, base)  # bar ending at prior cash close
    _put(a, symbol, open_ms - 2 * BAR_MS, dec_px, dec_px)  # bar ending at the decision time (open - 15m)
    _put(a, symbol, open_ms - BAR_MS, entry_px, exit_px)  # entry bar (starts at decision time) = last pre-open bar; exit = its close
    _put(a, symbol, open_ms, exit_px, exit_px)  # first regular-session bar (only used by post-open-exit variants)
    d = a.cashd.setdefault(ticker, {})
    d.setdefault(s.prev_date, DailyBar(s.prev_date, CASH_CLOSE, CASH_CLOSE))  # never clobber that day's own open
    d[spec.date] = DailyBar(spec.date, CASH_CLOSE * (1 + spec.cash / 1e4), CASH_CLOSE)


def world(specs: list[Spec], symbol: str = SYMBOL, ticker: str = TICKER) -> MemoryAdapter:
    a = MemoryAdapter()
    for sp in specs:
        add_session(a, sp, symbol, ticker)
    return a


def weekdays(start: dt.date, n: int) -> list[dt.date]:
    out, d = [], start
    while len(out) < n:
        if cal.is_trading_day(d):
            out.append(d)
        d += dt.timedelta(days=1)
    return out
