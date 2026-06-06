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

# Corresponding-author override. The default `*` marker goes on the LAST
# author of each paper (typical CS convention). For papers where the
# corresponding author is in another position (e.g. multi-institution
# studies where the first or middle author led the work), map the bib
# citation key → the last-name string. `build_citation` finds the
# matching author in the bib's author list and marks THAT one with `*`
# instead of the structural last author. Example:
#   corresponding_authors:
#     herbold2022fine: "Herbold"  # 48 authors; Herbold (first) corresponds
CORRESPONDING_AUTHORS: dict[str, str] = dict(
    _conf.get("corresponding_authors") or {}
)

# Bib titles to drop on load (case- and whitespace-insensitive match). Scholar
# re-exports clobber manual edits to my_papers.bib, so a config-driven filter
# is the only durable way to suppress entries the user doesn't want in the CV.
BIB_IGNORE: list[str] = list(_conf.get("bib_ignore") or [])

# Publications to suppress from the rendered packet — keyed by BibTeX
# citation key (the stable identifier; titles change with edits, keys
# don't). Distinct semantic from `bib_ignore` (which is "this entry
# shouldn't exist in the bib at all"): `publication_hide` is "this
# entry exists but I don't want to show it in THIS rendering of the
# packet — trim by audience". Equivalent of the YAML-side
# `show: false` field for service entries; both paths flow through
# cli.py BEFORE numbering / paper_index assembly so hidden entries
# leave no gaps and never become reachable cross-ref targets.
PUBLICATION_HIDE: list[str] = list(_conf.get("publication_hide") or [])

# Venue acronym → full name. Used by `build_service_entry` to expand the
# FIRST occurrence of each acronym in a service description (C.23-C.26)
# to "{full_name} ({acronym})". Sorted by length descending at lookup
# time so compound acronyms (`ESEC/FSE`) match before substrings (`FSE`).
VENUE_FULL_NAMES: dict[str, str] = dict(_conf.get("venue_full_names") or {})


# ----- Code-side constants (not in YAML) -----------------------------------

SECTION_ORDER: list[Section] = [
    # Section III front matter (A.1-A.7; A.6 intentionally absent — handled
    # outside this generator). Emitted before all C.* sections at the top
    # of the doc, under an "A. GENERAL INFORMATION" group heading.
    "Identifiers",
    "Degrees",
    "Positions at Purdue",
    "Positions at Other Institutions",
    "Licenses",
    "Awards",
    "Professional Memberships",
    # Section IV (Self-Evaluation) — B.1-B.5. Emitted between A.7 and C.1.
    "B1 Summary",
    "B2 Impact",
    "B3 Vision",
    "B4 External Events",
    "B5 COVID Impact",
    # Section V appendix — products under review + pending proposals.
    # Emitted last per the original appendix discipline. The A.1 / A.2
    # codes REUSE the Section III prefix; the doc's Roman-numeral parent
    # section disambiguates ("Section III, A.1" vs "Section V, A.1";
    # "Section III, A.2" vs "Section V, A.2").
    "Under Review",
    "Pending Proposals",
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
    "Graduate Students",
    "Postdocs and Visiting Scholars",
    "Undergraduate Students",
    "Undergraduate Research Pathways",
    "Undergraduate Research Pathways",
    "Undergraduate Research Products",
    "Undergraduate Student Awards",
    "Graduate Student Awards",
    "Courses Taught",
    "Course Development",
    "Patents",
    "Entrepreneurial Activities",
    "Technology Transfer",
    "Software Products",
    "University Service",
    "Profession Service",
    "National Service",
    "Other Service",
]

SECTION_CODES: dict[Section, str] = {
    # Section III front matter — A.X codes shared with Section V; the
    # Roman parent disambiguates at cross-ref render time.
    "Identifiers": "A.1",
    "Degrees": "A.2",
    "Positions at Purdue": "A.3",
    "Positions at Other Institutions": "A.4",
    "Licenses": "A.5",
    "Awards": "A.6",
    "Professional Memberships": "A.7",
    "B1 Summary":         "B.1",
    "B2 Impact":          "B.2",
    "B3 Vision":          "B.3",
    "B4 External Events": "B.4",
    "B5 COVID Impact":    "B.5",
    # Section V appendix — bare codes A.1 / A.2 too; cross-refs render
    # with the explicit "Section V, " prefix to disambiguate from
    # Section III's A.1 / A.2 above. Bookmarks for Section V entries
    # use the "V." prefix so they don't collide with Section III A.2
    # Degrees bookmarks at the same numeric code.
    "Under Review":      "A.1",
    "Pending Proposals": "A.2",
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
    "Graduate Students": "C.14",
    "Postdocs and Visiting Scholars": "C.15",
    "Undergraduate Students": "C.16",
    "Undergraduate Research Pathways": "C.16.2.2",
    "Undergraduate Research Products": "C.16.2.3",
    "Undergraduate Student Awards": "C.16.2.4",
    "Graduate Student Awards": "C.16.3.2",
    "Courses Taught": "C.17",
    "Course Development": "C.18",
    "Patents": "C.19",
    "Entrepreneurial Activities": "C.20",
    "Technology Transfer": "C.21",
    "Software Products": "C.22",
    "University Service": "C.23",
    "Profession Service": "C.24",
    "National Service": "C.25",
    "Other Service": "C.26",
}

SECTION_HEADINGS: dict[Section, str] = {
    # Section III front matter — text mirrors the Purdue template
    # sub-section labels (matches the user's source-of-truth screenshots).
    "Identifiers": "Name and any appropriate scholarly identifiers (include ORCID).",
    "Degrees": "Degrees.",
    "Positions at Purdue": "Positions at Purdue.",
    "Positions at Other Institutions": "Positions at other institutions or organizations.",
    "Licenses": "Licenses, registrations, and certificates.",
    "Awards": "Recognitions (honors and awards) and the significance of these recognitions.",
    "Professional Memberships": "Membership in professional organizations.",
    # Section IV (Self-Evaluation) — text from the Purdue template.
    "B1 Summary":         "Summary of achievements.",
    "B2 Impact":          "Impact of accomplishments.",
    "B3 Vision":          "Vision.",
    "B4 External Events": "Candidate comments on any external events or issues that have impacted their productivity.",
    "B5 COVID Impact":    "Professional COVID-19 Impact Statement (optional).",
    # NOTE: every C.X heading text MUST end in a period (Purdue P&T
    # convention — matches the screenshots the user shared for the
    # template's heading style). Pinned by
    # `test_every_c_x_section_heading_ends_in_period` in test_config.py.
    "Key Works": "Key Scholarly Publications or Patents.",
    "Journals": "Refereed journal papers.",
    "Books and Chapters": "Books and chapters in books.",
    "Conferences and Workshops": "Refereed conferences, symposium papers or other refereed reports.",
    "Other publications and products": "Other publications and products.",
    "Invited Talks": "Invited external keynote/conference/symposium/colloquium/seminar presentations.",
    "Leadership Roles": "Leadership roles in government or professional organizations.",
    "Media Appearances": "Appearances in media interviews and other coverage.",
    "Conference Presentations": "Selected contributed conference/symposium presentations where the candidate was the presenter.",
    "Grants PI": "Externally sponsored grants as PI, or Purdue lead on multi-institution grants.",
    "Grants Co-PI": "Externally sponsored grants as Co-PI or Co-I.",
    "Gifts": "External gifts and voluntary support.",
    "Internal Grants": "Internal competitive grants as PI or Co-PI.",
    "Graduate Students": "Graduate students advised.",
    "Postdocs and Visiting Scholars": "Mentoring of postdoctoral and visiting faculty scholars the candidate has directly supervised.",
    "Undergraduate Students": "Graduate or undergraduate student mentoring activities and outcomes.",
    "Undergraduate Research Pathways": "Other Undergraduate Research Pathways.",
    "Undergraduate Research Products": "Undergraduate Research Products and Authorship.",
    "Undergraduate Student Awards": "Undergraduate Awards, Fellowships, and Career Development.",
    "Graduate Student Awards": "Graduate Student Awards and Fellowships.",
    "Courses Taught": "Courses taught at Purdue and elsewhere and teaching scores.",
    "Course Development": "Course development, within Purdue, or external short courses and workshops taught.",
    "Patents": "Issued U.S. and International Patents.",
    "Entrepreneurial Activities": "Major entrepreneurial activities.",
    "Technology Transfer": "Technology transfer to industry practice, non-profits, or government policy.",
    "Software Products": "Software products.",
    "University Service": "Service to Purdue.",
    "Profession Service": "Service to the profession through professional societies.",
    "National Service": "Service to State, Nation, or International Organizations.",
    "Other Service": "Other external service activities to the profession not noted above.",
    # Section V appendix — emits LAST, so declared LAST in this dict so
    # the iteration order matches emission order. The A.1 / A.2 codes
    # collide with the Section III front matter (Identifiers / Degrees)
    # but disambiguate at the dict-key level (Under Review / Pending
    # Proposals) and at the rendered substring level (different heading
    # text).
    "Under Review": "Products under review (e.g. papers, books, software).",
    "Pending Proposals": "Pending proposals.",
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
    "Preprint": "Technical report",
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
# Section III front matter source (A.1-A.7). Override via CLI --candidate-info.
# Missing file → front matter is silently skipped (no error).
DEFAULT_CANDIDATE_INFO_FILE = "assets/candidate-information.yaml"
# EvaluationKit raw-data CSV → C.17 CourseTaught rows. Override via CLI
# --evaluationkit-rawdata. Missing file → CSV-derived rows are silently
# skipped (the YAML-loaded `courses_taught:` list still emits).
DEFAULT_EVALUATIONKIT_RAWDATA_FILE = "assets/evaluationkit-rawdata.csv"
# Section IV self-evaluation source (B.1-B.5). Override via CLI
# --self-eval. Missing file → B section silently skipped.
DEFAULT_SELF_EVALUATION_FILE = "assets/self-evaluation.md"
# Crossref-backfilled page-count cache (populated by
# `tools/crossref_pages_backfill.py`). Missing file → silent no-op at
# render time. Override via CLI --pages-cache. Pass empty string to
# disable the merge entirely.
DEFAULT_PAGES_CACHE_FILE = "assets/page-cache.yaml"
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
