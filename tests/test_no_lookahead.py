"""The most important tests in the repo: the decision may never see the realised cash gap or anything after the decision bar."""
import dataclasses
import datetime as dt
import inspect

from rift24 import residual as R
from rift24.backtest import Window, build_dataset, walk_forward
from rift24.data import BAR_MS, Bar, DailyBar
from tests.helpers import SYMBOL, TICKER, Spec, add_session, weekdays, world

DAYS = weekdays(dt.date(2026, 7, 6), 60)
CFG = R.Config()


def specs(n=60):
    # deterministic mix of big moves that mostly over-shoot the cash gap (beta ~ 0.4), so the gate can fire
    out = []
    for i, d in enumerate(DAYS[:n]):
        void = (60 + (i % 7) * 15) * (1 if i % 2 == 0 else -1)
        out.append(Spec(d, void=void, cash=0.4 * void, hold=-0.6 * void))  # rToken converges to the cash gap
    return out


def run(adapter):
    obs, _ = build_dataset(adapter, CFG, DAYS[0], DAYS[-1])
    win = Window(DAYS[0], DAYS[-1], DAYS[-1])
    return obs, walk_forward(obs, CFG, win)


def sig(row):
    d = row.decisions["RIFT24"]
    return (d.stance, d.direction, d.binding_gate, d.expected_edge_bps, d.implied_cash_bps,
            tuple((g.gate, g.passed, g.detail) for g in d.gates), row.model)


def test_preopen_view_contains_no_post_decision_field():
    names = {f.name for f in dataclasses.fields(R.PreOpenView)}
    assert not (names & {"cash_open", "cash_prev_close", "cash_bps", "cash_ret", "entry_px", "exit_px", "hold_ret", "outcome"})
    assert "decision_utc" in names
    props = {n for n, v in inspect.getmembers(R.PreOpenView) if isinstance(v, property)}
    assert props == {"void_ret", "void_bps"}


def test_decision_functions_cannot_be_handed_an_outcome():
    for fn in (R.decide, R.decide_always_fade):
        params = list(inspect.signature(fn).parameters)
        assert params[0] == "view" and "outcome" not in params and "obs" not in params
    for name, fn in (("decide", R.decide), ("decide_always_fade", R.decide_always_fade)):
        src = inspect.getsource(fn)
        assert "cash" not in src.lower().replace("cash market", ""), name


def test_gates_never_read_outcome_fields():
    src = inspect.getsource(R._gates) + inspect.getsource(R._build) + inspect.getsource(R.explain)
    import re
    for banned in ("outcome", "cash_bps", "cash_ret", "cash_open", "hold_ret", "exit_px", "entry_px"):
        assert not re.search(rf"\b{banned}\b", src), banned


def test_all_decisions_taken_while_cash_is_closed():
    from rift24 import calendar as cal
    obs, rows = run(world(specs()))
    for r in rows:
        assert cal.status_at(r.obs.view.decision_utc) is not cal.SessionStatus.REGULAR
        assert r.obs.view.decision_utc < r.obs.outcome.known_utc


def test_decisions_invariant_to_every_post_decision_input():
    base_specs = specs()
    _, rows0 = run(world(base_specs))
    target = 45
    a = world(base_specs)
    # Rewrite EVERYTHING that becomes known after the decision on the target day: cash open, entry bar, exit bar.
    d = DAYS[target]
    s = R.cal.make_session(d)
    open_ms = int(s.open_utc.timestamp() * 1000)
    a.cashd[TICKER][d] = DailyBar(d, 100 * 1.20, 100.0)  # +20% cash gap
    a.bars[SYMBOL][open_ms] = Bar(open_ms, 1.0, 999.0, 0.5, 500.0)  # exit bar: absurd
    a.bars[SYMBOL][open_ms - BAR_MS] = Bar(open_ms - BAR_MS, 101.0, 101.0, 101.0, 101.0)  # entry bar (open only feeds P&L)
    _, rows1 = run(a)
    assert len(rows0) == len(rows1)
    for i, (r0, r1) in enumerate(zip(rows0, rows1)):
        if r0.obs.view.open_date <= d:
            assert sig(r0) == sig(r1), f"decision changed for {r0.obs.view.open_date}"
    # ...and the outcome DID change, so the test is not vacuous
    assert rows0[target].obs.outcome.cash_bps != rows1[target].obs.outcome.cash_bps


def test_changing_a_later_outcome_never_changes_earlier_decisions():
    base_specs = specs()
    _, rows0 = run(world(base_specs))
    j = 30
    mod = list(base_specs)
    mod[j] = Spec(mod[j].date, mod[j].void, cash=-3 * mod[j].cash, hold=-5 * mod[j].hold)
    _, rows1 = run(world(mod))
    for r0, r1 in zip(rows0, rows1):
        if r0.obs.view.open_date <= DAYS[j]:
            assert sig(r0) == sig(r1)
    # the model for the next session must differ (it now trains on the altered outcome) -> walk-forward really learns
    assert rows0[j + 1].model != rows1[j + 1].model


def test_training_set_only_holds_outcomes_known_before_decision():
    obs, rows = run(world(specs()))
    for r in rows:
        eligible_known = [
            o for o in obs
            if o.outcome.known_utc <= r.obs.view.decision_utc and R.is_large_move(o.view, CFG)
        ]
        got = 0 if r.model is None else r.model.n
        assert got == len(eligible_known) or (r.model is None and len(eligible_known) < 3)


def test_same_day_outcome_is_never_in_its_own_training_set():
    obs, rows = run(world(specs()))
    first = rows[0]
    assert first.model is None  # nothing known yet on day one
    second = rows[1]
    assert second.model is None or second.model.n <= 1


def test_beta_estimate_matches_cash_gap_relationship_when_fully_known():
    # by construction R_cash = 0.4 * R_void, so the walk-forward beta must converge to 0.4 using past data only
    _, rows = run(world(specs()))
    est = rows[-1].model
    assert est is not None and abs(est.beta - 0.4) < 1e-9
