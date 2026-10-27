#!/usr/bin/env python3
"""Generate a self-contained local HTML investment report. Pure stdlib.

Reads journal/equity.csv (date,equity[,spy]), journal/flows.csv (date,amount: deposits +, withdrawals -),
journal/trades.jsonl, state/state.json
and the latest run/<date>/ (post.json preferred, else input.json; plan.json for the ranking).
Writes reports/report-<date>.html and reports/latest.html.

usage: python3 report.py [--root DIR]
Data dir: --root, else $AGENT_TRADER_HOME, else the project root (see datadir.py).
"""
import csv
import html
import json
import sys
from collections import defaultdict, deque
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import datadir

ROOT = datadir.resolve(sys.argv[sys.argv.index("--root") + 1] if "--root" in sys.argv else None)
CONFIG = datadir.require_config(ROOT)


def load_equity():
    p = ROOT / "journal" / "equity.csv"
    if not p.exists():
        return []
    rows = []
    for r in csv.DictReader(p.open()):
        rows.append({"date": r["date"], "equity": float(r["equity"]),
                     "spy": float(r["spy"]) if r.get("spy") else None})
    # time-weighted NAV: a day's return excludes that day's deposits/withdrawals
    flows = load_flows()
    nav = 1.0
    for i, r in enumerate(rows):
        if i:
            nav *= (r["equity"] - flows.get(r["date"], 0.0)) / rows[i - 1]["equity"]
        r["nav"] = nav
    return rows


def load_flows():
    p = ROOT / "journal" / "flows.csv"
    flows = defaultdict(float)
    if p.exists():
        for r in csv.DictReader(p.open()):
            flows[r["date"]] += float(r["amount"])
    return flows


def load_trades():
    p = ROOT / "journal" / "trades.jsonl"
    if not p.exists():
        return []
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()]


def latest_run():
    runs = sorted(d for d in (ROOT / "run").glob("*") if d.is_dir() and not d.name.endswith("-test"))
    for d in reversed(runs):
        for name in ("post.json", "input.json"):
            if (d / name).exists():
                plan = json.loads((d / "plan.json").read_text()) if (d / "plan.json").exists() else None
                return d.name, json.loads((d / name).read_text()), plan
    return None, None, None


def load_runs():
    """Every non-test run as (date, input, plan); missing files are None."""
    runs = []
    for d in sorted(d for d in (ROOT / "run").glob("*") if d.is_dir() and not d.name.endswith("-test")):
        inp, pl = d / "input.json", d / "plan.json"
        runs.append((d.name, json.loads(inp.read_text()) if inp.exists() else None,
                     json.loads(pl.read_text()) if pl.exists() else None))
    return runs


def trade_day(ts):
    """Journal timestamps mix local offsets and Z; bucket them by US/Eastern trading date."""
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return str(ts)[:10]
    return (dt.astimezone(ZoneInfo("America/New_York")) if dt.tzinfo else dt).date().isoformat()


def execution_audit(runs, trades):
    """Match each run's final plan.json against the journal. Returns rows of
    (day, action, symbol, status, detail); status is 一致 / 偏离 / 未执行 / 计划外."""
    by_day = defaultdict(list)
    for t in trades:
        by_day[trade_day(t.get("ts"))].append(t)
    rows, planned_days = [], set()
    for day, _, plan in runs:
        if not plan:
            continue
        planned_days.add(day)
        done = list(by_day.get(day, []))
        for o in plan.get("orders", []):
            m = next((t for t in done if t.get("action") == o["action"] and t.get("symbol") == o["symbol"]), None)
            if m is None:
                rows.append((day, o["action"], o["symbol"], "未执行", o.get("reason", "")))
                continue
            done.remove(m)
            detail, off = [], False
            if float(m.get("qty") or 0) != float(o.get("quantity") or 0):
                detail.append(f"数量 {m.get('qty')} ≠ 计划 {o.get('quantity')}")
                off = True
            slip = None
            if o["action"] in ("BUY", "ADD") and m.get("fill_price") and o.get("ref_price"):
                slip = m["fill_price"] / o["ref_price"] - 1
                detail.append(f"成交价偏离 {slip:+.2%}")
            if m.get("stop_price") is not None and o.get("stop_price") is not None \
                    and abs(m["stop_price"] - o["stop_price"]) > 0.01:
                detail.append(f"止损 {m['stop_price']} ≠ 计划 {o['stop_price']}")
                # the runbook re-bases a BUY's stop when the fill is >1% off ref_price
                off = off or not (slip is not None and abs(slip) > 0.01)
            rows.append((day, o["action"], o["symbol"], "偏离" if off else "一致", "; ".join(detail)))
        for t in done:
            rows.append((day, t.get("action"), t.get("symbol"), "计划外", t.get("reason") or ""))
    for day in sorted(set(by_day) - planned_days):
        for t in by_day[day]:
            rows.append((day, t.get("action"), t.get("symbol"), "计划外", "当天没有 plan.json"))
    return rows


def overlay_audit(runs, trades):
    """How each LLM veto / shrink played out: the stock's move since the decision vs SPY's,
    priced from the newest run's input.json."""
    latest = next((inp for _, inp, _ in reversed(runs) if inp), None)
    if not latest:
        return []
    px_now = {r["symbol"]: r["price"] for r in latest.get("universe", []) + latest.get("crypto", [])}
    notes = {(trade_day(t.get("ts")), t.get("symbol")): t.get("overlay_note") for t in trades if t.get("overlay_note")}
    rows = []
    for day, inp, _ in runs:
        if not inp:
            continue
        px0 = {r["symbol"]: r["price"] for r in inp.get("universe", []) + inp.get("crypto", [])}
        spy_ret = latest["spy"]["price"] / inp["spy"]["price"] - 1 if inp.get("spy") else None
        items = [(s, "否决", why) for s, why in (inp.get("vetoes") or {}).items()]
        items += [(s, f"缩小 ×{m}", notes.get((day, s), "")) for s, m in (inp.get("shrinks") or {}).items()]
        for sym, kind, why in items:
            a, b = px0.get(sym), px_now.get(sym)
            rows.append({"day": day, "symbol": sym, "kind": kind, "why": why or "",
                         "ret": b / a - 1 if a and b else None, "spy": spy_ret})
    return rows


def realized_pnl(trades):
    """FIFO match of buy fills (BUY/ADD) against sell fills (SELL/TRIM/STOP_HIT). Returns (total, per-symbol dict)."""
    lots, per = defaultdict(deque), defaultdict(float)
    for t in trades:
        a, sym, qty, px = t.get("action"), t.get("symbol"), float(t.get("qty") or 0), t.get("fill_price")
        if px is None or qty <= 0:
            continue
        if a in ("BUY", "ADD"):
            lots[sym].append([qty, float(px)])
        elif a in ("SELL", "TRIM", "STOP_HIT"):
            while qty > 1e-9 and lots[sym]:
                lot = lots[sym][0]
                m = min(qty, lot[0])
                per[sym] += m * (float(px) - lot[1])
                lot[0] -= m
                qty -= m
                if lot[0] <= 1e-9:
                    lots[sym].popleft()
    return sum(per.values()), dict(per)


def max_drawdown(values):
    peak, mdd = None, 0.0
    for v in values:
        peak = v if peak is None else max(peak, v)
        mdd = max(mdd, 1 - v / peak)
    return mdd


def svg_curve(eq):
    w, h, pad = 720, 240, 36
    if len(eq) < 2:
        return '<p class="muted">净值数据点不足（至少需要两天），暂无曲线。</p>'
    base_s = next((r["spy"] for r in eq if r["spy"]), None)
    series = [("策略", [r["nav"] - 1 for r in eq], "var(--accent)")]
    if base_s:
        series.append(("SPY", [(r["spy"] / base_s - 1) if r["spy"] else None for r in eq], "var(--muted)"))
    vals = [v for _, s, _ in series for v in s if v is not None]
    lo, hi = min(vals + [0]), max(vals + [0])
    span = (hi - lo) or 0.01
    x = lambda i: pad + i * (w - 2 * pad) / (len(eq) - 1)
    y = lambda v: h - pad - (v - lo) / span * (h - 2 * pad)
    parts = [f'<svg viewBox="0 0 {w} {h}" role="img" aria-label="净值曲线">',
             f'<line x1="{pad}" x2="{w - pad}" y1="{y(0):.1f}" y2="{y(0):.1f}" class="axis"/>',
             f'<text x="4" y="{y(hi) + 4:.1f}" class="tick">{hi:+.1%}</text>',
             f'<text x="4" y="{y(lo) + 4:.1f}" class="tick">{lo:+.1%}</text>',
             f'<text x="{pad}" y="{h - 8}" class="tick">{eq[0]["date"]}</text>',
             f'<text x="{w - pad}" y="{h - 8}" class="tick" text-anchor="end">{eq[-1]["date"]}</text>']
    for name, s, color in series:
        pts = " ".join(f"{x(i):.1f},{y(v):.1f}" for i, v in enumerate(s) if v is not None)
        parts.append(f'<polyline points="{pts}" fill="none" stroke="{color}" stroke-width="2"/>')
    parts.append("</svg>")
    legend = " ".join(f'<span class="key" style="--c:{c}"></span>{n}' for n, _, c in series)
    return f'<div class="legend">{legend}</div>' + "".join(parts)


def fmt_money(v):
    return f"${v:,.2f}"


def pct_cls(v):
    return "pos" if v > 0 else "neg" if v < 0 else ""


def table(headers, rows):
    if not rows:
        return '<p class="muted">无</p>'
    th = "".join(f"<th>{html.escape(h)}</th>" for h in headers)
    trs = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
    return f'<div class="scroll"><table><thead><tr>{th}</tr></thead><tbody>{trs}</tbody></table></div>'


def build():
    eq, trades = load_equity(), load_trades()
    state_p = ROOT / "state" / "state.json"
    state = json.loads(state_p.read_text()) if state_p.exists() else {}
    run_date, snap, plan = latest_run()
    today = datetime.now().strftime("%Y-%m-%d")

    equity = snap["equity"] if snap else (eq[-1]["equity"] if eq else 0)
    total_ret = eq[-1]["nav"] - 1 if eq else 0
    spy_ret = None
    if eq and eq[0]["spy"] and eq[-1]["spy"]:
        spy_ret = eq[-1]["spy"] / eq[0]["spy"] - 1
    mdd = max_drawdown([r["nav"] for r in eq]) if eq else 0
    realized, realized_by = realized_pnl(trades)

    # holdings
    prices = {r["symbol"]: r["price"] for r in (snap or {}).get("universe", []) + (snap or {}).get("crypto", [])}
    crypto_syms = set(CONFIG.get("crypto", {}).get("symbols", []))
    hold_rows, invested, open_risk, unreal, crypto_val = [], 0.0, 0.0, 0.0, 0.0
    for p in (snap or {}).get("positions", []):
        q, cost = float(p["quantity"]), float(p["avg_cost"])
        px = prices.get(p["symbol"], p.get("price", cost))
        val, pnl = q * px, q * (px - cost)
        invested += val
        unreal += pnl
        crypto_val += val if p["symbol"] in crypto_syms else 0
        stop = p.get("stop_price")
        risk = q * max(0.0, px - stop) if stop else None
        open_risk += risk or 0
        tag = ' <span class="tag">数字货币</span>' if p["symbol"] in crypto_syms else ""
        hold_rows.append([
            f"<b>{html.escape(p['symbol'])}</b>{tag}", f"{q:g}", fmt_money(cost), fmt_money(px), fmt_money(val),
            f"{val / equity:.1%}" if equity else "-",
            f'<span class="{pct_cls(pnl)}">{fmt_money(pnl)} ({px / cost - 1:+.1%})</span>',
            fmt_money(stop) if stop else '<span class="neg"><b>无止损</b></span>',
            f"{px / stop - 1:.1%}" if stop else "-",
        ])
    cash = (snap or {}).get("cash", equity - invested)

    trade_rows = [[html.escape(str(t.get("ts", ""))[:16]), html.escape(t.get("action", "")),
                   f"<b>{html.escape(t.get('symbol', ''))}</b>", html.escape(str(t.get("qty", ""))),
                   fmt_money(t["fill_price"]) if t.get("fill_price") is not None else "-",
                   fmt_money(t["stop_price"]) if t.get("stop_price") is not None else "-",
                   html.escape(t.get("reason", "") or ""), html.escape(t.get("overlay_note", "") or "")]
                  for t in reversed(trades[-30:])]

    plan_html = '<p class="muted">还没有运行记录。</p>'
    if plan:
        top = table(["排名", "代码", "价格", "12-1 动量", "6-1 动量", "得分"],
                    [[r["rank"], f"<b>{r['symbol']}</b>", fmt_money(r["price"]), f"{r['mom12']:.0%}",
                      f"{r['mom6']:.0%}", r["score"]] for r in plan.get("top10", [])])
        notes = "".join(f"<li>{html.escape(n)}</li>" for n in plan.get("notes", []))
        plan_html = f"<ul class='notes'>{notes}</ul><h3>动量排名前 10</h3>{top}"

    runs = load_runs()
    exec_rows = execution_audit(runs, trades)
    bad = [r for r in exec_rows if r[3] != "一致"]
    exec_cls = {"一致": "pos", "偏离": "neg", "未执行": "neg", "计划外": "neg"}
    exec_html = (f'<p class="sub">{len(exec_rows)} 条计划/成交记录，<span class="{"neg" if bad else "pos"}">'
                 f'{len(bad)} 条异常</span>。按计划执行是规则引擎的前提，异常需要人工看一眼。</p>'
                 + table(["日期", "操作", "代码", "状态", "说明"],
                         [[d, html.escape(str(a)), f"<b>{html.escape(str(s))}</b>",
                           f'<span class="{exec_cls[st]}">{st}</span>', html.escape(det)]
                          for d, a, s, st, det in reversed(exec_rows[-40:])]))
    ov = overlay_audit(runs, trades)
    judged = [o for o in ov if o["ret"] is not None and o["spy"] is not None]
    right = sum(1 for o in judged if o["ret"] < o["spy"])
    ov_html = (f'<p class="sub">被否决/缩小的股票之后跑输 SPY 才算把关正确：{right} / {len(judged)}'
               f'{"（样本太少，暂不说明问题）" if len(judged) < 10 else ""}。</p>'
               + table(["决策日", "代码", "动作", "理由", "此后涨跌", "同期 SPY", "判断"],
                       [[o["day"], f"<b>{html.escape(o['symbol'])}</b>", html.escape(o["kind"]), html.escape(o["why"]),
                         f'<span class="{pct_cls(o["ret"])}">{o["ret"]:+.1%}</span>' if o["ret"] is not None else "-",
                         f"{o['spy']:+.1%}" if o["spy"] is not None else "-",
                         ("正确" if o["ret"] < o["spy"] else "错过收益") if o["ret"] is not None and o["spy"] is not None else "-"]
                        for o in reversed(ov)]))

    halted = state.get("halted")
    status = (f'<div class="banner neg">⚠ 已熔断：{html.escape(state.get("halt_reason") or "")}。需手动解除。</div>'
              if halted else "")
    if state.get("kill"):
        status += (f'<div class="banner neg">⛔ 紧急停机已开启：{html.escape(state.get("kill_reason") or "用户手动")}。'
                   f'引擎不会发出任何订单，券商端已挂的止损单不受影响。解除：python3 strategy.py unkill</div>')
    cards = [
        ("账户净值", fmt_money(equity), ""),
        ("累计收益", f"{total_ret:+.2%}", pct_cls(total_ret)),
        ("同期 SPY", f"{spy_ret:+.2%}" if spy_ret is not None else "-", pct_cls(spy_ret or 0)),
        ("超额收益", f"{total_ret - spy_ret:+.2%}" if spy_ret is not None else "-",
         pct_cls(total_ret - (spy_ret or 0))),
        ("最大回撤", f"{mdd:.2%}", "neg" if mdd > 0 else ""),
        ("已实现盈亏", fmt_money(realized), pct_cls(realized)),
        ("未实现盈亏", fmt_money(unreal), pct_cls(unreal)),
        ("现金占比", f"{cash / equity:.0%}" if equity else "-", ""),
        ("数字货币占比", f"{crypto_val / equity:.0%} / 上限 {CONFIG['crypto']['max_total_pct']:.0%}" if equity else "-", ""),
        ("止损总风险", f"{fmt_money(open_risk)} ({open_risk / equity:.1%})" if equity else "-", ""),
    ]
    cards_html = "".join(f'<div class="card"><div class="label">{l}</div><div class="value {c}">{v}</div></div>'
                         for l, v, c in cards)
    cfg = CONFIG
    params = (f"选股范围 {' + '.join(cfg['universe_indexes'])} · 最多 {cfg['max_positions']} 只 · "
              f"每笔风险 {cfg['risk_per_trade']:.0%} · 止损 {cfg['stop_atr_mult']}×ATR · 单只上限 {cfg['max_position_pct']:.0%} · "
              f"日亏 {cfg['daily_loss_halt_pct']:.0%} 停买 · 回撤 {cfg['drawdown_kill_pct']:.0%} 熔断 · "
              f"数字货币 {' / '.join(cfg['crypto']['symbols'])} 总上限 {cfg['crypto']['max_total_pct']:.0%}、单只 {cfg['crypto']['max_each_pct']:.0%}")

    return today, f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>投资报告 {today}</title>
<style>
:root{{--bg:#f7f7f5;--panel:#fff;--text:#1d1d1f;--muted:#8a8a8e;--line:#e4e4e2;--accent:#2f6fde;--pos:#1a8f4c;--neg:#c9372c}}
@media (prefers-color-scheme:dark){{:root{{--bg:#151517;--panel:#1f1f22;--text:#ececee;--muted:#8d8d93;--line:#333338;--accent:#6b9cff;--pos:#4cc483;--neg:#ff6b5e}}}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--text);font:15px/1.55 -apple-system,"PingFang SC","Helvetica Neue",sans-serif}}
main{{max-width:960px;margin:0 auto;padding:24px 16px 48px}}h1{{font-size:24px;margin:0 0 4px}}h2{{font-size:17px;margin:32px 0 10px}}h3{{font-size:15px;margin:18px 0 8px}}
.muted,.sub{{color:var(--muted)}}.sub{{font-size:13px}}
.cards{{display:grid;grid-template-columns:repeat(auto-fill,minmax(160px,1fr));gap:10px;margin-top:18px}}
.card,.panel{{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:12px 14px}}
.label{{color:var(--muted);font-size:12px}}.value{{font-size:20px;font-weight:600;font-variant-numeric:tabular-nums}}
.pos{{color:var(--pos)}}.neg{{color:var(--neg)}}.banner{{margin-top:14px;padding:10px 14px;border-radius:8px;border:1px solid currentColor}}
.scroll{{overflow-x:auto}}table{{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums;font-size:14px}}
th,td{{text-align:left;padding:7px 10px;border-bottom:1px solid var(--line);white-space:nowrap}}th{{color:var(--muted);font-weight:500;font-size:12px}}
svg{{width:100%;height:auto}}.axis{{stroke:var(--line)}}.tick{{fill:var(--muted);font-size:11px}}
.legend{{font-size:12px;color:var(--muted);margin-bottom:4px}}.key{{display:inline-block;width:14px;height:3px;background:var(--c);margin:0 6px 3px 12px;vertical-align:middle}}
.notes{{margin:0;padding-left:18px}}.tag{{font-size:11px;color:var(--accent);border:1px solid currentColor;border-radius:4px;padding:0 4px;margin-left:4px}}footer{{margin-top:36px;font-size:12px;color:var(--muted)}}
</style></head><body><main>
<h1>投资报告</h1>
<div class="sub">MCP 账户 ••••{str(cfg['account_number'])[-4:]} · 生成于 {datetime.now().strftime('%Y-%m-%d %H:%M')} · 最近运行 {run_date or '无'}</div>
{status}
<div class="cards">{cards_html}</div>
<h2>净值曲线（相对起点）</h2><div class="panel">{svg_curve(eq)}</div>
<h2>当前持仓</h2>{table(["代码", "股数", "成本", "现价", "市值", "占比", "浮动盈亏", "止损价", "距止损"], hold_rows)}
<h2>最近一次运行</h2><div class="panel">{plan_html}</div>
<h2>交易记录（最近 30 笔）</h2>{table(["时间", "操作", "代码", "数量", "成交价", "止损价", "原因", "新闻把关"], trade_rows)}
<h2>执行审计：计划 vs 实际成交</h2>{exec_html}
<h2>新闻把关回看：否决 / 缩小</h2>{ov_html}
<h2>策略参数</h2><p class="sub">{params}</p>
<footer>数据来自本地日志（journal/、run/、state/），价格为最近一次运行时的快照，非实时。本报告不构成投资建议。</footer>
</main></body></html>
"""


def main():
    today, page = build()
    out = ROOT / "reports"
    out.mkdir(exist_ok=True)
    (out / f"report-{today}.html").write_text(page)
    (out / "latest.html").write_text(page)
    print(out / f"report-{today}.html")


if __name__ == "__main__":
    main()
