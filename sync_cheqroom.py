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


RESERVATION_FIELDS = "name,status,number,fromDate,toDate,customer.name,itemSummary,location.name,fields"
ORDER_FIELDS = "name,status,number,started,due,customer.name,itemSummary,location.name,fields"

# The exact custom field name as entered in Cheqroom — must match precisely,
# including capitalization, since custom field values come back keyed by name.
GRADE_LEVEL_FIELD = "Grade level"


def within_window(date_str, days_ahead=56):
    """Keep only items whose relevant date falls today through `days_ahead` days from now (8 weeks)."""
    if not date_str:
        return False
    try:
        start = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
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


def get_field_case_insensitive(fields_dict, field_name):
    """
    Custom field values come back keyed by their exact display name in
    Cheqroom, and capitalization is easy to get slightly wrong when typing
    it into a script by hand. Match case-insensitively so a mismatch like
    "Grade level" vs "Grade Level" doesn't silently return nothing.
    """
    if not isinstance(fields_dict, dict):
        return ""
    target = field_name.strip().lower()
    for key, value in fields_dict.items():
        if key.strip().lower() == target:
            return value
    return ""


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
    Maps Cheqroom's fields into the flat shape the board expects. Each item
    already has _date and _action set during expansion in main() — a single
    reservation becomes two entries (a checkout card at fromDate and a
    check-in card at toDate) so a same-day pickup-and-return shows both ends,
    not just the pickup.
    """
    normalized = []
    for r in raw_items:
        full_name = (r.get("customer") or {}).get("name", "")
        date_str = r.get("_date", "")
        grade_level = get_field_case_insensitive(r.get("fields") or {}, GRADE_LEVEL_FIELD)
        normalized.append(
            {
                "cheqroomId": r.get("_id") or r.get("id"),
                "name": short_name(full_name),
                "orderName": r.get("name", ""),
                "gradeLevel": grade_level,
                "date": date_str[:10],
                "time": format_time(date_str[11:16]) if len(date_str) >= 16 else "",
                "items": r.get("itemSummary", ""),
                "location": (r.get("location") or {}).get("name", ""),
                "cheqroomStatus": r.get("status", "unknown"),
                "source": r.get("_source", "unknown"),
                "action": r.get("_action", "checkout"),
            }
        )
    return normalized


def main():
    api_key, user_id = get_credentials()

    reservations = fetch_list(api_key, user_id, "reservations", RESERVATION_FIELDS)
    print(f"Total reservations fetched: {len(reservations)}")

    orders = fetch_list(api_key, user_id, "orders", ORDER_FIELDS)
    print(f"Total orders (check-outs) fetched: {len(orders)}")

    # A reservation carries both a pickup time (fromDate) and an expected
    # return time (toDate) — expand it into two cards so both show up, even
    # when pickup and return happen the same day. Orders (already-checked-out
    # items) only need a check-in card, using their due date.
    expanded = []
    for r in reservations:
        expanded.append({**r, "_source": "reservation", "_action": "checkout", "_date": r.get("fromDate") or ""})
        if r.get("toDate"):
            expanded.append({**r, "_source": "reservation", "_action": "checkin", "_date": r.get("toDate")})
    for o in orders:
        expanded.append({**o, "_source": "order", "_action": "checkin", "_date": o.get("due") or o.get("started") or ""})

    windowed = [r for r in expanded if within_window(r["_date"])]
    print(f"Combined items within the 8-week window: {len(windowed)}")
    for r in windowed:
        cust = (r.get("customer") or {}).get("name", "?")
        print(f"  - [{r['_source']}/{r['_action']}] {cust} | name={r.get('name')!r} | date={r['_date']} | status={r.get('status')} | fields={r.get('fields')}")

    # Normally only "open" (finalized) items matter for day-to-day workflow.
    # Exception: a "creating" (still-draft) item happening today or tomorrow
    # is worth showing anyway, since staff may need to start prepping before
    # it's fully locked in — but a draft further out is much more likely to
    # still change, so those stay hidden until finalized.
    today = datetime.now(timezone.utc).date()
    tomorrow = today + timedelta(days=1)
    near_term_dates = {today.isoformat(), tomorrow.isoformat()}

    def is_active(r):
        status = r.get("status")
        if status == "open":
            return True
        if status == "creating" and r["_date"][:10] in near_term_dates:
            return True
        return False

    active = [r for r in windowed if is_active(r)]
    print(f"Active items (open, plus near-term drafts): {len(active)}")

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
