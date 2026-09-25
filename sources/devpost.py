"""
Devpost adapter.

Uses Devpost's public (unofficial) JSON endpoint that backs the
https://devpost.com/hackathons listing page:

    https://devpost.com/api/hackathons?page=1&status[]=open

This is not an official/documented API, so the response shape can change
without notice. If parsing starts failing, open the listing page in a
browser with DevTools -> Network -> filter "hackathons" to re-check the
current JSON shape.
"""

from __future__ import annotations

import re
import time
from datetime import datetime
from typing import Iterator, Optional

import httpx

from models import Hackathon

BASE_URL = "https://devpost.com/api/hackathons"
HEADERS = {
    # A normal browser UA avoids some basic bot-blocking.
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36"
    ),
    "Accept": "application/json",
}


_HTML_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(raw: Optional[str]) -> str:
    if not raw:
        return ""
    return _HTML_TAG_RE.sub("", raw).strip()


def _parse_prize(raw: Optional[str]) -> Optional[float]:
    """Best-effort: pull the first number out of a prize string like
    '$<span data-currency-value>740,000</span>' -> 740000.0"""
    clean = _strip_html(raw)
    if not clean:
        return None
    match = re.search(r"[\d,]+(?:\.\d+)?", clean)
    if not match:
        return None
    try:
        return float(match.group().replace(",", ""))
    except ValueError:
        return None


def _parse_deadline(submission_period_dates: Optional[str]) -> Optional[datetime]:
    """Devpost gives a human range like 'Jul 31 - Oct 01, 2026' or
    'Dec 15, 2026 - Jan 15, 2027'. The deadline is the END of that range,
    which is the part after ' - ' and always carries the year."""
    if not submission_period_dates:
        return None
    parts = submission_period_dates.split(" - ")
    if len(parts) != 2:
        return None
    end_str = parts[1].strip()
    try:
        return datetime.strptime(end_str, "%b %d, %Y")
    except ValueError:
        return None


def _parse_mode(location_obj: dict) -> Optional[str]:
    location = (location_obj or {}).get("location")
    if not location:
        return None
    return "online" if "online" in location.lower() else "offline"


def _normalize(entry: dict) -> Hackathon:
    themes = [t.get("name", "") for t in entry.get("themes", []) if t.get("name")]
    location_obj = entry.get("displayed_location") or {}
    prize_raw = entry.get("prize_amount")

    return Hackathon(
        source="devpost",
        source_id=str(entry.get("id")),
        title=entry.get("title", "").strip(),
        url=entry.get("url", ""),
        thumbnail_url=entry.get("thumbnail_url"),
        mode=_parse_mode(location_obj),
        location=location_obj.get("location"),
        deadline=_parse_deadline(entry.get("submission_period_dates")),
        prize_amount=_parse_prize(prize_raw),
        prize_text=_strip_html(prize_raw) or None,
        themes=themes,
        participants=entry.get("registrations_count"),
    )


def fetch_hackathons(
    max_pages: int = 5,
    status: str = "open",         # "open" | "upcoming" | "ended"
    delay_seconds: float = 1.0,
    client: Optional[httpx.Client] = None,
) -> Iterator[Hackathon]:
    """
    Yields normalized Hackathon records from Devpost, paging until an
    empty page is returned or max_pages is hit.
    """
    owns_client = client is None
    client = client or httpx.Client(headers=HEADERS, timeout=15.0)

    try:
        for page in range(1, max_pages + 1):
            params = {"page": page, "status[]": status}
            resp = client.get(BASE_URL, params=params)
            resp.raise_for_status()
            data = resp.json()

            entries = data.get("hackathons", [])
            if not entries:
                break

            for entry in entries:
                try:
                    yield _normalize(entry)
                except Exception as exc:  # keep going even if one entry is malformed
                    print(f"[devpost] skipped a malformed entry: {exc}")

            time.sleep(delay_seconds)  # be polite, avoid hammering the endpoint
    finally:
        if owns_client:
            client.close()


if __name__ == "__main__":
    # Quick manual check: python -m sources.devpost
    for h in fetch_hackathons(max_pages=1):
        print(h.title, "|", h.deadline, "|", h.prize_text, "|", h.themes)