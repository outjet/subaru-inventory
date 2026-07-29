#!/usr/bin/env python3
"""
Watch CPO Subaru Outback Touring XT listings under a price cap and send a
Pushover alert on two events:
  * a listing we've never seen appears under the cap        ("new listing")
  * a listing we already alerted on drops >= --drop-threshold ("price drop")

State is a JSON map of VIN -> last alerted price (checked into the repo by the
GitHub Actions workflow), so re-runs only fire on genuinely new postings or on
a fresh new low for a car we're already tracking. The legacy list-of-VINs state
file is migrated automatically.

Env vars (set as GitHub Actions secrets):
  PUSHOVER_USER_KEY   - your Pushover user key
  PUSHOVER_APP_TOKEN  - your Pushover application/API token

Usage:
  python3 check_new_touring_xt.py --zip 44107 --radius 200 --trim "Touring XT" --max-price 45000
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fetch_cpo_inventory import get_dealers_within_radius, get_cpo_inventory, simplify  # noqa: E402
from drive_time import get_drive_times_batch  # noqa: E402

STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "seen_touring_xt_vins.json")


def load_rows(args):
    """Return simplified, drive-time-enriched listing rows. When --from-file is
    given, read a pre-fetched snapshot (the shared per-run fetch) instead of
    hitting subaru.com again; otherwise fetch live and enrich here."""
    if args.from_file:
        with open(args.from_file) as f:
            rows = json.load(f)
        print(f"  {len(rows)} listings loaded from {args.from_file}")
        return rows
    dealers = get_dealers_within_radius(args.zip, args.radius)
    print(f"  {len(dealers)} CPO-flagged dealers")
    dist_by_dealer = {d["name"]: d["distance"] for d in dealers}
    addr_by_dealer = {
        d["name"]: f'{d["street"]}, {d["city"]}, {d["state"]} {d["zipcode"]}'
        for d in dealers if d.get("street")
    }
    items = get_cpo_inventory(args.model, [d["id"] for d in dealers])
    rows = [simplify(it, dist_by_dealer) for it in items]
    drive = get_drive_times_batch(f"{args.zip} USA", addr_by_dealer)
    for r in rows:
        dt = drive.get(r["dealer"])
        if dt:
            r["driveMinutes"], r["driveText"] = dt["minutes"], dt["text"]
    return rows


def load_state():
    """State maps VIN -> last price we alerted on (the reference for drop
    detection). Migrates the legacy list-of-VINs format: those VINs come back
    with price None, meaning "seen, baseline unknown" — we record their current
    price on the next run without firing a spurious drop alert."""
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            data = json.load(f)
        if isinstance(data, list):
            return {vin: None for vin in data}
        return data
    return {}


def save_state(state):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w") as f:
        json.dump(dict(sorted(state.items())), f, indent=2)


def send_pushover(title, message, url=None, url_title=None):
    user_key = os.environ.get("PUSHOVER_USER_KEY")
    app_token = os.environ.get("PUSHOVER_APP_TOKEN")
    if not user_key or not app_token:
        print("PUSHOVER_USER_KEY / PUSHOVER_APP_TOKEN not set — skipping notification, printing instead:")
        print(title)
        print(message)
        return
    data = {"token": app_token, "user": user_key, "title": title, "message": message[:1024]}
    if url:
        data["url"] = url
        data["url_title"] = url_title or "View listing"
    req = urllib.request.Request(
        "https://api.pushover.net/1/messages.json",
        data=urllib.parse.urlencode(data).encode(),
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = resp.read().decode()
            print(f"Pushover response: {resp.status} {body}")
    except urllib.error.HTTPError as e:
        print(f"Pushover error: HTTP {e.code} {e.reason} — {e.read().decode()}")
        raise


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--zip", default="44107")
    ap.add_argument("--radius", type=int, default=200)
    ap.add_argument("--model", default="OBK")
    ap.add_argument("--trim", default="Touring XT")
    ap.add_argument("--year", type=int, default=2026)
    ap.add_argument("--max-price", type=int, default=45000)
    ap.add_argument("--drop-threshold", type=int, default=200,
                    help="re-alert when a known listing drops at least this many $ below its last alerted price")
    ap.add_argument("--from-file", default=None,
                    help="read a pre-fetched snapshot JSON instead of fetching live (shared per-run fetch)")
    args = ap.parse_args()

    print(f"Checking CPO {args.year} {args.model} '{args.trim}' under ${args.max_price:,} within {args.radius}mi of {args.zip}...")
    rows = load_rows(args)

    matches = []
    for r in rows:
        price = r.get("price") or 0
        if r.get("trim") != args.trim:
            continue
        if r.get("year") != args.year:
            continue
        if price <= 0 or price >= args.max_price:
            continue
        matches.append({
            "vin": r.get("vin"),
            "year": r.get("year"),
            "price": price,
            "mileage": r.get("mileage"),
            "dealer": r.get("dealer"),
            "distance": r.get("distance"),
            "url": r.get("url"),
            "driveText": r.get("driveText"),
        })

    print(f"  {len(matches)} match trim/price filter")

    # Classify each match against saved state:
    #   new  -> VIN we've never alerted on          (fires "new listing")
    #   drop -> known VIN now >= threshold cheaper   (fires "price drop")
    # A known VIN with an unknown baseline (None, from the legacy format) just
    # gets its baseline recorded here, silently, so it can't false-alarm.
    state = load_state()
    new_matches, drop_matches = [], []
    for m in matches:
        vin, price = m["vin"], m["price"]
        if vin not in state:
            new_matches.append(m)
        elif state[vin] is None:
            state[vin] = price  # establish baseline, no alert
        elif price <= state[vin] - args.drop_threshold:
            m["prevPrice"] = state[vin]
            drop_matches.append(m)
    print(f"  {len(new_matches)} new, {len(drop_matches)} price drop(s) (≥ ${args.drop_threshold})")

    def dealer_line(m):
        dt = m.get("driveText")
        return f"{m['dealer']} ({m['distance']}mi, ~{dt} drive)" if dt else f"{m['dealer']} ({m['distance']}mi)"

    notified = set()

    # --- New listings ---
    if not new_matches:
        print("No new listings.")
    elif len(new_matches) > 10:
        # Large batch (e.g. a manual state reset) -- one summary instead of a storm.
        lines = [f"{m['year']} — ${m['price']:,}, {m['dealer']}" for m in new_matches]
        title = f"{len(new_matches)} new Outback Touring XT under ${args.max_price:,}"
        try:
            send_pushover(title, "\n".join(lines), url=new_matches[0]["url"], url_title="First listing")
            notified |= {m["vin"] for m in new_matches}
        except Exception as e:
            print(f"Failed to send summary notification: {e}")
    else:
        title = f"New Outback Touring XT under ${args.max_price:,}"
        for m in new_matches:
            message = f"{m['year']} — ${m['price']:,}, {m['mileage']:,}mi\n{dealer_line(m)}"
            try:
                send_pushover(title, message, url=m["url"], url_title="View listing")
                notified.add(m["vin"])
            except Exception as e:
                print(f"Failed to notify for VIN {m['vin']}: {e}")

    # --- Price drops on already-known listings ---
    for m in drop_matches:
        delta = m["prevPrice"] - m["price"]
        title = f"Price drop — {m['year']} Touring XT"
        message = (f"↓ ${delta:,} — was ${m['prevPrice']:,}, now ${m['price']:,} ({m['mileage']:,}mi)\n"
                   f"{dealer_line(m)}")
        try:
            send_pushover(title, message, url=m["url"], url_title="View listing")
            notified.add(m["vin"])
        except Exception as e:
            print(f"Failed to send drop alert for VIN {m['vin']}: {e}")

    # Record the new reference price only for listings we actually notified about;
    # unsent ones keep their old baseline and retry next run.
    for m in new_matches + drop_matches:
        if m["vin"] in notified:
            state[m["vin"]] = m["price"]

    save_state(state)
    unsent = (len(new_matches) + len(drop_matches)) - len([m for m in new_matches + drop_matches if m["vin"] in notified])
    tail = f" ({unsent} left unsent due to failures, will retry)" if unsent else ""
    print(f"State updated: {len(state)} VINs tracked.{tail}")


if __name__ == "__main__":
    main()
