"""
Pulls the current week's orders from Cheqroom and writes them to docs/orders.json
so the EO order board (a Claude artifact) can fetch it as a plain JSON file via
GitHub Pages.

Read-only: this script only ever performs GET requests. It never creates, edits,
or deletes anything in Cheqroom.

--------------------------------------------------------------------------------
ONE THING THIS SCRIPT NEEDS BEFORE IT WILL WORK: the exact request shape Cheqroom
expects for API-key auth.

I could not confirm this from public docs, so CHEQROOM_AUTH_HEADER and
CHEQROOM_RESERVATIONS_PATH below are my best guess, not a confirmed spec. The
first time you run this (see the "Test this first" note in the workflow file),
check the Action's log output:
  - If you get a 401/403, the header name or "Bearer" prefix is wrong.
  - If you get a 404, the path below is wrong.
  - Either way, paste the log output back and I'll fix these two lines.
--------------------------------------------------------------------------------
"""

import json
import os
import sys
from datetime import datetime, timedelta, timezone

import requests

CHEQROOM_BASE_URL = "https://api.cheqroom.com/api/v3"          # best guess — confirm
CHEQROOM_RESERVATIONS_PATH = "/reservations"                    # best guess — confirm
CHEQROOM_AUTH_HEADER = "Authorization"                          # best guess — confirm
CHEQROOM_AUTH_PREFIX = "Bearer "                                 # best guess — confirm

OUTPUT_PATH = os.path.join(os.path.dirname(__file__), "docs", "orders.json")


def get_api_key():
    key = os.environ.get("CHEQROOM_API_KEY")
    if not key:
        print("ERROR: CHEQROOM_API_KEY environment variable is not set.", file=sys.stderr)
        sys.exit(1)
    return key


def fetch_reservations(api_key):
    """
    Fetches reservations covering today through 6 days from now (this week's window).
    Returns the raw list from Cheqroom, or exits with a descriptive error so the
    GitHub Actions log makes the failure obvious.
    """
    headers = {
        CHEQROOM_AUTH_HEADER: f"{CHEQROOM_AUTH_PREFIX}{api_key}",
        "Accept": "application/json",
    }

    today = datetime.now(timezone.utc).date()
    week_end = today + timedelta(days=6)

    params = {
        "from": today.isoformat(),
        "to": week_end.isoformat(),
    }

    url = CHEQROOM_BASE_URL + CHEQROOM_RESERVATIONS_PATH
    print(f"Requesting: GET {url} params={params}")

    resp = requests.get(url, headers=headers, params=params, timeout=30)

    print(f"Response status: {resp.status_code}")
    if resp.status_code != 200:
        print("Response body (first 2000 chars):", file=sys.stderr)
        print(resp.text[:2000], file=sys.stderr)
        print(
            "\nThis means the guessed endpoint/auth in sync_cheqroom.py is wrong. "
            "Paste this log back to fix CHEQROOM_BASE_URL / CHEQROOM_RESERVATIONS_PATH / "
            "CHEQROOM_AUTH_HEADER / CHEQROOM_AUTH_PREFIX at the top of the script.",
            file=sys.stderr,
        )
        sys.exit(1)

    data = resp.json()
    # Cheqroom's list endpoints are commonly wrapped, e.g. {"results": [...]}.
    # Handle both a bare list and a wrapped one so we don't crash either way.
    if isinstance(data, dict):
        items = data.get("results") or data.get("items") or data.get("data") or []
    else:
        items = data

    print(f"Fetched {len(items)} reservation(s).")
    return items


def short_name(full_name):
    """
    "John Smith" -> "John S."   |   "John" -> "John"   |  "" -> "Unknown"
    This file gets published on a public URL, so we deliberately don't write
    full names — first name + last initial is enough for staff to match an
    order at the counter without exposing a full name to the open internet.
    """
    if not full_name:
        return "Unknown"
    parts = full_name.strip().split()
    if len(parts) == 1:
        return parts[0]
    return f"{parts[0]} {parts[-1][0]}."


def normalize(raw_items):
    """
    Maps Cheqroom's fields into the flat shape the board expects. Field names
    below (name/startDate/status/etc.) are also guesses pending a real sample
    response — likely need small adjustments once we see actual data.
    """
    normalized = []
    for r in raw_items:
        full_name = r.get("user", {}).get("name") or r.get("customerName") or r.get("name") or ""
        normalized.append(
            {
                "cheqroomId": r.get("_id") or r.get("id"),
                "name": short_name(full_name),
                "date": (r.get("startDate") or r.get("start_date") or "")[:10],
                "time": r.get("startDate", "")[11:16] if r.get("startDate") else "",
                "items": ", ".join(
                    i.get("name", "") for i in r.get("items", []) if isinstance(i, dict)
                ) or r.get("itemSummary", ""),
                "cheqroomStatus": r.get("status", "unknown"),
            }
        )
    return normalized


def main():
    api_key = get_api_key()
    raw = fetch_reservations(api_key)
    normalized = normalize(raw)

    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    payload = {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "orders": normalized,
    }
    with open(OUTPUT_PATH, "w") as f:
        json.dump(payload, f, indent=2)

    print(f"Wrote {len(normalized)} order(s) to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
