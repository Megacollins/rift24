import datetime as dt

import pytest

from rift24 import data as D
from rift24.data import FixtureAdapter, SpreadQuote


@pytest.fixture(scope="module")
def fx():
    a = FixtureAdapter()
    if not a.has_data():
        pytest.skip("fixtures not present")
    return a


def test_allowlist_has_the_ten_named_rtokens_and_verified_symbols():
    ins, meta = D.load_allowlist()
    assert [i.rtoken for i in ins] == ["rNVDA", "rTSLA", "rQQQ", "rSPY", "rMETA", "rAAPL", "rMSFT", "rAMZN", "rGOOGL", "rAMD"]
    assert all(i.bitget_symbol == f"R{i.underlying}USDT" for i in ins)
    assert meta["max_single_name_notional_pct_of_book"] == 20


def test_fixtures_are_real_frozen_snapshots_covering_60_plus_days(fx):
    st = fx.status()
    assert st.mode == "FIXTURE" and "OBSERVED" in st.provenance and st.fetched_at_utc
    first = min(min(b) for b in fx._bars.values())
    last = max(max(b) for b in fx._bars.values())
    assert (last - first) / 86_400_000 >= 90  # >= 90 days of 15m bars
    for ins in fx.instruments():
        assert len(fx._bars[ins.bitget_symbol]) > 7000
        assert len(fx._cash[ins.underlying]) > 60


def test_bars_are_sane_ohlc(fx):
    for sym, bars in fx._bars.items():
        for b in list(bars.values())[::97]:
            assert b.low <= min(b.open, b.close) + 1e-9 and b.high >= max(b.open, b.close) - 1e-9 and b.low > 0
        ts = sorted(bars)
        assert all(t % D.BAR_MS == 0 for t in ts[::50])


def test_backtest_spread_is_estimated_fallback_never_presented_as_observed(fx):
    q = fx.spread("RNVDAUSDT", 1_780_000_000_000)
    assert isinstance(q, SpreadQuote) and q.source == "ESTIMATED" and q.bps == D.SPREAD_FALLBACK_BPS


def test_observed_spread_snapshot_is_exposed_separately_and_labelled_as_a_snapshot(fx):
    snap = fx.observed_spread_snapshot()
    assert snap["RNVDAUSDT"]["spread_bps"] > 0
    assert "NOT historical" in fx.manifest["sources"]["spread"]


def test_get_adapter_falls_back_loudly_when_live_is_unreachable(monkeypatch, capsys):
    monkeypatch.setattr(D, "bitget_reachable", lambda: False)
    a, notice = D.get_adapter(prefer_live=True)
    out = capsys.readouterr().out
    assert isinstance(a, FixtureAdapter)
    assert "LIVE DATA UNAVAILABLE" in out and "FALLING BACK TO FIXTURE DATA" in out
    assert notice and "FALLING BACK TO FIXTURE DATA" in notice


def test_get_adapter_default_is_offline_and_touches_no_network(monkeypatch):
    monkeypatch.setattr(D, "http_json", lambda *a, **k: (_ for _ in ()).throw(AssertionError("network used")))
    a, notice = D.get_adapter()
    assert isinstance(a, FixtureAdapter) and notice is None


def test_live_adapter_uses_observed_spread_only_when_fresh(monkeypatch):
    class Stub(D.LiveAdapter):
        def __init__(self):  # bypass network
            D.DataAdapter.__init__(self)
            self._bars, self._cash = {}, {}
            self._spread = {"RNVDAUSDT": {"ts_ms": 1_000_000_000_000, "spread_bps": 1.7}}

    s = Stub()
    fresh = s.spread("RNVDAUSDT", 1_000_000_000_000 + 5 * 60_000)
    stale = s.spread("RNVDAUSDT", 1_000_000_000_000 + 30 * 60_000)
    assert fresh.source == "OBSERVED" and fresh.bps == 1.7
    assert stale.source == "ESTIMATED" and stale.bps == D.SPREAD_FALLBACK_BPS


def test_stale_data_status_reports_last_bar_end(fx):
    assert dt.datetime.fromisoformat(fx.status().last_bar_end_utc).year == 2026


def test_rtoken_open_bar_matches_yahoo_cash_open_on_real_fixtures(fx):
    """Two unrelated sources agreeing validates session alignment and the cash series."""
    from rift24.backtest import Window, last_complete_session, run_backtest
    from rift24.residual import Config
    res = run_backtest(fx, Config(), Window.ending(last_complete_session(fx), 90, 30))
    v = res.data_validation
    assert v["n"] > 400 and v["median_bps"] < 5 and v["share_within_10_bps"] > 0.8, v
    for r in res.rows:  # and no scored trade window ever reaches into the regular session
        if r.period:
            from rift24 import calendar as cal
            assert r.obs.outcome.known_utc <= cal.session_open(r.obs.view.open_date)
