"""
Pulls this week's reservations from Cheqroom and writes them to docs/orders.json
so the EO order board (a Claude artifact) can fetch it as a plain JSON file via
GitHub Pages.

Read-only: this script only ever performs search/read requests. It never
creates, edits, or deletes anything in Cheqroom, even though Cheqroom's search
endpoints are technically POST requests (that's just how their API is built).

Based on Cheqroom's own documented API (v2_5, JWT-style API key auth).
"""

import json
import os
import sys
from datetime import datetime, timedelta, timezone

import requests

CHEQROOM_BASE_URL = "https://app.cheqroom.com/api/v2_5"

# Fields to pull back for each reservation. See Cheqroom's docs for the full
# list of what's available — trimmed here to just what the board displays.
RESERVATION_FIELDS = "status,number,fromDate,toDate,customer.name,itemSummary,location.name"

OUTPUT_PATH = os.path.join(os.path.dirname(__file__), "docs", "orders.json")


def get_credentials():
    api_key = os.environ.get("CHEQROOM_API_KEY")
    user_id = os.environ.get("CHEQROOM_USER_ID")
    missing = [
        name
        for name, val in [("CHEQROOM_API_KEY", api_key), ("CHEQROOM_USER_ID", user_id)]
        if not val
    ]
    if missing:
        print(f"ERROR: missing environment variable(s): {', '.join(missing)}", file=sys.stderr)
        sys.exit(1)
    return api_key, user_id


def fetch_reservations(api_key, user_id):
    """
    Searches reservations whose start date falls within today through 41 days
    from now (a 6-week window). Pages through results 100 at a time so we
    never silently drop reservations during a busy stretch. Exits with a
    descriptive error so the GitHub Actions log makes any failure obvious.
    """
    url = f"{CHEQROOM_BASE_URL}/{user_id}/null/jwt/reservations/search"

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Accept": "application/json",
        "Content-Type": "application/x-www-form-urlencoded",
    }

    today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    week_end = today + timedelta(days=42)  # 6 weeks ahead

    page_size = 100
    skip = 0
    all_items = []

    while True:
        data = {
            "_fields": RESERVATION_FIELDS,
            "_sort": "fromDate",
            "_listname": "all",
            "_limit": page_size,
            "_skip": skip,
            "fromDate__gte": today.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "fromDate__lte": week_end.strftime("%Y-%m-%dT%H:%M:%SZ"),
        }

        print(f"Requesting: POST {url} (skip={skip})")

        resp = requests.post(url, headers=headers, data=data, timeout=30)

        print(f"Response status: {resp.status_code}")
        if resp.status_code != 200:
            print("Response body (first 2000 chars):", file=sys.stderr)
            print(resp.text[:2000], file=sys.stderr)
            print(
                "\nCheck: is CHEQROOM_USER_ID correct? Is the API key still valid? "
                "Paste this log back if you're not sure what to fix next.",
                file=sys.stderr,
            )
            sys.exit(1)

        payload = resp.json()
        if skip == 0:
            print(f"Top-level response keys: {list(payload.keys()) if isinstance(payload, dict) else 'response is a bare list'}")

        if isinstance(payload, dict):
            page_items = (
                payload.get("results")
                or payload.get("docs")
                or payload.get("items")
                or payload.get("data")
                or []
            )
        else:
            page_items = payload

        all_items.extend(page_items)
        print(f"Fetched {len(page_items)} on this page (total so far: {len(all_items)}).")

        if len(page_items) < page_size:
            break  # last page
        skip += page_size

    return all_items


def format_time(hh_mm):
    """
    "14:00" -> "2:00 PM"   |   "09:05" -> "9:05 AM"   |   "" -> ""
    Cheqroom returns 24-hour time; staff read 12-hour, so convert here once
    rather than relying on the display page to do it.
    """
    if not hh_mm or ":" not in hh_mm:
        return hh_mm
    try:
        dt = datetime.strptime(hh_mm, "%H:%M")
        return dt.strftime("%I:%M %p").lstrip("0")
    except ValueError:
        return hh_mm


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
    Maps Cheqroom's reservation fields into the flat shape the board expects.
    """
    normalized = []
    for r in raw_items:
        full_name = (r.get("customer") or {}).get("name", "")
        from_date = r.get("fromDate", "") or ""
        normalized.append(
            {
                "cheqroomId": r.get("_id") or r.get("id"),
                "name": short_name(full_name),
                "date": from_date[:10],
                "time": format_time(from_date[11:16]) if len(from_date) >= 16 else "",
                "items": r.get("itemSummary", ""),
                "location": (r.get("location") or {}).get("name", ""),
                "cheqroomStatus": r.get("status", "unknown"),
            }
        )
    return normalized


def main():
    api_key, user_id = get_credentials()
    raw = fetch_reservations(api_key, user_id)
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
