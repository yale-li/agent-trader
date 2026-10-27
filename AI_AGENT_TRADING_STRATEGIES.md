# Trading Strategies for AI Agents — Research Notes

*Compiled 2026-10-15. Not financial advice. Most figures come from short live windows (2–9 weeks) and preprints; treat them as directional.*

---

## 1. TL;DR

1. **Most LLM agents that trade on their own judgment lose money live.** In Alpha Arena's US-stock season, models were profitable in only 6 of 32 result sets, and the pool lost about one-third of its capital. In a six-month production fleet, only 15% of agent accounts were net positive, compared with 53% of matched human retail traders.
2. **Architecture and guardrails matter more than which model you use.** Swapping the LLM inside a fixed agent design changed results only modestly. Changing the design, especially risk controls, changed them a lot. Three frontier models were statistically indistinguishable on decision regret, while their costs differed by about 25×.
3. **What has evidence behind it:**
   - mechanical exits (a stop and a target attached when the order opens)
   - position sizing that scales with volatility
   - low trading frequency and restraint
   - concrete numeric rules rather than vague prose
   - reading fresh news and filings (8-Ks), not stale fundamentals
4. **What reliably hurts:**
   - leverage that ignores volatility
   - chasing whatever is trending on a leaderboard or "movers" list
   - overtrading
   - herding
   - trusting a single unverified news item
   - stateless agents repeating the same mistake
   - "memory" that drifts into fabricated beliefs
5. **Recommended pattern:** a deterministic, rules-based strategy owns entries, exits and sizing. The LLM acts as an analyst or veto layer (reads news and filings, flags events, can only reduce risk). Hard risk limits are enforced in code, not in the prompt.

---

## 2. What the live evidence says

### 2.1 Alpha Arena (nof1): real money, frontier models
- **Season 1** (crypto perps, Oct–Nov 2025, $10k each): Qwen3-Max +22% and DeepSeek about +5%. Claude Sonnet 4.5, Gemini 2.5 Pro, Grok 4 and GPT-5 lost 42–59%. Commentators credited the winners' lower trade frequency and steadier strategies. The losers suffered from heavy leverage and frequently flipping strategy.
- **Season 1.5** (US equities, 8 models, 4 parallel modes: *Monk* = capital preservation, *Situational Awareness* = sees rivals' rankings, *Max Leverage*, plus baseline): Grok 4.20 won, with about +12% over two weeks. It placed **158 trades, against 1,418 for Qwen** under the same prompt. Overall, only 6 of 32 result sets were profitable.
- **Lesson:** trading less and preserving capital beat activity. A two-week ranking is mostly noise.

### 2.2 "What LLM Trading Agents Actually Do in Production" (arXiv 2609.05663, 2026)
This paper covers about 3,500 vaults trading memecoins plus a fleet of about 500 agents trading Hyperliquid perps over six months. It is the most actionable source here.

| Finding | Number |
|---|---|
| Agents chased momentum: they entered after +0.75% hourly moves 2.46× as often as baseline | Forward 4h return of those entries: **−6.9 bps** |
| Selection followed the UI: entries in symbols shown on a "movers" leaderboard | 46.5%, vs an 8.9% random baseline |
| Herding: vaults buying the same token within one hour | 1,544 of 3,454 |
| Leverage ignored volatility: returns fell as volatility rose | −10.6 bps in calm markets → −98.2 bps in wild ones |
| **Fixed 2% stop / 4% target attached at entry** | **+39 bps per position** (95% CI +21 to +57); about 60% of the gain came from avoiding liquidation |
| Upside not captured: positions that reached +3% but still closed red | 49.3% |
| Asking the agent to *state* its liquidation distance | No change in sizing; those agents liquidated *more* (5.8% vs 1.2%) |
| Owners who gave numeric instructions | Profitable 4.2× as often as the median |
| Memory-write frequency vs P&L | ρ = −0.20; agents also traded on deleted strategies and fabricated income |
| Win rate vs human retail | 41% vs 50%; agents were long 78% of the time at 4–5× retail's leverage |

**The authors' recommendations:**
1. Attach the stop and target *atomically* when the order opens.
2. Scale leverage and size in the tool layer, using realized volatility.
3. Treat what the agent sees (leaderboards, screens) as a design variable.
4. Prefer mechanisms over prompt wording.
5. Run a post-order risk check and log the provenance of every decision.
6. Remove tools that don't change behavior. Their market-research sub-agent added nothing.

### 2.3 AI-Trader benchmark (arXiv 2512.10971)
- Six LLMs ran in the same scaffold across Nasdaq-100 (hourly), China A-shares and crypto.
- No agent beat the index in A-shares, and only one beat it in crypto.
- **Cross-market robustness depended on suppressing risk, not chasing returns.** MiniMax-M2 was the most consistent model and also had the smallest drawdowns.
- **Good behavior:** before a drop, DeepSeek searched the news, cut tech exposure from 99% to 70%, and held about 17% cash.
- **Failure modes:**
  - trusting a single unverified news item
  - "hold-to-die" passivity through a correction
  - rigid target weights that ignored liquidity
  - overtrading

### 2.4 Agent Market Arena / "When Agents Trade" (arXiv 2510.11695)
- Four agent architectures × five LLMs, live on TSLA, BMRN, BTC and ETH (Aug–Sep 2025).
- **No agent beat buy-and-hold on TSLA's +47%.** Some agents beat it on risk-adjusted terms or in falling markets.
- **The architecture choice moved results much more than the LLM choice.**
- Results on a single asset are unreliable, so evaluate across several markets.

### 2.5 Fin-Analyst, FinMMEval 2026 (arXiv 2607.12233)
- **Design:** eight gpt-4o-mini specialists (news, 8-K, 10-Q, 10-K, fundamentals, analyst consensus, technicals, WallStreetBets), each returning a JSON vote with a confidence score. A meta-agent aggregates them; news can override when its confidence is above 0.70, and the meta-agent follows any consensus of five or more.
- **Results:** TSLA returned +13.5% live against −14.7% for buy-and-hold. The rule-based BTC agent returned −5.3%.
- **Ablation:**
  - Removing the 8-K specialist cut 30.7 points of return, the largest drop.
  - Removing the 10-K or fundamentals specialists slightly *improved* returns, so slow data adds noise at a daily horizon.
- **Errors:**
  - The stateless agent stayed short for six days while TSLA rose 6%.
  - The fixed ±0.5% threshold was smaller than BTC's daily noise.
  - The interim and final rankings reversed.

### 2.6 LLM news-sentiment alpha (Lopez-Lira & Tang, arXiv 2304.07619, revised 2025)
- LLM-scored headlines predict next-day returns, and do so better than classic sentiment models.
- The effect is strongest in **small caps and negative news**.
- **The alpha is decaying as LLM adoption grows**, but GPT-4 scores still predict post-news drift in small caps.

### 2.7 Survey: "Agentic Quantitative Trading" (arXiv 2608.31041)
- **Pipeline stages:** factor mining → signal discovery → portfolio construction → execution → risk. Of 20 systems reviewed, only 2 treat all five stages as core.
- **Ways the LLM gets used:**
  1. **LLM-as-trader:** it issues buy/sell/hold directly. This is the most fragile option, with look-ahead leakage in backtests.
  2. **LLM improves factors and models:** it proposes factors or features (e.g. RD-Agent(Q), Alpha-GPT).
  3. **LLM writes executable strategies:** it generates rules that a backtester validates.
  4. **LLM supplies views to an optimizer:** e.g. Black–Litterman, while the optimizer sets the weights.
- **Coordination:** aggregation or voting is used by 15 of 17 multi-agent systems. Debate, selection and *gating* (typed contracts plus hard rules) are the alternatives.

---

## 3. Strategy catalogue

Each entry notes how well it fits the setup this repo targets: a small Robinhood account traded through the Robinhood MCP, with limited margin, no options level, and equities and crypto.

### A. Rules-based core + LLM overlay (recommended)
- **Core:** a classic systematic rule (B, C or D below) generates candidate trades and sizes them.
- **LLM role:**
  - read news, 8-Ks and earnings transcripts for held or candidate tickers
  - **veto or shrink** trades around adverse events (fraud, guidance cuts, lawsuits, pending M&A)
  - write a short rationale to a decision log
- **The LLM cannot:** raise size, add leverage, or open trades the rules didn't propose.
- **Why:** this keeps the parts with evidence behind them (mechanical rules and exits, fresh news reading) and removes the documented failure modes (momentum chasing, overtrading, over-leverage).

### B. Trend-following / time-series momentum
- **Rule:** hold an asset only while it is above its N-day moving average, or while its 12-month return is positive. Otherwise move to cash or T-bills.
- **Evidence:**
  - Moskowitz, Ooi & Pedersen (JFE 2012) is the academic basis.
  - An S&P 500 backtest since 2000 found the 200-day filter cut max drawdown from about 57% to about 21%.
  - Plain crossovers whipsaw; a 3% band helps.
- **Agent fit:** excellent. It is low frequency (weekly or monthly checks), simple to verify, and the LLM is barely needed.
- **Variants:** dual momentum, choosing between relative and absolute momentum across SPY, international and bond ETFs.

### C. Cross-sectional momentum / rotation
- **Rule:** rank a universe (sector ETFs, or large caps) by 6–12-month return, skipping the latest month. Hold the top K and rebalance monthly.
- **Agent fit:** good. Use `run_scan` / `get_equity_historicals` to rank.
- **Risk:** momentum crashes during sharp reversals. Pair it with B as a regime filter.

### D. Short-term mean reversion
- **Rule:** buy liquid large caps or ETFs after a sharp 1–3-day selloff (e.g. RSI(2) < 10) while they are still above the 200-day MA, then exit on a bounce or after N days.
- **Evidence:** this is a well-known retail edge, but transaction costs and slippage erode it (arXiv 1909.04327).
- **Agent fit:** moderate. It needs strict, mechanical exits. **This is exactly where the production study found agents fail if left to discretion.**

### E. Event / news-driven (where the LLM actually adds value)
- **Inputs:** 8-K filings (the largest single contributor in the Fin-Analyst ablation), earnings releases, guidance changes, and sentiment on breaking news.
- **Evidence:** LLM headline scoring has documented predictive power, strongest in small caps and on bad news, but it is decaying. Post-earnings drift in large caps has mostly gone.
- **Agent fit:**
  - Use this mainly to *avoid* bad events and to *exit* on negative news.
  - Only open positions with small size, many trades and strict stops.
- **Must-haves:**
  - cross-verify news across at least two sources
  - never act on a single unverified headline (an AI-Trader failure mode)

### F. Multi-agent "trading firm" (TradingAgents style)
- **Roles:** fundamental, sentiment, news and technical analysts → bull/bear researchers debate → trader → risk team (aggressive, neutral, conservative) → portfolio manager approves.
- **Evidence:** the paper reports gains in returns, Sharpe and drawdown, but only in backtests, which are prone to look-ahead leakage. Live benchmarks show multi-agent designs are not consistently better.
- **Agent fit:** a useful structure for the *analysis* layer of A. It is expensive in tokens. Don't let the debate output set size.

### G. LLM as strategy researcher (offline)
- **Workflow:** the LLM proposes rules or factors, a backtester validates them on out-of-sample periods *after the model's knowledge cutoff*, and only the survivors are deployed.
- **Agent fit:** good for research, but needs strict anti-overfitting discipline (walk-forward tests, deflated Sharpe).

### H. Things to avoid on this setup
- **High leverage or "max leverage" modes:** these produced the worst Alpha Arena results and drove liquidations in the production fleet.
- **Hourly discretionary LLM trading:** high cost, no demonstrated edge, momentum-chasing bias.
- **Memecoin or "movers"-list chasing:** agents herded into these at a −6.9 bps forward return.
- **Options:** the setup assumes no options level, and the LiveOption benchmark found typical LLM episode returns negative in every task.

---

## 4. Design principles (the rules to encode)

**Risk and execution, enforced in code**
1. Attach the stop and target to every entry when the order opens, using `place_advanced_order` / bracket-style orders. Never leave a naked open.
2. Size by volatility: risk at most about 0.5–1% of equity per trade, with position size = risk ÷ (ATR × k).
3. Hard caps, checked before every order:
   - maximum position about 20% of equity
   - maximum gross exposure 100% (no margin use at first)
   - maximum N trades per day or week
   - a daily loss limit that halts trading (e.g. −2%)
   - a drawdown kill switch (e.g. −10% from peak → stop and alert a human)
4. Keep a ticker allowlist of liquid instruments. No penny stocks or newly listed meme names.
5. Run a post-order check after each fill (exposure, stop present, limits respected) and alert on violation.

**Decision process**

6. Low frequency by default: decide daily or weekly, not intraday. Restraint was the most consistent trait of winners.
7. Numeric rules beat prose: "exit if close < 50-DMA" rather than "manage risk carefully."
8. Keep the LLM's authority asymmetric: it can veto or shrink but not enlarge.
9. Use fresh information only: news and 8-Ks, rather than 10-K or fundamentals noise at short horizons. Verify each item against at least two sources.
10. Don't show the agent "top movers" or leaderboards in its context unless that is deliberate.

**Memory and logging**

11. Keep structured memory, not free-form notes: a trade journal with entry, thesis, exit and P&L, read back each cycle so the agent doesn't repeat losing calls. Keep it append-only and validated against actual positions from `get_equity_positions` / `get_equity_orders`, to prevent fabricated state.
12. Log every decision's provenance: the inputs, the rule that fired, the LLM rationale, and the order ID.

**Evaluation**

13. Paper trade or use a tiny size first. Use a benchmark (SPY buy-and-hold) and require months of data, not weeks, because short-window rankings reverse.
14. Keep a human in the loop at first. Robinhood exposes a trade-approval setting (`get_trade_approval_setting`), so require approval until the system has a track record.

---

## 5. Suggested starter blueprint for this repo

*This was the first sketch. The strategy the repo implements ranks S&P 100 and Nasdaq-100 stocks instead of sector ETFs, and adds a crypto-ETF sleeve; see the README and `skills/trade/`.*

```
Weekly (Mon, after open):
  1. Regime: SPY > 200-DMA (3% band)? If not → hold cash / short-term treasury ETF.
  2. Rank sector ETFs (XLK, XLF, XLE, XLV, XLY, XLP, XLI, XLU, XLB, XLRE, XLC)
     by 6-month return excluding last month; pick top 3.
  3. LLM overlay: for each pick, pull news/8-K headlines for top holdings;
     veto if a major negative event is pending. Log rationale.
  4. Size: equal-weight, volatility-capped; stop = 2×ATR below entry.
  5. Review orders → (human approval) → place with attached stops.
Daily:
  - Check stops/fills, daily-loss limit, kill switch; alert on anomalies.
Monthly:
  - Compare vs SPY; review journal; adjust only via written, numeric rule changes.
```

---

## 6. Regulatory note (US)
- The SEC approved FINRA's replacement of the **$25,000 pattern-day-trader rule** with intraday margin standards on 2026-04-14, **effective 2026-06-04**.
- Brokers may phase this in until **2027-10-20**, so check Robinhood's current behavior before relying on it for frequent day trades.

---

## 7. 中文研究补充（2026-10-15 补查）

### 7.1 总体结论：与英文研究一致
- **头部量化机构的主力仍是树模型和线性多因子。** 大模型主要用于处理新闻、公告、研报这类非结构化数据，还没有取代核心预测引擎（知乎行业调研）。
- **中文报道对大模型实盘的结论同样偏负面。** 常见问题包括交易过频、仓位失控、没有止损、高杠杆多头。一个 ChatGPT 炒股实验 24 小时内交易 12 笔，在高位追涨且不设止损，最终亏损 23.5%，也没跑赢买入持有。
- **中文圈的研究重心与英文不同。** 中文研究更多是让 LLM 当"量化研究员"挖因子，而不是让 LLM 直接当交易员。这对应第 3 节的策略 G，证据也比"LLM 直接下单"更扎实。

### 7.2 LLM 因子挖掘（中文研究的主战场）

| 框架 | 来源 | 做法 | 报告结果（论文自述，未经独立验证） |
|---|---|---|---|
| **RD-Agent(Q)** | 微软亚洲研究院，NeurIPS 2025，开源 | 研究智能体提假设，开发智能体 Co-STEER 写代码并回测，再用多臂老虎机决定下一步探索方向；因子与模型联合优化 | 年化收益最高约为经典因子库的 2 倍，所用因子减少 70%；优于先进的深度时序模型 |
| **QuantaAlpha** | 上海财经大学；东方证券金工 2026-04-07 研报解读 | LLM 与进化算法结合；Idea、Factor、Evaluation 三类智能体模拟研究员的流程（假设→构建→回测→迭代→因子池维护） | 研报 PDF 未能解析出数字 |
| **AlphaSchema** | arXiv 2607.26642 | 把因子思路结构化为"事件、背景、特征、方向、输出"五部分，先选计划再让 LLM 写代码；用 LightGBM 代理模型预测奖励 | CSI300，2023–2025 测试期：纯量价因子年化超额 8.53%，加基本面后 11.94%（IR 1.09，最大回撤 15.4%）。**不同 LLM 实现的因子质量相当** |
| **AlphaAgent** | 知乎论文解读 | 用原创性约束（AST 比对）、假设与因子一致性、复杂度控制来对抗 alpha 衰减 | — |
| **AlphaLogics** | arXiv 2603.20247 | 从"市场逻辑"出发生成因子 | 能以 95–99% 的一致度还原 Alpha101/191 里的成熟因子 |
| 清华 MCTS 方法 | 知乎论文解读 | LLM 加蒙特卡洛树搜索，迭代生成符号化公式 | — |

据知乎作者说，这条路线最早源自华泰金工的一篇研报，研报拆解了基于 LLM 和多智能体的量价因子挖掘框架。

**对本项目的启示**
- AlphaSchema 和 Agent Market Arena 都发现：**决定效果的是框架设计，不是哪个 LLM。** 可以用便宜的模型。
- 因子挖掘适合离线研究：LLM 提出想法，回测验证，样本外通过后再上线。
- 不要让 LLM 实时决定下单。

### 7.3 LLM 写的回测代码不可信：BacktestBench
- **规模：** BacktestBench（arXiv 2605.17937）用 600 多万条市场记录和 18,246 个标注问答，评测了 23 个主流 LLM 把自然语言策略转成回测代码的能力。
- **常见错误：** 指标算错，比如波动率和夏普比率漏掉了第 0 天的初始资金；交易逻辑缺少边界约束，比如允许在最后一天开仓。知乎解读的总结是："不是带未来函数，就是年化指标算错"。
- **配套研究：** 另一篇论文（arXiv 2609.28090）专门测试 LLM 能否识别被做了手脚的回测。
- **启示：** AI 生成的策略和回测代码必须人工审查，或者用成熟的回测库，并单独检查前视偏差。QMT、PTrade 等平台已支持用自然语言生成策略，调研同样提醒有"幻觉策略"风险：逻辑看起来正确，实际在市场上无效。

### 7.4 中文新闻情绪
- **A 股研究：** 有论文（arXiv 2306.14222）比较了 ChatGPT、二郎神 RoBERTa 和中文 FinBERT 从中文财经新闻提取情绪因子的效果，但检索结果里没有给出具体回测数字。
- **未找到：** 没有搜到华泰、中信等券商公开发布的大模型情绪因子回测结论。这类研报通常要在 Wind 或券商研究所平台上查看。

### 7.5 来源质量提醒
中文检索结果里营销内容很多。比如阿里云开发者社区一篇宣传"收益率 5860%"的 OpenClaw 自动炒股教程，没有任何可核实的出处，本文未采用。知乎原帖多数会拦截自动抓取（返回 403），上面引用的内容来自搜索摘要。

### 7.6 中文来源
- [RD-Agent(Q)（NeurIPS 2025）](https://papers.nips.cc/paper_files/paper/2025/hash/ac5c2b6e423883cbcacbcccf88491b78-Abstract-Datasets_and_Benchmarks_Track.html) ／ [arXiv 2505.15155](https://www.arxiv.org/abs/2505.15155)
- [东方证券：QuantaAlpha 用大模型做量化因子挖掘](https://quantaalpha.com/documents/orient-20260407-quantaalpha-factor-mining.pdf) ／ [发现报告](https://www.fxbaogao.com/detail/5339368)
- [AlphaSchema（alphaXiv 中文）](https://www.alphaxiv.org/zh/abs/2607.26642)
- [AlphaLogics（alphaXiv 中文）](https://www.alphaxiv.org/zh/abs/2603.20247)
- [AlphaGPT 答疑（知乎）](https://zhuanlan.zhihu.com/p/689844787)
- [Paper Reading：AlphaAgent / MCTS（知乎）](https://zhuanlan.zhihu.com/p/26622852737)
- [BacktestBench（arXiv 2605.17937）](https://arxiv.org/pdf/2605.17937) ／ [知乎解读](https://zhuanlan.zhihu.com/p/2042267042540245632)
- [Can LLMs Catch a Rigged Backtest?（arXiv 2609.28090）](https://arxiv.org/pdf/2609.28090)
- [主流量化交易技术方案全景调研（知乎）](https://zhuanlan.zhihu.com/p/2047072565588501883)
- [LLM 预测中国股价的情绪研究（arXiv 2306.14222）](https://arxiv.org/pdf/2306.14222)
- [当智能体交易时（alphaXiv 中文）](https://www.alphaxiv.org/zh/abs/2510.11695)
- [Bloomberg：AI 交易机器人大多亏损](https://www.bloomberg.com/news/articles/2026-05-06/ai-bots-auditioning-for-wall-street-trading-are-mostly-losing)
- [人人都是产品经理：AI 炒股实验](https://www.woshipm.com/?p=6284120)

---

## 8. 更激进的策略调研（2026-10-16）

问题：在现有"周度动量 + 200DMA + ATR止损"之上，有没有证据支持的更激进做法？按证据强弱排序：

| 方案 | 证据强度 | 主要数字 | 结论 |
|---|---|---|---|
| **杠杆轮动 LRS**（指数 > 200DMA 时持 2x/3x，跌破换现金） | 强（87 年回测，Dow Award 论文） | 1928–2015：S&P 1x 买入持有 9.1%/年、回撤 −86%；**2x LRS 19.1%/年、波动 24.9%、Sharpe 0.51、最大回撤 −78.7%**；3x LRS 26.8%/年、回撤 −92.2%；平均每年 5 次交易，未扣交易成本 | 最值得做。用 2x 不用 3x；震荡市会被来回打脸 |
| **波动率目标化**（Barroso & Santa-Clara 2015） | 强（JFE） | 动量按预测波动缩放到固定目标（~12%），**Sharpe 近乎翻倍、基本消除动量崩盘** | "聪明的激进"：低波动时加仓、高波动时减仓 |
| **调大现有参数**（风险 3%、持仓 3 只、单票上限 35%） | 中（机械推导） | 收益/波动近似线性放大；集中度越高回撤越深 | 可小幅做；注意 10% 回撤熔断会更早触发 |
| **极度集中（1–3 只动量股）** | 反面证据 | 互联网泡沫破裂期：10 只动量组合 −71%，**单只 −87%**（指数 −43%）；实证最优持股 10–25 只 | 不推荐 |
| **突破波段（Minervini VCP / Qullamaggie）** | 弱（自报、社区脚本） | 名人收益未审计；一个 GitHub 项目 walk-forward 通过但标为"provisional" | 需每日运行、换手高，暂不推荐 |
| **买期权 / 0DTE** | 反面证据 | 散户 0DTE 多头平均亏 ~61%，其中 ~60% 来自交易成本 | 不推荐 |
| **加密趋势跟随**（BTC > 均线才持有） | 中（多为博客） | 方向一致：Sharpe 提升、回撤从 ~−80% 降到 ~−44% | 已在 IBIT/ETHA 袖子里实现；可考虑上调到 30% |

**杠杆 ETF 要点**：每日再平衡导致波动损耗——趋势强、波动低时有利，震荡时有害（2011-08 一周内 S&P 微涨、3x 却为负）。指数在 200DMA 上方时年化波动显著低于下方，这正是 LRS 有效的原因。

**与现有系统冲突**：`drawdown_kill_pct = 10%` 对 2x 杠杆仓位约等于标的跌 5% 就熔断；若上激进方案，熔断阈值需同步放宽（如 20–25%），否则会频繁停机。

### 8.1 可落地的激进升级方案（建议）
1. **新增"杠杆趋势袖子"**：QQQ > 200DMA（3% 带）时持 **QLD（2x 纳指）**，否则空仓/现金；占净值 30–40%，从个股预算中拨出。每周四随调仓判断一次。
2. **个股部分做波动率目标化**：组合目标年化波动 ~25%，用持仓 ATR 估算，超出则按比例缩小新买单。
3. 加密袖子上限 20% → 25–30%（可选）。
4. 熔断：日亏停买 2% → 4%，回撤熔断 10% → 20%。
5. 不做：单票集中、期权、日内突破。

来源：[Gayed & Bilello, Leverage for the Long Run (CMT 2016)](https://docs.cmtassociation.org/dow-award/2016-gayed-bilello.pdf)，[CXO 摘要](https://www.cxoadvisory.com/volatility-effects/leveraging-the-u-s-stock-market-based-on-sma-rules/)，[Quantpedia 杠杆 ETF 资产配置](https://quantpedia.com/leveraged-etfs-in-asset-allocation-opportunity-or-trap/)，[Barroso & Santa-Clara 摘要](https://academicnewsletter.sufe.edu.cn/info/356496)，[Alpha Architect 动量崩盘](https://alphaarchitect.com/avoiding-momentum-crashes/)，[Globe and Mail 集中动量](https://arc-dev.theglobeandmail.com/investing/markets/inside-the-market/article-this-portfolio-offers-strategy-with-momentum)，[0DTE 散户亏损](https://wealthmanagement.com/node/153003)，[Composer TQQQ 200d](https://www.composer.trade/trading-strategies/tqqq-based-on-200d-ma-X3BQchFSANrGzrIKnDO6)，[swing-trading-strategies (GitHub)](https://github.com/sofus-nl/swing-trading-strategies)，[CoinDesk 周期策略](https://cryptobriefing.com/bitcoin-defined-risk-strategy-price-surge/)，[虎嗅 杠杆ETF爆仓案例](https://www.huxiu.com/article/4870810.html)

## 9. 补充调研（2026-10-19）：新进展、社区实战、止损后再入场

### 9.1 总体结论：没有推翻前面的判断，反而更强化了
1. **LLM 自主选股/择时依然跑不赢指数。** 新的实盘、模拟盘数据和新论文都指向同一结论：收益主要来自市场 beta 和运气，不是选股能力。
2. **交易越少越好，又多了两组独立数据。** TradeRank 第 9 季中，平仓 6 次以上的 8 个模型全部亏损；ClawStreet 上交易最频繁的四分之一 agent 中位收益 −1.18%，最不活跃的四分之一为 +0.01%。
3. **风控必须写在 LLM 之外。** 一篇新的安全综述（SoK）检查了 7 个开源学术交易 agent，没有一个在 LLM 之外强制执行止损或仓位上限。本项目的设计（引擎决定、LLM 只能否决或缩小）正好避开了这个问题。

### 9.2 新的实盘 / 模拟盘数据
| 来源 | 时间与规模 | 关键数字 |
|---|---|---|
| **TradeRank 第 9 季** | 2026-09-12 至 10-10，18 个模型，各 $1 万模拟资金，50 只美股 + 10 种加密货币，每日决策一次，手续费 0.1% | **只有 1/18 跑赢标普**（SPY +2.20%）。冠军 Nemotron 3.5 Lightning +3.84%，几乎全靠一笔占仓位 60% 的 SOL，最高曾 +15.18%，后回吐。**无模型跑赢 BTC**（+6.93%）。全场已平仓交易：加密 −$1,960，股票 −$687。Claude Fable 5.1 −2.29%（第 12），GPT-6 Astra −2.18%（第 11）。 |
| **TradeRank 九季汇总**（72 个模型-赛季） | 赛季 0–8 | **排名不延续**：相邻赛季排名相关系数汇总 +0.03，约等于随机，所以排行榜冠军不能当选模型的依据。48.6% 的模型-赛季盈利，且主要取决于当季行情：上涨季 12/14 盈利，另一个上涨季因模型普遍做空而 0/9。 |
| **ClawStreet**（2026-10 实时） | 130 个 agent，各 $10 万模拟资金，美股 + 加密货币 | 44% 盈利，**41% 跑赢标普**，中位收益 −0.14%（同期标普 +3.05%）。按策略分：**动量 19 个，中位 +0.02%，9/19 跑赢**；**均值回归 14 个，中位 −1.12%，仅 3/14 跑赢**；多策略 4 个，样本太小。 |

注意：以上都是模拟成交，没有计入滑点；赛季只有 4 周，市场方向占主导。

### 9.3 新论文（2026-05 至 10）
- **KTD-Fin（arXiv 2605.28359）**：在沪深 300 上测了 10 个前沿模型（2024–2026）。把股票代码和日期匿名化以后，模型的推理方式明显改变，说明之前的回测部分靠"记住了历史"。用 Barra 归因拆解收益，**主要来自被动的市场和风格暴露，几乎没有持续的选股 alpha**。
  - 由此可见，小红书上流传的"TradingAgents 收益 26%、夏普 8.21"属于这类未控制数据泄露的回测，不可信。
- **CLQT（arXiv 2606.29771）**：做了一年的去污染回测，外加 4 周券商模拟盘。
  - 发现 agent 的实际仓位**系统性地偏离它自己写下的分析**（"说一套做一套"，偏差在回测中为 +0.30，实盘为 +0.23）。
  - 扣掉真实成本后，能跑赢防御型基准，但**不能干净地跑赢指数**。
  - "能力最强"的模型和夏普最高的模型并不是同一个。
- **LiveMACE（arXiv 2610.09872）**：5 个前沿模型实盘 30 天。实际收益和各项能力指标经常对不上，相近的收益可以来自完全不同的行为，因此**不能用短期收益判断 agent 好坏**。
- **SoK：交易 agent 还是市场破坏者（arXiv 2609.19705）**：审查了 15 个学术方案。
  - 没有一个在稳健性指标上拿满分；没有一篇论文给出不用等 LLM 推理就能停止交易的办法。
  - 7 个开源实现中，**没有一个在 LLM 之外强制止损或仓位上限**，2 个会在主机上直接运行模型写的代码。
  - 给部署者的建议：多来源交叉验证信息、保留绕过模型的停机开关、在模型之外强制执行亏损上限。

### 9.4 社区实战（X / Reddit / 小红书）
- 小红书上点赞最高的 Claude 自动交易开源流程（3,200+ 赞）和本项目架构几乎一致：
  - 收盘后只分析、不下单，第二天开盘前重新验证再交易。
  - 风控全部机械执行，"哪怕 Claude 再相信一只股票，也没有权限为了一个好故事修改止损或加仓"。
  - 默认先跑模拟盘，确认日志没问题后再手动打开实盘。
- "Robinhood Agentic Account + Claude Code + Robinhood MCP"已经有中文教程在流传（525 赞、757 收藏），使用这一套的人在增多。
- Reddit 上的主流共识是：回测漂亮、实盘失效。高互动帖子基本都在提醒看实盘、警惕过拟合和数据泄露。

### 9.5 止损后再入场：要不要冷却期？
- **Han, Zhou & Zhu (2016)《Taming Momentum Crashes》**：在动量组合上加 15% 止损，用 1926–2013 年的数据：
  - 等权组合的最大月亏损从 −49.79% 降到 −17.43%，市值加权组合从 −64.97% 降到 −22.10%。
  - **夏普比率提高一倍以上。**
  - 做法是：被止损的股票当月剩余时间持有现金，**下次调仓时按排名正常重新入选，没有额外的冷却期**。这和本项目当前的设计（止损后下周调仓时重新排名）基本一致。
- **Kaminski & Lo (2014)《When Do Stop-Loss Rules Stop Losses?》**：
  - 如果价格是随机游走，止损必然降低期望收益。
  - 如果价格有动量（正自相关），止损可以提高收益。
  - **止损的价值必须和再入场规则一起评估。**
- **结论：现有"无冷却期、下次调仓重新排名"的做法有学术依据，不需要改。**
  - 唯一值得考虑的小改动，是防止同一只股票在短期内被反复止损。例如：同一只股票 4 周内被止损两次，就跳过一次调仓。
  - 没找到证据支持更长的冷却期，而冷却期越长，越容易错过动量重新走强的阶段。

### 9.6 对本项目的建议
1. **维持架构不变**：规则引擎决定买卖，LLM 只能否决或缩小，止损单挂在券商端（不依赖 LLM）。新证据都支持这套做法。
2. **不要按排行榜换模型。** 排名在赛季之间没有延续性，选便宜、稳定的模型就够了。
3. **评估时看过程，别只看收益**（参考 CLQT 和 LiveMACE）：
   - 每周对照 `plan.json` 和实际成交，检查执行是否偏离了计划。
   - 统计 LLM 否决的命中率：被否决的股票之后表现是否真的更差。
4. **补一个绕过 LLM 的紧急停机开关**（SoK 的建议），例如在 `state/state.json` 中加 `kill: true`，引擎读到后直接停止所有买单。目前的回撤熔断已经部分覆盖这一点。**（已实现：`strategy.py kill` / `unkill`，开启后引擎不发出任何订单。）**
5. **止损后再入场**：保持现状；如果观察到反复止损，再加"4 周内两次止损就跳过一期"的规则。

## Sources
- 2026-10-19 补充：[TradeRank 第 9 季结果](https://www.traderank.ai/blog/season-9-final-results)，[TradeRank 九季研究](https://www.traderank.ai/blog/nine-seasons-llm-paper-trading)，[ClawStreet agentic trading 实时结果](https://www.clawstreet.io/agentic-trading)，[KTD-Fin (arXiv 2605.28359)](https://arxiv.org/abs/2605.28359)，[CLQT (arXiv 2606.29771)](https://arxiv.org/abs/2606.29771)，[LiveMACE (arXiv 2610.09872)](https://arxiv.org/abs/2610.09872)，[SoK: Trading Agents or Market Crashers? (arXiv 2609.19705)](https://arxiv.org/abs/2609.19705)，[Han, Zhou & Zhu, Taming Momentum Crashes (SSRN 2407199)](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2407199)，[Kaminski & Lo, When Do Stop-Loss Rules Stop Losses? (SSRN 968338)](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=968338)
- [What LLM Trading Agents Actually Do in Production (arXiv 2609.05663)](https://arxiv.org/html/2609.05663v1)
- [Agentic Quantitative Trading: A Survey (arXiv 2608.31041)](https://arxiv.org/html/2608.31041v1)
- [Fin-Analyst at FinMMEval 2026 (arXiv 2607.12233)](https://arxiv.org/html/2607.12233v1)
- [AI-Trader: Benchmarking Autonomous Agents in Real-Time Markets (arXiv 2512.10971)](https://arxiv.org/html/2512.10971v1)
- [When Agents Trade: Live Multi-Market Benchmark (arXiv 2510.11695)](https://arxiv.org/html/2510.11695v2)
- [TradingAgents: Multi-Agents LLM Financial Trading Framework (arXiv 2412.20138)](https://arxiv.org/html/2412.20138v4)
- [Look-Ahead-Bench (arXiv 2601.13770)](https://arxiv.org/pdf/2601.13770)
- [LiveOption benchmark](https://www.researchgate.net/publication/414926178_LiveOption_Evaluating_LLM_Agents_in_Structured_Option_Trading_with_Nonlinear_Payoffs)
- [Lopez-Lira & Tang, Can ChatGPT Forecast Stock Price Movements? (arXiv 2304.07619)](https://arxiv.org/abs/2304.07619v4)
- Alpha Arena coverage: [Business Standard](https://www.business-standard.com/markets/news/ai-bots-auditioning-for-wall-street-trading-are-mostly-losing-money-126050701793_1.html), [Forklog S1](https://forklog.com/en/?p=90617), [Forklog S1.5](https://forklog.com/en/news/ai-model-grok-4-2-triumphs-in-trading-tournament), [Buildix lessons](https://www.buildix.trade/blog/nof1-alpha-arena-lessons-ai-trading-byok-strategy-advisor-2026), [Medium analysis](https://medium.com/@farhat.hanne/alpha-arena-when-ais-fight-who-will-survive-3b179c84f7fa)
- Trend / momentum: [Time-series momentum summary](https://systematicallybiased.substack.com/p/quantitativesystematic-trading), [StockCharts 200-DMA backtest](https://stockcharts.com/articles/arthurhill/2023/11/who-will-win-the-battle-for-th-837.html), [Mean-reversion & costs (arXiv 1909.04327)](https://www.arxiv.org/abs/1909.04327)
- PEAD decline: [Martineau](https://cfr.ivo-welch.info/published/papers/martineau2021rest.pdf)
- PDT rule: [FINRA](https://www.finra.org/compliance-tools/weekly-archive/04152026), [WilmerHale](https://www.wilmerhale.com/en/insights/client-alerts/20260423-sec-approves-amendments-to-finra-rule-4210-replacing-day-trading-margin-requirements-with-a-modernized-intraday-margin-standard)
