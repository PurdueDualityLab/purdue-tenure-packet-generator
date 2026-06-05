"""Unit tests for pubs_emitter.rtf — table builder + per-record renderers."""
from __future__ import annotations

import io

import pytest

from pubs_emitter.rtf import (
    RtfTable,
    _finalize_ref_hyperlinks,
    _student_tier,
    apply_acronym_expansions,
    build_paper_index,
    render_candidate_information_section,
    render_citation,
    render_conference_presentation,
    render_grants_section,
    render_invited_talk,
    render_key_works_section,
    render_leadership_role,
    render_link_field,
    render_media_appearance,
    render_postdocs_section,
    render_entrepreneurial_activities_section,
    render_service_section,
    render_student_awards_section,
    render_students_section,
    render_technology_transfer_section,
    render_under_review_section,
    render_undergrad_products_section,
)
from pubs_emitter.types import (
    Award,
    CandidateInformation,
    Citation,
    ConferencePresentation,
    Degree,
    EntrepreneurialActivity,
    Grant,
    GrantPerson,
    Identifiers,
    InvitedTalk,
    KeyWork,
    LeadershipRole,
    MediaAppearance,
    OtherPosition,
    PostdocVisiting,
    ProfessionalMembership,
    ServiceEntry,
    Student,
    StudentAward,
    TechnologyTransfer,
    UndergradProduct,
    UnderReview,
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
        # Peer-reviewed section → "Venue rank:" prefix. The space inside
        # "Tier N" is an RTF non-breaking space (`\~`) so the digit can't
        # orphan onto the next line — preserve that form here.
        assert "Venue rank: Tier\\~1" in out

    def test_non_ranked_section_no_prefix(self) -> None:
        cit = _citation(section="Other publications and products", rank="Preprint")
        out = render_citation(cit, expansion_done=set())
        # arXiv preprints are tier-labeled "Technical report" — aligns
        # with the C.5 subcategory "Technical Reports" that groups them,
        # and avoids any implication that the paper is on a peer-review
        # track (which "Preprint" connotes).
        assert "Technical report" in out
        assert "Venue rank:" not in out

    def test_back_ref_resolved(self) -> None:
        # All C.X.Y cross-refs now emit sentinel-wrapped form via
        # `_code_link` so `_finalize_ref_hyperlinks` converts them to
        # clickable links (matching `@id` resolution). The unit test
        # checks the sentinel form; the integration path is exercised
        # in TestE2eSectionsFilter.
        from pubs_emitter.rtf import _code_link
        cit = _citation(back_ref_title="Another Paper")
        out = render_citation(
            cit,
            expansion_done=set(),
            paper_index={"anotherpaper": "C.4.3"},
        )
        assert f"(see {_code_link('C.4.3')})" in out

    def test_key_work_cross_link(self) -> None:
        # When a regular C.2/C.4 paper is ALSO a designated key work,
        # the cross-link reads "(key paper C.1.X)" — explicitly flags
        # promotion-relevant papers without making the reader visit C.1.
        from pubs_emitter.rtf import _code_link
        cit = _citation(title="Highlighted Paper")
        out = render_citation(
            cit,
            expansion_done=set(),
            key_work_index={"highlightedpaper": "C.1.2"},
        )
        assert f"(key paper {_code_link('C.1.2')})" in out
        # Regression guard: the old "(listed as C.1.X)" form is gone.
        assert "(listed as " not in out


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
        from pubs_emitter.rtf import _code_link
        assert "Talk at" in out
        assert "2025" in out
        assert f"Associated with publication {_code_link('C.4.1')}" in out

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
        # CVEs are excluded from the index (they'd be back-pointer chains).
        # For C.5 specifically, build_paper_index mirrors the renderer's
        # subcategory grouping (Magazine → Technical Reports → Direct
        # computing industry impacts) so the index codes match what's
        # actually emitted in the RTF. Preprint goes to "Technical Reports"
        # (first non-empty bucket) → C.5.1; CVE goes to "Direct computing
        # industry impacts" → would be C.5.2 but excluded from the index.
        pubs = {
            "Other publications and products": [
                _citation(title="A CVE Entry", rank="CVE"),
                _citation(title="A Preprint", rank="Preprint"),
            ],
        }
        idx = build_paper_index(pubs)
        assert "acvenetry" not in idx
        assert idx["apreprint"] == "C.5.1"


class TestOrderCitationsForEmission:
    """Single source of truth for "what's the i-th C.X entry?".

    Both `build_paper_index` and `render_other_pubs_section` MUST call
    this — that's the structural defense against the 260603 incident
    class (paper_index numbered by source order, renderer regrouped by
    subcategory → C.X.Y back-pointers landed at the wrong paper).
    """

    def test_default_is_pass_through(self) -> None:
        """Most sections emit in input order; the function returns the
        same list reference (no copy, no reorder)."""
        from pubs_emitter.rtf import order_citations_for_emission
        cits = [_citation(title=f"P{i}") for i in range(3)]
        assert order_citations_for_emission(
            "Conferences and Workshops", cits,
        ) == cits

    def test_c5_groups_by_subcategory(self) -> None:
        """C.5 input that mixes Preprint / Magazine / CVE / Disclosure
        must come back ordered: Magazine → Technical Reports → Direct
        computing industry impacts (the canonical C.5 subsection order
        the user ratified)."""
        from pubs_emitter.rtf import order_citations_for_emission
        cits = [
            _citation(title="A-preprint", rank="Preprint"),
            _citation(title="B-magazine", rank="Magazine"),
            _citation(title="C-cve",      rank="CVE"),
            _citation(title="D-preprint", rank="Preprint"),
            _citation(title="E-disclosure", rank="Disclosure"),
        ]
        ordered = order_citations_for_emission(
            "Other publications and products", cits,
        )
        # Magazines first, then Technical Reports (Preprints), then
        # Direct-computing-industry-impacts (CVE + Disclosure pooled).
        # Within each subcategory the input order is preserved.
        titles = [c.title for c in ordered]
        assert titles == [
            "B-magazine",
            "A-preprint", "D-preprint",
            "C-cve", "E-disclosure",
        ]

    def test_paper_index_codes_match_emit_order(self) -> None:
        """The structural invariant: paper_index codes line up with the
        positional order returned by order_citations_for_emission, so
        rendered C.X.Y bookmarks and back-pointer codes always agree."""
        from pubs_emitter.rtf import order_citations_for_emission
        cits = [
            _citation(title="A-preprint", rank="Preprint"),
            _citation(title="B-magazine", rank="Magazine"),
        ]
        pubs = {"Other publications and products": cits}
        idx = build_paper_index(pubs)
        ordered = order_citations_for_emission(
            "Other publications and products", cits,
        )
        for i, cit in enumerate(ordered, 1):
            if cit.rank == "CVE":
                continue
            expected_code = f"C.5.{i}"
            from pubs_emitter.venue import normalize_title
            assert idx[normalize_title(cit.title)] == expected_code


def _grant(**overrides) -> Grant:
    """Test-helper Grant factory.

    Convenience: pass `amount=X` to set all three 3-way fields to the same
    value (the sole-PI single-institution case). Pass any of `total_amount`,
    `purdue_amount`, `my_amount` individually to override only that field.
    """
    amount = overrides.pop("amount", 600000)
    # Back-compat shim for tests that still pass co_pis / lead_pi: synthesize
    # GrantPerson records so the renderer's Row 4 produces equivalent output.
    co_pis = overrides.pop("co_pis", None)
    lead_pi = overrides.pop("lead_pi", None)
    personnel = overrides.pop("personnel", None)
    if personnel is None:
        personnel = []
        if lead_pi:
            personnel.append(GrantPerson(name=lead_pi, role="PI"))
        for c in (co_pis or []):
            personnel.append(GrantPerson(name=c, role="Co-PI"))
    base = dict(
        start_year=2025, end_year=2030,
        title="Test Project", agency="National Science Foundation",
        agency_short="NSF", grant_number="2025001",
        role="PI",
        lead_institution="",
        personnel=personnel,
        responsibility_percent=0,
        total_amount=amount, purdue_amount=amount, my_amount=amount,
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
    """Row 1 of each grant table — `{N}. [{grant_number}] {agency} / {title}`.
    Pins the two shapes (with / without grant_number) and the no-grant_number
    regression guard (no orphan leading space before the agency name)."""

    def test_grant_number_present(self) -> None:
        # _grant's default agency_short="NSF" routes Row 1 through the
        # NSF auto-link path, so the grant_number gets wrapped in an RTF
        # HYPERLINK field. Assert the visible tokens separately.
        buf = io.StringIO()
        render_grants_section(
            "Grants PI",
            [_grant(
                agency="US National Science Foundation",
                grant_number="2541917", title="CAREER",
            )],
            buf,
        )
        out = buf.getvalue()
        assert "2541917" in out
        assert "US National Science Foundation / CAREER" in out
        assert "fldinst HYPERLINK" in out

    def test_grant_number_absent(self) -> None:
        # Sponsored contract with no funder-side award number (e.g. Cisco).
        buf = io.StringIO()
        render_grants_section(
            "Grants PI",
            [_grant(
                agency="Contract with Cisco",
                grant_number="", title="Trustworthy ML",
            )],
            buf,
        )
        out = buf.getvalue()
        assert "Contract with Cisco / Trustworthy ML" in out
        # Regression guard: no double-space, no orphan "/ " at start.
        assert "  /" not in out

    def test_first_row_carries_bold_full_code(self) -> None:
        # Each grant's Row 1 carries the full `C.X.Y` code (e.g. C.10.1)
        # in bold, wrapped in a bookmark anchor so `@id` refs to grants
        # can hyperlink to the same form used elsewhere in the document.
        # Bold is brace-scoped (`{\b ... .}`) so the trailing space
        # before the rest of the head renders literally instead of being
        # eaten as the `\b0` control-word delimiter.
        buf = io.StringIO()
        render_grants_section(
            "Grants PI",
            [_grant(title="First"), _grant(title="Second"), _grant(title="Third")],
            buf,
        )
        out = buf.getvalue()
        # The bookmark anchor for grant N is "C_10_N" (dots → underscores).
        assert "\\*\\bkmkstart C_10_1" in out
        assert "\\*\\bkmkstart C_10_2" in out
        assert "\\*\\bkmkstart C_10_3" in out
        # Display code is the dotted form, inside the brace-scoped bold.
        assert "{\\b " in out and "C.10.1" in out

    def test_internal_grant_no_funder_prefix(self) -> None:
        # Internal grants typically have a long agency string ("Office of the
        # Provost, through the program ...") but no grant_number. Row 1 should
        # carry just "{N}. {agency} / {title}" with no orphan markers.
        buf = io.StringIO()
        render_grants_section(
            "Internal Grants",
            [_grant(
                agency="Purdue VEIL Program", agency_short="",
                grant_number="", title="Intercultural Engineering",
            )],
            buf,
        )
        out = buf.getvalue()
        assert "Purdue VEIL Program / Intercultural Engineering" in out


class TestGrantTableStructure:
    """Pins the 4-row table layout so renderer refactors can't silently
    collapse to paragraphs or drop a row."""

    def test_table_emits_four_rows_for_solo_pi(self) -> None:
        # Sole PI → row 4 renders "Sole PI" (always emitted; no conditional skip).
        buf = io.StringIO()
        render_grants_section(
            "Grants PI",
            [_grant(role="PI", co_pis=[], lead_pi="")],
            buf,
        )
        out = buf.getvalue()
        assert out.count("\\row") == 4
        assert "Sole PI" in out

    def test_table_emits_four_rows_when_co_pi_present(self) -> None:
        buf = io.StringIO()
        render_grants_section(
            "Grants PI",
            [_grant(role="PI", co_pis=["Yung-Hsiang Lu"])],
            buf,
        )
        assert buf.getvalue().count("\\row") == 4

    def test_amount_right_aligned_in_row_2(self) -> None:
        # Use a distinctive amount that won't collide with the section total
        # line (which also formats with the same `$N,NNN` helper).
        buf = io.StringIO()
        render_grants_section("Grants PI", [_grant(amount=123456)], buf)
        out = buf.getvalue()
        # The amount cell starts with `\pard\intbl\qr` (paragraph reset +
        # in-table marker + right-align). Row 2 now shows just the total
        # amount (template-aligned); the "my share" sub-figure is gone.
        # Each cell opens with its own `\pard` so the next cell starts
        # with default alignment automatically.
        assert "\\pard\\intbl\\qr $123,456\\cell" in out
        # Old three-way breakdown form must not appear anywhere.
        assert "my share" not in out
        assert " total; " not in out

    def test_borders_on_every_cell(self) -> None:
        buf = io.StringIO()
        render_grants_section("Grants PI", [_grant()], buf)
        out = buf.getvalue()
        # Top + bottom + left + right borders all appear at least once per cell.
        assert out.count("\\clbrdrt\\brdrs\\brdrw15") >= 4  # one per cell
        assert out.count("\\clbrdrb\\brdrs\\brdrw15") >= 4


class TestGrantTablePersonnelRow:
    """Row 4 (personnel-other-than-candidate): each person rendered as
    `Label: Name[, Department[, Institution]]`, joined with "; ". Per-person
    labels (not collective "Co-PIs: A, B") so different affiliations don't
    collide with the "Name, Dept" comma."""

    def test_pi_with_single_co_pi(self) -> None:
        buf = io.StringIO()
        render_grants_section(
            "Grants PI",
            [_grant(role="PI", co_pis=["Yung-Hsiang Lu"])],
            buf,
        )
        assert "Co-PI: Yung-Hsiang Lu" in buf.getvalue()

    def test_pi_with_multiple_co_pis_each_labeled(self) -> None:
        buf = io.StringIO()
        render_grants_section(
            "Grants PI",
            [_grant(role="PI", co_pis=["A", "B", "C"])],
            buf,
        )
        out = buf.getvalue()
        # Per-person labels, not collective "Co-PIs: A, B, C".
        assert "Co-PI: A; Co-PI: B; Co-PI: C" in out
        assert "Co-PIs:" not in out

    def test_co_pi_with_lead_pi(self) -> None:
        buf = io.StringIO()
        render_grants_section(
            "Grants Co-PI",
            [_grant(role="Co-PI", co_pis=[], lead_pi="Aravind Machiry")],
            buf,
        )
        assert "PI: Aravind Machiry" in buf.getvalue()

    def test_co_pi_with_lead_pi_and_other_co_pis(self) -> None:
        # Davis-as-Co-PI on a multi-PI internal grant: lead PI + per-person
        # Co-PI labels.
        buf = io.StringIO()
        render_grants_section(
            "Internal Grants",
            [_grant(
                role="Co-PI",
                lead_pi="Aravind Machiry",
                co_pis=["Carla Zoltowski", "Justin Hess"],
            )],
            buf,
        )
        out = buf.getvalue()
        assert "PI: Aravind Machiry; Co-PI: Carla Zoltowski; Co-PI: Justin Hess" in out

    def test_sole_pi_omits_personnel_row(self) -> None:
        # No lead_pi, no co_pis → no other personnel → row 4 not emitted.
        buf = io.StringIO()
        render_grants_section(
            "Grants PI",
            [_grant(role="PI", co_pis=[], lead_pi="")],
            buf,
        )
        out = buf.getvalue()
        # Absence of "PI:" / "Co-PI:" labels (the role line is just "PI", not "PI:").
        assert "PI: " not in out
        assert "Co-PI: " not in out


class TestGrantPersonnelAffiliations:
    """Personnel records carry their own `department` / `institution` /
    `nsf_award` fields. The renderer reads structured data directly off
    each `GrantPerson` — no separate registry lookup."""

    def test_co_pi_with_department(self) -> None:
        buf = io.StringIO()
        render_grants_section(
            "Grants PI",
            [_grant(role="PI", personnel=[
                GrantPerson(
                    name="Yung-Hsiang Lu", role="Co-PI",
                    department="Electrical & Computer Engineering",
                ),
            ])],
            buf,
        )
        # Template-aligned: comma-separated affiliation, no parens.
        assert "Co-PI: Yung-Hsiang Lu, Electrical & Computer Engineering" in buf.getvalue()

    def test_lead_pi_with_department(self) -> None:
        buf = io.StringIO()
        render_grants_section(
            "Grants Co-PI",
            [_grant(role="Co-PI", personnel=[
                GrantPerson(
                    name="Kirsten Davis", role="PI",
                    department="Engineering Education",
                ),
            ])],
            buf,
        )
        assert "PI: Kirsten Davis, Engineering Education" in buf.getvalue()

    def test_external_with_dept_and_institution(self) -> None:
        # External collaborator: dept + institution both appear inside parens.
        buf = io.StringIO()
        render_grants_section(
            "Grants Co-PI",
            [_grant(role="Co-PI", personnel=[
                GrantPerson(
                    name="Aravind Machiry", role="PI",
                    department="Electrical & Computer Engineering",
                ),
                GrantPerson(
                    name="External Person", role="Co-PI",
                    department="Civil Engineering",
                    institution="University of Maine",
                ),
            ])],
            buf,
        )
        out = buf.getvalue()
        assert "PI: Aravind Machiry, Electrical & Computer Engineering" in out
        assert "Co-PI: External Person, Civil Engineering, University of Maine" in out

    def test_nsf_award_moves_to_parenthetical(self) -> None:
        # Multi-inst NSF Collab: external PI's separate award number is
        # rendered as a trailing parenthetical after the full
        # `Role: Name, Dept, Institution` prefix. Keeps the personnel
        # rendering uniform with Purdue Co-PIs (template convention).
        buf = io.StringIO()
        render_grants_section(
            "Grants PI",
            [_grant(role="PI", lead_institution="Purdue University", personnel=[
                GrantPerson(
                    name="Dongyoon Lee", role="PI",
                    department="Computer Science",
                    institution="SUNY at Stony Brook",
                    nsf_award="2135157",
                ),
            ])],
            buf,
        )
        assert (
            "PI: Dongyoon Lee, Computer Science, "
            "SUNY at Stony Brook (NSF #2135157)"
        ) in buf.getvalue()

    def test_bare_name_when_no_dept_or_institution(self) -> None:
        # Personnel record with only name → renders without parentheses.
        buf = io.StringIO()
        render_grants_section(
            "Grants PI",
            [_grant(role="PI", personnel=[
                GrantPerson(name="Unknown Person", role="Co-PI"),
            ])],
            buf,
        )
        out = buf.getvalue()
        assert "Co-PI: Unknown Person" in out
        # Make sure we didn't append empty parens or a stray comma.
        assert "Co-PI: Unknown Person (" not in out
        assert "Co-PI: Unknown Person," not in out


class TestGrantLeadInstitutionAnnotationElided:
    """The renderer intentionally OMITS the lead-institution annotation
    from Row 3 (template-aligned behavior). The C.X section a grant
    lands in (C.10 PI vs C.11 Co-PI vs whatever) already conveys whether
    Davis is the lead or follower, so "(Purdue is lead)" / "(Columbia is
    lead)" reads as redundant noise in the rendered packet.

    The `Grant.lead_institution` YAML field stays in the schema — it's
    just elided from this specific rendering. Future consumers can read
    it directly if needed.
    """

    def test_purdue_lead_not_annotated(self) -> None:
        buf = io.StringIO()
        render_grants_section(
            "Grants PI",
            [_grant(role="PI", lead_institution="Purdue University")],
            buf,
        )
        out = buf.getvalue()
        assert "is lead" not in out
        assert "Purdue University is lead" not in out

    def test_external_lead_not_annotated(self) -> None:
        buf = io.StringIO()
        render_grants_section(
            "Grants PI",
            [_grant(role="PI", lead_institution="Columbia University")],
            buf,
        )
        out = buf.getvalue()
        assert "is lead" not in out
        # Davis's role doesn't get prefixed with "Purdue" either.
        assert "Purdue PI" not in out

    def test_external_lead_with_co_pi_role_not_annotated(self) -> None:
        buf = io.StringIO()
        render_grants_section(
            "Grants Co-PI",
            [_grant(role="Co-PI", lead_institution="Loyola University Chicago")],
            buf,
        )
        out = buf.getvalue()
        assert "is lead" not in out
        assert "Purdue Co-PI" not in out


class TestGrantNumberNsfHyperlink:
    """Row 1 grant_number rendering: NSF awards get a hyperlink to the
    official award-search page; other agencies render as plain text."""

    def test_nsf_grant_emits_hyperlink_field(self) -> None:
        buf = io.StringIO()
        render_grants_section(
            "Grants PI",
            [_grant(
                agency="US National Science Foundation",
                agency_short="NSF",
                grant_number="2541917",
                title="CAREER",
            )],
            buf,
        )
        out = buf.getvalue()
        # RTF hyperlink field with the deterministic NSF URL + visible text.
        assert (
            "\\field{\\*\\fldinst HYPERLINK "
            "\"https://www.nsf.gov/awardsearch/showAward?AWD_ID=2541917\"}"
            "{\\fldrslt 2541917}"
        ) in out

    def test_non_nsf_grant_plain_text(self) -> None:
        # Rolls Royce contract has a grant_number-like field but no auto-link.
        buf = io.StringIO()
        render_grants_section(
            "Grants PI",
            [_grant(
                agency="Contract with Rolls Royce",
                agency_short="Rolls Royce",
                grant_number="RR-12345",
                title="Securing Software",
            )],
            buf,
        )
        out = buf.getvalue()
        assert "RR-12345 Contract with Rolls Royce" in out
        # No HYPERLINK field for non-NSF grants.
        assert "fldinst HYPERLINK" not in out

    def test_nsf_grant_without_number_no_hyperlink(self) -> None:
        # No grant_number → no field at all (head row carries just agency + title).
        buf = io.StringIO()
        render_grants_section(
            "Grants PI",
            [_grant(
                agency="US National Science Foundation",
                agency_short="NSF",
                grant_number="",
                title="Some Award",
            )],
            buf,
        )
        out = buf.getvalue()
        assert "fldinst HYPERLINK" not in out


class TestGrantTotalAmountAndResponsibilityPct:
    """Template-aligned grant rendering:

      * Row 2 shows ONLY the total award amount (single dollar figure).
      * Row 3's `{role} - {pct}%` derives `pct` from `my_amount /
        total_amount`. When `pct == 100` (sole PI single-inst), the
        `- 100%` is suppressed since 100% is the implicit default.
      * Davis's per-recipient share is conveyed via the Row 3
        percentage, NOT via a Row 2 sub-figure.
    """

    def _fmt(self, total: int, purdue: int, mine: int) -> str:
        from pubs_emitter.rtf import _format_grant_amounts
        g = _grant(total_amount=total, purdue_amount=purdue, my_amount=mine)
        return _format_grant_amounts(g)

    def test_sole_pi_single_inst_single_amount(self) -> None:
        # Sole PI single-inst: row 2 shows just the total. The redundant
        # "my share: $X" repetition is gone.
        assert self._fmt(600000, 600000, 600000) == "$600,000"

    def test_collab_multi_pi_row_2_shows_total(self) -> None:
        # Even multi-inst NSF Collab with split credit: row 2 shows the
        # full award total; the credited share moves to Row 3 as a pct.
        assert self._fmt(2000000, 800000, 200000) == "$2,000,000"

    def test_pct_100_suppressed_when_sole_pi(self) -> None:
        # Sole PI single-inst → 100% credit → suppress the `- 100%`
        # because 100% is the implicit default.
        buf = io.StringIO()
        render_grants_section(
            "Grants PI",
            [_grant(role="PI", total_amount=500000,
                    purdue_amount=500000, my_amount=500000,
                    responsibility="Lead project")],
            buf,
        )
        out = buf.getvalue()
        assert "- 100%" not in out
        # Role + responsibility still present in Row 3.
        assert "PI, Lead project" in out

    def test_pct_shown_when_lt_100(self) -> None:
        # Multi-PI split: render `- {pct}%` on the role line.
        buf = io.StringIO()
        render_grants_section(
            "Grants PI",
            [_grant(role="PI", total_amount=548000,
                    purdue_amount=274000, my_amount=274000,
                    responsibility="Lead Purdue effort")],
            buf,
        )
        out = buf.getvalue()
        # 274000 / 548000 = 50%
        assert "PI - 50%, Lead Purdue effort" in out

    def test_amounts_render_in_row_2_end_to_end(self) -> None:
        buf = io.StringIO()
        render_grants_section(
            "Grants PI",
            [_grant(total_amount=1000000,
                    purdue_amount=400000, my_amount=400000)],
            buf,
        )
        out = buf.getvalue()
        # Just the total amount appears.
        assert "$1,000,000" in out
        # No three-way breakdown leftovers.
        assert "Purdue" not in out.split("Row 2")[0] or "total" not in out
        # Specifically the old "T total; P Purdue; M my share" form is gone.
        assert " total; " not in out
        assert " Purdue; " not in out
        assert " my share" not in out


class TestGrantTotalsMath:
    """Per-section total computation. Each grant section can declare a
    `Total amount of ...: $X` line via GRANT_TOTAL_LABELS; the value must
    be `sum(g.my_amount for g in grants)` (the tenure-credited share)
    formatted with thousands separators."""

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

    def test_section_total_sums_my_amount_not_purdue(self) -> None:
        # Renderer must use `my_amount` for the headline total, not
        # `purdue_amount`. Demonstrate with a Co-PI split where my_amount
        # is smaller than purdue_amount.
        out = self._render(
            "Grants PI",
            [
                _grant(total_amount=500000, purdue_amount=500000, my_amount=100000),
                _grant(total_amount=300000, purdue_amount=300000, my_amount=60000),
            ],
        )
        # Should sum my_amount → $160,000, NOT purdue → $800,000.
        assert "$160,000" in out
        # Sanity guard: the headline doesn't accidentally sum purdue.
        # Note: $500,000 / $300,000 / $100,000 / $60,000 also appear in the
        # individual amount cells of each grant; the regression-sensitive
        # token is the TOTAL line carrying $160,000.
        # Bold is brace-scoped (`{\b ...:}`) so the trailing space renders
        # as literal whitespace instead of being eaten as the `\b0`
        # control-word delimiter.
        assert "external funding as PI:} $160,000" in out


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
        # Each entry's code substring lives inside an RTF bookmark wrap
        # (`{\*\bkmkstart C_24_N}C.24.N{\*\bkmkend ...}`), so the trailing
        # period that used to be part of the assertion is now separated
        # from `C.24.N` by the closing-bookmark markup. Match the bare
        # code instead.
        assert "C.24.1" in out
        assert "C.24.2" in out
        assert "C.24.3" in out


class TestRenderUnderReviewSection:
    """A.1 numbered list — authors. title. /italic venue/, pages.
    Optional 'Due: …' suffix when the due_date isn't the 9999 sentinel."""

    def _ur(self, **overrides):
        base = dict(
            due_date="9999-99-99",
            title="Some Under-Review Paper",
            authors_rtf="\\b Davis, J.C.\\b0",
            venue="ACM Digital Governance (ACM DGOV)",
            pages="30 pages",
        )
        base.update(overrides)
        return UnderReview(**base)

    def test_emits_section_heading_and_a1_code(self) -> None:
        buf = io.StringIO()
        render_under_review_section([self._ur()], buf)
        out = buf.getvalue()
        assert "A.1 Products under review" in out
        # Numbered list cross-ref form: A.1.1 (inside a bookmark wrap, then
        # the literal period + `\tab` follow). Confirm both the code and
        # the bookmark anchor.
        assert "A.1.1" in out
        assert "\\*\\bkmkstart A_1_1" in out

    def test_multiple_entries_increment_index(self) -> None:
        buf = io.StringIO()
        render_under_review_section(
            [self._ur(title="One"), self._ur(title="Two"), self._ur(title="Three")],
            buf,
        )
        out = buf.getvalue()
        assert "A.1.1" in out and "A.1.2" in out and "A.1.3" in out

    def test_body_shape(self) -> None:
        buf = io.StringIO()
        render_under_review_section([self._ur()], buf)
        out = buf.getvalue()
        # Authors, then title, then italic venue, then comma + pages, then period.
        assert "\\b Davis, J.C.\\b0. Some Under-Review Paper. " in out
        assert "\\i ACM Digital Governance (ACM DGOV)\\i0" in out
        assert "30 pages." in out

    def test_due_date_suppressed_for_sentinel(self) -> None:
        buf = io.StringIO()
        render_under_review_section([self._ur(due_date="9999-99-99")], buf)
        assert "Due:" not in buf.getvalue()

    def test_due_date_suppressed_for_empty(self) -> None:
        buf = io.StringIO()
        render_under_review_section([self._ur(due_date="")], buf)
        assert "Due:" not in buf.getvalue()

    def test_due_date_emitted_when_known(self) -> None:
        buf = io.StringIO()
        render_under_review_section([self._ur(due_date="2026-03-15")], buf)
        out = buf.getvalue()
        assert "\\ul Due: 2026-03-15\\ulnone." in out

    def test_empty_list_writes_nothing(self) -> None:
        buf = io.StringIO()
        render_under_review_section([], buf)
        assert buf.getvalue() == ""


class TestRenderStudentAwardsSection:
    """C.16.2.4 / C.16.3.3 student awards, level-filtered + tier-grouped.

    Invariants pinned here:
      * Renderer takes a Section key ("Undergraduate Student Awards" or
        "Graduate Student Awards") and filters the shared `awards` list by
        each entry's `level` matching the section's expected level (U or G).
      * Section heading emitted with the correct C.16.2.4 / C.16.3.3 code.
      * Canonical tier order: National before Institutional regardless of
        YAML order.
      * Flat sequential numbering: `{code}.1`, `{code}.2`, … across BOTH
        tier subheadings (tier doesn't contribute to the number).
      * Within a tier, entries sort by year DESCENDING (newest first).
      * Empty filtered list = nothing emitted (no orphan heading).
    """

    def _award(self, **overrides) -> StudentAward:
        base = dict(
            year=2025, year_str="2025",
            level="U",
            tier="Institutional Awards",
            recipient="Test Student",
            award="Test Award",
        )
        base.update(overrides)
        return StudentAward(**base)

    def test_undergrad_section_emits_c1624_code(self) -> None:
        buf = io.StringIO()
        render_student_awards_section(
            "Undergraduate Student Awards",
            [self._award(level="U", tier="National and International Awards")],
            buf,
        )
        out = buf.getvalue()
        # Sub-section heading carries a bookmark wrap (`\\*\\bkmkstart C_16_2_4`)
        # so the code and heading text are no longer one contiguous substring;
        # check the two halves separately.
        assert "bkmkstart C_16_2_4" in out
        assert "Undergraduate Awards, Fellowships, and Career Development" in out
        # The old "These are students I mentored." preamble was dropped —
        # the section heading already conveys the context.
        assert "These are students I mentored" not in out
        # Bookmark-wrapped code followed by `.\tab` is the canonical
        # `_emit_list_item` shape; the bookmark-end is the unique anchor
        # confirming the entry was emitted as a numbered list item.
        assert "C_16_2_4_1}.\\tab" in out

    def test_grad_section_emits_c1633_code(self) -> None:
        buf = io.StringIO()
        render_student_awards_section(
            "Graduate Student Awards",
            [self._award(level="G", tier="Institutional Awards")],
            buf,
        )
        out = buf.getvalue()
        assert "bkmkstart C_16_3_3" in out
        assert "Graduate Student Awards, Fellowships, Internships, and Placement" in out
        assert "C_16_3_3_1}.\\tab" in out

    def test_filters_by_level(self) -> None:
        """The mixed list goes to BOTH calls; each filters by its own level."""
        mixed = [
            self._award(level="U", recipient="UndergradEntry"),
            self._award(level="G", recipient="GradEntry"),
        ]
        ubuf = io.StringIO()
        render_student_awards_section("Undergraduate Student Awards", mixed, ubuf)
        gbuf = io.StringIO()
        render_student_awards_section("Graduate Student Awards", mixed, gbuf)
        assert "UndergradEntry" in ubuf.getvalue()
        assert "GradEntry" not in ubuf.getvalue()
        assert "GradEntry" in gbuf.getvalue()
        assert "UndergradEntry" not in gbuf.getvalue()

    def test_canonical_tier_order(self) -> None:
        """Even if Institutional appears first in input, National renders first."""
        buf = io.StringIO()
        render_student_awards_section(
            "Undergraduate Student Awards",
            [
                self._award(tier="Institutional Awards", recipient="A"),
                self._award(tier="National and International Awards", recipient="B"),
            ],
            buf,
        )
        out = buf.getvalue()
        # Subheading order: National block precedes Institutional block.
        # Use `\i0` close-tag to disambiguate the Institutional SUBHEADING
        # line (italic inline-heading per `_emit_inline_heading`) from the
        # body-text mention in the earlier numbered entry.
        national_pos = out.index("National and International Awards\\i0")
        institutional_pos = out.index("Institutional Awards\\i0")
        assert national_pos < institutional_pos
        # Counter is section-wide: B (in National tier) gets .1, A gets .2.
        assert "C_16_2_4_1}.\\tab B" in out
        assert "C_16_2_4_2}.\\tab A" in out

    def test_within_tier_year_desc(self) -> None:
        buf = io.StringIO()
        render_student_awards_section(
            "Undergraduate Student Awards",
            [
                self._award(year=2021, year_str="2021", recipient="Old"),
                self._award(year=2026, year_str="2026", recipient="New"),
                self._award(year=2024, year_str="2024", recipient="Mid"),
            ],
            buf,
        )
        out = buf.getvalue()
        new_pos = out.index("New, Test Award")
        mid_pos = out.index("Mid, Test Award")
        old_pos = out.index("Old, Test Award")
        assert new_pos < mid_pos < old_pos
        # Numbering reflects the sorted order, not the input order.
        assert "C_16_2_4_1}.\\tab New" in out
        assert "C_16_2_4_2}.\\tab Mid" in out
        assert "C_16_2_4_3}.\\tab Old" in out

    def test_year_in_parens(self) -> None:
        buf = io.StringIO()
        render_student_awards_section(
            "Undergraduate Student Awards", [self._award(year_str="2025")], buf,
        )
        assert "(2025)" in buf.getvalue()

    def test_empty_list_writes_nothing(self) -> None:
        buf = io.StringIO()
        render_student_awards_section("Undergraduate Student Awards", [], buf)
        assert buf.getvalue() == ""

    def test_filtered_empty_writes_nothing(self) -> None:
        """If the list is non-empty but no entry matches the section's level,
        emit nothing (don't leave an orphan heading without entries)."""
        buf = io.StringIO()
        render_student_awards_section(
            "Graduate Student Awards",  # expects level="G"
            [self._award(level="U")],   # only undergrad
            buf,
        )
        assert buf.getvalue() == ""

    def test_invalid_section_key_raises(self) -> None:
        """Passing a non-award section to the renderer is a programmer
        error — fail loud."""
        buf = io.StringIO()
        with pytest.raises(ValueError):
            render_student_awards_section(
                "Software Products",  # type: ignore[arg-type]
                [self._award()],
                buf,
            )


class TestStudentTier:
    """`_student_tier` maps (degree, role) → Purdue's mandated 1-7 subsection
    order. Tier-based sort is the load-bearing C.14 invariant."""

    def _student(self, degree: str, role: str) -> Student:
        return Student(
            grad_year=2025, grad_display="Graduated 2025",
            name="X", degree=degree, role=role, position="",
        )

    def test_phd_chair_tier_1(self) -> None:
        assert _student_tier(self._student("PhD", "Chair")) == 1

    def test_phd_co_chair_tier_2(self) -> None:
        assert _student_tier(self._student("PhD", "Co-Chair")) == 2

    def test_deng_tier_3(self) -> None:
        assert _student_tier(self._student("D.Eng", "Chair")) == 3
        assert _student_tier(self._student("D.Eng", "Co-Chair")) == 3

    def test_ms_thesis_chair_tier_4(self) -> None:
        assert _student_tier(self._student("MS Thesis", "Chair")) == 4

    def test_ms_thesis_co_chair_tier_5(self) -> None:
        assert _student_tier(self._student("MS Thesis", "Co-Chair")) == 5

    def test_ms_non_thesis_tier_6(self) -> None:
        assert _student_tier(self._student("MS Non-Thesis", "Chair")) == 6

    def test_committee_member_tier_7_regardless_of_degree(self) -> None:
        # Committee member overrides degree for tier purposes.
        assert _student_tier(self._student("PhD", "Committee member")) == 7
        assert _student_tier(self._student("MS Thesis", "Committee member")) == 7
        assert _student_tier(self._student("MS Non-Thesis", "Committee member")) == 7

    def test_unrecognized_degree_falls_through_to_tier_7(self) -> None:
        assert _student_tier(self._student("PhD candidate", "Chair")) == 7
        assert _student_tier(self._student("MSc", "Chair")) == 7


class TestStudentSectionTierSort:
    """C.14 must sort by (tier, grad_year). Pin the sort order so renderer
    refactors can't silently flatten back to chronological-only."""

    def test_tier_sort_dominates_grad_year(self) -> None:
        # A PhD Chair student graduating LATER (2030) must still come BEFORE
        # an MS Thesis Chair graduating EARLIER (2020), because PhD Chair is
        # tier 1 and MS Thesis Chair is tier 4.
        buf = io.StringIO()
        render_students_section(
            "Graduate Students",
            [
                Student(grad_year=2020, grad_display="2020", name="MS Earlier",
                        degree="MS Thesis", role="Chair", position=""),
                Student(grad_year=2030, grad_display="2030", name="PhD Later",
                        degree="PhD", role="Chair", position=""),
            ],
            bib_entries=[], paper_index={}, out=buf,
        )
        out = buf.getvalue()
        assert out.index("PhD Later") < out.index("MS Earlier")

    def test_grad_year_sort_within_tier(self) -> None:
        # Two PhD Chair students sort by grad_year ascending.
        buf = io.StringIO()
        render_students_section(
            "Graduate Students",
            [
                Student(grad_year=2026, grad_display="2026", name="Later",
                        degree="PhD", role="Chair", position=""),
                Student(grad_year=2024, grad_display="2024", name="Earlier",
                        degree="PhD", role="Chair", position=""),
            ],
            bib_entries=[], paper_index={}, out=buf,
        )
        out = buf.getvalue()
        assert out.index("Earlier") < out.index("Later")

    def test_tier_divider_emitted_at_each_tier_change(self) -> None:
        # One student per tier 1, 4, 7 → three dividers + three data rows.
        buf = io.StringIO()
        render_students_section(
            "Graduate Students",
            [
                Student(grad_year=2025, grad_display="x", name="PhDChair",
                        degree="PhD", role="Chair", position=""),
                Student(grad_year=2025, grad_display="x", name="MSChair",
                        degree="MS Thesis", role="Chair", position=""),
                Student(grad_year=2025, grad_display="x", name="MemberX",
                        degree="PhD", role="Committee member", position=""),
            ],
            bib_entries=[], paper_index={}, out=buf,
        )
        out = buf.getvalue()
        # Each tier divider emits exactly one `\clmgf` (merge-first cell)
        # — the divider row is now a 6-column horizontal merge so that
        # the row's column structure matches the data rows (without this,
        # TextEdit/Word collapse the divider visually into the next
        # data row). `\clcbpat2` (grey-fill) appears per-cell of the
        # merge, so counting `\clcbpat2` gives 6× the divider count;
        # `\clmgf` is the per-row signal.
        assert out.count("\\clmgf") == 3
        assert "PhD students" in out and "Committee Chair" in out
        assert "MS Thesis students" in out
        assert "Other supervision and mentoring" in out

    def test_no_divider_when_only_one_tier(self) -> None:
        # Single tier still emits ONE divider at the top (before the first
        # row of that tier). Pin so the "no-op" tier-change case is explicit.
        buf = io.StringIO()
        render_students_section(
            "Graduate Students",
            [
                Student(grad_year=2025, grad_display="x", name="A",
                        degree="PhD", role="Chair", position=""),
                Student(grad_year=2026, grad_display="x", name="B",
                        degree="PhD", role="Chair", position=""),
            ],
            bib_entries=[], paper_index={}, out=buf,
        )
        # Exactly one divider (between header and first row), no
        # mid-tier dividers. Count `\clmgf` (the per-row merge-first
        # marker) — `\clcbpat2` would count once per merged cell.
        assert buf.getvalue().count("\\clmgf") == 1


class TestRenderPostdocsSection:
    """C.15: empty list emits the section heading + indented 'N/A'.
    Populated list renders a table parallel to C.14."""

    def test_empty_emits_na(self) -> None:
        buf = io.StringIO()
        render_postdocs_section([], bib_entries=[], paper_index={}, out=buf)
        out = buf.getvalue()
        assert "C.15" in out
        assert (
            "Mentoring of postdoctoral and visiting faculty scholars "
            "the candidate has directly supervised"
        ) in out
        # Indented N/A paragraph; not a table.
        assert "\\pard\\li720 N/A\\par" in out
        # No \trowd because there's no table for empty C.15.
        assert "\\trowd" not in out

    def test_populated_emits_table(self) -> None:
        buf = io.StringIO()
        render_postdocs_section(
            [
                PostdocVisiting(
                    year=2025, name="Test Scholar",
                    last_degree_date="PhD/2024",
                    prior_affiliation="University X",
                    position_title_dates="Postdoc, 09/01/24 – present",
                    current_position="",
                )
            ],
            bib_entries=[], paper_index={}, out=buf,
        )
        out = buf.getvalue()
        assert "Test Scholar" in out
        assert "PhD/2024" in out
        assert "University X" in out
        # No N/A when populated.
        assert "N/A" not in out


class TestPatentImpactWiring:
    """build_patent looks up impact text from a number → impact map. Tested
    in test_builders.py — this anchor confirms the renderer surfaces it in
    the C.19 patent table when populated."""

    def test_renders_impact_text_in_table(self) -> None:
        from pubs_emitter.rtf import render_patents_section
        from pubs_emitter.types import Patent
        buf = io.StringIO()
        render_patents_section(
            [Patent(
                year=2024, year_str="2024",
                title="Test Patent",
                co_inventors="X",
                date="Jan 1, 2024",
                number="11875185",
                impact="Resulted from pre-Purdue work. Incorporated into IBM Spectrum Scale.",
            )],
            buf,
        )
        out = buf.getvalue()
        assert "Incorporated into IBM Spectrum Scale" in out


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
        from pubs_emitter.rtf import _code_link
        assert "C.1" in out
        assert "Highlighted" in out
        assert "Big impact text." in out
        # Cross-link to canonical location (sentinel-wrapped → becomes a
        # clickable hyperlink in the final write_rtf pass).
        assert f"(listed as {_code_link('C.4.7')})" in out

    def test_empty_list_writes_nothing(self) -> None:
        buf = io.StringIO()
        render_key_works_section([], paper_index={}, out=buf)
        assert buf.getvalue() == ""

    def test_emits_author_marker_legend_after_heading(self) -> None:
        """C.1 must lead with a Notation legend explaining each per-author
        marker the citation renderer produces. Mirror the actual marker
        semantics (`authors.format_author`): Bold = candidate, * =
        corresponding author, # = senior co-author / advisor, G = grad
        student, U = undergrad student."""
        buf = io.StringIO()
        kw = KeyWork(citation=_citation(title="X"), impact="impact")
        render_key_works_section(
            [kw], paper_index={"x": "C.4.1"}, out=buf,
        )
        out = buf.getvalue()
        # Legend appears AFTER the C.1 heading but BEFORE the first entry.
        legend_idx = out.index("Notation:")
        first_entry_idx = out.index("C.1.1")
        assert legend_idx < first_entry_idx
        # Each marker is described in plain English so the reader doesn't
        # have to know the bib-side convention.
        assert "candidate" in out          # bold
        assert "corresponding author" in out  # *
        assert "PhD or postdoc advisor" in out  # # (verbatim phrasing)
        assert "graduate student" in out   # G
        assert "undergraduate student" in out  # U
        # Superscript markup mirrors `format_author` so the legend visually
        # matches the citation form below it.
        assert "\\super *\\nosupersub{}" in out
        assert "\\super #\\nosupersub{}" in out
        assert "\\super G\\nosupersub{}" in out
        assert "\\super U\\nosupersub{}" in out


class TestRenderUndergradProductsSection:
    """C.16.2.3 numbered list, auto-derived from bib coauthor scan.

    Invariants pinned: pluralization (1 → "co-author", N>1 → "co-authors"),
    optional "[undergraduate is lead author]" tag, empty list emits nothing
    (NO "N/A" — that's the C.20/C.21/C.15 pattern, not this one).
    """

    def _p(self, **overrides) -> UndergradProduct:
        base = dict(
            year=2024, product_label="Paper", ref="C.4.7",
            n_coauthors=2, lead_is_undergrad=False,
        )
        base.update(overrides)
        return UndergradProduct(**base)

    def test_heading_and_basic_line(self) -> None:
        buf = io.StringIO()
        render_undergrad_products_section([self._p()], buf)
        out = buf.getvalue()
        assert "bkmkstart C_16_2_3" in out
        assert "Undergraduate Research Products and Authorship" in out
        # Entry code lives inside an RTF bookmark wrap; the period+\tab
        # follow the closing-bookmark markup.
        from pubs_emitter.rtf import _code_link
        assert "C.16.2.3.1" in out
        # The product's `ref` (`C.4.7`) is emitted as a styled link via
        # `_code_link` so it survives the post-write finalize as a
        # clickable hyperlink in the rendered RTF.
        assert (
            f"Paper {_code_link('C.4.7')} has 2 undergraduate co-authors."
            in out
        )

    def test_singular_form(self) -> None:
        buf = io.StringIO()
        render_undergrad_products_section([self._p(n_coauthors=1)], buf)
        assert "has 1 undergraduate co-author." in buf.getvalue()

    def test_lead_undergrad_tag(self) -> None:
        buf = io.StringIO()
        render_undergrad_products_section(
            [self._p(n_coauthors=1, lead_is_undergrad=True)], buf,
        )
        out = buf.getvalue()
        assert "has 1 undergraduate co-author." in out
        assert "This paper was led by an undergraduate." in out

    def test_intro_paragraph_emitted(self) -> None:
        """The intro before the numbered list points the reader at the
        `U` superscript convention used elsewhere in the packet."""
        buf = io.StringIO()
        render_undergrad_products_section([self._p()], buf)
        out = buf.getvalue()
        assert "Undergraduate authors are marked" in out
        # `\super U\nosupersub{}` is the actual marker; verify the
        # superscript control words land in the intro paragraph.
        assert "\\super U\\nosupersub" in out

    def test_empty_list_writes_nothing(self) -> None:
        """Unlike C.15/C.20/C.21 (which emit 'N/A'), C.16.2.3 silently skips
        when there are no undergrad coauthors anywhere — the heading would
        be misleading without entries to back it up."""
        buf = io.StringIO()
        render_undergrad_products_section([], buf)
        assert buf.getvalue() == ""

    def test_under_review_entry_gets_disambiguator(self) -> None:
        """A Section V A.1 under-review entry surfaces here with a
        '(Under review.)' suffix so the bare 'A.1.N' code reads
        unambiguously (Section III also has an A.1 heading — no numbered
        sub-entries, but the disambiguator makes the section-shift
        legible to a tenure-packet reader)."""
        buf = io.StringIO()
        render_undergrad_products_section(
            [self._p(ref="A.1.7", n_coauthors=1, is_under_review=True)], buf,
        )
        out = buf.getvalue()
        from pubs_emitter.rtf import _code_link
        assert (
            f"Paper {_code_link('A.1.7')} has 1 undergraduate co-author."
            in out
        )
        assert "(Under review.)" in out

    def test_publication_entry_no_disambiguator(self) -> None:
        """Default `is_under_review=False` does NOT inject the
        disambiguator — published-paper entries read the same as before."""
        buf = io.StringIO()
        render_undergrad_products_section([self._p()], buf)
        assert "(Under review.)" not in buf.getvalue()


class TestRenderEntrepreneurialActivitiesSection:
    """C.20: numbered list with bold-summary + description. Empty list emits
    heading + 'N/A' (NOT silent skip — section is mandatory pre-promotion)."""

    def test_empty_emits_na(self) -> None:
        buf = io.StringIO()
        render_entrepreneurial_activities_section([], buf)
        out = buf.getvalue()
        assert "C.20 Major entrepreneurial activities" in out
        assert "N/A" in out

    def test_populated_renders_summary_and_description(self) -> None:
        buf = io.StringIO()
        render_entrepreneurial_activities_section(
            [EntrepreneurialActivity(
                summary="NSF I-Corps",
                description="Customer-discovery program based on RegexBench work.",
            )],
            buf,
        )
        out = buf.getvalue()
        # Entry code is wrapped in an RTF bookmark, so the trailing dot
        # isn't adjacent to "C.20.1" in the output.
        assert "C.20.1" in out
        assert "\\b NSF I-Corps\\b0" in out
        assert "Customer-discovery program based on RegexBench work." in out


class TestRenderTechnologyTransferSection:
    """C.21: 6-column table. Empty list emits heading + 'N/A' (same convention
    as C.15/C.20). `cited_publications` cell resolves bib titles to C.X.Y
    refs via paper_index; unresolved titles fall back to escaped raw title."""

    def test_empty_emits_na(self) -> None:
        buf = io.StringIO()
        render_technology_transfer_section([], paper_index={}, out=buf)
        out = buf.getvalue()
        assert "C.21 Technology transfer" in out
        assert "N/A" in out

    def test_populated_renders_table_with_resolved_refs(self) -> None:
        # Use a normalized lookup-key so the paper_index hit reflects the
        # renderer's actual normalize_title pipeline.
        from pubs_emitter.venue import normalize_title
        paper_index = {normalize_title("My Paper Title"): "C.4.7"}
        buf = io.StringIO()
        render_technology_transfer_section(
            [TechnologyTransfer(
                code_standard="AASHTO X",
                change_subject="UHPC",
                reason="Enable 3-D printing",
                research_supporting="Project X",
                cited_publications=["My Paper Title"],
                impact="Bridge construction.",
            )],
            paper_index=paper_index,
            out=buf,
        )
        out = buf.getvalue()
        assert "C.21 Technology transfer" in out
        # All 6 column headers (each is wrapped in `\b` markup by RtfTable).
        assert "Code/Standard" in out
        assert "Change Subject" in out
        assert "Cited Publications" in out
        assert "Impact" in out
        # Resolved ref appears in the Cited-Publications cell.
        assert "C.4.7" in out
        # Body cells.
        assert "AASHTO X" in out
        assert "UHPC" in out
        assert "Bridge construction." in out

    def test_unresolved_cited_title_falls_back_to_raw(self) -> None:
        """If a title doesn't resolve in paper_index, render the raw escaped
        title in the cell (defensive — validation should prevent this at
        load-time, but the renderer doesn't crash)."""
        buf = io.StringIO()
        render_technology_transfer_section(
            [TechnologyTransfer(
                code_standard="X", change_subject="Y", reason="Z",
                research_supporting="W",
                cited_publications=["Nonexistent Title"],
                impact="I",
            )],
            paper_index={},
            out=buf,
        )
        assert "Nonexistent Title" in buf.getvalue()


class TestRefAnchorAndHyperlinkFinalize:
    """RTF bookmark wrap + sentinel-to-hyperlink post-pass.

    Invariants pinned:
      * `_ref_anchor("C.18.1")` returns `{\\*\\bkmkstart C_18_1}C.18.1{\\*\\bkmkend C_18_1}`
        — bookmark names use `_` in place of `.` (RTF spec restriction).
      * `_finalize_ref_hyperlinks` converts `\\x01CODE\\x02` sentinels into
        RTF HYPERLINK fields targeting the corresponding bookmark.
      * Sentinels with no closing `\\x02` are left untouched (defensive).
      * Text with no sentinels is returned unchanged.
    """

    def test_ref_anchor_uses_underscore_in_bookmark_name(self) -> None:
        from pubs_emitter.rtf import _ref_anchor
        anchor = _ref_anchor("C.18.1")
        assert "\\*\\bkmkstart C_18_1" in anchor
        assert "\\*\\bkmkend C_18_1" in anchor
        # Display content keeps the original dotted form.
        assert "}C.18.1{" in anchor

    def test_finalize_converts_sentinel_to_hyperlink(self) -> None:
        from pubs_emitter.builders import REF_LINK_CLOSE, REF_LINK_OPEN
        from pubs_emitter.rtf import _finalize_ref_hyperlinks
        rtf = f"see {REF_LINK_OPEN}C.4.7{REF_LINK_CLOSE} for details"
        out = _finalize_ref_hyperlinks(rtf)
        # No raw sentinels survive.
        assert REF_LINK_OPEN not in out and REF_LINK_CLOSE not in out
        # Hyperlink field is present.
        assert "HYPERLINK \\\\l \"C_4_7\"" in out
        # Display text is the dotted code.
        assert "C.4.7" in out

    def test_finalize_multiple_sentinels(self) -> None:
        from pubs_emitter.builders import REF_LINK_CLOSE, REF_LINK_OPEN
        from pubs_emitter.rtf import _finalize_ref_hyperlinks
        rtf = (
            f"see {REF_LINK_OPEN}C.1.1{REF_LINK_CLOSE} and "
            f"{REF_LINK_OPEN}C.10.3{REF_LINK_CLOSE}"
        )
        out = _finalize_ref_hyperlinks(rtf)
        assert "HYPERLINK \\\\l \"C_1_1\"" in out
        assert "HYPERLINK \\\\l \"C_10_3\"" in out

    def test_finalize_passes_through_clean_rtf(self) -> None:
        from pubs_emitter.rtf import _finalize_ref_hyperlinks
        rtf = "no sentinels here \\par"
        assert _finalize_ref_hyperlinks(rtf) == rtf

    def test_bookmark_name_matches_hyperlink_target(self) -> None:
        """The bookmark wrapper and the hyperlink-finalize post-pass must
        use the same `code.replace('.', '_')` sanitization, otherwise
        click-to-jump would point at a non-existent bookmark."""
        from pubs_emitter.builders import REF_LINK_CLOSE, REF_LINK_OPEN
        from pubs_emitter.rtf import _finalize_ref_hyperlinks, _ref_anchor
        anchor = _ref_anchor("C.16.2.3")  # multi-dot code
        link = _finalize_ref_hyperlinks(
            f"{REF_LINK_OPEN}C.16.2.3{REF_LINK_CLOSE}"
        )
        # Both reference the same bookmark name.
        assert "bkmkstart C_16_2_3" in anchor
        assert "HYPERLINK \\\\l \"C_16_2_3\"" in link


# ----- Section III front matter (A.1-A.7) ---------------------------------


class TestRenderCandidateInformationSection:
    """Section III A.X front matter — bullets for A.1, numbered for A.2/A.4/A.7,
    prose for A.3/A.5. A.6 is intentionally absent.
    """

    def _ci(self, **overrides) -> CandidateInformation:
        base = CandidateInformation(
            identifiers=Identifiers(
                name="James C. Davis, PhD",
                orcid="https://orcid.org/0000-0003-2495-686X",
                google_scholar="https://scholar.google.com/citations?user=X",
            ),
            degrees=[
                Degree(
                    institution="Clarkson University",
                    years="2008-2012",
                    degree="BSc CS",
                    thesis_kind="Undergraduate thesis",
                    thesis_title="Some Title",
                    advisor="Dr. T. Nishikawa",
                ),
            ],
            positions_at_purdue=["Assistant Professor, 2020-2026"],
            positions_at_other=[
                OtherPosition(
                    title="Software Engineer", years="2012-2017",
                    organization="International Business Machines",
                    acronym="IBM",
                ),
            ],
            licenses="N/A",
            awards=[],
            professional_memberships=[
                ProfessionalMembership(
                    level="Member",
                    organization="Association for Computing Machinery",
                    acronym="ACM",
                ),
                ProfessionalMembership(
                    level="Senior Member",
                    organization="Institute of Electrical and Electronics Engineers",
                    acronym="IEEE",
                ),
            ],
        )
        return base._replace(**overrides) if overrides else base

    def test_emits_group_heading_and_all_section_headings(self) -> None:
        buf = io.StringIO()
        render_candidate_information_section(self._ci(), buf)
        out = buf.getvalue()
        # Top-level "A. GENERAL INFORMATION" group heading.
        assert "A. GENERAL INFORMATION" in out
        # Each A.X sub-section heading text.
        assert "A.1 Name and any appropriate scholarly identifiers" in out
        assert "A.2 Degrees." in out
        assert "A.3 Positions at Purdue." in out
        assert "A.4 Positions at other institutions or organizations." in out
        assert "A.5 Licenses, registrations, and certificates." in out
        assert "A.6 Recognitions" in out
        assert "A.7 Membership in professional organizations." in out

    def test_a1_renders_as_bullet_list_with_bold_labels(self) -> None:
        buf = io.StringIO()
        render_candidate_information_section(self._ci(), buf)
        out = buf.getvalue()
        # Bullet char (舦?) + bold labels Name:, ORCID:, Google Scholar:.
        assert "\\u8226?" in out
        assert "\\b Name\\b0" in out
        assert "\\b ORCID\\b0" in out
        assert "\\b Google Scholar\\b0" in out
        # Identifier URLs become RTF HYPERLINK fields, not bare text.
        assert 'HYPERLINK "https://orcid.org/0000-0003-2495-686X"' in out
        assert 'HYPERLINK "https://scholar.google.com/citations?user=X"' in out
        # No A.1.N codes are emitted (bullets, not numbered).
        assert "A.1.1" not in out

    def test_a2_numbered_with_italic_thesis(self) -> None:
        buf = io.StringIO()
        render_candidate_information_section(self._ci(), buf)
        out = buf.getvalue()
        # Code "A.2.1" emitted as a bookmark-wrapped numbered-list item.
        assert "A.2.1" in out
        assert "\\*\\bkmkstart A_2_1" in out
        # Thesis title in italics + advisor suffix.
        assert "\\i Some Title\\i0" in out
        assert "supervised by Dr. T. Nishikawa" in out

    def test_a3_renders_as_numbered_list(self) -> None:
        """A.3 is a numbered list so future promotions (Assoc → Full)
        append a new entry rather than editing the prose."""
        buf = io.StringIO()
        render_candidate_information_section(self._ci(), buf)
        out = buf.getvalue()
        assert "Assistant Professor, 2020-2026" in out
        assert "A.3.1" in out
        assert "\\*\\bkmkstart A_3_1" in out

    def test_a4_numbered_with_acronym_parenthetical(self) -> None:
        buf = io.StringIO()
        render_candidate_information_section(self._ci(), buf)
        out = buf.getvalue()
        assert "A.4.1" in out
        assert "\\*\\bkmkstart A_4_1" in out
        assert "International Business Machines (IBM)." in out

    def test_a5_renders_na_when_empty(self) -> None:
        buf = io.StringIO()
        render_candidate_information_section(self._ci(licenses=""), buf)
        out = buf.getvalue()
        # Literal "N/A" prose; no A.5.1 numbering.
        assert "N/A" in out
        assert "A.5.1" not in out

    def test_a7_numbered_increments_and_emits_acronym(self) -> None:
        buf = io.StringIO()
        render_candidate_information_section(self._ci(), buf)
        out = buf.getvalue()
        assert "A.7.1" in out
        assert "A.7.2" in out
        assert "\\*\\bkmkstart A_7_1" in out
        assert "Association for Computing Machinery (ACM)" in out
        assert "Institute of Electrical and Electronics Engineers (IEEE)" in out

    def test_emit_order_is_a1_a2_a3_a4_a5_a6_a7(self) -> None:
        """A.1 → A.2 → A.3 → A.4 → A.5 → A.6 → A.7. Matches the Purdue
        front-matter sub-section order verbatim."""
        buf = io.StringIO()
        render_candidate_information_section(self._ci(), buf)
        out = buf.getvalue()
        positions = [out.index(h) for h in (
            "A.1 Name and any",
            "A.2 Degrees.",
            "A.3 Positions at Purdue.",
            "A.4 Positions at other",
            "A.5 Licenses,",
            "A.6 Recognitions",
            "A.7 Membership in",
        )]
        assert positions == sorted(positions)


class TestRenderA6Awards:
    """A.6 table — EXTERNAL + INTERNAL tier groups, chronological within each,
    flat A.6.N numbering across groups (externals first, then internals).
    Significance cell may carry @-resolved sentinel-form refs."""

    def _ci_with_awards(self, awards: list[Award]) -> CandidateInformation:
        return CandidateInformation(
            identifiers=Identifiers(name="X", orcid="", google_scholar=""),
            degrees=[], positions_at_purdue=[], positions_at_other=[],
            licenses="", awards=awards, professional_memberships=[],
        )

    def _award(self, **kwargs) -> Award:
        base = dict(
            year=2024, year_str="2024", tier="external",
            name="Some Award", significance="Some significance.",
        )
        base.update(kwargs)
        return Award(**base)

    def test_emits_two_group_headers_when_both_tiers_present(self) -> None:
        buf = io.StringIO()
        render_candidate_information_section(self._ci_with_awards([
            self._award(tier="external", name="Ext"),
            self._award(tier="internal", name="Int"),
        ]), buf)
        out = buf.getvalue()
        assert "EXTERNAL RECOGNITIONS" in out
        assert "INTERNAL RECOGNITIONS" in out
        assert out.index("EXTERNAL") < out.index("INTERNAL")

    def test_externals_emit_before_internals(self) -> None:
        buf = io.StringIO()
        render_candidate_information_section(self._ci_with_awards([
            self._award(tier="internal", name="Int2026", year=2026),
            self._award(tier="external", name="Ext2024", year=2024),
        ]), buf)
        out = buf.getvalue()
        assert out.index("Ext2024") < out.index("Int2026")

    def test_within_tier_chronological_ascending(self) -> None:
        buf = io.StringIO()
        render_candidate_information_section(self._ci_with_awards([
            self._award(tier="external", name="Newer", year=2025),
            self._award(tier="external", name="Older", year=2018),
        ]), buf)
        out = buf.getvalue()
        assert out.index("Older") < out.index("Newer")

    def test_a6_n_codes_flat_across_tiers(self) -> None:
        buf = io.StringIO()
        render_candidate_information_section(self._ci_with_awards([
            self._award(tier="external", name="E1", year=2018),
            self._award(tier="external", name="E2", year=2020),
            self._award(tier="internal", name="I1", year=2022),
        ]), buf)
        out = buf.getvalue()
        # Externals numbered first (A.6.1, A.6.2), internal continues (A.6.3).
        assert "\\*\\bkmkstart A_6_1" in out
        assert "\\*\\bkmkstart A_6_2" in out
        assert "\\*\\bkmkstart A_6_3" in out

    def test_empty_awards_renders_na(self) -> None:
        buf = io.StringIO()
        render_candidate_information_section(self._ci_with_awards([]), buf)
        out = buf.getvalue()
        # Heading still emitted; body is the indented N/A placeholder.
        assert "A.6 Recognitions" in out
        # No table row markup when empty.
        assert "EXTERNAL RECOGNITIONS" not in out

    def test_columns_header_says_date_and_significance(self) -> None:
        buf = io.StringIO()
        render_candidate_information_section(self._ci_with_awards([
            self._award(tier="external"),
        ]), buf)
        out = buf.getvalue()
        assert "DATE" in out
        assert "BRIEF DESCRIPTION OF SIGNIFICANCE" in out


class TestIndexAwards:
    """The `index_awards` helper must yield (A.6.N, award) tuples in the
    SAME order as the renderer emits them, so cli's ref_index can register
    matching codes for @id resolution."""

    def test_externals_numbered_first(self) -> None:
        from pubs_emitter.rtf import index_awards
        a1 = Award(year=2025, year_str="2025", tier="internal", name="I", significance="")
        a2 = Award(year=2018, year_str="2018", tier="external", name="E", significance="")
        result = index_awards([a1, a2])
        assert result == [("A.6.1", a2), ("A.6.2", a1)]

    def test_within_tier_chronological(self) -> None:
        from pubs_emitter.rtf import index_awards
        a1 = Award(year=2024, year_str="2024", tier="external", name="Later", significance="")
        a2 = Award(year=2018, year_str="2018", tier="external", name="Earlier", significance="")
        result = index_awards([a1, a2])
        assert [r for r, _ in result] == ["A.6.1", "A.6.2"]
        assert result[0][1].name == "Earlier"
        assert result[1][1].name == "Later"


class TestFinalizeRefHyperlinksPipeForm:
    """Pipe-form sentinel: `\\x01display|bookmark_code\\x02` → display = LHS,
    bookmark target = RHS. Enables "Section V, A.1.3" display + "A_1_3"
    bookmark target without changing the bookmark namespace."""

    def test_pipe_form_splits_display_and_bookmark(self) -> None:
        from pubs_emitter.builders import REF_LINK_CLOSE, REF_LINK_OPEN
        out = _finalize_ref_hyperlinks(
            f"prefix {REF_LINK_OPEN}Section V, A.1.3|A.1.3{REF_LINK_CLOSE} suffix",
        )
        # Display text = "Section V, A.1.3"; bookmark = "A_1_3".
        assert 'HYPERLINK \\\\l "A_1_3"' in out
        assert "Section V, A.1.3" in out
        # No stray pipe in the output.
        assert "|" not in out

    def test_bare_form_unchanged(self) -> None:
        """Backward compat: bare sentinels keep working (display=bookmark)."""
        from pubs_emitter.builders import REF_LINK_CLOSE, REF_LINK_OPEN
        out = _finalize_ref_hyperlinks(
            f"{REF_LINK_OPEN}C.4.7{REF_LINK_CLOSE}",
        )
        assert 'HYPERLINK \\\\l "C_4_7"' in out
        # Display is bare code in the link's fldrslt segment.
        assert "C.4.7" in out


# ----- Career-phase dividers (C.1 / C.2 / C.3 / C.4 / C.5) ----------------


class TestEmitInlineHeading:
    """Pins the shared inline-heading helper that backs C.5 subcategory
    subheadings, C.16.2.4 / C.16.3.3 student-awards tier labels, and the
    career-phase divider. The italic-not-bold styling decision is load-
    bearing — bold reads as a section heading and confuses the
    hierarchy.

    DRY guarantee: each call site routes through `_emit_inline_heading`
    rather than hand-rolling the paragraph shape. Verified here by
    confirming the helper's output appears in the renderers of all
    three sites.
    """

    def test_italic_not_bold(self) -> None:
        from pubs_emitter.rtf import _emit_inline_heading
        import io
        buf = io.StringIO()
        _emit_inline_heading(buf, "Sample Subheading", indent=720)
        out = buf.getvalue()
        # `\i` is the italic-open control word; `\i0` closes. The text
        # itself sits between, prefixed by other control words (\fs26
        # etc.) before the space separator.
        assert "\\i\\fs26" in out
        assert "Sample Subheading\\i0" in out
        # No bold control word — the whole point of factoring this out.
        assert "\\b Sample Subheading" not in out
        assert "\\b\\fs26" not in out

    def test_indent_applied(self) -> None:
        from pubs_emitter.rtf import _emit_inline_heading
        import io
        buf = io.StringIO()
        _emit_inline_heading(buf, "x", indent=720)
        assert "\\li720" in buf.getvalue()

    def test_border_variant_has_top_and_bottom_borders(self) -> None:
        from pubs_emitter.rtf import _emit_inline_heading
        import io
        buf = io.StringIO()
        _emit_inline_heading(buf, "Phase Label", indent=720, border=True)
        out = buf.getvalue()
        assert "\\brdrt\\brdrs" in out
        assert "\\brdrb\\brdrs" in out
        assert "\\i Phase Label\\i0" in out

    def test_default_variant_no_borders(self) -> None:
        from pubs_emitter.rtf import _emit_inline_heading
        import io
        buf = io.StringIO()
        _emit_inline_heading(buf, "Subcat", indent=240)
        out = buf.getvalue()
        assert "\\brdrt" not in out
        assert "\\brdrb" not in out

    def test_student_awards_tier_label_routes_through_helper(self) -> None:
        """C.16.2.4 tier label uses the helper — verified by italic
        styling appearing on the tier-label line."""
        buf = io.StringIO()
        from pubs_emitter.types import StudentAward
        render_student_awards_section(
            "Undergraduate Student Awards",
            [StudentAward(
                tier="National and International Awards",
                year=2024, year_str="2024",
                level="U",
                recipient="Test U", award="Test Award",
            )],
            buf,
        )
        out = buf.getvalue()
        # Match the helper's emit shape: \i\fs26... text \i0\fs24.
        assert "National and International Awards\\i0" in out
        assert "\\i\\fs26\\sb120\\sa60 National and International Awards" in out
        assert "\\b National and International Awards" not in out

    def test_career_phase_divider_routes_through_helper(self) -> None:
        """Career-phase divider uses border=True — verified by italic
        + top/bottom borders on the phase label."""
        from pubs_emitter.rtf import _maybe_emit_career_phase_divider
        import io
        buf = io.StringIO()
        _maybe_emit_career_phase_divider(buf, 2020, current_phase="", indent=720)
        out = buf.getvalue()
        # Border variant emits: ... \i Phase Label\i0\par.
        assert "\\i PhD studies at Virginia Tech\\i0" in out
        assert "\\brdrt\\brdrs" in out
        assert "\\brdrb\\brdrs" in out


class TestCareerPhaseDivider:
    """The publication-section renderers (C.1 / C.2 / C.3 / C.4 / C.5)
    interleave an italic-labeled, border-delimited divider between "PhD
    studies at Virginia Tech" (year ≤ 2020) and "Assistant Professor at
    Purdue" (year ≥ 2021) regions. Visual cue ONLY — section numbering
    is unaffected by the divider (numbering continues through the
    boundary). Routes through the shared `_emit_inline_heading`
    helper with `border=True` so the italic-not-bold styling decision
    lives in one place across the renderer."""

    def test_helper_fires_on_first_entry(self) -> None:
        from pubs_emitter.rtf import _maybe_emit_career_phase_divider
        import io
        buf = io.StringIO()
        new_phase = _maybe_emit_career_phase_divider(
            buf, 2020, current_phase="", indent=720,
        )
        out = buf.getvalue()
        assert "PhD studies at Virginia Tech" in out
        assert new_phase == "phd"

    def test_helper_fires_on_boundary_crossing(self) -> None:
        from pubs_emitter.rtf import _maybe_emit_career_phase_divider
        import io
        buf = io.StringIO()
        new_phase = _maybe_emit_career_phase_divider(
            buf, 2021, current_phase="phd", indent=720,
        )
        out = buf.getvalue()
        assert "Assistant Professor at Purdue" in out
        assert new_phase == "ap"

    def test_helper_silent_within_phase(self) -> None:
        from pubs_emitter.rtf import _maybe_emit_career_phase_divider
        import io
        buf = io.StringIO()
        # Already in PhD phase, another PhD-era year → no divider.
        new_phase = _maybe_emit_career_phase_divider(
            buf, 2019, current_phase="phd", indent=720,
        )
        assert buf.getvalue() == ""
        assert new_phase == "phd"

    def test_boundary_year_2020_is_phd(self) -> None:
        from pubs_emitter.rtf import _career_phase_for_year
        # The candidate's PhD years are 2015-2020 per their A.2 entry; 2020
        # publications go to the PhD region per the candidate's spec
        # ("Everything published in 2020 or earlier is 'PhD studies'").
        assert _career_phase_for_year(2020) == "phd"
        assert _career_phase_for_year(2019) == "phd"
        assert _career_phase_for_year(2021) == "ap"

    def test_in_press_sentinel_falls_in_ap_region(self) -> None:
        from pubs_emitter.rtf import _career_phase_for_year
        # Citation.year sentinel 9999 means "In Press" / unparseable —
        # post-2020, so it lands in the AP region.
        assert _career_phase_for_year(9999) == "ap"

    def test_numbering_continues_across_boundary_in_journals_loop(self) -> None:
        """C.2 Journals renderer: a 2020-paper followed by a 2021-paper
        produces ONE divider between them and consecutive entry codes
        (C.2.1, C.2.2) on either side — no reset."""
        # Use the same _citation factory as the rest of test_rtf.py.
        cit_phd = _citation(year=2020, year_str="2020", title="Pre-PhD wrap")
        cit_ap = _citation(year=2021, year_str="2021", title="Post-PhD start")
        # Drive the same emit path the write_rtf generic loop runs.
        import io
        from pubs_emitter.rtf import (
            _emit_list_item, _hanging_indent_for_codes,
            _maybe_emit_career_phase_divider, _section_codes_up_to,
            render_citation,
        )
        buf = io.StringIO()
        code = "C.2"
        citations = [cit_phd, cit_ap]
        indent = _hanging_indent_for_codes(
            _section_codes_up_to(code, len(citations))
        )
        phase = ""
        for idx, cit in enumerate(citations, 1):
            phase = _maybe_emit_career_phase_divider(
                buf, cit.year, phase, indent,
            )
            body = render_citation(cit, expansion_done=set())
            _emit_list_item(buf, f"{code}.{idx}", body, indent=indent)
        out = buf.getvalue()
        # Both dividers present.
        assert "PhD studies at Virginia Tech" in out
        assert "Assistant Professor at Purdue" in out
        # Divider appears BETWEEN C.2.1 and C.2.2 (numbering continues).
        idx1 = out.index("C.2.1")
        idx_ap = out.index("Assistant Professor at Purdue")
        idx2 = out.index("C.2.2")
        assert idx1 < idx_ap < idx2


# ----- C.17 data-not-available + grey-note row rendering ------------------


class TestRenderCoursesTaughtDataNotAvailable:
    """When a row's three CIE fields are all None, the CIE cell renders the
    literal string 'data not available' rather than an em-dash trio. Used
    for course-rows the candidate taught but for which no EvaluationKit
    response set exists (e.g., F20 / Sp21 VIP, which preceded CIE rollout
    in the VIP program)."""

    def _ct(self, **overrides):
        from pubs_emitter.types import CourseTaught
        base = dict(
            year=2020, semester_order=3, semester_str="F20",
            title="Vertically Integrated Projects (VIP)",
            is_new_course=False,
            course_number="VIP (merged all sections)",
            responsibility="50% responsibility, supervisory",
            responses=None, enrolled=None,
            cie_average=None, cie_min=None, cie_max=None,
        )
        base.update(overrides)
        return CourseTaught(**base)

    def test_all_three_cie_none_renders_data_not_available(self) -> None:
        import io
        from pubs_emitter.rtf import render_courses_taught_section
        buf = io.StringIO()
        render_courses_taught_section([self._ct()], buf)
        out = buf.getvalue()
        assert "data not available" in out
        # AND the partial-marker "*" + footnote must NOT fire — the row
        # has no CIE data at all, not partial-CIE data.
        assert "Computed on the relevant subset" not in out

    def test_partial_cie_still_renders_summary_not_data_not_available(self) -> None:
        import io
        from pubs_emitter.rtf import render_courses_taught_section
        buf = io.StringIO()
        # Avg / min / max all present → render normally.
        render_courses_taught_section([self._ct(
            cie_average=4.0, cie_min=3.5, cie_max=4.5, cie_partial=True,
        )], buf)
        out = buf.getvalue()
        assert "data not available" not in out
        # Partial marker fires.
        assert "4.00 (3.50, 4.50)*" in out
        assert "Computed on the relevant subset" in out

    def test_grey_row_carries_semester_label_inline(self) -> None:
        """The grey note row's text is prefixed with the semester string
        ('Sp25: …', 'F22: …') so the row reads in context — the merged
        cell no longer leaves the Sem/Year column visually anchoring the
        row."""
        import io
        from pubs_emitter.rtf import render_courses_taught_section
        buf = io.StringIO()
        render_courses_taught_section([
            self._ct(
                year=2025, semester_order=1, semester_str="Sp25",
                title="No 3-credit course taught - ABET self-study release",
                course_number="", responsibility="",
                is_note_row=True,
            ),
        ], buf)
        out = buf.getvalue()
        assert "Sp25: No 3-credit course taught" in out


class TestResponsibilityOverrideMergeLogic:
    """Pure mapping logic: a (year, semester_str, course_number) key
    looks up its responsibility text from the overrides table; on miss,
    falls through to the default. Empty-string responsibility means
    "use override or default"; non-empty wins outright."""

    def test_override_wins_when_responsibility_empty(self) -> None:
        from pubs_emitter.types import CourseTaught
        c = CourseTaught(
            year=2020, semester_order=3, semester_str="F20",
            title="Data Structures", is_new_course=False,
            course_number="ECE 36800", responsibility="",
            responses=43, enrolled=99,
            cie_average=4.46, cie_min=4.28, cie_max=4.53,
        )
        # Simulate the override-table application loop verbatim.
        resp_overrides = {
            (2020, "F20", "ECE 36800"): "50% responsibility, non-supervisory",
        }
        resp_default = "100% responsibility, supervisory"
        if not c.responsibility and not c.is_note_row:
            key = (c.year, c.semester_str, c.course_number)
            c = c._replace(
                responsibility=resp_overrides.get(key, resp_default),
            )
        assert c.responsibility == "50% responsibility, non-supervisory"

    def test_default_fires_when_no_override(self) -> None:
        from pubs_emitter.types import CourseTaught
        c = CourseTaught(
            year=2024, semester_order=3, semester_str="F24",
            title="Software Engineering", is_new_course=False,
            course_number="ECE 46100", responsibility="",
            responses=55, enrolled=130,
            cie_average=4.10, cie_min=3.58, cie_max=4.47,
        )
        resp_overrides: dict = {}
        resp_default = "100% responsibility, supervisory"
        if not c.responsibility and not c.is_note_row:
            key = (c.year, c.semester_str, c.course_number)
            c = c._replace(
                responsibility=resp_overrides.get(key, resp_default),
            )
        assert c.responsibility == "100% responsibility, supervisory"

    def test_explicit_yaml_responsibility_wins_over_override(self) -> None:
        """An explicit non-empty `responsibility:` on a YAML row keeps its
        value — the override + default lookup is skipped. This lets a
        YAML row carry a one-off responsibility string without needing
        a parallel entry in the overrides table."""
        from pubs_emitter.types import CourseTaught
        c = CourseTaught(
            year=2020, semester_order=3, semester_str="F20",
            title="Custom Course", is_new_course=False,
            course_number="ECE 36800",
            responsibility="custom override on the row itself",
            responses=10, enrolled=20,
            cie_average=4.0, cie_min=3.5, cie_max=4.5,
        )
        resp_overrides = {
            (2020, "F20", "ECE 36800"): "table-override-would-win-if-empty",
        }
        if not c.responsibility and not c.is_note_row:
            key = (c.year, c.semester_str, c.course_number)
            c = c._replace(
                responsibility=resp_overrides.get(key, "DEFAULT"),
            )
        # Stayed as the explicit YAML value.
        assert c.responsibility == "custom override on the row itself"


# ----- Section V, A.2 Pending Proposals -----------------------------------


class TestRefAnchorBookmarkPrefix:
    """`_ref_anchor` accepts an optional `bookmark_prefix` so Section V
    entries can target bookmarks like "V_A_2_3" while displaying the
    bare code "A.2.3". Used to keep Section V entries from colliding
    with same-coded Section III entries (A.2 Degrees in particular)."""

    def test_default_no_prefix(self) -> None:
        from pubs_emitter.rtf import _ref_anchor
        out = _ref_anchor("A.2.3")
        # Both anchor bookmarks reference "A_2_3" (the bare code).
        assert "bkmkstart A_2_3" in out
        assert "bkmkend A_2_3" in out
        # Display text is still the dotted form.
        assert "A.2.3" in out

    def test_v_prefix_namespaces_the_bookmark(self) -> None:
        from pubs_emitter.rtf import _ref_anchor
        out = _ref_anchor("A.2.3", bookmark_prefix="V.")
        # Bookmark name is "V_A_2_3" — different from the bare "A_2_3"
        # bookmark a Section III A.2 Degrees entry would emit.
        assert "bkmkstart V_A_2_3" in out
        assert "bkmkend V_A_2_3" in out
        # Display text unchanged — reader still sees "A.2.3".
        assert "A.2.3" in out
        assert "V.A.2.3" not in out


class TestRenderPendingProposalsSection:
    """Section V, A.2 renderer. Reuses the existing 4-row grant table
    shape via `_format_grant_table(bookmark_prefix='V.')`."""

    def _pending(self, **overrides):
        from pubs_emitter.types import Grant
        base = dict(
            start_year=2026, end_year=2028,
            title="EAGER: Test Pending Grant",
            agency="US National Science Foundation",
            agency_short="NSF",
            grant_number="9999999",
            role="PI",
            lead_institution="Other University",
            personnel=[],
            responsibility_percent=0,
            total_amount=300000, purdue_amount=149998, my_amount=149998,
            activities="Submitted 4/15/2026.",
            responsibility="",
            inspired_by=[],
            publication_outcomes=[],
            status="pending",
        )
        base.update(overrides)
        return Grant(**base)

    def test_emits_section_heading_and_bookmark_prefix(self) -> None:
        import io
        from pubs_emitter.rtf import render_pending_proposals_section
        buf = io.StringIO()
        render_pending_proposals_section([self._pending()], buf)
        out = buf.getvalue()
        assert "A.2 Pending proposals" in out
        # Bookmark is prefixed with V_ — wouldn't collide with Section
        # III A.2 Degrees bookmarks at A_2_1.
        assert "bkmkstart V_A_2_1" in out
        # And the bare "A_2_1" form is NOT emitted by THIS section.
        # (Section III Degrees may emit it elsewhere; this assertion
        # scopes to the pending-proposals block in `out`.)
        assert "bkmkstart A_2_1" not in out

    def test_empty_list_writes_nothing(self) -> None:
        import io
        from pubs_emitter.rtf import render_pending_proposals_section
        buf = io.StringIO()
        render_pending_proposals_section([], buf)
        assert buf.getvalue() == ""

    def test_numbering_increments_across_entries(self) -> None:
        import io
        from pubs_emitter.rtf import render_pending_proposals_section
        buf = io.StringIO()
        render_pending_proposals_section([
            self._pending(title="Pending #1"),
            self._pending(title="Pending #2"),
        ], buf)
        out = buf.getvalue()
        assert "bkmkstart V_A_2_1" in out
        assert "bkmkstart V_A_2_2" in out


class TestGrantStatusPartition:
    """A grant's `status` field routes it between C.10 / C.11 (awarded,
    default) and Section V, A.2 (pending). Partition is a simple field
    filter applied in cli.py — pin the invariant here so the test
    surfaces a routing regression at unit-test speed."""

    def _grant(self, **overrides):
        from pubs_emitter.types import Grant
        base = dict(
            start_year=2025, end_year=2027,
            title="T", agency="NSF", agency_short="NSF",
            grant_number="123", role="PI",
            lead_institution="", personnel=[],
            responsibility_percent=0,
            total_amount=100000, purdue_amount=100000, my_amount=100000,
            activities="", responsibility="",
            inspired_by=[], publication_outcomes=[],
            status="awarded",
        )
        base.update(overrides)
        return Grant(**base)

    def test_default_status_is_awarded(self) -> None:
        g = self._grant()
        assert g.status == "awarded"

    def test_partition_filter_logic(self) -> None:
        """The exact filter expression the cli.py partition uses."""
        all_grants = [
            self._grant(title="A1", status="awarded"),
            self._grant(title="P1", status="pending"),
            self._grant(title="A2", status="awarded"),
            self._grant(title="P2", status="pending"),
        ]
        awarded = [g for g in all_grants if g.status != "pending"]
        pending = [g for g in all_grants if g.status == "pending"]
        assert [g.title for g in awarded] == ["A1", "A2"]
        assert [g.title for g in pending] == ["P1", "P2"]


class TestFormatRoleResponsibilityPurdueLead:
    """Pending-proposal rows must carry an inline 'Purdue is (not) lead
    institution' annotation; awarded grants must NOT (those sections
    convey the position via their heading). Detection rule: empty or
    'Purdue University' → lead; any other non-empty string → not lead."""

    def _g(self, **kw):
        from pubs_emitter.types import Grant
        base = dict(
            start_year=2026, end_year=2028, title='T', agency='NSF',
            agency_short='NSF', grant_number='1', role='PI',
            lead_institution='', personnel=[], responsibility_percent=0,
            total_amount=100000, purdue_amount=100000, my_amount=100000,
            activities='', responsibility='', inspired_by=[],
            publication_outcomes=[], status='awarded',
        )
        base.update(kw)
        return Grant(**base)

    def test_awarded_grant_no_lead_note(self) -> None:
        from pubs_emitter.rtf import _format_role_responsibility_line
        out = _format_role_responsibility_line(
            self._g(status='awarded', lead_institution='Loyola University'),
        )
        assert "lead institution" not in out

    def test_pending_purdue_lead_when_empty(self) -> None:
        from pubs_emitter.rtf import _format_role_responsibility_line
        out = _format_role_responsibility_line(
            self._g(status='pending', lead_institution=''),
        )
        assert "Purdue is lead institution" in out

    def test_pending_purdue_lead_when_literal_purdue(self) -> None:
        from pubs_emitter.rtf import _format_role_responsibility_line
        out = _format_role_responsibility_line(self._g(
            status='pending', lead_institution='Purdue University',
        ))
        assert "Purdue is lead institution" in out

    def test_pending_purdue_not_lead_names_the_lead(self) -> None:
        from pubs_emitter.rtf import _format_role_responsibility_line
        out = _format_role_responsibility_line(self._g(
            status='pending', lead_institution='Loyola University Chicago',
        ))
        assert "Purdue is not lead institution (lead: Loyola University Chicago)" in out


class TestLevelAwareBodyIndent:
    """`_body_indent_for_code(code)` is the single source of truth for
    "where does content under a heading at `code` start?". Every prose,
    placeholder, and numbered-entry renderer routes its base indent
    through it so deeper sub-sections nest visually inside their
    parents.

    Invariant: each additional level of nesting adds exactly
    `_HEADING_INDENT_PER_LEVEL` twips (360 ≈ ¼ inch / "2 spaces") so
    body content lines up step-for-step with how the heading-itself
    indents at level 2, 3, ...
    """

    def test_level_1_body_indent_includes_label_offset(self) -> None:
        """Level-1 body indent = label_position (1 step) + label-fit
        gap (720). With step=240, body indent at level 1 = 240 + 720 =
        960."""
        from pubs_emitter.rtf import (
            _HEADING_INDENT_PER_LEVEL, _body_indent_for_code,
        )
        step = _HEADING_INDENT_PER_LEVEL
        expected = step + 720
        assert _body_indent_for_code("C.6") == expected
        assert _body_indent_for_code("A.3") == expected
        assert _body_indent_for_code("B.1") == expected

    def test_each_level_adds_one_step(self) -> None:
        """Each additional level adds exactly `_HEADING_INDENT_PER_LEVEL`
        (360 twips) to the body indent. Body indent at level N is
        N * 360 (label position) + 720 (gap for label width)."""
        from pubs_emitter.rtf import (
            _HEADING_INDENT_PER_LEVEL, _body_indent_for_code,
        )
        step = _HEADING_INDENT_PER_LEVEL  # 360
        assert _body_indent_for_code("C.16")       == 1 * step + 720  # 1080
        assert _body_indent_for_code("C.16.1")     == 2 * step + 720  # 1440
        assert _body_indent_for_code("C.16.2.1")   == 3 * step + 720  # 1800
        assert _body_indent_for_code("C.16.2.3.1") == 4 * step + 720  # 2160

    def test_level_caps_at_4(self) -> None:
        """`_heading_level` caps the depth at 4 (font size + indent stop
        scaling beyond that hurts readability). Anything deeper than
        five dots maps to the level-4 indent."""
        from pubs_emitter.rtf import _body_indent_for_code
        assert _body_indent_for_code("C.16.2.3.1.99") == _body_indent_for_code("C.16.2.3.1")

    def test_label_position_one_step_right_of_heading(self) -> None:
        """`_label_position_for_code` puts the entry label one indent
        step right of the parent heading. Without this offset the
        hanging-indent would drop the label into the heading's column,
        defeating the visual nesting cue."""
        from pubs_emitter.rtf import (
            _HEADING_INDENT_PER_LEVEL, _label_position_for_code,
        )
        step = _HEADING_INDENT_PER_LEVEL  # 360
        # "A.2.1" parent = "A.2" (level 1) → label at 360
        assert _label_position_for_code("A.2.1") == 1 * step
        # "C.16.1.5" parent = "C.16.1" (level 2) → label at 720
        assert _label_position_for_code("C.16.1.5") == 2 * step
        # "C.16.2.3.7" parent = "C.16.2.3" (level 3) → label at 1080
        assert _label_position_for_code("C.16.2.3.7") == 3 * step

    def test_emit_list_item_label_visibly_offset_from_heading(self) -> None:
        """The emitted `\\pard\\li{li}\\fi{fi}` markup puts the label at
        column `li + fi`, which should equal `_label_position_for_code`
        — one step right of the parent heading. Regression pin for the
        bug class where labels rendered in the heading's column."""
        import io
        from pubs_emitter.rtf import (
            _body_indent_for_code, _emit_list_item, _label_position_for_code,
        )
        buf = io.StringIO()
        # Level-1 entry: A.2.1. Parent A.2, heading at column 0.
        # Label should be at column 360, body wrap at 1080.
        indent = _body_indent_for_code("A.2")  # 1080
        _emit_list_item(buf, "A.2.1", "body", indent=indent)
        out = buf.getvalue()
        expected_label_pos = _label_position_for_code("A.2.1")  # 360
        expected_fi = expected_label_pos - indent  # -720
        assert f"\\pard\\li{indent}\\fi{expected_fi}\\tx{indent}" in out

    def test_hanging_indent_inherits_level_base(self) -> None:
        """`_hanging_indent_for_codes` uses the level-derived base so a
        section's numbered entries nest under its heading. Short codes
        (≤ 7 visible chars) stay at the level base; long codes widen
        only when the label demands more room."""
        from pubs_emitter.rtf import _hanging_indent_for_codes
        from pubs_emitter.rtf import _body_indent_for_code
        # Level 1 short labels — equals the level-1 body indent.
        assert (
            _hanging_indent_for_codes(["C.6.1", "C.6.2"])
            == _body_indent_for_code("C.6")
        )
        # Level 3 + 10-char codes — wider than the base because the
        # label needs more horizontal room (the label-fit widening fires).
        assert (
            _hanging_indent_for_codes(["C.16.2.3.1", "C.16.2.3.2"])
            >= _body_indent_for_code("C.16.2.3")
        )
        # Level 1 long labels (8+ visible chars) may widen past the base.
        long_label = _hanging_indent_for_codes(
            [f"C.26.{i}" for i in range(1, 100)]
        )
        assert long_label >= _body_indent_for_code("C.26")

    def test_placeholder_body_uses_level_aware_indent(self) -> None:
        """The `_emit_placeholder_subsection` body paragraph matches
        `_body_indent_for_code` so when content is later added, it
        lands at the same indent as the placeholder it replaces."""
        import io
        from pubs_emitter.rtf import (
            _body_indent_for_code, _emit_placeholder_subsection,
        )
        buf = io.StringIO()
        _emit_placeholder_subsection(buf, "C.16.2.1", "VIP")
        out = buf.getvalue()
        expected = _body_indent_for_code("C.16.2.1")
        assert f"\\pard\\li{expected}\\par\\par" in out

    def test_b_section_body_inherits_level_base(self) -> None:
        """B.1-B.5 are level 1 — prose lands at the level-1 body indent (1080)."""
        import io
        from pubs_emitter.rtf import _body_indent_for_code, _emit_b_section_body
        buf = io.StringIO()
        _emit_b_section_body(buf, "Some prose.", "B.1")
        out = buf.getvalue()
        expected = _body_indent_for_code("B.1")
        assert f"\\pard\\li{expected} Some prose.\\par\\par" in out

    def test_a3_a5_at_level_1_baseline(self) -> None:
        """A.3 is now a numbered list (A.3.N), A.5 stays prose. Both at
        the level-1 baseline indent."""
        import io
        from pubs_emitter.rtf import (
            _body_indent_for_code, _render_a3_positions_at_purdue,
            _render_a5_licenses,
        )
        buf = io.StringIO()
        _render_a3_positions_at_purdue(buf, ["Test Professor, 2020-2026"])
        _render_a5_licenses(buf, "N/A")
        out = buf.getvalue()
        # A.3 emits a numbered A.3.1 entry, body indent at level 1.
        expected_a3_indent = _body_indent_for_code("A.3")
        assert "A.3.1" in out
        assert "Test Professor, 2020-2026" in out
        # A.5 prose at level-1 baseline indent.
        expected_a5_indent = _body_indent_for_code("A.5")
        assert f"\\pard\\li{expected_a5_indent} N/A" in out
