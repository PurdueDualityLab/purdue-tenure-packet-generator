"""HTTP fetchers + per-host rate limiting + retry-with-backoff.

Each `try_*` is a *pure* network call: returns the URL/dict/etc. or empty/None
on miss. No DB writes, no cache reads — `lookup.py` orchestrates that.

Why this matters: when N workers fan out to the same host concurrently,
anonymous-pool APIs (Crossref, DBLP, NVD-without-key) throttle aggressively.
A per-host RateLimiter serializes workers through a minimum-interval gate;
polite_get() wraps that with exponential-backoff retry on transient failures
(429, 5xx, connection reset).
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
import urllib.parse
from typing import Optional, cast

import requests

from .config import (
    BACKOFF_BASE_S,
    CROSSREF_API_URL,
    DBLP_API_URL,
    DEFAULT_HEADERS,
    MAX_RETRIES,
    NO_DOI_ACRONYM_PREFIXES,
    NVD_API_URL,
    PATENTSVIEW_API_URL,
    TITLE_MATCH_THRESHOLD,
    TRANSIENT_HTTP_CODES,
)


log = logging.getLogger(__name__)


class RateLimiter:
    """Per-host minimum-interval gate. Thread-safe; workers serialize through acquire()."""

    def __init__(self, min_interval_s: float):
        self.min_interval = min_interval_s
        self._lock = threading.Lock()
        self._next_ok = 0.0

    def acquire(self) -> None:
        with self._lock:
            now = time.monotonic()
            wait = self._next_ok - now
            if wait > 0:
                time.sleep(wait)
                now += wait
            self._next_ok = now + self.min_interval


# NVD without an API key: 5 requests per rolling 30s (~6s between calls).
# With a key: 50/30s (~0.6s). Decided at module import.
_NVD_INTERVAL_S = 0.7 if os.environ.get("NVD_API_KEY") else 6.5


_HOST_LIMITERS: dict[str, RateLimiter] = {
    "api.crossref.org": RateLimiter(0.1),        # ~10 req/s, polite-pool comfort
    "dblp.org": RateLimiter(0.2),                # ~5 req/s; DBLP throttles harder
    "services.nvd.nist.gov": RateLimiter(_NVD_INTERVAL_S),
    "search.patentsview.org": RateLimiter(0.5),
}


def polite_get(host: str, url: str, **kwargs) -> Optional[requests.Response]:
    """Rate-limited + backoff-retrying GET.

    Returns the final Response (even on bad status — caller checks) or None
    if every attempt raised a transport-layer exception. Respects `Retry-After`.
    """
    limiter = _HOST_LIMITERS.get(host)
    timeout = kwargs.pop("timeout", 10)
    last_resp: Optional[requests.Response] = None
    for attempt in range(MAX_RETRIES + 1):
        if limiter:
            limiter.acquire()
        try:
            resp = requests.get(url, timeout=timeout, **kwargs)
        except (requests.ConnectionError, requests.Timeout) as e:
            log.warning(
                "%s transient (attempt %d/%d): %s",
                host, attempt + 1, MAX_RETRIES + 1, e,
            )
            if attempt == MAX_RETRIES:
                return None
        else:
            if resp.status_code not in TRANSIENT_HTTP_CODES:
                return resp
            last_resp = resp
            log.warning(
                "%s HTTP %d (attempt %d/%d)",
                host, resp.status_code, attempt + 1, MAX_RETRIES + 1,
            )
            if attempt == MAX_RETRIES:
                return resp
        # Backoff. Honor Retry-After if the server sent a parseable one.
        wait = (2 ** attempt) * BACKOFF_BASE_S
        if last_resp is not None:
            ra = last_resp.headers.get("Retry-After", "")
            if ra.isdigit():
                wait = max(wait, float(ra))
        time.sleep(wait)
    return last_resp


# ----- Title similarity (for Crossref/DBLP fuzzy-match rejection) ---------


def _title_tokens(s: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", s.lower()) if len(w) >= 3}


def titles_similar(a: str, b: str, threshold: float = TITLE_MATCH_THRESHOLD) -> bool:
    """Jaccard overlap of significant tokens, ≥ threshold."""
    ta, tb = _title_tokens(a), _title_tokens(b)
    if not ta or not tb:
        return False
    return (len(ta & tb) / len(ta | tb)) >= threshold


def is_no_doi_venue(acronym: Optional[str]) -> bool:
    """Bracket-tag acronym indicates a venue that doesn't register DOIs (e.g., USENIX)."""
    if not acronym:
        return False
    return any(acronym.upper().startswith(p) for p in NO_DOI_ACRONYM_PREFIXES)


# ----- Concrete API fetchers ----------------------------------------------


def try_crossref(title: str, authors: str) -> str:
    """Returns a DOI URL if Crossref's top hit's title is similar to ours, else ''."""
    query = urllib.parse.quote(f"{title} {authors.split(' and ')[0]}")
    url = f"{CROSSREF_API_URL}?query={query}&select=DOI,title&rows=1"
    resp = polite_get("api.crossref.org", url, headers=DEFAULT_HEADERS)
    if resp is None or resp.status_code != 200:
        if resp is not None:
            log.warning("Crossref HTTP %d for '%s'", resp.status_code, title[:60])
        return ""
    try:
        items = resp.json().get("message", {}).get("items", [])
    except ValueError as e:
        log.warning("Crossref non-JSON body for '%s': %s", title[:60], e)
        return ""
    if not items:
        return ""
    returned_title = (items[0].get("title") or [""])[0]
    if not titles_similar(title, returned_title):
        log.warning(
            "Crossref returned a likely-wrong match for '%s' (got '%s'); skipping",
            title[:60], returned_title[:60],
        )
        return ""
    return f"https://doi.org/{items[0]['DOI']}"


def try_dblp(title: str) -> str:
    """Returns a URL if DBLP's top hit's title is similar to ours, else ''."""
    q_title = urllib.parse.quote(title)
    url = f"{DBLP_API_URL}?q={q_title}&format=json&h=1"
    resp = polite_get("dblp.org", url, headers=DEFAULT_HEADERS)
    if resp is None or resp.status_code != 200:
        if resp is not None:
            log.warning("DBLP HTTP %d for '%s'", resp.status_code, title[:60])
        return ""
    try:
        hits = resp.json().get("result", {}).get("hits", {}).get("hit", [])
    except ValueError as e:
        log.warning("DBLP non-JSON body for '%s': %s", title[:60], e)
        return ""
    if not hits:
        return ""
    info = hits[0].get("info", {}) or {}
    returned_title = info.get("title", "") or ""
    if not titles_similar(title, returned_title):
        log.warning(
            "DBLP returned a likely-wrong match for '%s' (got '%s'); skipping",
            title[:60], returned_title[:60],
        )
        return ""
    return info.get("ee", "") or ""


def try_doi(title: str, authors: str, acronym: Optional[str] = None) -> str:
    """Crossref (skipped for no-DOI venues like USENIX) then DBLP. Returns URL or ''."""
    link = "" if is_no_doi_venue(acronym) else try_crossref(title, authors)
    return link or try_dblp(title)


def try_patentsview(number_clean: str) -> Optional[str]:
    """Query PatentsView for the patent issue date. Returns 'YYYY-MM-DD' or None.

    Requires PATENTSVIEW_API_KEY env var (free registration). When absent,
    returns None so the caller falls back to the bib date.
    """
    api_key = os.environ.get("PATENTSVIEW_API_KEY", "")
    if not api_key:
        log.debug(
            "PATENTSVIEW_API_KEY not set; skipping USPTO lookup for %s", number_clean,
        )
        return None
    try:
        params = {
            "q": json.dumps({"patent_id": number_clean}),
            "f": json.dumps(["patent_id", "patent_date"]),
        }
        headers = {**DEFAULT_HEADERS, "X-Api-Key": api_key}
        resp = polite_get(
            "search.patentsview.org", PATENTSVIEW_API_URL,
            params=params, headers=headers,
        )
        if resp is None or resp.status_code != 200:
            if resp is not None:
                log.warning(
                    "PatentsView HTTP %d for patent %s", resp.status_code, number_clean,
                )
            return None
        data = resp.json()
        patents = data.get("patents") or []
        if patents and patents[0].get("patent_date"):
            return cast(str, patents[0]["patent_date"])
    except Exception as e:  # pylint: disable=broad-except
        log.warning("PatentsView query failed for patent %s: %s", number_clean, e)
    return None


def try_nvd(cve_id: str) -> Optional[dict]:
    """Query NIST NVD for a CVE record. Returns the inner cve dict or None.

    NVD_API_KEY env var raises the rate limit ~10x (50/30s vs 5/30s).
    """
    api_key = os.environ.get("NVD_API_KEY", "")
    headers = dict(DEFAULT_HEADERS)
    if api_key:
        headers["apiKey"] = api_key
    resp = polite_get(
        "services.nvd.nist.gov", NVD_API_URL,
        params={"cveId": cve_id}, headers=headers,
    )
    if resp is None or resp.status_code != 200:
        if resp is not None:
            log.warning("NVD HTTP %d for %s", resp.status_code, cve_id)
        return None
    try:
        vulns = resp.json().get("vulnerabilities") or []
    except ValueError as e:
        log.warning("NVD non-JSON body for %s: %s", cve_id, e)
        return None
    if vulns:
        return cast(Optional[dict], vulns[0].get("cve"))
    log.warning("NVD has no record for %s", cve_id)
    return None
