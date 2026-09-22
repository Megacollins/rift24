"""U.S. cash-equity session calendar (NYSE/Nasdaq regular session, America/New_York).

Regular session : 09:30-16:00 ET on trading days (13:00 close on early-close days).
Everything else is the "void" - the window in which rTokens trade and the cash market does not.

Coverage is 2025-2027. Dates outside that range raise CalendarRangeError instead of
silently assuming there are no holidays. Extended-hours venues (pre-market / after-hours ATS)
do exist; Rift24 deliberately measures against the *regular session* only - see README.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from enum import Enum
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
UTC = dt.timezone.utc

OPEN_T = dt.time(9, 30)
CLOSE_T = dt.time(16, 0)
EARLY_CLOSE_T = dt.time(13, 0)

CAL_FIRST_YEAR, CAL_LAST_YEAR = 2025, 2027

# Full-day closures. Observed-date rules already applied (e.g. Jul 4 2026 is a Saturday -> Fri Jul 3).
HOLIDAYS: dict[dt.date, str] = {
    # 2025 (includes the Jan 9 national day of mourning closure)
    dt.date(2025, 1, 1): "New Year's Day",
    dt.date(2025, 1, 9): "National Day of Mourning (Carter)",
    dt.date(2025, 1, 20): "Martin Luther King Jr. Day",
    dt.date(2025, 2, 17): "Presidents' Day",
    dt.date(2025, 4, 18): "Good Friday",
    dt.date(2025, 5, 26): "Memorial Day",
    dt.date(2025, 6, 19): "Juneteenth",
    dt.date(2025, 7, 4): "Independence Day",
    dt.date(2025, 9, 1): "Labor Day",
    dt.date(2025, 11, 27): "Thanksgiving",
    dt.date(2025, 12, 25): "Christmas",
    # 2026
    dt.date(2026, 1, 1): "New Year's Day",
    dt.date(2026, 1, 19): "Martin Luther King Jr. Day",
    dt.date(2026, 2, 16): "Presidents' Day",
    dt.date(2026, 4, 3): "Good Friday",
    dt.date(2026, 5, 25): "Memorial Day",
    dt.date(2026, 6, 19): "Juneteenth",
    dt.date(2026, 7, 3): "Independence Day (observed)",
    dt.date(2026, 9, 7): "Labor Day",
    dt.date(2026, 11, 26): "Thanksgiving",
    dt.date(2026, 12, 25): "Christmas",
    # 2027
    dt.date(2027, 1, 1): "New Year's Day",
    dt.date(2027, 1, 18): "Martin Luther King Jr. Day",
    dt.date(2027, 2, 15): "Presidents' Day",
    dt.date(2027, 3, 26): "Good Friday",
    dt.date(2027, 5, 31): "Memorial Day",
    dt.date(2027, 6, 18): "Juneteenth (observed)",
    dt.date(2027, 7, 5): "Independence Day (observed)",
    dt.date(2027, 9, 6): "Labor Day",
    dt.date(2027, 11, 25): "Thanksgiving",
    dt.date(2027, 12, 24): "Christmas (observed)",
}

# 13:00 ET early closes
EARLY_CLOSES: dict[dt.date, str] = {
    dt.date(2025, 7, 3): "Day before Independence Day",
    dt.date(2025, 11, 28): "Day after Thanksgiving",
    dt.date(2025, 12, 24): "Christmas Eve",
    dt.date(2026, 11, 27): "Day after Thanksgiving",
    dt.date(2026, 12, 24): "Christmas Eve",
    dt.date(2027, 11, 26): "Day after Thanksgiving",
}


class CalendarRangeError(ValueError):
    pass


class SessionStatus(str, Enum):
    REGULAR = "Regular"
    CLOSED = "Closed"
    WEEKEND_VOID = "Weekend void"


def _check_range(d: dt.date) -> None:
    if not (CAL_FIRST_YEAR <= d.year <= CAL_LAST_YEAR):
        raise CalendarRangeError(f"{d} is outside the calendar's {CAL_FIRST_YEAR}-{CAL_LAST_YEAR} coverage")


def is_trading_day(d: dt.date) -> bool:
    _check_range(d)
    return d.weekday() < 5 and d not in HOLIDAYS


def session_open(d: dt.date) -> dt.datetime:
    return dt.datetime.combine(d, OPEN_T, tzinfo=ET).astimezone(UTC)


def session_close(d: dt.date) -> dt.datetime:
    t = EARLY_CLOSE_T if d in EARLY_CLOSES else CLOSE_T
    return dt.datetime.combine(d, t, tzinfo=ET).astimezone(UTC)


def prev_trading_day(d: dt.date) -> dt.date:
    d = d - dt.timedelta(days=1)
    while not is_trading_day(d):
        d -= dt.timedelta(days=1)
    return d


def next_trading_day(d: dt.date) -> dt.date:
    d = d + dt.timedelta(days=1)
    while not is_trading_day(d):
        d += dt.timedelta(days=1)
    return d


def trading_days(start: dt.date, end: dt.date) -> list[dt.date]:
    out, d = [], start
    while d <= end:
        if is_trading_day(d):
            out.append(d)
        d += dt.timedelta(days=1)
    return out


@dataclass(frozen=True)
class Void:
    """The closed window between one regular close and the next regular open."""

    prev_close_utc: dt.datetime
    next_open_utc: dt.datetime

    @property
    def hours(self) -> float:
        return (self.next_open_utc - self.prev_close_utc).total_seconds() / 3600

    @property
    def is_extended(self) -> bool:
        return self.hours > 24


def _to_utc(ts: dt.datetime) -> dt.datetime:
    if ts.tzinfo is None:
        raise ValueError("timestamps must be timezone-aware")
    return ts.astimezone(UTC)


def surrounding_void(ts: dt.datetime) -> Void | None:
    """The void containing `ts`, or None if `ts` falls inside a regular session."""
    t = _to_utc(ts)
    d = t.astimezone(ET).date()
    if is_trading_day(d) and session_open(d) <= t < session_close(d):
        return None
    # previous close at or before t
    c = d if is_trading_day(d) else prev_trading_day(d)
    if session_close(c) > t:
        c = prev_trading_day(c)
    # next open strictly after t
    n = d if is_trading_day(d) else next_trading_day(d)
    if session_open(n) <= t:
        n = next_trading_day(n)
    return Void(session_close(c), session_open(n))


def status_at(ts: dt.datetime) -> SessionStatus:
    v = surrounding_void(ts)
    if v is None:
        return SessionStatus.REGULAR
    return SessionStatus.WEEKEND_VOID if v.is_extended else SessionStatus.CLOSED


def is_regular_session(ts: dt.datetime) -> bool:
    return status_at(ts) is SessionStatus.REGULAR


@dataclass(frozen=True)
class Session:
    """One prior-cash-close -> next-cash-open window. `open_date` names the session."""

    open_date: dt.date
    prev_date: dt.date
    close_utc: dt.datetime
    open_utc: dt.datetime
    kind: str  # 'overnight' | 'weekend' | 'holiday'

    @property
    def void_hours(self) -> float:
        return (self.open_utc - self.close_utc).total_seconds() / 3600


def make_session(open_date: dt.date) -> Session:
    if not is_trading_day(open_date):
        raise ValueError(f"{open_date} is not a trading day")
    p = prev_trading_day(open_date)
    close_u, open_u = session_close(p), session_open(open_date)
    gap_days = (open_date - p).days
    if gap_days == 1:
        kind = "overnight"
    elif any((p + dt.timedelta(days=i)).weekday() == 5 for i in range(1, gap_days)):
        kind = "weekend"
    else:
        kind = "holiday"
    return Session(open_date, p, close_u, open_u, kind)


def build_sessions(start: dt.date, end: dt.date) -> list[Session]:
    """Every session whose *open* date lies in [start, end]."""
    return [make_session(d) for d in trading_days(start, end)]
