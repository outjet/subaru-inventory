#!/usr/bin/env python3
"""
Check for newly-posted CPO Subaru Outback Touring XT listings under a price
cap, and send a Pushover alert when one shows up that we haven't seen before.

Persists the set of already-seen VINs to a JSON state file (checked into the
repo by the GitHub Actions workflow) so re-runs only alert on genuinely new
postings, not ones we already know about.

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


def load_seen():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return set(json.load(f))
    return set()


def save_seen(vins):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w") as f:
        json.dump(sorted(vins), f, indent=2)


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

    seen = load_seen()
    new_matches = [m for m in matches if m["vin"] not in seen]
    print(f"  {len(new_matches)} are new (not previously alerted)")

    notified_vins = set()
    if not new_matches:
        print("No new matches — no notification sent.")
    elif len(new_matches) > 10:
        # Large batch (e.g. a manual state reset) -- one summary instead of a notification storm.
        lines = [f"{m['year']} — ${m['price']:,}, {m['dealer']}" for m in new_matches]
        title = f"{len(new_matches)} new Outback Touring XT under ${args.max_price:,}"
        message = "\n".join(lines)
        try:
            send_pushover(title, message, url=new_matches[0]["url"], url_title="First listing")
            notified_vins = {m["vin"] for m in new_matches}
        except Exception as e:
            print(f"Failed to send summary notification: {e}")
    else:
        title = f"New Outback Touring XT under ${args.max_price:,}"
        for m in new_matches:
            dt = m.get("driveText")
            dealer_line = f"{m['dealer']} ({m['distance']}mi, ~{dt} drive)" if dt else f"{m['dealer']} ({m['distance']}mi)"
            message = f"{m['year']} — ${m['price']:,}, {m['mileage']:,}mi\n{dealer_line}"
            try:
                send_pushover(title, message, url=m["url"], url_title="View listing")
                notified_vins.add(m["vin"])
            except Exception as e:
                print(f"Failed to notify for VIN {m['vin']}: {e}")

    all_current_vins = seen | notified_vins
    save_seen(all_current_vins)
    unnotified = len(new_matches) - len(notified_vins)
    if unnotified > 0:
        print(f"State updated: {len(all_current_vins)} VINs tracked total "
              f"({unnotified} new match(es) left unnotified due to send failures, will retry next run).")
    else:
        print(f"State updated: {len(all_current_vins)} VINs tracked total.")


if __name__ == "__main__":
    main()
