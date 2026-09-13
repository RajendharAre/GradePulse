"""
visit_counter.py
A lightweight visit counter for the deployed app — no database, no signup.

How it counts:
  - Uses the free public "visitor-badge" SVG service
    (visitor-badge.laobi.icu). Each request increments the total for a given
    page_id and returns an SVG whose embedded <text> holds the new total.
  - record_visit() is called ONCE per browser session (Streamlit re-runs the
    script on every interaction, so we must never increment on a warm re-run).
    The counter therefore counts browser sessions / visits, which is exactly
    what a faculty "how many people opened the app" badge should show.
  - If the service is unreachable the badge degrades to "—" and the app never
    breaks. A secondary provider (countapi.xyz) is tried as a fallback.

For deeper stats (unique visitors, geography, device) swap in Google Analytics
later via .streamlit/secrets.toml (tracked in requirements.md §11).
"""

import re
from typing import Optional

import requests

_PAGE_ID = "gradepulse-main"
_PROVIDERS = [
    # (increment, read) — read may be None (no read-only endpoint)
    (
        f"https://visitor-badge.laobi.icu/badge?page_id={_PAGE_ID}",
        None,
    ),
    # countapi.xyz is a documented secondary but its DNS was unreachable in
    # testing; kept as a fallback in case it works from the cloud runner.
    (
        f"https://api.countapi.xyz/hit/{_PAGE_ID}/overall",
        f"https://api.countapi.xyz/get/{_PAGE_ID}/overall",
    ),
]

_NUM_TEXT_RE = re.compile(r"<text[^>]*>([\d,]+)</text>")
_JSON_VALUE_RE = re.compile(r'"value"\s*:\s*(\d+)')


def _parse(text: str) -> Optional[int]:
    nums = _NUM_TEXT_RE.findall(text)
    if nums:
        try:
            return int(nums[-1].replace(",", ""))
        except ValueError:
            return None
    m = _JSON_VALUE_RE.search(text)
    if m:
        return int(m.group(1))
    return None


def _fetch(url: str) -> Optional[int]:
    try:
        r = requests.get(url, timeout=8)
        r.raise_for_status()
        return _parse(r.text)
    except Exception:
        return None


def record_visit() -> Optional[int]:
    """Increment the counter once for this browser session; return the new total."""
    for increment, _ in _PROVIDERS:
        value = _fetch(increment)
        if value is not None:
            return value
    return None


def current_visits() -> Optional[int]:
    """Read the current total WITHOUT incrementing (best effort, may be None)."""
    for _, read in _PROVIDERS:
        if read is None:
            continue
        value = _fetch(read)
        if value is not None:
            return value
    return None