# Rift24 — 3-minute demo script

Record a **desktop window ≥ 1380 px wide** (the desk shows table + explanation side by side only from 1380 px; narrower windows use the
stacked layout, which is fine but changes the shots below). Start the desk with `bash scripts/serve.sh` → <http://localhost:5210/app/>.
Open on the **Desk** tab, decision time **04:00 ET** (the default).

**Frame the story honestly.** The headline is *not* "we found alpha". It is: *"We tested the obvious idea, the data said no, and Rift24
is built to say no."* The 04:00 ET result is a hypothesis with a pre-registered forward test.

| time | on screen | say |
|---|---|---|
| 0:00–0:20 | **Desk**, clock band: **U.S. CASH MARKET · CLOSED** / **rTOKENS · TRADING 24/7** | "The U.S. market is closed, but these tokenized stocks are still trading. Rift24 asks whether the move that happened while cash was closed has already priced the information." |
| 0:20–1:00 | Event board (point at the **SOURCED** Meta / AMD items), then the **DO NOTHING 8/10** banner and the residual table | "Headlines map to allow-listed tickers with a rule-based mapper — no LLM in the decision. Look at rTSLA: up 116 bps overnight, looks tempting. Rift24 says **DO NOTHING**." |
| 1:00–1:30 | Click **rTSLA** → detail panel: void move, cost, residual, stance, gates | "The model, fitted only on past sessions, expects a 19 bps residual. The cost is 12 bps and the rule needs twice that, 24. Gate 5 fails. The math is deterministic; nothing here is an LLM." Then click **rAMD** to show one that *does* pass. |
| 1:30–2:10 | **Backtest** tab: the red **H1 REJECTED** box, the IS/OOS timeline, the baseline table, β chart, stand-down | "We tested the obvious hypothesis first — fade big overnight moves. 60 days in-sample, 30 out-of-sample, real Bitget and Yahoo data. It loses after costs and before costs: the move is already priced, beta 1.03. Rift24 stands down on every session. That's the result, and the metrics table shows it — Sharpe, Sortino, drawdown, OOS/IS ratio, rolling Sharpe, stand-down rate." |
| 2:10–2:45 | **Weekend replay** → **Run replay** (about 12 s) | "Friday close. Weekend. rTokens move. Rift24 decides at 04:00 with the future hidden. Then Monday's open. rAMD: +212 bps. rMETA: **minus 29** — I'm showing both." Let the outcome table and "all 13 weekends" bars sit for a beat. |
| 2:45–3:00 | Back to **Backtest**, scroll to the amber **POST-HOC** band | "Rift24 isn't trying to trade every headline. It asks how much has already been priced. When there's no residual after costs, it does nothing. We found one hypothesis worth testing — and pre-registered a forward test rather than claim it." |

## Do not say
* "Rift24 is profitable" / "we found an edge" — the 04:00 ET rule is post-hoc, 12 out-of-sample trades, Sharpe ± 3.6.
* "The headline caused the move" — sourced items are context only; their timing vs the decision was not verified.
* Anything about the spread being observed in the backtest — it is an assumed +4 bps (ESTIMATED).

## Pre-flight (30 seconds)
```bash
python -m pytest -q                    # all green
python -m scripts.run_backtest         # reproduces reports/ byte-for-byte
```
