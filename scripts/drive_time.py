#!/usr/bin/env python3
"""
Drive time lookups via the Google Maps Directions API, cached forever per
(origin, destination) pair so each dealership is only ever billed once.

Env var required: GOOGLE_MAPS_API_KEY

Cache file: data/drive_times_cache.json
  { "<origin>|<destination>": {"minutes": 22, "text": "22 mins", "miles": 14.3} }
"""
import json
import os
import urllib.parse
import urllib.request

CACHE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "drive_times_cache.json")


def _load_cache():
    if os.path.exists(CACHE_FILE):
        with open(CACHE_FILE) as f:
            return json.load(f)
    return {}


def _save_cache(cache):
    os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
    with open(CACHE_FILE, "w") as f:
        json.dump(cache, f, indent=2, sort_keys=True)


def get_drive_time(origin, destination, cache=None):
    """Returns {"minutes": int, "text": str, "miles": float} or None on failure.

    `cache` may be passed in to batch multiple lookups without re-reading/
    writing the file on every call; if omitted, the cache is loaded and
    saved fresh for this one lookup.
    """
    owns_cache = cache is None
    if owns_cache:
        cache = _load_cache()

    key = f"{origin}|{destination}"
    if key in cache:
        return cache[key]

    api_key = os.environ.get("GOOGLE_MAPS_API_KEY")
    if not api_key:
        print(f"GOOGLE_MAPS_API_KEY not set — cannot look up drive time for: {destination}")
        return None

    qs = urllib.parse.urlencode({
        "origin": origin,
        "destination": destination,
        "key": api_key,
    })
    url = f"https://maps.googleapis.com/maps/api/directions/json?{qs}"
    try:
        with urllib.request.urlopen(url, timeout=15) as resp:
            data = json.loads(resp.read().decode())
    except Exception as e:
        print(f"Directions API request failed for {destination}: {e}")
        return None

    if data.get("status") != "OK" or not data.get("routes"):
        print(f"Directions API returned {data.get('status')} for {destination}: {data.get('error_message', '')}")
        return None

    leg = data["routes"][0]["legs"][0]
    result = {
        "minutes": round(leg["duration"]["value"] / 60),
        "text": leg["duration"]["text"],
        "miles": round(leg["distance"]["value"] / 1609.34, 1),
    }
    cache[key] = result
    if owns_cache:
        _save_cache(cache)
    return result


def get_drive_times_batch(origin, destinations):
    """destinations: dict of {key: address}. Returns {key: result_or_None}.
    Loads/saves the cache once for the whole batch instead of per lookup."""
    cache = _load_cache()
    out = {}
    hits, misses = 0, 0
    for key, address in destinations.items():
        cache_key = f"{origin}|{address}"
        was_cached = cache_key in cache
        result = get_drive_time(origin, address, cache=cache)
        out[key] = result
        hits += was_cached
        misses += not was_cached
    _save_cache(cache)
    print(f"Drive times: {hits} from cache, {misses} new API calls.")
    return out
