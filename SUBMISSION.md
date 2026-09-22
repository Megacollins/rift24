# Rift24 — hackathon submission copy

Copy-paste sections for the Bitget AI Base Camp Hackathon S2 form. **Paper/backtest only. No live-money trading.**
Track: **Alpha Factory — Quantitative Strategies** · Sub-theme: **After-Hours Information Pricing**.
Every figure below is generated from `reports/backtest.json` by `python -m scripts.render_docs`; nothing is typed by hand.

---

## 1. Thesis  *(alpha source: signal / spread logic)*

U.S. cash equities are shut for about 17.5 hours overnight and 65 hours on weekends, but Bitget's rTokens trade 24/7. Information that arrives in that gap is priced by the rToken first; the cash market only prints it at the next 09:30 ET open. Rift24 measures the **void move** (rToken vs the prior cash close), asks how much of the eventual cash gap that move already contains (the **priced-in ratio**, learned only from past sessions), and computes a **residual** = model-implied cash gap − move already printed. It acts only if the expected edge is at least 2× a transparent round-trip cost (8 bps taker-style haircut + 4 bps after-hours spread haircut, both ESTIMATED), on an allow-listed rToken, while cash is closed, with data fresher than 15 minutes, capped at 20% of book per name. Otherwise the answer is **DO NOTHING** — and stand-down is part of the strategy, counted in every metric.

The pre-specified hypothesis (rTokens over-move, so fade them) is **rejected** by the data: at 09:15 ET the void move is already ≈ fully priced (β = 1.03), fading earns -3.7 bps gross and -15.7 bps net per trade, and Rift24 correctly stands down on 100% of sessions. An exploratory two-sided residual rule at 04:00 ET looks positive (OOS Sharpe 5.22 ± 3.64 on 12 trades) but was found after the null on the same sample and is presented only as a hypothesis for a forward test.

## 2. Target user

Crypto-native Bitget retail / lower VIP traders with approximately $3k–$25k capital who hold 2–10 liquid rTokens, such as rNVDA, rTSLA, rQQQ, rSPY, rMETA, rAAPL, rMSFT, rAMZN, rGOOGL and rAMD, and who sleep through the U.S. cash session rather than running a professional stat-arb book.

## 3. Validation

Labels: **OBSERVED** = read from exchange / cash data · **ESTIMATED** = depends on a modelled assumption · **TARGETED** = not yet built or measured. Sharpe = mean/std of per-session book returns × √252 (rf 0, stand-down sessions count as 0); "n/a" means undefined, never zero.

| metric | value | label | note |
|---|---|---|---|
| Data window | 2026-06-24 → 2026-09-21 (90 d) | **OBSERVED** | Bitget spot 15m candles + Yahoo daily open/close, frozen snapshot |
| Sessions / weekend voids / symbol-sessions | 62 / 13 / 620 | **OBSERVED** | 10 rTokens |
| IN-SAMPLE / OUT-OF-SAMPLE split | 60 d / 30 d | **OBSERVED** | chronological; model refit walk-forward |
| Open alignment: rToken 09:30 open vs cash open | median 1.92 bps; 87.9% within 10 bps | **OBSERVED** | two independent sources agree |
| Priced-in beta at 09:15 ET (large moves) | 1.03 [1.01, 1.05], n=497 | **OBSERVED** | 1.00 = fully priced-in; <1 would support the fade |
| Always-fade gross return / trade | -3.7 bps (t = -1.97) | **OBSERVED** | before any cost |
| Round-trip fee / spread haircut | 8 bps / 4 bps | **ESTIMATED** | exchange's own published taker fee is 20 bps round trip (OBSERVED); spread history is not public |
| B ALWAYS FADE: Sharpe IS / OOS | -13.51 / -16.02 | **ESTIMATED** | net of costs; annualised √252 |
| B ALWAYS FADE: Sortino IS / OOS | -10.66 / -11.84 | **ESTIMATED** | downside vs 0 |
| B ALWAYS FADE: max drawdown IS / OOS | 6.11% / 2.89% | **ESTIMATED** |  |
| B ALWAYS FADE: turnover IS / OOS | 82.80× / 39.60× book | **ESTIMATED** |  |
| B ALWAYS FADE: trades IS / OOS, win rate | 340 / 157, 28.8% | **ESTIMATED** |  |
| B ALWAYS FADE: OOS/IS Sharpe ratio | n/a | **ESTIMATED** | IS Sharpe -13.51 <= 0: ratio not meaningful; read OOS Sharpe directly |
| B ALWAYS FADE: rolling 30-day Sharpe (median [min, max]) | -15.25 [-21.10, -9.84] over 48 windows | **ESTIMATED** |  |
| RIFT24 (pre-specified fade): Sharpe IS / OOS | n/a / n/a | **ESTIMATED** | net of costs; annualised √252 |
| RIFT24 (pre-specified fade): Sortino IS / OOS | n/a / n/a | **ESTIMATED** | downside vs 0 |
| RIFT24 (pre-specified fade): max drawdown IS / OOS | 0.00% / 0.00% | **ESTIMATED** |  |
| RIFT24 (pre-specified fade): turnover IS / OOS | 0.00× / 0.00× book | **ESTIMATED** |  |
| RIFT24 (pre-specified fade): trades IS / OOS, win rate | 0 / 0, n/a% | **ESTIMATED** |  |
| RIFT24 (pre-specified fade): OOS/IS Sharpe ratio | n/a | **ESTIMATED** | Sharpe undefined in at least one period (no variance / no trades) |
| RIFT24 (pre-specified fade): rolling 30-day Sharpe (median [min, max]) | n/a [n/a, n/a] over 0 windows | **ESTIMATED** |  |
| Rift24 stand-down rate | 100.0% of symbol-sessions (0 traded) | **ESTIMATED** | no residual survived 2× cost |
| Trades Rift24 refused that always-fade took | 497, mean -15.7 bps net | **ESTIMATED** |  |
| POST-HOC residual @ 04:00 ET: Sharpe IS / OOS (± s.e.) | 6.88 / 5.22 (±3.64) | **ESTIMATED** | found after the null on the same sample; OOS not clean |
| POST-HOC residual: trades / distinct days / OOS trades | 47 / 21 / 12 | **ESTIMATED** | concentrated on earnings-type gap days |
| POST-HOC residual: return without best 3 days | 4.217% (Sharpe 5.495) | **ESTIMATED** |  |
| Forward test of the residual rule on untouched data | harness built + pre-registered; 0 forward trades so far | **TARGETED** | first decision 22 Sep 04:00 ET; verdict stays INCONCLUSIVE until 30 trades / 15 days |
| Observed after-hours spreads / depth | not collected | **TARGETED** | single fetch-time snapshot only; backtest uses +4 bps fallback |
| Live Bitget Signal / news-briefing event feed | not wired | **TARGETED** | event board is fixture-driven offline |

## 4. Progress — exactly what is done

- [x] Session calendar (holidays, early closes, DST) cross-checked against the trading days in the real data; 10-name allow-list with verified Bitget symbols.
- [x] `DataAdapter` with a frozen-fixture adapter (offline, default) and a live adapter over Bitget public REST + Yahoo daily; loud "LIVE DATA UNAVAILABLE / FALLING BACK TO FIXTURE DATA" fallback.
- [x] Deterministic residual engine, 8 gates, walk-forward priced-in model; look-ahead protected by types and by tests that rewrite every post-decision input.
- [x] Baselines (A do-nothing, B always-fade) and Rift24 with the full metric set; reproducible `python -m scripts.run_backtest`; `reports/backtest.json` and `reports/thesis.md`.
- [x] Static desk (session clock, event board, residual table, row explanation, backtest page, weekend replay) reading pre-rendered `data/demo_state.json`.
- [x] Rule-based event→ticker mapper; optional feature-flagged LLM behind a guard.
- [x] 110+ unit/integration tests.
- [x] Pre-registered forward-test harness (`scripts/forward_test.py`, frozen sha256-stamped model, success criterion fixed in `data/forward/PREREGISTRATION.md`) — validated by replaying 21 Sep against the live API; **no forward results yet**.
- [ ] **Not done (needs you):** the required X quote post with `#BitgetHackathon` + `@Bitget_AI`, and submitting the Google Form. Both are outward-facing actions I have not taken.
- [ ] **Not done:** a GetAgent Playbook port (would require uploading to your GetAgent account), a live Bitget Signal feed, `bitget-mcp-server` wiring (the live adapter uses the same public market data over REST), observed spread history, a forward test.

## 5. Deliverables

- **Live demo:** https://rift24.vercel.app (landing page → desk / backtest / weekend replay; static, reads the same frozen fixture as the local build).
- **Source:** https://github.com/Megacollins/rift24
- Strategy code: `rift24/` (`calendar.py`, `data.py`, `residual.py`, `backtest.py`, `metrics.py`, `events.py`, `report.py`, `thesis.py`, `demo.py`).
- Backtest: `python -m scripts.run_backtest` → `reports/backtest.json`.
- Demo: `bash scripts/serve.sh` → `app/` (Desk · Backtest · Weekend replay); pre-rendered `data/demo_state.json`.
- Fixtures: `data/fixtures/` — real, frozen Bitget 15-minute candles and Yahoo daily bars (no synthetic data), plus `events.json`.
- Reports: `reports/backtest.json`, `reports/thesis.md`.
- Tests: `python -m pytest -q`.

## 6. LLM role

The LLM is limited to **event → allow-listed ticker** plus a **short explanation paragraph**, behind `RIFT24_LLM=1` (off by default; the rule-based mapper is the default). Its output is validated: unknown tickers and any text that reads like a trading instruction (buy/sell now, price targets, sizing, probabilities) are rejected and replaced by the rule-based result. The quantitative engine never imports it. The LLM does not size, time or decide any trade and is not part of the scored signal.

## 7. One-line summary  *(≤ 140 characters)*

Rift24 measures how much overnight news 24/7 rTokens have priced before the US open, and stands down when nothing's left.

*(121 characters)*
