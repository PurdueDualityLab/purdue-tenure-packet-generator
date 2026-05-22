"""Author-name parsing + RTF formatting (with role markers + bold-for-me)."""
from __future__ import annotations

import sqlite3

from .config import ADVISORS, ME
from .latex import decode_latex


def name_matches(bib_name: str, person_list: list[str]) -> bool:
    return any(p.lower() in bib_name.lower() for p in person_list)


def lookup_student_type(conn: sqlite3.Connection, bib_name: str) -> str:
    """Return 'G' / 'U' if bib_name matches a known student, else ''.

    Normalizes by stripping periods before substring comparison so a single-letter
    middle initial ('C' vs 'C.') matches either way.
    """
    cur = conn.cursor()
    cur.execute("SELECT name, type FROM students")
    bib_norm = bib_name.lower().replace(".", "")
    for db_name, s_type in cur.fetchall():
        if db_name.lower().replace(".", "") in bib_norm:
            return str(s_type)
    return ""


def parse_name_parts(bib_name: str) -> tuple[str, list[str]]:
    """Parse 'Last, First Middle' or 'First Middle Last' → (last, [first_parts])."""
    if "," in bib_name:
        parts = [p.strip() for p in bib_name.split(",")]
        last = parts[0]
        firsts = parts[1].split() if len(parts) > 1 else []
    else:
        parts = bib_name.split()
        last = parts[-1]
        firsts = parts[:-1]
    return last, firsts


def format_author(conn: sqlite3.Connection, bib_name: str, is_last: bool) -> str:
    """Citation form: 'Last, F.I.' + bold-for-me + comma-joined role markers."""
    bib_name = decode_latex(bib_name)
    last, firsts = parse_name_parts(bib_name)
    initials = ".".join(f[0].upper() for f in firsts) + "." if firsts else ""
    formatted = f"{last}, {initials}" if initials else last

    if name_matches(bib_name, ME):
        formatted = f"\\b {formatted}\\b0"

    markers: list[str] = []
    student_type = lookup_student_type(conn, bib_name)
    if student_type:
        markers.append(student_type)
    if name_matches(bib_name, ADVISORS):
        markers.append("#")
    if is_last:
        markers.append("*")

    if markers:
        formatted += f"\\super {','.join(markers)}\\nosupersub"
    return formatted


def format_inventors(raw_authors: str) -> str:
    """Patent-table form: 'Last, F.I.', comma-joined, in bib order. Bold for me.

    Same name shape as citations. No role markers (no G/U/#/* in patent tables).
    Returns RTF-marked-up text — do NOT escape_rtf the result before emitting.
    """
    parts: list[str] = []
    for raw in raw_authors.split(" and "):
        decoded = decode_latex(raw.strip())
        last, firsts = parse_name_parts(decoded)
        initials = ".".join(f[0].upper() for f in firsts) + "." if firsts else ""
        formatted = f"{last}, {initials}" if initials else last
        if name_matches(decoded, ME):
            formatted = f"\\b {formatted}\\b0"
        parts.append(formatted)
    return ", ".join(parts)
