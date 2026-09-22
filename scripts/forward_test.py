"""python -m scripts.forward_test {freeze|snapshot|settle|report} [--dry-run] [--as-of 2026-09-22T08:02:00Z]

Pre-registered forward test of the 04:00 ET residual rule (see data/forward/PREREGISTRATION.md).
  freeze    once: fit + stamp the model from the fixtures
  snapshot  run at ~04:02 ET (Mon-Fri): logs the frozen rule's stance for all 10 rTokens + OBSERVED spreads
  settle    run after 09:35 ET: logs the realised cash gap and the rule's net P&L
  report    prints the running summary and the pre-registered verdict (INCONCLUSIVE until 30 trades / 15 days)
Paper only. Read-only public market data; no keys; no orders.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys

from rift24 import calendar as cal
from rift24 import forward as F
from rift24.data import FixtureAdapter, LiveAdapter, bitget_reachable


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["freeze", "snapshot", "settle", "report"])
    ap.add_argument("--dry-run", action="store_true", help="compute and print, write nothing")
    ap.add_argument("--as-of", help="ISO UTC time to pretend it is (validation only; implies --dry-run)")
    ap.add_argument("--live-days", type=int, default=6)
    a = ap.parse_args(argv)

    if a.cmd == "freeze":
        doc = F.freeze_model(FixtureAdapter())
        print(f"frozen: beta={doc['beta']:.3f} se={doc['se']:.3f} n={doc['n']} through {doc['fitted_through_session']} sha256={doc['sha256'][:16]}...")
        return 0
    if a.cmd == "report":
        print(json.dumps(F.summarize(), indent=2))
        return 0

    if not bitget_reachable():
        print("LIVE DATA UNAVAILABLE - nothing logged (a forward test cannot run on fixtures).")
        return 1
    now = dt.datetime.fromisoformat(a.as_of.replace("Z", "+00:00")) if a.as_of else dt.datetime.now(dt.timezone.utc)
    dry = a.dry_run or bool(a.as_of)
    ad = LiveAdapter(days=a.live_days)
    try:
        if a.cmd == "snapshot":
            model, meta = F.load_model()
            rows = F.take_snapshot(ad, now, model, meta, dry_run=dry)
            print(f"{'DRY RUN - ' if dry else ''}{len(rows)} new rows for the session opening {F.next_session(now).open_date}")
            for r in rows:
                print(f"  {r['rtoken']:<7} void {r.get('void_bps', float('nan')):>+8.1f} bps  {r['stance']:<10} "
                      f"gate {r.get('binding_gate')}  obs.spread {r.get('spread_bps_observed')}")
        else:
            rows = F.settle(ad, now, dry_run=dry)
            print(f"{'DRY RUN - ' if dry else ''}settled {len(rows)} rows")
            for r in rows:
                print(f"  {r['rtoken']:<7} cash gap {r['realized_cash_gap_bps']:>+8.1f} bps  {r['stance']:<10} net {r.get('net_bps_assumed_cost')}")
    except F.ForwardError as e:
        print(f"not run: {e}")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
