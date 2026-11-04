#!/usr/bin/env python3
"""Fill universe earnings dates in input.json from a Robinhood earnings calendar response. Pure stdlib.

The scanner's fundamental.earningsYmd column comes back empty, so the earnings blackout needs this instead.
Save the response of get_earnings_calendar(start_date=<run date>, days=14, filter="high_market_cap")
to a file, then run:
    python3 earnings.py run/<date>/input.json <calendar.json>
Each universe row gets earnings_ymd = its next report on or after the run date (null if none in the
window), and input.json gets "earnings_source", which strategy.py requires before it buys stocks.
Unverified (tentative) dates count: the blackout errs toward skipping.
"""
import json
import sys
from datetime import date

import datadir

ROOT = datadir.resolve()


def reports(raw):
    """Accept the tool's {data: {results}}, a bare {results}, or a plain list."""
    if isinstance(raw, dict):
        raw = raw.get("data", raw)
        raw = raw.get("results", raw) if isinstance(raw, dict) else raw
    if not isinstance(raw, list):
        sys.exit("calendar file has no results list")
    return raw


def main(input_path, calendar_path):
    path = ROOT / input_path
    inp = json.loads(path.read_text())
    today = date.fromisoformat(inp["date"])
    rows = reports(json.loads((ROOT / calendar_path).read_text()))
    if not rows:
        sys.exit("calendar is empty; refusing to mark the universe as earnings-free")

    nxt, last = {}, today
    for r in rows:
        d = date.fromisoformat((r.get("report") or {}).get("date") or "1900-01-01")
        last = max(last, d)
        sym = r.get("symbol")
        if sym and d >= today and (sym not in nxt or d < nxt[sym]):
            nxt[sym] = d

    matched = []
    for row in inp["universe"]:
        d = nxt.get(row["symbol"])
        row["earnings_ymd"] = d.strftime("%Y%m%d") if d else None
        if d:
            matched.append(row["symbol"])
    inp["earnings_source"] = {"source": "robinhood get_earnings_calendar", "file": str(calendar_path),
                              "through": last.isoformat(), "reports": len(rows), "matched": len(matched)}
    path.write_text(json.dumps(inp, indent=1))
    soon = sorted((nxt[s], s) for s in matched)
    print(json.dumps({**inp["earnings_source"],
                      "upcoming": [f"{s} {d.isoformat()}" for d, s in soon]}, indent=1))


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit("usage: earnings.py run/<date>/input.json <calendar.json>")
    main(sys.argv[1], sys.argv[2])
