"""Typed data containers + type aliases shared across the package."""
from __future__ import annotations

from typing import Any, Callable, Literal, NamedTuple, Optional


Category = Literal[
    "Journals",
    "Conferences and Workshops",
    "arXiv / Preprints",
]

# Open enum (not Literal): unranked venues are caught at lookup time. Values
# in practice are "Rank 1" | "Rank 2" | "Rank 3" | "Workshop" | "Magazine"
# | "Preprint" | "CVE".
Rank = str

Section = Literal[
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
    "Undergraduate Students",
    "Patents",
    "University Service",
    "Profession Service",
    "National Service",
    "Other Service",
]

StudentType = Literal["G", "U"]

BibEntry = dict[str, str]


class Citation(NamedTuple):
    section: Section
    rank: Rank
    year: int           # sort key; 9999 for "In Press" / unparseable
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
    co_inventors: str   # already RTF-marked (\b for me); do NOT escape at render
    date: str
    number: str
    impact: str


class InvitedTalk(NamedTuple):
    """A C.6 invited talk. All talks are type 'Seminar' for now."""
    year: int           # sort key (first 4-digit year in year_str for ranges)
    year_str: str       # display, e.g. "2024" or "Annual, 2015-2019"
    topic: str          # main subject; e.g. "Regular Expression Denial of Service"
    subtitle: str       # optional specific talk title within that topic ("" if none)
    venue: str          # institution / forum + location


class LeadershipRole(NamedTuple):
    """A C.7 leadership role (workshop chair, journal editor, etc.)."""
    year: int           # sort key
    year_str: str       # display
    role: str           # "Co-Chair", "Organizer", "Mentor", "Editor", etc.
    description: str    # the event/journal/committee name + venue context
    society: str        # affiliated professional society ("ACM SIGSOFT", "IEEE", "ASEE")


class MediaAppearance(NamedTuple):
    """A C.8 media interview / podcast / coverage appearance."""
    year: int           # sort key
    year_str: str       # display
    title: str          # the episode/article/segment title
    venue: str          # podcast/publication/show name
    url: str            # optional link


class ConferencePresentation(NamedTuple):
    """A C.9 contributed conference talk; venue/year/code come from the linked paper."""
    paper_title: str    # exact bib title (case+whitespace insensitive)


class Student(NamedTuple):
    """A C.14 graduate student record. Related publications are auto-derived."""
    grad_year: int           # sort key (use 9999 for ongoing students)
    grad_display: str        # "2025 Spring" / "ongoing" / etc.
    name: str
    degree: str              # "PhD" | "PhD candidate" | "PhD student" | "MSc" | "DEng" | "MS-Thesis" | "MS-Non-Thesis"
    role: str                # "Chair" | "Co-Chair" | "Committee member"
    position: str            # current job + affiliation (optional)
    co_advisor: str = ""     # set when role == "Co-Chair"; renders "(with NAME)" in Role column


class Grant(NamedTuple):
    """A C.10/C.11/C.12/C.13 grant entry (same datatype, routed by YAML key).

    inspired_by + publication_outcomes are paper-title lists that get
    resolved against the bib (case+whitespace insensitive) to C.X.Y refs.
    """
    start_year: int               # sort key + display
    end_year: int                 # display
    title: str                    # grant title (e.g. "CAREER: PTM-SEER: ...")
    agency: str                   # full name (e.g. "US National Science Foundation")
    agency_short: str             # short form for prefix (e.g. "NSF")
    grant_number: str             # funder's award ID (e.g. "2541917")
    role: str                     # "PI" / "Co-PI" / "Co-I" / "Sole PI" / etc.
    co_pis: list[str]             # other co-PIs (when user is PI)
    lead_pi: str                  # name of the lead PI (when user is Co-PI/Co-I)
    responsibility_percent: int   # user's responsibility share (0 = unspecified)
    amount: int                   # USD; enables per-section "Total amount" computation
    activities: str               # optional multi-sentence description
    responsibility: str           # optional free-text role/percent statement
    inspired_by: list[str]        # bib titles that motivated this grant → C.X.Y refs
    publication_outcomes: list[str]  # bib titles funded by this grant → C.X.Y refs


class KeyWork(NamedTuple):
    """A paper designated as a key/highlight scholarly publication (C.1 entry)."""
    citation: Citation   # the resolved paper's standard Citation
    impact: str          # 100-word impact statement


class ServiceEntry(NamedTuple):
    """A C.23–C.26 service activity (university, profession, national, other).

    `description` carries the full role+venue string (e.g. "PC Member, ICSE").
    `year_str` is the display string: "2025", "2025, 2026, 2027",
    "2024-2025", "2023-present", or "" for ongoing service with no fixed date
    (e.g. journal reviewing).
    """
    year: int            # sort key; 9999 when year_str is empty
    year_str: str        # display
    description: str


class NetworkTask(NamedTuple):
    """One unit of network work to be dispatched in parallel.

    `kind` selects which SQLite cache table the result writes into;
    `key` is the primary key for that cache row;
    `fetcher` is the pure network call (no DB writes — main thread commits).
    """
    kind: str                       # "doi" | "cve" | "patent"
    key: str
    fetcher: Callable[[], Any]


Publications = dict[Section, list[Citation]]
