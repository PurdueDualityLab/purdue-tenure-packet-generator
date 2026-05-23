"""Unit tests for pubs_emitter.venue."""
from __future__ import annotations

import pytest

from pubs_emitter.venue import (
    CVE_ID_RE,
    MissingBracketTag,
    UnrankedVenue,
    classify_entry,
    extract_arxiv_id,
    extract_cve_id,
    extract_figshare_id,
    is_book_chapter_entry,
    is_patent_entry,
    is_thesis_entry,
    lookup_rank,
    normalize_title,
    parse_venue,
)


class TestParseVenue:
    def test_extracts_acronym(self) -> None:
        acronym, cleaned = parse_venue("[ICSE'25] Proceedings of ICSE")
        assert acronym == "ICSE"
        assert cleaned == "Proceedings of ICSE"

    def test_no_bracket_returns_none(self) -> None:
        acronym, cleaned = parse_venue("Proceedings of ICSE")
        assert acronym is None
        assert cleaned == "Proceedings of ICSE"

    def test_strips_year_with_curly_apostrophe(self) -> None:
        acronym, _ = parse_venue("[ICSE’25] ...")
        assert acronym == "ICSE"

    def test_strips_year_with_four_digits(self) -> None:
        acronym, _ = parse_venue("[ICSE 2025] ...")
        assert acronym == "ICSE"


class TestLookupRank:
    def test_known_acronym(self) -> None:
        assert lookup_rank("ICSE", "Some Title") == "Rank 1"

    def test_case_insensitive(self) -> None:
        assert lookup_rank("icse", "Some Title") == "Rank 1"

    def test_unknown_raises(self) -> None:
        with pytest.raises(UnrankedVenue):
            lookup_rank("BOGUS-VENUE", "Some Title")

    def test_none_acronym_raises_bracket_tag(self) -> None:
        with pytest.raises(MissingBracketTag):
            lookup_rank(None, "Some Title")


class TestClassifyEntry:
    def test_journal(self) -> None:
        cat, venue = classify_entry({"journal": "Some Journal"})
        assert cat == "Journals"
        assert venue == "Some Journal"

    def test_conference(self) -> None:
        cat, venue = classify_entry({"booktitle": "Some Conference"})
        assert cat == "Conferences and Workshops"
        assert venue == "Some Conference"

    def test_arxiv_journal(self) -> None:
        cat, _ = classify_entry({"journal": "arXiv preprint"})
        assert cat == "arXiv / Preprints"

    def test_figshare(self) -> None:
        cat, _ = classify_entry({"journal": "figshare deposit"})
        assert cat == "arXiv / Preprints"

    def test_eprint_only(self) -> None:
        cat, _ = classify_entry({"eprint": "2605.10712"})
        assert cat == "arXiv / Preprints"


class TestEntryKindPredicates:
    def test_patent_via_publisher(self) -> None:
        assert is_patent_entry({"ENTRYTYPE": "misc", "publisher": "US patent"})

    def test_patent_via_note(self) -> None:
        assert is_patent_entry(
            {"ENTRYTYPE": "misc", "note": "US Patent 11,176,090"}
        )

    def test_not_patent_when_no_marker(self) -> None:
        assert not is_patent_entry({"ENTRYTYPE": "misc", "publisher": "ACM"})

    def test_thesis(self) -> None:
        assert is_thesis_entry({"ENTRYTYPE": "phdthesis"})
        assert is_thesis_entry({"ENTRYTYPE": "mastersthesis"})
        assert not is_thesis_entry({"ENTRYTYPE": "article"})

    def test_book_chapter(self) -> None:
        assert is_book_chapter_entry({"ENTRYTYPE": "incollection"})
        assert is_book_chapter_entry({"ENTRYTYPE": "inbook"})
        assert not is_book_chapter_entry({"ENTRYTYPE": "article"})


class TestArxivIdExtraction:
    def test_modern_id_from_eprint(self) -> None:
        assert extract_arxiv_id({"eprint": "2605.10712"}) == "2605.10712"

    def test_versioned_id_is_stripped(self) -> None:
        # v1/v2 suffix consumed but not captured (DOI is per-paper, not per-version).
        assert extract_arxiv_id({"eprint": "2605.10712v3"}) == "2605.10712"

    def test_legacy_id(self) -> None:
        assert extract_arxiv_id({"eprint": "hep-ph/0501001"}) == "hep-ph/0501001"

    def test_falls_back_to_journal_field(self) -> None:
        assert extract_arxiv_id(
            {"journal": "[arXiv'26] arXiv preprint arXiv:2605.10712"}
        ) == "2605.10712"

    def test_missing_returns_none(self) -> None:
        assert extract_arxiv_id({"title": "no arxiv id here"}) is None


class TestFigshareIdExtraction:
    def test_present(self) -> None:
        assert extract_figshare_id(
            {"journal": "Some figshare m9.figshare.12345 deposit"}
        ) == "m9.figshare.12345"

    def test_absent(self) -> None:
        assert extract_figshare_id({"title": "no figshare id"}) is None


class TestCveIdExtraction:
    def test_extract(self) -> None:
        assert extract_cve_id("see CVE-2024-12345 for details") == "CVE-2024-12345"

    def test_uppercases(self) -> None:
        assert extract_cve_id("cve-2024-12345") == "CVE-2024-12345"

    def test_missing(self) -> None:
        assert extract_cve_id("no cve here") == ""

    def test_regex_full_match(self) -> None:
        assert CVE_ID_RE.fullmatch("CVE-2024-12345")
        assert not CVE_ID_RE.fullmatch("CVE-24-12345")


class TestNormalizeTitle:
    def test_lowercase_strips_punctuation(self) -> None:
        assert normalize_title("Hello, World!") == "helloworld"

    def test_strips_whitespace(self) -> None:
        assert normalize_title("Foo  Bar  Baz") == "foobarbaz"

    def test_handles_pdf_paste_artifacts(self) -> None:
        # "TheImpact" (missing space) and "The Impact" should normalize equal.
        assert normalize_title("TheImpact of X") == normalize_title("The Impact of X")

    def test_handles_latex_in_title(self) -> None:
        # {\c{C}}akar decodes to Çakar; lowercase ç is outside [a-z0-9] and
        # gets stripped. Final form is "paperbyakar".
        assert normalize_title("Paper by {\\c{C}}akar") == "paperbyakar"
