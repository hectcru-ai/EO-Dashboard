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


def fetch_list(api_key, user_id, collection, fields):
    """
    Pages through every item in a Cheqroom collection ('reservations' or
    'orders'), 100 at a time, using the 'upcoming' list. In practice Cheqroom
    seems to ignore _listname/date filters on this endpoint and just returns
    everything regardless — so we fetch it all and filter client-side later.
    """
    url = f"{CHEQROOM_BASE_URL}/{user_id}/null/jwt/{collection}/search"

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Accept": "application/json",
        "Content-Type": "application/x-www-form-urlencoded",
    }

    page_size = 100
    skip = 0
    all_items = []

    while True:
        data = {
            "_fields": fields,
            "_sort": "created",
            "_listname": "upcoming",
            "_limit": page_size,
            "_skip": skip,
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
        print(f"[{collection}] Fetched {len(page_items)} on this page (total so far: {len(all_items)}).")

        if len(page_items) < page_size:
            break  # last page
        skip += page_size

    return all_items


RESERVATION_FIELDS = "status,number,fromDate,toDate,customer.name,itemSummary,location.name"
ORDER_FIELDS = "status,number,started,due,customer.name,itemSummary,location.name"


def within_window(from_date_str, days_ahead=42):
    """Keep only reservations starting today through `days_ahead` days from now."""
    if not from_date_str:
        return False
    try:
        start = datetime.fromisoformat(from_date_str.replace("Z", "+00:00"))
    except ValueError:
        return False
    now = datetime.now(timezone.utc)
    return now - timedelta(days=1) <= start <= now + timedelta(days=days_ahead)


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
    Maps Cheqroom's fields into the flat shape the board expects. Reservations
    use "fromDate" for their start time; orders (check-outs) use "started" —
    we accept either.
    """
    normalized = []
    for r in raw_items:
        full_name = (r.get("customer") or {}).get("name", "")
        start = r.get("fromDate") or r.get("started") or ""
        normalized.append(
            {
                "cheqroomId": r.get("_id") or r.get("id"),
                "name": short_name(full_name),
                "date": start[:10],
                "time": format_time(start[11:16]) if len(start) >= 16 else "",
                "items": r.get("itemSummary", ""),
                "location": (r.get("location") or {}).get("name", ""),
                "cheqroomStatus": r.get("status", "unknown"),
                "source": r.get("_source", "unknown"),
            }
        )
    return normalized


def main():
    api_key, user_id = get_credentials()

    reservations = fetch_list(api_key, user_id, "reservations", RESERVATION_FIELDS)
    for r in reservations:
        r["_source"] = "reservation"
    print(f"Total reservations fetched: {len(reservations)}")

    orders = fetch_list(api_key, user_id, "orders", ORDER_FIELDS)
    for o in orders:
        o["_source"] = "order"
    print(f"Total orders (check-outs) fetched: {len(orders)}")

    raw = reservations + orders

    def start_of(item):
        return item.get("fromDate") or item.get("started") or ""

    windowed = [r for r in raw if within_window(start_of(r))]
    print(f"Combined items within the 6-week window: {len(windowed)}")
    for r in windowed:
        cust = (r.get("customer") or {}).get("name", "?")
        print(f"  - [{r['_source']}] {cust} | start={start_of(r)} | status={r.get('status')}")

    # Only statuses that actually matter for day-to-day workflow.
    ACTIVE_STATUSES = {"open"}
    active = [r for r in windowed if r.get("status") in ACTIVE_STATUSES]
    print(f"Active-status items (open only): {len(active)}")

    normalized = normalize(active)

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
