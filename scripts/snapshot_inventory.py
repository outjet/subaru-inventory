#!/usr/bin/env python3
"""
Snapshot the full CPO Outback inventory (every trim) and merge it into a
running per-VIN history, so we can derive things the Subaru API never gives us
directly and that a single snapshot can't show:

  * days on lot     — firstSeen -> today (or delistedDate), per VIN
  * price drops      — whether/when/how much a dealer has already cut a price
  * market velocity  — what delisted (sold) and how fast

State file: data/inventory_history.json
  {
    "meta": { "trackingSince", "lastSnapshot", "zip", "radius",
              "trend": { "<trim>": [{"date","n","median","min","max"}, ...] } },
    "vehicles": { "<vin>": { ...per-car..., "priceHistory": [{"date","price"}] } }
  }

The per-trim `trend` medians are recorded once per calendar date (idempotent),
computed at snapshot time from that run's full live set — so the trend line is
exact rather than reconstructed from the change-only price history.

Kept separate from check_new_touring_xt.py (which only cares about one trim for
alerting) — comps need the whole market.

Usage:
  python3 snapshot_inventory.py --zip 44107 --radius 200 --model OBK
  python3 snapshot_inventory.py --from-file ../data/outback_data_200mi.json   # seed/offline
"""
import argparse
import json
import os
import statistics
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fetch_cpo_inventory import get_dealers_within_radius, get_cpo_inventory, simplify  # noqa: E402
from drive_time import get_drive_times_batch  # noqa: E402

HISTORY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "inventory_history.json")

# Fields we carry on each vehicle straight from a flat "simplified" row.
CARRY = ("year", "trim", "dealer", "color", "interior", "url", "sticker",
         "msrp", "distance", "driveMinutes", "driveText")


def load_history():
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE) as f:
            h = json.load(f)
            h.setdefault("meta", {})
            h["meta"].setdefault("trend", {})
            h.setdefault("vehicles", {})
            return h
    return {"meta": {"trend": {}}, "vehicles": {}}


def save_history(history):
    os.makedirs(os.path.dirname(HISTORY_FILE), exist_ok=True)
    with open(HISTORY_FILE, "w") as f:
        json.dump(history, f, indent=2, sort_keys=True)


def live_rows(zipcode, radius, model):
    dealers = get_dealers_within_radius(zipcode, radius)
    dist_by_dealer = {d["name"]: d["distance"] for d in dealers}
    addr_by_dealer = {
        d["name"]: f'{d["street"]}, {d["city"]}, {d["state"]} {d["zipcode"]}'
        for d in dealers if d.get("street")
    }
    items = get_cpo_inventory(model, [d["id"] for d in dealers])
    rows = [simplify(it, dist_by_dealer) for it in items]
    # Enrich with drive time (cached forever per dealer; degrades to None w/o API key).
    drive = get_drive_times_batch(f"{zipcode} USA", addr_by_dealer)
    for r in rows:
        dt = drive.get(r["dealer"])
        if dt:
            r["driveMinutes"] = dt["minutes"]
            r["driveText"] = dt["text"]
    return rows


def record_trend(history, rows, today, years, trims):
    """Append today's median price, once per date, for each watched
    (model-year, trim) — keyed "<year> <trim>" (e.g. "2026 Touring XT") so the
    trend tracks exactly the cars we care about rather than all model years
    pooled together."""
    trend = history["meta"]["trend"]
    priced = [r for r in rows if (r.get("price") or 0) > 0]
    for y in years:
        for t in trims:
            group = [r for r in priced if r.get("year") == y and r.get("trim") == t]
            if not group:
                continue
            series = trend.setdefault(f"{y} {t}", [])
            if any(p["date"] == today for p in series):
                continue
            prices = sorted(g["price"] for g in group)
            series.append({
                "date": today, "n": len(group),
                "median": int(statistics.median(prices)),
                "min": prices[0], "max": prices[-1],
            })
            series.sort(key=lambda p: p["date"])


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--zip", default="44107")
    ap.add_argument("--radius", type=int, default=200)
    ap.add_argument("--model", default="OBK")
    ap.add_argument("--from-file", default=None,
                    help="ingest a flat simplified-rows JSON instead of fetching live (for seeding/testing)")
    ap.add_argument("--trend-years", type=int, nargs="+", default=[2026],
                    help="model years to record trend series for")
    ap.add_argument("--trend-trims", nargs="+", default=["Touring XT", "Limited XT"],
                    help="trims to record trend series for")
    args = ap.parse_args()

    today = date.today().isoformat()

    if args.from_file:
        with open(args.from_file) as f:
            rows = json.load(f)
        print(f"Snapshot {today}: ingesting {len(rows)} rows from {args.from_file}")
    else:
        rows = live_rows(args.zip, args.radius, args.model)
        print(f"Snapshot {today}: {len(rows)} live {args.model} listings")

    history = load_history()
    vehicles = history["vehicles"]

    # Guard: a failed/blocked fetch that returns nothing (or a tiny fraction of
    # what we had) must NOT mutate history — otherwise it would mass-delist the
    # whole market and write a garbage trend point. Bail loudly so the workflow
    # run fails, the page stops updating, and the dashboard's freshness banner
    # flips to stale instead of silently showing corrupted data.
    active_before = sum(1 for e in vehicles.values() if e.get("active"))
    if not rows or (active_before and len(rows) < active_before * 0.5):
        print(f"ABORT: fetched {len(rows)} rows but {active_before} were active last run — "
              f"refusing to corrupt history. Leaving state untouched.", file=sys.stderr)
        sys.exit(1)

    meta = history["meta"]
    meta.setdefault("trackingSince", today)
    meta["lastSnapshot"] = today
    meta["zip"], meta["radius"] = args.zip, args.radius

    seen, new, price_changes, reactivated = set(), 0, 0, 0
    for r in rows:
        vin = r.get("vin")
        if not vin:
            continue
        seen.add(vin)
        price = r.get("price") or 0
        base = {k: r.get(k) for k in CARRY}
        if vin not in vehicles:
            vehicles[vin] = {
                "vin": vin, **base,
                "firstSeen": today, "lastSeen": today,
                "active": True, "delistedDate": None,
                "currentPrice": price, "currentMileage": r.get("mileage"),
                "priceHistory": [{"date": today, "price": price}] if price > 0 else [],
            }
            new += 1
        else:
            e = vehicles[vin]
            if not e.get("active"):
                reactivated += 1
            e.update(base)
            e["lastSeen"] = today
            e["active"] = True
            e["delistedDate"] = None
            e["currentMileage"] = r.get("mileage")
            last = e["priceHistory"][-1]["price"] if e["priceHistory"] else None
            if price > 0 and price != last:
                e["priceHistory"].append({"date": today, "price": price})
                price_changes += 1
            if price > 0:
                e["currentPrice"] = price

    delisted = 0
    for vin, e in vehicles.items():
        if vin not in seen and e.get("active"):
            e["active"] = False
            e["delistedDate"] = today
            delisted += 1

    record_trend(history, rows, today, args.trend_years, args.trend_trims)
    save_history(history)

    print(f"  {new} new · {price_changes} price change(s) · {reactivated} relisted · {delisted} newly delisted")
    print(f"  {len(vehicles)} VINs tracked all-time (since {meta['trackingSince']})")


if __name__ == "__main__":
    main()
