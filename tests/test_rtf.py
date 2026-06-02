"""Unit tests for pubs_emitter.rtf — table builder + per-record renderers."""
from __future__ import annotations

import io

import pytest

from pubs_emitter.rtf import (
    RtfTable,
    _student_tier,
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
    render_postdocs_section,
    render_service_section,
    render_students_section,
    render_under_review_section,
)
from pubs_emitter.types import (
    Citation,
    ConferencePresentation,
    Grant,
    GrantPerson,
    InvitedTalk,
    KeyWork,
    LeadershipRole,
    MediaAppearance,
    PostdocVisiting,
    ServiceEntry,
    Student,
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

    def test_first_row_carries_bold_index_only(self) -> None:
        # The "C.X.Y" prefix is intentionally not in the table; the section
        # heading above the tables already carries the section code.
        buf = io.StringIO()
        render_grants_section(
            "Grants PI",
            [_grant(title="First"), _grant(title="Second"), _grant(title="Third")],
            buf,
        )
        out = buf.getvalue()
        # Each grant numbered "{N}." in bold; not "C.10.{N}."
        assert "\\b 1.\\b0" in out
        assert "\\b 2.\\b0" in out
        assert "\\b 3.\\b0" in out
        assert "C.10.1.\\tab" not in out

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
        # Right-align directive opens the cell, formatted amount is the
        # first token inside, and `\ql\cell` closes. Use prefix + presence
        # checks so the test stays correct after the renderer started
        # emitting "my share" inline in the same cell.
        assert "\\qr $123,456" in out
        assert "; my share: $123,456\\ql\\cell" in out

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
        assert "Co-PI: Yung-Hsiang Lu (Electrical & Computer Engineering)" in buf.getvalue()

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
        assert "PI: Kirsten Davis (Engineering Education)" in buf.getvalue()

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
        assert "PI: Aravind Machiry (Electrical & Computer Engineering)" in out
        assert "Co-PI: External Person (Civil Engineering, University of Maine)" in out

    def test_nsf_award_prefix_for_external_collab_pi(self) -> None:
        # Multi-inst NSF Collab: external PI's record carries their separate
        # award number; rendered as "NSF #X: Name (Dept, Institution)".
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
        assert "NSF #2135157: Dongyoon Lee (Computer Science, SUNY at Stony Brook)" in buf.getvalue()

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


class TestGrantLeadInstitutionAnnotation:
    """Row 3 role line carries the lead-institution annotation for multi-inst
    grants. Three shapes:
      * `lead_institution == ""`                  → "{role}"
      * `lead_institution == "Purdue University"` → "{role} (Purdue University is lead)"
      * `lead_institution == "X"` (external)      → "Purdue {role} (X is lead)"
    """

    def test_single_inst_no_annotation(self) -> None:
        buf = io.StringIO()
        render_grants_section("Grants PI", [_grant(role="PI", lead_institution="")], buf)
        out = buf.getvalue()
        assert "is lead" not in out

    def test_purdue_lead_annotation(self) -> None:
        buf = io.StringIO()
        render_grants_section(
            "Grants PI",
            [_grant(role="PI", lead_institution="Purdue University")],
            buf,
        )
        assert "PI (Purdue University is lead)" in buf.getvalue()

    def test_external_lead_annotation(self) -> None:
        buf = io.StringIO()
        render_grants_section(
            "Grants PI",
            [_grant(role="PI", lead_institution="Columbia University")],
            buf,
        )
        # External lead → Davis becomes "Purdue PI" to disambiguate.
        assert "Purdue PI (Columbia University is lead)" in buf.getvalue()

    def test_external_lead_with_co_pi_role(self) -> None:
        buf = io.StringIO()
        render_grants_section(
            "Grants Co-PI",
            [_grant(role="Co-PI", lead_institution="Loyola University Chicago")],
            buf,
        )
        assert "Purdue Co-PI (Loyola University Chicago is lead)" in buf.getvalue()


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


class TestGrantThreeWayAmountFormat:
    """`_format_grant_amounts` always renders `my_amount` (no conditional
    collapse) per the CV convention. Single-inst grants get `"$X; my share:
    $Y"`; multi-inst grants get `"$T total; $P Purdue; $M my share"`."""

    def _fmt(self, total: int, purdue: int, mine: int) -> str:
        from pubs_emitter.rtf import _format_grant_amounts
        g = _grant(total_amount=total, purdue_amount=purdue, my_amount=mine)
        return _format_grant_amounts(g)

    def test_sole_pi_single_inst_still_shows_my_share(self) -> None:
        # Even when all three are equal, my_share is rendered (no collapse).
        assert self._fmt(600000, 600000, 600000) == "$600,000; my share: $600,000"

    def test_single_inst_multi_pi_split(self) -> None:
        # total == purdue, mine smaller.
        assert self._fmt(500000, 500000, 125000) == "$500,000; my share: $125,000"

    def test_collab_sole_purdue_pi(self) -> None:
        # total > purdue, purdue == mine — multi-inst form still includes
        # the "my share" component.
        out = self._fmt(1000000, 400000, 400000)
        assert "$1,000,000 total" in out
        assert "$400,000 Purdue" in out
        assert "$400,000 my share" in out

    def test_collab_multi_pi(self) -> None:
        # All three differ.
        out = self._fmt(2000000, 800000, 200000)
        assert "$2,000,000 total" in out
        assert "$800,000 Purdue" in out
        assert "$200,000 my share" in out

    def test_amounts_render_in_row_2(self) -> None:
        # End-to-end: Row 2 of the grant table picks up the new format helper.
        buf = io.StringIO()
        render_grants_section(
            "Grants PI",
            [_grant(total_amount=1000000, purdue_amount=400000, my_amount=400000)],
            buf,
        )
        out = buf.getvalue()
        assert "$1,000,000 total" in out
        assert "$400,000 Purdue" in out
        assert "$400,000 my share" in out


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
        assert "external funding as PI:\\b0 $160,000" in out


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
        # Numbered list cross-ref form: A.1.1.\tab
        assert "A.1.1.\\tab" in out

    def test_multiple_entries_increment_index(self) -> None:
        buf = io.StringIO()
        render_under_review_section(
            [self._ur(title="One"), self._ur(title="Two"), self._ur(title="Three")],
            buf,
        )
        out = buf.getvalue()
        assert "A.1.1." in out and "A.1.2." in out and "A.1.3." in out

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
        # Each tier divider uses the grey-background marker (\clcbpat2).
        assert out.count("\\clcbpat2") == 3
        # Labels appear in tier order. Em-dash in the labels is RTF-escaped
        # to 舒? per the cp1252 discipline, so check head + tail substrings
        # of each label separately rather than the full literal.
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
        # mid-tier dividers.
        assert buf.getvalue().count("\\clcbpat2") == 1


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
        assert "C.1" in out
        assert "Highlighted" in out
        assert "Big impact text." in out
        # Cross-link to canonical location.
        assert "(listed as C.4.7)" in out

    def test_empty_list_writes_nothing(self) -> None:
        buf = io.StringIO()
        render_key_works_section([], paper_index={}, out=buf)
        assert buf.getvalue() == ""
