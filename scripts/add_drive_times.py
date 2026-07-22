#!/usr/bin/env python3
"""
One-time (well, one-time-per-dealer) enrichment: adds real drive time from a
given ZIP to every dealer in data/outback_data_200mi.json, using the cached
Google Directions lookup in drive_time.py. Safe to re-run -- dealers already
in data/drive_times_cache.json cost zero additional API calls.

Usage:
  python3 add_drive_times.py --zip 44107 --data ../data/outback_data_200mi.json
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fetch_cpo_inventory import get_dealers_within_radius  # noqa: E402
from drive_time import get_drive_times_batch  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--zip", default="44107")
    ap.add_argument("--radius", type=int, default=200)
    ap.add_argument("--data", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "outback_data_200mi.json"))
    args = ap.parse_args()

    dealers = get_dealers_within_radius(args.zip, args.radius)
    addr_by_dealer = {
        d["name"]: f'{d["street"]}, {d["city"]}, {d["state"]} {d["zipcode"]}'
        for d in dealers if d.get("street")
    }
    print(f"Looking up drive times for {len(addr_by_dealer)} dealers from {args.zip}...")
    drive_times = get_drive_times_batch(f"{args.zip} USA", addr_by_dealer)

    missing = [name for name, result in drive_times.items() if result is None]
    if missing:
        print(f"No drive time found for {len(missing)} dealer(s): {missing}")

    rows = json.load(open(args.data))
    updated = 0
    for r in rows:
        dt = drive_times.get(r["dealer"])
        if dt:
            r["driveMinutes"] = dt["minutes"]
            r["driveText"] = dt["text"]
            updated += 1
    json.dump(rows, open(args.data, "w"))
    print(f"Updated {updated}/{len(rows)} vehicle rows with drive time.")


if __name__ == "__main__":
    main()
