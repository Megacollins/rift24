"""python -m scripts.build_demo_state

Pre-renders data/demo_state.json (+ app/demo_state.js for file:// use) from the Python engine, offline.
"""
from __future__ import annotations

import sys

from rift24.backtest import Window, exploratory_study, last_complete_session, run_backtest, sensitivities
from rift24.data import get_adapter
from rift24.demo import build_demo_state, write_state
from rift24.residual import Config


def main() -> int:
    adapter, notice = get_adapter()
    cfg = Config()
    window = Window.ending(last_complete_session(adapter), 90, 30)
    res = run_backtest(adapter, cfg, window)
    sens = sensitivities(adapter, cfg, window)
    study = exploratory_study(adapter, cfg, window)
    state = build_demo_state(adapter, cfg, window, res, sens, study, notice)
    for p in write_state(state):
        print(f"wrote {p} ({p.stat().st_size / 1024:.0f} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
