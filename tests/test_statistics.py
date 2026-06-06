"""Isolated tests for pubs_emitter.statistics.

Each test pins one invariant on a minimal synthetic StatsContext —
no bib parsing, no RTF rendering, no document-level dependencies.
The macro registry's contract is the test surface; the rest of the
codebase consumes it via `compute_all` + `substitute`.
"""
from __future__ import annotations

import logging
import sqlite3
from unittest.mock import MagicMock

import pytest

from pubs_emitter.statistics import (
    MACROS,
    StatsContext,
    compute_all,
    register,
    substitute,
)
from pubs_emitter.types import Citation, Grant, InvitedTalk, Patent, ServiceEntry


_counter = [0]


def _cit(*, rank="Rank 1", year=2024, title=None) -> Citation:
    """Minimal Citation factory; only the fields the statistics
    computers read are populated. Each call gets a unique title so
    the `title_to_bib` lookup can be controlled per-citation in
    tests that exercise the student-led path."""
    _counter[0] += 1
    return Citation(
        section="Journals",
        rank=rank,
        year=year,
        year_str=str(year),
        authors_rtf="Author, A.",
        title=title or f"Paper number {_counter[0]}",
        venue="V",
        details="",
        link="",
        back_ref_title=None,
    )


def _bib(title: str, author: str) -> dict:
    """Minimal bib-entry shape — bibtexparser-style dict with the
    fields the statistics module reads."""
    return {"title": title, "author": author}


def _grant(*, role: str = "PI", purdue_amount: int = 0, my_amount: int = 0) -> Grant:
    """Minimal Grant tuple — only the fields the funding macros read are
    meaningful. Everything else is set to type-zero defaults so the
    NamedTuple validates."""
    return Grant(
        start_year=2024, end_year=2025, title="t", agency="a",
        agency_short="", grant_number="", role=role, lead_institution="",
        personnel=[], responsibility_percent=0,
        total_amount=0, purdue_amount=purdue_amount, my_amount=my_amount,
        activities="", responsibility="",
        inspired_by=[], publication_outcomes=[],
    )


def _conn_with_students(students: dict[str, str]) -> sqlite3.Connection:
    """Build an in-memory students table matching `lookup_student_type`'s
    contract: SELECT name, type FROM students."""
    c = sqlite3.connect(":memory:")
    c.execute("CREATE TABLE students (name TEXT, type TEXT)")
    for name, t in students.items():
        c.execute("INSERT INTO students VALUES (?, ?)", (name, t))
    c.commit()
    return c


# ----- Registry well-formedness ------------------------------------------


class TestRegistry:
    """The macro registry must follow naming convention + uniqueness."""

    def test_every_macro_name_is_uppercase_with_underscores(self) -> None:
        import re
        for name in MACROS:
            assert re.match(r"^[A-Z][A-Z0-9_]*$", name), (
                f"macro {name!r} doesn't match the NAME_NAME convention"
            )

    def test_register_decorator_rejects_duplicates(self) -> None:
        """A duplicate registration is a programming error — fail loud
        so two macros don't silently overwrite each other."""
        @register("UNIQUE_TEST_MACRO_001")
        def _fn(_ctx: StatsContext) -> int:
            return 0
        with pytest.raises(RuntimeError, match="Duplicate macro"):
            @register("UNIQUE_TEST_MACRO_001")
            def _fn2(_ctx: StatsContext) -> int:
                return 0
        # Cleanup so other tests aren't affected.
        del MACROS["UNIQUE_TEST_MACRO_001"]


# ----- Individual macro computations -------------------------------------


class TestMacroComputations:
    """Each macro must compute deterministically from a known input."""

    def test_num_peer_reviewed_counts_juried_ranks_only(self) -> None:
        ctx = StatsContext(publications={
            "Journals": [_cit(rank="Rank 1"), _cit(rank="Rank 2")],
            "Books and Chapters": [_cit(rank="Book Chapter")],
            "Conferences and Workshops": [
                _cit(rank="Rank 1"), _cit(rank="Workshop"), _cit(rank="Rank 3"),
            ],
            "Other publications and products": [
                _cit(rank="Preprint"), _cit(rank="Magazine"),
                _cit(rank="CVE"), _cit(rank="Disclosure"),
            ],
        })
        assert compute_all(ctx)["NUM_PEER_REVIEWED_WORKS"] == "6"

    def test_num_works_at_purdue_filters_by_year(self) -> None:
        ctx = StatsContext(publications={
            "Journals": [
                _cit(rank="Rank 1", year=2018),
                _cit(rank="Rank 1", year=2019),
                _cit(rank="Rank 1", year=2020),  # join year — counts
                _cit(rank="Rank 1", year=2021),
                _cit(rank="Rank 2", year=2022),
            ],
        })
        # 2020/2021/2022 all count → 3.
        assert compute_all(ctx)["NUM_WORKS_AT_PURDUE"] == "3"

    def test_num_tier_1_counts_rank_1_only(self) -> None:
        ctx = StatsContext(publications={
            "Journals": [
                _cit(rank="Rank 1"), _cit(rank="Rank 1"),
                _cit(rank="Rank 2"), _cit(rank="Workshop"),
            ],
        })
        result = compute_all(ctx)
        assert result["NUM_TIER_1"] == "2"
        assert result["NUM_TIER_2"] == "1"

    def test_num_student_led_uses_first_author_lookup(self) -> None:
        conn = _conn_with_students({"Grad Student": "G", "Undergrad Person": "U"})
        cits = [
            _cit(rank="Rank 1", title="grad-led-tier1"),
            _cit(rank="Rank 1", title="non-student-led"),       # student NOT first
            _cit(rank="Rank 1", title="undergrad-led-tier1"),
            _cit(rank="Rank 2", title="grad-led-tier2"),         # peer reviewed
        ]
        bib_entries = [
            _bib("grad-led-tier1",       "Grad Student and Other Davis"),
            _bib("non-student-led",      "Other Davis and Grad Student"),
            _bib("undergrad-led-tier1",  "Undergrad Person"),
            _bib("grad-led-tier2",       "Grad Student"),
        ]
        ctx = StatsContext(
            publications={"Journals": cits},
            bib_entries=bib_entries,
            conn=conn,
        )
        result = compute_all(ctx)
        # 3 student-led: 2 grad-led (Rank 1 + Rank 2) + 1 undergrad-led
        # (Rank 1). The "non-student-led" entry has the student in
        # second position so it doesn't count.
        assert result["NUM_STUDENT_LED"] == "3"
        # Of those, Rank 1 only: grad-led-tier1 + undergrad-led-tier1 = 2.
        assert result["NUM_STUDENT_LED_TIER_1"] == "2"

    def test_num_student_led_returns_zero_without_conn(self) -> None:
        cit = _cit(rank="Rank 1", title="paper")
        ctx = StatsContext(
            publications={"Journals": [cit]},
            bib_entries=[_bib("paper", "Student Name")],
            # No conn passed — student-led detection should return 0.
        )
        assert compute_all(ctx)["NUM_STUDENT_LED"] == "0"

    def test_num_papers_with_undergraduate_coauthors(self) -> None:
        """Any U-student anywhere in the author list counts the paper.
        Distinct from NUM_STUDENT_LED (which requires the U/G student
        be FIRST). Non-peer-reviewed (e.g. CVE) papers excluded even if
        they have a U co-author."""
        conn = _conn_with_students({
            "Grad Person": "G",
            "Undergrad One": "U",
            "Undergrad Two": "U",
        })
        cits = [
            _cit(rank="Rank 1", title="undergrad-coauth-mid"),
            _cit(rank="Workshop", title="undergrad-lead"),
            _cit(rank="Rank 2", title="grad-only"),
            _cit(rank="Rank 1", title="no-students"),
            _cit(rank="CVE", title="cve-with-undergrad"),
        ]
        bib_entries = [
            _bib("undergrad-coauth-mid", "Other Davis and Undergrad One and Grad Person"),
            _bib("undergrad-lead",       "Undergrad Two and Other Davis"),
            _bib("grad-only",            "Grad Person and Other Davis"),
            _bib("no-students",          "Other Davis and Other Other"),
            _bib("cve-with-undergrad",   "Undergrad One and Other Davis"),
        ]
        ctx = StatsContext(
            publications={"Journals": cits},
            bib_entries=bib_entries,
            conn=conn,
        )
        # undergrad-coauth-mid (Rank 1, U mid) + undergrad-lead (Workshop, U first) = 2
        # grad-only and no-students excluded (no U); cve-with-undergrad excluded (not peer reviewed)
        assert compute_all(ctx)["NUM_PAPERS_WITH_UNDERGRADUATE_COAUTHORS"] == "2"

    def test_num_undergraduate_coauthors_counts_distinct_people(self) -> None:
        """Distinct count of U-students seen across peer-reviewed papers.
        Companion to NUM_PAPERS_WITH_UNDERGRADUATE_COAUTHORS (which counts
        papers). Repeated co-authorship by the same student counts ONCE;
        CVE-only co-authors are excluded (not peer reviewed)."""
        conn = _conn_with_students({
            "Grad Person": "G",
            "Undergrad One": "U",
            "Undergrad Two": "U",
            "Undergrad Three (CVE only)": "U",
        })
        cits = [
            _cit(rank="Rank 1", title="paper-a"),
            _cit(rank="Workshop", title="paper-b"),
            _cit(rank="Rank 2", title="paper-c"),
            _cit(rank="Rank 1", title="paper-d-repeat"),
            _cit(rank="CVE", title="cve-only-u"),
        ]
        bib_entries = [
            # paper-a: Undergrad One (peer-reviewed) → counted
            _bib("paper-a", "Other Davis and Undergrad One and Grad Person"),
            # paper-b: Undergrad Two (peer-reviewed) → counted
            _bib("paper-b", "Undergrad Two and Other Davis"),
            # paper-c: no undergrads
            _bib("paper-c", "Grad Person and Other Davis"),
            # paper-d-repeat: Undergrad One again → already in the set
            _bib("paper-d-repeat", "Undergrad One and Other Davis"),
            # cve-only-u: Undergrad Three but NOT peer-reviewed → excluded
            _bib("cve-only-u", "Undergrad Three (CVE only) and Other Davis"),
        ]
        ctx = StatsContext(
            publications={"Journals": cits},
            bib_entries=bib_entries,
            conn=conn,
        )
        # Distinct U-students across peer-reviewed: {Undergrad One, Undergrad Two} = 2.
        assert compute_all(ctx)["NUM_UNDERGRADUATE_COAUTHORS"] == "2"

    def test_num_undergraduate_coauthors_zero_when_no_conn(self) -> None:
        """No SQLite connection → no student-type lookups → 0 by safety
        (same posture as `_has_undergraduate_coauthor`)."""
        ctx = StatsContext(publications={}, conn=None)
        assert compute_all(ctx)["NUM_UNDERGRADUATE_COAUTHORS"] == "0"

    def test_num_patents_counts_patent_list(self) -> None:
        patents: list[Patent] = [
            MagicMock(spec=Patent), MagicMock(spec=Patent), MagicMock(spec=Patent),
        ]
        ctx = StatsContext(publications={}, patents=patents)
        assert compute_all(ctx)["NUM_PATENTS"] == "3"

    def test_total_external_funding_sums_attributable_share(self) -> None:
        """Sum prefers `my_amount` when set, falls back to `purdue_amount`.
        Includes grants_as_pi + grants_as_co_pi + gifts. Excludes
        internal_grants (the field doesn't exist on StatsContext —
        callers route them elsewhere)."""
        ctx = StatsContext(
            publications={},
            grants_as_pi=[
                _grant(role="PI", purdue_amount=687140, my_amount=0),     # single-PI fallback to purdue
                _grant(role="PI", purdue_amount=274000, my_amount=274000),
            ],
            grants_as_co_pi=[
                _grant(role="Co-PI", purdue_amount=149976, my_amount=74988),  # split-credit
            ],
            gifts=[
                _grant(role="PI", purdue_amount=5000, my_amount=0),       # OpenAI-style fallback
                _grant(role="Co-PI", purdue_amount=200000, my_amount=100000),
            ],
        )
        # 687,140 + 274,000 + 74,988 + 5,000 + 100,000 = 1,141,128
        assert compute_all(ctx)["TOTAL_EXTERNAL_FUNDING"] == "1,141,128"

    def test_total_external_funding_as_pi_filters_by_role(self) -> None:
        ctx = StatsContext(
            publications={},
            grants_as_pi=[
                _grant(role="PI", purdue_amount=687140, my_amount=0),
                _grant(role="Co-PI", purdue_amount=274000, my_amount=137000),  # role=Co-PI excluded
            ],
            gifts=[
                _grant(role="PI", purdue_amount=5000, my_amount=0),
            ],
        )
        # 687,140 + 5,000 = 692,140 (Co-PI entry excluded)
        assert compute_all(ctx)["TOTAL_EXTERNAL_FUNDING_AS_PI"] == "692,140"

    def test_external_funding_empty_when_no_grants(self) -> None:
        ctx = StatsContext(publications={})
        result = compute_all(ctx)
        assert result["TOTAL_EXTERNAL_FUNDING"] == "0"
        assert result["TOTAL_EXTERNAL_FUNDING_AS_PI"] == "0"

    def test_num_tier_1_pcs_counts_year_appointments(self) -> None:
        """Each (Tier-1 venue, year) pair contributes ONE appointment.
        A PC entry "ICSE" with year_str "2025, 2026, 2027" counts as
        3. Non-Tier-1 venues are excluded."""
        entries = [
            ServiceEntry(
                year=2025,
                year_str="2025, 2026, 2027",
                description="Member of Program Committee, ICSE",
            ),
            ServiceEntry(
                year=2024,
                year_str="2024",
                description="Member of Program Committee, AAAI",
            ),
            ServiceEntry(
                year=2023,
                year_str="2023",
                description="Member of Program Committee, SCORED",  # not Tier 1
            ),
            ServiceEntry(
                year=2024,
                year_str="2024",
                description="Reviewer, ICSE–Doctoral Symposium Track",  # not PC
            ),
        ]
        ctx = StatsContext(publications={}, profession_service=entries)
        # ICSE: 3 + AAAI: 1 = 4
        assert compute_all(ctx)["NUM_TIER_1_CONFERENCE_PROGRAM_COMMITTEES"] == "4"

    def test_num_invited_talks_rounddown_nearest_ten(self) -> None:
        """27 talks → 20, 30 → 30, 9 → 0."""
        for n, expected in [(0, 0), (5, 0), (9, 0), (10, 10), (24, 20), (27, 20), (30, 30), (33, 30)]:
            ctx = StatsContext(
                publications={},
                invited_talks=[
                    MagicMock(spec=InvitedTalk) for _ in range(n)
                ],
            )
            assert compute_all(ctx)["NUM_INVITED_TALKS_ROUNDDOWN_NEAREST_TEN"] == str(expected), (
                f"{n} talks should round down to {expected}"
            )


# ----- Substitution ------------------------------------------------------


class TestSubstitute:
    """Token replacement + unresolved-token warning behavior."""

    def test_replaces_known_macros(self) -> None:
        macros = {"NUM_A": "5", "NUM_B": "12"}
        text = "We have #NUM_A apples and #NUM_B bananas."
        out, unresolved = substitute(text, macros)
        assert out == "We have 5 apples and 12 bananas."
        assert unresolved == set()

    def test_unresolved_tokens_left_as_is_and_returned(self) -> None:
        """Unresolved tokens stay in the output AND surface in the
        returned set so the caller can fail the build."""
        macros = {"NUM_KNOWN": "5"}
        text = "Resolved #NUM_KNOWN but not #NUM_TYPO."
        out, unresolved = substitute(text, macros)
        assert out == "Resolved 5 but not #NUM_TYPO."
        assert unresolved == {"NUM_TYPO"}

    def test_unresolved_token_logs_warning(self, caplog) -> None:
        with caplog.at_level(logging.WARNING):
            substitute("see #NUM_TYPO", {})
        assert any("NUM_TYPO" in r.message for r in caplog.records)

    def test_unresolved_warning_emitted_once_per_token(
        self, caplog,
    ) -> None:
        """Same unresolved token mentioned 3 times → 1 warning, not 3."""
        with caplog.at_level(logging.WARNING):
            substitute("#NUM_TYPO and #NUM_TYPO again #NUM_TYPO", {})
        warns = [r for r in caplog.records if "NUM_TYPO" in r.message]
        assert len(warns) == 1

    def test_lowercase_hash_tokens_not_matched(self) -> None:
        """Token shape is `#UPPER` — `#heading` (lowercase) is left
        alone so the substitution doesn't eat markdown-ish text."""
        text = "Plain #heading text"
        out, unresolved = substitute(text, {"HEADING": "X"})
        assert out == "Plain #heading text"
        assert unresolved == set()

    def test_compute_all_is_stable_order(self) -> None:
        """`compute_all` returns a dict in sorted macro-name order so
        the log output + downstream consumers see deterministic
        ordering across runs."""
        ctx = StatsContext(publications={})
        result = compute_all(ctx)
        names = list(result.keys())
        assert names == sorted(names)
