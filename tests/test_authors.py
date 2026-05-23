"""Unit tests for pubs_emitter.authors."""
from __future__ import annotations

import sqlite3

from pubs_emitter.authors import (
    format_author,
    format_inventors,
    lookup_student_type,
    name_matches,
    parse_name_parts,
)


class TestParseNameParts:
    def test_comma_form(self) -> None:
        last, firsts = parse_name_parts("Davis, James C")
        assert last == "Davis"
        assert firsts == ["James", "C"]

    def test_space_form(self) -> None:
        last, firsts = parse_name_parts("James C Davis")
        assert last == "Davis"
        assert firsts == ["James", "C"]

    def test_single_word(self) -> None:
        last, firsts = parse_name_parts("Davis")
        assert last == "Davis"
        assert firsts == []

    def test_comma_no_first(self) -> None:
        last, firsts = parse_name_parts("Davis,")
        assert last == "Davis"
        assert firsts == []


class TestNameMatches:
    def test_substring_match(self) -> None:
        assert name_matches("Davis, James C", ["Davis, James C"])

    def test_case_insensitive(self) -> None:
        assert name_matches("DAVIS, JAMES C", ["davis, james c"])

    def test_no_match(self) -> None:
        assert not name_matches("Smith, John", ["Davis, James C"])


class TestLookupStudentType:
    def test_known_student_full_name(self, conn: sqlite3.Connection) -> None:
        assert lookup_student_type(conn, "Paschal C. Amusuo") == "G"

    def test_known_student_comma_form(self, conn: sqlite3.Connection) -> None:
        assert lookup_student_type(conn, "Amusuo, Paschal C") == "G"

    def test_initials_only_match(self, conn: sqlite3.Connection) -> None:
        # "Amusuo, P." → last name match + bib initials ("P") prefix of "PC".
        assert lookup_student_type(conn, "Amusuo, P.") == "G"

    def test_last_name_only_match(self, conn: sqlite3.Connection) -> None:
        # Empty bib initials still resolves via last-name match.
        assert lookup_student_type(conn, "Amusuo") == "G"

    def test_undergrad(self, conn: sqlite3.Connection) -> None:
        assert lookup_student_type(conn, "Test Undergrad") == "U"

    def test_unknown(self, conn: sqlite3.Connection) -> None:
        assert lookup_student_type(conn, "Smith, John") == ""

    def test_wrong_initials_does_not_match(self, conn: sqlite3.Connection) -> None:
        # Bib initials are the first letter of each space-separated first-name
        # part. "Amusuo, R" → bib initials "R" → "PC".startswith("R") is False.
        assert lookup_student_type(conn, "Amusuo, R") == ""


class TestFormatAuthor:
    def test_me_is_bolded(self, conn: sqlite3.Connection) -> None:
        out = format_author(conn, "Davis, James C", is_last=False)
        assert "\\b " in out and "\\b0" in out
        assert "Davis, J.C." in out

    def test_student_marker(self, conn: sqlite3.Connection) -> None:
        out = format_author(conn, "Amusuo, Paschal C", is_last=False)
        assert "\\super G" in out
        assert "\\nosupersub" in out

    def test_last_author_marker(self, conn: sqlite3.Connection) -> None:
        # is_last=True adds a `*` marker.
        out = format_author(conn, "Davis, James C", is_last=True)
        assert "*" in out
        assert "\\super " in out

    def test_advisor_marker(self, conn: sqlite3.Connection) -> None:
        out = format_author(conn, "Yung-Hsiang Lee", is_last=True)
        # # = advisor marker, * = last author. Comma-joined.
        assert "#" in out
        assert "*" in out

    def test_non_ascii_escaped_as_rtf_unicode(self, conn: sqlite3.Connection) -> None:
        # Çakar → \u199?akar in the formatted RTF
        out = format_author(conn, "{\\c{C}}akar, Test", is_last=False)
        assert r"\u199?" in out
        # No raw Ç byte should leak through.
        assert "Ç" not in out


class TestFormatInventors:
    def test_two_inventors(self) -> None:
        out = format_inventors("Davis, James C and Amusuo, Paschal C")
        assert "Davis, J.C." in out
        assert "Amusuo, P.C." in out
        assert ", " in out  # joined with comma

    def test_me_is_bolded(self) -> None:
        out = format_inventors("Davis, James C")
        assert "\\b " in out and "\\b0" in out

    def test_non_ascii_escaped(self) -> None:
        out = format_inventors("{\\c{C}}akar, Test")
        assert r"\u199?" in out
        assert "Ç" not in out

    def test_no_role_markers(self) -> None:
        # Patents don't carry G/U/#/* markers.
        out = format_inventors("Davis, James C")
        assert "\\super" not in out
