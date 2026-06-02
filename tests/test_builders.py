"""Unit tests for pubs_emitter.builders.

Covers small pure helpers + the per-record builders that don't require
network I/O. End-to-end `build_citation` is exercised in test_e2e.py.
"""
from __future__ import annotations

import pytest

from pubs_emitter.builders import (
    build_grant,
    build_invited_talk,
    build_leadership_role,
    build_media_appearance,
    build_service_entry,
    build_student,
    derive_section,
    escape_rtf,
    format_bib_date,
    format_details,
    format_iso_date,
    load_non_scholar,
    parse_year,
    validate_non_scholar,
)


class TestEscapeRtf:
    def test_backslash_doubled(self) -> None:
        assert escape_rtf("a\\b") == "a\\\\b"

    def test_braces_escaped(self) -> None:
        assert escape_rtf("{x}") == "\\{x\\}"

    def test_non_ascii_becomes_rtf_unicode(self) -> None:
        assert escape_rtf("Çakar") == r"\u199?akar"

    def test_ascii_passthrough(self) -> None:
        assert escape_rtf("Hello, World.") == "Hello, World."


class TestFormatDetails:
    def test_volume_only(self) -> None:
        assert format_details({"volume": "10"}) == ", 10"

    def test_volume_and_number(self) -> None:
        assert format_details({"volume": "10", "number": "3"}) == ", 10(3)"

    def test_volume_number_pages(self) -> None:
        assert format_details(
            {"volume": "10", "number": "3", "pages": "1-12"}
        ) == ", 10(3), 1-12"

    def test_pages_only(self) -> None:
        assert format_details({"pages": "1-12"}) == ", 1-12"

    def test_empty(self) -> None:
        assert format_details({}) == ""


class TestParseYear:
    def test_plain_int(self) -> None:
        assert parse_year("2025") == 2025

    def test_extracts_first_year_from_range(self) -> None:
        assert parse_year("Annual, 2015-2019") == 2015

    def test_no_year_sorts_to_end(self) -> None:
        assert parse_year("In Press") == 9999
        assert parse_year("") == 9999


class TestDeriveSection:
    def test_journal(self) -> None:
        assert derive_section("Journals", "Rank 1") == "Journals"

    def test_conference(self) -> None:
        assert derive_section("Conferences and Workshops", "Rank 1") == (
            "Conferences and Workshops"
        )

    def test_arxiv_category(self) -> None:
        assert derive_section("arXiv / Preprints", "Preprint") == (
            "Other publications and products"
        )

    def test_magazine_in_journals_category_lands_in_c5(self) -> None:
        # A 'Journals'-category entry whose rank is 'Magazine' lands in C.5.
        assert derive_section("Journals", "Magazine") == (
            "Other publications and products"
        )


class TestDateFormatting:
    def test_iso_to_long_form(self) -> None:
        assert format_iso_date("2021-11-16") == "November 16, 2021"

    def test_iso_parse_failure_returns_input(self) -> None:
        assert format_iso_date("garbage") == "garbage"

    def test_bib_date_month_and_year(self) -> None:
        assert format_bib_date({"month": "nov", "year": "2021"}) == "nov 2021"

    def test_bib_date_year_only(self) -> None:
        assert format_bib_date({"year": "2021"}) == "2021"

    def test_bib_date_tilde_collapsed(self) -> None:
        # `~` is non-breaking space in BibTeX; we collapse it to a regular space.
        assert format_bib_date({"month": "nov~", "year": "2021"}) == "nov 2021"


class TestBuildInvitedTalk:
    def test_basic(self) -> None:
        t = build_invited_talk(
            {"topic": "X", "subtitle": "", "venue": "Test U", "year": 2024}
        )
        assert t.year == 2024
        assert t.year_str == "2024"
        assert t.topic == "X"
        assert t.venue == "Test U"

    def test_year_range_parses_first_year(self) -> None:
        t = build_invited_talk(
            {"topic": "X", "venue": "V", "year": "Annual, 2015-2019"}
        )
        assert t.year == 2015
        assert t.year_str == "Annual, 2015-2019"


class TestBuildGrant:
    def test_basic_grant_fields(self) -> None:
        g = build_grant(
            {
                "title": "T",
                "agency": "NSF",
                "agency_short": "NSF",
                "grant_number": "1",
                "role": "PI",
                "start_year": 2025,
                "end_year": 2030,
                "purdue_amount": 600000,
            }
        )
        assert g.start_year == 2025
        assert g.end_year == 2030
        # Sole-PI single-institution case: total/purdue/my all default to
        # purdue_amount when not explicitly set.
        assert g.purdue_amount == 600000
        assert g.total_amount == 600000
        assert g.my_amount == 600000
        assert g.personnel == []
        assert g.lead_institution == ""
        assert g.inspired_by == []
        assert g.publication_outcomes == []

    def test_three_way_amount_breakdown(self) -> None:
        # Multi-institution collab + multi-PI: all three fields differ.
        g = build_grant(
            {
                "title": "T", "agency": "NSF", "role": "PI",
                "start_year": 2025, "end_year": 2030,
                "total_amount": 1000000,
                "purdue_amount": 500000,
                "my_amount": 250000,
            }
        )
        assert g.total_amount == 1000000
        assert g.purdue_amount == 500000
        assert g.my_amount == 250000

    def test_my_amount_defaults_to_purdue_amount(self) -> None:
        # Single-institution but multi-PI split — total == purdue, my smaller.
        # Here we only override my; total should still default to purdue.
        g = build_grant(
            {
                "title": "T", "agency": "NSF", "role": "Co-PI",
                "start_year": 2025, "end_year": 2030,
                "purdue_amount": 400000,
                "my_amount": 100000,
            }
        )
        assert g.total_amount == 400000  # defaulted to purdue
        assert g.my_amount == 100000

    def test_grant_number_coerced_to_string(self) -> None:
        # YAML often parses bare grant numbers as ints; we coerce defensively.
        g = build_grant({"grant_number": 2025001})
        assert g.grant_number == "2025001"

    def test_inspired_by_preserved(self) -> None:
        g = build_grant(
            {
                "inspired_by": ["Paper A"],
                "publication_outcomes": ["Paper B", "Paper C"],
            }
        )
        assert g.inspired_by == ["Paper A"]
        assert g.publication_outcomes == ["Paper B", "Paper C"]


class TestBuildStudent:
    def test_basic(self) -> None:
        s = build_student(
            {
                "name": "Paschal C. Amusuo",
                "degree": "PhD",
                "role": "Chair",
                "grad_year": 2025,
                "graduation": "2025 Spring",
                "position": "Software Engineer",
            }
        )
        assert s.grad_year == 2025
        assert s.grad_display == "2025 Spring"
        assert s.name == "Paschal C. Amusuo"

    def test_missing_grad_year_sorts_to_end(self) -> None:
        s = build_student({"name": "X", "degree": "PhD", "role": "Chair"})
        assert s.grad_year == 9999

    def test_latex_in_name(self) -> None:
        s = build_student(
            {
                "name": "{\\c{C}}akar, Test",
                "degree": "PhD",
                "role": "Chair",
                "grad_year": 2025,
            }
        )
        assert "Ç" in s.name  # decoded; rtf escaping happens later at render

    def test_co_advisor_default_empty(self) -> None:
        s = build_student({"name": "X", "degree": "PhD", "role": "Chair"})
        assert s.co_advisor == ""

    def test_co_advisor_read_from_yaml(self) -> None:
        s = build_student(
            {
                "name": "X", "degree": "PhD candidate", "role": "Co-Chair",
                "co_advisor": "Yung-Hsiang Lu",
            }
        )
        assert s.co_advisor == "Yung-Hsiang Lu"

    def test_co_advisor_latex_decoded(self) -> None:
        s = build_student(
            {
                "name": "X", "degree": "PhD", "role": "Co-Chair",
                "co_advisor": "{\\c{C}}akar",
            }
        )
        assert s.co_advisor == "Çakar"


class TestBuildServiceEntry:
    def test_basic_int_year(self) -> None:
        e = build_service_entry({"description": "PC Member, ICSE", "year": 2025})
        assert e.year == 2025
        assert e.year_str == "2025"
        assert e.description == "PC Member, ICSE"

    def test_multi_year_string(self) -> None:
        e = build_service_entry(
            {"description": "PC Member, ICSE", "year": "2025, 2026, 2027"}
        )
        # parse_year extracts the FIRST 4-digit year as the sort key.
        assert e.year == 2025
        assert e.year_str == "2025, 2026, 2027"

    def test_year_range_string(self) -> None:
        e = build_service_entry(
            {"description": "Member, ABET Committee", "year": "2023-present"}
        )
        assert e.year == 2023
        assert e.year_str == "2023-present"

    def test_no_year_sorts_to_end(self) -> None:
        # Journal reviewing has no year; should sort last with empty display.
        e = build_service_entry({"description": "Reviewer, IEEE TSE"})
        assert e.year == 9999
        assert e.year_str == ""

    def test_explicit_none_year(self) -> None:
        e = build_service_entry({"description": "X", "year": None})
        assert e.year == 9999
        assert e.year_str == ""

    def test_latex_decoded_in_description(self) -> None:
        e = build_service_entry(
            {"description": "Host, Dr. {\\c{C}}akar visit", "year": 2025}
        )
        assert "Ç" in e.description


class TestNormalizeUnderReviewAuthors:
    """`_normalize_under_review_authors` accepts user-CV mixed-delimiter
    forms and produces canonical BibTeX 'X and Y and Z' for format_author."""

    def _norm(self, s: str) -> str:
        from pubs_emitter.builders import _normalize_under_review_authors
        return _normalize_under_review_authors(s)

    def test_ampersand_separator(self) -> None:
        assert self._norm("Daniel Lugo & James C. Davis") == (
            "Daniel Lugo and James C. Davis"
        )

    def test_comma_and_separator(self) -> None:
        assert self._norm("Tyler Sivertsen, Neal Singh, and James C. Davis") == (
            "Tyler Sivertsen and Neal Singh and James C. Davis"
        )

    def test_plain_comma_separator(self) -> None:
        assert self._norm("Chinenye Okafor, James C. Davis, Santiago Torres-Arias") == (
            "Chinenye Okafor and James C. Davis and Santiago Torres-Arias"
        )

    def test_bare_and_separator(self) -> None:
        assert self._norm("Luiz Parente and James C. Davis") == (
            "Luiz Parente and James C. Davis"
        )

    def test_inserts_missing_initial_period(self) -> None:
        # "James C Davis" → "James C. Davis" so the bold-for-me matcher
        # against "James C. Davis" in config.yaml fires.
        assert "James C. Davis" in self._norm("Oreofe Solarin, James C Davis, Paschal C. Amusuo")

    def test_strips_trailing_sentence_period(self) -> None:
        assert self._norm("Author One and Author Two.") == (
            "Author One and Author Two"
        )

    def test_empty_string_returns_empty(self) -> None:
        assert self._norm("") == ""


class TestBuildUnderReview:
    """build_under_review wires the YAML dict through author normalization +
    format_author so role markers fire on coauthored students."""

    def test_due_date_default_sorts_last(self, conn) -> None:
        from pubs_emitter.builders import build_under_review
        u = build_under_review(conn, {
            "title": "X", "authors": "James C. Davis", "venue": "V",
        })
        assert u.due_date == "9999-99-99"

    def test_due_date_passthrough(self, conn) -> None:
        from pubs_emitter.builders import build_under_review
        u = build_under_review(conn, {
            "due_date": "2026-03-15",
            "title": "X", "authors": "James C. Davis", "venue": "V",
        })
        assert u.due_date == "2026-03-15"

    def test_bold_for_me_applied(self, conn) -> None:
        # ME isn't seeded in the test conn (only students are), but the
        # config.yaml fixture has "James C. Davis" in `me:` and that's what
        # format_author reads at import time. The seeded students table also
        # carries Amusuo so G marker should fire on him.
        from pubs_emitter.builders import build_under_review
        u = build_under_review(conn, {
            "title": "X",
            "authors": "James C. Davis and Paschal C. Amusuo",
            "venue": "V",
        })
        assert "\\b " in u.authors_rtf  # bold-for-me
        assert "\\super G" in u.authors_rtf  # grad student marker

    def test_pages_stored_verbatim(self, conn) -> None:
        from pubs_emitter.builders import build_under_review
        u = build_under_review(conn, {
            "title": "X", "authors": "James C. Davis", "venue": "V",
            "pages": "30 pages",
        })
        assert u.pages == "30 pages"


class TestValidateUnderReview:
    def test_missing_title_exits(self) -> None:
        with pytest.raises(SystemExit):
            validate_non_scholar(
                {"under_review": [{"authors": "X", "venue": "V"}]},
                [],
            )

    def test_missing_authors_exits(self) -> None:
        with pytest.raises(SystemExit):
            validate_non_scholar(
                {"under_review": [{"title": "X", "venue": "V"}]},
                [],
            )

    def test_missing_venue_exits(self) -> None:
        with pytest.raises(SystemExit):
            validate_non_scholar(
                {"under_review": [{"title": "X", "authors": "Y"}]},
                [],
            )

    def test_full_entry_passes(self) -> None:
        validate_non_scholar(
            {"under_review": [
                {"title": "X", "authors": "Y", "venue": "V", "pages": "10 pages"},
            ]},
            [],
        )


class TestBuildPatentImpactLookup:
    """build_patent looks up impact text from the patent_impacts map (keyed
    by the clean digits-only patent number). Empty map / missing key →
    blank impact, with no error."""

    def _entry(self) -> dict:
        return {
            "title": "A Test Patent",
            "author": "Davis, James C",
            "note": "US Patent 11,875,185",
            "year": "2024",
            "month": "jan",
            "publisher": "US patent",
        }

    def test_impact_populated_from_map(self) -> None:
        # We use an in-memory sqlite via the `conn` fixture (defined in
        # conftest). It has the empty patent_cache so fetch_patent_date
        # falls back to the bib date without making a network call.
        import sqlite3
        from pubs_emitter.builders import build_patent
        c = sqlite3.connect(":memory:")
        c.execute("CREATE TABLE patent_cache (patent_id TEXT PRIMARY KEY, issue_date TEXT)")
        c.execute("INSERT INTO patent_cache VALUES ('11875185', '2024-01-16')")
        c.commit()
        impacts = {"11875185": "Incorporated into IBM Spectrum Scale."}
        p = build_patent(c, self._entry(), impacts)
        assert p.impact == "Incorporated into IBM Spectrum Scale."

    def test_impact_default_blank_when_no_map(self) -> None:
        import sqlite3
        from pubs_emitter.builders import build_patent
        c = sqlite3.connect(":memory:")
        c.execute("CREATE TABLE patent_cache (patent_id TEXT PRIMARY KEY, issue_date TEXT)")
        c.execute("INSERT INTO patent_cache VALUES ('11875185', '2024-01-16')")
        c.commit()
        p = build_patent(c, self._entry(), None)
        assert p.impact == ""

    def test_impact_blank_when_number_missing_from_map(self) -> None:
        import sqlite3
        from pubs_emitter.builders import build_patent
        c = sqlite3.connect(":memory:")
        c.execute("CREATE TABLE patent_cache (patent_id TEXT PRIMARY KEY, issue_date TEXT)")
        c.execute("INSERT INTO patent_cache VALUES ('11875185', '2024-01-16')")
        c.commit()
        p = build_patent(c, self._entry(), {"99999999": "other"})
        assert p.impact == ""


class TestBuildLeadershipRoleAndMedia:
    def test_leadership(self) -> None:
        r = build_leadership_role(
            {
                "role": "Co-Chair",
                "description": "Test WS",
                "society": "ACM SIGSOFT",
                "year": 2023,
            }
        )
        assert r.year == 2023
        assert r.role == "Co-Chair"

    def test_media(self) -> None:
        m = build_media_appearance(
            {
                "title": "T",
                "venue": "V",
                "year": 2024,
                "url": "https://example.com",
            }
        )
        assert m.year == 2024
        assert m.url == "https://example.com"


class TestValidateNonScholar:
    def test_empty_passes(self) -> None:
        # Empty / missing YAML must not crash.
        validate_non_scholar({}, [])

    def test_cve_missing_id_exits(self) -> None:
        with pytest.raises(SystemExit):
            validate_non_scholar({"cves": [{"organization": "X"}]}, [])

    def test_cve_unknown_paper_title_exits(self) -> None:
        with pytest.raises(SystemExit):
            validate_non_scholar(
                {
                    "cves": [
                        {
                            "cve_id": "CVE-2024-12345",
                            "paper_title": "Paper That Does Not Exist",
                        }
                    ]
                },
                [{"title": "An Unrelated Paper"}],
            )

    def test_cve_known_paper_title_passes(self) -> None:
        validate_non_scholar(
            {
                "cves": [
                    {
                        "cve_id": "CVE-2024-12345",
                        "paper_title": "Some Paper",
                    }
                ]
            },
            [{"title": "Some Paper"}],
        )

    def test_grant_missing_required_field_exits(self) -> None:
        with pytest.raises(SystemExit):
            validate_non_scholar(
                {"grants_as_pi": [{"title": "T"}]},  # missing agency, role, etc.
                [],
            )

    def test_student_missing_required_exits(self) -> None:
        with pytest.raises(SystemExit):
            validate_non_scholar(
                {"graduate_students": [{"name": "X"}]},
                [],
            )

    def test_service_missing_description_exits(self) -> None:
        with pytest.raises(SystemExit):
            validate_non_scholar(
                {"university_service": [{"year": 2025}]},
                [],
            )

    def test_service_with_description_passes(self) -> None:
        validate_non_scholar(
            {"profession_service": [{"description": "PC Member, ICSE", "year": 2025}]},
            [],
        )

    def test_service_no_year_passes(self) -> None:
        # Journal reviewing has no year — must NOT trigger a validation error.
        validate_non_scholar(
            {"other_service": [{"description": "Reviewer, IEEE TSE"}]},
            [],
        )


class TestLoadNonScholar:
    def test_none_path_returns_empty(self) -> None:
        assert load_non_scholar(None) == {}

    def test_missing_path_exits(self) -> None:
        with pytest.raises(SystemExit):
            load_non_scholar("/no/such/file.yaml")

    def test_valid_file(self, fixtures_dir) -> None:
        # Round-trip the test fixture.
        path = fixtures_dir / "non-scholar.yaml"
        data = load_non_scholar(str(path))
        assert "key_works" in data
        assert "graduate_students" in data
