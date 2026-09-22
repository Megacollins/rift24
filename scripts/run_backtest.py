"""python -m scripts.run_backtest [--live] [--no-sens] [--total-days 90] [--oos-days 30]

Runs the full walk-forward backtest, prints the metrics table, and writes reports/backtest.json
(and reports/thesis.md once the thesis renderer is available). Fully offline by default.
"""
from __future__ import annotations

import argparse
import sys

from rift24 import report
from rift24.backtest import Window, exploratory_study, last_complete_session, run_backtest, sensitivities
from rift24.data import get_adapter
from rift24.residual import Config


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true", help="try Bitget public API first; falls back to fixtures LOUDLY")
    ap.add_argument("--live-days", type=int, default=30, help="with --live: how many days of public data to pull")
    ap.add_argument("--no-sens", action="store_true")
    ap.add_argument("--total-days", type=int, default=90)
    ap.add_argument("--oos-days", type=int, default=30)
    ap.add_argument("--reports-dir", default=str(report.REPORTS))
    args = ap.parse_args(argv)

    adapter, notice = get_adapter(prefer_live=args.live, live_days=args.live_days)
    print(f"DATA: {adapter.status().label}")
    print(f"      {adapter.status().provenance}")
    if notice:
        print(f"      !! {notice}")
    cfg = Config()
    total, oos = args.total_days, args.oos_days
    if adapter.mode == "LIVE":
        total = min(total, max(args.live_days - 10, 6))
        oos = max(2, min(oos, total // 3))
        print(f"NOTE: a live pull of {args.live_days} days cannot support the official 60/30 backtest. Using a {total}/{oos}-day window "
              "for a smoke test; the reported results come from the frozen fixtures (run without --live).")
    window = Window.ending(last_complete_session(adapter), total, oos)

    res = run_backtest(adapter, cfg, window)
    sens = None if args.no_sens else sensitivities(adapter, cfg, window)
    study = None if args.no_sens else exploratory_study(adapter, cfg, window)
    print(report.print_table(res, sens, study))

    from pathlib import Path
    out = Path(args.reports_dir)
    if adapter.mode == "LIVE" and args.reports_dir == str(report.REPORTS):
        out = Path(report.REPORTS) / "live_smoke"  # official reports stay fixture-derived
    report.write_json(report.backtest_json(res, adapter, sens or [], notice, study), out / "backtest.json")
    print(f"\nwrote {out / 'backtest.json'}")
    if hasattr(report, "write_thesis"):
        report.write_thesis(res, adapter, sens or [], study, out / "thesis.md")
        print(f"wrote {out / 'thesis.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
