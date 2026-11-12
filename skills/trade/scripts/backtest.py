#!/usr/bin/env python3
"""Replay history through the live rules engine (strategy.plan). Pure stdlib.

    python3 backtest.py fetch                      # download/refresh daily bars into backtest/cache/
    python3 backtest.py run [options]              # one simulation, prints a summary
    python3 backtest.py compare [options]          # baseline vs the rule variants side by side
    python3 backtest.py bench [options]            # strategy (every rebalance weekday) vs buy & hold ETFs

Each trading day the simulator does what the live agent does:
  1. broker-side stops: a bar that opens below the stop fills at the open (gap), one that trades
     through it fills at the stop;
  2. builds the same input.json the agent would (price = that day's close, indicators from bars up to it);
  3. calls strategy.plan() with the live config and a simulated state.json;
  4. on the weekly rebalance day executes every order; on other days applies the /trade check rules
     (stops, risk-off sells, BUYs only into empty slots; no rank-drop SELL, ADD or TRIM);
  5. fills at the close +/- slippage, whole shares, only with cash on hand.

Not modelled, so read results with that in mind:
  - survivorship: the universe is today's SP100 + NDX members, which favours names that went up;
  - earnings: no historical calendar, so the blackout never triggers (gaps still hit stops);
  - the news overlay (vetoes/shrinks) and intraday timing (live runs ~14:00 ET, here at the close).
Dividends are credited as cash on the ex-date and lower an open stop by the same amount (as brokers
adjust stop orders), so the SPY comparison is total return on both sides.

Rule variants (all go through the engine's own vetoes or config, strategy.py is unchanged):
  --cooldown N      veto re-buying a symbol within N trading days of exiting it
  --max-corr X      veto a stock BUY whose 60-day return correlation with a held/new stock exceeds X
  --set k=v         override a config.json value (e.g. stop_atr_mult=3.5, max_positions=8)
  --halt-reset N    simulate a human resetting the drawdown halt N trading days after it trips
                    (0 = never); ignored while config halt_release.enabled is true, because the
                    engine then lifts the halt itself
"""
import argparse
import copy
import json
import math
import sys
import time
import urllib.request
from datetime import date, datetime, timezone

import datadir
import strategy

ROOT = datadir.resolve()
CACHE = ROOT / "backtest" / "cache"
OUT = ROOT / "backtest" / "runs"
YAHOO = "https://query1.finance.yahoo.com/v8/finance/chart/{sym}?range={rng}&interval=1d&events=div"
EXTRA = ["SPY", "IBIT", "ETHA"]
BENCH = ["SPY", "QQQ", "MTUM", "RSP"]  # S&P 500, Nasdaq-100, US momentum factor, equal-weight S&P 500


# --- data -------------------------------------------------------------------

def universe_symbols():
    """Today's SP100 + NDX members, taken from the latest live scan."""
    scans = sorted(ROOT.glob("run/*/scan.json"))
    if not scans:
        sys.exit("no run/<date>/scan.json yet: run /trade once so the universe is known")
    rows = json.loads(scans[-1].read_text())["data"]["result"]["results"]
    return sorted(r["columns"]["Symbol"] for r in rows)


def fetch(symbols, rng="12y"):
    CACHE.mkdir(parents=True, exist_ok=True)
    failed = []
    for i, sym in enumerate(symbols, 1):
        url = YAHOO.format(sym=sym.replace(".", "-"), rng=rng)
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                res = json.load(resp)["chart"]["result"][0]
        except Exception as e:  # noqa: BLE001 - report and keep going
            failed.append(f"{sym}: {e}")
            continue
        q = res["indicators"]["quote"][0]
        bars = []
        for k, ts in enumerate(res.get("timestamp") or []):
            o, h, l, c = q["open"][k], q["high"][k], q["low"][k], q["close"][k]
            if None in (o, h, l, c):
                continue
            d = datetime.fromtimestamp(ts + res["meta"].get("gmtoffset", 0), timezone.utc).date().isoformat()
            bars.append([d, o, h, l, c])
        divs = {datetime.fromtimestamp(int(ts), timezone.utc).date().isoformat(): v["amount"]
                for ts, v in (res.get("events") or {}).get("dividends", {}).items()}
        (CACHE / f"{sym}.json").write_text(json.dumps({"bars": bars, "divs": divs}))
        print(f"[{i}/{len(symbols)}] {sym}: {len(bars)} bars", file=sys.stderr)
        time.sleep(0.15)
    return failed


def load_bars():
    data = {}
    for f in CACHE.glob("*.json"):
        data[f.stem] = json.loads(f.read_text())
    if "SPY" not in data:
        sys.exit("no cached data: run `backtest.py fetch` first")
    return data


def indicators(bars):
    """date -> (open, high, low, close, sma200, atr14, mom12, mom6, ret) on split-adjusted bars."""
    out, atr, closes = {}, None, []
    for k, (d, o, h, l, c) in enumerate(bars):
        closes.append(c)
        prev = bars[k - 1][4] if k else c
        tr = max(h - l, abs(h - prev), abs(l - prev))
        if k < 14:
            atr = (atr or 0) + tr / 14
        else:
            atr = (atr * 13 + tr) / 14
        sma = sum(closes[k - 199:k + 1]) / 200 if k >= 199 else None
        mom12 = closes[k - 21] / closes[k - 252] - 1 if k >= 252 else None
        mom6 = closes[k - 21] / closes[k - 126] - 1 if k >= 126 else None
        ret = c / prev - 1 if k else 0.0
        out[d] = (o, h, l, c, sma, atr if k >= 13 else None, mom12, mom6, ret)
    return out


# --- simulation -------------------------------------------------------------

def corr(a, b):
    n = len(a)
    if n < 20:
        return 0.0
    ma, mb = sum(a) / n, sum(b) / n
    cov = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    va = sum((x - ma) ** 2 for x in a)
    vb = sum((y - mb) ** 2 for y in b)
    return cov / math.sqrt(va * vb) if va and vb else 0.0


def simulate(data, cfg, start, end=None, capital=10000.0, slippage_bps=5, rebalance_weekday=3,
             cooldown=0, max_corr=None, halt_reset=20):
    ind = {s: indicators(v["bars"]) for s, v in data.items()}
    divs = {s: v["divs"] for s, v in data.items()}
    dates = [d for d in sorted(ind["SPY"]) if d >= start and (end is None or d <= end)]
    crypto_syms = set(cfg["crypto"]["symbols"])
    stocks = [s for s in ind if s not in crypto_syms and s not in BENCH]
    slip = slippage_bps / 1e4

    cash, pos = capital, {}          # pos: sym -> {qty, avg_cost, stop, entry_date}
    state = {"peak_equity": None, "halted": False, "halt_reason": None}
    last_close, last_exit = {}, {}
    trades, curve, halts = [], [], []
    spy_tr, spy_prev = 1.0, None     # SPY total-return index
    halted_on = None
    prev_equity = capital
    hist_ret = {s: [] for s in ind}  # trailing daily returns for the correlation filter

    def value():
        return cash + sum(p["qty"] * last_close.get(s, p["avg_cost"]) for s, p in pos.items())

    def exit_pos(sym, qty, price, d, action, reason):
        nonlocal cash
        p = pos[sym]
        cash += qty * price
        trades.append({"date": d, "action": action, "symbol": sym, "qty": qty, "price": round(price, 4),
                       "entry_date": p["entry_date"], "avg_cost": round(p["avg_cost"], 4),
                       "pnl": round(qty * (price - p["avg_cost"]), 2),
                       "pnl_pct": round(price / p["avg_cost"] - 1, 4), "stop": p["stop"], "reason": reason})
        p["qty"] -= qty
        if p["qty"] <= 0:
            del pos[sym]
            last_exit[sym] = len(curve)

    for i, d in enumerate(dates):
        today = {s: ind[s][d] for s in ind if d in ind[s]}
        for s, row in today.items():
            last_close[s] = row[3]
            hist_ret[s].append(row[8])
            if len(hist_ret[s]) > 60:
                hist_ret[s].pop(0)

        # dividends: cash for holders, stops move down with the price
        for sym, p in pos.items():
            dv = divs.get(sym, {}).get(d)
            if dv:
                cash += p["qty"] * dv
                if p["stop"] is not None:
                    p["stop"] = round(p["stop"] - dv, 2)
        spy_c = today["SPY"][3]
        if spy_prev:
            spy_tr *= (spy_c + divs["SPY"].get(d, 0)) / spy_prev
        spy_prev = spy_c

        # 1. broker-side stops
        for sym in list(pos):
            p, row = pos[sym], today.get(sym)
            if row is None or p["stop"] is None:
                continue
            o, l = row[0], row[2]
            if o <= p["stop"]:
                exit_pos(sym, p["qty"], o * (1 - slip), d, "STOP_GAP", f"opened {o:.2f} below stop {p['stop']:.2f}")
            elif l <= p["stop"]:
                exit_pos(sym, p["qty"], p["stop"] * (1 - slip), d, "STOP_HIT", "stop")

        equity = value()

        # simulated human review of the drawdown halt (skipped when the engine's halt_release handles it)
        if state.get("halted") and not cfg.get("halt_release", {}).get("enabled"):
            halted_on = halted_on if halted_on is not None else i
            if halt_reset and i - halted_on >= halt_reset:
                halts.append({"tripped": dates[halted_on], "reset": d})
                state.update(halted=False, halt_reason=None, peak_equity=equity)
                halted_on = None

        # 2. engine input, as the agent would build it
        spy = today["SPY"]
        if spy[4] is None:
            curve.append((d, equity, spy_tr))
            prev_equity = equity
            continue
        universe = []
        for s in stocks:
            row = today.get(s)
            if row is None or None in (row[4], row[5], row[6], row[7]):
                continue
            universe.append({"symbol": s, "price": row[3], "sma200": row[4], "mom12": row[6], "mom6": row[7],
                             "atr": row[5], "earnings_ymd": None, "verified": True})
        crypto = []
        for s in cfg["crypto"]["symbols"]:
            row = today.get(s)
            if row and None not in (row[4], row[5], row[6], row[7]):
                crypto.append({"symbol": s, "price": row[3], "sma200": row[4], "mom12": row[6],
                               "mom6": row[7], "atr": row[5]})
        inp = {"date": d, "equity": equity, "cash": cash, "prev_close_equity": prev_equity,
               "spy": {"price": spy[3], "sma200": spy[4]}, "universe": universe, "crypto": crypto,
               "earnings_source": {"source": "backtest (no calendar)", "through": "2099-12-31"},
               "positions": [{"symbol": s, "quantity": p["qty"], "avg_cost": p["avg_cost"],
                              "stop_price": p["stop"]} for s, p in pos.items()],
               "vetoes": {}}
        if cooldown:
            for s, k in last_exit.items():
                if len(curve) - k < cooldown and s not in pos:
                    inp["vetoes"][s] = f"cooldown: exited {len(curve) - k}d ago"

        # 3. engine (re-run while the correlation filter adds vetoes, like the live overlay re-run)
        for _ in range(12):
            result, new_state = strategy.plan(inp, cfg, copy.deepcopy(state))
            if max_corr is None:
                break
            accepted = [s for s in pos if s not in crypto_syms]
            added = False
            for o in result["orders"]:
                if o["action"] != "BUY" or o["symbol"] in crypto_syms:
                    continue
                worst = max((corr(hist_ret[o["symbol"]], hist_ret[h]) for h in accepted), default=0)
                if worst > max_corr:
                    inp["vetoes"][o["symbol"]] = f"corr {worst:.2f} > {max_corr}"
                    added = True
                    break
                accepted.append(o["symbol"])
            if not added:
                break
        if new_state.get("released_on") == d and state.get("released_on") != d:
            halts.append({"tripped": state.get("halted_on"), "reset": d, "auto": True})
        state = new_state
        if state.get("halted") and halted_on is None:
            halted_on = i

        # 4. which orders this run would execute
        rebalance = date.fromisoformat(d).weekday() == rebalance_weekday
        orders = []
        for o in result["orders"]:
            a = o["action"]
            if rebalance or a in ("RAISE_STOP", "SET_STOP") or (a == "SELL" and o["reason"] == "regime risk-off"):
                orders.append(o)
            elif a == "BUY":
                orders.append(o)  # slot-limited below
        if not rebalance:
            held_stocks = sum(1 for s in pos if s not in crypto_syms) - sum(
                1 for o in orders if o["action"] == "SELL" and o["symbol"] not in crypto_syms)
            slots = cfg["max_positions"] - held_stocks
            kept = []
            for o in orders:
                if o["action"] == "BUY" and o["symbol"] not in crypto_syms:
                    if slots <= 0:
                        continue
                    slots -= 1
                kept.append(o)
            orders = kept

        # 5. execute: sells, stops, buys, adds
        px = lambda s: last_close[s]
        for o in orders:
            s = o["symbol"]
            if o["action"] == "SELL" and s in pos:
                exit_pos(s, pos[s]["qty"], px(s) * (1 - slip), d, "SELL", o["reason"])
            elif o["action"] == "TRIM" and s in pos:
                exit_pos(s, o["quantity"], px(s) * (1 - slip), d, "TRIM", o["reason"])
                if s in pos:
                    pos[s]["stop"] = o["stop_price"]
            elif o["action"] in ("RAISE_STOP", "SET_STOP") and s in pos:
                pos[s]["stop"] = o["stop_price"]
        for o in orders:
            s = o["symbol"]
            if o["action"] not in ("BUY", "ADD"):
                continue
            fill = px(s) * (1 + slip)
            cost = o["quantity"] * fill
            if cost > cash:
                continue
            cash -= cost
            if o["action"] == "BUY":
                pos[s] = {"qty": o["quantity"], "avg_cost": fill, "stop": o["stop_price"], "entry_date": d}
                trades.append({"date": d, "action": "BUY", "symbol": s, "qty": o["quantity"],
                               "price": round(fill, 4), "stop": o["stop_price"], "reason": o["reason"]})
            elif s in pos:
                p = pos[s]
                p["avg_cost"] = (p["avg_cost"] * p["qty"] + cost) / (p["qty"] + o["quantity"])
                p["qty"] += o["quantity"]
                p["stop"] = o["stop_price"]
                trades.append({"date": d, "action": "ADD", "symbol": s, "qty": o["quantity"],
                               "price": round(fill, 4), "stop": o["stop_price"], "reason": o["reason"]})

        equity = value()
        curve.append((d, equity, spy_tr))
        prev_equity = equity

    return {"curve": curve, "trades": trades, "halts": halts, "open": pos}


# --- metrics ----------------------------------------------------------------

def stats(series, dates):
    rets = [series[k] / series[k - 1] - 1 for k in range(1, len(series))]
    years = (date.fromisoformat(dates[-1]) - date.fromisoformat(dates[0])).days / 365.25
    cagr = (series[-1] / series[0]) ** (1 / years) - 1 if years > 0 else 0
    mean = sum(rets) / len(rets)
    vol = math.sqrt(sum((r - mean) ** 2 for r in rets) / len(rets)) * math.sqrt(252)
    peak, mdd = series[0], 0.0
    for v in series:
        peak = max(peak, v)
        mdd = max(mdd, 1 - v / peak)
    return {"total": series[-1] / series[0] - 1, "cagr": cagr, "vol": vol,
            "sharpe": mean * 252 / vol if vol else 0, "max_dd": mdd}


def summarize(res):
    curve = res["curve"]
    dates = [c[0] for c in curve]
    strat = stats([c[1] for c in curve], dates)
    spy = stats([c[2] for c in curve], dates)
    exits = [t for t in res["trades"] if t["action"] in ("SELL", "TRIM", "STOP_HIT", "STOP_GAP")]
    wins = [t for t in exits if t["pnl"] > 0]
    losses = [t for t in exits if t["pnl"] <= 0]
    gaps = [t for t in exits if t["action"] == "STOP_GAP"]
    gap_cost = sum(t["qty"] * (t["stop"] - t["price"]) for t in gaps if t["stop"])
    years = {}
    for d, eq, sp in curve:
        y = d[:4]
        years.setdefault(y, [eq, sp, eq, sp])
        years[y][2:] = [eq, sp]
    return {
        "period": f"{dates[0]} .. {dates[-1]}",
        "strategy": strat, "spy": spy,
        "exits": len(exits), "win_rate": len(wins) / len(exits) if exits else 0,
        "avg_win": sum(t["pnl_pct"] for t in wins) / len(wins) if wins else 0,
        "avg_loss": sum(t["pnl_pct"] for t in losses) / len(losses) if losses else 0,
        "profit_factor": sum(t["pnl"] for t in wins) / -sum(t["pnl"] for t in losses) if losses else None,
        "worst": min(exits, key=lambda t: t["pnl_pct"])["pnl_pct"] if exits else 0,
        "gap_exits": len(gaps), "gap_cost_usd": round(gap_cost, 2),
        "halts": res["halts"],
        "years": {y: (v[2] / v[0] - 1, v[3] / v[1] - 1) for y, v in years.items()},
    }


def pct(x):
    return f"{x * 100:+.1f}%" if x is not None else "n/a"


def print_summary(name, s):
    st, sp = s["strategy"], s["spy"]
    print(f"\n== {name}  ({s['period']})")
    print(f"  strategy  total {pct(st['total'])}  CAGR {pct(st['cagr'])}  vol {st['vol']:.1%}  "
          f"Sharpe {st['sharpe']:.2f}  maxDD {st['max_dd']:.1%}")
    print(f"  SPY       total {pct(sp['total'])}  CAGR {pct(sp['cagr'])}  vol {sp['vol']:.1%}  "
          f"Sharpe {sp['sharpe']:.2f}  maxDD {sp['max_dd']:.1%}")
    pf = f"{s['profit_factor']:.2f}" if s["profit_factor"] is not None else "n/a"
    print(f"  exits {s['exits']}  win rate {s['win_rate']:.0%}  avg win {pct(s['avg_win'])}  "
          f"avg loss {pct(s['avg_loss'])}  profit factor {pf}  worst {pct(s['worst'])}")
    print(f"  gap-through stops {s['gap_exits']} (cost vs stop ${s['gap_cost_usd']:,.0f})  "
          f"drawdown halts {len(s['halts'])}")
    print("  year    strat     SPY")
    for y, (a, b) in s["years"].items():
        print(f"  {y}  {pct(a):>7}  {pct(b):>7}")


def save(name, res, s):
    out = OUT / name
    out.mkdir(parents=True, exist_ok=True)
    (out / "summary.json").write_text(json.dumps(s, indent=2) + "\n")
    (out / "equity.csv").write_text("date,equity,spy_tr\n" + "".join(f"{d},{e:.2f},{p:.4f}\n" for d, e, p in res["curve"]))
    (out / "trades.jsonl").write_text("".join(json.dumps(t) + "\n" for t in res["trades"]))
    return out


# --- cli --------------------------------------------------------------------

def parse_value(v):
    for cast in (int, float):
        try:
            return cast(v)
        except ValueError:
            pass
    return {"true": True, "false": False}.get(v.lower(), v)


def make_cfg(sets):
    cfg = copy.deepcopy(strategy.CONFIG)
    for kv in sets or []:
        k, v = kv.split("=", 1)
        node, *path = k.split(".")
        target = cfg
        keys = [node] + path
        for key in keys[:-1]:
            target = target[key]
        target[keys[-1]] = parse_value(v)
    return cfg


def hold_curve(data, sym, dates):
    """Buy & hold total-return index (dividends reinvested) on the given dates."""
    bars = {b[0]: b[4] for b in data[sym]["bars"]}
    divs, out, idx, prev = data[sym]["divs"], [], 1.0, None
    for d in dates:
        c = bars.get(d)
        if c is None:
            out.append(idx)
            continue
        if prev:
            idx *= (c + divs.get(d, 0)) / prev
        prev = c
        out.append(idx)
    return out


def bench(data, a, common):
    missing = [b for b in BENCH if b not in data]
    if missing:
        sys.exit(f"no cached data for {missing}: run `backtest.py fetch`")
    for b in BENCH:  # an unadjusted split shows up as a huge one-day move
        bars = data[b]["bars"]
        for k in range(1, len(bars)):
            if abs(bars[k][4] / bars[k - 1][4] - 1) > 0.4:
                print(f"WARNING {b}: {bars[k - 1][0]} -> {bars[k][0]} close {bars[k - 1][4]:.2f} -> "
                      f"{bars[k][4]:.2f}, likely an unadjusted split; pass --end before it", file=sys.stderr)
    periods = [(a.start, a.end), ("2016-01-04", "2020-12-31"), ("2021-01-04", a.end)]
    cfg = make_cfg(a.set)
    for start, end in periods:
        rows = []
        for wd in range(5):
            res = simulate(data, cfg, cooldown=a.cooldown, max_corr=a.max_corr, halt_reset=a.halt_reset,
                           **{**common, "start": start, "end": end, "rebalance_weekday": wd})
            dates = [c[0] for c in res["curve"]]
            rows.append((f"strategy ({['Mon', 'Tue', 'Wed', 'Thu', 'Fri'][wd]})", stats([c[1] for c in res["curve"]], dates)))
        rows.sort(key=lambda r: r[1]["cagr"])
        for b in BENCH:
            rows.append((f"{b} buy & hold", stats(hold_curve(data, b, dates), dates)))
        print(f"\n== {dates[0]} .. {dates[-1]}")
        print(f"  {'':<20} {'total':>9} {'CAGR':>7} {'vol':>6} {'maxDD':>6} {'Sharpe':>6}")
        for name, st in rows:
            print(f"  {name:<20} {pct(st['total']):>9} {pct(st['cagr']):>7} {st['vol']:>6.1%} "
                  f"{st['max_dd']:>6.1%} {st['sharpe']:>6.2f}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["fetch", "run", "compare", "bench"])
    ap.add_argument("--start", default="2016-01-04")
    ap.add_argument("--end")
    ap.add_argument("--capital", type=float, default=10000.0)
    ap.add_argument("--slippage-bps", type=float, default=5)
    ap.add_argument("--weekday", type=int, default=3, help="rebalance weekday, 0=Mon (default Thu)")
    ap.add_argument("--cooldown", type=int, default=0)
    ap.add_argument("--max-corr", type=float)
    ap.add_argument("--halt-reset", type=int, default=20)
    ap.add_argument("--set", action="append", help="config override k=v (dotted keys ok)")
    ap.add_argument("--name", default="run")
    a = ap.parse_args()

    if a.cmd == "fetch":
        failed = fetch(universe_symbols() + EXTRA + [b for b in BENCH if b not in EXTRA])
        print(json.dumps({"cache": str(CACHE), "failed": failed}, indent=2))
        return

    data = load_bars()
    common = dict(start=a.start, end=a.end, capital=a.capital, slippage_bps=a.slippage_bps,
                  rebalance_weekday=a.weekday)
    if a.cmd == "bench":
        return bench(data, a, common)
    if a.cmd == "run":
        res = simulate(data, make_cfg(a.set), cooldown=a.cooldown, max_corr=a.max_corr,
                       halt_reset=a.halt_reset, **common)
        s = summarize(res)
        print_summary(a.name, s)
        print(f"\n  saved to {save(a.name, res, s)}")
        return

    variants = [
        ("baseline", {}, []),
        ("halt-never-reset", {"halt_reset": 0}, []),
        ("cooldown-20d", {"cooldown": 20}, []),
        ("max-corr-0.6", {"max_corr": 0.6}, []),
        ("stops-3.5atr", {}, ["stop_atr_mult=3.5"]),
        ("cooldown+corr", {"cooldown": 20, "max_corr": 0.6}, []),
    ]
    rows = []
    for name, kw, sets in variants:
        opts = {"cooldown": a.cooldown, "max_corr": a.max_corr, "halt_reset": a.halt_reset, **kw}
        res = simulate(data, make_cfg((a.set or []) + sets), **opts, **common)
        s = summarize(res)
        save(name, res, s)
        print_summary(name, s)
        rows.append((name, s))
    print("\n== comparison")
    print(f"  {'variant':<18} {'total':>8} {'CAGR':>7} {'maxDD':>6} {'Sharpe':>6} {'win%':>5} {'gaps':>5} {'halts':>5}")
    for name, s in rows:
        st = s["strategy"]
        print(f"  {name:<18} {pct(st['total']):>8} {pct(st['cagr']):>7} {st['max_dd']:>6.1%} "
              f"{st['sharpe']:>6.2f} {s['win_rate']:>5.0%} {s['gap_exits']:>5} {len(s['halts']):>5}")
    sp = rows[0][1]["spy"]
    print(f"  {'SPY buy&hold':<18} {pct(sp['total']):>8} {pct(sp['cagr']):>7} {sp['max_dd']:>6.1%} {sp['sharpe']:>6.2f}")


if __name__ == "__main__":
    main()
