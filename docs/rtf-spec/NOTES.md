# RTF — Notes for future-me

Cached spec mirrors (read these BEFORE writing new RTF code):

| File | Source | Use for |
|---|---|---|
| [`rtf15-biblioscape.html`](rtf15-biblioscape.html) | <https://www.biblioscape.com/rtf15_spec.htm> | Full RTF 1.5 spec — control words, syntax rules, character/paragraph properties |
| [`latex2rtf-rtfspec-7.html`](latex2rtf-rtfspec-7.html) | <https://latex2rtf.sourceforge.net/rtfspec_7.html> | "Document Area" section — table syntax + the §"every paragraph in a table row must have `\intbl`" rule |
| [`pindari-rtf3-tables.html`](pindari-rtf3-tables.html) | <https://www.pindari.com/rtf3.html> | Hands-on tables tutorial with examples |

## Rules I've learned the hard way

### 1. Tables: every cell's content paragraph needs `\pard\intbl`

Per [latex2rtf-rtfspec-7.html](latex2rtf-rtfspec-7.html) §Tables:

> Every paragraph that is contained in a table row must have the `\intbl`
> control word specified or inherited from the previous paragraph.

Without `\pard\intbl` before each cell's content, viewers treat the row
as inline text and the `\cell` markers vanish. Symptoms:
- TextEdit renders the row as one continuous strip
- Word can't import the table on paste — cells flatten to inline text

Canonical pattern:
```rtf
\trowd\trgaph108\trleft0
\clbrdrt\brdrs\brdrw15\clbrdrb...\cellx1800
\clbrdrt\brdrs\brdrw15\clbrdrb...\cellx9360
\pard\intbl Cell A content\cell
\pard\intbl Cell B content\cell
\row
```

### 2. Tables: cell borders need an explicit block per `\cellx`

Without `\clbrdrt\brdrs\brdrw15\clbrdrb\brdrs\brdrw15\clbrdrl\brdrs\brdrw15\clbrdrr\brdrs\brdrw15`
BEFORE each `\cellx{pos}`, Word doesn't draw vertical separators and
adjacent cell contents visually concatenate. Implemented as
`_CELL_BORDER_BLOCK` in `rtf.py` — single source of truth, shared by
`RtfTable` + all hand-rolled table emitters.

### 3. Multi-row tables with mixed cell structures need merged cells

When consecutive rows have different `\cellx` positions (e.g., a
tier-divider row with one wide cell vs data rows with 6 narrow
cells), TextEdit + Word visually collapse them into one row.

Fix: keep ALL `\cellx` positions identical across rows, use
`\clmgf` (merge-first) on the first cell of the divider row and
`\clmrg` (merge-continuation) on the rest. Emit empty `\cell`
placeholders for every merged cellx so the row's `\cell` count
matches the `\cellx` count.

### 4. Control-word delimiter eats trailing spaces

A bare control word like `\b0` consumes its trailing space as the
RTF parser's delimiter. So `\b Total:\b0 $X` reads as `Total:$X`
in the rendered output (the space disappears).

**Fix patterns** (any of these work):

- **Brace-scope the formatting** (preferred): `{\b Total:} $X` — the
  close-brace terminates the bold AND emits a literal space (no
  delimiter consumption). Used for the section-total line + the grant
  Row 1 numbering.
- **Backslash-tilde** (non-breaking space): `\b0\~$X`.
- **Double space**: `\b0  $X` — first space delimits, second renders.

Same bug class affects `\i0 ` (italic close), `\ul0 ` (underline close),
`\b0 `, `\cf0 ` etc. Anywhere a control word is followed by text that
must read as separated.

### 5. Bookmarks: names use underscores, not dots

RTF bookmark names are restricted to alphanumerics + underscore
(40 char max per the spec). So section codes like `C.18.1` become
bookmark name `C_18_1`. Helper `_ref_anchor(code)` does the
substitution; the post-pass `_finalize_ref_hyperlinks` agrees on
the same `code.replace(".", "_")` rule (pinned by
`TestRefAnchorAndHyperlinkFinalize`).

### 6. Internal hyperlinks: `\\l` flag in HYPERLINK field

To jump within the same document (bookmark target), use:

```rtf
{\field{\*\fldinst HYPERLINK \\l "BookmarkName"}{\fldrslt Display}}
```

The `\\l` (lowercase L) means "local jump" — Word's RTF reader
distinguishes this from external URLs (`HYPERLINK "https://..."`).

### 7. Hyperlink display: use `\cs1` Hyperlink character style

For Word copy-paste to preserve the link's blue+underline styling,
inline `\cf1\ul {text}\ul0\cf0` is NOT enough — Word strips inline
formatting on copy to clipboard and re-applies its named character
styles from the destination doc's stylesheet.

Define `Hyperlink` as a named character style in the stylesheet:

```rtf
{\stylesheet
{\s1\b\fs28...heading 1;}
{\*\cs1\additive\cf1\ul \sbasedon10 Hyperlink;}
}
```

Then reference inline:
```rtf
{\field{\*\fldinst HYPERLINK \\l "Bookmark"}
       {\fldrslt {\cs1\cf1\ul Display}}}
```

Word recognizes the canonical `Hyperlink` style name across the
clipboard. The `\cf1\ul` inside is fallback for viewers that don't
honor the character style (TextEdit, basic RTF parsers).

`\additive` = the character style ADDS to existing formatting rather
than replacing it (preserves surrounding bold/italic).

### 8. Non-ASCII chars: emit `\u<signed16>?` escapes, NOT raw UTF-8

The RTF header declares `\ansicpg1252` (cp1252). Raw UTF-8 bytes
get mangled when Word reads as cp1252. Use the `\u<num>?` escape
where `num` is the codepoint as a signed 16-bit int (values > 0x7FFF
wrap to negative).

Handled by `rtf_escape_unicode` in `latex.py`. The trailing `?` is
the 1-char fallback for non-Unicode-aware readers.

For supplementary-plane codepoints (> U+FFFF), encode as UTF-16
surrogate pairs (two `\u` escapes).

### 9. Page-range conventions

The bib uses LaTeX `--` for ranges (`pages = {1--12}`). Word/TextEdit
DON'T auto-convert `--` to en-dash, so `1--12` renders literally
with two hyphens (ugly on a CV).

Normalize at format time in `_format_pages` (`builders.py`):
- `"X--pages"` → `"X pages"` (Scholar's quirky article-number form)
- `"X--Y"` → `"X–Y"` (U+2013 en-dash, proper typographic form)

## Common gotchas summary

| Symptom | Diagnosis | Fix |
|---|---|---|
| "TitleCo-Inventors" cells merged | Missing `_CELL_BORDER_BLOCK` per `\cellx` | Add the block before each cellx |
| Tables flatten on Word paste | Missing `\pard\intbl` per cell | Add before each cell content |
| "Engineeringin 2019" no space | `\i0 in...` — `\i0` ate the space | Brace-scope: `{\i ...} in ...` |
| "Total:$X" no space | `\b0 $X` — `\b0` ate the space | `{\b Total:} $X` |
| Hyperlink loses blue on Word copy-paste | Inline `\cf1\ul` stripped by clipboard | Use `\cs1` named character style |
| "1--12" or "62--pages" verbatim | Pages from bib pass through raw | `_format_pages` normalizes |
| Tier divider visually merges with data row | Mixed `\cellx` count across rows | Use `\clmgf`/`\clmrg` cell-merge with matching `\cellx` positions |
