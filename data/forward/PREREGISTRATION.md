# Pre-registration — forward test of the 04:00 ET residual rule

**Registered:** 2026-09-21 (before any forward observation exists). **Paper only. No live-money trading.**

## Why this exists

The 04:00 ET result in `reports/thesis.md` §6 was found *after* the pre-specified fade test failed, on the same sample, with the
decision time chosen from a 5-point grid. That is a hypothesis, not evidence. This file fixes the rule, the sample and the success
criterion **before** any new data arrives, so the outcome cannot be argued into shape afterwards.

## The frozen rule (nothing below may change; a change is a new registered version)

| item | value |
|---|---|
| Rule | `RIFT24_RESIDUAL` (two-sided; FADE if the model says over-priced, ADD if under-priced) |
| Decision time | 04:00 ET on every regular trading day (incl. the void that follows a weekend) |
| Price used | last rToken 15-minute bar close ≤ decision time; base = rToken price at the prior cash close |
| Entry / exit | entry = open of the 15-minute bar starting at the decision time; exit = close of the last bar before the 09:30 ET open. **No position touches the regular session.** |
| Model | pooled through-origin `R_cash = β·R_void` on large moves; **β, se, n frozen in `frozen_model.json`** (sha256-stamped, fitted only on the fixtures through 2026-09-21). **Never refit on forward data.** |
| Gates | the eight gates of `rift24/residual.py` at the frozen `Config` (8 bps fee + 4 bps spread haircut, gate 3 = 2× cost, gate 5 = edge ≥ 2× cost with two-sided t ≥ 1.96, ≤ 20% per name, unlevered, staleness ≤ 15 min) |
| Cost used for the verdict | the assumed 12 bps (so it is comparable with the backtest). Observed spreads are logged and reported alongside, not substituted. |
| Universe | the 10 allow-listed rTokens in `data/allowlist.json` |

## How it is run

```
python -m scripts.forward_test snapshot   # ~04:02 ET, Mon-Fri  -> data/forward/log.jsonl      (append-only)
python -m scripts.forward_test settle     # after 09:35 ET      -> data/forward/outcomes.jsonl (append-only)
python -m scripts.forward_test report     # running summary and the verdict below
```

Missed days are simply missing (never back-filled): a snapshot taken later than 04:15 ET trips the freshness gate and is logged as
a stand-down. Rows are never edited or deleted.

## Success criterion (decided now)

Evaluate **once**, when both are true: **≥ 30 settled trades** and **≥ 15 distinct trade days** (or after 12 weeks, whichever
comes first — in which case the result is reported as underpowered if the minimums are not met).

* **SUPPORTED** only if the mean net return per trade (assumed 12 bps cost) is **> 0** *and* the day-level (clustered) one-sided
  t-statistic is **≥ 1.645**.
* Otherwise **NOT SUPPORTED**. Below the minimums the verdict is **INCONCLUSIVE** and no conclusion may be drawn.

Also reported, not gating: win rate, total return on book, observed-vs-assumed spread, stand-down rate, and the BUY-only view
(spot rTokens cannot be shorted).

## Known weaknesses of this test (stated in advance)

* ~1 trade every 1–2 sessions ⇒ about 8 weeks to reach the minimum; regime change in that time is possible.
* Trades cluster on earnings-type gap days; a quiet period may produce almost no trades.
* The 04:00 ET entry is into a thin book: observed spread at that hour is logged, but depth and slippage for real size are not.
* Yahoo's daily open is used for the cash gap; the rToken's 09:30 bar open matched it to a median 1.9 bps in the backtest sample.
