# agent-trader

A rules-based momentum strategy that an AI agent runs on a Robinhood account through the [Robinhood Trading MCP](https://robinhood.com/us/en/support/articles/agentic-trading-overview/). It works with any agent that can load a skill (`SKILL.md`), call MCP tools and run Python. The step-by-step trading procedure is in [skills/trade/SKILL.md](skills/trade/SKILL.md).

> **This places real orders with real money.** It is not financial advice. Start with an amount you can afford to lose, and watch the first runs yourself.

## Install

Run these from the project folder you'll open the agent in. That folder holds your config and trading data.

```bash
npx skills add yale-li/agent-trader
```

Then connect your agent to Robinhood. Follow Robinhood's guide, [Connect your AI agent](https://robinhood.com/us/en/support/articles/agentic-trading-overview/#ConnectyourAIagent), which has the steps for each AI platform:

1. Add an HTTP MCP server named `robinhood-trading` with the URL `https://agent.robinhood.com/mcp/trading`. Use your agent's MCP settings or CLI. Many clients accept this JSON:

   ```json
   { "mcpServers": { "robinhood-trading": { "type": "http", "url": "https://agent.robinhood.com/mcp/trading" } } }
   ```

   The repo's `.mcp.json` holds this config, for agents that read project MCP files.
2. Authenticate the server from your agent and sign in to Robinhood.
3. When prompted, open a **Robinhood Agentic account** and follow the on-screen steps. This is the only account the agent can trade in. It requires a primary individual investing account in good standing.

Connecting gives the agent read access to all your Robinhood accounts. That's why `config.json` names the one account it may trade.

Then run `/trade setup`. It asks for your Agentic account number and creates `config.json` plus the data folders (`state/`, `run/`, `journal/`, `reports/`). The scripts need only Python 3.9+, with nothing to `pip install`.

## Use

| Command | When | Does |
|---|---|---|
| `/trade setup` | first time, or when something looks off | checks the install; places no orders |
| `/trade check` | daily, market hours | raises/repairs stops; sells only on a halt or risk-off market; never buys |
| `/trade` | weekly, market hours, after 10:00 ET | full rebalance: sells, trims, stops, buys, adds |

`/trade <mode>` means "run the trade skill with that mode". In an agent without slash commands, just ask, e.g. "run the trade skill: check".

The first live run shows the order table and waits for your go-ahead. To stop all trading, run `python3 <skill>/scripts/strategy.py kill "reason"`; `unkill` clears it. Broker-side stops stay live either way.

## Strategy

- **Universe:** S&P 100 + Nasdaq-100, plus a crypto-ETF sleeve (IBIT, ETHA, at most 20% of equity).
- **Ranking:** score = average of 12-1 and 6-1 month momentum ÷ ATR%. Only names above their 200-day average with positive momentum qualify.
- **Market filter:** when SPY is more than 3% below its 200-day average, sell all stocks and buy none.
- **Entries:** top-ranked names, up to 5 stocks. Each is sized so its stop costs 2% of equity, capped at 20% of equity, with no buys within 7 days of earnings.
- **Exits:** a stock is sold when it falls below its 200-day average or out of the top 10. Every position has a 2.5×ATR trailing stop at the broker, and stops only move up.
- **Resizing:** held positions are topped up to target size and trimmed when they outgrow their cap (`resize` in `config.json`).
- **Safety:** no buys after a 2% daily loss or a 10% drawdown (the drawdown halt stays until you reset it), and a kill switch you control.

All parameters are in `config.json`. Background research is in [AI_AGENT_TRADING_STRATEGIES.md](AI_AGENT_TRADING_STRATEGIES.md).

## For agents changing this repo

**Rules**
- Trade only the `account_number` in `config.json`, and never pick or guess one.
- The engine (`strategy.py`) decides every trade. You may only veto or shrink a buy.
- Never clear `kill` or `halted`.
- Keep the data private. `.gitignore` covers `config.json`, `state/`, `run/`, `journal/` and `reports/`.

**Layout**

```
skills/trade/SKILL.md            runbook: /trade setup | check | rebalance
skills/trade/config.example.json starting config
skills/trade/scripts/strategy.py rules engine: plan | check | kill | unkill
skills/trade/scripts/report.py   HTML report from the journal
skills/trade/scripts/setup.py    init | doctor (JSON output)
skills/trade/scripts/datadir.py  finds the data folder
```

**Data folder:** `$AGENT_TRADER_HOME` if set, else the current folder if it has `config.json`, else the git root of the current folder.

**Testing a change.** `strategy.py plan` writes `state/` and `run/`, so never test it against live data. Use a sandbox:

```bash
SANDBOX=$(mktemp -d)
python3 skills/trade/scripts/setup.py init --account TEST --dir "$SANDBOX"
mkdir -p "$SANDBOX/run/test" && cp run/<date>/input.json "$SANDBOX/run/test/"
AGENT_TRADER_HOME="$SANDBOX" python3 skills/trade/scripts/strategy.py plan run/test/input.json
```

With the new feature off, the orders must match the original `plan.json`.

**Also update:**
- the `strategy.py` docstring;
- SKILL.md;
- `config.example.json`;
- `OPTIONAL_KEYS` in `setup.py`, for new config keys. Read them with `.get`.

Tell the user before changing the live `config.json`.

**Data pitfalls**
- Scanner history isn't split-adjusted. Flagged rows must be re-fetched with split-adjusted indicators (SKILL.md 2c).
- `preview_scan` returns at most 200 rows, so a larger universe must be paged.
- Buyout targets have near-zero volatility and rank artificially high. The news overlay must veto them.

## License

[MIT](LICENSE). Provided as is, without warranty; you are responsible for every order it places on your account.
