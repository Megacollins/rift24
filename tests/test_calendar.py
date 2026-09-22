import datetime as dt

import pytest

from rift24 import calendar as cal
from rift24.calendar import ET, UTC, SessionStatus


def et(y, m, d, hh=0, mm=0):
    return dt.datetime(y, m, d, hh, mm, tzinfo=ET)


def test_holidays_are_not_trading_days():
    for d in (dt.date(2026, 6, 19), dt.date(2026, 7, 3), dt.date(2026, 9, 7), dt.date(2026, 11, 26), dt.date(2026, 12, 25)):
        assert not cal.is_trading_day(d), d
    assert cal.is_trading_day(dt.date(2026, 7, 2))
    assert not cal.is_trading_day(dt.date(2026, 9, 19))  # Saturday


def test_early_close_thanksgiving_friday():
    assert cal.session_close(dt.date(2026, 11, 27)) == dt.datetime(2026, 11, 27, 18, 0, tzinfo=UTC)  # 13:00 EST
    assert cal.session_close(dt.date(2026, 11, 30)) == dt.datetime(2026, 11, 30, 21, 0, tzinfo=UTC)


def test_dst_shifts_utc_open():
    assert cal.session_open(dt.date(2026, 3, 6)) == dt.datetime(2026, 3, 6, 14, 30, tzinfo=UTC)  # EST
    assert cal.session_open(dt.date(2026, 3, 9)) == dt.datetime(2026, 3, 9, 13, 30, tzinfo=UTC)  # EDT


def test_weekend_void_length_across_dst_is_64_5_hours():
    s = cal.make_session(dt.date(2026, 3, 9))
    assert s.kind == "weekend"
    assert s.void_hours == pytest.approx(64.5)


def test_normal_weekend_and_overnight_void_hours():
    assert cal.make_session(dt.date(2026, 9, 21)).void_hours == pytest.approx(65.5)  # Fri 16:00 -> Mon 09:30
    assert cal.make_session(dt.date(2026, 9, 22)).void_hours == pytest.approx(17.5)


def test_session_kinds():
    assert cal.make_session(dt.date(2026, 9, 21)).kind == "weekend"
    assert cal.make_session(dt.date(2026, 9, 22)).kind == "overnight"
    assert cal.make_session(dt.date(2026, 4, 6)).kind == "weekend"  # Mon after Good Friday (Fri->Mon, 3-day gap)
    assert cal.make_session(dt.date(2026, 11, 27)).kind == "holiday"  # Wed close -> Fri open, mid-week holiday
    assert cal.make_session(dt.date(2026, 6, 22)).prev_date == dt.date(2026, 6, 18)  # Juneteenth Friday skipped


@pytest.mark.parametrize(
    "ts,expected",
    [
        (et(2026, 9, 19, 12), SessionStatus.WEEKEND_VOID),  # Saturday
        (et(2026, 9, 18, 17), SessionStatus.WEEKEND_VOID),  # Friday after close
        (et(2026, 9, 21, 9, 29), SessionStatus.WEEKEND_VOID),  # 1 min before Monday open
        (et(2026, 9, 21, 9, 30), SessionStatus.REGULAR),
        (et(2026, 9, 21, 15, 59), SessionStatus.REGULAR),
        (et(2026, 9, 21, 16, 0), SessionStatus.CLOSED),  # Monday close -> Tuesday overnight (17.5 h)
        (et(2026, 9, 22, 6, 0), SessionStatus.CLOSED),
        (et(2026, 11, 26, 12), SessionStatus.WEEKEND_VOID),  # Thanksgiving: extended void
        (et(2026, 11, 27, 13, 0), SessionStatus.WEEKEND_VOID),  # after early close, 3-day extended void
        (et(2026, 11, 27, 12, 59), SessionStatus.REGULAR),
    ],
)
def test_status_at(ts, expected):
    assert cal.status_at(ts) is expected
    assert cal.status_at(ts.astimezone(UTC)) is expected  # timezone-independent


def test_naive_timestamps_rejected():
    with pytest.raises(ValueError):
        cal.status_at(dt.datetime(2026, 9, 21, 12))


def test_out_of_range_raises_instead_of_guessing():
    with pytest.raises(cal.CalendarRangeError):
        cal.is_trading_day(dt.date(2030, 1, 3))


def test_calendar_matches_observed_yahoo_trading_days_in_fixtures():
    """Cross-check against real data: every date Yahoo has a daily bar for is one of our trading days and vice versa."""
    from rift24.data import FixtureAdapter

    a = FixtureAdapter()
    if not a.has_data():
        pytest.skip("fixtures not present")
    observed = {d for d in a._cash["SPY"]}
    lo, hi = min(observed), max(observed)
    ours = set(cal.trading_days(lo, hi))
    assert ours == observed
