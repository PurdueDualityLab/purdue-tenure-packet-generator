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
        renderer's section-total computation."""
        total = 0
        for g in (yaml_data.get(key) or []):
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
            ]
        )
        rtf = out.read_text(encoding="utf-8")
        # 123456 + 654321 + 1000000 = 1,777,777
        assert "$1,777,777" in rtf
