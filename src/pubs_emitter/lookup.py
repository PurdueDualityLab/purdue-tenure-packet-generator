"""Network-task planning + parallel dispatch + result commit + cache-aware fetchers.

Pipeline (called from cli.main):
  1. plan_lookups()      → list[NetworkTask] for items not already cached
  2. dispatch_parallel() → run them concurrently via ThreadPoolExecutor
  3. commit_results()    → persist results into the appropriate cache table
After that, the build phase reads from the now-warm cache via fetch_*.
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import partial
from typing import Any, Optional

from .config import DEFAULT_MAX_WORKERS
from .db import (
    LOOKUP_STATS,
    cache_read_cve,
    cache_read_doi,
    cache_read_patent,
)
from .latex import decode_latex
from .network import try_doi, try_nvd, try_patentsview
from .types import BibEntry, NetworkTask
from .venue import classify_entry, is_book_chapter_entry, is_patent_entry, parse_venue


log = logging.getLogger(__name__)


# Patent number extraction lives here (not venue.py) because only plan_lookups
# + build_patent need it, both of which already import this module.


def extract_patent_number(note: str) -> tuple[str, str]:
    """('US Patent 11,176,090', '11176090') from the bib note; ('','') on failure."""
    m = re.search(r"(US\s+Patent\s+([\d,]+))", note, re.IGNORECASE)
    if m:
        return m.group(1), re.sub(r"\D", "", m.group(2))
    m = re.search(r"([\d][\d,]*)", note)
    if m:
        return note.strip(), re.sub(r"\D", "", m.group(1))
    return "", ""


# ----- Planning ----------------------------------------------------------


def plan_lookups(
    conn: sqlite3.Connection,
    entries: list[BibEntry],
    non_scholar: Optional[dict] = None,
) -> list[NetworkTask]:
    """Walk every entry + YAML CVE list, return NetworkTasks for cache misses.

    Empty list = the warm-cache path; main() will skip dispatch entirely.
    """
    tasks: list[NetworkTask] = []
    cur = conn.cursor()

    for entry in entries:
        # Book chapters bypass DOI lookup — link comes from manual_links if any.
        if is_book_chapter_entry(entry):
            continue
        if is_patent_entry(entry):
            _, number_clean = extract_patent_number(entry.get("note", ""))
            if number_clean:
                cur.execute(
                    "SELECT 1 FROM patent_cache WHERE patent_id=?", (number_clean,),
                )
                if not cur.fetchone():
                    tasks.append(NetworkTask(
                        kind="patent",
                        key=number_clean,
                        fetcher=partial(try_patentsview, number_clean),
                    ))
            continue

        # Citation entry. Skip arXiv — its DOI is constructed, no lookup needed.
        category, venue_raw = classify_entry(entry)
        if category == "arXiv / Preprints":
            continue
        title = decode_latex(entry.get("title", "")).replace("\n", " ")
        if not title:
            continue
        cur.execute("SELECT 1 FROM doi_cache WHERE title=?", (title.lower(),))
        if cur.fetchone():
            continue
        # Acronym from bracket-tag — used to skip Crossref for no-DOI venues.
        acronym, _ = parse_venue(decode_latex(venue_raw))
        authors = entry.get("author", "")
        tasks.append(NetworkTask(
            kind="doi",
            key=title.lower(),
            fetcher=partial(try_doi, title, authors, acronym),
        ))

    # YAML CVEs.
    if non_scholar:
        for cve in non_scholar.get("cves") or []:
            cve_id = (cve.get("cve_id") or "").upper()
            if not cve_id:
                continue
            cur.execute("SELECT 1 FROM cve_cache WHERE cve_id=?", (cve_id,))
            if cur.fetchone():
                continue
            tasks.append(NetworkTask(
                kind="cve",
                key=cve_id,
                fetcher=partial(try_nvd, cve_id),
            ))

    return tasks


# ----- Parallel dispatch -------------------------------------------------


def dispatch_parallel(
    tasks: list[NetworkTask], max_workers: int = DEFAULT_MAX_WORKERS,
) -> dict[tuple[str, str], Any]:
    """Run all NetworkTasks concurrently. Returns {(kind, key): result_or_None}."""
    results: dict[tuple[str, str], Any] = {}
    if not tasks:
        return results
    log.info(
        "Dispatching %d network lookup(s) with max_workers=%d",
        len(tasks), max_workers,
    )
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        future_to_task = {ex.submit(t.fetcher): t for t in tasks}
        for fut in as_completed(future_to_task):
            t = future_to_task[fut]
            try:
                results[(t.kind, t.key)] = fut.result()
            except Exception as e:  # pylint: disable=broad-except
                log.warning("Lookup %s/%s raised: %s", t.kind, t.key, e)
                results[(t.kind, t.key)] = None
    return results


def commit_results(
    conn: sqlite3.Connection, results: dict[tuple[str, str], Any],
) -> None:
    """Persist parallel-fetched results into their respective cache tables."""
    if not results:
        return
    cur = conn.cursor()
    for (kind, key), value in results.items():
        if kind == "doi":
            cur.execute(
                "INSERT OR REPLACE INTO doi_cache VALUES (?, ?)", (key, value or ""),
            )
        elif kind == "patent":
            cur.execute(
                "INSERT OR REPLACE INTO patent_cache VALUES (?, ?)",
                (key, value or ""),
            )
        elif kind == "cve":
            cur.execute(
                "INSERT OR REPLACE INTO cve_cache VALUES (?, ?)",
                (key, json.dumps(value) if value else ""),
            )
    conn.commit()


# ----- Cache-or-fetch fallbacks (defensive; planning should warm everything) ----


def fetch_doi_or_url(
    conn: sqlite3.Connection,
    title: str,
    authors: str,
    acronym: Optional[str] = None,
) -> str:
    """Cache read → on miss, sequential network call → cache write. Used by build."""
    cached = cache_read_doi(conn, title)
    if cached is not None:
        LOOKUP_STATS["doi_cache_hits"] += 1
        log.debug("DOI cache hit: %s", title[:60])
        return cached
    LOOKUP_STATS["doi_cache_misses"] += 1
    log.info("Fetching DOI for: %s (sequential fallback)", title[:60])
    link = try_doi(title, authors, acronym)
    if not link:
        log.warning("No DOI/URL found for: %s", title[:60])
    cur = conn.cursor()
    cur.execute("INSERT INTO doi_cache VALUES (?, ?)", (title.lower(), link))
    conn.commit()
    return link


def fetch_patent_date(
    conn: sqlite3.Connection, number_clean: str, fallback_date: str,
) -> str:
    """Cache read → on miss, USPTO call → cache write. Falls back to bib date."""
    cached = cache_read_patent(conn, number_clean)
    if cached is not None:
        LOOKUP_STATS["patent_cache_hits"] += 1
        log.debug("Patent cache hit: %s -> %s", number_clean, cached)
        return cached or fallback_date
    LOOKUP_STATS["patent_cache_misses"] += 1
    log.info("Fetching patent issue date for: %s (sequential fallback)", number_clean)
    iso = try_patentsview(number_clean)
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO patent_cache VALUES (?, ?)", (number_clean, iso or ""),
    )
    conn.commit()
    if iso:
        return iso
    log.info(
        "No USPTO date for %s; using bib date '%s'", number_clean, fallback_date,
    )
    return fallback_date


def fetch_cve_data(conn: sqlite3.Connection, cve_id: str) -> Optional[dict]:
    """Cache read → on miss, NVD call → cache write. Returns NVD record or None."""
    cached = cache_read_cve(conn, cve_id)
    if cached is not None:
        LOOKUP_STATS["cve_cache_hits"] += 1
        return cached
    # `cache_read_cve` returns None for both "uncached" and "cached empty";
    # we need to distinguish for hit-counting. Probe again:
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM cve_cache WHERE cve_id=?", (cve_id,))
    if cur.fetchone():
        LOOKUP_STATS["cve_cache_hits"] += 1
        return None
    LOOKUP_STATS["cve_cache_misses"] += 1
    log.info("Fetching NVD data for: %s (sequential fallback)", cve_id)
    data = try_nvd(cve_id)
    cur.execute(
        "INSERT INTO cve_cache VALUES (?, ?)",
        (cve_id, json.dumps(data) if data else ""),
    )
    conn.commit()
    return data
