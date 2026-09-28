from datetime import datetime, timezone
import re
from typing import Optional

import yaml

from models import Hackathon


_UNSTOP_INSTITUTION_PATTERNS = {
    "iim": (
        re.compile(r"\biims?\b", re.IGNORECASE),
        re.compile(r"\bindian institutes? of management\b", re.IGNORECASE),
    ),
    "iit": (
        re.compile(r"\biits?\b", re.IGNORECASE),
        re.compile(r"\bindian institutes? of technology\b", re.IGNORECASE),
    ),
}


def unstop_institution_type(h: Hackathon) -> Optional[str]:
    """Return 'iim' or 'iit' only for an Unstop listing that names one."""
    if (h.source or "").lower() != "unstop":
        return None

    haystack = " ".join(
        str(value or "")
        for value in (
            h.title,
            h.url,
            h.location,
            getattr(h, "college", None),
            getattr(h, "organizer", None),
        )
    ).replace("-", " ")

    for institution_type, patterns in _UNSTOP_INSTITUTION_PATTERNS.items():
        if any(pattern.search(haystack) for pattern in patterns):
            return institution_type
    return None


def load_preferences(path: str = "preferences.yaml") -> dict:
    with open(path, "r") as f:
        return yaml.safe_load(f) or {}


def matches(h: Hackathon, prefs: dict) -> bool:
    # --- theme keyword match (title + themes) ---
    theme_keywords = [t.lower() for t in prefs.get("themes", [])]
    if theme_keywords:
        haystack = (h.title + " " + " ".join(h.themes)).lower()
        if not any(k in haystack for k in theme_keywords):
            return False

    # --- mode ---
    allowed_modes = [m.lower() for m in prefs.get("mode", {}).get("allowed", [])]
    if allowed_modes and (h.mode or "").lower() not in allowed_modes:
        return False

    # --- Unstop institution filter ---
    # Supported values: "iim"/"iims" and "iit"/"iits".
    # This is intentionally separate from location filtering because Unstop
    # locations are usually cities, while institution names are often in the
    # title, URL slug, college, or organizer field.
    unstop_prefs = prefs.get("unstop", {}) or {}
    allowed_institutions = (
        unstop_prefs.get("institutions")
        or prefs.get("unstop_institutions")
        or []
    )
    allowed_institutions = {
        str(value).lower().rstrip("s")
        for value in allowed_institutions
        if str(value).strip()
    }
    if allowed_institutions and (
        unstop_institution_type(h) not in allowed_institutions
    ):
        return False

    # --- prize ---
    min_prize = prefs.get("min_prize_amount", 0) or 0
    if min_prize and (h.prize_amount or 0) < min_prize:
        return False

    # --- deadline window ---
    if h.deadline:
        deadline = h.deadline
        if deadline.tzinfo is None:
            deadline = deadline.replace(tzinfo=timezone.utc)
        days_left = (deadline - datetime.now(timezone.utc)).days

        min_days = prefs.get("min_days_until_deadline")
        max_days = prefs.get("max_days_until_deadline")
        if min_days is not None and days_left < min_days:
            return False
        if max_days is not None and days_left > max_days:
            return False

    return True


def filter_hackathons(items: list[Hackathon], prefs: Optional[dict] = None) -> list[Hackathon]:
    prefs = prefs if prefs is not None else load_preferences()
    return [h for h in items if matches(h, prefs)]
