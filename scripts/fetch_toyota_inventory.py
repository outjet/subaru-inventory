#!/usr/bin/env python3
"""
Pull Toyota Certified (CPO) inventory within a mile radius of a ZIP code.

Unlike Subaru, Toyota's search API takes `zipcode` + `radius` directly as
query params on the same endpoint -- no separate dealer-locator lookup needed.

  GET https://www.toyotacertified.com/rest/uvii/vehicles?zipcode=...&radius=...miles&...

Usage:
  python3 fetch_toyota_inventory.py --zip 44107 --radius 250 --model "TOYOTA CROWN SIGNIA" --out ../data/crown_signia_250mi.json

The search response itself has no per-vehicle detail-page URL, but individual
listings live at https://www.toyotacertified.com/vdp?vin=<VIN> -- this script
builds that link from each vehicle's VIN.
"""
import argparse
import json
import time
import urllib.parse
import urllib.request

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36")


def fetch_page(zipcode, radius, page_no, page_size, model=None, ext_color=None,
               year_min=2015, year_max=2026, mileage_min=0, mileage_max=125000,
               price_min=5000, price_max=200000):
    params = {
        "zipcode": zipcode,
        "pageNo": page_no,
        "pageSize": page_size,
        "brand": "TOYOTA",
        "radius": f"{radius}miles",
        "certificationStatus": "CERTIFIED",
        "yearMin": year_min,
        "yearMax": year_max,
        "mileageMin": mileage_min,
        "mileageMax": mileage_max,
        "priceMin": price_min,
        "priceMax": price_max,
        "sort": "location asc",
    }
    if model:
        params["baseModelName"] = model
    if ext_color:
        params["extColor"] = ext_color
    qs = urllib.parse.urlencode(params)
    url = f"https://www.toyotacertified.com/rest/uvii/vehicles?{qs}"
    req = urllib.request.Request(url, headers={
        "accept": "*/*",
        "accept-language": "en-US,en;q=0.9",
        "content-type": "application/json",
        "referer": f"https://www.toyotacertified.com/inventory?zipCode={zipcode}&radius={radius}",
        "user-agent": UA,
    })
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def simplify(v):
    price = v.get("price") or {}
    model = v.get("model") or {}
    ext = v.get("extColor") or {}
    mpg = v.get("mpg") or {}
    drivetrain = v.get("drivetrain") or {}
    vin = v.get("vin")
    return {
        "vin": vin,
        "vdpUrl": f"https://www.toyotacertified.com/vdp?vin={vin}" if vin else None,
        "stock": v.get("stockNumber"),
        "year": v.get("year"),
        "trim": model.get("marketingName") or v.get("grade"),
        "mileage": int(v.get("mileage") or 0),
        "price": price.get("sellingPrice") or price.get("advertizedPrice") or 0,
        "msrp": price.get("baseMsrp") or 0,
        "color": ext.get("marketingName"),
        "colorHex": ext.get("hexCode"),
        "dealer": v.get("owningDealerName"),
        "certificationType": v.get("certificationType"),
        "drivetrain": drivetrain.get("title"),
        "mpgCombined": mpg.get("combined"),
        "bodyStyle": v.get("bodyStyle"),
        "oneOwner": ((v.get("carFaxReport") or {}).get("ownerHistory") or {}).get("oneOwner"),
        "noAccidents": ((v.get("carFaxReport") or {}).get("accident") or {}).get("hasAccidents") is False,
        "thumbnail": (v.get("media") or [{}])[0].get("href"),
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--zip", required=True)
    ap.add_argument("--radius", type=int, default=250)
    ap.add_argument("--model", default=None, help='e.g. "TOYOTA CROWN SIGNIA", "TOYOTA RAV4"')
    ap.add_argument("--color", default=None)
    ap.add_argument("--page-size", type=int, default=50)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    all_rows = []
    page = 1
    total_pages = 1
    while page <= total_pages:
        data = fetch_page(args.zip, args.radius, page, args.page_size, args.model, args.color)
        items = data.get("vehicleSummary") or []
        all_rows.extend(simplify(v) for v in items)
        pager = data.get("pagination") or {}
        total_pages = pager.get("totalPages", 1) or 1
        print(f"  page {page}/{total_pages} -> {len(items)} vehicles")
        page += 1
        if page <= total_pages:
            time.sleep(0.4)

    all_rows.sort(key=lambda r: (r["price"] == 0, r["price"]))
    print(f"Total: {len(all_rows)} vehicles")

    if args.out:
        with open(args.out, "w") as f:
            json.dump(all_rows, f, indent=2)
        print(f"Saved to {args.out}")


if __name__ == "__main__":
    main()
