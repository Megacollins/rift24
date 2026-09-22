import json
import math

import pytest

from rift24 import report
from rift24.backtest import Window, exploratory_study, last_complete_session, run_backtest
from rift24.data import FixtureAdapter
from rift24.residual import Config

REQUIRED = ["trade_count", "sharpe", "sortino", "max_drawdown_pct", "turnover_x_book", "win_rate", "rolling_30d_sharpe",
            "stand_down_rate_sessions", "total_return_pct"]


@pytest.fixture(scope="module")
def world():
    a = FixtureAdapter()
    if not a.has_data():
        pytest.skip("fixtures not present")
    w = Window.ending(last_complete_session(a), 90, 30)
    res = run_backtest(a, Config(), w)
    return a, w, res


def _no_nan(o, path="$"):
    if isinstance(o, float):
        assert math.isfinite(o), f"non-finite number at {path}"
    elif isinstance(o, dict):
        for k, v in o.items():
            _no_nan(v, f"{path}.{k}")
    elif isinstance(o, list):
        for i, v in enumerate(o):
            _no_nan(v, f"{path}[{i}]")


def test_backtest_covers_60_plus_days_with_30_plus_oos_days_and_multiple_weekends(world):
    _, w, res = world
    assert w.total_days >= 60 and w.oos_days >= 30 and w.is_days >= 60
    assert res.diagnostics["weekend_sessions_scored"] >= 8
    assert res.diagnostics["symbol_sessions_scored"] > 500


def test_json_is_reproducible_byte_for_byte(world):
    a, w, res = world
    j1 = json.dumps(report.backtest_json(res, a, [], None), sort_keys=True)
    res2 = run_backtest(a, Config(), w)
    j2 = json.dumps(report.backtest_json(res2, a, [], None), sort_keys=True)
    assert j1 == j2


def test_json_contains_every_required_metric_for_every_strategy_and_period_and_is_finite(world):
    a, w, res = world
    doc = report.backtest_json(res, a, [], None)
    _no_nan(doc)
    for s in ("A_DO_NOTHING", "B_ALWAYS_FADE", "RIFT24"):
        for period in ("FULL", "IN_SAMPLE", "OUT_OF_SAMPLE"):
            for k in REQUIRED:
                assert k in doc["strategies"][s][period], (s, period, k)
        assert "ratio" in doc["strategies"][s]["OOS_IS"] and "note" in doc["strategies"][s]["OOS_IS"]
    assert doc["paper_backtest_only"].startswith("Paper/backtest only")
    assert doc["data"]["no_synthetic_data"] is True and doc["data"]["spread"].startswith("ESTIMATED")
    assert doc["methodology"]["split"]["in_sample"]["label"] == "IN-SAMPLE"
    assert doc["methodology"]["split"]["out_of_sample"]["label"] == "OUT-OF-SAMPLE"


def test_undefined_metrics_are_null_never_zero(world):
    _, _, res = world
    a = res.metrics["A_DO_NOTHING"]["FULL"]
    assert a["sharpe"] is None and a["sortino"] is None and a["win_rate"] is None  # no variance / no trades
    assert a["total_return_pct"] == 0.0 and a["stand_down_rate_sessions"] == 1.0


def test_do_nothing_is_flat_and_always_fade_has_costs_baked_in(world):
    _, _, res = world
    assert all(r == 0.0 for _, r in res.daily["A_DO_NOTHING"])
    tr = res.trades["B_ALWAYS_FADE"]
    assert tr and all(abs(t.net_bps - (t.gross_bps - t.cost_bps)) < 1e-9 for t in tr)
    assert all(t.cost_bps >= 12.0 - 1e-9 for t in tr) and all(t.weight <= 0.2 + 1e-12 for t in tr)


def test_rift24_trades_are_always_a_subset_of_always_fade(world):
    _, _, res = world
    b = {(t.symbol, t.open_date) for t in res.trades["B_ALWAYS_FADE"]}
    assert {(t.symbol, t.open_date) for t in res.trades["RIFT24"]} <= b


def test_every_scored_trade_window_ends_before_the_cash_open(world):
    from rift24 import calendar as cal
    _, _, res = world
    for r in res.rows:
        assert r.obs.outcome.known_utc <= cal.session_open(r.obs.view.open_date)
        assert r.obs.view.decision_utc < r.obs.outcome.known_utc


def test_thesis_is_generated_from_numbers_and_never_claims_an_edge_it_does_not_have(world, tmp_path):
    a, w, res = world
    study = exploratory_study(a, Config(), w)
    out = tmp_path / "thesis.md"
    report.write_thesis(res, a, [], study, out)
    t = out.read_text(encoding="utf-8")
    assert "Paper/backtest only" in t and "POST-HOC" in t and "not an edge" in t
    assert "OBSERVED" in t and "ESTIMATED" in t and "TARGETED" in t
    assert "NaN" not in t and "None" not in t


def test_demo_state_separates_pre_decision_from_revealed_fields(world):
    from rift24.demo import build_demo_state
    a, w, res = world
    study = exploratory_study(a, Config(), w)
    st = build_demo_state(a, Config(), w, res, [], study, None)
    _no_nan(st)
    post_words = ("realized", "cash_open", "cash_prev_close", "exit_px", "entry_px", "hold_return", "net_bps", "gross_bps", "verdict")
    for t in st["desk"]["times"]:
        for r in t["rows"]:
            blob = json.dumps(r["pre"])
            assert not any(w_ in blob for w_ in post_words), "post-decision field leaked into `pre`"
            assert set(r) == {"pre", "reveal"}
    # replay paths: `pre` ends at or before the decision time, `reveal` starts after it
    dec = st["replay"]["decision_utc"]
    import datetime as dt
    dms = dt.datetime.fromisoformat(dec.replace("Z", "+00:00")).timestamp() * 1000
    for path in st["replay"]["pre"]["paths"].values():
        assert all(p[0] <= dms for p in path)
    for path in st["replay"]["reveal"]["paths"].values():
        assert all(p[0] > dms for p in path)
    assert st["meta"]["paper_only"].startswith("Paper/backtest only")
    assert all(e["provenance"] in ("SOURCED", "ILLUSTRATIVE") for e in st["events"])
