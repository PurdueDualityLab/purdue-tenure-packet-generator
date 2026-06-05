"""Macro substitution for self-evaluation prose.

The B-section statements reference summary statistics like
`#NUM_PEER_REVIEWED_WORKS` and `#NUM_TIER_1` that must be computed
DETERMINISTICALLY from the bib + paper_index data so the candidate
doesn't have to recount the publication record by hand on every
revision.

Architecture:

* `MACROS` is a registry mapping macro name → computer function. Each
  computer takes a `StatsContext` and returns an int (which renders
  as the string form) OR a pre-formatted string. New macros are added
  by appending to the registry — no other code change.

* `StatsContext` is a thin namespace bundling everything a computer
  function might need: publications, bib_entries, paper_index, conn
  (for student-type lookup), patents, etc. Adding a new data source
  is one field on the namespace.

* `compute_all(ctx) -> dict[str, str]` resolves every registered
  macro against the context. Stable order; deterministic output.

* `substitute(text, macros) -> str` replaces `#MACRO_NAME` occurrences
  in arbitrary text. Unresolved `#NUM_*` tokens are logged as
  warnings so a typo in a statement doesn't silently render as the
  raw token.

Add a new macro:

    @register("NUM_INVITED_TALKS")
    def _num_invited_talks(ctx: StatsContext) -> int:
        return len(ctx.invited_talks or [])

The registry decorator handles the rest. Computer functions MUST be
pure (no I/O) and deterministic (no clock, no random).
"""
from __future__ import annotations

import logging
import re
import sqlite3
from dataclasses import dataclass, field
from typing import Callable, Optional, Union

from .authors import lookup_student_type, parse_name_parts
from .types import (
    BibEntry, Citation, InvitedTalk, Patent, Publications, ServiceEntry,
)
from .venue import normalize_title


log = logging.getLogger(__name__)


# Year Davis joined Purdue — the "at Purdue" cutoff for
# NUM_WORKS_AT_PURDUE. If the candidate is someone else, update this
# constant (or move to config); for now hard-coded since the renderer
# is single-user.
_PURDUE_JOIN_YEAR = 2020


@dataclass
class StatsContext:
    """Bundle of everything a macro-computer might need. Build once at
    render time after paper_index is populated.

    `title_to_bib` is derived from `bib_entries` at construction time
    so a per-citation first-author lookup is O(1) instead of O(N) per
    macro call.
    """
    publications: Publications
    bib_entries: list[BibEntry] = field(default_factory=list)
    paper_index: dict[str, str] = field(default_factory=dict)
    conn: Optional[sqlite3.Connection] = None
    patents: list[Patent] = field(default_factory=list)
    invited_talks: list[InvitedTalk] = field(default_factory=list)
    profession_service: list[ServiceEntry] = field(default_factory=list)
    title_to_bib: dict[str, BibEntry] = field(default_factory=dict, init=False)

    def __post_init__(self) -> None:
        self.title_to_bib = {
            normalize_title(e.get("title", "") or ""): e
            for e in self.bib_entries
            if e.get("title")
        }


MacroValue = Union[int, str]
MacroFn = Callable[[StatsContext], MacroValue]
MACROS: dict[str, MacroFn] = {}


def register(name: str) -> Callable[[MacroFn], MacroFn]:
    """Decorator to register a macro computer.

    Names are bare (no leading `#`) and uppercase-with-underscores per
    the substitution convention. A duplicate registration is a
    programming error — fail loud."""
    def _wrap(fn: MacroFn) -> MacroFn:
        if name in MACROS:
            raise RuntimeError(f"Duplicate macro registration: {name}")
        MACROS[name] = fn
        return fn
    return _wrap


# ----- Predicates over publications --------------------------------------


# Publication ranks that count as "peer reviewed" for tenure-statement
# purposes. Tier 1-3 + Workshop + Book Chapter are juried; Magazine,
# Preprint, CVE, Disclosure, Dissertation are not.
_PEER_REVIEWED_RANKS: frozenset[str] = frozenset({
    "Rank 1", "Rank 2", "Rank 3", "Workshop", "Book Chapter",
})


def _all_publications(ctx: StatsContext) -> list[Citation]:
    """Flat list of every Citation across every section. Stable order
    by section iteration order."""
    out: list[Citation] = []
    for section, cits in ctx.publications.items():
        out.extend(cits)
    return out


def _is_peer_reviewed(cit: Citation) -> bool:
    return cit.rank in _PEER_REVIEWED_RANKS


def _is_at_purdue(cit: Citation) -> bool:
    return cit.year >= _PURDUE_JOIN_YEAR


def _is_student_led(cit: Citation, ctx: StatsContext) -> bool:
    """First author of the citation is a student (G or U type) per the
    student registry. Looks up the bib entry by normalized title to
    read the author field (Citation doesn't carry raw author tuple).
    Returns False when no conn / no matching bib entry / no first
    author is parseable."""
    if ctx.conn is None:
        return False
    bib = ctx.title_to_bib.get(normalize_title(cit.title))
    if not bib:
        return False
    raw_authors = bib.get("author", "") or ""
    if not raw_authors:
        return False
    first = raw_authors.split(" and ", 1)[0].strip()
    return lookup_student_type(ctx.conn, first) in ("G", "U")


# ----- Registered macros -------------------------------------------------


@register("NUM_PEER_REVIEWED_WORKS")
def _num_peer_reviewed_works(ctx: StatsContext) -> int:
    """Count of publications whose rank is one of the peer-reviewed
    tiers (Tier 1-3 + Workshop + Book Chapter)."""
    return sum(1 for c in _all_publications(ctx) if _is_peer_reviewed(c))


@register("NUM_WORKS_AT_PURDUE")
def _num_works_at_purdue(ctx: StatsContext) -> int:
    """Peer-reviewed publications since Davis joined Purdue (2020+)."""
    return sum(
        1 for c in _all_publications(ctx)
        if _is_peer_reviewed(c) and _is_at_purdue(c)
    )


@register("NUM_TIER_1")
def _num_tier_1(ctx: StatsContext) -> int:
    """Tier-1 conference / journal papers (rank "Rank 1")."""
    return sum(1 for c in _all_publications(ctx) if c.rank == "Rank 1")


@register("NUM_TIER_2")
def _num_tier_2(ctx: StatsContext) -> int:
    """Tier-2 conference / journal papers (rank "Rank 2")."""
    return sum(1 for c in _all_publications(ctx) if c.rank == "Rank 2")


@register("NUM_STUDENT_LED")
def _num_student_led(ctx: StatsContext) -> int:
    """Peer-reviewed publications where the first author is one of
    Davis's students (G or U type)."""
    return sum(
        1 for c in _all_publications(ctx)
        if _is_peer_reviewed(c) and _is_student_led(c, ctx)
    )


@register("NUM_STUDENT_LED_TIER_1")
def _num_student_led_tier_1(ctx: StatsContext) -> int:
    """Student-led Tier-1 papers."""
    return sum(
        1 for c in _all_publications(ctx)
        if c.rank == "Rank 1" and _is_student_led(c, ctx)
    )


@register("NUM_PATENTS")
def _num_patents(ctx: StatsContext) -> int:
    """Count of issued patents in the C.19 list."""
    return len(ctx.patents)


# ----- Service / talks --------------------------------------------------


# Tier-1 software-engineering / security / systems venues whose
# Program Committee service "counts" for the B.1 tenure statement.
# Match is case-insensitive on the venue name (after the comma in
# "Member of Program Committee, {venue}"). Extending the list is
# additive — bump a venue here when its tiering changes.
TIER_1_PC_VENUES: frozenset[str] = frozenset({
    "icse", "esec/fse", "fse", "ase", "issta",
    "usenix security", "ieee s&p", "ccs", "ndss",
    "eurosys", "usenix atc", "vldb", "www",
    "popl", "pldi", "oopsla",
    "aaai",
})


_PC_DESCRIPTION_PREFIX = "Member of Program Committee, "


def _count_pc_year_appointments(entry: ServiceEntry) -> int:
    """Each profession_service entry's `year_str` may carry multiple
    comma-separated years (e.g. `"2021, 2024"` = two appointments) or
    a single year (`"2025"`). Return the count of distinct year
    tokens. Empty / unparseable → 0."""
    raw = entry.year_str or ""
    if not raw:
        return 0
    parts = [p.strip() for p in raw.split(",")]
    return sum(1 for p in parts if p)


def _extract_pc_venue(description: str) -> Optional[str]:
    """Pull the venue acronym out of a PC service description.

    Descriptions arrive post-`expand_venue_acronyms`, so they look like
    "Member of Program Committee, International Conference on Software
    Engineering (ICSE)". The match priority:
      1. Trailing parenthetical (the expanded form): `(ICSE)` → "icse".
      2. Bare venue if no parens (acronym wasn't in VENUE_FULL_NAMES):
         "Member of Program Committee, SCORED" → "scored".
    Returns lowercased venue token, or None if description doesn't
    start with the PC prefix.
    """
    if not description.startswith(_PC_DESCRIPTION_PREFIX):
        return None
    tail = description[len(_PC_DESCRIPTION_PREFIX):].strip().rstrip(".")
    # Trailing parenthetical → use the acronym inside.
    m = re.search(r"\(([^)]+)\)\s*$", tail)
    if m:
        return m.group(1).strip().lower()
    return tail.lower()


@register("NUM_TIER_1_CONFERENCE_PROGRAM_COMMITTEES")
def _num_tier_1_pcs(ctx: StatsContext) -> int:
    """Total Tier-1 PC appointments — each (venue, year) pair counts.
    A profession_service entry "Member of Program Committee, ICSE"
    with year "2025, 2026, 2027" contributes 3 to the count."""
    total = 0
    for entry in ctx.profession_service:
        venue = _extract_pc_venue(entry.description or "")
        if venue is None or venue not in TIER_1_PC_VENUES:
            continue
        total += _count_pc_year_appointments(entry)
    return total


def _round_down_to_nearest(n: int, step: int) -> int:
    """Standard floor-to-multiple: 27 // 10 * 10 = 20."""
    if step <= 0:
        return n
    return (n // step) * step


@register("NUM_INVITED_TALKS_ROUNDDOWN_NEAREST_TEN")
def _num_invited_talks_rounddown(ctx: StatsContext) -> int:
    """Invited-talk count rounded DOWN to the nearest ten. The user
    prefers a soft "more than twenty" framing over an exact head count
    in B.1 prose — this macro produces the rounded-down number to use
    after "more than" (so 27 → 20, 30 → 30, 9 → 0)."""
    return _round_down_to_nearest(len(ctx.invited_talks), 10)


# ----- Compute + substitute ----------------------------------------------


def compute_all(ctx: StatsContext) -> dict[str, str]:
    """Resolve every registered macro against `ctx`. Returns a stable
    mapping macro-name → string value."""
    out: dict[str, str] = {}
    for name in sorted(MACROS):
        value = MACROS[name](ctx)
        out[name] = str(value)
    return out


# Token shape: `#NAME` where NAME is uppercase + underscores. The
# trailing word-boundary stops at any non-alnum/underscore character
# (whitespace, punctuation, end of string).
_MACRO_TOKEN_RE = re.compile(r"#([A-Z][A-Z0-9_]*)")


def substitute(text: str, macros: dict[str, str]) -> str:
    """Replace `#MACRO_NAME` tokens with their resolved values.

    Unresolved tokens (not in `macros`) are LEFT AS-IS in the output
    and logged as warnings so a typo'd macro name in a statement is
    surfaced at build time. The token regex matches uppercase-only
    names so it doesn't accidentally swallow `#Heading` markdown or
    HTML-like fragments."""
    seen_unresolved: set[str] = set()
    def _replace(m: re.Match[str]) -> str:
        name = m.group(1)
        if name in macros:
            return macros[name]
        if name not in seen_unresolved:
            log.warning("Unresolved macro #%s in statement text", name)
            seen_unresolved.add(name)
        return m.group(0)
    return _MACRO_TOKEN_RE.sub(_replace, text)
