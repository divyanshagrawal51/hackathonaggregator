"""
CI entrypoint: fetch Devpost hackathons, filter by preferences, and write a
single JSON file that the static dashboard (docs/index.html) reads.

No database, no notifications -- this just regenerates the full current
list every time it runs, which is exactly what a static site needs.
"""

import json
import os
from datetime import datetime, timezone

from filters import filter_hackathons, load_preferences
from sources.devpost import fetch_hackathons

OUTPUT_PATH = os.path.join(os.path.dirname(__file__), "docs", "hackathons.json")


def run() -> None:
    prefs = load_preferences()

    fetched = list(fetch_hackathons(max_pages=5, status="open"))
    matched = filter_hackathons(fetched, prefs)

    # Soonest deadlines first; ones with no listed deadline go last.
    matched.sort(key=lambda h: (h.deadline is None, h.deadline))

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