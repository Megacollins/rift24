"""Data access. One interface (DataAdapter), two implementations:

  FixtureAdapter - frozen snapshot of OBSERVED public data in data/fixtures/ (offline, default)
  LiveAdapter    - pulls the same series from Bitget's public REST market-data endpoints
                   (the same data bitget-mcp-server exposes to agents) and Yahoo's daily bars.

Nothing here fabricates prices. If live data cannot be reached, get_adapter() says so out loud
and returns the fixture adapter.
"""
from __future__ import annotations

import csv
import datetime as dt
import json
import time
import urllib.parse
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
FIXTURES = DATA_DIR / "fixtures"

BAR_MS = 15 * 60 * 1000
SPREAD_FALLBACK_BPS = 4.0
STALE_MINUTES = 15

BITGET = "https://api.bitget.com"
_UA = {"User-Agent": "Mozilla/5.0 (rift24-research)"}

FALLBACK_NOTICE = "LIVE DATA UNAVAILABLE\nFALLING BACK TO FIXTURE DATA"


@dataclass(frozen=True)
class Instrument:
    rtoken: str
    bitget_symbol: str
    underlying: str
    kind: str
    listing_date: dt.date
    aliases: tuple[str, ...]


@dataclass(frozen=True)
class Bar:
    ts_ms: int  # bar OPEN time, UTC epoch ms
    open: float
    high: float
    low: float
    close: float

    @property
    def end_ms(self) -> int:
        return self.ts_ms + BAR_MS


@dataclass(frozen=True)
class DailyBar:
    date: dt.date
    open: float  # regular-session opening print
    close: float  # regular-session close


@dataclass(frozen=True)
class SpreadQuote:
    bps: float
    source: str  # 'OBSERVED' | 'ESTIMATED'
    note: str


@dataclass(frozen=True)
class DataStatus:
    mode: str  # 'FIXTURE' | 'LIVE'
    label: str  # human-readable, shown in the UI
    provenance: str  # 'OBSERVED' snapshot description
    fetched_at_utc: str | None
    last_bar_end_utc: str | None
    notice: str | None = None


def load_allowlist(path: Path | None = None) -> tuple[list[Instrument], dict]:
    raw = json.loads((path or DATA_DIR / "allowlist.json").read_text(encoding="utf-8"))
    ins = [
        Instrument(
            rtoken=i["rtoken"],
            bitget_symbol=i["bitget_symbol"],
            underlying=i["underlying"],
            kind=i["kind"],
            listing_date=dt.date.fromisoformat(i["listing_date"]),
            aliases=tuple(a.lower() for a in i["aliases"]),
        )
        for i in raw["instruments"]
    ]
    return ins, raw["_meta"]


class DataAdapter(ABC):
    mode: str

    def __init__(self, allowlist_path: Path | None = None):
        self._instruments, self.allowlist_meta = load_allowlist(allowlist_path)

    def instruments(self) -> list[Instrument]:
        return list(self._instruments)

    @abstractmethod
    def rtoken_bar(self, symbol: str, ts_ms: int) -> Bar | None: ...

    @abstractmethod
    def cash_day(self, ticker: str, date: dt.date) -> DailyBar | None: ...

    @abstractmethod
    def last_bar_end_ms(self, symbol: str) -> int | None: ...

    @abstractmethod
    def status(self) -> DataStatus: ...

    def spread(self, symbol: str, ts_ms: int) -> SpreadQuote:
        """Historical bid/ask does not exist in public data, so backtests use the labelled fallback."""
        return SpreadQuote(SPREAD_FALLBACK_BPS, "ESTIMATED", "no observed spread at this time; +4 bps fallback haircut")

    def observed_spread_snapshot(self) -> dict[str, dict]:
        return {}


# ----------------------------------------------------------------------------- fixtures
class FixtureAdapter(DataAdapter):
    mode = "FIXTURE"

    def __init__(self, fixtures_dir: Path | None = None, allowlist_path: Path | None = None):
        super().__init__(allowlist_path)
        self.dir = fixtures_dir or FIXTURES
        self._bars: dict[str, dict[int, Bar]] = {}
        self._cash: dict[str, dict[dt.date, DailyBar]] = {}
        mf = self.dir / "MANIFEST.json"
        self.manifest = json.loads(mf.read_text(encoding="utf-8")) if mf.exists() else {}
        for ins in self._instruments:
            p = self.dir / "rtoken_15m" / f"{ins.bitget_symbol}.csv"
            if p.exists():
                with open(p, newline="") as f:
                    rd = csv.DictReader(f)
                    self._bars[ins.bitget_symbol] = {
                        int(r["ts_ms"]): Bar(int(r["ts_ms"]), float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"]))
                        for r in rd
                    }
            p = self.dir / "cash_daily" / f"{ins.underlying}.csv"
            if p.exists():
                with open(p, newline="") as f:
                    rd = csv.DictReader(f)
                    self._cash[ins.underlying] = {
                        dt.date.fromisoformat(r["date"]): DailyBar(dt.date.fromisoformat(r["date"]), float(r["open"]), float(r["close"]))
                        for r in rd
                    }

    def rtoken_bar(self, symbol, ts_ms):
        return self._bars.get(symbol, {}).get(ts_ms)

    def cash_day(self, ticker, date):
        return self._cash.get(ticker, {}).get(date)

    def last_bar_end_ms(self, symbol):
        b = self._bars.get(symbol)
        return (max(b) + BAR_MS) if b else None

    def has_data(self) -> bool:
        return bool(self._bars) and bool(self._cash)

    def observed_spread_snapshot(self):
        return self.manifest.get("spread_snapshot", {})

    def status(self) -> DataStatus:
        last = max((self.last_bar_end_ms(i.bitget_symbol) or 0) for i in self._instruments)
        return DataStatus(
            mode="FIXTURE",
            label="FIXTURE SNAPSHOT (offline) - observed data frozen at fetch time",
            provenance=(
                "OBSERVED: Bitget spot 15m candles + Yahoo Finance daily open/close, "
                f"fetched {self.manifest.get('fetched_at_utc', 'unknown')}"
            ),
            fetched_at_utc=self.manifest.get("fetched_at_utc"),
            last_bar_end_utc=dt.datetime.fromtimestamp(last / 1000, dt.timezone.utc).isoformat() if last else None,
        )


# ----------------------------------------------------------------------------- live
def http_json(url: str, retries: int = 4):
    last = None
    for i in range(retries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=_UA), timeout=25) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:  # noqa: BLE001 - network best effort
            last = e
            time.sleep(0.8 * (i + 1))
    raise RuntimeError(f"GET failed after {retries} tries: {url} ({last})")


def bitget_reachable() -> bool:
    try:
        return http_json(f"{BITGET}/api/v2/public/time", retries=2).get("code") == "00000"
    except Exception:  # noqa: BLE001
        return False


def fetch_bitget_candles(symbol: str, start_ms: int, end_ms: int, granularity: str = "15min"):
    """Return [(ts_ms, o, h, l, c)] ascending. Pages backwards; Bitget returns candles ending before endTime."""
    rows: dict[int, tuple[str, str, str, str]] = {}
    cursor = end_ms
    while cursor > start_ms:
        q = urllib.parse.urlencode({"symbol": symbol, "granularity": granularity, "endTime": cursor, "limit": 200})
        d = http_json(f"{BITGET}/api/v2/spot/market/history-candles?{q}")
        if d.get("code") != "00000":
            raise RuntimeError(f"{symbol}: API said {d.get('code')} {d.get('msg')}")
        data = d.get("data") or []
        if not data:
            break
        for c in data:
            rows[int(c[0])] = (c[1], c[2], c[3], c[4])
        oldest = min(int(c[0]) for c in data)
        if oldest >= cursor:
            break
        cursor = oldest
        time.sleep(0.12)
    return [(ts, *rows[ts]) for ts in sorted(rows) if ts >= start_ms]


def fetch_yahoo_daily(ticker: str, start: dt.date, end: dt.date):
    """Return ([(date_iso, o, h, l, c)], corporate_events). Raw (unadjusted) regular-session bars."""
    p1 = int(dt.datetime.combine(start, dt.time(), tzinfo=dt.timezone.utc).timestamp())
    p2 = int(dt.datetime.combine(end + dt.timedelta(days=2), dt.time(), tzinfo=dt.timezone.utc).timestamp())
    url = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
        f"?interval=1d&period1={p1}&period2={p2}&events=div%2Csplits"
    )
    res = http_json(url)["chart"]["result"][0]
    q = res["indicators"]["quote"][0]
    off = res["meta"]["gmtoffset"]
    out = []
    for i, ts in enumerate(res["timestamp"]):
        if q["open"][i] is None or q["close"][i] is None:
            continue
        day = dt.datetime.fromtimestamp(ts + off, dt.timezone.utc).date()
        out.append((day.isoformat(), q["open"][i], q["high"][i], q["low"][i], q["close"][i]))
    return out, {k: list(v.values()) for k, v in res.get("events", {}).items()}


def fetch_bitget_ticker(symbol: str) -> dict:
    t = http_json(f"{BITGET}/api/v2/spot/market/tickers?symbol={symbol}", retries=2)["data"][0]
    bid, ask = float(t["bidPr"]), float(t["askPr"])
    return {"ts_ms": int(t["ts"]), "bid": bid, "ask": ask, "spread_bps": round((ask - bid) / ((bid + ask) / 2) * 1e4, 3)}


class LiveAdapter(DataAdapter):
    """Pulls a rolling window from public endpoints into memory. Requires network, no API key."""

    mode = "LIVE"

    def __init__(self, days: int = 14, allowlist_path: Path | None = None):
        super().__init__(allowlist_path)
        now = dt.datetime.now(dt.timezone.utc)
        self.fetched_at = now
        start_ms = int((now - dt.timedelta(days=days)).timestamp() * 1000)
        end_ms = int(now.timestamp() * 1000)
        self._bars, self._cash, self._spread = {}, {}, {}
        for ins in self._instruments:
            rows = fetch_bitget_candles(ins.bitget_symbol, start_ms, end_ms)
            self._bars[ins.bitget_symbol] = {r[0]: Bar(r[0], *map(float, r[1:])) for r in rows}
            daily, _ = fetch_yahoo_daily(ins.underlying, (now - dt.timedelta(days=days + 10)).date(), now.date())
            self._cash[ins.underlying] = {
                dt.date.fromisoformat(r[0]): DailyBar(dt.date.fromisoformat(r[0]), float(r[1]), float(r[4])) for r in daily
            }
            try:
                self._spread[ins.bitget_symbol] = fetch_bitget_ticker(ins.bitget_symbol)
            except Exception:  # noqa: BLE001
                pass

    def rtoken_bar(self, symbol, ts_ms):
        return self._bars.get(symbol, {}).get(ts_ms)

    def cash_day(self, ticker, date):
        return self._cash.get(ticker, {}).get(date)

    def last_bar_end_ms(self, symbol):
        b = self._bars.get(symbol)
        return (max(b) + BAR_MS) if b else None

    def observed_spread_snapshot(self):
        return dict(self._spread)

    def spread(self, symbol, ts_ms):
        s = self._spread.get(symbol)
        if s and abs(s["ts_ms"] - ts_ms) <= STALE_MINUTES * 60_000:
            return SpreadQuote(s["spread_bps"], "OBSERVED", "best bid/ask from Bitget ticker snapshot")
        return super().spread(symbol, ts_ms)

    def status(self) -> DataStatus:
        last = max((self.last_bar_end_ms(i.bitget_symbol) or 0) for i in self._instruments)
        return DataStatus(
            mode="LIVE",
            label="LIVE - Bitget public market data + Yahoo daily bars",
            provenance=f"OBSERVED live pull at {self.fetched_at.isoformat()}",
            fetched_at_utc=self.fetched_at.isoformat(),
            last_bar_end_utc=dt.datetime.fromtimestamp(last / 1000, dt.timezone.utc).isoformat() if last else None,
        )


def get_adapter(prefer_live: bool = False, fixtures_dir: Path | None = None, live_days: int = 14) -> tuple[DataAdapter, str | None]:
    """Return (adapter, notice). `notice` is non-None whenever we degraded to fixtures."""
    if prefer_live:
        try:
            if not bitget_reachable():
                raise RuntimeError("Bitget public API not reachable")
            return LiveAdapter(days=live_days), None
        except Exception as e:  # noqa: BLE001
            print(FALLBACK_NOTICE + f"  ({e})")
            return FixtureAdapter(fixtures_dir), FALLBACK_NOTICE.replace("\n", " - ")
    return FixtureAdapter(fixtures_dir), None
