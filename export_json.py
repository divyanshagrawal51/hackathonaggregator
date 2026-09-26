"""
CI entrypoint: fetch Devpost hackathons, filter by preferences, and write a
single JSON file that the static dashboard (docs/index.html) reads.

No database, no notifications -- this just regenerates the full current
list every time it runs, which is exactly what a static site needs.
"""

import json
import os
from datetime import datetime, timezone
from typing import Optional

from filters import filter_hackathons, load_preferences
from models import Hackathon
from sources.devpost import fetch_hackathons

OUTPUT_PATH = os.path.join(os.path.dirname(__file__), "docs", "hackathons.json")


def _prize_currency(prize_text: Optional[str]) -> str:
    """Best-effort currency detection from the raw prize text Devpost gives us
    (e.g. '$740,000', '₹50,000'). Falls back to "other" when it can't tell."""
    if not prize_text:
        return "other"
    upper = prize_text.upper()
    if "₹" in prize_text or "RS." in upper or "RS " in upper or "INR" in upper:
        return "inr"
    if "$" in prize_text or "USD" in upper:
        return "usd"
    return "other"


def _sort_key(h: Hackathon):
    """
    Three-tier sort:
      1. Rupee-prized hackathons, ascending by prize amount
      2. Dollar-prized hackathons, ascending by prize amount
      3. Everything else (no usable prize info), by days-left ascending,
         with no-deadline hackathons pushed to the very end.
    """
    currency = _prize_currency(h.prize_text) if h.prize_amount is not None else "other"

    if currency == "inr":
        return (0, h.prize_amount, False, datetime.max)
    if currency == "usd":
        return (1, h.prize_amount, False, datetime.max)

    return (2, 0.0, h.deadline is None, h.deadline or datetime.max)


def run() -> None:
    prefs = load_preferences()

    # Fetch both "open" (accepting submissions now) and "upcoming" (not
    # open yet) separately -- Devpost's endpoint only returns one status
    # per request -- then merge. Dedup by (source, source_id) in case a
    # hackathon somehow appears in both pages.
    fetched = list(fetch_hackathons(status="open")) + list(
        fetch_hackathons(status="upcoming")
    )
    seen = set()
    deduped = []
    for h in fetched:
        key = (h.source, h.source_id)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(h)

    matched = filter_hackathons(deduped, prefs)

    # Tier 1: rupee prizes ascending. Tier 2: dollar prizes ascending.
    # Tier 3: everything else, by days-left ascending, no-deadline last.
    matched.sort(key=_sort_key)

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "count": len(matched),
        "hackathons": [
            {
                "source": h.source,
                "title": h.title,
                "url": h.url,
                "thumbnail_url": h.thumbnail_url,
                "mode": h.mode,
                "location": h.location,
                "deadline": h.deadline.isoformat() if h.deadline else None,
                "prize_text": h.prize_text,
                "themes": h.themes,
                "participants": h.participants,
            }
            for h in matched
        ],
    }

    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, "w") as f:
        json.dump(payload, f, indent=2)

    print(f"[export] wrote {len(matched)} hackathons to {OUTPUT_PATH}")


if __name__ == "__main__":
    run()
