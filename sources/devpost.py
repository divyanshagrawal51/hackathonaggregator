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
    max_pages: int = 100,
    status: str = "open",         # "open" | "upcoming" | "ended"
    delay_seconds: float = 1.0,
    client: Optional[httpx.Client] = None,
    max_empty_retries: int = 3,
) -> Iterator[Hackathon]:
    """
    Yields normalized Hackathon records from Devpost, paging until an
    empty page is returned or max_pages is hit.

    Devpost's response includes a "meta": {"total_count": N} field. We use
    that as the ground truth for how many results should exist. An empty
    page is only treated as "no more results" once we've actually
    collected total_count items -- if it comes back empty earlier than
    that (Devpost intermittently does this, likely rate-limiting rapid
    repeated requests rather than genuinely running out of data), we
    retry that same page a few times with backoff before giving up and
    logging a warning, instead of silently under-reporting.
    """
    owns_client = client is None
    client = client or httpx.Client(headers=HEADERS, timeout=15.0)

    collected = 0
    expected_total = None
    empty_retries = 0

    try:
        page = 1
        while page <= max_pages:
            params = {"page": page, "status[]": status}
            resp = client.get(BASE_URL, params=params)
            resp.raise_for_status()

            try:
                data = resp.json()
            except ValueError:
                # Devpost occasionally returns a 200/202 with an empty or
                # non-JSON body under load. Treat this exactly like an
                # empty page below -- retry with backoff rather than
                # letting the whole export crash on one bad response.
                data = {}

            meta = data.get("meta") or {}
            if expected_total is None and "total_count" in meta:
                expected_total = meta["total_count"]

            entries = data.get("hackathons", [])

            if not entries:
                if (
                    expected_total is not None
                    and collected < expected_total
                    and empty_retries < max_empty_retries
                ):
                    empty_retries += 1
                    print(
                        f"[devpost] page {page} ({status}) came back empty but only "
                        f"{collected}/{expected_total} collected -- retrying "
                        f"(attempt {empty_retries}/{max_empty_retries})"
                    )
                    time.sleep(delay_seconds * 2)
                    continue  # retry the same page, don't advance
                if expected_total is not None and collected < expected_total:
                    print(
                        f"[devpost] gave up after {max_empty_retries} retries on page "
                        f"{page} ({status}) -- only got {collected}/{expected_total}"
                    )
                break

            empty_retries = 0
            for entry in entries:
                try:
                    yield _normalize(entry)
                    collected += 1
                except Exception as exc:  # keep going even if one entry is malformed
                    print(f"[devpost] skipped a malformed entry: {exc}")

            page += 1
            time.sleep(delay_seconds)  # be polite, avoid hammering the endpoint
    finally:
        if owns_client:
            client.close()


if __name__ == "__main__":
    # Quick manual check: python -m sources.devpost
    for h in fetch_hackathons(max_pages=1):
        print(h.title, "|", h.deadline, "|", h.prize_text, "|", h.themes)
