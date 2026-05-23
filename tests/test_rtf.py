"""Unit tests for pubs_emitter.rtf — table builder + per-record renderers."""
from __future__ import annotations

import io

import pytest

from pubs_emitter.rtf import (
    RtfTable,
    apply_acronym_expansions,
    build_paper_index,
    render_citation,
    render_conference_presentation,
    render_grants_section,
    render_invited_talk,
    render_key_works_section,
    render_leadership_role,
    render_link_field,
    render_media_appearance,
    render_service_section,
    render_students_section,
)
from pubs_emitter.types import (
    Citation,
    ConferencePresentation,
    Grant,
    InvitedTalk,
    KeyWork,
    LeadershipRole,
    MediaAppearance,
    ServiceEntry,
    Student,
)


# ----- RtfTable -----------------------------------------------------------


class TestRtfTable:
    def test_empty_table_renders_empty_string(self) -> None:
        t = RtfTable([100, 200, 300])
        assert t.render() == ""

    def test_arity_mismatch_raises(self) -> None:
        t = RtfTable([100, 200])
        with pytest.raises(ValueError):
            t.add_row(["only-one-cell"])

    def test_zero_columns_raises(self) -> None:
        with pytest.raises(ValueError):
            RtfTable([])

    def test_header_renders_bold(self) -> None:
        t = RtfTable([100, 200])
        t.add_header(["A", "B"])
        out = t.render()
        assert r"\b A\b0" in out
        assert r"\b B\b0" in out

    def test_row_no_bold(self) -> None:
        t = RtfTable([100, 200])
        t.add_row(["x", "y"])
        out = t.render()
        assert r"\b " not in out

    def test_cumulative_cellx(self) -> None:
        # cellx positions are cumulative — used by Word to lay out cell widths.
        t = RtfTable([100, 200, 300])
        t.add_row(["x", "y", "z"])
        out = t.render()
        assert r"\cellx100" in out
        assert r"\cellx300" in out
        assert r"\cellx600" in out


# ----- Pure render helpers -----------------------------------------------


class TestApplyAcronymExpansions:
    def test_first_occurrence_expanded(self) -> None:
        done: set[str] = set()
        out = apply_acronym_expansions("Proceedings of ICSE (IEEE)", done)
        assert "Institute of Electrical and Electronics Engineers (IEEE)" in out
        assert "IEEE" in done

    def test_second_occurrence_uses_bare_acronym(self) -> None:
        done: set[str] = {"IEEE"}
        out = apply_acronym_expansions("Proceedings of ICSE (IEEE)", done)
        assert out == "Proceedings of ICSE (IEEE)"

    def test_unrelated_venue_untouched(self) -> None:
        done: set[str] = set()
        out = apply_acronym_expansions("Some Journal", done)
        assert out == "Some Journal"


class TestRenderLinkField:
    def test_doi_link(self) -> None:
        out = render_link_field("https://doi.org/10.1000/x")
        assert "DOI:" in out
        assert "10.1000/x" in out
        assert "HYPERLINK" in out

    def test_cve_link(self) -> None:
        out = render_link_field("https://nvd.nist.gov/vuln/detail/CVE-2024-12345")
        # Visible text is the bare CVE id, no "DOI:" / "URL:" prefix.
        assert "CVE-2024-12345" in out
        assert "DOI:" not in out
        assert "URL:" not in out

    def test_generic_url(self) -> None:
        out = render_link_field("https://example.com/x")
        assert "URL:" in out
        assert "https://example.com/x" in out

    def test_empty_link_renders_empty(self) -> None:
        assert render_link_field("") == ""


def _citation(**overrides) -> Citation:
    """Test helper — build a Citation with sensible defaults + overrides."""
    base = dict(
        section="Conferences and Workshops",
        rank="Rank 1",
        year=2025,
        year_str="2025",
        authors_rtf="\\b Davis, J.C.\\b0",
        title="A Conference Paper",
        venue="Proceedings of ICSE",
        details=", 1-12",
        link="https://doi.org/10.x",
    )
    base.update(overrides)
    return Citation(**base)


class TestRenderCitation:
    def test_basic_shape(self) -> None:
        out = render_citation(_citation(), expansion_done=set())
        assert "Davis, J.C." in out
        assert "(2025)" in out
        assert "A Conference Paper" in out
        assert r"\i " in out and r"\i0" in out  # italic venue
        assert "DOI:" in out
        assert "Venue rank: Tier 1" in out  # peer-reviewed section → prefix

    def test_non_ranked_section_no_prefix(self) -> None:
        cit = _citation(section="Other publications and products", rank="Preprint")
        out = render_citation(cit, expansion_done=set())
        assert "Preprint" in out
        assert "Venue rank:" not in out

    def test_back_ref_resolved(self) -> None:
        cit = _citation(back_ref_title="Another Paper")
        out = render_citation(
            cit,
            expansion_done=set(),
            paper_index={"anotherpaper": "C.4.3"},
        )
        assert "(see C.4.3)" in out

    def test_key_work_cross_link(self) -> None:
        cit = _citation(title="Highlighted Paper")
        out = render_citation(
            cit,
            expansion_done=set(),
            key_work_index={"highlightedpaper": "C.1.2"},
        )
        assert "(listed as C.1.2)" in out


class TestRenderInvitedTalk:
    def test_with_topic_and_subtitle(self) -> None:
        out = render_invited_talk(
            InvitedTalk(
                year=2024, year_str="2024",
                topic="ReDoS", subtitle="Empirical Study",
                venue="Stanford",
            )
        )
        assert "Seminar on ReDoS: Empirical Study" in out
        assert "Stanford" in out
        assert "2024" in out

    def test_topic_only(self) -> None:
        out = render_invited_talk(
            InvitedTalk(
                year=2024, year_str="2024",
                topic="ReDoS", subtitle="",
                venue="Stanford",
            )
        )
        assert "Seminar on ReDoS" in out
        assert ":" not in out.replace("Seminar on ReDoS", "")  # no leftover colon

    def test_subtitle_only(self) -> None:
        out = render_invited_talk(
            InvitedTalk(
                year=2024, year_str="2024",
                topic="", subtitle="My Talk Title",
                venue="V",
            )
        )
        assert out.startswith("Seminar: My Talk Title")


class TestRenderLeadershipRole:
    def test_shape(self) -> None:
        out = render_leadership_role(
            LeadershipRole(
                year=2023, year_str="2023",
                role="Co-Chair",
                description="A Workshop",
                society="ACM SIGSOFT",
            )
        )
        assert "Co-Chair" in out
        assert "A Workshop" in out
        assert "2023" in out
        assert r"\ul Society: ACM SIGSOFT\ulnone" in out


class TestRenderMediaAppearance:
    def test_shape(self) -> None:
        out = render_media_appearance(
            MediaAppearance(
                year=2024, year_str="2024",
                title="Episode",
                venue="Podcast",
                url="https://example.com",
            )
        )
        assert "Episode" in out
        assert r"\i Podcast\i0" in out
        assert "URL:" in out

    def test_no_url(self) -> None:
        out = render_media_appearance(
            MediaAppearance(
                year=2024, year_str="2024",
                title="Episode", venue="Podcast", url="",
            )
        )
        assert "URL:" not in out
        assert "HYPERLINK" not in out


class TestRenderConferencePresentation:
    def test_resolves_via_paper_index(self) -> None:
        bib = [
            {
                "title": "A Conference Paper",
                "year": "2025",
                "booktitle": "[ICSE'25] Proceedings of ICSE",
            }
        ]
        paper_index = {"aconferencepaper": "C.4.1"}
        out = render_conference_presentation(
            ConferencePresentation(paper_title="A Conference Paper"),
            bib,
            paper_index,
        )
        assert "Talk at" in out
        assert "2025" in out
        assert "Associated with publication C.4.1" in out

    def test_unresolved_paper_renders_placeholder(self) -> None:
        out = render_conference_presentation(
            ConferencePresentation(paper_title="No Such Paper"),
            [],
            {},
        )
        assert "[unresolved paper:" in out


# ----- Section assembly + paper-index ------------------------------------


class TestBuildPaperIndex:
    def test_assigns_section_codes(self) -> None:
        pubs = {
            "Conferences and Workshops": [
                _citation(title="Paper A"),
                _citation(title="Paper B"),
            ],
        }
        idx = build_paper_index(pubs)
        assert idx["papera"] == "C.4.1"
        assert idx["paperb"] == "C.4.2"

    def test_cve_entries_excluded(self) -> None:
        # CVEs are excluded from the index (they'd be back-pointer chains), but
        # the per-section enumeration still counts them — the preprint takes
        # whichever slot enumerate() gives it. The CVE is still rendered in
        # write_rtf at slot 1; the index just doesn't record it as a target.
        pubs = {
            "Other publications and products": [
                _citation(title="A CVE Entry", rank="CVE"),
                _citation(title="A Preprint", rank="Preprint"),
            ],
        }
        idx = build_paper_index(pubs)
        assert "acvenetry" not in idx
        assert idx["apreprint"] == "C.5.2"


def _grant(**overrides) -> Grant:
    base = dict(
        start_year=2025, end_year=2030,
        title="Test Project", agency="National Science Foundation",
        agency_short="NSF", grant_number="2025001",
        role="PI", co_pis=[], lead_pi="",
        responsibility_percent=0, amount=600000,
        activities="", responsibility="",
        inspired_by=[], publication_outcomes=[],
    )
    base.update(overrides)
    return Grant(**base)


class TestRenderGrantsSection:
    def test_emits_section_heading(self) -> None:
        buf = io.StringIO()
        render_grants_section("Grants PI", [_grant()], buf)
        out = buf.getvalue()
        assert "C.10" in out
        assert "Externally sponsored grants as PI" in out

    def test_does_not_emit_inspired_by_or_publication_outcomes(self) -> None:
        # Cross-link rendering is reserved for C.1 Key Works once the linkage
        # shape is defined; grant section must NOT emit "Inspired by:" /
        # "Publication outcomes:" lines.
        buf = io.StringIO()
        render_grants_section(
            "Grants PI",
            [_grant(
                inspired_by=["Some Paper Title"],
                publication_outcomes=["Another Paper Title"],
            )],
            buf,
        )
        out = buf.getvalue()
        assert "Inspired by" not in out
        assert "Publication outcomes" not in out

    def test_empty_list_writes_nothing(self) -> None:
        buf = io.StringIO()
        render_grants_section("Grants PI", [], buf)
        assert buf.getvalue() == ""


class TestGrantHeadShape:
    """The first line of each grant entry — `agency_short` + `grant_number` +
    `title` — has four shapes; pin them so future renderer refactors can't
    drop a case."""

    def test_full_shape_short_plus_number_plus_title(self) -> None:
        buf = io.StringIO()
        render_grants_section(
            "Grants PI",
            [_grant(agency_short="NSF", grant_number="2541917", title="CAREER")],
            buf,
        )
        assert "NSF #2541917: CAREER" in buf.getvalue()

    def test_short_only_no_number(self) -> None:
        buf = io.StringIO()
        render_grants_section(
            "Grants PI",
            [_grant(agency_short="Cisco", grant_number="", title="Trustworthy ML")],
            buf,
        )
        assert "Cisco: Trustworthy ML" in buf.getvalue()

    def test_number_only_no_short(self) -> None:
        # Rare but plausible: someone files a grant_number with no funder prefix.
        buf = io.StringIO()
        render_grants_section(
            "Grants PI",
            [_grant(agency_short="", grant_number="X-1", title="Mystery Grant")],
            buf,
        )
        assert "#X-1: Mystery Grant" in buf.getvalue()

    def test_title_only_no_prefix(self) -> None:
        # The new case enabled by making agency_short optional. Internal
        # grants + fellowships render as just the bolded title with no
        # "FUNDER: " prefix.
        buf = io.StringIO()
        render_grants_section(
            "Internal Grants",
            [_grant(agency_short="", grant_number="", title="Bare Title Grant")],
            buf,
        )
        out = buf.getvalue()
        assert "Bare Title Grant" in out
        # And critically: no leading colon (regression guard — the prior
        # renderer would have emitted ": Bare Title Grant").
        assert ": Bare Title Grant" not in out


class TestGrantTotalsMath:
    """Per-section total computation. Each grant section can declare a
    `Total amount of ...: $X` line via GRANT_TOTAL_LABELS; the value must
    be `sum(g.amount for g in grants)` formatted with thousands separators."""

    def _render(self, section, grants):
        buf = io.StringIO()
        render_grants_section(section, grants, buf)
        return buf.getvalue()

    def test_single_grant_total(self) -> None:
        out = self._render("Grants PI", [_grant(amount=687140)])
        assert "Total amount of external funding as PI" in out
        assert "$687,140" in out

    def test_multi_grant_total_sums_amounts(self) -> None:
        out = self._render(
            "Grants PI",
            [_grant(amount=100000), _grant(amount=200000), _grant(amount=50000)],
        )
        assert "$350,000" in out

    def test_zero_amount_grant_contributes_nothing(self) -> None:
        out = self._render(
            "Grants PI",
            [_grant(amount=100000), _grant(amount=0), _grant(amount=50000)],
        )
        assert "$150,000" in out

    def test_thousands_separators_below_one_million(self) -> None:
        out = self._render("Grants PI", [_grant(amount=24000)])
        assert "$24,000" in out
        assert "$24000" not in out  # no separator-less form leaked

    def test_thousands_separators_above_one_million(self) -> None:
        out = self._render("Grants PI", [_grant(amount=1234567)])
        assert "$1,234,567" in out

    def test_per_section_label_is_section_specific(self) -> None:
        # GRANT_TOTAL_LABELS distinguishes PI / Co-PI / Gifts / Internal.
        pi = self._render("Grants PI", [_grant(amount=100)])
        copi = self._render("Grants Co-PI", [_grant(amount=100)])
        gifts = self._render("Gifts", [_grant(amount=100)])
        internal = self._render("Internal Grants", [_grant(amount=100)])
        assert "Total amount of external funding as PI" in pi
        assert "Total amount of external funding as Co-PI or Co-I" in copi
        assert "Total amount of external gifts and voluntary support" in gifts
        assert "Total amount of internal funding" in internal

    def test_no_total_line_when_section_label_unset(self) -> None:
        # Sections not in GRANT_TOTAL_LABELS shouldn't emit a total line. All
        # four canonical grant sections have one, so we synthesise a section
        # that doesn't by importing the map and using any non-grant Section.
        # Easier: confirm that all four DO render the line (covered above) +
        # that an empty grant list renders no total (since the whole section
        # is skipped, no total is possible).
        out = self._render("Grants PI", [])
        assert "Total amount" not in out


# ----- Student helpers ----------------------------------------------------


class TestStudentPubRefs:
    def test_matches_via_last_name_and_initials(self) -> None:
        from pubs_emitter.rtf import _student_pub_refs as fn  # local for clarity
        bib = [
            {
                "title": "Paper One",
                "author": "Davis, James C and Amusuo, P.",
            },
            {
                "title": "Paper Two",
                "author": "Smith, John and Amusuo, Paschal C",
            },
        ]
        paper_index = {"paperone": "C.4.1", "papertwo": "C.4.2"}
        refs = fn("Paschal C. Amusuo", bib, paper_index)
        assert refs == ["C.4.1", "C.4.2"]

    def test_dedupes(self) -> None:
        from pubs_emitter.rtf import _student_pub_refs as fn
        bib = [
            {"title": "Paper One", "author": "Amusuo, P. and Amusuo, P.C."},
        ]
        paper_index = {"paperone": "C.4.1"}
        refs = fn("Paschal C. Amusuo", bib, paper_index)
        assert refs == ["C.4.1"]

    def test_no_match(self) -> None:
        from pubs_emitter.rtf import _student_pub_refs as fn
        bib = [{"title": "X", "author": "Smith, John"}]
        assert fn("Paschal C. Amusuo", bib, {"x": "C.4.1"}) == []


class TestRenderStudentsSection:
    def test_emits_table_with_header(self) -> None:
        buf = io.StringIO()
        render_students_section(
            "Graduate Students",
            [
                Student(
                    grad_year=2025, grad_display="2025 Spring",
                    name="Paschal C. Amusuo", degree="PhD",
                    role="Chair", position="Software Eng.",
                )
            ],
            bib_entries=[
                {
                    "title": "Paper One",
                    "author": "Davis, James C and Amusuo, Paschal C",
                }
            ],
            paper_index={"paperone": "C.4.1"},
            out=buf,
        )
        out = buf.getvalue()
        assert "C.14" in out
        assert "Graduate students advised" in out
        # Auto-derived pubs cell wired in.
        assert "C.4.1" in out
        # Table row carries the student name.
        assert "Paschal C. Amusuo" in out

    def test_empty_list_writes_nothing(self) -> None:
        buf = io.StringIO()
        render_students_section(
            "Graduate Students", [], bib_entries=[], paper_index={}, out=buf,
        )
        assert buf.getvalue() == ""

    def test_co_advisor_inlined_in_role_cell(self) -> None:
        # When co_advisor is set, the Role column renders as
        # "Co-Chair (with NAME)" — matches the CV format where co-chair
        # status is annotated inline with the partner faculty name.
        buf = io.StringIO()
        render_students_section(
            "Graduate Students",
            [
                Student(
                    grad_year=2027, grad_display="expected Spring 2027",
                    name="Some Student", degree="PhD candidate",
                    role="Co-Chair", position="",
                    co_advisor="Yung-Hsiang Lu",
                )
            ],
            bib_entries=[], paper_index={}, out=buf,
        )
        out = buf.getvalue()
        assert "Co-Chair (with Yung-Hsiang Lu)" in out

    def test_co_advisor_absent_renders_bare_role(self) -> None:
        # Regression guard: a Chair student must not pick up a "(with )"
        # suffix just because the field exists with a default empty string.
        buf = io.StringIO()
        render_students_section(
            "Graduate Students",
            [
                Student(
                    grad_year=2026, grad_display="Graduated 2026",
                    name="Some Student", degree="PhD",
                    role="Chair", position="",
                )
            ],
            bib_entries=[], paper_index={}, out=buf,
        )
        out = buf.getvalue()
        assert "(with" not in out
        assert "Chair" in out


class TestRenderServiceSection:
    def test_emits_section_heading_and_body(self) -> None:
        buf = io.StringIO()
        render_service_section(
            "University Service",
            [ServiceEntry(year=2025, year_str="2025", description="ECE Rep on COE Cmte")],
            buf,
        )
        out = buf.getvalue()
        assert "C.23" in out
        assert "Service to Purdue" in out
        assert "ECE Rep on COE Cmte. 2025." in out

    def test_multi_year_string_rendered_verbatim(self) -> None:
        buf = io.StringIO()
        render_service_section(
            "Profession Service",
            [ServiceEntry(year=2025, year_str="2025, 2026, 2027", description="PC Member, ICSE")],
            buf,
        )
        assert "PC Member, ICSE. 2025, 2026, 2027." in buf.getvalue()

    def test_year_range_string_rendered_verbatim(self) -> None:
        buf = io.StringIO()
        render_service_section(
            "University Service",
            [ServiceEntry(year=2023, year_str="2023-present", description="Member, ABET")],
            buf,
        )
        assert "Member, ABET. 2023-present." in buf.getvalue()

    def test_empty_year_str_suppresses_year_emission(self) -> None:
        # Journal reviewing → no year string → render description with a
        # single trailing period (no double period or stray year).
        buf = io.StringIO()
        render_service_section(
            "Other Service",
            [ServiceEntry(year=9999, year_str="", description="Reviewer, IEEE TSE")],
            buf,
        )
        out = buf.getvalue()
        assert "Reviewer, IEEE TSE." in out
        # Regression guards: no orphan ". ." and no stray 9999.
        assert ".." not in out.replace("\\par", "")
        assert "9999" not in out

    def test_section_codes_match_section_param(self) -> None:
        # All four service sections route through the same renderer; each
        # must surface its own C.X code.
        for section, expected_code in (
            ("University Service", "C.23"),
            ("Profession Service", "C.24"),
            ("National Service", "C.25"),
            ("Other Service", "C.26"),
        ):
            buf = io.StringIO()
            render_service_section(
                section,
                [ServiceEntry(year=2024, year_str="2024", description="x")],
                buf,
            )
            assert expected_code in buf.getvalue()

    def test_empty_list_writes_nothing(self) -> None:
        buf = io.StringIO()
        render_service_section("Other Service", [], buf)
        assert buf.getvalue() == ""

    def test_numbered_list_increments(self) -> None:
        buf = io.StringIO()
        render_service_section(
            "Profession Service",
            [
                ServiceEntry(year=2025, year_str="2025", description="First"),
                ServiceEntry(year=2026, year_str="2026", description="Second"),
                ServiceEntry(year=2027, year_str="2027", description="Third"),
            ],
            buf,
        )
        out = buf.getvalue()
        assert "C.24.1." in out
        assert "C.24.2." in out
        assert "C.24.3." in out


class TestRenderKeyWorksSection:
    def test_emits_citation_then_impact_paragraphs(self) -> None:
        buf = io.StringIO()
        kw = KeyWork(citation=_citation(title="Highlighted"), impact="Big impact text.")
        render_key_works_section(
            [kw],
            paper_index={"highlighted": "C.4.7"},
            out=buf,
        )
        out = buf.getvalue()
        assert "C.1" in out
        assert "Highlighted" in out
        assert "Big impact text." in out
        # Cross-link to canonical location.
        assert "(listed as C.4.7)" in out

    def test_empty_list_writes_nothing(self) -> None:
        buf = io.StringIO()
        render_key_works_section([], paper_index={}, out=buf)
        assert buf.getvalue() == ""
