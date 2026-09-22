"""Snapshot real market data into data/fixtures/ (best effort, stdlib only).

Sources (public, no API key):
  * Bitget spot REST  /api/v2/spot/market/history-candles  rToken 15-minute OHLC
  * Bitget spot REST  /api/v2/spot/market/tickers           best bid/ask snapshot (fetch time only)
  * Yahoo Finance chart API, interval=1d                    underlying regular-session open/close

Everything written is OBSERVED data as of the fetch timestamp recorded in MANIFEST.json.
Nothing is synthesised. If Bitget is unreachable the script says so and leaves existing
fixtures untouched.

Usage:  python -m scripts.fetch_if_possible [--start 2026-06-01] [--symbols RNVDAUSDT,...] [--out DIR]
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import sys
from pathlib import Path

from rift24.data import (
    FALLBACK_NOTICE,
    FIXTURES,
    bitget_reachable,
    fetch_bitget_candles,
    fetch_bitget_ticker,
    fetch_yahoo_daily,
    load_allowlist,
)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2026-06-01")
    ap.add_argument("--symbols", default="")
    ap.add_argument("--out", default=str(FIXTURES))
    args = ap.parse_args()
    out = Path(args.out)

    ins, _ = load_allowlist()
    if args.symbols:
        keep = set(args.symbols.split(","))
        ins = [i for i in ins if i.bitget_symbol in keep]

    if not bitget_reachable():
        print(FALLBACK_NOTICE, "- existing fixtures left untouched.")
        return 0

    start = dt.date.fromisoformat(args.start)
    start_ms = int(dt.datetime.combine(start, dt.time(), tzinfo=dt.timezone.utc).timestamp() * 1000)
    now = dt.datetime.now(dt.timezone.utc)
    end_ms = int(now.timestamp() * 1000)

    (out / "rtoken_15m").mkdir(parents=True, exist_ok=True)
    (out / "cash_daily").mkdir(parents=True, exist_ok=True)
    manifest: dict = {"fetched_at_utc": now.isoformat(), "granularity": "15min", "rtoken": {}, "cash": {}, "spread_snapshot": {}}

    for i in ins:
        print(f"[rToken] {i.bitget_symbol} ...", end=" ", flush=True)
        rows = fetch_bitget_candles(i.bitget_symbol, start_ms, end_ms)
        with open(out / "rtoken_15m" / f"{i.bitget_symbol}.csv", "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["ts_ms", "open", "high", "low", "close"])
            w.writerows(rows)
        iso = lambda ms: dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc).isoformat()  # noqa: E731
        manifest["rtoken"][i.bitget_symbol] = {
            "rows": len(rows), "first": iso(rows[0][0]) if rows else None, "last": iso(rows[-1][0]) if rows else None,
        }
        print(f"{len(rows)} bars", flush=True)

        print(f"[cash]   {i.underlying} ...", end=" ", flush=True)
        daily, events = fetch_yahoo_daily(i.underlying, start - dt.timedelta(days=10), now.date())
        with open(out / "cash_daily" / f"{i.underlying}.csv", "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["date", "open", "high", "low", "close"])
            w.writerows(daily)
        manifest["cash"][i.underlying] = {"rows": len(daily), "corporate_events": events}
        print(f"{len(daily)} days", flush=True)

    # The only observable spread is a snapshot at fetch time; historical spreads are not public.
    for i in ins:
        try:
            manifest["spread_snapshot"][i.bitget_symbol] = fetch_bitget_ticker(i.bitget_symbol)
        except Exception as e:  # noqa: BLE001
            manifest["spread_snapshot"][i.bitget_symbol] = {"error": str(e)}

    manifest["sources"] = {
        "rtoken": "Bitget spot REST /api/v2/spot/market/history-candles (public, no key)",
        "cash": "Yahoo Finance chart API interval=1d (raw, unadjusted open/close)",
        "spread": "Bitget spot REST /api/v2/spot/market/tickers bidPr/askPr - single snapshot at fetch time, NOT historical",
    }
    (out / "MANIFEST.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print("wrote", out / "MANIFEST.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
