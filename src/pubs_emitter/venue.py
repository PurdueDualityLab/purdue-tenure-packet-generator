"""Entry classification + venue/acronym parsing + ID extraction (arXiv, CVE)."""
from __future__ import annotations

import logging
import re
from typing import Optional

from .config import RANKS
from .types import BibEntry, Category, Rank


log = logging.getLogger(__name__)


class EntryParseError(ValueError):
    """One entry failed parsing/validation. Recoverable in batch mode:
    cli.main collects all of these during the build loop and reports
    them together at the end instead of crashing on the first one.

    Subclasses carry structured fields (acronym, title) so the report
    can group and dedupe by error class — e.g., one ICSE-JAWs line
    instead of five identical ones for five papers."""


class MissingBracketTag(EntryParseError):
    """An entry's journal/booktitle had no `[ACRONYM'YY]` prefix."""

    def __init__(self, title: str):
        super().__init__(f"Missing [ACRONYM'YY] tag for paper: '{title}'")
        self.title = title


class UnrankedVenue(EntryParseError):
    """The bracket-tag acronym wasn't found in the RANKS table."""

    def __init__(self, acronym: str, title: str):
        super().__init__(f"Unranked venue '{acronym}' for paper: '{title}'")
        self.acronym = acronym
        self.title = title


class MissingArxivId(EntryParseError):
    """An arXiv-routed entry had no parseable arXiv ID."""

    def __init__(self, title: str):
        super().__init__(f"arXiv entry missing arXiv ID for paper: '{title}'")
        self.title = title


# ----- Entry classification -----------------------------------------------


# Substrings (case-insensitive) in a venue string that mark an entry as a
# preprint rather than a peer-reviewed journal. Each is paired with an ID
# extractor + DOI constructor in resolve_link.
_PREPRINT_VENUE_MARKERS: tuple[str, ...] = ("arxiv", "figshare")


def classify_entry(entry: BibEntry) -> tuple[Category, str]:
    """Determine (category, raw-venue-string) for a non-patent, non-CVE entry."""
    if "journal" in entry:
        venue = entry["journal"].replace("\n", " ")
        lower = venue.lower()
        is_preprint = any(m in lower for m in _PREPRINT_VENUE_MARKERS)
        category: Category = "arXiv / Preprints" if is_preprint else "Journals"
        return category, venue
    if "booktitle" in entry:
        return "Conferences and Workshops", entry["booktitle"].replace("\n", " ")
    if "eprint" in entry:
        return "arXiv / Preprints", "arXiv"
    return "arXiv / Preprints", ""


def is_patent_entry(entry: BibEntry) -> bool:
    """Detect a patent entry: @misc with 'patent' in publisher or note."""
    if entry.get("ENTRYTYPE", "").lower() != "misc":
        return False
    publisher = entry.get("publisher", "").lower()
    note = entry.get("note", "").lower()
    return "patent" in publisher or "patent" in note


def is_thesis_entry(entry: BibEntry) -> bool:
    """Detect a thesis entry: @phdthesis (or @mastersthesis)."""
    return entry.get("ENTRYTYPE", "").lower() in ("phdthesis", "mastersthesis")


def is_book_chapter_entry(entry: BibEntry) -> bool:
    """Detect a book chapter: @incollection or @inbook."""
    return entry.get("ENTRYTYPE", "").lower() in ("incollection", "inbook")


# ----- Venue parsing ------------------------------------------------------


def parse_venue(venue_str: str) -> tuple[Optional[str], str]:
    """Pull '[ACRONYM'YY]' off the front. Returns (acronym, cleaned-venue)."""
    match = re.search(r"\[(.*?)\]", venue_str)
    if not match:
        return None, venue_str
    raw_acronym = match.group(1).strip()
    acronym = re.sub(r"[\'’`\s]+\d{2,4}$", "", raw_acronym).strip()
    cleaned = venue_str.replace(match.group(0), "").strip()
    cleaned = re.sub(r"^[,:\s]+", "", cleaned)
    return acronym, cleaned


def lookup_rank(acronym: Optional[str], title: str) -> Rank:
    """Resolve an acronym to its rank, or raise a typed EntryParseError."""
    if not acronym:
        raise MissingBracketTag(title)
    for known, rank in RANKS.items():
        if acronym.upper() == known.upper():
            return rank
    raise UnrankedVenue(acronym, title)


# ----- arXiv ID extraction ------------------------------------------------

# Modern arXiv IDs: 2605.10712 (YYMM.NNNNN, 4-5 digit suffix).
# Legacy IDs: hep-ph/0501001 or math.GT/0501001.
# Optional `v<N>` version suffix is consumed but not captured — arXiv DOIs are
# per-paper, not per-version (e.g., `10.48550/arXiv.2605.10712` covers all versions).
ARXIV_ID_RE = re.compile(
    r"\b(\d{4}\.\d{4,5}|[a-z\-]+(?:\.[A-Z]{2})?/\d{7})(?:v\d+)?\b"
)


def extract_arxiv_id(entry: BibEntry) -> Optional[str]:
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


# ----- figshare ID extraction ---------------------------------------------

# figshare DOIs follow the form 10.6084/m9.figshare.<id> — the suffix
# `m9.figshare.<id>` is what we extract; the DOI is constructed
# deterministically, no API call.
FIGSHARE_ID_RE = re.compile(r"\b(m9\.figshare\.\d+)\b")


def extract_figshare_id(entry: BibEntry) -> Optional[str]:
    """Pull a figshare ID (m9.figshare.NNNN) out of the entry."""
    for field in ("eprint", "journal", "booktitle", "url", "title"):
        m = FIGSHARE_ID_RE.search(entry.get(field, ""))
        if m:
            return m.group(1)
    return None


# ----- CVE-related helpers ------------------------------------------------

CVE_ID_RE = re.compile(r"\bCVE-\d{4}-\d+\b", re.IGNORECASE)


def extract_cve_id(text: str) -> str:
    m = CVE_ID_RE.search(text)
    return m.group(0).upper() if m else ""


def normalize_title(title: str) -> str:
    """Lowercase + decode LaTeX + strip everything that isn't alphanumeric.

    Used for CVE.paper_title ↔ bib title matching. Aggressive on purpose:
    survives missing/extra spaces ('TheImpact' vs 'The Impact', a common
    copy-paste-from-PDF artifact), differing punctuation, and casing drift.
    """
    # Local import keeps this module free of LaTeX-decoder deps if a future
    # caller doesn't need the rest of venue.py.
    from .latex import decode_latex
    return re.sub(r"[^a-z0-9]", "", decode_latex(title).lower())
