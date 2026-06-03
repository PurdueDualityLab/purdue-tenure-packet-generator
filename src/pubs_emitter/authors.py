"""Author-name parsing + RTF formatting (with role markers + bold-for-me)."""
from __future__ import annotations

import sqlite3

from .config import ADVISORS, ME
from .latex import decode_latex, rtf_escape_unicode


def name_matches(bib_name: str, person_list: list[str]) -> bool:
    return any(p.lower() in bib_name.lower() for p in person_list)


def lookup_student_type(conn: sqlite3.Connection, bib_name: str) -> str:
    """Return 'G' / 'U' if bib_name matches a known student, else ''.

    Structural match: last name must equal, AND the bib name's initials must
    be a prefix of the canonical name's initials. So "Amusuo, P." and
    "Amusuo, P.C." both resolve to "Paschal C. Amusuo" — bib initials "P"
    and "PC" are prefixes of canonical "PC". Empty bib initials still
    require last-name match (an author given only as "Amusuo" matches).
    """
    bib_last, bib_firsts = parse_name_parts(bib_name)
    bib_last_norm = bib_last.lower()
    bib_initials = "".join(f[0].upper() for f in bib_firsts if f)

    cur = conn.cursor()
    cur.execute("SELECT name, type FROM students")
    for db_name, s_type in cur.fetchall():
        db_last, db_firsts = parse_name_parts(db_name)
        if db_last.lower() != bib_last_norm:
            continue
        db_initials = "".join(f[0].upper() for f in db_firsts if f)
        if not bib_initials or db_initials.startswith(bib_initials):
            return str(s_type)
    return ""


_NAME_SUFFIXES: frozenset[str] = frozenset({"Jr", "Jr.", "Sr", "Sr.", "II", "III", "IV"})


def parse_name_parts(bib_name: str) -> tuple[str, list[str]]:
    """Parse 'Last, First Middle' or 'First Middle Last' → (last, [first_parts]).

    Generational suffixes (Jr, Sr, II, III, IV) are grouped with the
    preceding last-name token rather than treated as a standalone last
    name. Without this, natural-order "William P Maxam III" parses as
    last="III" and the student matcher fails to associate the row with
    bib entries like "Maxam III, William P" (last="Maxam III"). Comma-
    form input passes through unchanged — BibTeX's "Last, First" shape
    already keeps the suffix on the last-name side.
    """
    if "," in bib_name:
        parts = [p.strip() for p in bib_name.split(",")]
        last = parts[0]
        firsts = parts[1].split() if len(parts) > 1 else []
    else:
        parts = bib_name.split()
        last = parts[-1]
        firsts = parts[:-1]
        # Suffix on the END of natural-order input: grab the previous
        # token and merge ("William P Maxam III" → last="Maxam III").
        if last in _NAME_SUFFIXES and firsts:
            last = f"{firsts[-1]} {last}"
            firsts = firsts[:-1]
    return last, firsts


def format_author(conn: sqlite3.Connection, bib_name: str, is_last: bool) -> str:
    """Citation form: 'Last, F.I.' + bold-for-me + comma-joined role markers."""
    bib_name = decode_latex(bib_name)
    last, firsts = parse_name_parts(bib_name)
    initials = ".".join(f[0].upper() for f in firsts) + "." if firsts else ""
    # Encode non-ASCII (Ç, é, etc.) as RTF \u<num>? — name pieces are pure
    # text at this point; RTF markup (\b, \super) is added after.
    last = rtf_escape_unicode(last)
    initials = rtf_escape_unicode(initials)
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
        # Trailing `{}` empty group terminates the `\nosupersub` control
        # word without consuming the next character as its delimiter.
        # Bare `\nosupersub (2023)` renders as `(2023)` glued to the marker
        # ("Davis, J.C.*(2023)") because the space after `\nosupersub` is
        # eaten — same trap class as `\b0 X` / `\i0 X`. The `{}` empty
        # group is the standard RTF idiom for "end this control word, do
        # not eat my trailing space."
        formatted += f"\\super {','.join(markers)}\\nosupersub{{}}"
    return formatted


def format_inventors(raw_authors: str) -> str:
    """Patent-table form: 'Last, F.I.', comma-joined, in bib order. Bold for me.

    Same name shape as citations. No role markers (no G/U/#/* in patent tables).
    Returns RTF-marked-up text — do NOT escape_rtf the result before emitting.
    Non-ASCII chars (Ç, é, etc.) are pre-escaped for RTF.
    """
    parts: list[str] = []
    for raw in raw_authors.split(" and "):
        decoded = decode_latex(raw.strip())
        last, firsts = parse_name_parts(decoded)
        initials = ".".join(f[0].upper() for f in firsts) + "." if firsts else ""
        last = rtf_escape_unicode(last)
        initials = rtf_escape_unicode(initials)
        formatted = f"{last}, {initials}" if initials else last
        if name_matches(decoded, ME):
            formatted = f"\\b {formatted}\\b0"
        parts.append(formatted)
    return ", ".join(parts)
