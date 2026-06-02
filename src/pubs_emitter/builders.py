"""Convert BibTeX entries + YAML CVE specs into Citation / Patent records.

The fetch_* calls used here normally hit a warm cache (planning ran first).
On a cache miss (e.g., new entry slipped through planning) the build phase
falls back to a sequential network call via lookup.fetch_*.
"""
from __future__ import annotations

import logging
import os
import re
import sqlite3
import sys
from datetime import datetime
from typing import Optional

import yaml

from .authors import format_author, format_inventors
from .db import LOOKUP_STATS
from .latex import decode_latex, rtf_escape_unicode
from .lookup import (
    extract_patent_number,
    fetch_cve_data,
    fetch_doi_or_url,
    fetch_patent_date,
)
from .types import (
    BibEntry, Category, Citation, ConferencePresentation, Grant, GrantPerson,
    InvitedTalk, LeadershipRole, MediaAppearance, Patent, Rank, Section,
    ServiceEntry, SoftwareProduct, Student, UnderReview,
)
from .venue import (
    CVE_ID_RE,
    MissingArxivId,
    MissingBracketTag,
    classify_entry,
    extract_arxiv_id,
    extract_figshare_id,
    lookup_rank,
    normalize_title,
    parse_venue,
)


log = logging.getLogger(__name__)


# ----- Small helpers -------------------------------------------------------


def escape_rtf(text: str) -> str:
    """Escape RTF-special chars + encode non-ASCII as \\u<num>? escapes."""
    text = text.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")
    return rtf_escape_unicode(text)


def format_details(entry: BibEntry) -> str:
    """', vol(issue), pages' suffix."""
    vol_issue = entry.get("volume", "")
    if "number" in entry:
        vol_issue += f"({entry['number']})"
    pages = entry.get("pages", "")
    parts = [p for p in (vol_issue, pages) if p]
    return ", " + ", ".join(parts) if parts else ""


def parse_year(year_str: str) -> int:
    """Sort key for chronological order.

    Plain int strings parse directly. Otherwise extract the FIRST 4-digit
    year from the string (so 'Annual, 2015-2019' sorts as 2015). Strings
    with no parseable year sort to the end.
    """
    try:
        return int(year_str)
    except ValueError:
        m = re.search(r"\d{4}", str(year_str))
        return int(m.group(0)) if m else 9999


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
        # Try arXiv first; fall back to figshare (m9.figshare.<id> → 10.6084/...).
        arxiv_id = extract_arxiv_id(entry)
        if arxiv_id:
            LOOKUP_STATS["arxiv_constructed"] += 1
            return f"https://doi.org/10.48550/arXiv.{arxiv_id}"
        figshare_id = extract_figshare_id(entry)
        if figshare_id:
            LOOKUP_STATS["arxiv_constructed"] += 1
            return f"https://doi.org/10.6084/{figshare_id}"
        raise MissingArxivId(title)
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
        raise MissingBracketTag(title)
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


def build_invited_talk(talk: dict) -> InvitedTalk:
    """Build an InvitedTalk record from a YAML dict. No bib lookup needed."""
    year_str = str(talk.get("year", ""))
    return InvitedTalk(
        year=parse_year(year_str),
        year_str=year_str,
        topic=decode_latex(talk.get("topic", "")).replace("\n", " "),
        subtitle=decode_latex(talk.get("subtitle", "")).replace("\n", " "),
        venue=decode_latex(talk.get("venue", "")).replace("\n", " "),
    )


def build_grant(entry: dict) -> Grant:
    """Build a Grant from a YAML dict.

    Amount fields: `purdue_amount` is required; `total_amount` and
    `my_amount` default to `purdue_amount` (sole-PI single-institution case).
    Override either when the grant has multi-institution structure or when
    multiple Purdue PIs split credit.
    """
    purdue = int(entry.get("purdue_amount", 0) or 0)
    total = int(entry.get("total_amount", purdue) or purdue)
    mine = int(entry.get("my_amount", purdue) or purdue)
    personnel = [
        GrantPerson(
            name=decode_latex(p.get("name", "")).replace("\n", " "),
            role=p.get("role", "Co-PI") or "Co-PI",
            department=decode_latex(p.get("department", "") or "").replace("\n", " "),
            institution=decode_latex(p.get("institution", "") or "").replace("\n", " "),
            nsf_award=str(p.get("nsf_award", "") or ""),
        )
        for p in (entry.get("personnel") or [])
    ]
    return Grant(
        start_year=int(entry.get("start_year", 0)),
        end_year=int(entry.get("end_year", 0)),
        title=decode_latex(entry.get("title", "")).replace("\n", " "),
        agency=decode_latex(entry.get("agency", "")).replace("\n", " "),
        agency_short=entry.get("agency_short", "") or "",
        grant_number=str(entry.get("grant_number", "") or ""),
        role=entry.get("role", "") or "",
        lead_institution=decode_latex(
            entry.get("lead_institution", "") or ""
        ).replace("\n", " "),
        personnel=personnel,
        responsibility_percent=int(entry.get("responsibility_percent", 0) or 0),
        total_amount=total,
        purdue_amount=purdue,
        my_amount=mine,
        activities=decode_latex(entry.get("activities", "") or "").replace("\n", " "),
        responsibility=decode_latex(entry.get("responsibility", "") or "").replace("\n", " "),
        inspired_by=list(entry.get("inspired_by") or []),
        publication_outcomes=list(entry.get("publication_outcomes") or []),
    )


def build_student(entry: dict) -> Student:
    """Build a Student record from a YAML dict (used for both C.14 and C.16)."""
    grad_year = int(entry.get("grad_year", 9999) or 9999)
    return Student(
        grad_year=grad_year,
        grad_display=str(entry.get("graduation", "") or ""),
        name=decode_latex(entry.get("name", "")).replace("\n", " "),
        degree=entry.get("degree", "") or "",
        role=entry.get("role", "") or "",
        position=decode_latex(entry.get("position", "") or "").replace("\n", " "),
        co_advisor=decode_latex(entry.get("co_advisor", "") or "").replace("\n", " "),
    )


def _normalize_under_review_authors(s: str) -> str:
    """Convert user-CV author-string forms to BibTeX 'X and Y and Z' form.

    Accepts mixed input shapes from the user's CV: "X & Y", "X, Y, and Z",
    "X, Y and Z", "X and Y", and plain comma-separated "X, Y, Z". Also adds
    a missing period after isolated capital letters before whitespace ("James
    C Davis" → "James C. Davis") so the ME / advisor matcher in
    `authors.name_matches` fires reliably.
    """
    if not s:
        return ""
    # Drop trailing sentence-end period+spaces on the full string.
    s = s.strip().rstrip(". ")
    # Insert dots after bare middle initials before format_author processes.
    s = re.sub(r"\b([A-Z])\b(?=\s)", r"\1.", s)
    # Unify delimiters → comma; final split + rejoin produces canonical
    # " and " form.
    s = s.replace(" & ", ", ")
    s = s.replace(", and ", ", ")
    s = s.replace(" and ", ", ")
    parts = [p.strip() for p in s.split(",")]
    return " and ".join(p for p in parts if p)


def build_under_review(conn: sqlite3.Connection, entry: dict) -> UnderReview:
    """Build an A.1 UnderReview record from a YAML dict.

    Author string is normalized to BibTeX " and " form, then each author is
    run through `format_author` so role markers (G / # / *) + bold-for-me
    fire — same convention as published C.2 / C.4 citations.
    """
    raw_authors = entry.get("authors", "") or ""
    bib_form = _normalize_under_review_authors(raw_authors)
    author_list = bib_form.split(" and ") if bib_form else []
    formatted = [
        format_author(conn, a.strip(), is_last=i == len(author_list) - 1)
        for i, a in enumerate(author_list)
    ]
    return UnderReview(
        # 9999-99-99 sorts unknown-due-date entries to the bottom of the list.
        due_date=str(entry.get("due_date", "") or "9999-99-99"),
        title=decode_latex(entry.get("title", "")).replace("\n", " "),
        authors_rtf=", ".join(formatted),
        venue=decode_latex(entry.get("venue", "")).replace("\n", " "),
        pages=str(entry.get("pages", "") or ""),
    )


def build_service_entry(entry: dict) -> ServiceEntry:
    """Build a ServiceEntry from a YAML dict (C.23 / C.24 / C.25 / C.26).

    `year` field may be int (2025) or string ("2025, 2026, 2027" /
    "2024-2025" / "2023-present"). Empty / missing → year_str="", which
    suppresses the trailing year on render (used for journal reviewing).
    """
    year_raw = entry.get("year", "")
    year_str = str(year_raw) if year_raw not in (None, "") else ""
    return ServiceEntry(
        year=parse_year(year_str) if year_str else 9999,
        year_str=year_str,
        description=decode_latex(entry.get("description", "")).replace("\n", " "),
    )


def build_conference_presentation(entry: dict) -> ConferencePresentation:
    """Build a ConferencePresentation. All metadata comes from the linked paper."""
    return ConferencePresentation(
        paper_title=entry.get("paper_title", ""),
    )


def build_media_appearance(entry: dict) -> MediaAppearance:
    """Build a MediaAppearance from a YAML dict."""
    year_str = str(entry.get("year", ""))
    return MediaAppearance(
        year=parse_year(year_str),
        year_str=year_str,
        title=decode_latex(entry.get("title", "")).replace("\n", " "),
        venue=decode_latex(entry.get("venue", "")).replace("\n", " "),
        url=entry.get("url", "") or "",
    )


def build_leadership_role(role: dict) -> LeadershipRole:
    """Build a LeadershipRole record from a YAML dict."""
    year_str = str(role.get("year", ""))
    return LeadershipRole(
        year=parse_year(year_str),
        year_str=year_str,
        role=decode_latex(role.get("role", "")).replace("\n", " "),
        description=decode_latex(role.get("description", "")).replace("\n", " "),
        society=decode_latex(role.get("society", "")).replace("\n", " "),
    )


def build_book_chapter(conn: sqlite3.Connection, entry: BibEntry) -> Citation:
    """Assemble a Citation for an @incollection / @inbook entry.

    Book chapters skip the [ACRONYM'YY] bracket-tag requirement and the
    Crossref/DBLP lookup (book DOIs aren't in those databases). Link comes
    from the doi_cache, normally pre-seeded via `manual_links` in
    assets/config.yaml.
    """
    title = decode_latex(entry.get("title", "")).replace("\n", " ")
    raw_authors = entry.get("author", "")
    year_str = entry.get("year", "In Press")

    booktitle = decode_latex(entry.get("booktitle", "")).replace("\n", " ")
    publisher = decode_latex(entry.get("publisher", "")).replace("\n", " ")
    venue = f"{booktitle} ({publisher})" if booktitle and publisher else booktitle or publisher

    pages = entry.get("pages", "")
    details = f", {pages}" if pages else ""

    author_list = raw_authors.split(" and ")
    formatted_authors = [
        format_author(conn, a.strip(), is_last=i == len(author_list) - 1)
        for i, a in enumerate(author_list)
    ]
    authors_rtf = ", ".join(formatted_authors)

    from .db import cache_read_doi  # local: same pattern as build_thesis
    link = cache_read_doi(conn, title) or ""

    return Citation(
        section="Books and Chapters",
        rank="Book Chapter",
        year=parse_year(year_str),
        year_str=year_str,
        authors_rtf=authors_rtf,
        title=title,
        venue=venue,
        details=details,
        link=link,
    )


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


def build_patent(
    conn: sqlite3.Connection,
    entry: BibEntry,
    patent_impacts: Optional[dict[str, str]] = None,
) -> Patent:
    """Build a Patent record. `patent_impacts` maps the clean (digits-only)
    patent number to its impact statement; "None yet" / blank is fine for
    pending entries. When the map has no entry for a given patent the impact
    cell stays empty (renderer will leave it blank in the table)."""
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

    impact = (patent_impacts or {}).get(number_clean, "") if number_clean else ""

    return Patent(
        year=parse_year(year_str),
        year_str=year_str,
        title=title,
        co_inventors=inventors,
        date=display_date,
        number=number_display,
        impact=impact,
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
    """Collect ALL schema + title-resolution violations; report and exit if any."""
    bib_titles = {
        normalize_title(e.get("title", ""))
        for e in bib_entries
        if e.get("title")
    }
    errors: list[str] = []
    for cve in non_scholar.get("cves") or []:
        if not isinstance(cve, dict):
            errors.append(
                f"Each `cves[]` entry must be a mapping; got {type(cve).__name__}"
            )
            continue
        cve_id = cve.get("cve_id", "")
        if not CVE_ID_RE.fullmatch(cve_id or ""):
            errors.append(f"CVE entry has invalid or missing `cve_id`: {cve_id!r}")
            continue
        # `organization` is optional — auto-derived from NVD's CPE product if missing.
        paper_title = cve.get("paper_title")
        disclosers = cve.get("disclosers")
        if not paper_title and not disclosers:
            errors.append(
                f"CVE {cve_id} must specify `paper_title` and/or `disclosers`"
            )
        if paper_title and normalize_title(paper_title) not in bib_titles:
            errors.append(
                f"CVE {cve_id} references paper_title {paper_title!r}, "
                f"but no matching entry exists in the bib "
                f"(case/whitespace-insensitive match but same text required)."
            )

    for key in ("grants_as_pi", "grants_as_co_pi", "gifts", "internal_grants"):
        for i, grant in enumerate(non_scholar.get(key) or []):
            if not isinstance(grant, dict):
                errors.append(
                    f"`{key}[{i}]` must be a mapping; got {type(grant).__name__}"
                )
                continue
            # agency_short is optional — when empty, the head line drops the
            # "{agency_short}: " prefix and renders just the title (used for
            # fellowships, internal grants, and other entries with no
            # canonical funder-name prefix). `total_amount` and `my_amount`
            # are optional too — they default to `purdue_amount` at build time
            # when not specified (sole-PI single-institution case).
            for required in (
                "title", "agency", "role", "start_year", "end_year",
                "purdue_amount",
            ):
                if grant.get(required) in (None, ""):
                    errors.append(f"{key}[{i}] missing required field `{required}`")
            # inspired_by + publication_outcomes paper titles must resolve to bib entries.
            for link_field in ("inspired_by", "publication_outcomes"):
                for title in grant.get(link_field) or []:
                    if normalize_title(title) not in bib_titles:
                        errors.append(
                            f"{key}[{i}] {link_field} references {title!r}, "
                            f"but no matching bib entry exists."
                        )

    for key in ("graduate_students", "undergraduate_students"):
        for i, s in enumerate(non_scholar.get(key) or []):
            if not isinstance(s, dict):
                errors.append(
                    f"`{key}[{i}]` must be a mapping; got {type(s).__name__}"
                )
                continue
            for required in ("name", "degree", "role", "grad_year"):
                if s.get(required) in (None, ""):
                    errors.append(f"{key}[{i}] missing required field `{required}`")

    for i, ur in enumerate(non_scholar.get("under_review") or []):
        if not isinstance(ur, dict):
            errors.append(
                f"`under_review[{i}]` must be a mapping; got {type(ur).__name__}"
            )
            continue
        for required in ("title", "authors", "venue"):
            if not ur.get(required):
                errors.append(
                    f"under_review[{i}] missing required field `{required}`"
                )

    for key in (
        "university_service", "profession_service", "national_service", "other_service",
    ):
        for i, entry in enumerate(non_scholar.get(key) or []):
            if not isinstance(entry, dict):
                errors.append(
                    f"`{key}[{i}]` must be a mapping; got {type(entry).__name__}"
                )
                continue
            if not entry.get("description"):
                errors.append(f"{key}[{i}] missing required field `description`")

    for i, pres in enumerate(non_scholar.get("conference_presentations") or []):
        if not isinstance(pres, dict):
            errors.append(
                f"`conference_presentations[{i}]` must be a mapping; "
                f"got {type(pres).__name__}"
            )
            continue
        paper_title = pres.get("paper_title")
        if not paper_title:
            errors.append(
                f"conference_presentations[{i}] missing required field `paper_title`"
            )
            continue
        if normalize_title(paper_title) not in bib_titles:
            errors.append(
                f"conference_presentations references paper_title {paper_title!r}, "
                f"but no matching bib entry exists."
            )

    for i, media in enumerate(non_scholar.get("media_appearances") or []):
        if not isinstance(media, dict):
            errors.append(
                f"`media_appearances[{i}]` must be a mapping; got {type(media).__name__}"
            )
            continue
        for required in ("title", "venue", "year"):
            if not media.get(required):
                errors.append(
                    f"media_appearances[{i}] missing required field `{required}`"
                )

    for i, role in enumerate(non_scholar.get("leadership_roles") or []):
        if not isinstance(role, dict):
            errors.append(
                f"`leadership_roles[{i}]` must be a mapping; got {type(role).__name__}"
            )
            continue
        for required in ("role", "description", "society", "year"):
            if not role.get(required):
                errors.append(
                    f"leadership_roles[{i}] missing required field `{required}`"
                )

    for i, talk in enumerate(non_scholar.get("invited_talks") or []):
        if not isinstance(talk, dict):
            errors.append(
                f"`invited_talks[{i}]` must be a mapping; got {type(talk).__name__}"
            )
            continue
        if not talk.get("venue"):
            errors.append(f"invited_talks[{i}] missing required field `venue`")
        if not talk.get("year"):
            errors.append(f"invited_talks[{i}] missing required field `year`")
        if not talk.get("topic") and not talk.get("subtitle"):
            errors.append(
                f"invited_talks[{i}] must specify `topic` and/or `subtitle`"
            )

    for i, kw in enumerate(non_scholar.get("key_works") or []):
        if not isinstance(kw, dict):
            errors.append(
                f"`key_works[{i}]` must be a mapping; got {type(kw).__name__}"
            )
            continue
        paper_title = kw.get("paper_title")
        if not paper_title:
            errors.append(f"key_works[{i}] missing required field `paper_title`")
            continue
        if not kw.get("impact"):
            errors.append(
                f"key_works for paper {paper_title!r} missing `impact`"
            )
        if normalize_title(paper_title) not in bib_titles:
            errors.append(
                f"key_works references paper_title {paper_title!r}, "
                f"but no matching bib entry exists."
            )
        # Soft warning: impact text > 100 words is over the recommended limit.
        impact = kw.get("impact") or ""
        if impact and len(impact.split()) > 100:
            log.warning(
                "key_works for paper %r: impact is %d words (>100 recommended).",
                paper_title, len(impact.split()),
            )

    for i, disc in enumerate(non_scholar.get("security_disclosures") or []):
        if not isinstance(disc, dict):
            errors.append(
                f"`security_disclosures[{i}]` must be a mapping; got {type(disc).__name__}"
            )
            continue
        paper_title = disc.get("paper_title")
        if not paper_title:
            errors.append(
                f"security_disclosure[{i}] missing required field `paper_title`"
            )
            continue
        if not disc.get("vendor"):
            errors.append(
                f"security_disclosure for paper {paper_title!r} missing `vendor`"
            )
        if not disc.get("description"):
            errors.append(
                f"security_disclosure for paper {paper_title!r} missing `description`"
            )
        if normalize_title(paper_title) not in bib_titles:
            errors.append(
                f"security_disclosure references paper_title {paper_title!r}, "
                f"but no matching bib entry exists."
            )

    if errors:
        log.error(
            "%d validation error(s) in non-Scholar YAML:", len(errors),
        )
        for e in errors:
            log.error("  - %s", e)
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


def _cve_nvd_organization(cve_data: Optional[dict]) -> str:
    """Derive a human-readable affected-product name from NVD's CPE configs.

    Returns the product name from the first CPE match (e.g. `freertos-plus-tcp`
    from `cpe:2.3:a:amazon:freertos-plus-tcp:...`). Empty string if no CPE
    data. CPE names are lower-kebab-case and ugly — user can override via
    YAML `organization:` for any entry where the auto-derived name is wrong.
    """
    if not cve_data:
        return ""
    for cfg in cve_data.get("configurations") or []:
        for node in cfg.get("nodes") or []:
            for match in node.get("cpeMatch") or []:
                criteria = match.get("criteria", "")
                # cpe:2.3:a:vendor:product:version:...  → take parts[4] (product)
                parts = criteria.split(":")
                if len(parts) > 4 and parts[4]:
                    return str(parts[4])
    return ""


def _bib_entry_by_title(
    bib_entries: list[BibEntry], paper_title: str,
) -> Optional[BibEntry]:
    target = normalize_title(paper_title)
    for e in bib_entries:
        if normalize_title(e.get("title", "")) == target:
            return e
    return None


def build_disclosure_from_yaml(
    conn: sqlite3.Connection,
    disc: dict,
    bib_entries: list[BibEntry],
) -> Citation:
    """Build a C.5 Citation for a security disclosure (no CVE, vendor-acknowledged).

    Always linked to a paper (no stand-alone disclosures). Authors + year
    inherit from the linked paper; description is the user-supplied brief
    note; vendor renders as the italicized venue.
    """
    paper_title = disc["paper_title"]
    paper = _bib_entry_by_title(bib_entries, paper_title)
    # validate_non_scholar guarantees paper exists; this is belt-and-suspenders.
    raw_authors = paper.get("author", "") if paper else ""
    year_str = disc.get("year") or (paper.get("year", "") if paper else "In Press")

    author_list = raw_authors.split(" and ")
    formatted = [
        format_author(conn, a.strip(), is_last=i == len(author_list) - 1)
        for i, a in enumerate(author_list)
    ]
    authors_rtf = ", ".join(formatted)

    # Strip trailing period so the render's own `, venue.` doesn't double up.
    title = decode_latex(disc["description"]).replace("\n", " ").rstrip(". ")
    venue = disc["vendor"]
    link = disc.get("url") or ""

    return Citation(
        section="Other publications and products",
        rank="Disclosure",
        year=parse_year(year_str),
        year_str=year_str,
        authors_rtf=authors_rtf,
        title=title,
        venue=venue,
        details="",
        link=link,
        back_ref_title=paper_title,
    )


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
    # YAML override wins; otherwise fall back to NVD's CPE-derived product name.
    # NVD's name is lower-kebab-case (e.g., `freertos-plus-tcp`) — friendly forms
    # like "AWS (FreeRTOS)" need explicit `organization:` in YAML.
    organization = cve.get("organization") or _cve_nvd_organization(cve_data)

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
