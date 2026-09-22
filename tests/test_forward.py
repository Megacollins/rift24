import dataclasses
import datetime as dt
import json

import pytest

from rift24 import calendar as cal
from rift24 import forward as F
from rift24.data import BAR_MS, Bar, DailyBar
from rift24.residual import PricedIn
from tests.helpers import SYMBOL, TICKER, MemoryAdapter

OPEN_DATE = dt.date(2026, 9, 22)  # Tuesday
SESS = cal.make_session(OPEN_DATE)
OPEN_MS, CLOSE_MS = int(SESS.open_utc.timestamp() * 1000), int(SESS.close_utc.timestamp() * 1000)
DEC_MS = OPEN_MS - F.LEAD_BARS * BAR_MS
DEC_UTC = dt.datetime.fromtimestamp(DEC_MS / 1000, dt.timezone.utc)
MODEL = PricedIn(1.3, 0.05, 100)
META = {"sha256": "test"}


def bar(a, start, o, c):
    a.bars.setdefault(SYMBOL, {})[start] = Bar(start, o, max(o, c), min(o, c), c)


def world(void=200.0, cash=260.0, hold=250.0, base=100.0):
    a = MemoryAdapter()
    dec_px = base * (1 + void / 1e4)
    bar(a, CLOSE_MS - BAR_MS, base, base)  # bar ending at prior cash close
    bar(a, DEC_MS - BAR_MS, dec_px, dec_px)  # bar ending at the decision time
    bar(a, DEC_MS, dec_px, dec_px * (1 + hold / 1e4))  # entry bar (open = entry)
    bar(a, OPEN_MS - BAR_MS, dec_px, dec_px * (1 + hold / 1e4))  # last pre-open bar (close = exit)
    a.cashd[TICKER] = {SESS.prev_date: DailyBar(SESS.prev_date, 100.0, 100.0), OPEN_DATE: DailyBar(OPEN_DATE, 100 * (1 + cash / 1e4), 100.0)}
    return a


def row_for(rows, sym=SYMBOL):
    return next(r for r in rows if r["symbol"] == sym)


def snap(a, now=None, **kw):
    return F.take_snapshot(a, now or DEC_UTC + dt.timedelta(minutes=2), MODEL, META, **kw)


# ----------------------------------------------------------------------------- freezing
def test_model_is_frozen_hashed_and_tamper_evident(tmp_path):
    from rift24.data import FixtureAdapter
    a = FixtureAdapter()
    if not a.has_data():
        pytest.skip("fixtures not present")
    p = tmp_path / "frozen_model.json"
    doc = F.freeze_model(a, p)
    assert doc["decision_lead_bars"] == 22 and doc["n"] > 300 and doc["beta"] > 1.0
    with pytest.raises(F.ForwardError):
        F.freeze_model(a, p)  # cannot silently re-freeze
    est, meta = F.load_model(p)
    assert est.beta == pytest.approx(doc["beta"]) and meta["sha256"] == doc["sha256"]
    tampered = json.loads(p.read_text()) | {"beta": 0.4}
    p.write_text(json.dumps(tampered))
    with pytest.raises(F.ForwardError, match="modified"):
        F.load_model(p)


def test_config_drift_is_rejected(tmp_path):
    from rift24.data import FixtureAdapter
    a = FixtureAdapter()
    if not a.has_data():
        pytest.skip("fixtures not present")
    p = tmp_path / "m.json"
    doc = F.freeze_model(a, p)
    cfg = dict(doc["config"]) | {"rt_cost_bps": 3.0}
    core = {k: doc[k] for k in ("rule", "decision_lead_bars", "beta", "se", "n", "fitted_through_session")} | {"config": cfg}
    p.write_text(json.dumps(core | {"sha256": F._digest(core)}))  # internally consistent, but not the registered config
    with pytest.raises(F.ForwardError, match="Config differs"):
        F.load_model(p)


def test_the_committed_frozen_model_verifies():
    est, meta = F.load_model()
    assert meta["rule"] == "RIFT24_RESIDUAL" and est.n > 300


# ----------------------------------------------------------------------------- snapshot
def test_snapshot_applies_the_frozen_rule_and_logs_it(tmp_path):
    log = tmp_path / "log.jsonl"
    rows = snap(world(void=200.0), log=log)
    r = row_for(rows)
    assert r["stance"] == "ADD" and r["side"] == "BUY" and r["binding_gate"] is None
    assert r["void_bps"] == pytest.approx(200.0) and r["residual_bps"] == pytest.approx(60.0)
    assert r["model_sha256"] == "test" and r["decision_utc"].endswith("Z")
    assert len(log.read_text().splitlines()) == 10  # every allow-listed name is logged, traded or not
    other = row_for(rows, "RTSLAUSDT")
    assert other["stance"] == "DO NOTHING"  # no price data for it: logged as a stand-down, never guessed


def test_small_residual_stands_down_with_the_reason(tmp_path):
    r = row_for(snap(world(void=50.0), log=tmp_path / "l.jsonl"))
    assert r["stance"] == "DO NOTHING" and r["binding_gate"] == 5  # residual 15 bps < 24 bps


def test_refuses_to_run_too_early_or_after_the_open(tmp_path):
    a = world()
    with pytest.raises(F.ForwardError, match="too early"):
        snap(a, DEC_UTC - dt.timedelta(minutes=1), log=tmp_path / "l.jsonl")
    with pytest.raises(F.ForwardError, match="open"):
        snap(a, SESS.open_utc + dt.timedelta(minutes=1), log=tmp_path / "l.jsonl")


def test_late_run_trips_the_freshness_gate_instead_of_pretending(tmp_path):
    r = row_for(snap(world(void=200.0), DEC_UTC + dt.timedelta(minutes=40), log=tmp_path / "l.jsonl"))
    assert r["stance"] == "DO NOTHING" and r["binding_gate"] == 6


def test_log_is_append_only_and_idempotent(tmp_path):
    log = tmp_path / "log.jsonl"
    a = world()
    snap(a, log=log)
    before = log.read_text()
    assert snap(a, log=log) == []
    assert log.read_text() == before


def test_snapshot_cannot_see_anything_after_the_decision(tmp_path):
    a1, a2 = world(void=200.0, cash=260.0, hold=250.0), world(void=200.0, cash=-900.0, hold=-900.0)
    r1, r2 = row_for(snap(a1, dry_run=True)), row_for(snap(a2, dry_run=True))
    keep = ("void_bps", "stance", "binding_gate", "residual_bps", "expected_edge_bps", "decision_px")
    assert {k: r1[k] for k in keep} == {k: r2[k] for k in keep}


def test_observed_spread_is_logged_but_does_not_move_the_frozen_decision(tmp_path):
    a = world(void=200.0)
    a.observed_spread_snapshot = lambda: {SYMBOL: {"ts_ms": DEC_MS, "spread_bps": 9.5}}
    r = row_for(snap(a, dry_run=True))
    assert r["spread_bps_observed"] == 9.5 and r["cost_bps_assumed"] == 12.0 and r["stance"] == "ADD"


# ----------------------------------------------------------------------------- settle
def test_settle_waits_for_the_open_then_records_gross_and_net(tmp_path):
    log, out = tmp_path / "log.jsonl", tmp_path / "out.jsonl"
    a = world(void=200.0, cash=260.0, hold=250.0)
    a.observed_spread_snapshot = lambda: {SYMBOL: {"ts_ms": DEC_MS, "spread_bps": 2.0}}
    snap(a, log=log)
    assert F.settle(a, SESS.open_utc + dt.timedelta(minutes=1), log=log, outcomes=out) == []  # gap not observable yet
    rows = F.settle(a, SESS.open_utc + dt.timedelta(minutes=10), log=log, outcomes=out)
    r = row_for(rows)
    assert r["realized_cash_gap_bps"] == pytest.approx(260.0)
    assert r["gross_bps"] == pytest.approx(250.0, abs=0.5)
    assert r["net_bps_assumed_cost"] == pytest.approx(r["gross_bps"] - 12.0)
    assert r["net_bps_observed_spread"] == pytest.approx(r["gross_bps"] - 10.0)  # 8 bps fee + 2 bps observed spread
    assert F.settle(a, SESS.open_utc + dt.timedelta(hours=1), log=log, outcomes=out) == []  # idempotent


# ----------------------------------------------------------------------------- verdict
def _write(tmp_path, n_days, net):
    log, out = tmp_path / "l.jsonl", tmp_path / "o.jsonl"
    ls, os_ = [], []
    for i in range(n_days):
        d = (dt.date(2026, 10, 1) + dt.timedelta(days=i)).isoformat()
        for s in ("A", "B"):
            rid = f"{d}-{s}"
            ls.append({"id": rid, "open_date": d, "symbol": s, "direction": 1, "cost_bps_assumed": 12.0, "spread_bps_observed": 3.0})
            os_.append({"id": rid, "open_date": d, "symbol": s, "net_bps_assumed_cost": net + (i % 3) * 5 - 5,
                        "net_bps_observed_spread": net + 1})
    F._append(log, ls)
    F._append(out, os_)
    return log, out


def test_verdict_is_inconclusive_until_the_preregistered_sample_is_reached(tmp_path):
    log, out = _write(tmp_path, 5, 100.0)
    s = F.summarize(log, out)
    assert s["status"].startswith("INCONCLUSIVE") and s["trades_settled"] == 10


def test_verdict_uses_day_clustered_evidence_once_the_sample_is_big_enough(tmp_path):
    log, out = _write(tmp_path, 16, 60.0)
    assert F.summarize(log, out)["status"].startswith("SUPPORTED")
    (tmp_path / "x").mkdir()
    log2, out2 = _write(tmp_path / "x", 16, -30.0)
    assert F.summarize(log2, out2)["status"].startswith("NOT SUPPORTED")


def test_real_forward_log_has_no_fabricated_rows():
    """Guard: nothing may be committed to the forward log except rows produced by the snapshot step."""
    for r in F._read(F.FWD / "log.jsonl"):
        assert {"id", "decision_utc", "run_at_utc", "model_sha256"} <= set(r)
