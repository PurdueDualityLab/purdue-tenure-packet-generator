#!/usr/bin/env python3
"""Generate a formatted RTF publication list from BibTeX.

Run: python3 pubs-emitter.py --bib my_papers.bib
Set LOG_LEVEL=DEBUG to see DOI cache hits per paper.
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import sqlite3
import sys
import urllib.parse
from collections import defaultdict
from typing import Literal, NamedTuple

import bibtexparser
import requests
from pylatexenc.latex2text import LatexNodes2Text

_LATEX_DECODER = LatexNodes2Text()


def decode_latex(s: str) -> str:
    """Convert LaTeX escapes (`{\\c{C}}` → `Ç`, `{\\'e}` → `é`, `{NLP}` → `NLP`) to Unicode."""
    return _LATEX_DECODER.latex_to_text(s)

# ==========================================
# CONFIGURATION
# ==========================================
DEFAULT_OUT_FILE = "publications.rtf"
DEFAULT_DB_FILE = "doi_cache.sqlite"

ME = ["Davis, James C", "Davis, J.", "Davis, James"]
ADVISORS = ["Lee, Dongyoon"]

StudentType = Literal["G", "U"]

STUDENTS: dict[StudentType, list[str]] = {
    "G": [  # PhDs, MScs, and Alumni (Grad)
        "Paschal C. Amusuo", "Dharun Anand", "Kelechi G. Kalu", "Purvish Jajal",
        "Nick Eliopoulos", "Berk Çakar", "Huiyun Peng", "Daniel Lugo", "Drew Rozema",
        "Sofia Okorafor", "Chinenye Okafor", "Tanmay Singla", "Parth V. Patil", "Ishgair",
        "Wenxin Jiang", "Taylor Schorlemmer", "Jason Jones", "William Maxam", "Trey Maxam",
        "Geoffrey Cramer", "Ricardo Calvo",
    ],
    "U": [  # Undergraduates and Alumni (Undergrad)
        "Charlie Sale", "Arav Tewari", "Taylor Le Lievre", "Nathaniel Bielanski",
        "Owen Cochell", "Ethan Burmane", "Sophie Chen", "Mohammed Ahmed",
        "Mohammed Sameh", "Mingyu Kim", "Heesoo Kim", "Zhongwei Xu",
        "Matthew Campbell", "Kyle Robinson", "Ananya Singh", "Evan Williams",
        "David Li", "Zach Ghera", "Allen Liu", "Feny Patel", "Efe Barlas",
        "Xin Du", "Diego Montes", "Naveen Vivek", "Anirudh Vegesana", "Vishnu Banna",
        "Jiashen Kuo", "Luke Chigges", "Kyung Ko", "Joseph Woo", "Anusha Sarraf",
        "Bhavesh Pareek", "Erik Kocinare",
    ],
}

# TODO: On item 43 from website, "Discrepancies".
# Once we're working on the full bib, add an assert that every student
# appears in at least one paper.

Category = Literal["Journals", "Conferences and Workshops", "arXiv / Preprints"]
Rank = str  # "Rank 1" | "Rank 2" | "Rank 3" | "Workshop" | "Magazine" | "Preprint"
Section = Literal["Journals", "Conferences and Workshops", "Magazines and Preprints"]

RANKS: dict[str, Rank] = {
    # Rank 1
    "EMSE": "Rank 1", "ICSE": "Rank 1", "FSE": "Rank 1", "ESEC/FSE": "Rank 1",
    "ASE": "Rank 1", "ISSTA": "Rank 1", "JSS": "Rank 1",
    "S&P": "Rank 1", "USENIX Security": "Rank 1", "CCS": "Rank 1", "NDSS": "Rank 1",
    "WWW": "Rank 1", "EuroSys": "Rank 1", "USENIX ATC": "Rank 1", "VLDB": "Rank 1",
    "AAAI": "Rank 1", "ICLR": "Rank 1", "WACV": "Rank 1", "CVPR": "Rank 1",
    "EJEE": "Rank 1",

    # Rank 2
    "SANER": "Rank 2", "MSR": "Rank 2", "ESEM": "Rank 2",
    "AsiaCCS": "Rank 2", "EuroS&P": "Rank 2",
    "ICSOC": "Rank 2", "ISLPED": "Rank 2", "CVPR-Findings": "Rank 2",
    "Frontiers": "Rank 2", "JIEE": "Rank 2",

    # Rank 3
    "ASEE": "Rank 3",

    # Workshops
    "SERP4IoT": "Workshop", "ASE-Demos": "Workshop", "MSR-MiningChallenge": "Workshop",
    "FSE-Artifacts": "Workshop", "JAWs": "Workshop", "EuroSec": "Workshop",
    "SCORED": "Workshop", "SIGMOD-Demos": "Workshop", "DSN-Disrupt": "Workshop",
    "LCTES-WIP": "Workshop", "ACIEE": "Workshop", "ICSE-DREE": "Workshop",

    # Magazines
    "IEEE Design&Test": "Magazine", "Computer": "Magazine",
}

SECTION_ORDER: list[Section] = [
    "Journals",
    "Conferences and Workshops",
    "Magazines and Preprints",
]

SECTION_CODES: dict[Section, str] = {
    "Journals": "C.2",
    "Conferences and Workshops": "C.4",
    "Magazines and Preprints": "C.5",
}

# Inline tier label appended at end of each citation.
TIER_LABELS: dict[Rank, str] = {
    "Rank 1": "Tier 1",
    "Rank 2": "Tier 2",
    "Rank 3": "Tier 3",
    "Workshop": "Workshop",
    "Magazine": "Magazine",
    "Preprint": "Preprint",
}

# Sponsoring orgs: spell out on first occurrence per section, bare acronym after.
ORG_EXPANSIONS: dict[str, str] = {
    "IEEE": "Institute of Electrical and Electronics Engineers (IEEE)",
    "ACM": "Association for Computing Machinery (ACM)",
    "USENIX": "USENIX Association (USENIX)",
}

# ==========================================
# LOGGING
# ==========================================
logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stderr,
)
log = logging.getLogger("pubs-emitter")


# DOI-lookup counters (mutated by fetch_doi_or_url + resolve_link; summarized in main).
DOI_STATS: dict[str, int] = {
    "cache_hits": 0,        # cache row found (whether the cached value was a DOI or empty)
    "cache_misses": 0,      # cache miss → Crossref/DBLP query
    "arxiv_constructed": 0, # arXiv entries: DOI built from arXiv ID, cache bypassed
}


# ==========================================
# TYPES
# ==========================================
class Citation(NamedTuple):
    section: Section
    rank: Rank
    year: int          # sort key; 9999 for "In Press" / unparseable
    year_str: str      # display value
    authors_rtf: str   # already carries RTF markup (\b, \super, ...)
    title: str         # raw text; escaped at render time
    venue: str         # raw cleaned text; expansion + escape at render time
    details: str       # ', vol(issue), pages' suffix; escaped at render time
    link: str          # full URL ("" if none)


Publications = dict[Section, list[Citation]]
BibEntry = dict[str, str]


# ==========================================
# DATABASE
# ==========================================
def open_db(path: str) -> sqlite3.Connection:
    """Open the DOI cache + create a fresh students table for this run."""
    conn = sqlite3.connect(path)
    cur = conn.cursor()
    cur.execute("CREATE TABLE IF NOT EXISTS doi_cache (title TEXT PRIMARY KEY, doi_or_url TEXT)")
    cur.execute("DROP TABLE IF EXISTS students")
    cur.execute("CREATE TABLE students (name TEXT PRIMARY KEY, type TEXT)")
    conn.commit()
    return conn


def populate_students(conn: sqlite3.Connection, students: dict[StudentType, list[str]]) -> None:
    """Insert every student name + auto-generated 'Last, First' reverse form."""
    cur = conn.cursor()
    for s_type, names in students.items():
        for name in names:
            cur.execute("INSERT OR IGNORE INTO students (name, type) VALUES (?, ?)", (name, s_type))
            if " " in name:
                parts = name.split()
                reversed_name = f"{parts[-1]}, {' '.join(parts[:-1])}"
                cur.execute(
                    "INSERT OR IGNORE INTO students (name, type) VALUES (?, ?)",
                    (reversed_name, s_type),
                )
    conn.commit()


# ==========================================
# AUTHOR CLASSIFICATION
# ==========================================
def name_matches(bib_name: str, person_list: list[str]) -> bool:
    return any(p.lower() in bib_name.lower() for p in person_list)


def lookup_student_type(conn: sqlite3.Connection, bib_name: str) -> str:
    """Return 'G' / 'U' if bib_name matches a known student, else ''."""
    cur = conn.cursor()
    cur.execute("SELECT name, type FROM students")
    for db_name, s_type in cur.fetchall():
        if db_name.lower() in bib_name.lower():
            return s_type
    return ""


def format_author(conn: sqlite3.Connection, bib_name: str, is_last: bool) -> str:
    """'Last, F.I.' format. Bold for me. Comma-joined role markers after the name."""
    bib_name = decode_latex(bib_name)
    if "," in bib_name:
        parts = [p.strip() for p in bib_name.split(",")]
        last = parts[0]
        firsts = parts[1].split() if len(parts) > 1 else []
    else:
        parts = bib_name.split()
        last = parts[-1]
        firsts = parts[:-1]

    initials = ".".join(f[0].upper() for f in firsts) + "." if firsts else ""
    formatted = f"{last}, {initials}" if initials else last

    if name_matches(bib_name, ME):
        formatted = f"\\b {formatted}\\b0"

    markers: list[str] = []
    student_type = lookup_student_type(conn, bib_name)
    if student_type:
        markers.append(student_type)
    if name_matches(bib_name, ADVISORS):
        markers.append("#")
    if is_last:
        markers.append("*")

    if markers:
        formatted += f"\\super {','.join(markers)}\\nosupersub"
    return formatted


# ==========================================
# DOI LOOKUP
# ==========================================
def try_crossref(title: str, authors: str) -> str:
    try:
        query = urllib.parse.quote(f"{title} {authors.split(' and ')[0]}")
        url = f"https://api.crossref.org/works?query={query}&select=DOI,title&rows=1"
        resp = requests.get(url, timeout=5).json()
        items = resp.get("message", {}).get("items", [])
        if items:
            return f"https://doi.org/{items[0]['DOI']}"
    except Exception as e:
        log.warning("Crossref query failed for '%s': %s", title[:60], e)
    return ""


def try_dblp(title: str) -> str:
    try:
        q_title = urllib.parse.quote(title)
        url = f"https://dblp.org/search/publ/api?q={q_title}&format=json&h=1"
        resp = requests.get(url, timeout=5).json()
        hits = resp.get("result", {}).get("hits", {}).get("hit", [])
        if hits:
            return hits[0]["info"].get("ee", "") or ""
    except Exception as e:
        log.warning("DBLP query failed for '%s': %s", title[:60], e)
    return ""


def fetch_doi_or_url(conn: sqlite3.Connection, title: str, authors: str) -> str:
    """Cache-first DOI lookup: Crossref, then DBLP, then empty string."""
    cur = conn.cursor()
    cur.execute("SELECT doi_or_url FROM doi_cache WHERE title=?", (title.lower(),))
    row = cur.fetchone()
    if row:
        DOI_STATS["cache_hits"] += 1
        log.debug("DOI cache hit: %s", title[:60])
        return row[0]

    DOI_STATS["cache_misses"] += 1
    log.info("Fetching DOI for: %s", title[:60])
    link = try_crossref(title, authors) or try_dblp(title)
    if not link:
        log.warning("No DOI/URL found for: %s", title[:60])
    cur.execute("INSERT INTO doi_cache VALUES (?, ?)", (title.lower(), link))
    conn.commit()
    return link


# ==========================================
# ARXIV ID EXTRACTION
# ==========================================
# Modern arXiv IDs: 2605.10712 (YYMM.NNNNN, 4 or 5 digit suffix)
# Legacy IDs: hep-ph/0501001 or math.GT/0501001
ARXIV_ID_RE = re.compile(r"\b(\d{4}\.\d{4,5}|[a-z\-]+(?:\.[A-Z]{2})?/\d{7})\b")


def extract_arxiv_id(entry: BibEntry) -> str | None:
    """Pull an arXiv ID out of the entry. Prefers `eprint`, falls back to text fields."""
    if "eprint" in entry:
        m = ARXIV_ID_RE.search(entry["eprint"])
        if m:
            return m.group(1)
    for field in ("journal", "booktitle", "url", "title"):
        m = ARXIV_ID_RE.search(entry.get(field, ""))
        if m:
            return m.group(1)
    return None


# ==========================================
# VENUE PARSING
# ==========================================
def parse_venue(venue_str: str) -> tuple[str | None, str]:
    """Pull '[ACRONYM'YY]' off the front. Returns (acronym, cleaned-venue)."""
    match = re.search(r"\[(.*?)\]", venue_str)
    if not match:
        return None, venue_str
    raw_acronym = match.group(1).strip()
    acronym = re.sub(r"[\'’`\s]+\d{2,4}$", "", raw_acronym).strip()
    cleaned = venue_str.replace(match.group(0), "").strip()
    cleaned = re.sub(r"^[,:\s]+", "", cleaned)
    return acronym, cleaned


def lookup_rank(acronym: str | None, title: str) -> Rank:
    """Resolve an acronym to its rank, or abort with a clear error."""
    if not acronym:
        log.error("Missing [ACRONYM] tag in venue field for paper: '%s'", title)
        sys.exit(1)
    for known, rank in RANKS.items():
        if acronym.upper() == known.upper():
            return rank
    log.error("Unranked venue '%s' for paper: '%s'", acronym, title)
    log.error("Add '%s' to the RANKS dictionary at the top of the script.", acronym)
    sys.exit(1)


# ==========================================
# CITATION BUILDING
# ==========================================
def escape_rtf(text: str) -> str:
    return text.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")


def classify_entry(entry: BibEntry) -> tuple[Category, str]:
    """Determine (category, raw-venue-string) for a BibTeX entry."""
    if "journal" in entry:
        venue = entry["journal"].replace("\n", " ")
        category: Category = "arXiv / Preprints" if "arxiv" in venue.lower() else "Journals"
        return category, venue
    if "booktitle" in entry:
        return "Conferences and Workshops", entry["booktitle"].replace("\n", " ")
    if "eprint" in entry:
        return "arXiv / Preprints", "arXiv"
    return "arXiv / Preprints", ""


def resolve_link(
    conn: sqlite3.Connection,
    entry: BibEntry,
    category: Category,
    title: str,
    raw_authors: str,
) -> str:
    """Pick the right link source.

    arXiv: construct the canonical arXiv DOI (`10.48550/arXiv.<id>`).
    Hard-fails if no ID can be parsed — every [arXiv'XX] entry must carry one.

    Everything else: cache-backed Crossref + DBLP lookup.
    """
    if category == "arXiv / Preprints":
        arxiv_id = extract_arxiv_id(entry)
        if not arxiv_id:
            log.error("arXiv entry missing arXiv ID: '%s'", title)
            log.error(
                "Add the ID via `eprint = {<id>}` or include `arXiv:<id>` in the journal field."
            )
            sys.exit(1)
        DOI_STATS["arxiv_constructed"] += 1
        return f"https://doi.org/10.48550/arXiv.{arxiv_id}"
    return fetch_doi_or_url(conn, title, raw_authors)


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
        return "Magazines and Preprints"
    if category == "Conferences and Workshops":
        return "Conferences and Workshops"
    return "Journals"


def build_citation(conn: sqlite3.Connection, entry: BibEntry) -> Citation:
    """Assemble a Citation. RTF assembly is deferred to render time."""
    title = decode_latex(entry.get("title", "")).replace("\n", " ")
    raw_authors = entry.get("author", "")
    year_str = entry.get("year", "In Press")

    category, venue_str = classify_entry(entry)
    venue_str = decode_latex(venue_str)
    acronym, venue_str = parse_venue(venue_str)
    rank: Rank = "Preprint" if category == "arXiv / Preprints" else lookup_rank(acronym, title)
    section = derive_section(category, rank)

    author_list = raw_authors.split(" and ")
    formatted_authors = [
        format_author(conn, a.strip(), is_last=(i == len(author_list) - 1))
        for i, a in enumerate(author_list)
    ]
    authors_rtf = ", ".join(formatted_authors)
    link = resolve_link(conn, entry, category, title, raw_authors)

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


# ==========================================
# RTF OUTPUT
# ==========================================
def apply_acronym_expansions(venue: str, done: set[str]) -> str:
    """Spell out an org acronym on its first occurrence in this section.

    Mutates `done` to record which acronyms have been expanded so subsequent
    citations in the same section keep them as bare acronyms.
    """
    for acronym, expansion in ORG_EXPANSIONS.items():
        if acronym in done:
            continue
        if re.search(rf"\b{acronym}\b", venue):
            venue = re.sub(rf"\b{acronym}\b", expansion, venue, count=1)
            done.add(acronym)
    return venue


def render_doi_field(link: str) -> str:
    """RTF for the hyperlink + visible 'DOI:<bare>' (or 'URL:<full>') prefix."""
    if not link:
        return ""
    if link.startswith("https://doi.org/"):
        prefix = "DOI:"
        display = link[len("https://doi.org/"):]
    else:
        prefix = "URL:"
        display = link
    return f' {prefix}{{\\field{{\\*\\fldinst HYPERLINK "{link}"}}{{\\fldrslt {display}}}}}'


def render_citation(cit: Citation, expansion_done: set[str]) -> str:
    """RTF body for one citation (no paragraph wrapping)."""
    venue = apply_acronym_expansions(cit.venue, expansion_done)
    body = (
        f"{cit.authors_rtf}, ({cit.year_str}). "
        f"{escape_rtf(cit.title)}, "
        f"\\i {escape_rtf(venue)}\\i0"
        f"{escape_rtf(cit.details)}."
    )
    body += render_doi_field(cit.link)
    body += f" \\i {TIER_LABELS[cit.rank]}\\i0."
    return body


def write_rtf(path: str, publications: Publications) -> None:
    log.info("Generating RTF file: %s", path)
    with open(path, "w", encoding="utf-8") as out:
        out.write(r"{\rtf1\ansi\ansicpg1252\deff0{\fonttbl{\f0\froman\fcharset0 Times New Roman;}}")
        out.write(r"{\colortbl;\red0\green0\blue255;}")

        for section in SECTION_ORDER:
            citations = publications.get(section, [])
            if not citations:
                continue
            code = SECTION_CODES[section]
            out.write(f"\\pard\\b\\fs28 {code} {section}\\b0\\fs24\\par\\par\n")

            expansion_done: set[str] = set()
            for idx, cit in enumerate(citations, 1):
                body = render_citation(cit, expansion_done)
                # Hanging indent: first line flush, continuation lines indented to 720 twips.
                out.write(f"\\pard\\li720\\fi-720 {code}.{idx}.\\tab {body}\\par\\par\n")
        out.write("}")
    log.info("Done. Output: %s", path)


# ==========================================
# MAIN
# ==========================================
def load_bib(path: str) -> list[BibEntry]:
    log.info("Loading BibTeX from %s", path)
    with open(path, "r", encoding="utf-8") as f:
        db = bibtexparser.load(f)
    log.info("Loaded %d entries", len(db.entries))
    return db.entries


def log_section_summary(publications: Publications) -> None:
    for section in SECTION_ORDER:
        cits = publications.get(section, [])
        if cits:
            log.info("Section %s '%s': %d papers", SECTION_CODES[section], section, len(cits))


def log_doi_stats() -> None:
    """Summarize DOI-lookup outcomes so the cache's effectiveness is visible."""
    hits = DOI_STATS["cache_hits"]
    misses = DOI_STATS["cache_misses"]
    arxiv = DOI_STATS["arxiv_constructed"]
    lookups = hits + misses
    if lookups:
        rate = 100.0 * hits / lookups
        log.info(
            "DOI cache: %d/%d hits (%.0f%%); %d network queries; %d arXiv constructed (cache bypassed)",
            hits, lookups, rate, misses, arxiv,
        )
    elif arxiv:
        log.info("DOI cache: 0 lookups; %d arXiv constructed (cache bypassed)", arxiv)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Generate a formatted RTF publication list from a BibTeX file. "
            "Output is suitable for pasting into Word with formatting preserved "
            "(bold for me, superscripts for student/advisor roles, hyperlinks to DOIs)."
        ),
        epilog=(
            "Environment:\n"
            "  LOG_LEVEL   Logging verbosity (DEBUG, INFO, WARNING, ERROR). Default: INFO.\n\n"
            "BibTeX convention:\n"
            "  Each `journal` / `booktitle` field must begin with a bracketed\n"
            "  acronym + year tag, e.g. `[ICSE'25] Proceedings of ...`. The acronym\n"
            "  must appear in the RANKS dictionary at the top of this script."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--bib",
        required=True,
        metavar="PATH",
        help="Path to the input BibTeX file (required).",
    )
    parser.add_argument(
        "--out",
        default=DEFAULT_OUT_FILE,
        metavar="PATH",
        help=f"Path to write the output RTF file. Default: {DEFAULT_OUT_FILE}",
    )
    parser.add_argument(
        "--cache",
        default=DEFAULT_DB_FILE,
        metavar="PATH",
        help=(
            f"Path to the SQLite DOI cache. Created if missing; safe to delete "
            f"to force re-lookup. Default: {DEFAULT_DB_FILE}"
        ),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    conn = open_db(args.cache)
    try:
        populate_students(conn, STUDENTS)
        entries = load_bib(args.bib)

        publications: Publications = defaultdict(list)
        for entry in entries:
            cit = build_citation(conn, entry)
            publications[cit.section].append(cit)

        # Chronological order (oldest first) within each section.
        for section in publications:
            publications[section].sort(key=lambda c: c.year)

        log_section_summary(publications)
        log_doi_stats()
        write_rtf(args.out, publications)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
