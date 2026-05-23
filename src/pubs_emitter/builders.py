"""Convert BibTeX entries + YAML CVE specs into Citation / Patent records.

The fetch_* calls used here normally hit a warm cache (planning ran first).
On a cache miss (e.g., new entry slipped through planning) the build phase
falls back to a sequential network call via lookup.fetch_*.
"""
from __future__ import annotations

import logging
import os
import sqlite3
import sys
from datetime import datetime
from typing import Optional

import yaml

from .authors import format_author, format_inventors
from .db import LOOKUP_STATS
from .latex import decode_latex
from .lookup import (
    extract_patent_number,
    fetch_cve_data,
    fetch_doi_or_url,
    fetch_patent_date,
)
from .types import BibEntry, Category, Citation, Patent, Rank, Section
from .venue import (
    CVE_ID_RE,
    classify_entry,
    extract_arxiv_id,
    lookup_rank,
    normalize_title,
    parse_venue,
)


log = logging.getLogger(__name__)


# ----- Small helpers -------------------------------------------------------


def escape_rtf(text: str) -> str:
    return text.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")


def format_details(entry: BibEntry) -> str:
    """', vol(issue), pages' suffix."""
    vol_issue = entry.get("volume", "")
    if "number" in entry:
        vol_issue += f"({entry['number']})"
    pages = entry.get("pages", "")
    parts = [p for p in (vol_issue, pages) if p]
    return ", " + ", ".join(parts) if parts else ""


def parse_year(year_str: str) -> int:
    """Sort key for chronological order. Non-int strings sort to the end."""
    try:
        return int(year_str)
    except ValueError:
        return 9999


def derive_section(category: Category, rank: Rank) -> Section:
    """Map (category, rank) to the display section. Magazines + preprints share C.5."""
    if category == "arXiv / Preprints" or rank in ("Magazine", "Preprint"):
        return "Other publications and products"
    if category == "Conferences and Workshops":
        return "Conferences and Workshops"
    return "Journals"


def format_iso_date(iso_date: str) -> str:
    """'2021-11-16' → 'November 16, 2021'. Returns input unchanged on parse failure."""
    try:
        dt = datetime.strptime(iso_date, "%Y-%m-%d")
        return f"{dt.strftime('%B')} {dt.day}, {dt.year}"
    except (ValueError, TypeError):
        return iso_date


def format_bib_date(entry: BibEntry) -> str:
    """Format bib `month` + `year` as a display string. Used when USPTO lookup misses."""
    year = entry.get("year", "")
    month = entry.get("month", "").replace("~", " ").strip()
    if month and year:
        return f"{month} {year}"
    return year


def resolve_link(
    conn: sqlite3.Connection,
    entry: BibEntry,
    category: Category,
    title: str,
    raw_authors: str,
    acronym: Optional[str] = None,
) -> str:
    """arXiv: construct DOI from arXiv ID (hard-fails if missing).
    Everything else: cache-backed DOI lookup (Crossref skipped for no-DOI venues)."""
    if category == "arXiv / Preprints":
        arxiv_id = extract_arxiv_id(entry)
        if not arxiv_id:
            log.error("arXiv entry missing arXiv ID: '%s'", title)
            log.error(
                "Add the ID via `eprint = {<id>}` or include `arXiv:<id>` in the journal field."
            )
            sys.exit(1)
        LOOKUP_STATS["arxiv_constructed"] += 1
        return f"https://doi.org/10.48550/arXiv.{arxiv_id}"
    return fetch_doi_or_url(conn, title, raw_authors, acronym)


# ----- Citation (bib paper) builder ---------------------------------------


def build_citation(conn: sqlite3.Connection, entry: BibEntry) -> Citation:
    """Assemble a Citation. RTF assembly is deferred to render time."""
    title = decode_latex(entry.get("title", "")).replace("\n", " ")
    raw_authors = entry.get("author", "")
    year_str = entry.get("year", "In Press")

    category, venue_str = classify_entry(entry)
    venue_str = decode_latex(venue_str)
    acronym, venue_str = parse_venue(venue_str)
    if not acronym:
        log.error("Missing [ACRONYM'YY] tag in venue field for paper: '%s'", title)
        log.error(
            "Every journal/booktitle must begin with a bracketed tag, e.g. `[ICSE'25]`, "
            "`[JSS'25]`, `[arXiv'26]`."
        )
        sys.exit(1)
    rank: Rank = "Preprint" if category == "arXiv / Preprints" else lookup_rank(acronym, title)
    section = derive_section(category, rank)

    author_list = raw_authors.split(" and ")
    formatted_authors = [
        format_author(conn, a.strip(), is_last=i == len(author_list) - 1)
        for i, a in enumerate(author_list)
    ]
    authors_rtf = ", ".join(formatted_authors)
    link = resolve_link(conn, entry, category, title, raw_authors, acronym)

    return Citation(
        section=section,
        rank=rank,
        year=parse_year(year_str),
        year_str=year_str,
        authors_rtf=authors_rtf,
        title=title,
        venue=venue_str,
        details=format_details(entry),
        link=link,
    )


# ----- Patent builder -----------------------------------------------------


def build_thesis(conn: sqlite3.Connection, entry: BibEntry) -> Citation:
    """Assemble a Citation for an @phdthesis / @mastersthesis entry.

    Theses are exempt from the [ACRONYM'YY] bracket-tag requirement (no venue
    field for it to live on). The link comes from the doi_cache, normally
    pre-seeded via `manual_links` in assets/config.yaml since Scholar exports
    don't carry a DOI for theses.

    Currently built but NOT emitted in any section — held for future cross-
    references (e.g. CVE → thesis back-pointer).
    """
    title = decode_latex(entry.get("title", "")).replace("\n", " ")
    raw_authors = entry.get("author", "")
    year_str = entry.get("year", "In Press")
    school = decode_latex(entry.get("school", "")).replace("\n", " ")

    author_list = raw_authors.split(" and ")
    formatted_authors = [
        format_author(conn, a.strip(), is_last=i == len(author_list) - 1)
        for i, a in enumerate(author_list)
    ]
    authors_rtf = ", ".join(formatted_authors)

    # Link comes from the cache (which `seed_manual_links` populates from
    # assets/config.yaml's `manual_links:` block). Cache miss = no link.
    from .db import cache_read_doi  # local import to avoid widening module surface
    link = cache_read_doi(conn, title) or ""
    if not link:
        log.warning(
            "Thesis '%s' has no link. Add an entry under `manual_links:` "
            "in assets/config.yaml.", title[:60],
        )

    # Use Other-publications-and-products as the placeholder section: never
    # iterated for theses (cli routes them to a separate list) but a valid
    # value for type-checking + future-emission flexibility.
    return Citation(
        section="Other publications and products",
        rank="Dissertation",
        year=parse_year(year_str),
        year_str=year_str,
        authors_rtf=authors_rtf,
        title=title,
        venue=school,
        details="",
        link=link,
    )


def build_patent(conn: sqlite3.Connection, entry: BibEntry) -> Patent:
    title = decode_latex(entry.get("title", "")).replace("\n", " ")
    inventors = format_inventors(entry.get("author", ""))

    number_display, number_clean = extract_patent_number(entry.get("note", ""))
    if not number_clean:
        log.warning("Patent entry has no extractable number in note: '%s'", title)

    year_str = entry.get("year", "")
    fallback = format_bib_date(entry)
    raw_date = (
        fetch_patent_date(conn, number_clean, fallback) if number_clean else fallback
    )
    display_date = format_iso_date(raw_date)

    return Patent(
        year=parse_year(year_str),
        year_str=year_str,
        title=title,
        co_inventors=inventors,
        date=display_date,
        number=number_display,
        impact="",  # Always blank; user fills in manually before submission.
    )


# ----- Non-Scholar YAML (CVE) loader + validator + builder ----------------


def load_non_scholar(path: Optional[str]) -> dict:
    """Load the YAML side file. Returns empty dict if path is None or file missing."""
    if not path:
        return {}
    if not os.path.exists(path):
        log.error("Non-Scholar YAML file not found: %s", path)
        sys.exit(1)
    log.info("Loading non-Scholar work from %s", path)
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        log.error(
            "Non-Scholar YAML root must be a mapping; got %s", type(data).__name__,
        )
        sys.exit(1)
    return data


def validate_non_scholar(non_scholar: dict, bib_entries: list[BibEntry]) -> None:
    """Enforce schema + title-resolution invariants. Crashes on any violation."""
    bib_titles = {
        normalize_title(e.get("title", ""))
        for e in bib_entries
        if e.get("title")
    }
    cves = non_scholar.get("cves") or []
    for cve in cves:
        if not isinstance(cve, dict):
            log.error("Each `cves[]` entry must be a mapping; got %s", type(cve).__name__)
            sys.exit(1)
        cve_id = cve.get("cve_id", "")
        if not CVE_ID_RE.fullmatch(cve_id or ""):
            log.error("CVE entry has invalid or missing `cve_id`: %r", cve_id)
            sys.exit(1)
        if not cve.get("organization"):
            log.error("CVE %s missing required field `organization`", cve_id)
            sys.exit(1)
        paper_title = cve.get("paper_title")
        disclosers = cve.get("disclosers")
        if not paper_title and not disclosers:
            log.error(
                "CVE %s must specify `paper_title` and/or `disclosers`", cve_id,
            )
            sys.exit(1)
        if paper_title and normalize_title(paper_title) not in bib_titles:
            log.error(
                "CVE %s references paper_title %r, but no matching entry exists in the bib.",
                cve_id, paper_title,
            )
            log.error(
                "Bib title matching is case- and whitespace-insensitive but requires "
                "same text."
            )
            sys.exit(1)


def _cve_nvd_description(cve_data: Optional[dict]) -> str:
    """First-sentence English description from an NVD record."""
    if not cve_data:
        return ""
    desc = next(
        (x["value"] for x in (cve_data.get("descriptions") or []) if x.get("lang") == "en"),
        "",
    )
    return desc.split(". ")[0].rstrip(".") if desc else ""


def _cve_nvd_year(cve_data: Optional[dict]) -> str:
    if not cve_data:
        return ""
    return (cve_data.get("published") or "")[:4]


def _bib_entry_by_title(
    bib_entries: list[BibEntry], paper_title: str,
) -> Optional[BibEntry]:
    target = normalize_title(paper_title)
    for e in bib_entries:
        if normalize_title(e.get("title", "")) == target:
            return e
    return None


def build_cve_from_yaml(
    conn: sqlite3.Connection,
    cve: dict,
    bib_entries: list[BibEntry],
) -> Citation:
    """Build a C.5 Citation from one YAML cve entry.

    Authorship rules:
      - If `disclosers` is set in YAML, use those names (with student markers).
      - Else, copy the linked paper's `author` field (with markers).
    """
    cve_id = cve["cve_id"].upper()
    cve_data = fetch_cve_data(conn, cve_id)

    title = cve.get("title") or _cve_nvd_description(cve_data) or cve_id
    title = decode_latex(title).replace("\n", " ")

    # Year: NVD published date so chrono-sort is grounded in disclosure date.
    year_str = _cve_nvd_year(cve_data) or "In Press"

    disclosers = cve.get("disclosers")
    paper_title = cve.get("paper_title")
    if disclosers:
        names = list(disclosers)
    elif paper_title:
        paper = _bib_entry_by_title(bib_entries, paper_title)
        # validate_non_scholar guarantees paper exists; this is defensive.
        names = (paper.get("author", "") if paper else "").split(" and ")
    else:
        names = []
    formatted = [
        format_author(conn, n.strip(), is_last=i == len(names) - 1)
        for i, n in enumerate(names)
    ]
    authors_rtf = ", ".join(formatted)

    link = cve.get("url") or f"https://nvd.nist.gov/vuln/detail/{cve_id}"
    organization = cve["organization"]

    return Citation(
        section="Other publications and products",
        rank="CVE",
        year=parse_year(year_str),
        year_str=year_str,
        authors_rtf=authors_rtf,
        title=title,
        venue=organization,
        details="",
        link=link,
        back_ref_title=paper_title,
    )
