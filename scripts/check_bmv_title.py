#!/usr/bin/env python3
"""
Watch the Ohio BMV online title search for a VIN and send a Pushover alert the
moment a title record appears — i.e. when the search stops returning
"Record(s) not found". Useful after buying a car, to catch the title being
issued by the county clerk as soon as it lands.

No login required. Each run:
  1. GET  /bmvonline/titles/titlesearch            -> antiforgery cookie + a
     __RequestVerificationToken embedded in the form.
  2. POST /bmvonline/titles/titlesearch/vinsearchbysearchmodel with the VIN,
     that token, and the cookie jar from step 1.
  3. Classify the response: not_found | found | error.

State (data/bmv_title_state.json) persists the last status so we alert only
once on the not-found -> found flip, and so transient errors don't cry wolf.
The file only changes when something meaningful changes, keeping commits quiet.

The VIN is read from the BMV_VIN env var (a GitHub secret) so it never lands in
this public repo — not in the code, and not in the committed state file.

Env (GitHub Actions secrets): PUSHOVER_USER_KEY, PUSHOVER_APP_TOKEN, BMV_VIN
Usage: BMV_VIN=<vin> python3 check_bmv_title.py   (or pass --vin)
"""
import argparse
import http.cookiejar
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

BASE = "https://bmvonline.dps.ohio.gov"
FORM_PATH = "/bmvonline/titles/titlesearch"
ACTION_PATH = "/bmvonline/titles/titlesearch/vinsearchbysearchmodel"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36")
NOT_FOUND_MARKER = "Record(s) not found"
# Unique to a real title result: the "Title Inquiry" link. The empty search
# form never contains it, so it's a clean positive signal for "titled".
FOUND_MARKER = "gettitlebytitlenumber"
STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "bmv_title_state.json")
ERROR_ALERT_AT = 6   # warn once after this many consecutive failures (~18h at a 3h cadence)


def send_pushover(title, message, url=None, url_title=None):
    user_key = os.environ.get("PUSHOVER_USER_KEY")
    app_token = os.environ.get("PUSHOVER_APP_TOKEN")
    if not user_key or not app_token:
        print("PUSHOVER_USER_KEY / PUSHOVER_APP_TOKEN not set — printing instead:")
        print(title)
        print(message)
        return
    data = {"token": app_token, "user": user_key, "title": title, "message": message[:1024]}
    if url:
        data["url"] = url
        data["url_title"] = url_title or "Open"
    req = urllib.request.Request("https://api.pushover.net/1/messages.json",
                                 data=urllib.parse.urlencode(data).encode(), method="POST")
    with urllib.request.urlopen(req, timeout=15) as resp:
        print(f"Pushover response: {resp.status} {resp.read().decode()}")


def load_state(path):
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return {}


def save_state(path, state):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(state, f, indent=2)


def check_vin(vin):
    """Return (status, body) with status in {'not_found','found','error'}."""
    cj = http.cookiejar.CookieJar()
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    hdrs = {"User-Agent": UA,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9"}

    # 1) GET the form page for a fresh antiforgery cookie + token
    try:
        html = op.open(urllib.request.Request(BASE + FORM_PATH, headers=hdrs), timeout=30).read().decode("utf-8", "replace")
    except Exception as e:
        return "error", f"GET failed: {e}"

    # token inside the VIN form specifically, else any token on the page
    m = re.search(r'action="[^"]*vinsearchbysearchmodel".*?name="__RequestVerificationToken"[^>]*value="([^"]+)"', html, re.S) \
        or re.search(r'name="__RequestVerificationToken"[^>]*value="([^"]+)"', html)
    if not m:
        return "error", "no antiforgery token found on form page"
    token = m.group(1)

    # 2) POST the VIN
    data = urllib.parse.urlencode({
        "searchModel.SearchParameter.VinHinMinParameter.VinHinMin": vin,
        "__RequestVerificationToken": token,
    }).encode()
    phdrs = dict(hdrs)
    phdrs.update({"Content-Type": "application/x-www-form-urlencoded", "Origin": BASE, "Referer": BASE + FORM_PATH})
    try:
        r = op.open(urllib.request.Request(BASE + ACTION_PATH, data=data, headers=phdrs), timeout=30)
        body, code, final = r.read().decode("utf-8", "replace"), r.getcode(), r.geturl()
    except urllib.error.HTTPError as e:
        return "error", f"POST HTTP {e.code} {e.reason}"
    except Exception as e:
        return "error", f"POST failed: {e}"

    # 3) Classify
    if "/auth/login" in final:
        return "error", "redirected to login"
    if NOT_FOUND_MARKER in body:
        return "not_found", body
    if code == 200 and FOUND_MARKER in body:
        return "found", body
    return "error", f"unexpected response (HTTP {code}, {len(body)} bytes)"


def parse_result(body):
    """Parse the BMV result page into (vehicle_dict, [title_dicts]). Fields are
    rendered as `<b>Label</b><br/><label|span ...>Value</label>`."""
    pairs = re.findall(r"<b>([^<]+)</b>\s*<br\s*/?>\s*<(?:label|span)[^>]*>([^<]*)</", body)
    vehicle, titles, cur = {}, [], None
    for label, val in pairs:
        label, val = label.strip(), val.strip()
        if label in ("Year", "Make", "Model", "VIN", "Mileage") and label not in vehicle:
            vehicle[label] = val
        elif label == "Title Number":
            cur = {"number": val}
            titles.append(cur)
        elif label == "Issue Date" and cur is not None:
            cur["issued"] = val
        elif label == "Title Status" and cur is not None:
            cur["status"] = val
    return vehicle, titles


def found_message(body):
    vehicle, titles = parse_result(body)
    desc = " ".join(vehicle.get(k, "") for k in ("Year", "Make", "Model")).strip()
    active = next((t for t in titles if t.get("status", "").upper() == "ACTIVE"), titles[0] if titles else None)
    lines = [f"Ohio title record now exists for {vehicle.get('VIN', '')}."]
    if active:
        lines.append(f"Title #{active['number']} — issued {active.get('issued', '?')} ({active.get('status', '?')})")
    if desc:
        lines.append(desc)
    if len(titles) > 1:
        lines.append(f"({len(titles)} title records on file)")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--vin", default=os.environ.get("BMV_VIN"),
                    help="VIN to watch (default: BMV_VIN env var — kept out of the repo)")
    ap.add_argument("--state", default=STATE_FILE)
    args = ap.parse_args()
    if not args.vin:
        raise SystemExit("No VIN provided. Set the BMV_VIN env var (GitHub secret) or pass --vin.")
    vin_masked = "…" + args.vin[-4:]

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    state = load_state(args.state)
    prev = dict(state)  # to detect meaningful change
    status, body = check_vin(args.vin)
    print(f"{now}  VIN {vin_masked}: {status} (was {state.get('status')})")

    state["status"] = status

    if status == "error":
        state["consecutiveErrors"] = state.get("consecutiveErrors", 0) + 1
        print(f"  error: {body} (streak {state['consecutiveErrors']})")
        if state["consecutiveErrors"] == ERROR_ALERT_AT:
            try:
                send_pushover("BMV title monitor is failing",
                              f"The title check for {vin_masked} has failed {state['consecutiveErrors']} times "
                              f"in a row ({body}). Still retrying.", url=BASE + FORM_PATH, url_title="Open search")
            except Exception as e:
                print(f"  (failed to send error alert: {e})")
    else:
        state["consecutiveErrors"] = 0
        if status == "found" and not state.get("alerted"):
            try:
                send_pushover("🎉 Your title is in!", found_message(body),
                              url=BASE + FORM_PATH, url_title="Open BMV title search")
                state["alerted"] = True
                state["firstFoundDate"] = now
                print("  ALERTED: title found.")
            except Exception as e:
                print(f"  title found but alert failed ({e}); will retry next run.")
        elif status == "found":
            print("  already alerted — staying quiet.")

    # Persist only on a meaningful change (keeps the workflow's commits quiet).
    meaningful = {k: state.get(k) for k in ("status", "alerted", "firstFoundDate", "consecutiveErrors")}
    if meaningful != {k: prev.get(k) for k in meaningful}:
        save_state(args.state, state)
        print("  state updated.")
    else:
        print("  no state change.")


if __name__ == "__main__":
    main()
