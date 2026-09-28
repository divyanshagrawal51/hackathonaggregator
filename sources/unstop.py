"""
Unstop adapter.

Uses Unstop's public (unofficial) JSON endpoint that backs the
https://unstop.com/hackathons listing page:

    https://unstop.com/api/public/opportunity/search-result

This is not an official/documented API, so the response shape can change
without notice, and -- importantly -- it's been widely reported (see
third-party Unstop scrapers on Apify) that Unstop's own query-param
filters are unreliable: an invalid or unsupported filter value is
silently ignored and the endpoint just returns everything (hackathons,
workshops, quizzes, competitions, jobs, scholarships, all mixed
together) rather than erroring.

Because of that, this adapter does NOT trust the "opportunity=hackathons"
query param alone to do the filtering. It's included in the request as a
best-effort hint, but the real filtering happens client-side in
_is_hackathon() below, which only keeps entries whose own "type" field
says "hackathons" and explicitly rejects anything that looks like a
workshop or webinar (by type, subtype, or URL slug). If Unstop changes
its response shape, check a live sample (run this file directly) and
adjust _is_hackathon() rather than the URL params.
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Iterator, Optional

import httpx

from models import Hackathon

BASE_URL = "https://unstop.com/api/public/opportunity/search-result"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36"
    ),
    "Accept": "application/json",
}

_CURRENCY_SYMBOLS = {
    "fa-rupee": "₹",
    "fa-dollar": "$",
    "fa-euro": "€",
    "fa-pound": "£",
}


def _currency_symbol(code: Optional[str]) -> str:
    return _CURRENCY_SYMBOLS.get(code or "", "")


def _parse_datetime(iso: Optional[str]) -> Optional[datetime]:
    if not iso:
        return None
    try:
        return datetime.fromisoformat(iso)
    except ValueError:
        return None


def _parse_prize(prizes: list) -> tuple[Optional[float], Optional[str]]:
    """Picks the highest cash prize out of Unstop's prizes list and
    formats it the same way devpost.py's prize_text is used downstream
    (a short human string plus a numeric amount for sorting)."""
    if not prizes:
        return None, None
    top = max(prizes, key=lambda p: p.get("cash") or 0)
    cash = top.get("cash")
    if not cash:
        return None, None
    symbol = _currency_symbol(top.get("currency"))
    text = f"{symbol}{cash:,.0f}" if symbol else f"{cash:,.0f}"
    return float(cash), text


def _parse_location(entry: dict) -> Optional[str]:
    addr = entry.get("address_with_country_logo") or {}
    city = addr.get("city") or None
    state = addr.get("state") or None
    parts = [p for p in (city, state) if p]
    return ", ".join(parts) if parts else None


def _first_text(entry: dict, *names: str) -> Optional[str]:
    """Read a named organizer/college value without falling back to location."""
    for name in names:
        value = entry.get(name)
        if isinstance(value, dict):
            value = value.get("name") or value.get("title") or value.get("label")
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def _is_hackathon(entry: dict) -> bool:
    """Strict allowlist: only entries Unstop itself tags as a hackathon.
    Explicitly rejects anything workshop/webinar-flavored even if it
    somehow carries a hackathon-ish type, per the "strictly exclude
    workshops" requirement -- belt and suspenders against Unstop's known
    filter unreliability."""
    type_ = (entry.get("type") or "").lower()
    subtype = (entry.get("subtype") or "").lower()
    public_url = (entry.get("public_url") or "").lower()

    if "workshop" in type_ or "workshop" in subtype or "workshop" in public_url:
        return False
    if "webinar" in type_ or "webinar" in public_url:
        return False

    return type_ == "hackathons" or public_url.startswith("hackathons/")


def _normalize(entry: dict) -> Hackathon:
    themes = [
        wf.get("name", "")
        for wf in entry.get("workfunction", [])
        if wf.get("name")
    ]
    prize_amount, prize_text = _parse_prize(entry.get("prizes") or [])
    reg = entry.get("regnRequirements") or {}
    deadline = _parse_datetime(reg.get("end_regn_dt"))

    public_url = entry.get("public_url")
    url = entry.get("seo_url") or (f"https://unstop.com/{public_url}" if public_url else "")

    return Hackathon(
        source="unstop",
        source_id=str(entry.get("id")),
        title=(entry.get("title") or "").strip(),
        url=url,
        thumbnail_url=entry.get("logoUrl2") or entry.get("thumb"),
        mode=(entry.get("region") or "").lower() or None,
        location=_parse_location(entry),
        college=_first_text(
            entry,
            "college",
            "college_name",
            "collegeName",
            "school",
            "school_name",
            "institute",
            "institute_name",
        ),
        organizer=_first_text(
            entry,
            "organizer",
            "organizer_name",
            "organizerName",
            "organiser",
            "organiser_name",
            "organization",
            "organization_name",
            "organisation",
            "organisation_name",
            "org_name",
            "orgName",
            "host",
            "host_name",
            "hostName",
        ),
        deadline=deadline,
        prize_amount=prize_amount,
        prize_text=prize_text,
        themes=themes,
        participants=entry.get("registerCount"),
    )


def fetch_hackathons(
    max_pages: int = 100,
    per_page: int = 50,
    delay_seconds: float = 1.0,
    client: Optional[httpx.Client] = None,
) -> Iterator[Hackathon]:
    """
    Yields normalized Hackathon records from Unstop, paging until an
    empty page is returned or max_pages is hit. Filters strictly to
    hackathons only (see _is_hackathon) and skips anything whose
    registration window has already finished.
    """
    owns_client = client is None
    client = client or httpx.Client(headers=HEADERS, timeout=15.0)

    try:
        for page in range(1, max_pages + 1):
            params = {
                # Best-effort hint -- see module docstring on why this
                # alone can't be trusted to actually filter server-side.
                "opportunity": "hackathons",
                "page": page,
                "per_page": per_page,
                "oppstatus": "open",
            }
            resp = client.get(BASE_URL, params=params)
            resp.raise_for_status()
            payload = resp.json()

            entries = ((payload.get("data") or {}).get("data")) or []
            if not entries:
                break

            for entry in entries:
                try:
                    if not _is_hackathon(entry):
                        continue

                    reg = entry.get("regnRequirements") or {}
                    if reg.get("reg_status") == "FINISHED":
                        continue

                    yield _normalize(entry)
                except Exception as exc:  # keep going even if one entry is malformed
                    print(f"[unstop] skipped a malformed entry: {exc}")

            time.sleep(delay_seconds)  # be polite, avoid hammering the endpoint
    finally:
        if owns_client:
            client.close()


if __name__ == "__main__":
    # Quick manual check: python -m sources.unstop
    # If this prints nothing, or prints non-hackathon-looking titles,
    # inspect a raw response (drop the "opportunity"/"oppstatus" params
    # and print payload["data"]["data"][0]) and adjust _is_hackathon().
    for h in fetch_hackathons(max_pages=1):
        print(h.title, "|", h.deadline, "|", h.prize_text, "|", h.themes)
