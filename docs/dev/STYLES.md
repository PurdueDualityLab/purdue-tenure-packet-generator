# Adding or editing a style

The style registry lives in [`src/pubs_emitter/styles.py`](../../src/pubs_emitter/styles.py).
Each style is a `StyleAttrs` dataclass describing the typographic
properties it sets — bold, italic, font-size, heading-level. The RTF
encoding lives separately in
[`src/pubs_emitter/styles_rtf.py`](../../src/pubs_emitter/styles_rtf.py)
(`to_rtf_open`, `to_rtf_close`, applied automatically by `emit_styled`
and `styled_inline`). **Editing a style requires zero RTF knowledge.**

## To add a new style

1. Add an entry to `_STYLES_ATTRS` in `styles.py`:

   ```python
   "my_new_style": StyleAttrs(
       bold=True,
       italic=True,
       font_size_pt=14,
       heading_level=3,        # optional: target Word "heading 3"
   ),
   ```

2. Add a pin to the legacy-RTF table in
   [`tests/test_styles.py`](../../tests/test_styles.py)
   (`TestStyleAttrsTranslator._LEGACY_OPENS` and `_LEGACY_CLOSES`) so
   the byte-identity invariant covers your new style.

3. Use it from `rtf.py`:

   ```python
   from .styles import emit_styled
   emit_styled(out, "my_new_style", "the styled text")
   # — or, for inline use —
   from .styles import styled_inline
   prefix_run = styled_inline("my_new_style", "venue name")
   ```

## Character vs paragraph styles

The `is_character_style: bool` field distinguishes:

* **Character styles** (`is_character_style=True`) — inline emphasis
  embedded mid-paragraph (e.g. `venue_italic`, `field_label`,
  `entry_name`). No `\pard` / `\par`; just the open + text + close.
  Use `styled_inline(name, text)`.
* **Paragraph styles** (`is_character_style=False`, default) —
  full-paragraph emit with `\pard\plain\f0` reset, indent, spacing,
  border, content, close, then a trailing body re-baseline paragraph.
  Use `emit_styled(out, name, text)`.

Calling `styled_inline` with a paragraph style (or vice versa) is a
programmer error.

## Field reference

| Field | Type | Purpose |
|---|---|---|
| `font_size_pt` | `Optional[int]` | Font size in points (e.g. `11`, `14`, `16`). `None` = inherit from parent. RTF translator multiplies by 2 to produce `\fs{N}`. |
| `bold` | `bool` | True → emit `\b` + close `\b0`. |
| `italic` | `Optional[bool]` | Tristate. `None` = no emit; `True` = `\i` + close `\i0`; `False` = explicit `\i0` (override a heading-style italic default). |
| `underline` | `bool` | True → emit `\ul` + close `\ulnone`. |
| `alignment` | `Literal["left","center","right","justify"]` | `left` is default (no emit). Others emit `\qc` / `\qr` / `\qj`. |
| `indent_twips` | `int` | Left indent (1440 twips = 1 inch). Applied by `emit_styled` as `\li{N}`. |
| `space_before_twips` | `int` | (Reserved — current code routes through the `SPACING` dict by style name.) |
| `space_after_twips` | `int` | (Reserved — see above.) |
| `page_break_before` | `bool` | (Reserved — current code routes through the `PAGE_BREAK_BEFORE` set.) |
| `heading_level` | `Optional[int]` | 1 / 2 / 3 / 4 → emit `\s{N}` (Word heading style ID). Listed in the document's `{\stylesheet}` block. |
| `border_block` | `str` | Raw RTF border control (e.g. `\brdrt\brdrs\brdrw15...`). Currently used only by `career_phase_divider`. |
| `is_character_style` | `bool` | See above. |

## Why this shape?

Three reasons the registry is `StyleAttrs`-typed, not raw RTF strings:

1. **Editor-friendly.** Bumping a heading from 14pt to 15pt is
   `font_size_pt=15`, not "edit the `\fs28` half-points and remember
   that the trailing space matters."
2. **Format-agnostic.** A future `styles_html.py` would parallel
   `styles_rtf.py` for HTML output — same `StyleAttrs` input, different
   wire format. The registry doesn't paint into an RTF corner.
3. **Type-checked.** mypy enforces the dataclass shape; `Literal[...]`
   on `alignment` catches typos. The translator is a small,
   pin-tested function — easy to reason about in isolation.

## Legacy `\i0` parity quirk

The legacy `_close_for` regex matches `\i` inside `\i0` (the explicit
italic-OFF token) at the END of `subgroup_heading`'s open string, and
pairs it with `\i0` again in the close. The result: shipped output
contains a doubled `\i0` at subgroup-heading close
(`\i0\ulnone\b0`). Word's parser treats italic-off idempotently, so
this is harmless — but the byte-identity invariant requires the new
translator to reproduce it. See `styles_rtf.to_rtf_close`'s docstring
for the rationale and the per-style pin tests in
`tests/test_styles.py` for the legacy-shape catalog.
