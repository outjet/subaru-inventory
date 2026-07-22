#!/usr/bin/env python3
"""
Pull Subaru Certified Pre-Owned inventory within a mile radius of a ZIP code.

Pipeline (mirrors what subaru.com's own CPO search page does):
  1. GET services/dealers/distances/by/zipcode?zipcode=...  -> every active dealer, sorted by distance
  2. Filter to dealers with a "Cpo" flag in their `types` list, within the requested radius
  3. GET services/graphql/cpoinventory?...&dealerCode=<comma list>  -> paginated vehicle listings

Usage:
  python3 fetch_cpo_inventory.py --zip 44107 --radius 200 --model OBK --out ../data/outback_200mi.json

Model codes seen so far: OBK=Outback, FOR=Forester, CTK=Crosstrek, ASC=Ascent,
LEG=Legacy, IMP=Impreza, WRX=WRX, BRZ=BRZ, SOL=Solterra, TSK=Trailseeker, UNC=Uncharted.
"""
import argparse
import json
import time
import urllib.parse
import urllib.request

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36")
REFERER = "https://www.subaru.com/vehicle-info/certified-pre-owned/certified-pre-owned.html"


def fetch_json(url):
    req = urllib.request.Request(url, headers={
        "Accept": "application/json, text/plain, */*",
        "Referer": REFERER,
        "User-Agent": UA,
    })
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def get_dealers_within_radius(zipcode, radius, cpo_only=True):
    url = f"https://www.subaru.com/services/dealers/distances/by/zipcode?zipcode={zipcode}&count=750&type=Active"
    dealers = fetch_json(url)
    out = []
    for d in dealers:
        if d["distance"] > radius:
            continue
        if cpo_only and "Cpo" not in d["dealer"]["types"]:
            continue
        addr = d["dealer"]["address"]
        out.append({
            "id": d["dealer"]["id"],
            "name": d["dealer"]["name"],
            "street": addr.get("street"),
            "city": addr["city"],
            "state": addr["state"],
            "zipcode": addr.get("zipcode"),
            "distance": round(d["distance"], 1),
        })
    return out


def get_cpo_inventory(model, dealer_ids, items_per_page=30):
    dealer_param = ",".join(dealer_ids)
    all_items = []
    page = 0
    total_pages = 1
    while page < total_pages:
        qs = urllib.parse.urlencode({
            "modelList": model,
            "models": model,
            "page": page,
            "dealerCode": dealer_param,
            "sortBy": "asc",
            "itemsPerPage": items_per_page,
        }, safe=",")
        url = f"https://www.subaru.com/services/graphql/cpoinventory?{qs}"
        data = fetch_json(url)
        wrapper = data.get("pagedListWrapper") or {}
        items = wrapper.get("items") or []
        all_items.extend(items)
        pager = wrapper.get("pager") or {}
        total_pages = pager.get("totalPages", 1) or 1
        page += 1
        if page < total_pages:
            time.sleep(0.4)  # be polite between paged requests
    return all_items


def simplify(item, dist_by_dealer):
    price = item.get("internetPrice") or item.get("cpoInternetPrice") or 0
    dealer = (item.get("dealership") or "").strip()
    sticker = item.get("windowstickerUrl")
    return {
        "vin": item.get("vinNumber"),
        "year": item.get("year"),
        "trim": item.get("trimName"),
        "mileage": item.get("mileage"),
        "price": price,
        "msrp": item.get("msrp") or 0,
        "color": (item.get("exteriorColor") or {}).get("name"),
        "colorRgb": (item.get("exteriorColor") or {}).get("rgb"),
        "interior": (item.get("interiorColor") or {}).get("name"),
        "dealer": dealer,
        "distance": dist_by_dealer.get(dealer),
        "stock": item.get("stockNumber"),
        "url": item.get("detailsUrl"),
        "sticker": ("https://www.subaru.com" + sticker) if sticker else None,
        "wilderness": "Wilderness" in (item.get("trimName") or ""),
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--zip", required=True, help="ZIP code to search around")
    ap.add_argument("--radius", type=int, default=200, help="max miles from ZIP (default 200)")
    ap.add_argument("--model", default="OBK", help="model code, e.g. OBK for Outback")
    ap.add_argument("--out", default=None, help="output JSON path (default: prints summary only)")
    args = ap.parse_args()

    print(f"Looking up dealers within {args.radius}mi of {args.zip}...")
    dealers = get_dealers_within_radius(args.zip, args.radius)
    print(f"  {len(dealers)} CPO-flagged active dealers found")

    print(f"Fetching CPO inventory for model={args.model} across {len(dealers)} dealers...")
    items = get_cpo_inventory(args.model, [d["id"] for d in dealers])
    print(f"  {len(items)} vehicles found")

    dist_by_dealer = {d["name"]: d["distance"] for d in dealers}
    rows = [simplify(it, dist_by_dealer) for it in items]
    rows.sort(key=lambda r: (r["price"] == 0, r["price"]))

    if args.out:
        with open(args.out, "w") as f:
            json.dump(rows, f, indent=2)
        print(f"Saved {len(rows)} rows to {args.out}")
    else:
        priced = [r["price"] for r in rows if r["price"] > 0]
        if priced:
            print(f"Price range: ${min(priced):,} - ${max(priced):,}")
        print("Pass --out to write the full JSON to a file.")


if __name__ == "__main__":
    main()
