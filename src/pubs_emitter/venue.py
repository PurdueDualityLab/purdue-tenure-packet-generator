"""Entry classification + venue/acronym parsing + ID extraction (arXiv, CVE)."""
from __future__ import annotations

import logging
import re
import sys
from typing import Optional

from .config import RANKS
from .types import BibEntry, Category, Rank


log = logging.getLogger(__name__)


# ----- Entry classification -----------------------------------------------


def classify_entry(entry: BibEntry) -> tuple[Category, str]:
    """Determine (category, raw-venue-string) for a non-patent, non-CVE entry."""
    if "journal" in entry:
        venue = entry["journal"].replace("\n", " ")
        category: Category = (
            "arXiv / Preprints" if "arxiv" in venue.lower() else "Journals"
        )
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
    """Resolve an acronym to its rank, or abort with a clear error."""
    if not acronym:
        log.error("Missing [ACRONYM] tag in venue field for paper: '%s'", title)
        sys.exit(1)
    for known, rank in RANKS.items():
        if acronym.upper() == known.upper():
            return rank
    log.error("Unranked venue '%s' for paper: '%s'", acronym, title)
    log.error("Add '%s' to the `ranks` block in assets/config.yaml.", acronym)
    sys.exit(1)


# ----- arXiv ID extraction ------------------------------------------------

# Modern arXiv IDs: 2605.10712 (YYMM.NNNNN, 4-5 digit suffix).
# Legacy IDs: hep-ph/0501001 or math.GT/0501001.
ARXIV_ID_RE = re.compile(r"\b(\d{4}\.\d{4,5}|[a-z\-]+(?:\.[A-Z]{2})?/\d{7})\b")


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


# ----- CVE-related helpers ------------------------------------------------

CVE_ID_RE = re.compile(r"\bCVE-\d{4}-\d+\b", re.IGNORECASE)


def extract_cve_id(text: str) -> str:
    m = CVE_ID_RE.search(text)
    return m.group(0).upper() if m else ""


def normalize_title(title: str) -> str:
    """Lowercase + collapse whitespace. Used for CVE.paper_title ↔ bib title matching."""
    # Importing decode_latex inline to keep this module free of LaTeX-decoder deps
    # in places where the caller may not have already decoded.
    from .latex import decode_latex  # local import: avoids circular if future split
    return " ".join(decode_latex(title).lower().split())
