import datetime as dt

import pytest

from rift24 import calendar as cal
from rift24 import residual as R
from rift24.backtest import Window, build_dataset, run_backtest, walk_forward
from tests.helpers import Spec, weekdays, world

CFG = R.Config()
MON = dt.date(2026, 9, 21)


def view(void_bps=100.0, *, decision=None, stale=0.0, spread=4.0, allow=True, src="ESTIMATED"):
    dec = decision or (cal.session_open(MON) - dt.timedelta(minutes=15))
    return R.PreOpenView("RNVDAUSDT", "rNVDA", "NVDA", MON, "weekend", 65.5, dec, 100.0, 100.0 * (1 + void_bps / 1e4),
                         stale, spread, src, allow)


def est(beta=0.4, se=0.1, n=100):
    return R.PricedIn(beta, se, n)


def gate(d, n):
    return next(g for g in d.gates if g.gate == n)


def test_costs_default_8bps_plus_4bps_labelled_estimated():
    v = view()
    assert R.total_cost_bps(v, CFG) == pytest.approx(12.0)
    assert v.spread_source == "ESTIMATED"
    assert R.total_cost_bps(view(spread=1.0, src="OBSERVED"), CFG) == pytest.approx(9.0)  # observed spread replaces fallback
    assert R.total_cost_bps(view(), CFG.with_(rt_cost_bps=20)) == pytest.approx(24.0)


def test_trade_happens_when_every_gate_passes_up_move_is_faded_down_move_is_added():
    up = R.decide(view(100), est(), CFG)
    assert up.stance == R.FADE and up.direction == -1 and up.binding_gate is None
    assert up.expected_edge_bps == pytest.approx(0.6 * 100)
    assert up.implied_cash_bps == pytest.approx(40.0)
    assert up.net_residual_bps == pytest.approx(60 - 12)
    assert up.side == "SELL/SHORT"
    dn = R.decide(view(-100), est(), CFG)
    # fading a DOWN move means buying it: stance is still FADE (against the move); the side is BUY
    assert dn.stance == R.FADE and dn.direction == 1 and dn.side == "BUY"


def test_gate1_regular_session_never_trades():
    d = R.decide(view(decision=cal.session_open(MON) + dt.timedelta(minutes=30)), est(), CFG)
    assert d.stance == R.NOTHING and d.binding_gate == 1 and not gate(d, 1).passed
    assert not gate(R.decide_always_fade(view(decision=cal.session_open(MON) + dt.timedelta(minutes=30)), None, CFG), 1).passed


def test_gate2_allowlist():
    d = R.decide(view(allow=False), est(), CFG)
    assert d.stance == R.NOTHING and not gate(d, 2).passed


def test_gate3_min_move_is_cost_based_and_configurable():
    assert R.min_move_bps(view(), CFG) == pytest.approx(24.0)  # 2 x 12
    assert R.decide(view(23), est(), CFG).binding_gate == 3
    assert R.decide(view(-23), est(), CFG).binding_gate == 3
    assert R.decide(view(30), est(beta=0.0), CFG).binding_gate is None
    assert R.min_move_bps(view(), CFG.with_(min_move_cost_multiple=4)) == pytest.approx(48.0)


def test_gate5_no_model_means_do_nothing_not_a_guess():
    d = R.decide(view(500), None, CFG)
    assert d.stance == R.NOTHING and d.binding_gate == 5
    assert "cannot be established" in gate(d, 5).detail
    assert d.expected_edge_bps is None and d.implied_cash_bps is None
    assert R.decide(view(500), est(n=10), CFG).binding_gate == 5  # too few past sessions


def test_gate5_needs_edge_at_least_2x_cost_and_significance():
    # beta 0.85 on a 100 bps move -> 15 bps edge < 24 bps (2 x 12)
    assert R.decide(view(100), est(beta=0.85, se=0.01), CFG).binding_gate == 5
    # edge passes (60 bps) but the beta is statistically indistinguishable from 1
    noisy = R.decide(view(100), est(beta=0.4, se=0.6), CFG)
    assert noisy.binding_gate == 5 and "significance t=1.00" in gate(noisy, 5).detail
    # rToken UNDER-moved (beta > 1): the fade thesis has no support, and we do not flip into momentum
    assert R.decide(view(100), est(beta=1.5, se=0.05), CFG).stance == R.NOTHING


def test_gate6_stale_data_stands_down_but_the_boundary_is_inclusive():
    fresh = R.decide(view(100, stale=15.0), est(), CFG)
    assert fresh.binding_gate is None
    stale = R.decide(view(100, stale=30.0), est(), CFG)
    assert stale.stance == R.NOTHING and stale.binding_gate == 6


def test_gate6_live_clock_staleness():
    v = view(100)
    now = v.decision_utc + dt.timedelta(minutes=16)
    assert R.decide(v, est(), CFG, now_utc=now).binding_gate == 6
    assert R.decide(v, est(), CFG, now_utc=v.decision_utc + dt.timedelta(minutes=10)).binding_gate is None


def test_baselines():
    v = view(100)
    assert R.do_nothing(v, CFG).stance == R.NOTHING
    # always-fade ignores the edge gate: trades even with no model, and even when Rift24 stands down
    b = R.decide_always_fade(v, None, CFG)
    assert b.stance == R.FADE
    assert R.decide(v, None, CFG).stance == R.NOTHING
    # but it still respects the min-move gate
    assert R.decide_always_fade(view(10), None, CFG).stance == R.NOTHING


def test_explanations_are_deterministic_and_state_the_binding_gate():
    d = R.decide(view(500), None, CFG)
    assert d.explanation == R.decide(view(500), None, CFG).explanation
    assert "DO NOTHING" in d.explanation and "Gate 5" in d.explanation
    t = R.decide(view(100), est(), CFG)
    assert "FADE" in t.explanation and "60" in t.explanation


def test_fit_priced_in_recovers_known_beta():
    xs = [50, -80, 120, -60, 200]
    f = R.fit_priced_in([(x, 0.3 * x) for x in xs])
    assert f.beta == pytest.approx(0.3) and f.n == 5 and f.se == pytest.approx(0.0, abs=1e-12)
    assert R.fit_priced_in([(1, 1)]) is None


def test_autopsy_uses_realised_cash_gap_and_classifies_verdict():
    v = view(100)
    out = R.Outcome(100.5, 100.0, 100.0, 100.3, cal.session_open(MON))  # cash +30 bps, rToken +100 => overshoot 70 bps
    a = R.autopsy(v, out, R.decide(v, est(), CFG), CFG)
    assert a["realized_cash_gap_bps"] == pytest.approx(30.0)
    assert a["priced_in_ratio"] == pytest.approx(100 / 30, rel=1e-3)
    assert a["realized_residual_bps"] == pytest.approx(-70.0)
    assert a["verdict"].startswith("OVER-PRICED")
    assert a["net_bps"] == pytest.approx(-1 * (100.0 / 100.5 - 1) * 1e4 - 12.0, abs=0.2)
    flat = R.autopsy(v, R.Outcome(100, 100, 100, 100.02, cal.session_open(MON)), R.do_nothing(v, CFG), CFG)
    assert flat["priced_in_ratio"] is None  # cash gap ~0 -> ratio unstable, not reported


def _bt(specs):
    days = [s.date for s in specs]
    a = world(specs)
    return run_backtest(a, CFG, Window(days[0], days[-1], days[-1]), warmup_start=days[0])


def test_sizing_cap_and_gross_limit_and_net_pnl():
    days = weekdays(dt.date(2026, 7, 6), 40)
    # fully deterministic: cash gap = 0.3 x void, rToken then converges fully during the hold
    sp = [Spec(d, void=100.0, cash=30.0, hold=-70.0) for d in days]
    res = _bt(sp)
    tr = res.trades["RIFT24"]
    assert tr, "expected some trades once the model has enough history"
    assert all(t.weight <= CFG.max_name_pct + 1e-12 for t in tr)
    t = tr[0]
    assert t.direction == -1 and t.stance == R.FADE
    assert t.gross_bps == pytest.approx(70.0, abs=0.5)
    assert t.net_bps == pytest.approx(t.gross_bps - 12.0)
    # the model needs 30 completed past sessions -> Rift24 stands down for the first ~30 days, always-fade does not
    assert len(res.trades["B_ALWAYS_FADE"]) == len(days)
    assert len(tr) < len(res.trades["B_ALWAYS_FADE"])
    assert res.trades["A_DO_NOTHING"] == []
    assert all(r == 0.0 for _, r in res.daily["A_DO_NOTHING"])


def test_stand_down_accounting_and_avoided_trade_pnl():
    days = weekdays(dt.date(2026, 7, 6), 40)
    sp = [Spec(d, void=100.0, cash=30.0, hold=-70.0) for d in days]
    res = _bt(sp)
    sd = res.diagnostics["stand_down"]
    assert sd["symbol_sessions"] == len(days)
    assert sd["rift24_trades"] + (sd["symbol_sessions"] - sd["rift24_trades"]) == sd["symbol_sessions"]
    assert 0 < sd["rift24_stand_down_rate_all_symbol_sessions"] < 1
    assert sd["binding_gate_counts"]["Gate 5"] > 0 and sd["binding_gate_counts"]["TRADED"] == sd["rift24_trades"]


def test_rift24_trades_are_a_subset_of_always_fade_trades():
    days = weekdays(dt.date(2026, 7, 6), 60)
    sp = [Spec(d, void=(80 if i % 3 else -90), cash=(30 if i % 3 else -30), hold=(-50 if i % 3 else 60)) for i, d in enumerate(days)]
    res = _bt(sp)
    b = {(t.symbol, t.open_date) for t in res.trades["B_ALWAYS_FADE"]}
    r = {(t.symbol, t.open_date) for t in res.trades["RIFT24"]}
    assert r <= b and len(r) > 0


def test_weekend_session_is_built_from_friday_close():
    d = dt.date(2026, 7, 13)  # ordinary Monday (Mon 2026-07-06 follows the Jul 3 holiday -> 89.5 h)
    a = world([Spec(d, 100, 30, -70)])
    assert cal.make_session(dt.date(2026, 7, 6)).void_hours == pytest.approx(89.5)
    obs, _ = build_dataset(a, CFG, d, d)
    assert obs[0].view.kind == "weekend" and obs[0].view.void_hours == pytest.approx(65.5)


# ----------------------------------------------------------------------------- exploratory two-sided residual + baseline C
def test_residual_mode_joins_an_underpriced_move_and_fades_an_overpriced_one():
    under = R.decide_residual(view(200), est(beta=1.3, se=0.05), CFG)  # cash gap will be 260 > printed 200
    assert under.stance == R.ADD and under.direction == 1 and under.side == "BUY"
    assert under.residual_bps == pytest.approx(60.0) and under.expected_edge_bps == pytest.approx(60.0)
    under_dn = R.decide_residual(view(-200), est(beta=1.3, se=0.05), CFG)
    assert under_dn.stance == R.ADD and under_dn.direction == -1 and under_dn.side == "SELL/SHORT"
    over = R.decide_residual(view(200), est(beta=0.6, se=0.05), CFG)
    assert over.stance == R.FADE and over.direction == -1
    # the pre-specified fade-only rule must NOT join an under-priced move
    assert R.decide(view(200), est(beta=1.3, se=0.05), CFG).stance == R.NOTHING


def test_residual_mode_still_needs_2x_cost_and_two_sided_significance():
    assert R.decide_residual(view(200), est(beta=1.05, se=0.01), CFG).binding_gate == 5  # 10 bps < 24 bps
    assert R.decide_residual(view(200), est(beta=1.3, se=0.2), CFG).binding_gate == 5  # t = 1.5 < 1.96
    assert R.decide_residual(view(200), None, CFG).binding_gate == 5


def test_always_follow_is_the_mirror_of_always_fade():
    f, m = R.decide_always_fade(view(100), None, CFG), R.decide_always_follow(view(100), None, CFG)
    assert f.direction == -1 and m.direction == 1 and m.stance == R.ADD
    assert R.decide_always_follow(view(-100), None, CFG).direction == -1
    assert R.decide_always_follow(view(10), None, CFG).stance == R.NOTHING  # same min-move gate


def test_explanation_for_do_nothing_still_reports_the_model_view():
    d = R.decide(view(100), est(beta=1.03, se=0.01), CFG)
    assert d.stance == R.NOTHING and "Historical reference: 100 past large-move sessions, 1.03x" in d.explanation and "Gate 5" in d.explanation


# ----------------------------------------------------------------------------- exits are inside the void (design correction)
def test_default_exit_happens_before_the_cash_open_so_no_position_touches_the_regular_session():
    days = weekdays(dt.date(2026, 7, 13), 3)
    a = world([Spec(d, 100, 30, -70) for d in days])
    obs, _ = build_dataset(a, CFG, days[0], days[-1])
    assert CFG.exit_after_open_bars == 0
    for o in obs:
        s = cal.make_session(o.view.open_date)
        assert o.outcome.known_utc == s.open_utc  # exit bar closes exactly at the open
        assert cal.status_at(o.outcome.known_utc - dt.timedelta(seconds=1)) is not cal.SessionStatus.REGULAR
        assert o.view.decision_utc < o.outcome.known_utc


def test_post_open_exit_is_available_only_as_an_explicit_sensitivity():
    days = weekdays(dt.date(2026, 7, 13), 2)
    a = world([Spec(d, 100, 30, -70) for d in days])
    obs, _ = build_dataset(a, CFG.with_(exit_after_open_bars=1), days[0], days[-1])
    s = cal.make_session(days[0])
    assert obs[0].outcome.known_utc == s.open_utc + dt.timedelta(minutes=15)
