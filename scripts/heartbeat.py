#!/usr/bin/env python3
"""
Weekly "still watching" Pushover heartbeat, so silence between alerts is
reassuring rather than ambiguous ("is it broken, or is nothing happening?").

Reads the latest committed snapshot in data/inventory_history.json (written by
snapshot_inventory.py every run) — it does NOT fetch, so it adds no API load and
can't get blocked. Summarizes, per watched trim, how many qualify under the cap
and the cheapest one, and warns if the underlying data has gone stale.

Env vars (GitHub Actions secrets): PUSHOVER_USER_KEY, PUSHOVER_APP_TOKEN

Usage:
  python3 heartbeat.py --year 2026 --max-price 45000 --trims "Touring XT" "Limited XT"
"""
import argparse
import json
import os
from datetime import date

from check_new_touring_xt import send_pushover  # reuse the sender

HISTORY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "inventory_history.json")


def days_on_lot(entry, today):
    first = date.fromisoformat(entry["firstSeen"])
    end = today if entry.get("active") else date.fromisoformat(entry["delistedDate"])
    return (end - first).days


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--history", default=HISTORY_FILE)
    ap.add_argument("--trims", nargs="+", default=["Touring XT", "Limited XT"])
    ap.add_argument("--year", type=int, default=2026)
    ap.add_argument("--max-price", type=int, default=45000)
    ap.add_argument("--stale-days", type=int, default=2,
                    help="warn in the heartbeat if the last snapshot is at least this many days old")
    args = ap.parse_args()

    with open(args.history) as f:
        history = json.load(f)
    today = date.today()
    vehicles = list(history["vehicles"].values())
    last_snap = history.get("meta", {}).get("lastSnapshot")

    lines = []
    total_under = 0
    for trim in args.trims:
        cands = [e for e in vehicles
                 if e.get("active") and e.get("trim") == trim and e.get("year") == args.year
                 and (e.get("currentPrice") or 0) > 0]
        under = sorted([e for e in cands if e["currentPrice"] < args.max_price],
                       key=lambda e: e["currentPrice"])
        total_under += len(under)
        if under:
            best = under[0]
            lines.append(f"{trim}: {len(under)} under cap — cheapest ${best['currentPrice']:,} "
                         f"({best['dealer']}, {days_on_lot(best, today)}d)")
        elif cands:
            nearest = min(cands, key=lambda e: e["currentPrice"])
            lines.append(f"{trim}: none under ${args.max_price:,} — closest ${nearest['currentPrice']:,} "
                         f"({nearest['dealer']})")
        else:
            lines.append(f"{trim}: none listed")

    # Staleness check: if the pipeline has stopped, say so loudly at the top.
    warn = ""
    if last_snap:
        age = (today - date.fromisoformat(last_snap)).days
        if age >= args.stale_days:
            warn = f"⚠ DATA STALE — last update {last_snap} ({age}d ago); scraper may be down.\n"

    title = f"Still watching {args.year} Outback CPO ≤ ${args.max_price:,}"
    footer = f"Data as of {last_snap}." if last_snap else ""
    message = warn + "\n".join(lines) + ("\n" + footer if footer else "")
    print(title)
    print(message)
    send_pushover(title, message,
                  url="https://outjet.github.io/subaru-toyota-inventory/", url_title="Open dashboard")


if __name__ == "__main__":
    main()
