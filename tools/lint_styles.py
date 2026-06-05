"""Lint that flags raw RTF control codes in `src/pubs_emitter/rtf.py`
outside the style-registry routes.

The whole point of `src/pubs_emitter/styles.py` is that every styled
paragraph + inline run goes through `emit_styled` or `styled_inline`.
A raw `\\b`, `\\i`, `\\fs28`, `\\sb120`, `\\page`, `\\ul`, or `\\qc`
in `rtf.py` is an unmigrated emit site — pin the regression class
at lint time so future drift gets caught at PR review instead of
visual diff time.

Allowlist (lines exempt from the lint):
  * `styles.py` itself — defines the styles.
  * `RtfTable._render_row` — cell border block (`_CELL_BORDER_BLOCK`)
    + body cell content are mechanical RTF, not paragraph styling.
  * Bookmark wraps + hyperlink fields — `\\*\\bkmkstart`,
    `\\fldinst HYPERLINK`, `\\cs1`, `\\cf1`.
  * RTF document opener — font table, color table, stylesheet
    declaration, the leading `\\fs22` document default.
  * `_emit_list_item` + `_emit_list_item_with_body` — hanging-indent
    geometry (`\\li`, `\\fi`, `\\tx`) is paragraph-shape, not style.
  * Hand-rolled table rows (student-table tier dividers, courses-
    taught note rows, grant tables) — table cell shape is mechanical.

Run as a script or via the pytest hook `tests/test_styles_lint.py`.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


# Control words that should only originate from `styles.py`. Each entry
# is a regex matching the control word with its arg (if any).
BANNED_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("bold-open",       re.compile(r"\\b\b(?!\d)")),       # \b but not \b0
    ("italic-open",     re.compile(r"\\i\b(?!\d)")),       # \i but not \i0
    ("underline-open",  re.compile(r"\\ul\b")),
    ("font-size",       re.compile(r"\\fs\d+")),
    ("space-before",    re.compile(r"\\sb\d+")),
    ("space-after",     re.compile(r"\\sa\d+")),
    ("page-break",      re.compile(r"\\page\b")),
    ("alignment",       re.compile(r"\\q[clrj]\b")),
]


# Exact substring marker that, when present on a line, exempts it from
# the lint. Use sparingly — every exemption is a place future drift
# can hide.
ALLOWLIST_MARKERS: list[str] = [
    "lint-allow: raw-rtf",
]


# Source lines containing any of these substrings are auto-exempt.
# The list below captures the mechanical RTF paths the lint design
# explicitly opts out of (bookmarks, RTF document opener, table-cell
# borders, hanging-indent geometry, etc.).
AUTO_EXEMPT_SUBSTRINGS: list[str] = [
    # Regex word-boundary `\b` and word-char `\w` — Python regex
    # patterns, not RTF control codes. The lint targets RTF emit only.
    r"re.search",
    r"re.sub",
    r"re.match",
    r"re.finditer",
    r"re.findall",
    r"re.compile",
    # RTF document opener (stylesheet, font table, color table). The
    # stylesheet block spans multiple lines; each `heading N` style
    # declaration line is auto-exempt by its `keepn` marker (a control
    # word that appears only in stylesheet declarations).
    r"{\stylesheet",
    r"{\fonttbl",
    r"{\colortbl",
    r"{\rtf1\ansi",
    r" heading 1;",
    r" heading 2;",
    r" heading 3;",
    r" heading 4;",
    r"\keepn",
    r"Hyperlink;",
    # Hyperlink character style declaration split across two lines —
    # `\additive` is the marker found only in character-style decls.
    r"\additive",
    # Doc-level default font size — sets the body default.
    r'out.write(f"\\fs{_BODY_FONT_SIZE}\n")',
    # Hyperlink character style + table border block constants.
    r"\\cs1",
    r"\\cf1",
    r"\\cf2",
    r"_CELL_BORDER_BLOCK",
    r"_GRANT_CELL_BORDER",
    r"_STUDENT_CELL_BORDER",
    # Bookmark anchors are not "styling".
    r"\\*\\bkmkstart",
    r"\\*\\bkmkend",
    r"bookmark_prefix",
    # Hanging-indent / list-item geometry (\li/\fi/\tx) — paragraph
    # shape, not formatting.
    r"\\li{indent}",
    r"\\li{label_pos}",
    r"\\fi{fi}",
    r"\\tx{indent}",
    r"\\tx{tab_pos}",
    # Page setup / margins.
    r"\\paperw",
    r"\\paperh",
    r"\\margl",
    r"\\margr",
    # Hand-rolled table row borders / right-align cells.
    r"\\clbrdrt",
    r"\\clbrdrb",
    r"\\clbrdrl",
    r"\\clbrdrr",
    r"\\trowd",
    r"\\trgaph",
    r"\\trleft",
    r"\\cellx",
    r"\\intbl",
    r"\\cell",
    r"\\row",
    r"\\trrh",
    r"\\trhdr",
    r"\\clcbpat",
    # RTF spec footnote markers + hyperlink field instructions.
    r"\\field",
    r"\\fldinst",
    r"\\fldrslt",
    r"HYPERLINK",
    # `\fs20` is the small italic note in the CIE-partial footnote;
    # purposeful one-off font shrink. Keep the lint quiet for it.
    r"\\fs20\\i \\*Computed",
    # Per-section indent calculations (level * step) are arithmetic,
    # not styling.
    r"\\li{label_pos}",
    # `\fs{font_size}` placeholder in emit_styled-pre-migration
    # leftover docstrings.
    r"_HEADING_FONT_SIZE_BY_LEVEL",
    r"\\fs{font_size}",
]


def is_auto_exempt(line: str) -> bool:
    if any(m in line for m in ALLOWLIST_MARKERS):
        return True
    if any(s in line for s in AUTO_EXEMPT_SUBSTRINGS):
        return True
    # Lines that are only RTF docstring / commentary (no actual
    # control-code emit) — match by being inside a triple-quoted
    # docstring OR a `#` comment.
    stripped = line.lstrip()
    if stripped.startswith("#"):
        return True
    return False


def scan_file(path: Path) -> list[tuple[int, str, str, str]]:
    """Return list of (line_no, banned_kind, control_code, line_text)."""
    findings: list[tuple[int, str, str, str]] = []
    in_docstring = False
    docstring_marker: str | None = None
    lines = path.read_text().splitlines()
    for i, raw_line in enumerate(lines, 1):
        line = raw_line
        # Track triple-quoted docstrings so we don't lint inside them.
        if in_docstring:
            if docstring_marker and docstring_marker in line:
                in_docstring = False
                docstring_marker = None
            continue
        for marker in ('"""', "'''"):
            if marker in line:
                # Count occurrences — even count = open+close on same line.
                if line.count(marker) % 2 == 1:
                    in_docstring = True
                    docstring_marker = marker
                    # The opening line itself may carry RTF examples;
                    # treat as docstring.
                    break
        if in_docstring:
            continue
        if is_auto_exempt(line):
            continue
        # Allow `# lint-allow: raw-rtf` markers within the most recent
        # 8 preceding lines (covers multi-line emit blocks that need
        # a comment-block justification above them).
        prev_window = lines[max(0, i - 9): i - 1]
        if any(
            mk in pl
            for pl in prev_window
            for mk in ALLOWLIST_MARKERS
        ):
            continue
        for kind, pat in BANNED_PATTERNS:
            m = pat.search(line)
            if m:
                findings.append((i, kind, m.group(0), line.rstrip()))
                break  # one finding per line is plenty
    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--path", type=Path,
        default=Path("src/pubs_emitter/rtf.py"),
        help="Path to scan (default: src/pubs_emitter/rtf.py)",
    )
    args = parser.parse_args()
    findings = scan_file(args.path)
    if not findings:
        print(f"OK — no raw control codes outside the style-registry routes ({args.path})")
        return 0
    print(f"Found {len(findings)} raw-control-code finding(s) in {args.path}:")
    for line_no, kind, code, text in findings:
        print(f"  {args.path}:{line_no}: [{kind}] {code} — {text.strip()[:120]}")
    print()
    print("Route this emit through styles.emit_styled / styles.styled_inline")
    print("or add the line to AUTO_EXEMPT_SUBSTRINGS with a justification.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
