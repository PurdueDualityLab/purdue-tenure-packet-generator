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
    "Journals",
    "Conferences and Workshops",
    "Other publications and products",
    "Patents",
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
