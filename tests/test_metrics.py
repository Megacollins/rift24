import datetime as dt
import math

import pytest

from rift24 import metrics as M


def test_sharpe_hand_computed():
    r = [0.01, -0.01, 0.02, 0.0]
    mean = 0.005
    sd = math.sqrt(sum((x - mean) ** 2 for x in r) / 3)
    assert M.sharpe(r) == pytest.approx(mean / sd * math.sqrt(252))


def test_sharpe_undefined_without_variance():
    assert M.sharpe([0.0] * 10) is None
    assert M.sharpe([0.01]) is None


def test_stand_down_zeros_stay_in_the_denominator():
    traded = [0.01, -0.005]
    padded = traded + [0.0] * 8
    assert M.sharpe(padded) != M.sharpe(traded)
    assert abs(sum(padded) / len(padded)) < abs(sum(traded) / len(traded))


def test_sortino_uses_downside_deviation_over_all_observations():
    r = [0.02, -0.01, 0.01, -0.03]
    dd = math.sqrt((0.01**2 + 0.03**2) / 4)
    assert M.sortino(r) == pytest.approx((sum(r) / 4) / dd * math.sqrt(252))
    assert M.sortino([0.01, 0.02]) is None  # no downside -> undefined, not infinity


def test_max_drawdown_compounded():
    # +10% then -20% then +5%:  peak 1.10, trough 0.88 -> 20%
    assert M.max_drawdown([0.10, -0.20, 0.05]) == pytest.approx(0.20)
    assert M.max_drawdown([0.01, 0.01]) == 0.0


def test_oos_is_alert_threshold():
    assert M.oos_is(2.0, 0.9)["alert"] is True
    assert M.oos_is(2.0, 1.0)["alert"] is False  # exactly 0.5 x IS is not below it
    assert M.oos_is(2.0, 1.2)["ratio"] == pytest.approx(0.6)


def test_oos_is_not_meaningful_when_is_negative():
    out = M.oos_is(-0.5, 1.0)
    assert out["ratio"] is None and out["alert"] is None and "not meaningful" in out["note"]
    assert M.oos_is(None, 1.0)["ratio"] is None


def test_rolling_sharpe_needs_min_observations_and_uses_calendar_window():
    d0 = dt.date(2026, 7, 1)
    dates = [d0 + dt.timedelta(days=i) for i in range(40)]
    rets = [0.001 * ((-1) ** i) + 0.0005 for i in range(40)]
    s = M.rolling_sharpe(dates, rets)
    assert s[13]["sharpe"] is None  # 14 obs < 15
    assert s[14]["sharpe"] is not None
    assert s[39]["n"] == 30  # calendar 30-day window, one obs per day


def test_summarize_reports_zero_days_and_trade_stats():
    d0 = dt.date(2026, 7, 1)
    dates = [d0 + dt.timedelta(days=i) for i in range(4)]
    out = M.summarize(dates, [0.01, 0.0, -0.005, 0.0], [100.0, -50.0], traded_notional=0.8, sessions_total=4)
    assert out["trade_count"] == 2 and out["win_rate"] == 0.5
    assert out["stand_down_rate_sessions"] == 0.5
    assert out["turnover_x_book"] == 0.8 and out["turnover_per_session"] == 0.2
    assert out["max_drawdown_pct"] == pytest.approx(0.5)
