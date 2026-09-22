"""Session construction, the priced-in model, the gates and the deterministic explanation.

The look-ahead barrier is structural, not a convention:

    PreOpenView  - everything knowable at decision time (rToken prices up to the decision bar).
    Outcome      - entry/exit fills and the realised cash gap. Known only AFTER the open.

`decide()` and `decide_always_fade()` accept a PreOpenView and nothing else; the cash open
(R_cash) can therefore never reach a trading decision. tests/test_no_lookahead.py enforces
this by inspecting the dataclass fields and by mutating every post-decision input.

Timeline of one session (all ET, regular close 16:00, regular open 09:30). Every trade lives inside the void:

    prior cash close -- void (rToken trades, cash does not) -- decision -- entry ------------ exit -- OPEN
    base price                                                 (pre-spec 09:15)  next bar's open   last pre-open bar's close (09:30)
    (15:45 bar close)                                          (09:00 bar close)

The exit is the close of the last bar BEFORE the cash open, so no position is ever opened or closed in the regular session.
(An earlier draft exited 15 minutes after the open; that mixed in regular-session drift. Post-open exits remain as sensitivities.)
"""
from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass, field, replace

from . import calendar as cal
from .data import BAR_MS, STALE_MINUTES, DataAdapter, Instrument

# ------------------------------------------------------------------------------- config


@dataclass(frozen=True)
class Config:
    # --- costs (Gate 4). All in basis points of notional, charged once per round trip.
    rt_cost_bps: float = 8.0  # required default: 8 bps round-trip taker-style haircut
    spread_fallback_bps: float = 4.0  # used, and labelled ESTIMATED, when no observed spread
    slippage_bps: float = 0.0  # explicit extra slippage on top of the haircuts (base case: none)
    # --- gates
    min_move_cost_multiple: float = 2.0  # Gate 3: |R_void| >= this x total cost (cost-based, not optimised)
    edge_cost_multiple: float = 2.0  # Gate 5: expected edge >= this x total cost
    min_train_obs: int = 30  # Gate 5: edge is "established" only with this many past large-move sessions
    min_tstat: float = 1.645  # Gate 5: one-sided 95% evidence that the void move over-shoots (beta < 1)
    min_tstat_two_sided: float = 1.96  # exploratory two-sided variant: |1 - beta| must be significant at 95%
    stale_minutes: float = STALE_MINUTES  # Gate 6
    max_name_pct: float = 0.20  # Gate 7: max single-name notional, % of simulated book
    max_gross_pct: float = 1.00  # unlevered book
    # --- session construction
    decision_lead_bars: int = 1  # decision at open - 15m (cash market unambiguously closed)
    exit_after_open_bars: int = 0  # 0 = exit at the close of the last PRE-open bar (cash still closed); N>=1 = close of the Nth bar after the open
    max_carry_bars: int = 4  # last-trade carry-forward limit when a 15m bar has no trades
    burn_in_days: int = 7  # sessions within N days of an instrument's listing are never scored

    def with_(self, **kw) -> "Config":
        return replace(self, **kw)


# ------------------------------------------------------------------------------- views


@dataclass(frozen=True)
class PreOpenView:
    """Information available at decision time. NO cash-open data lives here, by design."""

    symbol: str  # bitget symbol, e.g. RNVDAUSDT
    rtoken: str
    underlying: str
    open_date: dt.date
    kind: str  # overnight | weekend | holiday
    void_hours: float
    decision_utc: dt.datetime
    base_px: float  # rToken price at prior cash close
    dec_px: float  # rToken price at decision time
    staleness_min: float  # decision time minus end of the last bar used
    spread_bps: float
    spread_source: str  # OBSERVED | ESTIMATED
    allowlisted: bool = True

    @property
    def void_ret(self) -> float:
        return self.dec_px / self.base_px - 1.0

    @property
    def void_bps(self) -> float:
        return self.void_ret * 1e4


@dataclass(frozen=True)
class Outcome:
    """Known only after the cash open. Used for P&L and autopsy, never for the decision."""

    entry_px: float
    exit_px: float
    cash_prev_close: float
    cash_open: float
    known_utc: dt.datetime  # when the exit bar closes: earliest moment this outcome may inform a model

    @property
    def cash_ret(self) -> float:
        return self.cash_open / self.cash_prev_close - 1.0

    @property
    def cash_bps(self) -> float:
        return self.cash_ret * 1e4

    @property
    def hold_ret(self) -> float:
        return self.exit_px / self.entry_px - 1.0


@dataclass(frozen=True)
class Observation:
    view: PreOpenView
    outcome: Outcome


@dataclass(frozen=True)
class Exclusion:
    symbol: str
    open_date: dt.date
    reason: str


def _price_at(adapter: DataAdapter, symbol: str, ts_ms: int, max_carry: int):
    """Last-trade price at ts_ms: close of the latest bar ending <= ts_ms, carried at most max_carry bars.
    Returns (price, bar_end_ms) or None."""
    for k in range(1, max_carry + 2):
        b = adapter.rtoken_bar(symbol, ts_ms - k * BAR_MS)
        if b is not None:
            return b.close, b.end_ms
    return None


def build_observation(
    adapter: DataAdapter, ins: Instrument, s: cal.Session, cfg: Config
) -> Observation | Exclusion:
    def excl(why: str) -> Exclusion:
        return Exclusion(ins.bitget_symbol, s.open_date, why)

    if (s.open_date - ins.listing_date).days < cfg.burn_in_days:
        return excl("listing burn-in")
    close_ms = int(s.close_utc.timestamp() * 1000)
    open_ms = int(s.open_utc.timestamp() * 1000)
    dec_ms = open_ms - cfg.decision_lead_bars * BAR_MS

    base = _price_at(adapter, ins.bitget_symbol, close_ms, cfg.max_carry_bars)
    if base is None:
        return excl("no rToken price near prior cash close")
    dec = _price_at(adapter, ins.bitget_symbol, dec_ms, cfg.max_carry_bars)
    if dec is None:
        return excl("no rToken price near decision time")
    entry = adapter.rtoken_bar(ins.bitget_symbol, dec_ms)
    if entry is None:
        return excl("no rToken trade in entry bar (cannot fill)")
    exit_bar = adapter.rtoken_bar(ins.bitget_symbol, open_ms + (cfg.exit_after_open_bars - 1) * BAR_MS)
    if exit_bar is None:
        return excl("no rToken trade in exit bar (cannot fill)")
    c0, c1 = adapter.cash_day(ins.underlying, s.prev_date), adapter.cash_day(ins.underlying, s.open_date)
    if c0 is None or c1 is None:
        return excl("missing cash-market daily bar")

    sp = adapter.spread(ins.bitget_symbol, dec_ms)
    # An OBSERVED spread is used as-is; otherwise the configurable fallback applies and stays labelled ESTIMATED.
    spread_bps = sp.bps if sp.source == "OBSERVED" else cfg.spread_fallback_bps
    decision_utc = dt.datetime.fromtimestamp(dec_ms / 1000, dt.timezone.utc)
    view = PreOpenView(
        symbol=ins.bitget_symbol,
        rtoken=ins.rtoken,
        underlying=ins.underlying,
        open_date=s.open_date,
        kind=s.kind,
        void_hours=s.void_hours,
        decision_utc=decision_utc,
        base_px=base[0],
        dec_px=dec[0],
        staleness_min=(dec_ms - dec[1]) / 60_000,
        spread_bps=spread_bps,
        spread_source=sp.source,
    )
    out = Outcome(
        entry_px=entry.open,
        exit_px=exit_bar.close,
        cash_prev_close=c0.close,
        cash_open=c1.open,
        known_utc=dt.datetime.fromtimestamp(exit_bar.end_ms / 1000, dt.timezone.utc),
    )
    return Observation(view, out)


# ------------------------------------------------------------------------------- priced-in model


@dataclass(frozen=True)
class PricedIn:
    """Pooled through-origin OLS  R_cash = beta * R_void  on PAST large-move sessions only.

    beta = 1  -> void move was fully priced-in; beta < 1 -> rToken over-shot the cash gap (fade thesis);
    beta > 1  -> rToken under-shot (momentum; outside the tested hypothesis)."""

    beta: float
    se: float
    n: int

    @property
    def t_overshoot(self) -> float:
        if self.se > 0:
            return (1.0 - self.beta) / self.se
        return math.inf if self.beta < 1.0 else (-math.inf if self.beta > 1.0 else 0.0)  # exact fit


@dataclass
class RunningFit:
    """Incremental sums so the walk-forward fit is O(1) per decision."""

    n: int = 0
    sxx: float = 0.0
    sxy: float = 0.0
    syy: float = 0.0

    def add(self, x: float, y: float) -> None:
        self.n += 1
        self.sxx += x * x
        self.sxy += x * y
        self.syy += y * y

    def fit(self) -> PricedIn | None:
        if self.n < 3 or self.sxx <= 0:
            return None
        beta = self.sxy / self.sxx
        sse = max(self.syy - 2 * beta * self.sxy + beta * beta * self.sxx, 0.0)
        s2 = sse / (self.n - 1)
        return PricedIn(beta=beta, se=math.sqrt(s2 / self.sxx), n=self.n)


def fit_priced_in(pairs: list[tuple[float, float]]) -> PricedIn | None:
    rf = RunningFit()
    for x, y in pairs:
        rf.add(x, y)
    return rf.fit()


# ------------------------------------------------------------------------------- decisions

FADE, ADD, FLATTEN, NOTHING = "FADE", "ADD", "FLATTEN", "DO NOTHING"


@dataclass(frozen=True)
class GateResult:
    gate: int
    name: str
    passed: bool
    detail: str


@dataclass(frozen=True)
class Decision:
    stance: str  # FADE (against the void move: over-priced) | ADD (with it: under-priced) | DO NOTHING (FLATTEN is the post-open exit)
    direction: int  # +1 long, -1 short, 0 none
    void_bps: float
    implied_cash_bps: float | None  # ESTIMATED: beta_hat * void move (past data only)
    cost_bps: float
    expected_edge_bps: float | None  # edge in the direction this strategy trades: fade -> (1-beta)|R|; residual -> |resid|
    net_residual_bps: float | None  # expected edge minus cost
    binding_gate: int | None  # first failed gate, None if traded
    gates: tuple[GateResult, ...]
    explanation: str
    model: PricedIn | None = field(default=None, compare=False)
    residual_bps: float | None = None  # signed: implied cash gap minus printed void move (ESTIMATED)

    @property
    def traded(self) -> bool:
        return self.direction != 0

    @property
    def side(self) -> str | None:
        return None if self.direction == 0 else ("BUY" if self.direction > 0 else "SELL/SHORT")


def total_cost_bps(view: PreOpenView, cfg: Config) -> float:
    return cfg.rt_cost_bps + view.spread_bps + cfg.slippage_bps


def min_move_bps(view: PreOpenView, cfg: Config) -> float:
    return cfg.min_move_cost_multiple * total_cost_bps(view, cfg)


def is_large_move(view: PreOpenView, cfg: Config) -> bool:
    return abs(view.void_bps) >= min_move_bps(view, cfg)


def _stance_for(direction: int, void_bps: float) -> str:
    """FADE = trade against the void move (over-priced); ADD = join it (under-priced). The side (buy/sell) is separate."""
    if direction == 0:
        return NOTHING
    return FADE if direction * void_bps < 0 else ADD


def _gates(view: PreOpenView, cfg: Config, est: PricedIn | None, *, full: bool, now_utc: dt.datetime | None,
           two_sided: bool = False):
    """Evaluate the gates. full=False evaluates only those the naive baseline applies (1,2,3,8).
    two_sided=True is the exploratory variant that may also join an under-priced move (beta > 1)."""
    cost = total_cost_bps(view, cfg)
    g: list[GateResult] = []

    status = cal.status_at(view.decision_utc)
    g.append(GateResult(1, "Cash market closed", status is not cal.SessionStatus.REGULAR,
                        f"session status at decision: {status.value}"))
    g.append(GateResult(2, "Allow-listed ticker", view.allowlisted,
                        f"{view.rtoken} {'is' if view.allowlisted else 'is NOT'} in data/allowlist.json"))
    thr = min_move_bps(view, cfg)
    g.append(GateResult(3, "Minimum move", abs(view.void_bps) >= thr,
                        f"|void move| {abs(view.void_bps):.1f} bps vs threshold {thr:.1f} bps "
                        f"({cfg.min_move_cost_multiple:g} x cost {cost:.1f} bps)"))
    if full:
        g.append(GateResult(4, "Costs priced", math.isfinite(cost),
                            f"{cfg.rt_cost_bps:g} bps round-trip haircut + {view.spread_bps:g} bps spread "
                            f"({view.spread_source}) + {cfg.slippage_bps:g} bps slippage = {cost:.1f} bps"))
        if est is None or est.n < cfg.min_train_obs:
            g.append(GateResult(5, "Expected edge >= 2x cost", False,
                                f"edge cannot be established honestly: {0 if est is None else est.n} past large-move "
                                f"sessions, need {cfg.min_train_obs}"))
        else:
            resid = (est.beta - 1.0) * view.void_bps  # signed unpriced move implied at the open (bps)
            if two_sided:
                edge, tstat, need_t = abs(resid), abs(est.t_overshoot), cfg.min_tstat_two_sided
                ok = tstat >= need_t and edge >= cfg.edge_cost_multiple * cost
            else:
                edge, tstat, need_t = (1.0 - est.beta) * abs(view.void_bps), est.t_overshoot, cfg.min_tstat
                ok = est.beta < 1.0 and tstat >= need_t and edge >= cfg.edge_cost_multiple * cost
            g.append(GateResult(5, "Expected edge >= 2x cost", ok,
                                f"priced-in beta {est.beta:.2f} (n={est.n}); implied unpriced move {resid:+.1f} bps; "
                                f"significance t={tstat:.2f} (need >= {need_t:g}); expected edge {edge:.1f} bps vs required "
                                f"{cfg.edge_cost_multiple * cost:.1f} bps"))
        if now_utc is None:
            stale = view.staleness_min
            where = "last bar before decision"
        else:
            stale = max(view.staleness_min, (now_utc - view.decision_utc).total_seconds() / 60)
            where = "live clock"
        g.append(GateResult(6, "Data fresh (<= 15 min)", stale <= cfg.stale_minutes,
                            f"price age {stale:.0f} min ({where}), limit {cfg.stale_minutes:g}"))
        g.append(GateResult(7, "Position cap", True, f"single-name notional capped at {cfg.max_name_pct:.0%} of book"))
    # Gate 8: the window itself must be a genuine close->open void (no regular-session decision).
    ok8 = status is not cal.SessionStatus.REGULAR and view.void_hours > 0
    g.append(GateResult(8, "Valid void session", ok8, f"{view.kind} void of {view.void_hours:.1f} h"))
    return g


def explain(view: PreOpenView, dec: "Decision") -> str:
    """Deterministic, number-driven explanation. The LLM (if enabled) never writes this."""
    move = f"{view.void_bps:+.0f} bps"
    head = f"{view.rtoken} moved {move} while U.S. cash was closed ({view.kind} void, {view.void_hours:.1f} h)."
    if dec.traded:
        if dec.expected_edge_bps is None:  # baseline B: no edge model is consulted
            return (f"{head} Baseline B (ALWAYS FADE) trades every move above the {min_move_bps(view, Config()):.0f} bps "
                    f"threshold without testing for edge: {dec.side.lower()}, exit just before the cash open.")
        rel = "more" if dec.residual_bps * view.void_bps > 0 else "less"
        return (
            f"{head} Historical reference ({dec.model.n} past large-move sessions, {dec.model.beta:.2f}x cash-gap response) "
            f"implies a cash gap of {dec.implied_cash_bps:+.0f} bps: {abs(dec.residual_bps):.0f} bps {rel} than the rToken has printed. "
            f"Expected edge {dec.expected_edge_bps:.0f} bps vs {dec.cost_bps:.0f} bps cost (need >= 2x): "
            f"net residual {dec.net_residual_bps:+.0f} bps. Stance {dec.stance}: {dec.side.lower()} now, flatten just before the cash open."
        )
    if dec.binding_gate is None:
        return "No stance."
    failed = next(g for g in dec.gates if g.gate == dec.binding_gate)
    tail = ""
    if dec.residual_bps is not None:
        tail = (f" Historical reference: {dec.model.n} past large-move sessions, {dec.model.beta:.2f}x cash-gap response, "
                f"implies the cash gap at {dec.implied_cash_bps:+.0f} bps, a residual of "
                f"{dec.residual_bps:+.0f} bps against {dec.cost_bps:.0f} bps cost.")
    return (f"{head} DO NOTHING - Gate {failed.gate} ({failed.name}) failed: {failed.detail}.{tail} "
            f"Rift24 does not invent a position when the edge does not survive the gates.")


def _build(view: PreOpenView, cfg: Config, est: PricedIn | None, gates: list[GateResult], *, mode: str = "fade") -> Decision:
    """mode: fade (signal = -R) | residual (sign of model residual, exploratory) | follow (signal = +R, baseline C)."""
    cost = total_cost_bps(view, cfg)
    failed = [g for g in gates if not g.passed]
    binding = failed[0].gate if failed else None
    edge = implied = net = resid = None
    if est is not None and est.n >= cfg.min_train_obs:
        implied = est.beta * view.void_bps
        resid = implied - view.void_bps  # signed; opposes the void move when the rToken over-shot
        edge = abs(resid) if mode == "residual" else -math.copysign(1.0, view.void_bps) * resid
        net = edge - cost
    if failed or view.void_bps == 0:
        direction = 0
    elif mode == "residual":
        direction = 1 if resid > 0 else -1
    elif mode == "follow":
        direction = 1 if view.void_bps > 0 else -1  # signal = +R_void
    else:
        direction = -1 if view.void_bps > 0 else 1  # signal = -R_void
    d = Decision(
        stance=_stance_for(direction, view.void_bps),
        direction=direction,
        void_bps=view.void_bps,
        implied_cash_bps=implied,
        cost_bps=cost,
        expected_edge_bps=edge,
        net_residual_bps=net,
        binding_gate=binding,
        gates=tuple(gates),
        explanation="",
        model=est,
        residual_bps=resid,
    )
    return replace(d, explanation=explain(view, d))


def decide(view: PreOpenView, est: PricedIn | None, cfg: Config, now_utc: dt.datetime | None = None) -> Decision:
    """Rift24 (pre-specified): signal = -R_void, but only through all eight gates."""
    return _build(view, cfg, est, _gates(view, cfg, est, full=True, now_utc=now_utc))


def decide_residual(view: PreOpenView, est: PricedIn | None, cfg: Config, now_utc: dt.datetime | None = None) -> Decision:
    """EXPLORATORY: same eight gates, but trades the sign of the model residual (FADE if over-priced, ADD if under-priced)."""
    return _build(view, cfg, est, _gates(view, cfg, est, full=True, now_utc=now_utc, two_sided=True), mode="residual")


def decide_always_fade(view: PreOpenView, est: PricedIn | None, cfg: Config) -> Decision:
    """Baseline B: fade every eligible large void move (gates 1, 2, 3, 8 only; no edge/freshness gate)."""
    return _build(view, cfg, est, _gates(view, cfg, est, full=False, now_utc=None))


def decide_always_follow(view: PreOpenView, est: PricedIn | None, cfg: Config) -> Decision:
    """Baseline C (exploratory study only): join every large void move, no edge test. The naive mirror of baseline B."""
    return _build(view, cfg, est, _gates(view, cfg, est, full=False, now_utc=None), mode="follow")


def do_nothing(view: PreOpenView, cfg: Config) -> Decision:
    """Baseline A."""
    return Decision(NOTHING, 0, view.void_bps, None, total_cost_bps(view, cfg), None, None, None, (),
                    "Baseline A: never trade.")


# ------------------------------------------------------------------------------- autopsy


def autopsy(view: PreOpenView, out: Outcome, dec: Decision, cfg: Config) -> dict:
    """After the open is known: was the void move justified? Uses R_cash - allowed here, never in decide()."""
    r, c = view.void_bps, out.cash_bps
    residual = c - r  # cash move still unpriced at decision time (bps); negative = rToken over-shot
    cost = total_cost_bps(view, cfg)
    ratio = (r / c) if abs(c) >= 5.0 else None  # unstable when the cash gap is ~0
    if abs(residual) < cost:
        verdict = "FAIRLY PRICED (residual inside cost)"
    elif (r > 0 and residual < 0) or (r < 0 and residual > 0):
        verdict = "OVER-PRICED (rToken over-shot the cash gap)"
    else:
        verdict = "UNDER-PRICED (cash gap went further than the rToken)"
    d = {
        "void_move_bps": round(r, 2),
        "realized_cash_gap_bps": round(c, 2),
        "priced_in_ratio": None if ratio is None else round(ratio, 3),
        "realized_residual_bps": round(residual, 2),
        "verdict": verdict,
        "hold_return_bps": round(out.hold_ret * 1e4, 2),
    }
    if dec.traded:
        gross = dec.direction * out.hold_ret * 1e4
        d["gross_bps"] = round(gross, 2)
        d["net_bps"] = round(gross - dec.cost_bps, 2)
    return d
