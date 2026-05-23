"""User configuration (loaded from assets/config.yaml) + code-side constants.

User-curated values live in `assets/config.yaml`. Output-structure decisions
(section codes, tier labels, rate-limit hosts, org expansions) stay in code
because they're not data the user re-curates.

Override the config path with the PUBS_EMITTER_CONFIG env var.
"""
from __future__ import annotations

import os
from typing import cast

import yaml

from .types import Rank, Section, StudentType


def _find_config_path() -> str:
    env = os.environ.get("PUBS_EMITTER_CONFIG")
    if env:
        return env
    here = os.path.dirname(os.path.abspath(__file__))
    # src/pubs_emitter/config.py → ../../assets/config.yaml
    return os.path.normpath(os.path.join(here, "..", "..", "assets", "config.yaml"))


def _load_config() -> dict:
    path = _find_config_path()
    if not os.path.exists(path):
        raise RuntimeError(
            f"User config not found at {path}. "
            "Set PUBS_EMITTER_CONFIG or create assets/config.yaml."
        )
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise RuntimeError(f"{path}: root must be a mapping")
    return data


_conf = _load_config()

ME: list[str] = list(_conf.get("me") or [])
ADVISORS: list[str] = list(_conf.get("advisors") or [])
STUDENTS: dict[StudentType, list[str]] = cast(
    "dict[StudentType, list[str]]",
    {k: list(v or []) for k, v in (_conf.get("students") or {}).items()},
)

# YAML stores rank → [acronyms] for readability. Invert to acronym → rank.
RANKS: dict[str, Rank] = {}
for _rank, _acronyms in (_conf.get("ranks") or {}).items():
    for _ac in _acronyms or []:
        RANKS[_ac] = _rank

# Hand-curated {title: url} overrides for entries Scholar can't supply a link
# for (PhD theses, some workshop papers, etc.). These are seeded into doi_cache
# at startup so the normal cache-lookup path picks them up.
MANUAL_LINKS: dict[str, str] = dict(_conf.get("manual_links") or {})

# Bib titles to drop on load (case- and whitespace-insensitive match). Scholar
# re-exports clobber manual edits to my_papers.bib, so a config-driven filter
# is the only durable way to suppress entries the user doesn't want in the CV.
BIB_IGNORE: list[str] = list(_conf.get("bib_ignore") or [])


# ----- Code-side constants (not in YAML) -----------------------------------

SECTION_ORDER: list[Section] = [
    "Key Works",
    "Journals",
    "Books and Chapters",
    "Conferences and Workshops",
    "Other publications and products",
    "Invited Talks",
    "Leadership Roles",
    "Media Appearances",
    "Conference Presentations",
    "Grants PI",
    "Grants Co-PI",
    "Gifts",
    "Internal Grants",
    "Patents",
]

SECTION_CODES: dict[Section, str] = {
    "Key Works": "C.1",
    "Journals": "C.2",
    "Books and Chapters": "C.3",
    "Conferences and Workshops": "C.4",
    "Other publications and products": "C.5",
    "Invited Talks": "C.6",
    "Leadership Roles": "C.7",
    "Media Appearances": "C.8",
    "Conference Presentations": "C.9",
    "Grants PI": "C.10",
    "Grants Co-PI": "C.11",
    "Gifts": "C.12",
    "Internal Grants": "C.13",
    "Patents": "C.19",
}

SECTION_HEADINGS: dict[Section, str] = {
    "Key Works": "Key Scholarly Publications or Patents",
    "Journals": "Journals",
    "Books and Chapters": "Books and chapters in books",
    "Conferences and Workshops": "Conferences and Workshops",
    "Other publications and products": "Other publications and products",
    "Invited Talks": "Invited external keynote/conference/symposium/colloquium/seminar presentations",
    "Leadership Roles": "Leadership roles in government or professional organizations",
    "Media Appearances": "Appearances in media interviews and other coverage",
    "Conference Presentations": "Selected contributed conference/symposium presentations where the candidate was the presenter",
    "Grants PI": "Externally sponsored grants as PI, or Purdue lead on multi-institution grants",
    "Grants Co-PI": "Externally sponsored grants as Co-PI or Co-I",
    "Gifts": "External gifts and voluntary support",
    "Internal Grants": "Internal competitive grants as PI or Co-PI",
    "Patents": "Issued U.S. and International Patents",
}

# Per-section "Total amount of ... :" label rendered above the numbered list.
# Sections not in this map don't render a total.
GRANT_TOTAL_LABELS: dict[Section, str] = {
    "Grants PI": "Total amount of external funding as PI",
    "Grants Co-PI": "Total amount of external funding as Co-PI or Co-I",
    "Gifts": "Total amount of external gifts and voluntary support",
    "Internal Grants": "Total amount of internal funding",
}

# Sections whose entries render the tier marker with a "Venue rank: " prefix.
# Limited to peer-reviewed venue categories. C.1 Key Works inherits this
# automatically — each Key Work wraps a Journal/Conference citation whose
# `section` field is one of these.
RANKED_SECTIONS: set[Section] = {"Journals", "Conferences and Workshops"}

TIER_LABELS: dict[Rank, str] = {
    "Rank 1": "Tier 1",
    "Rank 2": "Tier 2",
    "Rank 3": "Tier 3",
    "Workshop": "Workshop",
    "Magazine": "Magazine",
    "Preprint": "Preprint",
    "CVE": "CVE",
    "Disclosure": "Security disclosure",
    "Dissertation": "Dissertation",
    "Book Chapter": "Book chapter",
}

# Sponsoring orgs: spell out on first occurrence per section, bare acronym after.
ORG_EXPANSIONS: dict[str, str] = {
    "IEEE": "Institute of Electrical and Electronics Engineers (IEEE)",
    "ACM": "Association for Computing Machinery (ACM)",
    "USENIX": "USENIX Association (USENIX)",
}

# Venues that don't register DOIs with Crossref (Crossref returns a wrong
# fuzzy match if we ask). Detected from the bracket-tag acronym.
NO_DOI_ACRONYM_PREFIXES: tuple[str, ...] = ("USENIX",)

# Defaults overridable via CLI.
DEFAULT_OUT_FILE = "publications.rtf"
DEFAULT_DB_FILE = "lookup_cache.sqlite"
DEFAULT_MAX_WORKERS = 8

# Patent table column widths in twips (1440 twips = 1 inch).
# Sum = 9360 = 6.5" usable on US Letter.
PATENT_TABLE_WIDTHS: list[int] = [2400, 2000, 1600, 1500, 1860]

# External API endpoints.
PATENTSVIEW_API_URL = "https://search.patentsview.org/api/v1/patent/"
NVD_API_URL = "https://services.nvd.nist.gov/rest/json/cves/2.0"
CROSSREF_API_URL = "https://api.crossref.org/works"
DBLP_API_URL = "https://dblp.org/search/publ/api"

# HTTP User-Agent (Crossref polite-pool requires a mailto). Override via env.
USER_AGENT = os.environ.get(
    "PUBS_EMITTER_USER_AGENT",
    "pubs-emitter/1.0 (mailto:davisjam@purdue.edu)",
)
DEFAULT_HEADERS: dict[str, str] = {"User-Agent": USER_AGENT}

# Title-similarity threshold for Crossref/DBLP fuzzy-match rejection.
TITLE_MATCH_THRESHOLD = 0.6

# Retry/backoff config for transient HTTP failures.
MAX_RETRIES = 2                        # 3 attempts total
BACKOFF_BASE_S = 1.0                   # 1s, 2s, 4s
TRANSIENT_HTTP_CODES: set[int] = {429, 500, 502, 503, 504}
