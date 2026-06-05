"""End-to-end pipeline test.

Drives `cli.main` against the tests/fixtures/ bib + YAML, monkey-patching
the three `try_*` network entry points (imported into `lookup`) so no real
HTTP traffic ever leaves the test process. Verifies that every output
section the YAML / bib drives lands in the generated RTF.
"""
from __future__ import annotations

import pathlib

import pytest
import yaml

from pubs_emitter import cli, lookup
from pubs_emitter.cli import filter_hidden


@pytest.fixture
def fake_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub every network entry point that lookup.py uses."""
    # Crossref/DBLP DOI lookup — return a canned doi.org URL.
    monkeypatch.setattr(
        lookup, "try_doi",
        lambda title, authors, acronym=None: "https://doi.org/10.test/{}".format(
            title.lower().replace(" ", "-"),
        ),
    )
    # USPTO patent date lookup — None means the fallback bib date is used.
    monkeypatch.setattr(
        lookup, "try_patentsview", lambda _number: None,
    )
    # NVD CVE lookup — return enough structure for the description /
    # year / organization extractors.
    def _fake_nvd(_cve_id: str) -> dict:
        return {
            "descriptions": [
                {"lang": "en", "value": "A test vulnerability description."}
            ],
            "published": "2024-01-15T00:00:00",
            "configurations": [
                {
                    "nodes": [
                        {
                            "cpeMatch": [
                                {"criteria": "cpe:2.3:a:vendor:testproduct:*:*:*"}
                            ]
                        }
                    ]
                }
            ],
        }
    monkeypatch.setattr(lookup, "try_nvd", _fake_nvd)


@pytest.fixture
def e2e_outputs(
    fixtures_dir: pathlib.Path,
    tmp_path: pathlib.Path,
    fake_network: None,  # noqa: ARG001 — pulled in for side effect
) -> tuple[str, pathlib.Path]:
    """Drive cli.main once, return (rtf_text, out_path)."""
    bib = fixtures_dir / "sample.bib"
    yaml = fixtures_dir / "non-scholar.yaml"
    out = tmp_path / "publications.rtf"
    cache = tmp_path / "lookup_cache.sqlite"
    cli.main(
        [
            "--bib", str(bib),
            "--non-scholar", str(yaml),
            "--out", str(out),
            "--cache", str(cache),
            # Skip Section III front matter — the prod YAML's @-refs target
            # real bib keys that aren't in the small test bib. E2e tests that
            # exercise A.X rendering live in test_rtf.py with hand-built
            # CandidateInformation fixtures.
            "--candidate-info", "",
            "--self-eval", "",
        ]
    )
    return out.read_text(encoding="utf-8"), out


# ----- Each section the fixture drives must appear in the RTF ------------


class TestE2eSectionHeadings:
    def test_key_works_emitted(self, e2e_outputs: tuple[str, pathlib.Path]) -> None:
        rtf, _ = e2e_outputs
        assert "C.1 Key Scholarly Publications or Patents" in rtf

    def test_journals_emitted(self, e2e_outputs: tuple[str, pathlib.Path]) -> None:
        rtf, _ = e2e_outputs
        assert "C.2 Journals" in rtf

    def test_book_chapters_emitted(
        self, e2e_outputs: tuple[str, pathlib.Path]
    ) -> None:
        rtf, _ = e2e_outputs
        assert "C.3 Books and chapters in books" in rtf

    def test_conferences_emitted(self, e2e_outputs: tuple[str, pathlib.Path]) -> None:
        rtf, _ = e2e_outputs
        assert "C.4 Conferences and Workshops" in rtf

    def test_other_pubs_emitted(self, e2e_outputs: tuple[str, pathlib.Path]) -> None:
        rtf, _ = e2e_outputs
        # arXiv + CVE + security disclosure all land here.
        assert "C.5 Other publications and products" in rtf

    def test_invited_talks_emitted(
        self, e2e_outputs: tuple[str, pathlib.Path]
    ) -> None:
        rtf, _ = e2e_outputs
        assert "C.6" in rtf
        assert "Seminar on Test Topic" in rtf

    def test_leadership_emitted(self, e2e_outputs: tuple[str, pathlib.Path]) -> None:
        rtf, _ = e2e_outputs
        assert "C.7" in rtf
        assert "Co-Chair" in rtf

    def test_media_emitted(self, e2e_outputs: tuple[str, pathlib.Path]) -> None:
        rtf, _ = e2e_outputs
        assert "C.8" in rtf
        assert "Test Episode" in rtf

    def test_conference_presentations_emitted(
        self, e2e_outputs: tuple[str, pathlib.Path]
    ) -> None:
        rtf, _ = e2e_outputs
        assert "C.9" in rtf
        assert "Talk at" in rtf

    def test_grants_pi_emitted(self, e2e_outputs: tuple[str, pathlib.Path]) -> None:
        rtf, _ = e2e_outputs
        assert "C.10" in rtf
        # Head row is now "{N}. [{grant_number}] {agency} / {title}", with
        # agency rendering the full funder name (not agency_short).
        assert "National Science Foundation" in rtf
        assert "2025001" in rtf  # fixture grant_number
        assert "$600,000" in rtf

    def test_grad_students_emitted(
        self, e2e_outputs: tuple[str, pathlib.Path]
    ) -> None:
        rtf, _ = e2e_outputs
        assert "C.14" in rtf
        assert "Paschal C. Amusuo" in rtf

    def test_patents_emitted(self, e2e_outputs: tuple[str, pathlib.Path]) -> None:
        rtf, _ = e2e_outputs
        assert "C.19" in rtf
        assert "A Patented Invention" in rtf
        assert "11,176,090" in rtf

    def test_university_service_emitted(
        self, e2e_outputs: tuple[str, pathlib.Path]
    ) -> None:
        rtf, _ = e2e_outputs
        assert "C.23 Service to Purdue" in rtf
        assert "Member, Test Faculty Committee. 2024." in rtf
        assert "Organizer, Test Reading Group. 2023-2024." in rtf

    def test_profession_service_emitted(
        self, e2e_outputs: tuple[str, pathlib.Path]
    ) -> None:
        rtf, _ = e2e_outputs
        assert "C.24" in rtf
        assert "PC Member, ICSE. 2025, 2026, 2027." in rtf

    def test_national_service_emitted(
        self, e2e_outputs: tuple[str, pathlib.Path]
    ) -> None:
        rtf, _ = e2e_outputs
        assert "C.25" in rtf
        assert "US National Science Foundation, Panelist. 2025." in rtf

    def test_other_service_emitted_with_journal_no_year(
        self, e2e_outputs: tuple[str, pathlib.Path]
    ) -> None:
        rtf, _ = e2e_outputs
        assert "C.26" in rtf
        # Journal-review entry has no year string → renders with single period only.
        assert "Reviewer, IEEE Transactions on Software Engineering (TSE)." in rtf
        # Multi-year entry → renders with year list verbatim.
        assert "PC Member, EuroSec. 2024, 2025, 2026." in rtf


class TestE2eContentInvariants:
    def test_grants_omit_inspired_by_emission(
        self, e2e_outputs: tuple[str, pathlib.Path]
    ) -> None:
        # Cross-link rendering is reserved for C.1 Key Works; the grant
        # section must not render "Inspired by" / "Publication outcomes" lines.
        rtf, _ = e2e_outputs
        assert "Inspired by" not in rtf
        assert "Publication outcomes" not in rtf

    def test_me_is_bolded(self, e2e_outputs: tuple[str, pathlib.Path]) -> None:
        rtf, _ = e2e_outputs
        assert r"\b Davis, J.C.\b0" in rtf

    def test_student_role_marker(
        self, e2e_outputs: tuple[str, pathlib.Path]
    ) -> None:
        rtf, _ = e2e_outputs
        # The "G" superscript marker should appear at least once.
        assert "\\super G" in rtf or "\\super G," in rtf

    def test_arxiv_doi_constructed_not_fetched(
        self, e2e_outputs: tuple[str, pathlib.Path]
    ) -> None:
        # arXiv 2605.10712 → constructed DOI 10.48550/arXiv.2605.10712
        rtf, _ = e2e_outputs
        assert "10.48550/arXiv.2605.10712" in rtf

    def test_cve_link_emitted(self, e2e_outputs: tuple[str, pathlib.Path]) -> None:
        rtf, _ = e2e_outputs
        assert "CVE-2024-12345" in rtf
        assert "nvd.nist.gov/vuln/detail/CVE-2024-12345" in rtf

    def test_unicode_round_trip_through_rtf_escape(
        self, e2e_outputs: tuple[str, pathlib.Path]
    ) -> None:
        # The arXiv entry has a Çakar coauthor — Ç must encode as \u199?,
        # not leak as a raw UTF-8 byte.
        rtf, _ = e2e_outputs
        assert r"\u199?" in rtf
        assert "Ç" not in rtf

    def test_rtf_starts_and_ends_well_formed(
        self, e2e_outputs: tuple[str, pathlib.Path]
    ) -> None:
        rtf, _ = e2e_outputs
        assert rtf.startswith("{\\rtf1")
        assert rtf.endswith("}")


class TestE2eGrantMath:
    """Re-derive per-section grant totals from the source YAML and assert the
    rendered RTF contains the matching `$N,NNN` figure. Catches drift
    anywhere along the path (build_grant amount coercion, render-time sum,
    `_format_usd` thousands-separator).
    """

    @pytest.fixture
    def yaml_data(self, fixtures_dir: pathlib.Path) -> dict:
        with open(fixtures_dir / "non-scholar.yaml", encoding="utf-8") as f:
            return yaml.safe_load(f)

    def _section_total_usd(self, yaml_data: dict, key: str) -> str:
        """Sum the `my_amount` fields under `key` (with `purdue_amount` as
        default when `my_amount` isn't set) and format as $N,NNN. Mirrors the
        renderer's section-total computation.

        Pending entries (`status: pending`) are EXCLUDED — they're routed
        to Section V, A.2 and don't contribute to the C.10 / C.11 total
        labels. Mirrors the same partition cli.py applies before
        registering grants.
        """
        total = 0
        for g in (yaml_data.get(key) or []):
            if str(g.get("status", "awarded") or "awarded") == "pending":
                continue
            purdue = int(g.get("purdue_amount", 0) or 0)
            mine = int(g.get("my_amount", purdue) or purdue)
            total += mine
        return f"${total:,}"

    def test_pi_total_matches_yaml_sum(
        self, e2e_outputs: tuple[str, pathlib.Path], yaml_data: dict
    ) -> None:
        rtf, _ = e2e_outputs
        expected = self._section_total_usd(yaml_data, "grants_as_pi")
        # The fixture has one PI grant — assert both the label and the figure.
        assert "Total amount of external funding as PI" in rtf
        assert expected in rtf

    def test_co_pi_section_skipped_when_empty(
        self, e2e_outputs: tuple[str, pathlib.Path], yaml_data: dict
    ) -> None:
        # The fixture's grants_as_co_pi: [] → the section isn't emitted at all
        # (write_rtf skips empty lists), so the Co-PI total label must NOT appear.
        rtf, _ = e2e_outputs
        if not yaml_data.get("grants_as_co_pi"):
            assert "Total amount of external funding as Co-PI" not in rtf

    def test_total_label_count_matches_nonempty_grant_sections(
        self, e2e_outputs: tuple[str, pathlib.Path], yaml_data: dict
    ) -> None:
        rtf, _ = e2e_outputs
        nonempty = sum(
            1 for key in ("grants_as_pi", "grants_as_co_pi", "gifts", "internal_grants")
            if yaml_data.get(key)
        )
        # Each nonempty grant section emits exactly one "Total amount" line.
        assert rtf.count("Total amount") == nonempty


class TestE2eGrantTotalsExtended:
    """Variant of the e2e test that swaps in a multi-grant fixture so the
    arithmetic itself is exercised end-to-end, not just the single-entry
    label-passthrough that the base fixture covers."""

    def test_summed_total_matches_arithmetic(
        self,
        fixtures_dir: pathlib.Path,
        tmp_path: pathlib.Path,
        fake_network: None,  # pulled in for the monkey-patch side effect
    ) -> None:
        # Write a custom YAML that adds 3 PI grants summing to a known total.
        # Reuses the base fixture's other sections to satisfy validation.
        with open(fixtures_dir / "non-scholar.yaml", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        data["grants_as_pi"] = [
            {
                "title": "Grant A", "agency": "X", "agency_short": "X",
                "role": "PI", "start_year": 2022, "end_year": 2023,
                "purdue_amount": 123456,
            },
            {
                "title": "Grant B", "agency": "Y", "agency_short": "Y",
                "role": "PI", "start_year": 2023, "end_year": 2024,
                "purdue_amount": 654321,
            },
            {
                "title": "Grant C", "agency": "Z", "agency_short": "Z",
                "role": "PI", "start_year": 2024, "end_year": 2025,
                "purdue_amount": 1000000,
            },
        ]
        custom_yaml = tmp_path / "custom.yaml"
        with open(custom_yaml, "w", encoding="utf-8") as f:
            yaml.safe_dump(data, f)

        out = tmp_path / "publications.rtf"
        cache = tmp_path / "cache.sqlite"
        cli.main(
            [
                "--bib", str(fixtures_dir / "sample.bib"),
                "--non-scholar", str(custom_yaml),
                "--out", str(out),
                "--cache", str(cache),
                "--candidate-info", "",
                "--self-eval", "",
            ]
        )
        rtf = out.read_text(encoding="utf-8")
        # 123456 + 654321 + 1000000 = 1,777,777
        assert "$1,777,777" in rtf


class TestE2eUnresolvedAtIdRef:
    """Build MUST fail loudly when any `@id` ref in a YAML prose field
    doesn't resolve to a registered id (YAML `id:` or bib citation key).

    Catches the typo class — without this gate a misspelled `@grant1` (vs
    the real `@grant-1`) would silently leave the literal text `@grant1`
    in the rendered RTF.
    """

    def test_unresolved_at_id_in_course_development_exits_nonzero(
        self,
        fixtures_dir: pathlib.Path,
        tmp_path: pathlib.Path,
        fake_network: None,  # noqa: ARG002 — pulled in for the side effect
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        # Start from the base fixture (so all required YAML sections are
        # present + valid), then inject ONE course_development entry whose
        # description references a non-existent `@id`.
        with open(fixtures_dir / "non-scholar.yaml", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        data["course_development"] = [
            {
                "summary": "Test course",
                "description": "See @this-id-does-not-exist for context.",
            },
        ]
        custom_yaml = tmp_path / "bad-ref.yaml"
        with open(custom_yaml, "w", encoding="utf-8") as f:
            yaml.safe_dump(data, f)

        out = tmp_path / "publications.rtf"
        cache = tmp_path / "cache.sqlite"
        with pytest.raises(SystemExit) as exc:
            cli.main(
                [
                    "--bib", str(fixtures_dir / "sample.bib"),
                    "--non-scholar", str(custom_yaml),
                    "--out", str(out),
                    "--cache", str(cache),
                    "--candidate-info", "",
                    "--self-eval", "",
                ]
            )
        # The cli exits 1 (failure) when refs don't resolve.
        assert exc.value.code == 1
        # The error log identifies the offending id by name so the user
        # can find their typo. The full ref list is also logged (for
        # discovery) but that's not asserted here.
        assert "this-id-does-not-exist" in caplog.text

    def test_unresolved_at_id_in_grant_responsibility_exits_nonzero(
        self,
        fixtures_dir: pathlib.Path,
        tmp_path: pathlib.Path,
        fake_network: None,  # noqa: ARG002
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        # Grants are the most common host for `@id` refs (e.g. "@ece-30861"
        # in the responsibility line). Verify the same gate fires from a
        # grant prose field, not just course_development.
        with open(fixtures_dir / "non-scholar.yaml", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        data["grants_as_pi"] = [
            {
                "title": "Test Grant", "agency": "X", "agency_short": "X",
                "role": "PI", "start_year": 2024, "end_year": 2025,
                "purdue_amount": 100000,
                "responsibility": "Integrate into @typo-here.",
            },
        ]
        custom_yaml = tmp_path / "bad-grant-ref.yaml"
        with open(custom_yaml, "w", encoding="utf-8") as f:
            yaml.safe_dump(data, f)

        out = tmp_path / "publications.rtf"
        cache = tmp_path / "cache.sqlite"
        with pytest.raises(SystemExit) as exc:
            cli.main(
                [
                    "--bib", str(fixtures_dir / "sample.bib"),
                    "--non-scholar", str(custom_yaml),
                    "--out", str(out),
                    "--cache", str(cache),
                    "--candidate-info", "",
                    "--self-eval", "",
                ]
            )
        assert exc.value.code == 1
        assert "typo-here" in caplog.text

    def test_at_id_self_ref_resolves(
        self,
        fixtures_dir: pathlib.Path,
        tmp_path: pathlib.Path,
        fake_network: None,  # noqa: ARG002
    ) -> None:
        """Positive control: a course_development entry can reference
        ANOTHER course_development entry by its `id:` field. The cli
        succeeds and the ref renders as the linked entry's resolved
        C.X.Y code (substituted at build time)."""
        with open(fixtures_dir / "non-scholar.yaml", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        data["course_development"] = [
            {"id": "course-a", "summary": "Course A", "description": "First."},
            {
                "summary": "Course B",
                "description": "Builds on @course-a foundations.",
            },
        ]
        custom_yaml = tmp_path / "good-ref.yaml"
        with open(custom_yaml, "w", encoding="utf-8") as f:
            yaml.safe_dump(data, f)

        out = tmp_path / "publications.rtf"
        cache = tmp_path / "cache.sqlite"
        cli.main(
            [
                "--bib", str(fixtures_dir / "sample.bib"),
                "--non-scholar", str(custom_yaml),
                "--out", str(out),
                "--cache", str(cache),
                "--candidate-info", "",
                "--self-eval", "",
            ]
        )
        rtf = out.read_text(encoding="utf-8")
        # The literal `@course-a` is NOT in the rendered RTF — it was
        # substituted at build time. Instead, the linked entry's code
        # (`C.18.1`) appears (as a clickable hyperlink in this case).
        assert "@course-a" not in rtf
        # The hyperlink target's bookmark name uses underscores in
        # place of dots: `C_18_1`.
        assert "HYPERLINK \\\\l \"C_18_1\"" in rtf


class TestE2eSectionsFilter:
    """The `--sections` CLI flag (and the matching `sections_filter`
    kwarg on `write_rtf`) emits only the requested top-level / child
    section codes. The build still COMPUTES every section so cross-refs,
    `@id` resolution, and paper-index numbering match the full document
    — only emission is filtered. Useful for spot-checking one section in
    Word without re-rendering the whole packet.
    """

    def test_no_filter_emits_every_section(
        self,
        e2e_outputs: tuple[str, pathlib.Path],
    ) -> None:
        # Sanity guard: the default (no --sections) emits the canonical
        # set of section headings. If this number changes you've either
        # added a new section or accidentally suppressed one.
        rtf, _ = e2e_outputs
        # Count fs28 (Heading 1) section title lines.
        heading_count = rtf.count("\\fs28 ")
        # The fixture YAML is intentionally minimal; the full
        # production YAML emits ~29. The fixture exercises ~25.
        # Use a floor + ceiling so the test catches regressions in
        # either direction without pinning the exact count.
        assert 15 <= heading_count <= 35

    def test_filter_c4_only_emits_one_section(
        self,
        fixtures_dir: pathlib.Path,
        tmp_path: pathlib.Path,
        fake_network: None,  # noqa: ARG002
    ) -> None:
        out = tmp_path / "c4-only.rtf"
        cache = tmp_path / "cache.sqlite"
        cli.main(
            [
                "--bib", str(fixtures_dir / "sample.bib"),
                "--non-scholar", str(fixtures_dir / "non-scholar.yaml"),
                "--out", str(out),
                "--cache", str(cache),
                "--sections", "C.4",
                "--candidate-info", "",
                "--self-eval", "",
            ]
        )
        rtf = out.read_text(encoding="utf-8")
        assert "C.4 Conferences and Workshops" in rtf
        # No other section headings emit. Spot-check a few that would
        # normally appear.
        assert "C.1 Key Scholarly Publications" not in rtf
        assert "C.2 Journals" not in rtf
        assert "C.6 Invited Talks" not in rtf

    def test_filter_parent_code_includes_children(
        self,
        fixtures_dir: pathlib.Path,
        tmp_path: pathlib.Path,
        fake_network: None,  # noqa: ARG002
    ) -> None:
        # `--sections C.16` should ALSO emit C.16.2.3 / C.16.2.4 /
        # C.16.3.3 (sub-section codes are children of C.16). The
        # parent-child rule: `code.startswith(filter + ".")`.
        with open(fixtures_dir / "non-scholar.yaml", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        # Inject one student-award entry so a C.16.2.4 section emits.
        data["student_awards"] = [
            {
                "level": "U",
                "tier": "Institutional Awards",
                "year": 2024, "year_str": "2024",
                "recipient": "Test Student",
                "award": "Test Award",
            },
        ]
        custom_yaml = tmp_path / "sub.yaml"
        with open(custom_yaml, "w", encoding="utf-8") as f:
            yaml.safe_dump(data, f)

        out = tmp_path / "c16.rtf"
        cache = tmp_path / "cache.sqlite"
        cli.main(
            [
                "--bib", str(fixtures_dir / "sample.bib"),
                "--non-scholar", str(custom_yaml),
                "--out", str(out),
                "--cache", str(cache),
                "--sections", "C.16",
                "--candidate-info", "",
                "--self-eval", "",
            ]
        )
        rtf = out.read_text(encoding="utf-8")
        # Sub-section heading carries a `\\*\\bkmkstart C_16_2_4` wrap;
        # check the bookmark + renamed heading text presence.
        assert "bkmkstart C_16_2_4" in rtf
        assert "Undergraduate Awards, Fellowships, and Career Development" in rtf
        # Not C.4 (would emit if filter were ignored).
        assert "C.4 Conferences" not in rtf

    def test_filter_multiple_codes(
        self,
        fixtures_dir: pathlib.Path,
        tmp_path: pathlib.Path,
        fake_network: None,  # noqa: ARG002
    ) -> None:
        # Comma-separated codes: union of all matches.
        out = tmp_path / "multi.rtf"
        cache = tmp_path / "cache.sqlite"
        cli.main(
            [
                "--bib", str(fixtures_dir / "sample.bib"),
                "--non-scholar", str(fixtures_dir / "non-scholar.yaml"),
                "--out", str(out),
                "--cache", str(cache),
                "--sections", "C.4,C.6",
                "--candidate-info", "",
                "--self-eval", "",
            ]
        )
        rtf = out.read_text(encoding="utf-8")
        assert "C.4 Conferences and Workshops" in rtf
        assert "C.6 Invited" in rtf
        # Other sections still suppressed.
        assert "C.2 Journals" not in rtf

    def test_filter_preserves_full_document_numbering(
        self,
        fixtures_dir: pathlib.Path,
        tmp_path: pathlib.Path,
        fake_network: None,  # noqa: ARG002
    ) -> None:
        # The C.4 numbering when only C.4 is emitted MUST match the
        # numbering from the full-document build. The cli computes
        # every section's contents to derive paper_index + ref_index
        # before filtering emission, so the C.X.Y back-pointers stay
        # stable.
        # Build the full document first.
        full_out = tmp_path / "full.rtf"
        cli.main(
            [
                "--bib", str(fixtures_dir / "sample.bib"),
                "--non-scholar", str(fixtures_dir / "non-scholar.yaml"),
                "--out", str(full_out),
                "--cache", str(tmp_path / "full-cache.sqlite"),
                "--candidate-info", "",
                "--self-eval", "",
            ]
        )
        full_rtf = full_out.read_text(encoding="utf-8")
        import re
        full_c4_codes = set(re.findall(r"C\.4\.\d+", full_rtf))

        # Now build with --sections C.4 only.
        c4_out = tmp_path / "c4.rtf"
        cli.main(
            [
                "--bib", str(fixtures_dir / "sample.bib"),
                "--non-scholar", str(fixtures_dir / "non-scholar.yaml"),
                "--out", str(c4_out),
                "--cache", str(tmp_path / "c4-cache.sqlite"),
                "--sections", "C.4",
                "--candidate-info", "",
                "--self-eval", "",
            ]
        )
        c4_rtf = c4_out.read_text(encoding="utf-8")
        c4_only_codes = set(re.findall(r"C\.4\.\d+", c4_rtf))
        assert full_c4_codes == c4_only_codes, (
            "C.4.N numbering shifted when --sections was applied — "
            "this would break back-pointers from other sections."
        )


def _strip_rtf_markup(rtf: str) -> str:
    """Best-effort RTF → plain text for the punctuation-spacing linter.

    Drops control words (`\\foo` + optional numeric arg + optional
    delimiter space), control symbols (`\\\\`, `\\{`, `\\}`, `\\~`,
    `\\-`, `\\_`), brace groups for destinations (`{\\*\\...}`), and
    the bookmark/field destination wrappers. The result isn't suitable
    for typography but IS suitable for "punctuation followed by text
    should have a space" sanity checking.
    """
    import re
    # Strip RTF destinations (groups starting with `{\*`).
    while True:
        new = re.sub(r"\{\\\*[^{}]*?\}", "", rtf)
        if new == rtf:
            break
        rtf = new
    # Strip HYPERLINK field instruction sub-groups so the link URL
    # text doesn't pollute the plain text.
    rtf = re.sub(r"\{\\field\{\\?\*?\\?fldinst[^{}]*\}", "", rtf)
    # Strip Unicode escapes `\u{N}?` → leave the fallback char.
    rtf = re.sub(r"\\u-?\d+\?", "", rtf)
    # Strip control words with optional numeric arg + optional one
    # trailing space (the RTF delimiter).
    rtf = re.sub(r"\\[a-zA-Z]+-?\d* ?", "", rtf)
    # Strip control symbols.
    rtf = re.sub(r"\\[^a-zA-Z]", "", rtf)
    # Drop any remaining braces.
    rtf = rtf.replace("{", "").replace("}", "")
    # Collapse runs of whitespace.
    rtf = re.sub(r"\s+", " ", rtf)
    return rtf


class TestE2ePunctuationSpacing:
    """Lint: every punctuation followed by alphanumeric text should have
    at least one whitespace character between them. Catches the class
    of bug where a colon-space (`DOI: `) gets emitted as `DOI:` and the
    URL/title runs into the previous word.

    The check runs against a best-effort RTF→plain-text strip
    (`_strip_rtf_markup`), not the raw RTF source — so RTF control
    words (`\\b0$X`) are NOT flagged here; that's a separate class of
    bug already caught by per-renderer tests via brace-scoping.
    """

    # Punctuation followed IMMEDIATELY by text. Three classes:
    #   `:` or `;` then alphanumeric  — DOI:X / URL:X / colon-space class
    #   LETTER then `(`               — "safe-regex(2018-...)" class (the
    #                                    `\b0`-eats-trailing-space bug).
    #                                    Digit-then-`(` is intentionally
    #                                    excluded: "Journal, 199(3)" is the
    #                                    canonical volume(issue) notation.
    #   `)` then `(`                  — "(name.com)(year)" class (renderer
    #                                    concatenation without separator)
    _BAD_SPACING_RE = (
        r"([;:][A-Za-z0-9]|[A-Za-z]\(|\)\()"
    )

    # Known good substrings whose missing-space is canonical and
    # intentional. Any finding whose 50-char context window contains
    # one of these is NOT a violation.
    _ALLOWED_SUBSTRINGS = (
        "https://",       # URL scheme
        "http://",        # URL scheme
        "arXiv:",         # canonical arXiv identifier form (arXiv:NNNN.NNNNN)
        "CVE-",           # adjacent CVE refs sometimes packed like "ref;CVE-..."
    )

    def _findings(self, plain_text: str) -> list[str]:
        import re
        pat = re.compile(self._BAD_SPACING_RE)
        findings: list[str] = []
        for m in pat.finditer(plain_text):
            start = max(0, m.start() - 25)
            end = min(len(plain_text), m.end() + 25)
            ctx = plain_text[start:end]
            if any(allow in ctx for allow in self._ALLOWED_SUBSTRINGS):
                continue
            findings.append(ctx)
        return findings

    def test_no_colon_or_semicolon_immediately_followed_by_text(
        self,
        e2e_outputs: tuple[str, pathlib.Path],
    ) -> None:
        rtf, _ = e2e_outputs
        plain = _strip_rtf_markup(rtf)
        # Drop the RTF preamble (font table, color table, stylesheet) —
        # its `;` separators are legitimate RTF syntax that survive the
        # best-effort strip. The user-visible content starts at the
        # first section heading.
        for anchor in ("C.1 ", "A.1 ", "C.2 "):
            i = plain.find(anchor)
            if i >= 0:
                plain = plain[i:]
                break
        findings = self._findings(plain)
        # On a clean build the list is empty. When the test fires, the
        # findings list IS the diagnostic — each entry is a 50-char
        # context window around the offending substring.
        assert not findings, (
            f"{len(findings)} punctuation-spacing violation(s) in the "
            f"rendered RTF (showing first 5):\n  - "
            + "\n  - ".join(findings[:5])
        )


class TestE2eNumericalOrdering:
    """Emitted entry codes must appear in monotone-increasing order per
    parent section. Within any C.X (or C.X.Y) section, the entries
    bookmarked as C.X.1, C.X.2, ... must appear in that order in the
    rendered RTF — and the numbering must be dense (1, 2, 3, ...; no
    gaps, no out-of-order). Catches regressions in:

      * The `_emit_list_item` helper (any callsite emitting out of
        the canonical-sort order)
      * `enumerate(..., 1)` getting replaced by something that skips
        or restarts the index
      * Subcategory grouping (e.g., C.5) accidentally restarting the
        section-wide counter when crossing a subheading
      * Tier grouping (C.16.2.4 / C.16.3.3) restarting the counter
        between tiers
    """

    # Bookmark-name form of every emitted entry code. Bookmarks are
    # written by `_ref_anchor` as `\\*\\bkmkstart C_X_Y_Z}` — the
    # opening marker is the canonical extraction anchor.
    _BOOKMARK_START_RE = __import__("re").compile(
        r"\\\*\\bkmkstart ([A-Z][\d_]+)"
    )

    def _entries_by_parent(
        self, rtf: str,
    ) -> dict[tuple[int, ...], list[tuple[int, ...]]]:
        """Group every emitted bookmark code by its parent code.

        Each bookmark name like `C_16_2_4_1` becomes the tuple
        `(C, 16, 2, 4, 1)`; its parent is `(C, 16, 2, 4)`. Returns a map
        `parent → [child_tuple, ...]` preserving emission order so the
        test can assert per-parent monotone-increasing suffix sequences.
        Single-segment "parents" (`(C,)` / `(A,)`) get a `C.X` family.
        """
        groups: dict[tuple[int, ...], list[tuple[int, ...]]] = {}
        for match in self._BOOKMARK_START_RE.finditer(rtf):
            name = match.group(1)
            parts = name.split("_")
            if len(parts) < 2:
                continue
            try:
                code_tuple: tuple[int, ...] = (
                    ord(parts[0]),
                    *(int(p) for p in parts[1:]),
                )
            except ValueError:
                continue  # not a section code (some other bookmark)
            parent = code_tuple[:-1]
            groups.setdefault(parent, []).append(code_tuple)
        return groups

    def test_entry_codes_emit_in_sorted_order(
        self,
        e2e_outputs: tuple[str, pathlib.Path],
    ) -> None:
        """For every section that emits numbered entries, the entry codes
        must appear in monotone-increasing order AND be a dense 1..N
        sequence."""
        rtf, _ = e2e_outputs
        groups = self._entries_by_parent(rtf)
        problems: list[str] = []
        for parent, codes in sorted(groups.items()):
            # Only check sections that actually emit numbered entries
            # (parent has 1+ segments; we look at the last segment of
            # each child). Skip groups with just one entry — order is
            # trivially correct.
            if len(codes) < 2:
                continue
            suffixes = [c[-1] for c in codes]
            # Reconstruct the parent code for diagnostic clarity.
            parent_dotted = (
                chr(parent[0]) + "." + ".".join(str(p) for p in parent[1:])
                if len(parent) > 1 else chr(parent[0])
            )
            if suffixes != sorted(suffixes):
                problems.append(
                    f"{parent_dotted}: out-of-order suffixes {suffixes}"
                )
            expected = list(range(1, len(suffixes) + 1))
            if suffixes != expected:
                problems.append(
                    f"{parent_dotted}: non-dense numbering "
                    f"{suffixes} (expected {expected})"
                )
        assert not problems, (
            f"{len(problems)} ordering/density violation(s):\n  - "
            + "\n  - ".join(problems)
        )

    def test_full_emit_order_is_tuple_monotone(
        self,
        e2e_outputs: tuple[str, pathlib.Path],
    ) -> None:
        """The FULL ordered list of emitted bookmarks (across all sections)
        must equal the same list sorted by code-tuple — i.e., C.16.1 must
        appear before C.16.2, and C.16.2 before C.16.2.3, and C.16.2.3
        before C.16.3.

        The sibling `test_entry_codes_emit_in_sorted_order` only checks
        within-parent (children of C.16.2). That misses the cross-depth
        class: a sub-section's content emitting BEFORE its intermediate
        parent. Canonical example (260603): C.16 renderer skipped
        C.16.1 / C.16.2 entirely and jumped to C.16.2.3 — every
        per-parent group was monotone, so the within-parent test
        passed, but the rendered packet was visibly out of order.

        A.* (Under Review appendix) is emitted LAST by design; this
        test splits the C and A families and checks each independently.
        """
        rtf, _ = e2e_outputs
        emitted: list[tuple[tuple[int, ...], str]] = []
        for m in self._BOOKMARK_START_RE.finditer(rtf):
            name = m.group(1)
            parts = name.split("_")
            if len(parts) < 2:
                continue
            try:
                tup = (ord(parts[0]), *(int(p) for p in parts[1:]))
            except ValueError:
                continue
            emitted.append((tup, name))

        problems: list[str] = []
        for family_char in ("C", "A"):
            fam_code = ord(family_char)
            in_family = [(t, n) for t, n in emitted if t[0] == fam_code]
            if not in_family:
                continue
            emit_order_names = [n for _, n in in_family]
            tuple_sorted_names = [
                n for _, n in sorted(in_family, key=lambda x: x[0])
            ]
            if emit_order_names != tuple_sorted_names:
                # Find first divergence for a useful error message.
                for i, (got, expected) in enumerate(
                    zip(emit_order_names, tuple_sorted_names)
                ):
                    if got != expected:
                        got_dotted = got.replace("_", ".")
                        exp_dotted = expected.replace("_", ".")
                        problems.append(
                            f"{family_char}.* family: position {i} emitted "
                            f"{got_dotted}, but tuple-sorted order expects "
                            f"{exp_dotted}"
                        )
                        break
        assert not problems, (
            f"{len(problems)} cross-section ordering violation(s):\n  - "
            + "\n  - ".join(problems)
        )


class TestFilterHidden:
    """Config-level `publication_hide:` filter — drops bib entries whose
    citation key appears in the list before paper_index assembly, so the
    visible sequence has no gaps and cross-refs never resolve to hidden
    papers."""

    def _entry(self, key: str, title: str) -> dict:
        return {"ID": key, "title": title, "author": "X"}

    def test_empty_hide_list_returns_entries_unchanged(self) -> None:
        entries = [self._entry("k1", "A"), self._entry("k2", "B")]
        assert filter_hidden(entries, []) == entries

    def test_hidden_keys_dropped(self) -> None:
        entries = [
            self._entry("paper1", "A"),
            self._entry("paper2", "B"),
            self._entry("paper3", "C"),
        ]
        result = filter_hidden(entries, ["paper2"])
        assert [e["ID"] for e in result] == ["paper1", "paper3"]

    def test_stale_key_logged(
        self, caplog: pytest.LogCaptureFixture,
    ) -> None:
        """A `publication_hide:` key that doesn't match any bib entry
        emits a WARNING — surfaces the case where Scholar renamed a key
        under us so the user can update the config."""
        entries = [self._entry("paper1", "A")]
        import logging
        with caplog.at_level(logging.WARNING, logger="pubs_emitter.cli"):
            filter_hidden(entries, ["paper-nonexistent"])
        assert any(
            "paper-nonexistent" in rec.message for rec in caplog.records
        )


# ----- Section bookmark-placement invariant ------------------------------


@pytest.fixture
def e2e_full_outputs(
    fixtures_dir: pathlib.Path,
    tmp_path: pathlib.Path,
    fake_network: None,  # noqa: ARG001 — pulled in for side effect
) -> str:
    """Drive cli.main with full Section III/IV/V coverage — uses the
    test-fixture candidate-information.yaml + self-evaluation.md so the
    placement invariant can verify cross-section bookmark targeting
    (Section III A.X, Section V A.1, Section V A.2, all C.X)."""
    bib = fixtures_dir / "sample.bib"
    non_scholar = fixtures_dir / "non-scholar.yaml"
    candidate_info = fixtures_dir / "candidate-information.yaml"
    self_eval = fixtures_dir / "self-evaluation.md"
    out = tmp_path / "publications.rtf"
    cache = tmp_path / "lookup_cache.sqlite"
    cli.main(
        [
            "--bib", str(bib),
            "--non-scholar", str(non_scholar),
            "--candidate-info", str(candidate_info),
            "--self-eval", str(self_eval),
            "--evaluationkit-rawdata", "",  # no CSV fixture
            "--out", str(out),
            "--cache", str(cache),
        ]
    )
    return out.read_text(encoding="utf-8")


class TestE2eSectionBookmarkPlacement:
    """Every `\\*\\bkmkstart NAME` marker in the rendered RTF must fall
    INSIDE the byte-range of the section that owns its named code.
    Catches the class of bug where a bookmark gets emitted in the
    wrong section block — most concretely, the V.A.2 vs Section III A.2
    Degrees bookmark collision the "V." prefix exists to prevent.

    Section ranges are derived from heading text positions; bookmark
    placement is asserted against those ranges.
    """

    _BOOKMARK_RE = __import__("re").compile(
        r"\\\*\\bkmkstart ([A-Z][\w]+)"
    )

    def _section_ranges(self, rtf: str) -> dict[str, tuple[int, int]]:
        """Map section label → (start, end) byte range.

        Each top-level section is delimited by its heading text appearing
        in `\\fs28 …` form (level-1 heading) or `\\fs32 …` (group heading
        "A. GENERAL INFORMATION" / "B. SELF-EVALUATION"). End-of-range
        is the next section's start (or end of doc for the last).
        """
        # The headings we care about for placement validation. Order
        # matters — must be in emission order so the (start, end) range
        # falls cleanly between successive section starts.
        headings_in_order = [
            ("A1", "A.1 Name and any appropriate scholarly identifiers"),
            ("A2_III", "A.2 Degrees"),
            ("A3", "A.3 Positions at Purdue"),
            ("A4", "A.4 Positions at other institutions"),
            ("A5", "A.5 Licenses"),
            ("A6", "A.6 Recognitions"),
            ("A7", "A.7 Membership in professional organizations"),
            ("B1", "B.1 Summary of achievements"),
            ("B2", "B.2 Impact of accomplishments"),
            ("B3", "B.3 Vision"),
            ("B4", "B.4 Candidate comments"),
            ("B5", "B.5 Professional COVID-19"),
            ("C1", "C.1 Key Scholarly Publications"),
            ("C2", "C.2 Journals"),
            ("C3", "C.3 Books and chapters"),
            ("C4", "C.4 Conferences and Workshops"),
            ("C5", "C.5 Other publications"),
            ("C6", "C.6 Invited"),
            ("C7", "C.7 Leadership"),
            ("C8", "C.8 Appearances in media"),
            ("C9", "C.9 Selected contributed conference"),
            ("C10", "C.10 Externally sponsored grants as PI"),
            ("C14", "C.14 Graduate students advised"),
            ("C15", "C.15 Mentoring of postdoctoral"),
            ("C16", "C.16 Undergraduate research"),
            ("C17", "C.17 Courses taught"),
            ("C18", "C.18 Course development"),
            ("C19", "C.19 Issued U.S. and International Patents"),
            ("C20", "C.20 Major entrepreneurial"),
            ("C21", "C.21 Technology transfer"),
            ("C22", "C.22 Software products"),
            ("C23", "C.23 Service to Purdue"),
            ("C24", "C.24 Service to the profession"),
            ("C25", "C.25 Service to State"),
            ("C26", "C.26 Other external service"),
            ("V_A1", "A.1 Products under review"),
            ("V_A2", "A.2 Pending proposals"),
        ]
        positions: list[tuple[str, int]] = []
        for label, snippet in headings_in_order:
            pos = rtf.find(snippet)
            if pos < 0:
                continue  # section absent from this run (e.g., empty C.X)
            positions.append((label, pos))
        positions.sort(key=lambda x: x[1])

        ranges: dict[str, tuple[int, int]] = {}
        for i, (label, start) in enumerate(positions):
            end = positions[i + 1][1] if i + 1 < len(positions) else len(rtf)
            ranges[label] = (start, end)
        return ranges

    def _expected_section_for_bookmark(self, name: str) -> str:
        """Map a bookmark name to the label whose range must contain it.

        Bookmark naming convention (single source of truth in
        `_ref_anchor`):
          * "V_A_X_N" → Section V's A.X (V_A1 or V_A2)
          * "A_X_N"   → Section III's A.X  (A1..A7)
          * "C_X[_…]" → C.X family — strip suffix to top-level "C.X"
        """
        if name.startswith("V_A_"):
            # "V_A_1_N" → V_A1; "V_A_2_N" → V_A2
            parts = name.split("_")
            return f"V_A{parts[2]}"
        if name.startswith("A_"):
            # Section III A.X.N bookmark — bare-A form only emitted by
            # Section III sub-section entries (Section V under-review
            # also uses bare A_1_N because Section III A.1 is bullets
            # and emits no A_1_N bookmark to collide with).
            parts = name.split("_")
            idx = parts[1]
            # A_1_N is owned by Section V under-review (V_A1 range);
            # all other A_X_N belong to Section III.
            return "V_A1" if idx == "1" else f"A{idx}"
        if name.startswith("C_"):
            # Strip subscripts: C_16_2_3_1 → C16. The top-level section
            # "C.16" owns every descendant bookmark.
            parts = name.split("_")
            return f"C{parts[1]}"
        return ""  # bookmark we don't model — skip

    def test_every_bookmark_lands_in_its_section(
        self, e2e_full_outputs: str,
    ) -> None:
        rtf = e2e_full_outputs
        ranges = self._section_ranges(rtf)
        # Sanity: the comprehensive run must emit every top-level section
        # we model — if a section is missing, the fixture lost coverage.
        for must_have in ("A2_III", "V_A1", "V_A2", "C1", "C10"):
            assert must_have in ranges, (
                f"section {must_have} not found in rendered RTF — "
                f"fixture lost coverage of this section"
            )
        misplacements: list[tuple[str, int, str]] = []
        for m in self._BOOKMARK_RE.finditer(rtf):
            name = m.group(1)
            expected = self._expected_section_for_bookmark(name)
            if not expected or expected not in ranges:
                continue
            start, end = ranges[expected]
            if not (start <= m.start() < end):
                # Where DID it land? Scan the ranges to report the
                # actual containing section for a useful failure message.
                actual = "(outside any modeled section)"
                for label, (s, e) in ranges.items():
                    if s <= m.start() < e:
                        actual = label
                        break
                misplacements.append((name, m.start(), actual))
        assert not misplacements, (
            f"{len(misplacements)} bookmark(s) emitted in the wrong section. "
            f"Sample (name, byte_pos, actual_section, expected_section):\n"
            + "\n".join(
                f"  {n!r} at {p} in {a!r} (expected "
                f"{self._expected_section_for_bookmark(n)!r})"
                for n, p, a in misplacements[:10]
            )
        )

    def test_section_iii_a2_degree_bookmark_present_in_section_iii(
        self, e2e_full_outputs: str,
    ) -> None:
        """Belt-and-suspenders: the Section III A.2.1 Degrees bookmark
        (`A_2_1`) must be in the Section III block, NOT the Section V
        block. Regression pin for the V.A.2 bookmark-prefix machinery."""
        rtf = e2e_full_outputs
        ranges = self._section_ranges(rtf)
        s3_a2 = rtf.find(r"\*\bkmkstart A_2_1")
        assert s3_a2 >= 0
        a2_iii_start, a2_iii_end = ranges["A2_III"]
        assert a2_iii_start <= s3_a2 < a2_iii_end

    def test_section_v_a2_pending_bookmark_present_in_section_v(
        self, e2e_full_outputs: str,
    ) -> None:
        """Companion to the Section III A.2 pin: the Section V A.2.1
        Pending Proposals bookmark (`V_A_2_1`) lives in the Section V
        block. Together these two tests anchor the "V." prefix
        invariant."""
        rtf = e2e_full_outputs
        ranges = self._section_ranges(rtf)
        s5_a2 = rtf.find(r"\*\bkmkstart V_A_2_1")
        assert s5_a2 >= 0
        v_a2_start, v_a2_end = ranges["V_A2"]
        assert v_a2_start <= s5_a2 < v_a2_end
