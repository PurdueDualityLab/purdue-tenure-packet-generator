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
from typing import Literal, Optional

import yaml

from .authors import format_author, format_inventors, lookup_student_type
from .config import SECTION_CODES
from .db import LOOKUP_STATS
from .latex import decode_latex, rtf_escape_unicode
from .lookup import (
    extract_patent_number,
    fetch_cve_data,
    fetch_doi_or_url,
    fetch_patent_date,
)
from .types import (
    Award, BibEntry, CandidateInformation, Category, Citation,
    ConferencePresentation, CourseDevelopment, CourseTaught, Degree,
    EntrepreneurialActivity, Grant, GrantPerson, Identifiers, InvitedTalk,
    LeadershipRole, MediaAppearance, OtherPosition, Patent,
    ProfessionalMembership, Publications, Rank, Section,
    ServiceEntry, SoftwareProduct, Student, StudentAward,
    TechnologyTransfer, UndergradPathway, UndergradProduct, UnderReview,
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


def _pluralize_pages(count: int) -> str:
    """Single source of truth for the "N page(s)" string. Singular form
    when count == 1 ("1 page"), plural otherwise ("12 pages"). Every
    site that emits a page-count string must route through here so a
    "1 pages" / "0 pages" class regression can't slip back in."""
    return f"{count} page" + ("" if count == 1 else "s")


def _format_pages(raw: str) -> str:
    """Normalize bib page strings to a `"N page(s)"` signal.

    The point of surfacing pages on a P&T packet line is "is this a
    full paper, a short paper, or a poster?" — a length signal, not a
    page span. So a range like "1--12" becomes "12 pages", not a span.
    Already-formatted "N pages" / "1 page" passes through unchanged.

      * `""`                       → `""`
      * `"62--pages"`              → `"62 pages"`  (Scholar form)
      * `"1--pages"`               → `"1 page"`    (singular Scholar form)
      * `"N pages"` / `"1 page"`   → passthrough (author wrote it)
      * `"N--M"` / `"N-M"` / `"N–M"` / `"N—M"` → `_pluralize_pages(K)`
        where K = M - N + 1 (any of ASCII hyphen, LaTeX `--`,
        en-dash, em-dash). Single-page ranges like "1604-1604" yield
        "1 page" (singular), not "1 pages".
      * Anything else (article numbers like "e12345", non-numeric forms)
        passes through unchanged.
    """
    if not raw:
        return ""
    # Scholar oddity: "{N}--pages" — emit via _pluralize_pages so a
    # `1--pages` input correctly produces "1 page".
    if raw.endswith("--pages"):
        prefix = raw[:-len("--pages")].strip()
        if prefix.isdigit():
            return _pluralize_pages(int(prefix))
        # Non-numeric prefix → fall through to passthrough.
    # Author already wrote "N page(s)" — trust it.
    if "page" in raw.lower():
        return raw
    # Range form: detect "N{dash}M" with any dash character and convert
    # to a page-count signal. The regex tolerates whitespace around the
    # dashes and accepts ASCII hyphen, LaTeX `--`, en-dash, em-dash.
    m = re.match(r"^\s*(\d+)\s*[-–—]+\s*(\d+)\s*$", raw)
    if m:
        start, end = int(m.group(1)), int(m.group(2))
        if end >= start:
            return _pluralize_pages(end - start + 1)
    return raw


def format_details(entry: BibEntry) -> str:
    """', vol(issue), pages' suffix."""
    vol_issue = entry.get("volume", "")
    if "number" in entry:
        vol_issue += f"({entry['number']})"
    pages = _format_pages(entry.get("pages", ""))
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
    # Default: structural last author gets the `*` (corresponding) marker.
    # Override per `CORRESPONDING_AUTHORS[bib_key]` when the corresponding
    # author lives elsewhere in the author order (e.g. herbold2022fine —
    # 48 authors, Herbold (first) is corresponding, not Erbel (last)).
    bib_key = entry.get("ID", "")
    from .authors import parse_name_parts
    from .config import CORRESPONDING_AUTHORS
    override_last = CORRESPONDING_AUTHORS.get(bib_key, "").strip().lower()
    corresponding_idx = len(author_list) - 1
    if override_last:
        for i, a in enumerate(author_list):
            last_part, _ = parse_name_parts(decode_latex(a.strip()))
            if last_part.lower() == override_last:
                corresponding_idx = i
                break
    formatted_authors = [
        format_author(conn, a.strip(), is_last=i == corresponding_idx)
        for i, a in enumerate(author_list)
    ]
    authors_rtf = ", ".join(formatted_authors)
    link = resolve_link(conn, entry, category, title, raw_authors, acronym)

    # Append the venue acronym (`(JSS)`, `(ESEC/FSE)`, ...) to the cleaned
    # venue string for the rendered packet. Tenure reviewers identify
    # venues by their community-canonical nickname, so the parenthetical
    # is load-bearing. Skip when the venue already ends with a `)` (book
    # chapters carry publisher annotations like "Intermediate C
    # Programming (CRC Press)") or already contains the acronym
    # (preprints whose journal field is "arXiv preprint arXiv:NNNN").
    if (
        acronym
        and acronym.lower() not in venue_str.lower()
        and not venue_str.rstrip().endswith(")")
    ):
        venue_str = f"{venue_str} ({acronym})"

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


# ---------------------------------------------------------------------------
# @id cross-reference resolution
# ---------------------------------------------------------------------------
#
# YAML entries may carry an optional `id` field (string, [a-zA-Z][a-zA-Z0-9_-]*).
# Free-form text fields in any other entry can write `@id` to embed a
# back-pointer; the build phase computes each entry's final `C.X.Y` code,
# then substitutes every `@id` in registered prose fields with that code.
#
# Escape: `@@` → literal `@` (rare, mostly relevant for emails in prose).
#
# Email-safe: the regex requires `@` to NOT be preceded by a word char, so
# `email@example.com` is left untouched.

# `@id` with an optional `^SECTION` suffix that disambiguates papers that
# appear in MULTIPLE sections. Examples:
#   @davis2024impact          → default resolution (paper's main section, C.4)
#   @davis2024impact^C.1      → force resolution to the C.1 (Key Works) entry
# Section override grammar: a capital letter optionally followed by `.N`
# (so `C`, `C.1`, `C.5`, `V.A` all match). Group 2 is None when no override.
# Caret separator chosen so the suffix can never collide with the
# bibkey-allowed `[a-zA-Z0-9_-]` alphabet.
_REF_PATTERN = re.compile(
    r"(?<![\w])@([a-zA-Z][a-zA-Z0-9_-]*)(?:\^([A-Z](?:\.\d+)?))?"
)
# Raw section-code syntax: `@C.16.2.1`, `@A.1`, `@C.10`, etc. — matches a
# capital letter, dot, then one-or-more numeric segments. Disjoint from
# `_REF_PATTERN` (which disallows dots) and from `@@` escape (different
# prefix). Used for cross-refs to sections whose backing YAML entry doesn't
# exist yet (the bookmark target may not be present, in which case the
# click falls through — same trade-off as cross-refs into table sections).
_RAW_CODE_PATTERN = re.compile(r"(?<![\w])@([A-Z]\.\d+(?:\.\d+)*)")
_REF_ESCAPE_SENTINEL = "\x00__AT_AT_ESCAPE__\x00"

# When `link_format=True`, resolved refs emit `\x01CODE\x02` sentinels
# instead of plain CODE. The sentinels survive `escape_rtf` unchanged
# (both are <0x80 and not in the escape set) and are converted to RTF
# hyperlink markup by `rtf._finalize_ref_hyperlinks` after write.
REF_LINK_OPEN = "\x01"
REF_LINK_CLOSE = "\x02"

# Per NamedTuple type-name → tuple of prose-field-names that should be
# scanned for @id refs. New free-form fields default to NOT resolving; add
# them here when you want the substitution to fire.
PROSE_FIELDS_BY_TYPE: dict[str, tuple[str, ...]] = {
    "Grant": ("title", "activities", "responsibility"),
    "InvitedTalk": ("topic", "subtitle", "venue"),
    "LeadershipRole": ("description", "society"),
    "MediaAppearance": ("title", "venue"),
    "KeyWork": ("impact",),
    "StudentAward": ("recipient", "award"),
    "SoftwareProduct": ("name", "description"),
    "EntrepreneurialActivity": ("summary", "description"),
    "TechnologyTransfer": (
        "code_standard", "change_subject", "reason",
        "research_supporting", "impact",
    ),
    "CourseDevelopment": ("summary", "description"),
    "CourseTaught": ("title", "responsibility"),
    "UnderReview": ("title", "venue"),
    "Award": ("name", "significance"),
    "ServiceEntry": ("description",),
    "PostdocVisiting": ("position_title_dates", "current_position"),
    "Student": ("position",),
}


def resolve_refs(
    text: str, ref_index: dict[str, str], *,
    link_format: bool = False,
    section_bibkey_index: Optional[dict[str, dict[str, str]]] = None,
) -> tuple[str, list[str]]:
    """Substitute `@id` tokens in `text` with the resolved C.X.Y code.

    Returns `(substituted_text, unresolved_ids)`. Caller checks for non-empty
    unresolved list and decides whether to error.

    Two ref syntaxes:

      * `@id` — default resolution via `ref_index`. For papers, this
        targets the main bucket (C.4 conferences, C.5 other pubs, etc.).
      * `@id^SECTION` — override resolution. Looks `id` up in
        `section_bibkey_index[id][SECTION]` to target a non-default
        location. The canonical case: a paper that appears in BOTH
        C.1 Key Works AND C.4 Conferences — `@bibkey^C.1` reaches the
        C.1 entry; bare `@bibkey` continues to mean the C.4 entry.
        SECTION matches `C`, `C.1`, `C.5`, `V.A`, etc.

      * `@@` → literal `@`. Word-char lookbehind keeps emails
        (`x@y.com`) safe.

    `link_format=True` wraps each substituted code with the sentinel pair
    `\\x01CODE\\x02` so the downstream RTF writer can convert it into a
    clickable hyperlink. Sentinels are <0x80 and survive `escape_rtf` and
    `rtf_escape_unicode` unchanged. Default `False` keeps the bare-text
    substitution useful for testing and any non-RTF consumer.

    `section_bibkey_index` is optional — when None (or empty), every
    `^SECTION` override is treated as unresolved. Callers that want
    override support build the structured map alongside `ref_index`
    in their prep phase (see cli.py).
    """
    if not text or "@" not in text:
        return text, []
    # Protect literal `@@` sequences.
    protected = text.replace("@@", _REF_ESCAPE_SENTINEL)
    unresolved: list[str] = []
    section_idx = section_bibkey_index or {}

    def _sub_raw_code(match: "re.Match[str]") -> str:
        # Raw section code — no lookup needed; bookmark may not exist.
        code = match.group(1)
        if link_format:
            return f"{REF_LINK_OPEN}{code}{REF_LINK_CLOSE}"
        return code

    def _sub(match: "re.Match[str]") -> str:
        ref_id = match.group(1)
        section_override = match.group(2)  # None when no `^SECTION` tail
        # Section-override resolution: must land in the structured index.
        # Falling back to the flat ref_index would silently mask a typo
        # in either the bibkey or the section name; the user asked for
        # a specific section, so honor that or report unresolved.
        if section_override is not None:
            per_section = section_idx.get(ref_id) or {}
            code = per_section.get(section_override)
            if code is not None:
                if link_format:
                    return f"{REF_LINK_OPEN}{code}{REF_LINK_CLOSE}"
                return code
            unresolved.append(f"{ref_id}^{section_override}")
            return match.group(0)
        # Default resolution path (no override).
        if ref_id in ref_index:
            code = ref_index[ref_id]
            if link_format:
                return f"{REF_LINK_OPEN}{code}{REF_LINK_CLOSE}"
            return code
        unresolved.append(ref_id)
        return match.group(0)  # leave as-is so it's visible in error report

    # Raw section codes first so `@C.16.2.1` doesn't get partially matched
    # as `@C` by the id pattern (which disallows dots).
    substituted = _RAW_CODE_PATTERN.sub(_sub_raw_code, protected)
    substituted = _REF_PATTERN.sub(_sub, substituted)
    # Restore literal `@`.
    substituted = substituted.replace(_REF_ESCAPE_SENTINEL, "@")
    return substituted, unresolved


def resolve_refs_in_list(
    items: list, type_name: str, ref_index: dict[str, str],
    *, link_format: bool = False,
    section_bibkey_index: Optional[dict[str, dict[str, str]]] = None,
) -> tuple[list, list[tuple[int, str]]]:
    """Apply `resolve_refs` to each item's prose fields per
    `PROSE_FIELDS_BY_TYPE[type_name]`. Returns `(new_items, errors)` where
    `errors` is `[(item_index, unresolved_id), ...]`.

    `section_bibkey_index` is passed through to `resolve_refs` to
    support `@bibkey^SECTION` overrides; see that function's docstring
    for the resolution rules.

    NamedTuple `_replace()` is used so the originals aren't mutated and
    the substitution is purely a transformation step.
    """
    fields = PROSE_FIELDS_BY_TYPE.get(type_name, ())
    if not fields:
        return items, []
    new_items = []
    errors: list[tuple[int, str]] = []
    for i, item in enumerate(items):
        updates: dict[str, str] = {}
        for f in fields:
            current = getattr(item, f, None)
            if not isinstance(current, str):
                continue
            new_text, unresolved = resolve_refs(
                current, ref_index, link_format=link_format,
                section_bibkey_index=section_bibkey_index,
            )
            for u in unresolved:
                errors.append((i, u))
            if new_text != current:
                updates[f] = new_text
        new_items.append(item._replace(**updates) if updates else item)
    return new_items, errors


def build_invited_talk(talk: dict) -> InvitedTalk:
    """Build an InvitedTalk record from a YAML dict. No bib lookup needed."""
    year_str = str(talk.get("year", ""))
    return InvitedTalk(
        year=parse_year(year_str),
        year_str=year_str,
        topic=decode_latex(talk.get("topic", "")).replace("\n", " "),
        subtitle=decode_latex(talk.get("subtitle", "")).replace("\n", " "),
        venue=decode_latex(talk.get("venue", "")).replace("\n", " "),
        id=str(talk.get("id", "") or ""),
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
        # `status:` defaults to "awarded" (back-compat: every existing
        # YAML entry omits the field and stays in C.10-C.13). Setting
        # `status: pending` routes the entry to Section V, A.2 instead.
        status=str(entry.get("status", "awarded") or "awarded"),
        id=str(entry.get("id", "") or ""),
    )


def build_student(entry: dict) -> Student:
    """Build a Student record from a YAML dict (used for both C.14 and C.16).

    `aliases:` is an OPTIONAL list of alternate names used in the bib /
    A.1 author lists when the student appears under a different last
    name (accent fold, hyphenation drop, or short form). LaTeX-decoded
    for accent normalization, then stored as a tuple on the record.
    """
    grad_year = int(entry.get("grad_year", 9999) or 9999)
    aliases = tuple(
        decode_latex(str(a)).replace("\n", " ")
        for a in (entry.get("aliases") or [])
    )
    return Student(
        grad_year=grad_year,
        grad_display=str(entry.get("graduation", "") or ""),
        name=decode_latex(entry.get("name", "")).replace("\n", " "),
        degree=entry.get("degree", "") or "",
        role=entry.get("role", "") or "",
        position=decode_latex(entry.get("position", "") or "").replace("\n", " "),
        co_advisor=decode_latex(entry.get("co_advisor", "") or "").replace("\n", " "),
        id=str(entry.get("id", "") or ""),
        aliases=aliases,
        linkedin=str(entry.get("linkedin", "") or "").strip(),
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
        # Persist the bib-form author tuple so the student-table linker
        # can match A.1 entries the same way it matches C.X bib entries.
        raw_authors=tuple(a.strip() for a in author_list),
        id=str(entry.get("id", "") or ""),
        submission_year=int(entry.get("submission_year", 0) or 0),
        # Q7 — repo-relative path to a PNG/JPEG screenshot of the
        # submission confirmation. Empty when no supporting doc.
        supporting_image=str(entry.get("supporting_image", "") or ""),
    )


_UNDERGRAD_PRODUCT_LABELS: dict[Section, str] = {
    "Journals": "Paper",
    "Books and Chapters": "Book chapter",
    "Conferences and Workshops": "Paper",
    "Other publications and products": "Paper",
}


def build_undergrad_pathway(entry: dict) -> UndergradPathway:
    """Build a C.16.2.2 row from a YAML dict. All four fields are
    required (renderer doesn't tolerate missing columns — every row
    needs to fill the 4-column grid)."""
    return UndergradPathway(
        dates=decode_latex(entry.get("dates", "")).replace("\n", " "),
        activity=decode_latex(entry.get("activity", "")).replace("\n", " "),
        audience=decode_latex(entry.get("audience", "")).replace("\n", " "),
        participation=decode_latex(entry.get("participation", "")).replace("\n", " "),
    )


def build_undergrad_products(
    conn: sqlite3.Connection,
    publications: Publications,
    paper_index: dict[str, str],
    bib_entries: list[BibEntry],
    under_review: "list[UnderReview] | tuple[UnderReview, ...]" = (),
) -> list[UndergradProduct]:
    """Auto-derive C.16.2.3 records by scanning every emitted citation's bib
    author list for undergrad coauthors. Section V A.1 under-review entries
    are scanned the same way and surface in the same list, tagged with
    `is_under_review=True` so the renderer can append a disambiguator.

    A bib entry contributes a record iff (a) at least one author resolves to
    student-type `"U"` via `lookup_student_type` AND (b) the citation is
    present in `paper_index` (i.e. it was actually rendered — held-internal
    theses are excluded). Lead-is-undergrad is True iff the FIRST bib author
    is an undergrad. Under-review entries use their `raw_authors` tuple
    directly (already pre-split at YAML-build time) and their
    `submission_year` as the sort key — mixes naturally with published-paper
    years under the `-year` ordering. Entries with `submission_year == 0`
    sort to the bottom.

    Order: year DESCENDING, then by `ref` ASC for deterministic tie-break.
    """
    title_to_bib: dict[str, BibEntry] = {
        normalize_title(e.get("title", "") or ""): e
        for e in bib_entries
        if e.get("title")
    }
    products: list[UndergradProduct] = []
    for section, label in _UNDERGRAD_PRODUCT_LABELS.items():
        for cit in publications.get(section, []):
            key = normalize_title(cit.title)
            bib = title_to_bib.get(key)
            if not bib:
                continue
            raw_authors = bib.get("author", "") or ""
            author_list = [a.strip() for a in raw_authors.split(" and ") if a.strip()]
            n_under = 0
            lead_is_under = False
            for i, raw in enumerate(author_list):
                if lookup_student_type(conn, raw) == "U":
                    n_under += 1
                    if i == 0:
                        lead_is_under = True
            if n_under == 0:
                continue
            ref = paper_index.get(key)
            if not ref:
                continue
            products.append(UndergradProduct(
                year=cit.year,
                product_label=label,
                ref=ref,
                n_coauthors=n_under,
                lead_is_undergrad=lead_is_under,
            ))

    # Section V A.1 (under-review) — pipe-form "A.1.N|V.A.1.N" keeps the
    # visible code compact ("Paper A.1.7 ...") while the hyperlink targets
    # the namespaced bookmark `V_A_1_N` placed by render_under_review_
    # section. The `V.` namespace prevents collision with Section III's
    # A.1 Identifiers entries (also numbered A.1.N). The "(Under review.)"
    # disambiguator appended by the renderer keeps the visible code
    # legible without spelling out "Section V, " on every C.16.2.3 row.
    ur_code = SECTION_CODES["Under Review"]
    for idx, ur in enumerate(under_review, 1):
        n_under = 0
        lead_is_under = False
        for i, raw in enumerate(ur.raw_authors):
            if lookup_student_type(conn, raw) == "U":
                n_under += 1
                if i == 0:
                    lead_is_under = True
        if n_under == 0:
            continue
        bare = f"{ur_code}.{idx}"
        products.append(UndergradProduct(
            year=ur.submission_year,
            product_label="Paper",
            ref=f"{bare}|V.{bare}",
            n_coauthors=n_under,
            lead_is_undergrad=lead_is_under,
            is_under_review=True,
        ))

    # Sort key: published-before-under-review (False sorts before True),
    # then year ASC, then ref ASC. Under-review entries sit at the BOTTOM
    # of the whole C.16.2.3 list — regardless of their submission year —
    # because the reviewer's eye should land on the candidate's out-the-
    # door work first, with in-flight submissions as a trailing appendix.
    # Within each group, oldest-first matches every other dated section
    # in the packet (C.1 / C.2 / C.4 / C.5 / C.10 / C.11 / C.14 / C.17).
    products.sort(key=lambda p: (p.is_under_review, p.year, p.ref))
    return products


_SEMESTER_ORDER_MAP: dict[str, int] = {
    "Sp": 1, "Spring": 1,
    "Su": 2, "Summer": 2,
    "F": 3, "Fall": 3,
}


def _infer_semester_order(semester_str: str) -> int:
    """Best-effort: "F20" → 3, "Sp21" → 1, "Su22" → 2. Returns 0 on miss
    (caller is expected to provide explicit `semester_order` in that case).
    """
    for prefix, order in _SEMESTER_ORDER_MAP.items():
        if semester_str.startswith(prefix):
            return order
    return 0


def _maybe_int(v: object) -> Optional[int]:
    if v in (None, "", "—", "-"):
        return None
    if isinstance(v, (int, str)):
        return int(v)
    if isinstance(v, float):
        return int(v)
    raise TypeError(f"_maybe_int cannot coerce {type(v).__name__}: {v!r}")


def _maybe_float(v: object) -> Optional[float]:
    if v in (None, "", "—", "-"):
        return None
    if isinstance(v, (int, float, str)):
        return float(v)
    raise TypeError(f"_maybe_float cannot coerce {type(v).__name__}: {v!r}")


def build_course_taught(entry: dict) -> CourseTaught:
    """Build a C.17 row from a YAML dict.

    `year` + `semester_str` + `title` + `course_number` + `responsibility`
    are required; `is_new_course` defaults to False; CIE fields are all
    optional (missing → rendered as "—").

    `semester_order` is inferred from the `semester_str` prefix
    ("Sp"/"Su"/"F") when not given; provide it explicitly only if the
    string doesn't follow that convention.
    """
    semester_str = str(entry.get("semester_str", "") or "")
    semester_order = int(
        entry.get("semester_order")
        or _infer_semester_order(semester_str)
        or 0
    )
    return CourseTaught(
        year=int(entry.get("year", 0) or 0),
        semester_order=semester_order,
        semester_str=semester_str,
        title=decode_latex(entry.get("title", "")).replace("\n", " "),
        is_new_course=bool(entry.get("is_new_course", False)),
        course_number=decode_latex(entry.get("course_number", "") or "").replace("\n", " "),
        responsibility=decode_latex(entry.get("responsibility", "") or "").replace("\n", " "),
        responses=_maybe_int(entry.get("responses")),
        enrolled=_maybe_int(entry.get("enrolled")),
        cie_average=_maybe_float(entry.get("cie_average")),
        cie_min=_maybe_float(entry.get("cie_min")),
        cie_max=_maybe_float(entry.get("cie_max")),
        cie_partial=bool(entry.get("cie_partial", False)),
        is_note_row=bool(entry.get("is_note_row", False)),
        id=str(entry.get("id", "") or ""),
    )


def build_course_development(entry: dict) -> CourseDevelopment:
    """Build a C.18 entry from a YAML dict.

    Both fields free-form prose. Same shape as `build_entrepreneurial_activity`
    (summary + description); kept as a separate function for type clarity
    and so future divergence (e.g., a `date` field on courses) doesn't have
    to ripple through both call sites.
    """
    return CourseDevelopment(
        summary=decode_latex(entry.get("summary", "")).replace("\n", " "),
        description=decode_latex(entry.get("description", "")).replace("\n", " "),
        id=str(entry.get("id", "") or ""),
    )


def build_entrepreneurial_activity(entry: dict) -> EntrepreneurialActivity:
    """Build a C.20 entry from a YAML dict.

    Both fields are free-form prose. Empty list is the canonical pre-promotion
    state (renderer emits "N/A" under the heading).
    """
    return EntrepreneurialActivity(
        summary=decode_latex(entry.get("summary", "")).replace("\n", " "),
        description=decode_latex(entry.get("description", "")).replace("\n", " "),
        id=str(entry.get("id", "") or ""),
    )


def build_technology_transfer(entry: dict) -> TechnologyTransfer:
    """Build a C.21 entry from a YAML dict.

    `cited_publications` is a list of bib titles; the renderer resolves each
    to a `C.X.Y` ref via `paper_index`. Validation in `validate_non_scholar`
    enforces that every cited title resolves.
    """
    cited_raw = entry.get("cited_publications") or []
    cited = [str(c) for c in cited_raw]
    return TechnologyTransfer(
        code_standard=decode_latex(entry.get("code_standard", "")).replace("\n", " "),
        change_subject=decode_latex(entry.get("change_subject", "")).replace("\n", " "),
        reason=decode_latex(entry.get("reason", "")).replace("\n", " "),
        research_supporting=decode_latex(entry.get("research_supporting", "")).replace("\n", " "),
        cited_publications=cited,
        impact=decode_latex(entry.get("impact", "")).replace("\n", " "),
        id=str(entry.get("id", "") or ""),
    )


def build_student_award(entry: dict) -> StudentAward:
    """Build a StudentAward from a YAML dict (C.16.2.4 / C.16.3.3)."""
    year_raw = entry.get("year", "")
    year_str = str(entry.get("year_str", "") or year_raw or "")
    level_raw = str(entry.get("level", "") or "").strip().upper()
    if level_raw not in ("U", "G"):
        raise ValueError(
            f"StudentAward level must be 'U' or 'G' (got {level_raw!r}); "
            f"validate_non_scholar enforces this upstream — should not reach here"
        )
    level: Literal["U", "G"] = level_raw  # type: ignore[assignment]
    return StudentAward(
        year=parse_year(year_str) if year_str else 9999,
        year_str=year_str,
        level=level,
        tier=str(entry.get("tier", "") or ""),
        recipient=decode_latex(entry.get("recipient", "")).replace("\n", " "),
        award=decode_latex(entry.get("award", "")).replace("\n", " "),
        id=str(entry.get("id", "") or ""),
    )


def build_software_product(entry: dict) -> SoftwareProduct:
    """Build a SoftwareProduct from a YAML dict (C.22)."""
    year_raw = entry.get("year", "")
    year_str = str(entry.get("year_str", "") or year_raw or "")
    return SoftwareProduct(
        year=parse_year(year_str) if year_str else 9999,
        year_str=year_str,
        name=decode_latex(entry.get("name", "")).replace("\n", " "),
        description=decode_latex(entry.get("description", "") or "").replace("\n", " "),
        id=str(entry.get("id", "") or ""),
    )


def expand_venue_acronyms(text: str, registry: dict[str, str]) -> str:
    """Expand the FIRST occurrence of each registry acronym in `text` to
    `"{full_name} ({acronym})"`. Subsequent occurrences in the same
    string keep the bare acronym (rare in practice; most service entries
    name the venue once).

    Word-boundary semantics: the acronym must not be preceded or followed
    by another acronym-like character (alphanumeric, underscore, slash,
    hyphen). This lets compound acronyms ("ESEC/FSE") match before
    substrings ("FSE"), and keeps acronyms inside hyphenated track names
    ("ESEC/FSE-Artifact") expanding cleanly to
    "{full ESEC/FSE name} (ESEC/FSE)-Artifact". Sort longest first so
    "ESEC/FSE" is tested before "FSE".
    """
    if not text or not registry:
        return text
    for acronym in sorted(registry, key=len, reverse=True):
        full_name = registry[acronym]
        pattern = re.compile(
            r"(?<![\w/\-])" + re.escape(acronym) + r"(?![\w/])"
        )
        # count=1 → expand FIRST occurrence only.
        text, n = pattern.subn(f"{full_name} ({acronym})", text, count=1)
    return text


def build_service_entry(entry: dict) -> ServiceEntry:
    """Build a ServiceEntry from a YAML dict (C.23 / C.24 / C.25 / C.26).

    `year` field may be int (2025) or string ("2025, 2026, 2027" /
    "2024-2025" / "2023-present"). Empty / missing → year_str="", which
    suppresses the trailing year on render (used for journal reviewing).

    Description goes through `expand_venue_acronyms` so YAML stays
    compact ("Member of Program Committee, ICSE") and the rendered
    output reads ("Member of Program Committee, {full ICSE name}
    (ICSE)").
    """
    from .config import VENUE_FULL_NAMES
    year_raw = entry.get("year", "")
    year_str = str(year_raw) if year_raw not in (None, "") else ""
    # `year_display:` (OPTIONAL) overrides what's shown after the
    # description — useful when the description already embeds the year
    # inline (e.g., "Sub-reviewer: ASPLOS (2018), EuroSys (2018)") and a
    # trailing "2018." would read redundantly. The `year:` field still
    # drives the chronological sort. Setting `year_display: ""`
    # suppresses the trailing year entirely.
    if "year_display" in entry:
        display = entry.get("year_display") or ""
        year_str_display = str(display)
    else:
        year_str_display = year_str
    # `show: false` suppresses the entry from rendering; default True.
    show_raw = entry.get("show", True)
    description = decode_latex(entry.get("description", "")).replace("\n", " ")
    description = expand_venue_acronyms(description, VENUE_FULL_NAMES)
    return ServiceEntry(
        year=parse_year(year_str) if year_str else 9999,
        year_str=year_str_display,
        description=description,
        id=str(entry.get("id", "") or ""),
        show=bool(show_raw),
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
        id=str(entry.get("id", "") or ""),
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
        id=str(role.get("id", "") or ""),
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


def load_candidate_information(path: Optional[str]) -> Optional[CandidateInformation]:
    """Load Section III front matter from YAML; return None if file missing.

    Schema is the `candidate_information:` mapping documented in
    `assets/candidate-information.yaml`. A missing path is NOT an error
    (skip emission of A.1-A.7); a missing FILE at the supplied path IS
    an error (user explicitly asked for it). Empty fields in the YAML
    default to "" / [] so callers can rely on the tuple shape.

    Validation errors (wrong types, missing required keys) are collected
    and surfaced together via sys.exit(1) so the user sees the full
    list in one pass, matching `validate_non_scholar`'s pattern.
    """
    if not path:
        return None
    if not os.path.exists(path):
        log.warning(
            "Candidate-info YAML file not found at %s — skipping Section III "
            "front matter (A.1-A.7).", path,
        )
        return None
    log.info("Loading candidate-information from %s", path)
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        log.error(
            "Candidate-info YAML root must be a mapping; got %s",
            type(data).__name__,
        )
        sys.exit(1)
    raw = data.get("candidate_information")
    if raw is None:
        log.error(
            "Candidate-info YAML must contain a `candidate_information:` "
            "top-level key. See assets/candidate-information.yaml for shape.",
        )
        sys.exit(1)
    if not isinstance(raw, dict):
        log.error(
            "`candidate_information:` must be a mapping; got %s",
            type(raw).__name__,
        )
        sys.exit(1)
    return build_candidate_information(raw)


def build_candidate_information(raw: dict) -> CandidateInformation:
    """Convert the validated raw YAML mapping into a typed CandidateInformation.

    Collects ALL schema violations before exiting so the user sees the full
    error list in one pass. Empty / missing sub-sections default to
    sensible empties: empty string for prose, empty list for entry-lists,
    empty Identifiers for A.1 if `identifiers:` is missing entirely.
    """
    errors: list[str] = []

    def _str(d: dict, key: str, *, default: str = "") -> str:
        v = d.get(key, default)
        if v is None:
            return default
        if not isinstance(v, str):
            errors.append(
                f"candidate_information.{key}: must be a string; "
                f"got {type(v).__name__}",
            )
            return default
        return v

    ident_raw = raw.get("identifiers") or {}
    if not isinstance(ident_raw, dict):
        errors.append(
            f"candidate_information.identifiers: must be a mapping; "
            f"got {type(ident_raw).__name__}",
        )
        ident_raw = {}
    identifiers = Identifiers(
        name=_str(ident_raw, "name"),
        orcid=_str(ident_raw, "orcid"),
        google_scholar=_str(ident_raw, "google_scholar"),
    )

    def _list(key: str) -> list:
        v = raw.get(key) or []
        if not isinstance(v, list):
            errors.append(
                f"candidate_information.{key}: must be a list; "
                f"got {type(v).__name__}",
            )
            return []
        return v

    degrees: list[Degree] = []
    for i, d in enumerate(_list("degrees")):
        if not isinstance(d, dict):
            errors.append(
                f"candidate_information.degrees[{i}]: must be a mapping; "
                f"got {type(d).__name__}",
            )
            continue
        degrees.append(Degree(
            institution=_str(d, "institution"),
            years=_str(d, "years"),
            degree=_str(d, "degree"),
            thesis_kind=_str(d, "thesis_kind"),
            thesis_title=_str(d, "thesis_title"),
            advisor=_str(d, "advisor"),
        ))

    other_positions: list[OtherPosition] = []
    for i, p in enumerate(_list("positions_at_other")):
        if not isinstance(p, dict):
            errors.append(
                f"candidate_information.positions_at_other[{i}]: must be a "
                f"mapping; got {type(p).__name__}",
            )
            continue
        other_positions.append(OtherPosition(
            title=_str(p, "title"),
            years=_str(p, "years"),
            organization=_str(p, "organization"),
            acronym=_str(p, "acronym"),
        ))

    awards: list[Award] = []
    for i, a in enumerate(_list("awards")):
        if not isinstance(a, dict):
            errors.append(
                f"candidate_information.awards[{i}]: must be a mapping; "
                f"got {type(a).__name__}",
            )
            continue
        tier_raw = _str(a, "tier")
        if tier_raw not in ("external", "internal"):
            errors.append(
                f"candidate_information.awards[{i}].tier: must be "
                f"'external' or 'internal'; got {tier_raw!r}",
            )
            continue
        year_raw = a.get("year")
        try:
            year_int = int(year_raw) if year_raw is not None else 9999
        except (TypeError, ValueError):
            errors.append(
                f"candidate_information.awards[{i}].year: must be an int "
                f"or coercible; got {year_raw!r}",
            )
            continue
        # year_str optional — default to str(year) for display.
        year_str = _str(a, "year_str") or str(year_int)
        awards.append(Award(
            year=year_int,
            year_str=year_str,
            tier=tier_raw,  # type: ignore[arg-type]
            name=_str(a, "name"),
            significance=_str(a, "significance"),
            id=_str(a, "id"),
        ))

    memberships: list[ProfessionalMembership] = []
    for i, m in enumerate(_list("professional_memberships")):
        if not isinstance(m, dict):
            errors.append(
                f"candidate_information.professional_memberships[{i}]: must "
                f"be a mapping; got {type(m).__name__}",
            )
            continue
        memberships.append(ProfessionalMembership(
            level=_str(m, "level"),
            organization=_str(m, "organization"),
            acronym=_str(m, "acronym"),
        ))

    # positions_at_purdue: accept either a plain string (legacy YAML
    # shape — convert to a single-element list at build time) or a YAML
    # list (recommended; promotions append a new entry).
    pap_raw = raw.get("positions_at_purdue", []) or []
    if isinstance(pap_raw, str):
        positions_at_purdue: list[str] = [pap_raw] if pap_raw.strip() else []
    elif isinstance(pap_raw, list):
        positions_at_purdue = [str(p) for p in pap_raw if str(p).strip()]
    else:
        errors.append(
            f"candidate_information.positions_at_purdue: must be a string "
            f"or list of strings; got {type(pap_raw).__name__}",
        )
        positions_at_purdue = []

    if errors:
        log.error("Candidate-info schema errors:")
        for e in errors:
            log.error("  · %s", e)
        sys.exit(1)

    return CandidateInformation(
        identifiers=identifiers,
        degrees=degrees,
        positions_at_purdue=positions_at_purdue,
        positions_at_other=other_positions,
        licenses=_str(raw, "licenses"),
        awards=awards,
        professional_memberships=memberships,
    )


# ----- B.1-B.5 word-count caps (Purdue template soft limits) --------------


# Purdue template's soft word caps for the B-section self-evaluation
# prose. None means "no cap." Over-cap is a build-time warning, not
# an error — drafts routinely run over before the polish round.
_B_WORD_CAPS: dict[str, Optional[int]] = {
    "b1": 1000,   # Summary of achievements
    "b2": 250,    # Impact of accomplishments
    "b3": 500,    # Vision
    "b4": None,   # Candidate comments on external events
    "b5": None,   # Professional COVID-19 Impact Statement
}


def _count_words(text: str) -> int:
    """Whitespace-split word count. `@ref` tokens count as one word each,
    same as a regular word — close enough for the soft cap warning."""
    return len(text.split())


# Match `## <CODE> …` where CODE is any dotted section code: a leading
# capital letter then one or more `.N` segments. Captures the dotted
# code so the loader returns one dict entry per section. Examples that
# match: `## A.1`, `## A.7`, `## C.5.4`, `## C.16.2.1`, `## D.7.4.2`.
_SECTION_PROSE_RE = re.compile(
    r"^##\s+([A-Z]\.\d+(?:\.\d+)*)\b[^\n]*$", re.MULTILINE,
)

# C-style `/* … */` editor-only comment blocks. Stripped from prose files
# (section-prose.md, self-evaluation.md) before parsing so the author can
# keep template prompts, authoring guidance, and reminders alongside the
# real prose without those notes leaking into the rendered packet.
# Multi-line via DOTALL; non-greedy so adjacent blocks don't merge.
_PROSE_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)


def _strip_prose_comments(text: str) -> str:
    """Strip `/* … */` editor-only comment blocks from a prose markdown
    source. Applied at load time so neither the section parser nor the
    rendered packet sees the commented content."""
    return _PROSE_COMMENT_RE.sub("", text)


def load_section_prose(path: Optional[str]) -> dict[str, list[str]]:
    """Parse `assets/section-prose.md` into a `{code: [paragraph, …]}` dict.

    Imitates `load_self_evaluation`'s shape: each `## <CODE> …` heading
    delimits a section; the body is the prose between this heading and
    the next. The intro block above the first `## <CODE>` heading is
    ignored (authoring guidance, not rendered).

    Within each section body, paragraphs are blank-line separated AND
    single internal newlines collapse to spaces (markdown soft-wrap
    convention — matches the B-section loader). Returns one list of
    paragraph strings per code.

    A missing path / missing file is NOT an error — every section
    without an entry just renders heading-only at emit time. The
    loader is permissive: codes not (yet) wired into the renderer are
    returned as-is so the file can carry prose for sections that haven't
    been hooked up.

    By convention, B.X self-evaluation prose lives in self-evaluation.md
    (separate loader with word-count caps + #macro substitution) — don't
    put B.X entries here.
    """
    if not path or not os.path.exists(path):
        return {}
    log.info("Loading section prose from %s", path)
    with open(path, "r", encoding="utf-8") as f:
        text = f.read()
    text = _strip_prose_comments(text)
    result: dict[str, list[str]] = {}
    matches = list(_SECTION_PROSE_RE.finditer(text))
    for i, m in enumerate(matches):
        code = m.group(1)
        body_start = m.end()
        body_end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[body_start:body_end].strip()
        # Split on blank-line gaps, then collapse internal soft-wrap.
        paragraphs = [p.strip() for p in re.split(r"\n{2,}", body) if p.strip()]
        flat = [re.sub(r"\s*\n\s*", " ", p) for p in paragraphs]
        result[code] = flat
    return result


def _warn_section_prose_word_counts(section_prose: dict[str, list[str]]) -> None:
    """Log a warning for each B.X section-prose entry that exceeds the
    Purdue template's soft word cap. Over-cap is a warning, not an
    error — polish passes routinely trim drafts back. Only B.1-B.5 have
    caps today; sections without an entry are skipped silently."""
    for key in ("b1", "b2", "b3", "b4", "b5"):
        cap = _B_WORD_CAPS.get(key)
        if cap is None:
            continue
        code = f"B.{key[1:]}"
        paragraphs = section_prose.get(code, [])
        text = "\n\n".join(paragraphs)
        n = _count_words(text)
        if n > cap:
            log.warning(
                "section-prose %s: %d words (cap: %d). "
                "Trim before final submission.",
                code, n, cap,
            )


# Per-column required + optional keys for a tabular-directive schema
# entry. Validated at load time so a malformed schema file fails fast
# during build, not at directive-emit time. See assets/table-schemas.yaml
# for the canonical shape + the design context in
# docs/design/markdown-master-outline-refactor.md §Q6.
_TABLE_SCHEMA_REQUIRED_TOP: tuple[str, ...] = ("source", "code_key", "columns")
_TABLE_SCHEMA_OPTIONAL_TOP: tuple[str, ...] = ("bookmark_column",)
_TABLE_SCHEMA_REQUIRED_COL: tuple[str, ...] = ("field", "header", "width")
_TABLE_SCHEMA_OPTIONAL_COL: tuple[str, ...] = ("escape",)
# Whitelist of supported escape policies. Add new ones here AND extend
# the dispatcher in `directives._make_tabular_directive` in the same
# commit.
_TABLE_SCHEMA_ESCAPE_POLICIES: tuple[str, ...] = ("escape_rtf", "raw")


def _validate_table_schema_entry(name: str, entry: object) -> dict:
    """Validate one top-level entry in `assets/table-schemas.yaml`.

    Returns the validated dict (typed view). Fails-loud (sys.exit(1))
    on any structural problem with the schema entry — a misconfigured
    schema file is a build-fatal error, not a render-time surprise.
    """
    if not isinstance(entry, dict):
        log.error(
            "table-schemas %s: must be a mapping; got %s",
            name, type(entry).__name__,
        )
        sys.exit(1)
    unknown_top = set(entry.keys()) - set(
        _TABLE_SCHEMA_REQUIRED_TOP + _TABLE_SCHEMA_OPTIONAL_TOP
    )
    if unknown_top:
        log.error(
            "table-schemas %s: unknown top-level key(s) %s; allowed: %s",
            name, sorted(unknown_top),
            sorted(_TABLE_SCHEMA_REQUIRED_TOP + _TABLE_SCHEMA_OPTIONAL_TOP),
        )
        sys.exit(1)
    for req in _TABLE_SCHEMA_REQUIRED_TOP:
        if req not in entry:
            log.error("table-schemas %s: missing required top-level key %r", name, req)
            sys.exit(1)
    if not isinstance(entry["columns"], list) or not entry["columns"]:
        log.error("table-schemas %s: 'columns' must be a non-empty list", name)
        sys.exit(1)
    bookmark_column = entry.get("bookmark_column")
    if bookmark_column is not None:
        if not isinstance(bookmark_column, int):
            log.error(
                "table-schemas %s: 'bookmark_column' must be an int; got %r",
                name, bookmark_column,
            )
            sys.exit(1)
        if not (0 <= bookmark_column < len(entry["columns"])):
            log.error(
                "table-schemas %s: 'bookmark_column' %d out of range "
                "(have %d columns)", name, bookmark_column, len(entry["columns"]),
            )
            sys.exit(1)
    for idx, col in enumerate(entry["columns"]):
        if not isinstance(col, dict):
            log.error(
                "table-schemas %s column %d: must be a mapping; got %s",
                name, idx, type(col).__name__,
            )
            sys.exit(1)
        unknown_col = set(col.keys()) - set(
            _TABLE_SCHEMA_REQUIRED_COL + _TABLE_SCHEMA_OPTIONAL_COL
        )
        if unknown_col:
            log.error(
                "table-schemas %s column %d: unknown key(s) %s; allowed: %s",
                name, idx, sorted(unknown_col),
                sorted(_TABLE_SCHEMA_REQUIRED_COL + _TABLE_SCHEMA_OPTIONAL_COL),
            )
            sys.exit(1)
        for req in _TABLE_SCHEMA_REQUIRED_COL:
            if req not in col:
                log.error(
                    "table-schemas %s column %d: missing required key %r",
                    name, idx, req,
                )
                sys.exit(1)
        if not isinstance(col["width"], int) or col["width"] <= 0:
            log.error(
                "table-schemas %s column %d: 'width' must be a positive int; got %r",
                name, idx, col["width"],
            )
            sys.exit(1)
        escape = col.get("escape", "escape_rtf")
        if escape not in _TABLE_SCHEMA_ESCAPE_POLICIES:
            log.error(
                "table-schemas %s column %d: 'escape' %r not in %s",
                name, idx, escape, list(_TABLE_SCHEMA_ESCAPE_POLICIES),
            )
            sys.exit(1)
    return entry


def load_table_schemas(path: Optional[str]) -> dict[str, dict]:
    """Load `assets/table-schemas.yaml` into a validated `{name: schema}` dict.

    Missing file is permissive (returns empty dict) — directive
    registration just won't add any tabular entries, and any
    `!FOO_TABLE!` directive referenced in the outline file will
    fail-loud at walker-emit time via the unknown-directive path.

    Malformed file is build-fatal: every entry runs through
    `_validate_table_schema_entry` before returning. The validation
    pass catches typos in field names, out-of-range bookmark_column
    indexes, unsupported escape policies, and missing required keys.

    Design: docs/design/markdown-master-outline-refactor.md §Q6.
    """
    if not path or not os.path.exists(path):
        return {}
    log.info("Loading table schemas from %s", path)
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        log.error(
            "table-schemas root must be a mapping; got %s", type(data).__name__,
        )
        sys.exit(1)
    return {name: _validate_table_schema_entry(name, entry) for name, entry in data.items()}


def load_outline(path: Optional[str]) -> str:
    """Load the markdown-master walker outline file (raw text).

    Missing file is permissive (returns empty string) — the walker
    then has nothing to emit, which is the correct Phase-1 / pre-
    enablement default.
    """
    if not path or not os.path.exists(path):
        return ""
    log.info("Loading walker outline from %s", path)
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


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

    for i, sp in enumerate(non_scholar.get("software_products") or []):
        if not isinstance(sp, dict):
            errors.append(
                f"`software_products[{i}]` must be a mapping; got {type(sp).__name__}"
            )
            continue
        for required in ("name", "description"):
            if not sp.get(required):
                errors.append(
                    f"software_products[{i}] missing required field `{required}`"
                )

    for i, ct in enumerate(non_scholar.get("courses_taught") or []):
        if not isinstance(ct, dict):
            errors.append(
                f"`courses_taught[{i}]` must be a mapping; "
                f"got {type(ct).__name__}"
            )
            continue
        # Note rows (grey-shaded "no course taught" placeholders) only
        # require year/semester_str/title — they don't carry course_number
        # or responsibility. Regular rows require course_number;
        # responsibility may be blank (filled at build time from the
        # `courses_responsibility_*` lookup, default fallback included).
        is_note = bool(ct.get("is_note_row", False))
        required_fields: tuple[str, ...] = ("year", "semester_str", "title")
        if not is_note:
            required_fields = required_fields + ("course_number",)
        for required in required_fields:
            if ct.get(required) in (None, ""):
                errors.append(
                    f"courses_taught[{i}] missing required field `{required}`"
                )

    for i, cd in enumerate(non_scholar.get("course_development") or []):
        if not isinstance(cd, dict):
            errors.append(
                f"`course_development[{i}]` must be a mapping; "
                f"got {type(cd).__name__}"
            )
            continue
        for required in ("summary", "description"):
            if not cd.get(required):
                errors.append(
                    f"course_development[{i}] missing required field "
                    f"`{required}`"
                )

    for i, ea in enumerate(non_scholar.get("entrepreneurial_activities") or []):
        if not isinstance(ea, dict):
            errors.append(
                f"`entrepreneurial_activities[{i}]` must be a mapping; "
                f"got {type(ea).__name__}"
            )
            continue
        for required in ("summary", "description"):
            if not ea.get(required):
                errors.append(
                    f"entrepreneurial_activities[{i}] missing required field "
                    f"`{required}`"
                )

    for i, tt in enumerate(non_scholar.get("technology_transfer") or []):
        if not isinstance(tt, dict):
            errors.append(
                f"`technology_transfer[{i}]` must be a mapping; "
                f"got {type(tt).__name__}"
            )
            continue
        for required in (
            "code_standard", "change_subject", "reason",
            "research_supporting", "impact",
        ):
            if not tt.get(required):
                errors.append(
                    f"technology_transfer[{i}] missing required field "
                    f"`{required}`"
                )
        # cited_publications is OPTIONAL but if present each title must
        # resolve against the bib (same pattern as grant inspired_by).
        for cited in (tt.get("cited_publications") or []):
            if normalize_title(str(cited)) not in bib_titles:
                errors.append(
                    f"technology_transfer[{i}] references cited_publications "
                    f"{cited!r}, which doesn't match any bib title"
                )

    for i, sa in enumerate(non_scholar.get("student_awards") or []):
        if not isinstance(sa, dict):
            errors.append(
                f"`student_awards[{i}]` must be a mapping; got {type(sa).__name__}"
            )
            continue
        for required in ("level", "tier", "recipient", "award", "year"):
            if not sa.get(required):
                errors.append(
                    f"student_awards[{i}] missing required field `{required}`"
                )
        # `level` must be exactly "U" or "G" — routes to C.16.2.4 vs C.16.3.3.
        level = str(sa.get("level", "") or "").strip().upper()
        if level and level not in ("U", "G"):
            errors.append(
                f"student_awards[{i}] has invalid `level={sa.get('level')!r}`; "
                f"must be 'U' (undergrad, C.16.2.4) or 'G' (grad, C.16.3.3)"
            )

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
        # `venue` is OPTIONAL: when empty, render_media_appearance
        # treats the entry as freeform-prose (title carries the full
        # sentence). Title + year remain required.
        for required in ("title", "year"):
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
