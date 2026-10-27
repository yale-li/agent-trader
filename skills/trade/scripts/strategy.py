#!/usr/bin/env python3
"""Rules engine for the weekly momentum strategy. Pure stdlib, no network.

The agent fetches data through the Robinhood MCP, writes it to run/<date>/input.json,
then runs:  python3 strategy.py plan run/<date>/input.json
The engine decides entries, exits, sizes and stops. The LLM overlay may only
veto or shrink buys; it never adds trades or size.

input.json shape:
{
  "date": "2026-10-16",
  "equity": 5000.0,            # portfolio total_value
  "cash": 5000.0,
  "prev_close_equity": 5000.0, # equity at the previous close (for the daily-loss halt)
  "spy": {"price": 0, "sma200": 0},
  "universe": [{"symbol","price","sma200","mom12","mom6","atr","earnings_ymd"}],  # all rows of config universe_indexes (SP100 + NDX), not only those above the 200DMA
                                 # a row may carry "verified": true once re-checked with split-adjusted data
  "crypto": [{"symbol","price","sma200","mom12","mom6","atr"}],  # IBIT/ETHA from get_equity_technical_indicators
                                 # + split-adjusted historicals, NOT the scanner (its history is not split-adjusted)
  "positions": [{"symbol","quantity","avg_cost","stop_price"}],  # stop_price null if none; includes crypto ETFs
  "vetoes": {"SYM": "reason"},   # optional, filled by the LLM overlay
  "shrinks": {"SYM": 0.5}        # optional multiplier in (0, 1]
}

Resizing held positions (config "resize", off unless "enabled": true):
  TRIM  a held position worth more than its cap x (1 + min_drift) is cut back to the cap
        (max_position_pct, or crypto max_each_pct). Allowed even when buys are blocked.
  ADD   a held position is topped up to the size a new entry would get today, measuring risk
        from the wider of its live stop and stop_atr_mult x ATR. Same gates as a new entry
        (risk-on, no halt, earnings blackout, vetoes/shrinks, budget), plus rank <= add_max_rank
        (default hold_rank_buffer). Runs after new entries, so new names get cash first.
  Both skip changes smaller than min_drift x held quantity or min_trade_usd.

Kill switch (user only):  python3 strategy.py kill [reason]  /  python3 strategy.py unkill
While state.json has "kill": true the engine emits no orders at all (not even stop raises).

Data dir (config.json, state/, run/, journal/): $AGENT_TRADER_HOME if set, else the current directory
if it has config.json, else the project root (see datadir.py; `setup.py init` creates it).
A relative <input.json> path is resolved against the data dir, so the commands work from any directory.
"""
import json
import math
import sys
from datetime import date, datetime
from pathlib import Path

import datadir

ROOT = datadir.resolve()
CONFIG = datadir.require_config(ROOT)
STATE_PATH = ROOT / "state" / "state.json"


def load_state():
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text())
    return {"peak_equity": None, "halted": False, "halt_reason": None}


def save_state(state):
    STATE_PATH.parent.mkdir(exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2) + "\n")


def days_until(ymd, today):
    if not ymd:
        return None
    try:
        d = datetime.strptime(str(int(float(ymd))), "%Y%m%d").date()
    except ValueError:
        return None
    return (d - today).days


def score(row):
    """Blend of 12-1 and 6-1 momentum, divided by ATR% so steady trends beat parabolic ones."""
    atr_pct = row["atr"] / row["price"]
    if atr_pct <= 0:
        return None
    return (0.5 * row["mom12"] + 0.5 * row["mom6"]) / atr_pct


def suspicious(row, cfg=CONFIG):
    """Scanner history is not split-adjusted; flag rows whose numbers look like a split artifact."""
    if row.get("verified"):
        return False
    g = cfg["data_guard"]
    ratio = row["price"] / row["sma200"] if row["sma200"] else 0
    return not (g["min_price_to_sma"] <= ratio <= g["max_price_to_sma"]) or row["mom12"] > g["max_mom12"]


def rank_universe(universe, excluded):
    eligible = []
    for r in universe:
        if r["symbol"] in excluded or suspicious(r) or r["price"] <= r["sma200"]:
            continue
        if r["mom12"] <= 0 or r["mom6"] <= 0:
            continue
        s = score(r)
        if s is not None:
            eligible.append({**r, "score": s})
    eligible.sort(key=lambda r: r["score"], reverse=True)
    for i, r in enumerate(eligible, 1):
        r["rank"] = i
    return eligible


def size_position(row, equity, cfg, shrink=1.0):
    stop_dist = cfg["stop_atr_mult"] * row["atr"]
    by_risk = math.floor(equity * cfg["risk_per_trade"] * shrink / stop_dist)
    by_cap = math.floor(equity * cfg["max_position_pct"] * shrink / row["price"])
    qty = max(0, min(by_risk, by_cap))
    return qty, round(row["price"] - stop_dist, 2)


def plan(inp, cfg=CONFIG, state=None):
    state = state or load_state()
    today = date.fromisoformat(inp["date"])
    equity = inp["equity"]
    notes, orders = [], []

    # --- circuit breakers -------------------------------------------------
    peak = max(state.get("peak_equity") or equity, equity)
    state["peak_equity"] = peak
    drawdown = 1 - equity / peak
    if drawdown >= cfg["drawdown_kill_pct"]:
        state["halted"] = True
        state["halt_reason"] = f"drawdown {drawdown:.1%} from peak {peak:.2f}"
    if state.get("halted"):
        notes.append(f"HALTED: {state['halt_reason']}. No new buys; existing stops stay. Human must reset state/state.json.")
    daily_loss = 1 - equity / inp["prev_close_equity"] if inp.get("prev_close_equity") else 0
    no_buys = state.get("halted", False)
    if daily_loss >= cfg["daily_loss_halt_pct"]:
        no_buys = True
        notes.append(f"Daily loss {daily_loss:.1%} >= limit: no new buys today.")

    # --- regime -----------------------------------------------------------
    spy = inp["spy"]
    risk_on = spy["price"] >= spy["sma200"] * (1 - cfg["regime_band"])
    notes.append(f"Regime: SPY {spy['price']:.2f} vs SMA200 {spy['sma200']:.2f} -> {'RISK-ON' if risk_on else 'RISK-OFF'}")

    crypto_syms = set(cfg["crypto"]["symbols"])
    by_sym = {r["symbol"]: r for r in inp["universe"]}
    by_sym.update({r["symbol"]: r for r in inp.get("crypto", [])})
    flagged = sorted(r["symbol"] for r in inp["universe"] if suspicious(r))
    if flagged:
        notes.append("data check: excluded until verified with split-adjusted data: " + ", ".join(flagged))
    ranked = rank_universe(inp["universe"], set(cfg["exclude"]) | crypto_syms)
    rank_of = {r["symbol"]: r["rank"] for r in ranked}
    all_held = {p["symbol"]: p for p in inp["positions"]}
    held = {s: p for s, p in all_held.items() if s not in crypto_syms}

    # --- exits ------------------------------------------------------------
    keep = []
    for sym, p in held.items():
        r = by_sym.get(sym)
        reason = None
        if r is not None and suspicious(r):
            notes.append(f"{sym}: data looks like a split artifact - verify before any exit decision; holding")
            keep.append(sym)
            continue
        if not risk_on:
            reason = "regime risk-off"
        elif r is None:
            reason = "left universe"
        elif r["price"] <= r["sma200"]:
            reason = "below 200DMA"
        elif rank_of.get(sym, 999) > cfg["hold_rank_buffer"]:
            reason = f"rank {rank_of.get(sym, 'n/a')} > {cfg['hold_rank_buffer']}"
        if reason:
            orders.append({"action": "SELL", "symbol": sym, "quantity": p["quantity"],
                           "cancel_stop_first": p.get("stop_price") is not None, "reason": reason})
        else:
            keep.append(sym)

    # --- crypto sleeve exits (absolute trend; ignores the SPY regime) -----
    crypto_cfg = cfg["crypto"]
    crypto_rows = {r["symbol"]: r for r in inp.get("crypto", [])}
    crypto_keep = []
    for sym, p in all_held.items():
        if sym not in crypto_syms:
            continue
        r = crypto_rows.get(sym)
        if r is None:
            notes.append(f"{sym}: no crypto data this run - holding, verify")
        elif r["price"] <= r["sma200"]:
            orders.append({"action": "SELL", "symbol": sym, "quantity": p["quantity"],
                           "cancel_stop_first": p.get("stop_price") is not None,
                           "reason": "crypto sleeve: below 200DMA"})
        else:
            crypto_keep.append(sym)

    # --- trail / repair stops on kept positions ---------------------------
    for sym in keep + crypto_keep:
        p, r = all_held[sym], by_sym.get(sym)
        if r is None:
            continue
        trail = round(r["price"] - cfg["stop_atr_mult"] * r["atr"], 2)
        old = p.get("stop_price")
        if old is None:
            orders.append({"action": "SET_STOP", "symbol": sym, "quantity": p["quantity"],
                           "stop_price": trail, "reason": "MISSING STOP - repair"})
        elif trail > old * 1.01:
            orders.append({"action": "RAISE_STOP", "symbol": sym, "quantity": p["quantity"],
                           "stop_price": trail, "old_stop": old, "reason": "trail 2.5xATR"})

    # --- trims: cut a position that outgrew its cap --------------------------
    rz = cfg.get("resize", {})
    drift = rz.get("min_drift", 0.25)
    min_trade = rz.get("min_trade_usd", cfg["min_position_usd"])
    stop_of = {o["symbol"]: o["stop_price"] for o in orders if o["action"] in ("RAISE_STOP", "SET_STOP")}
    cap_pct_of = lambda s: crypto_cfg["max_each_pct"] if s in crypto_syms else cfg["max_position_pct"]
    if rz.get("enabled") and rz.get("trim", True):
        for sym in keep + crypto_keep:
            p, r = all_held[sym], by_sym.get(sym)
            if r is None or (sym not in crypto_syms and suspicious(r)):
                continue
            value = p["quantity"] * r["price"]
            if value <= equity * cap_pct_of(sym) * (1 + drift):
                continue
            target = math.floor(equity * cap_pct_of(sym) / r["price"])
            qty = p["quantity"] - target
            if qty * r["price"] < min_trade:
                continue
            orders.append({"action": "TRIM", "symbol": sym, "quantity": qty, "ref_price": r["price"],
                           "remaining": target, "stop_price": stop_of.get(sym, p.get("stop_price")),
                           "reason": f"{value / equity:.0%} of equity > cap {cap_pct_of(sym):.0%}; trim to {target}"})

    # --- entries ----------------------------------------------------------
    vetoes, shrinks = inp.get("vetoes", {}), inp.get("shrinks", {})
    slots = cfg["max_positions"] - len(keep)
    px = lambda s: by_sym[s]["price"] if s in by_sym else all_held[s]["avg_cost"]
    trim_value = lambda syms: sum(o["quantity"] * o["ref_price"] for o in orders
                                  if o["action"] == "TRIM" and o["symbol"] in syms)
    invested = sum(all_held[s]["quantity"] * px(s) for s in keep + crypto_keep) - trim_value(set(keep + crypto_keep))
    budget = min(inp["cash"] + trim_value(set(keep + crypto_keep))
                 + sum(o["quantity"] * by_sym[o["symbol"]]["price"]
                       for o in orders if o["action"] == "SELL" and o["symbol"] in by_sym),
                 equity * cfg["max_gross_pct"] - invested)

    # crypto sleeve entries: price > SMA200 and positive 6-1 momentum; capped per ETF and in total
    crypto_room = (equity * crypto_cfg["max_total_pct"] - sum(all_held[s]["quantity"] * px(s) for s in crypto_keep)
                   + trim_value(crypto_syms))
    if not no_buys:
        for sym in crypto_cfg["symbols"]:
            r = crypto_rows.get(sym)
            if sym in all_held or r is None:
                continue
            if not (r["price"] > r["sma200"] and r["mom6"] > 0):
                notes.append(f"crypto {sym}: no trend (price {r['price']:.2f} vs SMA200 {r['sma200']:.2f}, mom6 {r['mom6']:.0%})")
                continue
            if sym in vetoes:
                notes.append(f"skip {sym}: LLM veto - {vetoes[sym]}")
                continue
            shrink = min(1.0, max(0.0, float(shrinks.get(sym, 1.0))))
            stop_dist = cfg["stop_atr_mult"] * r["atr"]
            cap = min(equity * crypto_cfg["max_each_pct"] * shrink, crypto_room, budget)
            qty = max(0, min(math.floor(equity * cfg["risk_per_trade"] * shrink / stop_dist),
                             math.floor(cap / r["price"])))
            cost = qty * r["price"]
            if qty == 0 or cost < cfg["min_position_usd"]:
                notes.append(f"skip {sym}: size too small (qty {qty}, ${cost:.0f})")
                continue
            crypto_room -= cost
            budget -= cost
            orders.append({"action": "BUY", "symbol": sym, "quantity": qty, "ref_price": r["price"],
                           "est_cost": round(cost, 2), "stop_price": round(r["price"] - stop_dist, 2),
                           "reason": f"crypto sleeve: above 200DMA, mom6 {r['mom6']:.0%}"
                                     + (f", shrunk x{shrink}" if shrink < 1 else "")})

    candidates = []
    if risk_on and not no_buys and slots > 0:
        for r in ranked:
            if len(candidates) >= slots:
                break
            sym = r["symbol"]
            if sym in held:
                continue
            dte = days_until(r.get("earnings_ymd"), today)
            if dte is not None and 0 <= dte <= cfg["earnings_blackout_days"]:
                notes.append(f"skip {sym}: earnings in {dte}d")
                continue
            if sym in vetoes:
                notes.append(f"skip {sym}: LLM veto - {vetoes[sym]}")
                continue
            shrink = min(1.0, max(0.0, float(shrinks.get(sym, 1.0))))
            qty, stop = size_position(r, equity, cfg, shrink)
            cost = qty * r["price"]
            if qty == 0 or cost < cfg["min_position_usd"]:
                notes.append(f"skip {sym}: size too small (qty {qty}, ${cost:.0f})")
                continue
            if cost > budget:
                notes.append(f"skip {sym}: over budget (${cost:.0f} > ${budget:.0f})")
                continue
            budget -= cost
            candidates.append(sym)
            orders.append({"action": "BUY", "symbol": sym, "quantity": qty, "ref_price": r["price"],
                           "est_cost": round(cost, 2), "stop_price": stop, "rank": r["rank"],
                           "score": round(r["score"], 2),
                           "reason": f"rank {r['rank']}, mom12 {r['mom12']:.0%}, mom6 {r['mom6']:.0%}"
                                     + (f", shrunk x{shrink}" if shrink < 1 else "")})

    # --- adds: top up held positions to today's entry size (after new entries) ---
    if rz.get("enabled") and not no_buys:
        trimmed = {o["symbol"] for o in orders if o["action"] == "TRIM"}
        for sym in keep + crypto_keep:
            p, r = all_held[sym], by_sym.get(sym)
            is_crypto = sym in crypto_syms
            if sym in trimmed or r is None:
                continue
            if is_crypto:
                if not (r["price"] > r["sma200"] and r["mom6"] > 0):
                    continue
            else:
                if not risk_on or suspicious(r) or rank_of.get(sym, 999) > rz.get("add_max_rank", cfg["hold_rank_buffer"]):
                    continue
                dte = days_until(r.get("earnings_ymd"), today)
                if dte is not None and 0 <= dte <= cfg["earnings_blackout_days"]:
                    notes.append(f"no add {sym}: earnings in {dte}d")
                    continue
            if sym in vetoes:
                notes.append(f"no add {sym}: LLM veto - {vetoes[sym]}")
                continue
            stop = stop_of.get(sym, p.get("stop_price"))
            if stop is None:
                continue
            shrink = min(1.0, max(0.0, float(shrinks.get(sym, 1.0))))
            dist = max(r["price"] - stop, cfg["stop_atr_mult"] * r["atr"])
            target = min(math.floor(equity * cfg["risk_per_trade"] * shrink / dist),
                         math.floor(equity * cap_pct_of(sym) * shrink / r["price"]))
            big_enough = lambda q: q >= max(1, p["quantity"] * drift) and q * r["price"] >= min_trade
            want = target - p["quantity"]
            if not big_enough(want):
                continue
            room = min(budget, crypto_room) if is_crypto else budget
            qty = min(want, math.floor(max(0.0, room) / r["price"]))
            if not big_enough(qty):
                notes.append(f"no add {sym}: over budget (target {target}, held {p['quantity']:g})")
                continue
            cost = qty * r["price"]
            budget -= cost
            if is_crypto:
                crypto_room -= cost
            orders.append({"action": "ADD", "symbol": sym, "quantity": qty, "ref_price": r["price"],
                           "est_cost": round(cost, 2), "held": p["quantity"], "new_total": p["quantity"] + qty,
                           "stop_price": stop, "rank": rank_of.get(sym),
                           "reason": f"top up {p['quantity']:g} -> {p['quantity'] + qty:g} (target {target})"
                                     + (f", shrunk x{shrink}" if shrink < 1 else "")})

    # --- guard: order count ----------------------------------------------
    if len(orders) > cfg["max_orders_per_run"]:
        notes.append(f"Order count {len(orders)} > limit; truncating BUYs/ADDs.")
        sells = [o for o in orders if o["action"] not in ("BUY", "ADD")]
        buys = [o for o in orders if o["action"] in ("BUY", "ADD")]
        orders = sells + buys[: max(0, cfg["max_orders_per_run"] - len(sells))]

    # --- kill switch: set by the user, checked last so nothing above can re-add orders ---
    if state.get("kill"):
        notes.append(f"KILL SWITCH: {state.get('kill_reason') or 'set by user'}. No orders of any kind; "
                     "broker-side stops stay. Human must clear it (strategy.py unkill).")
        orders = []

    return {"date": inp["date"], "equity": equity, "peak_equity": peak,
            "drawdown": round(drawdown, 4), "risk_on": risk_on, "notes": notes,
            "top10": [{k: (round(r[k], 3) if isinstance(r[k], float) else r[k])
                       for k in ("rank", "symbol", "price", "mom12", "mom6", "score")} for r in ranked[:10]],
            "orders": orders}, state


def check(inp, cfg=CONFIG):
    """Post-trade audit: every position has a stop, caps respected."""
    problems = []
    equity = inp["equity"]
    prices = {r["symbol"]: r["price"] for r in inp.get("universe", [])}
    prices.update({r["symbol"]: r["price"] for r in inp.get("crypto", [])})
    crypto_syms = set(cfg["crypto"]["symbols"])
    gross = crypto = 0
    for p in inp["positions"]:
        px = prices.get(p["symbol"], p.get("price", p["avg_cost"]))
        val = p["quantity"] * px
        gross += val
        if p["symbol"] in crypto_syms:
            crypto += val
        if p.get("stop_price") is None:
            problems.append(f"{p['symbol']}: NO STOP ORDER")
        if val > equity * (cfg["max_position_pct"] + 0.05):
            problems.append(f"{p['symbol']}: {val / equity:.0%} of equity > cap")
    n_equity = sum(1 for p in inp["positions"] if p["symbol"] not in crypto_syms)
    if n_equity > cfg["max_positions"]:
        problems.append(f"{n_equity} stock positions > {cfg['max_positions']}")
    if crypto > equity * (cfg["crypto"]["max_total_pct"] + 0.05):
        problems.append(f"crypto sleeve {crypto / equity:.0%} of equity > cap")
    if gross > equity * (cfg["max_gross_pct"] + 0.05):
        problems.append(f"gross exposure {gross / equity:.0%} > cap")
    return problems


def main():
    if len(sys.argv) >= 2 and sys.argv[1] in ("kill", "unkill"):
        state = load_state()
        state["kill"] = sys.argv[1] == "kill"
        state["kill_reason"] = " ".join(sys.argv[2:]) or None if state["kill"] else None
        save_state(state)
        print(json.dumps(state, indent=2))
        return
    if len(sys.argv) != 3 or sys.argv[1] not in ("plan", "check"):
        sys.exit("usage: strategy.py plan|check <input.json>  |  strategy.py kill [reason]  |  strategy.py unkill")
    inp_path = ROOT / sys.argv[2]  # an absolute argv path wins over ROOT
    inp = json.loads(inp_path.read_text())
    if sys.argv[1] == "check":
        problems = check(inp)
        print(json.dumps({"ok": not problems, "problems": problems}, indent=2))
        sys.exit(1 if problems else 0)
    result, state = plan(inp)
    save_state(state)
    out = inp_path.with_name("plan.json")
    out.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
