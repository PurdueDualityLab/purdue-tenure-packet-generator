#!/usr/bin/env python3
"""Generate a formatted RTF publication list from BibTeX.

Run: python3 pubs-emitter.py --bib my_papers.bib
Set LOG_LEVEL=DEBUG to see cache hits per entry.
Set PATENTSVIEW_API_KEY=<key> to enable USPTO issue-date lookup for patents.
Set NVD_API_KEY=<key> to raise the NIST NVD rate limit ~10x.

Architecture overview
---------------------
Pipeline runs in five phases (see main()):

  1. plan_lookups()   walks every entry, decides which network calls
                      would be needed (cache misses), emits NetworkTasks.
  2. dispatch_parallel()  runs all NetworkTasks concurrently
                          via ThreadPoolExecutor (I/O-bound).
  3. commit_results() writes the resolved values into the SQLite cache.
  4. build_*()        constructs Citation / Patent records. By now the
                      cache is warm so these calls are local-only.
  5. write_rtf()      emits the final RTF (citations + patent table).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sqlite3
import sys
import threading
import time
import urllib.parse
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Any, Callable, Literal, NamedTuple, Optional

import bibtexparser
import requests
import yaml
from pylatexenc.latex2text import LatexNodes2Text

_LATEX_DECODER = LatexNodes2Text()


def decode_latex(s: str) -> str:
    """Convert LaTeX escapes (`{\\c{C}}` → `Ç`, `{\\'e}` → `é`, `{NLP}` → `NLP`) to Unicode."""
    return _LATEX_DECODER.latex_to_text(s)


# ==========================================
# CONFIGURATION
# ==========================================
DEFAULT_OUT_FILE = "publications.rtf"
DEFAULT_DB_FILE = "lookup_cache.sqlite"
DEFAULT_MAX_WORKERS = 8

PATENTSVIEW_API_URL = "https://search.patentsview.org/api/v1/patent/"
NVD_API_URL = "https://services.nvd.nist.gov/rest/json/cves/2.0"

# User-Agent buys access to Crossref's "polite pool" (higher, more predictable
# rate limit) and is good citizenship across all the APIs we hit. The mailto
# is what Crossref uses to contact you if your traffic causes issues.
# Override with the PUBS_EMITTER_USER_AGENT env var.
USER_AGENT = os.environ.get(
    "PUBS_EMITTER_USER_AGENT",
    "pubs-emitter/1.0 (mailto:davisjam@purdue.edu)",
)
DEFAULT_HEADERS: dict[str, str] = {"User-Agent": USER_AGENT}

# ------------------------------------------
# Per-host rate limiting + retry-with-backoff.
#
# Why this matters: when N workers fan out to the same host concurrently,
# anonymous-pool APIs (Crossref, DBLP, NVD-without-key) throttle aggressively.
# A per-host RateLimiter serializes workers through a minimum-interval gate;
# polite_get() wraps that with exponential-backoff retry on transient failures
# (429, 5xx, connection reset). All four `try_*` fetchers go through it.
# ------------------------------------------
MAX_RETRIES = 2                      # i.e., 3 attempts total
BACKOFF_BASE_S = 1.0                 # 1s, 2s, 4s
TRANSIENT_HTTP_CODES: set[int] = {429, 500, 502, 503, 504}


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


# NVD without an API key allows 5 requests per rolling 30s (~6s between calls);
# with a key, 50 per 30s (~0.6s). Adjusted at import time based on env.
_NVD_INTERVAL_S = 0.7 if os.environ.get("NVD_API_KEY") else 6.5

_HOST_LIMITERS: dict[str, RateLimiter] = {
    "api.crossref.org": RateLimiter(0.1),         # ~10 req/s, well under polite-pool limit
    "dblp.org": RateLimiter(0.2),                 # ~5 req/s; DBLP throttles harder than Crossref
    "services.nvd.nist.gov": RateLimiter(_NVD_INTERVAL_S),
    "search.patentsview.org": RateLimiter(0.5),
}


def polite_get(host: str, url: str, **kwargs) -> requests.Response | None:
    """Rate-limited + backoff-retrying GET.

    Honors per-host RateLimiter spacing AND retries on transient failures
    (HTTP in TRANSIENT_HTTP_CODES, or ConnectionError/Timeout). Returns the
    final Response (even if status is bad — caller checks) or None if every
    attempt raised a transport-layer exception.

    Respects a server's `Retry-After` header when present on 429/503.
    """
    limiter = _HOST_LIMITERS.get(host)
    kwargs.setdefault("timeout", 10)
    last_resp: requests.Response | None = None
    for attempt in range(MAX_RETRIES + 1):
        if limiter:
            limiter.acquire()
        try:
            resp = requests.get(url, **kwargs)
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
        # Backoff. Honor Retry-After if the server sent one we can parse.
        wait = (2 ** attempt) * BACKOFF_BASE_S
        if last_resp is not None:
            ra = last_resp.headers.get("Retry-After", "")
            if ra.isdigit():
                wait = max(wait, float(ra))
        time.sleep(wait)
    return last_resp


# ------------------------------------------
# DOI-lookup matching helpers.
# ------------------------------------------
TITLE_MATCH_THRESHOLD = 0.6  # Jaccard overlap; below this, treat as a wrong-paper match


def _title_tokens(s: str) -> set[str]:
    """Bag of significant words for fuzzy title comparison."""
    return {w for w in re.findall(r"[a-z0-9]+", s.lower()) if len(w) >= 3}


def titles_similar(a: str, b: str, threshold: float = TITLE_MATCH_THRESHOLD) -> bool:
    """Jaccard overlap of significant tokens, ≥ threshold."""
    ta, tb = _title_tokens(a), _title_tokens(b)
    if not ta or not tb:
        return False
    union = ta | tb
    return (len(ta & tb) / len(union)) >= threshold


# Venues that don't register DOIs with Crossref (USENIX runs its own service).
# Detected from the bracket-tag acronym. Crossref will happily return a fuzzy
# match against some other paper if we ask, so we skip Crossref entirely for
# these — DBLP can still return a usenix.org URL.
NO_DOI_ACRONYM_PREFIXES: tuple[str, ...] = ("USENIX",)


def is_no_doi_venue(acronym: str | None) -> bool:
    if not acronym:
        return False
    return any(acronym.upper().startswith(p) for p in NO_DOI_ACRONYM_PREFIXES)

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
Rank = str  # "Rank 1"..3 | "Workshop" | "Magazine" | "Preprint" | "CVE"
Section = Literal[
    "Journals",
    "Conferences and Workshops",
    "Other publications and products",
    "Patents",
]

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
    "Other publications and products",
    "Patents",
]

SECTION_CODES: dict[Section, str] = {
    "Journals": "C.2",
    "Conferences and Workshops": "C.4",
    "Other publications and products": "C.5",
    "Patents": "C.19",
}

SECTION_HEADINGS: dict[Section, str] = {
    "Journals": "Journals",
    "Conferences and Workshops": "Conferences and Workshops",
    "Other publications and products": "Other publications and products",
    "Patents": "Issued U.S. and International Patents",
}

# Inline tier label appended at end of each citation.
TIER_LABELS: dict[Rank, str] = {
    "Rank 1": "Tier 1",
    "Rank 2": "Tier 2",
    "Rank 3": "Tier 3",
    "Workshop": "Workshop",
    "Magazine": "Magazine",
    "Preprint": "Preprint",
    "CVE": "CVE",
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


# Cache + lookup counters (mutated by fetch_*; summarized in main).
LOOKUP_STATS: dict[str, int] = {
    "doi_cache_hits": 0,
    "doi_cache_misses": 0,
    "arxiv_constructed": 0,
    "patent_cache_hits": 0,
    "patent_cache_misses": 0,
    "cve_cache_hits": 0,
    "cve_cache_misses": 0,
}


# ==========================================
# TYPES
# ==========================================
class Citation(NamedTuple):
    section: Section
    rank: Rank
    year: int          # sort key; 9999 for "In Press" / unparseable
    year_str: str
    authors_rtf: str
    title: str
    venue: str
    details: str
    link: str
    back_ref_title: Optional[str] = None  # CVE → paper title (resolved to "(see C.4.7)" at render)


class Patent(NamedTuple):
    year: int
    year_str: str
    title: str
    co_inventors: str  # already RTF-marked (\b for me); do NOT escape at render
    date: str
    number: str
    impact: str


class NetworkTask(NamedTuple):
    """One unit of network work to be dispatched in parallel.

    `kind` selects which SQLite cache table the result writes into;
    `key` is the primary key for that cache row;
    `fetcher` is the pure network call (no DB writes — main thread commits).
    """
    kind: str                       # "doi" | "cve" | "patent"
    key: str                        # cache primary key
    fetcher: Callable[[], Any]      # pure network call; returns result or None


Publications = dict[Section, list[Citation]]
BibEntry = dict[str, str]


# ==========================================
# RTF TABLE BUILDER
# ==========================================
class RtfTable:
    """Composes an RTF table: column widths in twips (1440 twips = 1 inch).

    Usage:
        t = RtfTable([2400, 2000, 1600])
        t.add_header(["A", "B", "C"])
        t.add_row(["1", "2", "3"])
        rtf_string = t.render()
    """

    def __init__(self, column_widths: list[int]):
        if not column_widths:
            raise ValueError("RtfTable requires at least one column")
        self.column_widths = column_widths
        cumulative = 0
        self._cellx: list[int] = []
        for w in column_widths:
            cumulative += w
            self._cellx.append(cumulative)
        self._rows: list[tuple[list[str], bool]] = []

    def add_header(self, cells: list[str]) -> None:
        self._check_arity(cells)
        self._rows.append((cells, True))

    def add_row(self, cells: list[str]) -> None:
        self._check_arity(cells)
        self._rows.append((cells, False))

    def render(self) -> str:
        return "".join(self._render_row(cells, h) for cells, h in self._rows)

    def _check_arity(self, cells: list[str]) -> None:
        if len(cells) != len(self.column_widths):
            raise ValueError(f"Expected {len(self.column_widths)} cells, got {len(cells)}")

    def _render_row(self, cells: list[str], is_header: bool) -> str:
        parts: list[str] = [r"\trowd\trgaph108\trleft0"]
        for pos in self._cellx:
            parts.append(rf"\cellx{pos}")
        for cell in cells:
            content = rf"\b {cell}\b0" if is_header else cell
            parts.append(rf" {content}\cell")
        parts.append("\\row\n")
        return "".join(parts)


# ==========================================
# DATABASE
# ==========================================
def open_db(path: str) -> sqlite3.Connection:
    """Open the lookup cache; fresh students table per run."""
    conn = sqlite3.connect(path)
    cur = conn.cursor()
    cur.execute("CREATE TABLE IF NOT EXISTS doi_cache (title TEXT PRIMARY KEY, doi_or_url TEXT)")
    cur.execute(
        "CREATE TABLE IF NOT EXISTS patent_cache (patent_id TEXT PRIMARY KEY, issue_date TEXT)"
    )
    cur.execute(
        "CREATE TABLE IF NOT EXISTS cve_cache (cve_id TEXT PRIMARY KEY, data_json TEXT)"
    )
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
# AUTHOR / NAME HANDLING
# ==========================================
def name_matches(bib_name: str, person_list: list[str]) -> bool:
    return any(p.lower() in bib_name.lower() for p in person_list)


def lookup_student_type(conn: sqlite3.Connection, bib_name: str) -> str:
    """Return 'G' / 'U' if bib_name matches a known student, else ''.

    Normalizes by stripping periods before substring comparison so a single-letter
    middle initial ('C' vs 'C.') matches either way.
    """
    cur = conn.cursor()
    cur.execute("SELECT name, type FROM students")
    bib_norm = bib_name.lower().replace(".", "")
    for db_name, s_type in cur.fetchall():
        if db_name.lower().replace(".", "") in bib_norm:
            return s_type
    return ""


def parse_name_parts(bib_name: str) -> tuple[str, list[str]]:
    """Parse 'Last, First Middle' or 'First Middle Last' → (last, [first_parts])."""
    if "," in bib_name:
        parts = [p.strip() for p in bib_name.split(",")]
        last = parts[0]
        firsts = parts[1].split() if len(parts) > 1 else []
    else:
        parts = bib_name.split()
        last = parts[-1]
        firsts = parts[:-1]
    return last, firsts


def format_author(conn: sqlite3.Connection, bib_name: str, is_last: bool) -> str:
    """Citation form: 'Last, F.I.' + bold-for-me + comma-joined role markers."""
    bib_name = decode_latex(bib_name)
    last, firsts = parse_name_parts(bib_name)
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


def format_inventors(raw_authors: str) -> str:
    """Patent-table form: 'Last, F.I.', comma-joined, in bib order. Bold for me.

    Same name shape as citations. No role markers (no G/U/#/* in patent tables).
    Returns RTF-marked-up text — do NOT escape_rtf the result before emitting.
    """
    parts: list[str] = []
    for raw in raw_authors.split(" and "):
        decoded = decode_latex(raw.strip())
        last, firsts = parse_name_parts(decoded)
        initials = ".".join(f[0].upper() for f in firsts) + "." if firsts else ""
        formatted = f"{last}, {initials}" if initials else last
        if name_matches(decoded, ME):
            formatted = f"\\b {formatted}\\b0"
        parts.append(formatted)
    return ", ".join(parts)


# ==========================================
# NETWORK FETCHERS (pure — no DB writes, no cache reads)
# ==========================================
def try_crossref(title: str, authors: str) -> str:
    """Returns a DOI URL if Crossref's top hit's title is similar to ours, else ''."""
    query = urllib.parse.quote(f"{title} {authors.split(' and ')[0]}")
    url = f"https://api.crossref.org/works?query={query}&select=DOI,title&rows=1"
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
    url = f"https://dblp.org/search/publ/api?q={q_title}&format=json&h=1"
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


def try_doi(title: str, authors: str, acronym: str | None = None) -> str:
    """Crossref (skipped for no-DOI venues like USENIX) then DBLP. Returns URL or ''."""
    link = "" if is_no_doi_venue(acronym) else try_crossref(title, authors)
    return link or try_dblp(title)


def try_patentsview(number_clean: str) -> str | None:
    """Query PatentsView for the issue date. Returns 'YYYY-MM-DD' or None.

    Requires PATENTSVIEW_API_KEY env var. When absent, returns None so the
    caller falls back to the bib date.
    """
    api_key = os.environ.get("PATENTSVIEW_API_KEY", "")
    if not api_key:
        log.debug("PATENTSVIEW_API_KEY not set; skipping USPTO lookup for %s", number_clean)
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
                log.warning("PatentsView HTTP %d for patent %s", resp.status_code, number_clean)
            return None
        data = resp.json()
        patents = data.get("patents") or []
        if patents and patents[0].get("patent_date"):
            return patents[0]["patent_date"]
    except Exception as e:
        log.warning("PatentsView query failed for patent %s: %s", number_clean, e)
    return None


def try_nvd(cve_id: str) -> dict | None:
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
        return vulns[0].get("cve")
    log.warning("NVD has no record for %s", cve_id)
    return None


# ==========================================
# PARALLEL DISPATCH
# ==========================================
def dispatch_parallel(
    tasks: list[NetworkTask], max_workers: int = DEFAULT_MAX_WORKERS,
) -> dict[tuple[str, str], Any]:
    """Run all NetworkTasks concurrently. Returns {(kind, key): result_or_None}.

    Threads are appropriate here: every fetcher is I/O-bound (HTTPS roundtrip),
    and the GIL is released during socket waits. None of the fetchers touch
    SQLite — caching is done sequentially by commit_results in the main thread.
    """
    results: dict[tuple[str, str], Any] = {}
    if not tasks:
        return results
    log.info("Dispatching %d network lookup(s) with max_workers=%d", len(tasks), max_workers)
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        future_to_task = {ex.submit(t.fetcher): t for t in tasks}
        for fut in as_completed(future_to_task):
            t = future_to_task[fut]
            try:
                results[(t.kind, t.key)] = fut.result()
            except Exception as e:
                log.warning("Lookup %s/%s raised: %s", t.kind, t.key, e)
                results[(t.kind, t.key)] = None
    return results


def commit_results(conn: sqlite3.Connection, results: dict[tuple[str, str], Any]) -> None:
    """Persist parallel-fetched results into their respective cache tables."""
    if not results:
        return
    cur = conn.cursor()
    for (kind, key), value in results.items():
        if kind == "doi":
            cur.execute("INSERT OR REPLACE INTO doi_cache VALUES (?, ?)", (key, value or ""))
        elif kind == "patent":
            cur.execute("INSERT OR REPLACE INTO patent_cache VALUES (?, ?)", (key, value or ""))
        elif kind == "cve":
            cur.execute(
                "INSERT OR REPLACE INTO cve_cache VALUES (?, ?)",
                (key, json.dumps(value) if value else ""),
            )
    conn.commit()


# ==========================================
# CACHE READS (used during build phase; cache is warm by then)
# ==========================================
def cache_read_doi(conn: sqlite3.Connection, title: str) -> Optional[str]:
    cur = conn.cursor()
    cur.execute("SELECT doi_or_url FROM doi_cache WHERE title=?", (title.lower(),))
    row = cur.fetchone()
    return row[0] if row else None


def cache_read_patent(conn: sqlite3.Connection, number_clean: str) -> Optional[str]:
    cur = conn.cursor()
    cur.execute("SELECT issue_date FROM patent_cache WHERE patent_id=?", (number_clean,))
    row = cur.fetchone()
    return row[0] if row else None


def cache_read_cve(conn: sqlite3.Connection, cve_id: str) -> Optional[dict]:
    cur = conn.cursor()
    cur.execute("SELECT data_json FROM cve_cache WHERE cve_id=?", (cve_id,))
    row = cur.fetchone()
    if not row:
        return None
    if not row[0]:
        return None  # cached "not found" sentinel
    try:
        return json.loads(row[0])
    except Exception:
        return None


def fetch_doi_or_url(
    conn: sqlite3.Connection, title: str, authors: str, acronym: str | None = None,
) -> str:
    """Cache-or-fetch DOI lookup (fallback path; planning normally pre-warms).

    `acronym` is the bracket-tag from the venue, used to skip Crossref for venues
    that don't register DOIs (USENIX). DBLP is still tried.
    """
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


def fetch_patent_date(conn: sqlite3.Connection, number_clean: str, fallback_date: str) -> str:
    """Cache-or-fetch patent issue-date lookup. Returns ISO date or fallback."""
    cached = cache_read_patent(conn, number_clean)
    if cached is not None:
        LOOKUP_STATS["patent_cache_hits"] += 1
        log.debug("Patent cache hit: %s -> %s", number_clean, cached)
        return cached or fallback_date
    LOOKUP_STATS["patent_cache_misses"] += 1
    log.info("Fetching patent issue date for: %s (sequential fallback)", number_clean)
    iso = try_patentsview(number_clean)
    cur = conn.cursor()
    cur.execute("INSERT INTO patent_cache VALUES (?, ?)", (number_clean, iso or ""))
    conn.commit()
    if iso:
        return iso
    log.info("No USPTO date for %s; using bib date '%s'", number_clean, fallback_date)
    return fallback_date


def fetch_cve_data(conn: sqlite3.Connection, cve_id: str) -> dict | None:
    """Cache-or-fetch NVD data for a CVE."""
    cur = conn.cursor()
    cur.execute("SELECT data_json FROM cve_cache WHERE cve_id=?", (cve_id,))
    row = cur.fetchone()
    if row:
        LOOKUP_STATS["cve_cache_hits"] += 1
        if not row[0]:
            return None
        try:
            return json.loads(row[0])
        except Exception:
            return None
    LOOKUP_STATS["cve_cache_misses"] += 1
    log.info("Fetching NVD data for: %s (sequential fallback)", cve_id)
    data = try_nvd(cve_id)
    cur.execute(
        "INSERT INTO cve_cache VALUES (?, ?)", (cve_id, json.dumps(data) if data else ""),
    )
    conn.commit()
    return data


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
# CVE / TITLE HELPERS
# ==========================================
CVE_ID_RE = re.compile(r"\bCVE-\d{4}-\d+\b", re.IGNORECASE)


def extract_cve_id(text: str) -> str:
    m = CVE_ID_RE.search(text)
    return m.group(0).upper() if m else ""


def normalize_title(title: str) -> str:
    """Lowercase + collapse whitespace. Used for CVE.paper_title ↔ bib title matching."""
    return " ".join(decode_latex(title).lower().split())


# ==========================================
# DATE FORMATTING
# ==========================================
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
# CITATION / PATENT / CVE BUILDING
# ==========================================
def escape_rtf(text: str) -> str:
    return text.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")


def classify_entry(entry: BibEntry) -> tuple[Category, str]:
    """Determine (category, raw-venue-string) for a non-patent, non-CVE entry."""
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
    acronym: str | None = None,
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
        format_author(conn, a.strip(), is_last=(i == len(author_list) - 1))
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


def is_patent_entry(entry: BibEntry) -> bool:
    """Detect a patent entry: @misc with 'patent' in publisher or note."""
    if entry.get("ENTRYTYPE", "").lower() != "misc":
        return False
    publisher = entry.get("publisher", "").lower()
    note = entry.get("note", "").lower()
    return "patent" in publisher or "patent" in note


def extract_patent_number(note: str) -> tuple[str, str]:
    """('US Patent 11,176,090', '11176090') from the bib note; ('','') on failure."""
    m = re.search(r"(US\s+Patent\s+([\d,]+))", note, re.IGNORECASE)
    if m:
        return m.group(1), re.sub(r"\D", "", m.group(2))
    m = re.search(r"([\d][\d,]*)", note)
    if m:
        return note.strip(), re.sub(r"\D", "", m.group(1))
    return "", ""


def build_patent(conn: sqlite3.Connection, entry: BibEntry) -> Patent:
    title = decode_latex(entry.get("title", "")).replace("\n", " ")
    inventors = format_inventors(entry.get("author", ""))

    number_display, number_clean = extract_patent_number(entry.get("note", ""))
    if not number_clean:
        log.warning("Patent entry has no extractable number in note: '%s'", title)

    year_str = entry.get("year", "")
    fallback = format_bib_date(entry)
    raw_date = fetch_patent_date(conn, number_clean, fallback) if number_clean else fallback
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


# ==========================================
# NON-SCHOLAR WORK (YAML side file)
# ==========================================
# The bib is Scholar-canonical (papers, patents). CVEs and other non-Scholar
# work products live in a separate YAML file referenced by --non-scholar.
def load_non_scholar(path: str | None) -> dict:
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
        log.error("Non-Scholar YAML root must be a mapping; got %s", type(data).__name__)
        sys.exit(1)
    return data


def validate_non_scholar(non_scholar: dict, bib_entries: list[BibEntry]) -> None:
    """Enforce schema + title-resolution invariants. Crashes on any violation."""
    bib_titles = {normalize_title(e.get("title", "")) for e in bib_entries if e.get("title")}
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
            log.error("CVE %s must specify `paper_title` and/or `disclosers`", cve_id)
            sys.exit(1)
        if paper_title and normalize_title(paper_title) not in bib_titles:
            log.error(
                "CVE %s references paper_title %r, but no matching entry exists in the bib.",
                cve_id, paper_title,
            )
            log.error("Bib title matching is case- and whitespace-insensitive but requires same text.")
            sys.exit(1)


def _cve_nvd_description(cve_data: dict | None) -> str:
    """First-sentence English description from an NVD record."""
    if not cve_data:
        return ""
    desc = next(
        (x["value"] for x in (cve_data.get("descriptions") or []) if x.get("lang") == "en"),
        "",
    )
    return desc.split(". ")[0].rstrip(".") if desc else ""


def _cve_nvd_year(cve_data: dict | None) -> str:
    if not cve_data:
        return ""
    return (cve_data.get("published") or "")[:4]


def _bib_entry_by_title(bib_entries: list[BibEntry], paper_title: str) -> BibEntry | None:
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

    # Year: prefer NVD's published date so chrono-sort is grounded in disclosure date.
    year_str = _cve_nvd_year(cve_data) or "In Press"

    disclosers = cve.get("disclosers")
    paper_title = cve.get("paper_title")
    if disclosers:
        names = list(disclosers)
    elif paper_title:
        paper = _bib_entry_by_title(bib_entries, paper_title)
        # validate_non_scholar already ensured paper exists; this is belt-and-suspenders.
        names = (paper.get("author", "") if paper else "").split(" and ")
    else:
        names = []
    formatted = [
        format_author(conn, n.strip(), is_last=(i == len(names) - 1))
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
        back_ref_title=paper_title,  # render-time looked up in paper_index
    )


# ==========================================
# LOOKUP PLANNING
# ==========================================
def plan_lookups(
    conn: sqlite3.Connection,
    entries: list[BibEntry],
    non_scholar: dict | None = None,
) -> list[NetworkTask]:
    """Walk every entry + YAML CVE list, return NetworkTasks for items not yet cached.

    Returns an empty list if every required lookup is already cached — that's
    the warm-cache path and runs at memory speed.
    """
    tasks: list[NetworkTask] = []
    cur = conn.cursor()

    for entry in entries:
        if is_patent_entry(entry):
            _, number_clean = extract_patent_number(entry.get("note", ""))
            if number_clean:
                cur.execute("SELECT 1 FROM patent_cache WHERE patent_id=?", (number_clean,))
                if not cur.fetchone():
                    tasks.append(NetworkTask(
                        kind="patent",
                        key=number_clean,
                        fetcher=lambda nc=number_clean: try_patentsview(nc),
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
        # Acronym from the bracket-tag, used to skip Crossref for no-DOI venues.
        acronym, _ = parse_venue(decode_latex(venue_raw))
        authors = entry.get("author", "")
        tasks.append(NetworkTask(
            kind="doi",
            key=title.lower(),
            fetcher=lambda t=title, a=authors, ac=acronym: try_doi(t, a, ac),
        ))

    # YAML CVE list — each cve_id needs an NVD lookup if not already cached.
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
                fetcher=lambda c=cve_id: try_nvd(c),
            ))

    return tasks


# ==========================================
# RTF OUTPUT
# ==========================================
def apply_acronym_expansions(venue: str, done: set[str]) -> str:
    """Spell out an org acronym on its first occurrence in this section."""
    for acronym, expansion in ORG_EXPANSIONS.items():
        if acronym in done:
            continue
        if re.search(rf"\b{acronym}\b", venue):
            venue = re.sub(rf"\b{acronym}\b", expansion, venue, count=1)
            done.add(acronym)
    return venue


def render_link_field(link: str) -> str:
    """RTF for the hyperlink + visible prefix.

      https://doi.org/X        →  DOI:X
      https://nvd.nist.gov/...  →  CVE-XXXX-NNNNN  (no prefix; the ID is self-descriptive)
      anything else            →  URL:<full>
    """
    if not link:
        return ""
    if link.startswith("https://doi.org/"):
        prefix = "DOI:"
        display = link[len("https://doi.org/"):]
    elif "nvd.nist.gov/vuln/detail/" in link:
        prefix = ""
        display = link.rsplit("/", 1)[-1]
    else:
        prefix = "URL:"
        display = link
    return f' {prefix}{{\\field{{\\*\\fldinst HYPERLINK "{link}"}}{{\\fldrslt {display}}}}}'


def render_citation(
    cit: Citation,
    expansion_done: set[str],
    paper_index: dict[str, str] | None = None,
) -> str:
    """RTF body for one citation (no paragraph wrapping).

    `paper_index` maps normalized-bib-title → section code (e.g. "C.4.7").
    When `cit.back_ref_title` is set (CVE → paper linkage), the back-pointer
    "(see C.4.7)" is appended to the rendered body.
    """
    venue = apply_acronym_expansions(cit.venue, expansion_done) if cit.venue else ""
    body = f"{cit.authors_rtf}, ({cit.year_str}). {escape_rtf(cit.title)}"
    if venue:
        body += f", \\i {escape_rtf(venue)}\\i0"
    body += f"{escape_rtf(cit.details)}."
    body += render_link_field(cit.link)
    body += f" \\i {TIER_LABELS[cit.rank]}\\i0."
    if cit.back_ref_title and paper_index:
        ref = paper_index.get(normalize_title(cit.back_ref_title))
        if ref:
            body += f" (see {ref})"
    return body


# Patent table column widths (twips). Sum = 9360 = 6.5" usable on US Letter.
PATENT_TABLE_WIDTHS: list[int] = [2400, 2000, 1600, 1500, 1860]


def render_patents_section(patents: list[Patent], out) -> None:
    if not patents:
        return
    code = SECTION_CODES["Patents"]
    heading = SECTION_HEADINGS["Patents"]
    out.write(f"\\pard\\b\\fs28 {code} {heading}\\b0\\fs24\\par\\par\n")

    table = RtfTable(column_widths=PATENT_TABLE_WIDTHS)
    table.add_header(["Title", "Co-Inventors", "Issue Date", "Number", "Impact"])
    for p in patents:
        table.add_row([
            escape_rtf(p.title),
            p.co_inventors,  # already RTF-marked (\b for me); escaping would clobber it
            escape_rtf(p.date),
            escape_rtf(p.number),
            escape_rtf(p.impact),
        ])
    out.write(table.render())
    out.write("\\pard\\par\n")


def build_paper_index(publications: Publications) -> dict[str, str]:
    """Map normalized-paper-title → 'C.X.Y' for back-pointer resolution.

    Assumes citations are already chrono-sorted within each section.
    Patents and CVEs are not back-pointable targets (would be a forward reference
    chain), so they're excluded.
    """
    index: dict[str, str] = {}
    for section in SECTION_ORDER:
        if section == "Patents":
            continue
        for idx, cit in enumerate(publications.get(section, []), 1):
            if cit.rank == "CVE":
                continue
            if cit.title:
                index[normalize_title(cit.title)] = f"{SECTION_CODES[section]}.{idx}"
    return index


def write_rtf(
    path: str,
    publications: Publications,
    patents: list[Patent],
    paper_index: dict[str, str] | None = None,
) -> None:
    log.info("Generating RTF file: %s", path)
    paper_index = paper_index or {}
    with open(path, "w", encoding="utf-8") as out:
        out.write(r"{\rtf1\ansi\ansicpg1252\deff0{\fonttbl{\f0\froman\fcharset0 Times New Roman;}}")
        out.write(r"{\colortbl;\red0\green0\blue255;}")

        for section in SECTION_ORDER:
            if section == "Patents":
                continue  # rendered separately as a table
            citations = publications.get(section, [])
            if not citations:
                continue
            code = SECTION_CODES[section]
            heading = SECTION_HEADINGS[section]
            out.write(f"\\pard\\b\\fs28 {code} {heading}\\b0\\fs24\\par\\par\n")

            expansion_done: set[str] = set()
            for idx, cit in enumerate(citations, 1):
                body = render_citation(cit, expansion_done, paper_index)
                # Hanging indent: first line flush, continuation lines indented to 720 twips.
                out.write(f"\\pard\\li720\\fi-720 {code}.{idx}.\\tab {body}\\par\\par\n")

        render_patents_section(patents, out)
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


def log_section_summary(publications: Publications, patents: list[Patent]) -> None:
    for section in SECTION_ORDER:
        if section == "Patents":
            if patents:
                log.info("Section %s '%s': %d patents", SECTION_CODES[section], section, len(patents))
            continue
        cits = publications.get(section, [])
        if cits:
            log.info("Section %s '%s': %d papers", SECTION_CODES[section], section, len(cits))


def log_lookup_stats() -> None:
    """Summarize lookup-cache effectiveness across DOI, patent, CVE caches."""
    doi_h = LOOKUP_STATS["doi_cache_hits"]
    doi_m = LOOKUP_STATS["doi_cache_misses"]
    pat_h = LOOKUP_STATS["patent_cache_hits"]
    pat_m = LOOKUP_STATS["patent_cache_misses"]
    cve_h = LOOKUP_STATS["cve_cache_hits"]
    cve_m = LOOKUP_STATS["cve_cache_misses"]
    arxiv = LOOKUP_STATS["arxiv_constructed"]

    def fmt(label: str, hits: int, misses: int) -> None:
        total = hits + misses
        if total:
            rate = 100.0 * hits / total
            log.info("%s cache: %d/%d hits (%.0f%%); %d sequential fallback fetch(es)",
                     label, hits, total, rate, misses)

    fmt("DOI", doi_h, doi_m)
    fmt("Patent", pat_h, pat_m)
    fmt("CVE", cve_h, cve_m)
    if arxiv:
        log.info("arXiv: %d DOIs constructed (cache bypassed)", arxiv)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Generate a formatted RTF publication list from a BibTeX file. "
            "Output is suitable for pasting into Word with formatting preserved "
            "(bold for me, superscripts for student/advisor roles, hyperlinks, "
            "patent table, CVE entries)."
        ),
        epilog=(
            "Environment:\n"
            "  LOG_LEVEL              Logging verbosity (DEBUG, INFO, WARNING, ERROR). Default: INFO.\n"
            "  PATENTSVIEW_API_KEY    Optional; enables USPTO issue-date lookup for patents.\n"
            "  NVD_API_KEY            Optional; raises NVD rate limit ~10x.\n\n"
            "BibTeX conventions:\n"
            "  - Citations: journal / booktitle must begin with a bracketed tag,\n"
            "    e.g. `[ICSE'25]`. The acronym must appear in the RANKS dictionary.\n"
            "  - Patents: @misc whose `publisher` or `note` contains 'patent';\n"
            "    `note = {US Patent 11,176,090}` carries the number.\n"
            "  - CVEs: @misc whose `note` matches `CVE-YYYY-NNNN`. Optional fields:\n"
            "    `organization = {<project>}` (italicized as venue), `url`,\n"
            "    `title` (overrides NVD's first-sentence description).\n"
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
            f"Path to the SQLite lookup cache (DOI + patent + CVE). Created if missing; "
            f"safe to delete to force re-lookup. Default: {DEFAULT_DB_FILE}"
        ),
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=DEFAULT_MAX_WORKERS,
        metavar="N",
        help=f"Parallel network workers for cache-miss lookups. Default: {DEFAULT_MAX_WORKERS}",
    )
    parser.add_argument(
        "--non-scholar",
        metavar="PATH",
        default=None,
        help=(
            "Path to YAML side file with non-Scholar work (CVEs etc.). "
            "Bib stays Scholar-canonical; this file fills the gaps. Optional."
        ),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    conn = open_db(args.cache)
    try:
        populate_students(conn, STUDENTS)
        entries = load_bib(args.bib)

        # Load + validate YAML side file (CVEs etc.) before any network work.
        # Validation crashes fast on unresolved paper_title.
        non_scholar = load_non_scholar(args.non_scholar)
        validate_non_scholar(non_scholar, entries)

        # Phase 1-3: plan all needed network calls, dispatch in parallel, persist.
        tasks = plan_lookups(conn, entries, non_scholar)
        if tasks:
            results = dispatch_parallel(tasks, max_workers=args.workers)
            commit_results(conn, results)
        else:
            log.info("Cache is fully warm; no network lookups needed.")

        # Phase 4: build citations / patents from bib (now all cache-hits).
        publications: Publications = defaultdict(list)
        patents: list[Patent] = []
        for entry in entries:
            if is_patent_entry(entry):
                patents.append(build_patent(conn, entry))
            else:
                cit = build_citation(conn, entry)
                publications[cit.section].append(cit)

        # Phase 4b: build CVE citations from YAML, route to C.5.
        for cve in non_scholar.get("cves") or []:
            cit = build_cve_from_yaml(conn, cve, entries)
            publications[cit.section].append(cit)

        # Chronological order (oldest first) within each section.
        for section in publications:
            publications[section].sort(key=lambda c: c.year)
        patents.sort(key=lambda p: p.year)

        # Paper index for "(see C.X.Y)" back-pointers from CVE entries.
        # Must run AFTER chrono-sort so indices are stable.
        paper_index = build_paper_index(publications)

        log_section_summary(publications, patents)
        log_lookup_stats()

        # Phase 5: render.
        write_rtf(args.out, publications, patents, paper_index)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
