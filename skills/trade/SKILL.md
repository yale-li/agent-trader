---
name: trade
description: Run the agent-trader momentum strategy on a Robinhood account through the Robinhood MCP. "/trade setup" = first-time install check and data dir; "/trade" or "/trade rebalance" = weekly rebalance (live orders); "/trade check" = daily risk check (stops, halts, no new buys). Use when the user asks to set up or install agent-trader, run the strategy, rebalance, or check positions/stops.
---

# Trade agent runbook

Live money. Trade only the account in `config.json` `account_number` (called `<account>` below). Never touch any other account.
The rules engine (`strategy.py`, parameters in `config.json`) decides; you fetch data, run the engine, apply the news overlay, execute, and log.

**Paths.** The scripts ship with this skill: `<skill>` below means this skill's base directory, so the engine is `python3 <skill>/scripts/strategy.py`. Everything else (`config.json`, `state/`, `run/`, `journal/`, `reports/`) lives in the data dir: `$AGENT_TRADER_HOME` if set, else the current working directory if it has a `config.json`, else the project root (the git top-level of the working directory, or the working directory itself). By default that is the folder the user runs the agent in. Never put it inside the skill folder (reinstalling the skill replaces that folder). `python3 <skill>/scripts/setup.py doctor` prints the resolved data dir and any problems as JSON. Every relative path below is relative to the data dir: read and write files there, and pass the scripts paths relative to it (they resolve them against the data dir from any directory).
If the data dir has no `config.json`, run `/trade setup` (below) instead of trading. Never guess an account.
**You may veto or shrink a BUY. You may never add a trade, increase size, widen/remove a stop, or override a halt.**

**First, read `state/state.json`. If `kill` is true, stop right there: place, cancel or modify nothing, and tell the user the kill switch is on (with `kill_reason`).** Only the user clears it (`python3 <skill>/scripts/strategy.py unkill`).

Run only during regular hours (9:30–16:00 ET, best after 10:00 ET). If the market is closed, stop and say so.
On the first live run (empty `journal/trades.jsonl`), show the order table and wait for the user's go-ahead before placing anything.

## 1. Gather state (in parallel)
- `get_portfolio(<account>)` → `equity` = total_value, `cash` = buying_power.buying_power
- `get_equity_positions(<account>)`
- `get_equity_orders(<account>, state="confirmed")` (also `queued`) → open stop orders; map each position to its `stop_price` (null if none)
- `prev_close_equity`: last row of `journal/equity.csv` (if none, current equity)
- **Cash flows:** if cash moved by more than fills and stops explain (deposit or withdrawal), don't count it as P&L. Pass `equity` and `cash` without the flow, so the daily-loss check sees the real move. Ask the user to confirm the flow. Once they do, add `date,amount,note` to `journal/flows.csv`, move `state.json` `peak_equity` by the same amount, and log the full account equity in `equity.csv`. `report.py` builds a time-weighted NAV from these files.

## 2. Universe scan (one call)
`preview_scan` with filter `{"expression":"symbol","predicate":"PREDICATE_IN_LIST","values":<config universe_indexes>}` (currently `["SP100","NDX"]`; no 200DMA filter, so held names that broke down still show) and columns:
| display_name | expression |
|---|---|
| SMA200 | `closeAvg(candleCount=200, candlePeriod="1d", session="regular")` |
| Mom 12-1 | `close(candleCount=21, candlePeriod="1d", session="regular") / close(candleCount=252, candlePeriod="1d", session="regular") - 1` |
| Mom 6-1 | `close(candleCount=21, candlePeriod="1d", session="regular") / close(candleCount=126, candlePeriod="1d", session="regular") - 1` |
| ATR14 | `atr(candleCount=14, candlePeriod="1d")` |
| Earnings | `fundamental.earningsYmd` |

Second `preview_scan`: `symbol ANY_OF ["SPY"]` with the SMA200 column (regime check).
Use the `Last` column as price. If a held symbol is missing, scan it with `symbol ANY_OF`.

**Scanner history is NOT split-adjusted** (ETHA's SMA200 came back 18.40 vs the true 50.59). So:

### 2b. Crypto sleeve data (config `crypto.symbols`, currently IBIT + ETHA) — never from the scanner
`get_equity_technical_indicators` (split-adjusted by default), `interval="day"`, `start_time` ≈ 6 weeks ago:
- `sma` period 200, output `latest` → sma200
- `atr` period 14, output `latest` → atr
- `roc` period 105, output `last:22` → **first** value ÷ 100 = mom6 (6-1 momentum)
- `roc` period 231, output `last:22` → **first** value ÷ 100 = mom12
Price = scanner `Last` or `get_equity_quotes`. Put them in input.json `crypto`.

### 2c. Verify flagged stocks
The engine excludes rows that look like split artifacts (price/SMA200 outside 0.5–2, or mom12 > 300%). For any such row that would otherwise rank in the top 10 or is held, fetch the same four indicators as 2b, replace the row's values, set `"verified": true`, and re-run.

## 3. Run the engine
Write `run/<YYYY-MM-DD>/input.json` (schema in the `strategy.py` docstring; every universe row, numbers as floats; earnings_ymd as an 8-digit string), then:
`python3 <skill>/scripts/strategy.py plan run/<date>/input.json` → writes `plan.json`.

## 4. News overlay (BUY and ADD candidates only)
For each BUY or ADD: search the web for news from the last ~7 days, using whatever web search tool your agent has. If it has none, say so and treat the overlay as not run; never invent news. Veto only on concrete adverse events confirmed by 2+ sources: fraud/accounting probe, guidance cut, pending acquisition (price pinned), trading halt, major lawsuit/regulatory action, CEO exit under a cloud. Shrink to 0.5 for elevated but unclear risk. Rumors, single-source items, "overbought" → no action.
If anything is vetoed/shrunk: add `vetoes`/`shrinks` to input.json and re-run the engine. Record the reason.

## 5. Show the plan, then execute
Show the order table (action, symbol, qty, est. cost, stop, reason) plus notes, then execute in this order:
1. **SELL**: cancel that symbol's stop (`cancel_equity_order`), then `review_equity_order` → `place_equity_order` market sell, full quantity, regular_hours.
   **TRIM** (partial sell): same, but sell only the order's `quantity`; once filled, place a `stop_market` gtc sell for `remaining` shares at the order's stop_price (skip if `remaining` is 0).
2. **RAISE_STOP / SET_STOP**: cancel the old stop (if any), place `stop_market` sell, `time_in_force="gtc"`, at the new stop_price.
3. **BUY**: `get_equity_quotes` → `review_equity_order` limit buy at the current ask (gfd, whole shares) → `place_equity_order` with a fresh UUID `ref_id`. Confirm the fill with `get_equity_orders(order_id=…)`. **Immediately** place the protective `stop_market` gtc sell for the filled quantity at the plan's stop_price (if the fill differs from ref_price by >1%, use fill_price − stop_atr_mult×ATR). If not filled within ~2 min, cancel and skip.
4. **ADD** (top up a held position; after all BUYs): limit buy `quantity` exactly like a BUY. Once filled, cancel the symbol's current stop and place one `stop_market` gtc sell for the **whole position** (held + filled) at the order's stop_price — never lower it, even if the fill is off ref_price. Partial or no fill: cancel the rest and re-place the stop for whatever is now held.
If a review returns alerts, stop and report — do not force.

## 6. Audit and log
- Re-fetch positions + open orders, write `run/<date>/post.json` (`equity`, `positions` with stop_price, `universe`), run `python3 <skill>/scripts/strategy.py check run/<date>/post.json`. Any problem (especially NO STOP) → fix immediately or tell the user.
- Append one JSON line per executed order to `journal/trades.jsonl`: `{ts, action, symbol, qty, fill_price, stop_price, order_id, stop_order_id, reason, overlay_note}`.
- Append `date,equity,spy` (SPY last price) to `journal/equity.csv` — one row per date; replace the row if today's already exists.
- Run `python3 <skill>/scripts/report.py` → `reports/report-<date>.html` and `reports/latest.html`; include the path in the summary.
- Summarise: trades done, holdings with stops, equity vs SPY since start, and any 偏离 / 未执行 / 计划外 rows from the report's execution audit.
- Journal only orders that are in this run's final `plan.json` (after any overlay re-run). If you had to place something else (e.g. repairing a missing stop found by `check`), re-run the engine first so the plan contains it, or say so in the summary.

## `/trade setup` (first time, or when anything looks off)
Places no orders and may run at any hour.
1. `python3 <skill>/scripts/setup.py doctor`. If `ok` is true, skip to step 3.
2. No `config.json`: ask the user for the number of their Robinhood Agentic account, which is the only account an agent can trade in. Never take one from `get_accounts` on your own; you may list accounts so they can choose. Then run `python3 <skill>/scripts/setup.py init --account <number>`, adding `--dir <path>` only if they want a specific folder. Fix every item in `problems`. Show `warnings`, and follow `next_steps` (e.g. set `AGENT_TRADER_HOME` when the data dir is not one the scripts find on their own).
3. Check the Robinhood MCP: call `get_accounts`. If the `robinhood-trading` tools are missing or unauthenticated, point the user to Robinhood's guide (https://robinhood.com/us/en/support/articles/agentic-trading-overview/#ConnectyourAIagent), which has the steps for each AI platform. In general:
   - add an HTTP MCP server named `robinhood-trading` with the URL `https://agent.robinhood.com/mcp/trading` (the repo's `.mcp.json` has the config);
   - authenticate it from the agent's MCP settings;
   - open the Robinhood Agentic account when prompted.

   Never handle their Robinhood credentials. If the configured account is absent or not `agentic_allowed`, say so: it must be the Agentic account. If the connection breaks later, Robinhood's fix is to disconnect and reconnect the MCP.
4. Report the data dir, the account, and that the next step is `/trade check` during market hours.

## `/trade check` (daily)
Steps 1–3 plus the step 6 audit. Execute **only** stop repairs/raises and sells caused by a halt or risk-off regime; never BUY, ADD or TRIM (those wait for the weekly rebalance). Report.

## Halt / kill switch
- `halted: true` (set automatically by the drawdown breaker) blocks buys until the user resets it.
- `kill: true` (set by the user: `python3 <skill>/scripts/strategy.py kill [reason]`) blocks **every** action; the engine also emits no orders. Broker-side stops stay live.
- Never reset either yourself. For a stop that does not depend on this agent at all, the user can turn on trade approval in Robinhood or revoke the account's agentic access.
