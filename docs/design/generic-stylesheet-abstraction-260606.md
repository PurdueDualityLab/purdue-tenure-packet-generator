# Generic stylesheet abstraction — design

## Problem

Today's [`src/pubs_emitter/styles.py`](../../src/pubs_emitter/styles.py)
is a registry where **the keys are semantic but the values are RTF
control words**:

```python
STYLES: dict[str, str] = {
    "section_h1": r"\s3\b\fs28 ",
    "section_h2": r"\s4\qc\b\ul\fs28\i0 ",
    "entry_name": r"\b ",
    "intro_note": r"\i\fs22 ",
    "venue_italic": r"\i ",
    ...
}
SPACING: dict[str, tuple[int, int]] = {
    "section_h1": (240, 120),
    ...
}
PAGE_BREAK_BEFORE: set[str] = {"roman_section", "subgroup_heading", ...}
```

The **names** are format-agnostic. The **values are entangled with
RTF**: a `\fs28` is "half-points × 2 → 14pt at the wire" — an RTF
encoding choice, not a typographic property. To know that
`section_h1` is *bold + 14pt + Word heading-3-style-mapped*, you have
to read the RTF and translate it back. To change the body font size,
you need RTF fluency.

Consequences:

1. **The registry is its own format-specific encoding** — there's no
   intermediate representation between "this is a section heading"
   and "these are the RTF control words." A future HTML / LaTeX
   emitter can't reuse the registry; it'd need a parallel one with
   different control word conventions.
2. **Editing styles requires RTF knowledge.** Changing
   `section_h1`'s font size means knowing that `\fs` takes
   half-points, not points, and that the trailing space matters.
3. **No standalone tests for "what does section_h1 actually look
   like."** Today's pin tests assert on the exact RTF string. There's
   no way to assert "section_h1 is bold + 14pt" without reading
   through `\b\fs28`.
4. **Couples styles to RTF emit, blocking the disentangling work**
   in [`ir-based-emit-disentangling-260606.md`](ir-based-emit-disentangling-260606.md).
   That refactor needs styles to be format-agnostic so the IR layer
   can speak in terms of `StyleRef("section_h1")` without nailing
   down RTF too early.

## Goal

A typed `StyleAttrs` registry where every style is described by **the
typographic properties it sets**, with a **separate translator**
(`styles_rtf.py`) that turns `StyleAttrs` into the same RTF control
words the renderer emits today.

After the refactor:

- `STYLES: dict[str, StyleAttrs]` carries semantic attributes.
- `styles_rtf.to_rtf_open(attrs) -> str` produces the RTF open string.
- `styles_rtf.to_rtf_close(attrs) -> str` produces the close string.
- `styles_rtf.to_rtf_stylesheet_block()` produces the document-header
  stylesheet declaration (currently `format_stylesheet_block()`).
- `emit_styled(out, name, text)` and `styled_inline(name, text)` keep
  their current signatures — internally they route through the
  translator instead of looking up a raw RTF string.
- Byte-identical RTF output to today's renderer at every site.

## Non-goals

- **Not implementing HTML / LaTeX styles.** This refactor is purely
  about splitting "semantic attributes" from "RTF encoding" — it
  doesn't add a second output format. A future
  `styles_html.to_html_open(attrs) -> str` is trivial once the
  substrate exists, but isn't part of this work.
- **Not changing any visual output.** Every section's appearance in
  the rendered packet stays identical. The refactor is purely
  internal substrate.
- **Not removing the existing `emit_styled` / `styled_inline` API.**
  Callers continue to invoke `emit_styled(out, "section_h1", text)`;
  the under-the-hood RTF generation changes but the call shape
  doesn't.

## Design

### `StyleAttrs` dataclass

```python
@dataclass(frozen=True)
class StyleAttrs:
    # Typography
    font_size_pt: Optional[int] = None        # None = inherit body size
    bold: bool = False
    italic: bool = False
    underline: bool = False
    color_rgb: Optional[tuple[int, int, int]] = None  # None = inherit

    # Layout
    alignment: Literal["left", "center", "right", "justify"] = "left"
    indent_twips: int = 0                     # \li
    first_line_indent_twips: int = 0          # \fi (negative for hanging)
    space_before_twips: int = 0               # \sb
    space_after_twips: int = 0                # \sa
    page_break_before: bool = False           # \page emit before open

    # Document-outline integration. Every formatted-document
    # format has a notion of "this paragraph is a heading at level N"
    # — RTF maps to `\sN` paragraph styles; HTML to `<h1>`/`<h2>`/…;
    # LaTeX to `\section`/`\subsection`/…. The substrate just records
    # the level; each translator maps to its format's conventions.
    heading_level: Optional[int] = None
    # 1 = roman / chapter
    # 2 = group / chapter-section
    # 3 = section
    # 4 = sub-section

    # Some formats restart their auto-numbered list at this paragraph
    # (Word's heading-2 list continues across the doc by default; the
    # first A. group heading under a new Roman section needs to
    # restart at A.). HTML / LaTeX ignore this hint.
    restart_list_numbering: bool = False

    # Character-style only (vs paragraph-style): inline styles like
    # `entry_name`, `venue_italic`, `field_label`, `table_header`. When
    # True, emit_styled treats this as inline (`{\...\}`) rather than a
    # paragraph (`\pard\plain\f0\...\par`).
    is_character_style: bool = False
```

### Sketch of the new `STYLES` dict

```python
STYLES: dict[str, StyleAttrs] = {
    "section_h1": StyleAttrs(
        font_size_pt=14, bold=True,
        space_before_twips=240, space_after_twips=120,
        heading_level=3,  # depth 3 in document outline
    ),
    "section_h2": StyleAttrs(
        font_size_pt=14, bold=True, italic=False, underline=True,
        alignment="center",
        space_before_twips=240, space_after_twips=120,
        heading_level=4,  # depth 4 in document outline
    ),
    "entry_name": StyleAttrs(
        bold=True, is_character_style=True,
    ),
    "intro_note": StyleAttrs(
        font_size_pt=11, italic=True,
        space_after_twips=120,
    ),
    "venue_italic": StyleAttrs(
        italic=True, is_character_style=True,
    ),
    "field_label": StyleAttrs(
        italic=True, is_character_style=True,
    ),
    "table_header": StyleAttrs(
        bold=True, is_character_style=True,
    ),
    "underline_marker": StyleAttrs(
        underline=True, is_character_style=True,
    ),
    "roman_section": StyleAttrs(
        font_size_pt=16, bold=True,
        space_before_twips=240, space_after_twips=120,
        heading_level=1,
        page_break_before=True,
    ),
    "group_heading": StyleAttrs(
        font_size_pt=16, bold=True,
        space_before_twips=240, space_after_twips=120,
        heading_level=2,
    ),
    "subgroup_heading": StyleAttrs(
        font_size_pt=14, bold=True, underline=True,
        alignment="center",
        space_before_twips=240, space_after_twips=120,
        heading_level=2,
        page_break_before=True,
    ),
    "na_placeholder": StyleAttrs(
        font_size_pt=11, italic=True,
    ),
    "entry_summary": StyleAttrs(
        bold=True, is_character_style=True,
    ),
}
```

Each entry self-documents the visual intent. An editor reading
`section_h1` sees "14pt bold, mapped to Word's heading 3 style" — no
RTF translation in their head.

### `styles_rtf.py` translator

The translator is a small function — its job is to emit the same RTF
control-word sequence the current `STYLES` dict carries, derived from
the `StyleAttrs` fields:

```python
def to_rtf_open(attrs: StyleAttrs) -> str:
    """Translate a StyleAttrs into the leading RTF control word run."""
    parts: list[str] = []
    if attrs.is_character_style:
        # Character styles are emitted inline, no paragraph reset.
        # `styled_inline` callers wrap in `{...}` themselves.
        pass
    else:
        # Paragraph style — reset state first.
        parts.append(r"\pard\plain\f0")
        if attrs.heading_level is not None:
            parts.append(rf"\s{attrs.heading_level}")
        if attrs.indent_twips:
            parts.append(rf"\li{attrs.indent_twips}")
        if attrs.first_line_indent_twips:
            parts.append(rf"\fi{attrs.first_line_indent_twips}")
        if attrs.alignment == "center":
            parts.append(r"\qc")
        elif attrs.alignment == "right":
            parts.append(r"\qr")
        elif attrs.alignment == "justify":
            parts.append(r"\qj")
    if attrs.bold:
        parts.append(r"\b")
    if attrs.italic:
        parts.append(r"\i")
    if attrs.underline:
        parts.append(r"\ul")
    if attrs.font_size_pt is not None:
        # RTF \fs is half-points.
        parts.append(rf"\fs{attrs.font_size_pt * 2}")
    # Trailing space terminates the last control word so the caller
    # can append literal text immediately. The current STYLES values
    # all end with a space; preserve that.
    return "".join(parts) + " "


def to_rtf_close(attrs: StyleAttrs) -> str:
    """Translate a StyleAttrs into the trailing close run.

    Character styles get explicit close control words (`\\b0`, `\\i0`,
    etc.) so the inline span ends cleanly. Paragraph styles emit
    `\\par` to terminate the paragraph.
    """
    parts: list[str] = []
    # Order matters — close in reverse-open order so nesting is clean.
    if attrs.font_size_pt is not None and not attrs.is_character_style:
        # Body size reset for paragraph styles; character styles
        # inherit the surrounding size on close.
        pass
    if attrs.underline:
        parts.append(r"\ul0")
    if attrs.italic:
        parts.append(r"\i0")
    if attrs.bold:
        parts.append(r"\b0")
    if not attrs.is_character_style:
        parts.append(r"\par")
    return "".join(parts)
```

The exact translator logic is faithful to today's `emit_styled` /
`styled_inline` implementation — every place that today does
`out.write(STYLES[name] + text + close)` becomes
`out.write(to_rtf_open(attrs) + text + to_rtf_close(attrs))`.

### Stylesheet block

Today's `format_stylesheet_block()` in [styles.py](../../src/pubs_emitter/styles.py)
emits the document-header `{\stylesheet ...}` block that names
heading styles for Word's TOC + nav pane. After refactor:

```python
# styles_rtf.py
def to_rtf_stylesheet_block() -> str:
    """Emit the `{\\stylesheet ...}` document-header block.

    Walks STYLES looking for paragraph styles with a
    `heading_level`, and emits one `\\s{N}` entry per level.
    """
    ...
```

The Word heading names ("heading 1", "heading 2", ...) come from the
mapping that today lives in `styles._HEADING_STYLE_NAMES`.

### Backwards compatibility

The public API (`emit_styled`, `styled_inline`, `format_stylesheet_block`)
keeps its current signatures. Internally:

```python
# styles.py — public API
def emit_styled(out: IO[str], name: str, text: str, ...) -> None:
    attrs = STYLES[name]
    out.write(to_rtf_open(attrs) + text + to_rtf_close(attrs))

def styled_inline(name: str, text: str) -> str:
    attrs = STYLES[name]
    # is_character_style invariant: must be True for inline use.
    assert attrs.is_character_style, f"{name!r} is not an inline style"
    return f"{{{to_rtf_open(attrs)}{text}{to_rtf_close(attrs)}}}"
```

Callers ([rtf.py](../../src/pubs_emitter/rtf.py), tests, etc.) keep
their existing call shape.

### Module layout

```
src/pubs_emitter/
├── styles.py            # Public API + STYLES dict (StyleAttrs values)
├── styles_rtf.py        # NEW — RTF translator
└── ...
```

`styles.py` stops carrying raw RTF strings. `styles_rtf.py` is the
only file in the codebase that knows about `\b`, `\fs`, `\sN`. Future
`styles_html.py` would parallel `styles_rtf.py` for HTML output.

## Migration plan

Five phases. Each phase is byte-parity-verifiable against the current
build.

### Phase 1 — `StyleAttrs` dataclass + per-style attribute reverse-engineering

**Scope:** Author `StyleAttrs` + populate `STYLES_GENERIC: dict[str, StyleAttrs]`
alongside (NOT replacing) the existing `STYLES: dict[str, str]`. For each
of the ~14 existing styles, decode the current RTF string into
`StyleAttrs` fields. No callers touch the new dict yet.

**Deliverables:**

- New `_STYLES_GENERIC_RAW` literal in `styles.py` (under a clearly-
  named module-internal namespace so callers can't accidentally reach it).
- 14 unit tests asserting each `StyleAttrs` decodes to the same fields
  as the current RTF string round-trips. (Test format: given the old
  `STYLES["section_h1"]` value, parse it back into `StyleAttrs`; assert
  equality with the hand-authored `_STYLES_GENERIC_RAW["section_h1"]`.
  This is "the dictionaries agree.")
- No behavioral change to the rendered packet.

**Tests:**

| Test | Asserts |
|---|---|
| `test_section_h1_attrs_match_legacy_rtf` | StyleAttrs(font_size_pt=14, bold=True, …) for section_h1 round-trips to the current RTF string |
| `test_every_style_round_trips` | Loop over all 14 styles, assert agreement |

**Exit criteria:** All 14 styles have hand-authored `StyleAttrs`
matching the legacy RTF. No production code change yet.

### Phase 2 — `styles_rtf.py` translator

**Scope:** Add the `to_rtf_open` / `to_rtf_close` /
`to_rtf_stylesheet_block` functions. They consume `StyleAttrs` and
produce RTF. Verify they produce the same output as the legacy
`STYLES` lookups.

**Deliverables:**

- New `src/pubs_emitter/styles_rtf.py` module.
- 14 pin tests: for each style, assert
  `to_rtf_open(STYLES_GENERIC[name]) == STYLES[name]` for character
  styles, and the right RTF leader for paragraph styles.
- Translator tests for SPACING, PAGE_BREAK_BEFORE, BORDER_BLOCKS too.

**Tests:**

| Test | Asserts |
|---|---|
| `test_to_rtf_open_character_style_section_h1` | Matches legacy STYLES["section_h1"] |
| `test_to_rtf_open_paragraph_style_intro_note` | Matches legacy `\i\fs22 ` |
| `test_to_rtf_close_emits_b0_then_i0_then_par` | Order matters |
| `test_to_rtf_stylesheet_block_matches_legacy` | Document-header block byte-identical |

**Exit criteria:** Translator output byte-identical to legacy STYLES
strings for every name. Still no production code change.

### Phase 3 — Switch `emit_styled` / `styled_inline` to translator

**Scope:** Reroute the public API through `to_rtf_open` /
`to_rtf_close` instead of looking up the raw RTF string. The
behavioral output stays the same; the path changes.

**Deliverables:**

- `styles.py` `emit_styled` / `styled_inline` / `format_stylesheet_block`
  call `styles_rtf.to_rtf_open(STYLES_GENERIC[name])` etc.
- Delete the legacy raw-RTF `STYLES: dict[str, str]` (now derived).
- Rename `STYLES_GENERIC` → `STYLES`. The migration is complete.

**Tests:**

| Test | Asserts |
|---|---|
| `test_e2e_byte_identical` | Live build via `pubs-emitter.py` produces the same RTF as the pre-refactor build (pinned via `/tmp/legacy.rtf` snapshot) |
| Existing `test_styles.py` and `test_styles_lint.py` | All pass unchanged |

**Exit criteria:** Live build byte-identical to pre-refactor. The new
`StyleAttrs` is the source of truth; the RTF translator is the sole
production path for style emit.

### Phase 4 — Cleanup + docs

**Scope:** Move the legacy support over to the new substrate, update
`docs/dev/` references that mention raw RTF in styles, ship a
`docs/dev/STYLES.md` explaining how to add/modify a style.

**Deliverables:**

- `docs/dev/STYLES.md` — one-pager: "to add a style, add a
  `StyleAttrs` entry; the RTF translator handles the rest." Covers
  character vs paragraph styles, the `is_character_style` invariant,
  Word stylesheet integration.
- Existing docstrings updated.
- Test suite continues to pass.

**Exit criteria:** Style editing requires zero RTF knowledge. Authors
add a `StyleAttrs(...)` literal; the translator is invisible.

### Phase 5 — (FUTURE; out of scope) HTML translator

Parallels Phase 2 — adds `styles_html.py` with the same translator
shape but emitting `<span class="...">` / inline CSS. Not part of
this refactor; pinned in this doc so the substrate's shape doesn't
paint into an RTF-only corner.

## Test strategy

Three layers:

1. **Per-style pin tests** (Phase 1 + 2) — every existing style's
   `StyleAttrs` decoded shape matches the legacy RTF.
2. **Translator pin tests** (Phase 2) — every translator output is
   byte-identical to the legacy `STYLES[name]` string.
3. **E2E pin tests** (Phase 3) — full document build byte-identical
   to pre-refactor snapshot.

Layer (1) catches "I forgot to set bold=True on section_h1." Layer (2)
catches "the translator emits `\b\fs28` instead of `\fs28\b`." Layer (3)
catches everything else.

## Risks

### Substrate (format-agnostic)

**R1: Character-vs-paragraph style ambiguity.** The
`is_character_style: bool` flag distinguishes inline emphasis (HTML
analogue: `<span>`; LaTeX: text command) from block-level styles
(HTML: `<p>`/`<div>`; LaTeX: paragraph). Calling `emit_styled(out,
"venue_italic", ...)` (a paragraph-emit helper) on a character style
would be a bug. Mitigation: `assert attrs.is_character_style ^
is_paragraph_emit` at the appropriate sites. The current 4 paragraph
styles + 7 character styles are statically partitioned; cross-use is
a programmer error caught by the assertion. This concern is generic
— every format draws the run-vs-paragraph distinction somehow.

**R2: Border blocks aren't a single style.** `_CELL_BORDER_BLOCK` in
[rtf.py](../../src/pubs_emitter/rtf.py) is the 4-side single-line
border RTF emit for table cells. It's not in `STYLES` today. Decision:
keep `_CELL_BORDER_BLOCK` in `rtf.py` for now; it's a table-cell
specific concern that doesn't fit the `StyleAttrs` model. If a future
need surfaces (e.g., HTML emit needs to translate to `<td
style="border:...">`), add a `BorderAttrs` dataclass paralleling
`StyleAttrs`.

### RTF translator specifics (in `styles_rtf.py` only)

These are concerns the RTF translator carries on the substrate's
behalf. Other format translators (`styles_html.py`, etc.) would have
their own format-specific risk list; none of these bleed into the
generic `StyleAttrs` substrate.

**R3: Order-of-control-words sensitivity.** RTF readers tolerate
control-word reordering in most cases, but Word's TOC scanner is
finicky about `\sN` (paragraph style) appearing before character
formatting. Mitigation: the RTF translator emits in a fixed canonical
order (style → indent → alignment → bold/italic/underline → font
size). The byte-identity pin tests on `styles_rtf` catch any
deviation.

**R4: Trailing-space delimiter trap.** RTF control words like `\fs28`
need a delimiter (space, `\`, or `{`) to separate them from the
following text. The current `STYLES[name]` strings all end with a
space; the translator must too. Mitigation: explicit unit test that
every `to_rtf_open(...)` output ends with `" "`.

## Rollback story

Each phase is independently revertible:

- Phase 1: drop the new dict; no behavior change.
- Phase 2: drop the new module; no behavior change.
- Phase 3: revert `emit_styled` / `styled_inline` to the dict lookup
  path. The byte-identity invariant means rollback is one git revert.

## Type discipline (for ALL new code in this refactor)

The substrate this refactor introduces is small, well-bounded, and a
natural place to **upgrade the codebase's type story** at the same
time. Current mypy state (audited 2026-06-06): `pyproject.toml`
configures mypy in gradual mode (`disallow_untyped_defs = false`),
nothing in tests/CI runs it, and 22 unrepaired errors sit in
production code. The compiler is not actively protecting us today.

New modules from this refactor — `styles.py` (post-refactor),
`styles_rtf.py`, and any test-helper modules — ship under strict mode
and a real gate:

1. **`mypy --strict` per-module override.** `pyproject.toml` gains:

   ```toml
   [[tool.mypy.overrides]]
   module = ["pubs_emitter.styles", "pubs_emitter.styles_rtf"]
   disallow_untyped_defs = true
   disallow_untyped_calls = true
   disallow_any_explicit = true   # No `Any` without inline `# why:` justification.
   warn_return_any = true
   no_implicit_reexport = true
   ```

   Legacy modules stay at gradual mode — this refactor doesn't touch
   them. New code lives at a higher standard from day one.

2. **mypy gated by the test suite.** A new
   `tests/test_strict_mypy.py` runs `mypy` via subprocess against the
   strict-mode modules and fails the build on any error. Wired into
   `pytest` so `python3 -m pytest` catches type regressions on the
   substrate without manual `mypy` invocation.

3. **Specific types, not raw dicts.** `StyleAttrs` is a
   `@dataclass(frozen=True, kw_only=True)` — every field has a type
   annotation, no `Any`, no `dict[str, object]` shortcut. `Literal`
   types where the value space is small + closed:
   `alignment: Literal["left", "center", "right", "justify"]` rather
   than `str`. The `STYLES` registry is `dict[str, StyleAttrs]` —
   typed both directions; mypy catches typo'd style names at lookup
   sites.

4. **No `Any` escape hatches without justification.** If a field
   genuinely needs `Any` (e.g., a payload that crosses a serialization
   boundary), the line carries an inline comment:
   `extra: dict[str, Any]  # why: caller-owned escape hatch for
   directives that need ctx-bag fields not modeled here yet.`

The cost: ~30 min of additional discipline work per phase to keep
mypy clean. The win: every refactor step is double-checked by the
type system, and the substrate becomes a beachhead for tightening
the rest of the codebase later.

## How this composes with IR-based disentangling

The companion design doc
[`ir-based-emit-disentangling-260606.md`](ir-based-emit-disentangling-260606.md)
proposes a Block IR for the walker → emit path. The styles refactor
**must land first** because the IR layer wants to speak in terms of
`StyleRef("section_h1")` rather than RTF strings. With the generic
`StyleAttrs` registry, the IR's `RtfWriter` translates style refs to
RTF; an `HtmlWriter` translates them to CSS. Without this refactor,
the IR would need to carry RTF strings inline, defeating the
disentangling goal.

The phasing in this doc is independent — it can land standalone — but
the architectural value compounds with the IR work.
