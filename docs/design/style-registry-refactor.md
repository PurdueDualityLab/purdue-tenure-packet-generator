# Style-registry refactor — design

## Problem

`rtf.py` currently treats RTF formatting as a property of each emitter
function. `_emit_section_heading` knows that section headings are bold,
`_emit_subgroup_heading` knows that subgroups are centered + bold +
underlined, `_emit_intro_note` knows that intro notes are italic. The
helpers ARE the style registry — implicitly, by virtue of being the
only callers that emit those control codes.

This shape is fine when there are a few styles, but two problems get
sharper as the doc grows:

1. **Drift surface area.** A style change ("make intro notes
   right-aligned") requires editing whichever helper owns that style,
   AND verifying no other call site spells the same control codes
   inline. Today: A.1 inline labels emit `\i` directly; A.6 awards
   headers emit `\b` directly inside `RtfTable.add_header`; the
   career-phase divider's `\sa240` is a magic number inside the inline-
   heading helper. There's no enforcement that every styled paragraph
   routes through a helper.

2. **Discoverability.** A reader of `rtf.py` who wants to know "what's
   the rule for italic mid-paragraph labels?" has to grep `\i` and
   filter out incidental matches. The style decisions are scattered.

The candidate's CSS-stylesheet framing crystallizes the goal:
**rendering should be mechanical obedience to a single policy artifact
that all emitters read from.**

## Goal

A single `styles.py` module that owns:

- The **name → RTF control sequence** map for every styled paragraph.
- The **spacing** (sb / sa twips) per style.
- The **page-break-before** set for hard-break styles.
- A `emit_styled(out, style_name, text, *, indent=…)` primitive that
  every paragraph-level emitter routes through.
- A `styled_inline(style_name, text) -> str` helper for mid-paragraph
  emphasis (A.1 field labels, table cell labels).

Plus a **lint** that scans `rtf.py` for raw `\b`, `\i`, `\fs`, `\ul`,
`\sb`, `\sa`, `\page` outside `styles.py` and a small allowlist of
mechanical paths (table-row borders, RTF document opener). Future drift
gets caught at PR review.

## Non-goals

- **No semantic changes** — the rendered RTF must be byte-identical
  (modulo paragraph-spacing tweaks the refactor explicitly chooses).
  Every call site visit is a pure refactor that preserves emit.
- **No CSS parser** — this is a Python dict, not a stylesheet language.
  Aspirational future work: derive RTF from a higher-level declarative
  description, but not now.
- **No external configuration** — styles are code-defined. The packet
  ships once; no need for YAML-overridable styles.

## Proposed API

### `src/pubs_emitter/styles.py`

```python
"""Single source of truth for every styled paragraph + inline run in
the rendered RTF. Every emitter in `rtf.py` routes through
`emit_styled` (paragraph) or `styled_inline` (mid-paragraph) so a
style change touches ONE entry instead of N call sites."""
from __future__ import annotations

import re
from typing import IO

# RTF half-points = 2 × point size. 22 = 11pt body.
_BODY_FONT_SIZE = 22

# Open-tag RTF sequence for each style. Style names are documentation:
# pick the most semantic name available so callsites read as policy.
STYLES: dict[str, str] = {
    # Paragraph-level styles
    "body":                 rf"\fs{_BODY_FONT_SIZE}",
    "intro_note":           rf"\i\fs{_BODY_FONT_SIZE}",
    "section_h1":           r"\b\fs28",
    "section_h2":           r"\i\fs26",
    "section_h3":           r"\i\fs24",
    "section_h4":           rf"\i\fs{_BODY_FONT_SIZE}",
    "group_heading":        r"\b\fs32",
    "subgroup_heading":     r"\qc\b\ul\fs28",
    "roman_section":        r"\b\fs32",
    "inline_subheading":    rf"\i\fs26",        # C.5 fallback subcat label
    "career_phase_divider": rf"\i\fs{_BODY_FONT_SIZE}",  # + border attrs
    "na_placeholder":       rf"\i\fs{_BODY_FONT_SIZE}",
    # Inline (mid-paragraph) styles
    "field_label":          r"\i",              # A.1 Name / ORCID / etc.
    "code_in_table":        r"",                # plain — was bold, now plain
    "grant_total_label":    r"\i",
}

# (space-before, space-after) in twips. Defaults to (0, 0).
SPACING: dict[str, tuple[int, int]] = {
    "intro_note":           (0, 120),
    "subgroup_heading":     (0, 120),
    "roman_section":        (0, 120),
    "career_phase_divider": (120, 240),
    "group_heading":        (0, 120),
}

# Styles that emit a hard `\page` BEFORE the paragraph (CAPS BOLD
# UNDERLINED + Roman section per the Purdue template convention).
PAGE_BREAK_BEFORE: set[str] = {"subgroup_heading", "roman_section"}

# Styles whose open-tag emits a border block (career-phase divider).
# Border RTF is verbose and not naturally a "control word"; capture
# it once here instead of inline-ing in the open string.
BORDER_BLOCKS: dict[str, str] = {
    "career_phase_divider":
        r"\brdrt\brdrs\brdrw15\brsp40\brdrb\brdrs\brdrw15",
}

# Close-tag pairings. Font-size resets are handled by the body re-
# baseline after the paragraph, not by a closing token, so `\fs{N}`
# doesn't have a close. Alignment (`\qc`) similarly closes via the
# paragraph reset.
_CLOSE_TAGS: dict[str, str] = {
    r"\b":  r"\b0",
    r"\i":  r"\i0",
    r"\ul": r"\ulnone",
}


def _close_for(open_codes: str) -> str:
    """Derive the close-tag sequence for an open-tag string.

    `\b\fs28` → `\b0` (font size doesn't need a close).
    `\i\b\ul` → `\ulnone\b0\i0` (closes in reverse order).
    """
    tokens = re.findall(r"\\[a-z]+", open_codes)
    closes = [_CLOSE_TAGS[t] for t in tokens if t in _CLOSE_TAGS]
    return "".join(reversed(closes))


def emit_styled(
    out: IO[str],
    style: str,
    text: str,
    *,
    indent: int = 0,
) -> None:
    """Emit a fully-styled paragraph applying `STYLES[style]`.

    The single paragraph-level emit primitive — every styled paragraph
    in the doc routes through here. The function:
      * (Optionally) emits a hard page break before the heading.
      * Resets paragraph formatting (\\pard\\plain).
      * Applies the indent + spacing.
      * Applies the style's open RTF control sequence.
      * Writes the (already-escaped) text.
      * Applies the matching close sequence.
      * Re-baselines the body paragraph.
    """
    prefix = STYLES[style]
    sb, sa = SPACING.get(style, (0, 0))
    border = BORDER_BLOCKS.get(style, "")
    close = _close_for(prefix)
    if style in PAGE_BREAK_BEFORE:
        out.write("\\pard\\page\\par\n")
    spacing = ""
    if sb:
        spacing += f"\\sb{sb}"
    if sa:
        spacing += f"\\sa{sa}"
    out.write(
        f"\\pard\\plain\\li{indent}{spacing}{border}{prefix} "
        f"{text}{close}\\par\n"
        f"\\pard\\plain\\fs{_BODY_FONT_SIZE}\\par\n"
    )


def styled_inline(style: str, text: str) -> str:
    """Return an inline RTF fragment with `STYLES[style]` applied.

    For mid-paragraph emphasis where the surrounding paragraph context
    is owned by another emitter — e.g., A.1 field labels embedded
    inside `_emit_list_item`'s body string.
    """
    prefix = STYLES[style]
    close = _close_for(prefix)
    return f"{prefix} {text}{close}"
```

### Lint

`tools/lint_styles.py` scans `src/pubs_emitter/rtf.py` for raw control
codes that should route through `styles.py`. Allowlist:

- `styles.py` itself
- The RTF document opener (font table, color table, stylesheet block)
- `RtfTable._render_row` (table-cell borders are mechanical)
- Bookmark wraps (`\\*\\bkmkstart`, `\\*\\bkmkend`)
- Hanging-indent paragraph attrs (`\li`, `\fi`, `\tx`)

Banned outside the allowlist:
- `\\b ` / `\\b0` / `\\i ` / `\\i0` / `\\ul` / `\\ulnone`
- `\\fs\d+`
- `\\sb\d+` / `\\sa\d+`
- `\\page`
- `\\qc` / `\\ql` / `\\qr`

A finding emits the file:line + offending control code. Run as a
pytest test so CI catches drift.

## Migration plan

The refactor is **incremental, byte-identity-preserving, and safe to
abandon mid-way.** Each phase is independently shippable.

### Phase 1 — substrate (this commit)

- Create `src/pubs_emitter/styles.py` with `STYLES`, `SPACING`,
  `PAGE_BREAK_BEFORE`, `BORDER_BLOCKS`, `emit_styled`, `styled_inline`.
- Migrate ONE canary helper to use `emit_styled` — `_emit_subgroup_heading`
  is the right choice: clearly bounded, low call-site count, recently
  touched so easy to verify.
- Verify byte-identical RTF output before and after via diff.
- Tests + live emit green.

### Phase 2 — paragraph-level helpers

Migrate (in order):
- `_emit_roman_section_heading` → `emit_styled(out, "roman_section", …)`
- `_emit_group_heading` → `emit_styled(out, "group_heading", …)`
- `_emit_intro_note` → `emit_styled(out, "intro_note", …)`
- `_emit_section_heading` → `emit_styled(out, f"section_h{level}", …)` —
  needs branch on level since the bookmark wrap and emphasis vary; the
  variation lives in the EMITTER, not the registry.
- `_emit_inline_heading` → `emit_styled(out, "inline_subheading", …)` or
  `emit_styled(out, "career_phase_divider", …)`.

### Phase 3 — inline labels + N/A placeholders

- A.1 inline field labels: `f"\\b Name\\b0 :"` → `f"{styled_inline('field_label', 'Name')}:"`
- Section heading "N/A" empty-list emits (A.2 degrees, A.6 awards, A.7
  memberships, A.4 positions, etc.) → `emit_styled(out, "na_placeholder", "N/A", indent=…)`
- Grant total label inside `_emit_intro_note` is already routed; the
  label-vs-value split moves into `styles.py` via a "labeled-intro-note"
  helper.

### Phase 4 — table cell content

- `RtfTable.add_header` adds bold inline. Migrate via a "table_header"
  style that wraps the cell content: `f"\\pard\\intbl {styled_inline('table_header', cell)}\\cell"`.
- Grant table Row 1 code emit was de-bolded already — confirm it routes
  through `code_in_table` (currently no-op style).

### Phase 5 — lint enforcement

- Add `tools/lint_styles.py`.
- Add a pytest test `tests/test_styles_lint.py` that runs the lint and
  fails on findings.
- Initial run gates against any remaining raw control codes outside
  the allowlist; document the exceptions in the lint's exception list.

## Risk + rollback

**Risk: visual regression** — the refactor changes WHERE the control
codes come from but not what they spell. After each phase, the
expected diff vs main is empty for the migrated paths. If a phase emits
a non-empty diff, the byte-identity invariant is broken and we revert.

**Risk: spacing drift** — the `SPACING` dict captures `(sb, sa)` per
style. The current emitters apply spacing inconsistently — some via
`\sa120` inline, some via paragraph-level `\sa{N}`. The migration
canonicalizes to the registry; any callsite that was emitting different
spacing for the "same" style is a bug worth surfacing during migration.

**Rollback:** each phase is one commit. Revert the commit; the
underlying `_emit_*` helpers are deleted by the migration so the
revert restores them from history.

## Open questions

1. **Indent — registry or per-call?** Sub-section headings indent by
   `(level - 1) * _HEADING_INDENT_PER_LEVEL`. Per-style indent doesn't
   capture this. Today the indent stays per-call (passed by caller);
   `emit_styled` accepts it as a keyword arg. The level-scaled
   computation lives in the caller (`_emit_section_heading` computes
   it from the code). Acceptable as long as the caller is the only
   thing that owns "what level am I."

2. **Per-section override?** Some emitters want a one-off spacing
   bump for a specific call (e.g., career-phase divider sometimes
   needs extra `\sa` for separation before a tight section). Today
   `emit_styled` accepts only the registered SPACING. If overrides
   become common, add an `extra_spacing=(sb, sa)` kwarg; until then
   resist.

3. **Style aliases?** `roman_section` and `group_heading` both render
   `\b\fs32`. They might diverge (Roman gets Title-Case rendering;
   group stays CAPS) — keep them separate even though their open
   sequence is identical, so a future style change to one doesn't
   accidentally drag the other.

## Definition of done

- All paragraph-level emit in `rtf.py` routes through `emit_styled` OR
  the lint allowlist.
- All inline emphasis routes through `styled_inline` OR the lint
  allowlist.
- `tools/lint_styles.py` runs as a pytest test with zero findings.
- Documentation: `styles.py` module docstring + this file are kept
  current as the registry grows.
- README's "How to add a new section heading" guidance (if any) points
  at the registry rather than a free-form RTF tutorial.

## Estimate

Phase 1: 30 minutes. Phase 2: 60 minutes. Phase 3: 45 minutes.
Phase 4: 30 minutes. Phase 5: 30 minutes. Total: ~3 hours of careful
work, spread across as many sessions as the candidate wants.
