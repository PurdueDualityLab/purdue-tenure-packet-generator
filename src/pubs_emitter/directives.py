"""Directive registry for the markdown-master walker.

Each registered entry maps an uppercase token (e.g., `PI_GRANT_TABLE`,
`PATENTS_TABLE`) to a renderer that emits one atomic RTF chunk for the
walker. The walker calls `DIRECTIVES[name](ctx, out)` when it
encounters `!NAME!` on its own line in the walker outline file.

Two registration paths coexist by design:

  1. **Declarative tabular directives.** Defined in
     `assets/table-schemas.yaml`. Each top-level entry becomes a
     directive whose name matches the schema key. The factory
     `_make_tabular_directive` builds the renderer from the schema —
     no Python edit required for simple-table sections (column-
     mapping + headers + widths + per-column escape policy).
     Brought forward into Phase 2 from Q6 of the design doc.

  2. **Bespoke Python directives.** Defined as functions in this
     module. Used for tables that need computed cells (grant totals,
     tier subheadings, condition-driven rows) or non-table emit
     chunks (intro counters, special list shapes). The schema-driven
     path is INTENTIONALLY narrow — bespoke logic stays in Python.

Per-migration recipe lives at the top of
`docs/design/markdown-master-outline-refactor.md` §"Phase 3".

Failure mode (Q5 of the design doc): an unknown directive aborts the
build via `sys.exit(1)` from the walker. A registered directive that
raises propagates — silent fallback would mask real bugs.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import IO, Callable

from .section_walker import RenderContext
from .types import Section


log = logging.getLogger(__name__)


def _directive_hello(ctx: RenderContext, out: IO[str]) -> None:
    """No-op directive used to exercise the walker's dispatch path.

    Emits a single RTF comment-style paragraph that's visible in the
    diff but trivially distinguishable from real content. Kept around
    after Phase 1 as a smoke-test handle for `--use-markdown-master`
    end-to-end runs (e.g., add `!HELLO!` to the outline once,
    confirm it appears in the rendered RTF, remove).
    """
    out.write("\\pard !HELLO! directive fired\\par\n")


def _directive_entrepreneurial_activities(
    ctx: RenderContext, out: IO[str],
) -> None:
    """C.20: dispatch the legacy renderer's body emit."""
    from .rtf import render_entrepreneurial_activities_section
    render_entrepreneurial_activities_section(
        ctx.entrepreneurial_activities or [], out, suppress_heading=True,
    )


def _directive_technology_transfer(ctx: RenderContext, out: IO[str]) -> None:
    """C.21: dispatch the legacy renderer's body emit."""
    from .rtf import render_technology_transfer_section
    render_technology_transfer_section(
        ctx.technology_transfer or [], ctx.paper_index, out,
        suppress_heading=True,
    )


def _directive_software_products(ctx: RenderContext, out: IO[str]) -> None:
    """C.22: dispatch the legacy renderer's body emit."""
    from .rtf import render_software_products_section
    render_software_products_section(
        ctx.software_products or [], out, suppress_heading=True,
    )


def _directive_courses_taught(ctx: RenderContext, out: IO[str]) -> None:
    """C.17: dispatch the legacy renderer's body emit."""
    from .rtf import render_courses_taught_section
    render_courses_taught_section(
        ctx.courses_taught or [], out, suppress_heading=True,
    )


def _directive_course_development(ctx: RenderContext, out: IO[str]) -> None:
    """C.18: dispatch the legacy renderer's body emit."""
    from .rtf import render_course_development_section
    render_course_development_section(
        ctx.course_development or [], out, suppress_heading=True,
    )


def _directive_invited_talks(ctx: RenderContext, out: IO[str]) -> None:
    """C.6: dispatch the legacy renderer's body emit."""
    from .rtf import render_invited_talks_section
    render_invited_talks_section(
        ctx.invited_talks or [], out, suppress_heading=True,
    )


def _directive_leadership_roles(ctx: RenderContext, out: IO[str]) -> None:
    """C.7: dispatch the legacy renderer's body emit."""
    from .rtf import render_leadership_section
    render_leadership_section(
        ctx.leadership_roles or [], out, suppress_heading=True,
    )


def _directive_media_appearances(ctx: RenderContext, out: IO[str]) -> None:
    """C.8: dispatch the legacy renderer's body emit."""
    from .rtf import render_media_appearances_section
    render_media_appearances_section(
        ctx.media_appearances or [], out, suppress_heading=True,
    )


def _directive_conference_presentations(
    ctx: RenderContext, out: IO[str],
) -> None:
    """C.9: dispatch the legacy renderer's body emit."""
    from .rtf import render_conference_presentations_section
    render_conference_presentations_section(
        ctx.conference_presentations or [],
        ctx.extra.get("bib_entries", []),
        ctx.paper_index, out, suppress_heading=True,
    )


def _directive_grants_pi(ctx: RenderContext, out: IO[str]) -> None:
    from .rtf import render_grants_section
    render_grants_section(
        "Grants PI", ctx.grants_as_pi or [], out, suppress_heading=True,
    )


def _directive_grants_copi(ctx: RenderContext, out: IO[str]) -> None:
    from .rtf import render_grants_section
    render_grants_section(
        "Grants Co-PI", ctx.grants_as_co_pi or [], out, suppress_heading=True,
    )


def _directive_gifts(ctx: RenderContext, out: IO[str]) -> None:
    from .rtf import render_grants_section
    render_grants_section(
        "Gifts", ctx.gifts or [], out, suppress_heading=True,
    )


def _directive_internal_grants(ctx: RenderContext, out: IO[str]) -> None:
    from .rtf import render_grants_section
    render_grants_section(
        "Internal Grants", ctx.internal_grants or [], out, suppress_heading=True,
    )


def _directive_university_service(ctx: RenderContext, out: IO[str]) -> None:
    from .rtf import render_service_section
    render_service_section(
        "University Service", ctx.university_service or [], out,
        suppress_heading=True,
    )


def _directive_profession_service(ctx: RenderContext, out: IO[str]) -> None:
    from .rtf import render_service_section
    render_service_section(
        "Profession Service", ctx.profession_service or [], out,
        suppress_heading=True,
    )


def _directive_national_service(ctx: RenderContext, out: IO[str]) -> None:
    from .rtf import render_service_section
    render_service_section(
        "National Service", ctx.national_service or [], out,
        suppress_heading=True,
    )


def _directive_other_service(ctx: RenderContext, out: IO[str]) -> None:
    from .rtf import render_service_section
    render_service_section(
        "Other Service", ctx.other_service or [], out,
        suppress_heading=True,
    )


def _directive_graduate_students(ctx: RenderContext, out: IO[str]) -> None:
    """C.14: dispatch the legacy renderer's body emit."""
    from .rtf import render_students_section
    render_students_section(
        "Graduate Students", ctx.graduate_students or [],
        ctx.extra.get("bib_entries", []), ctx.paper_index, out,
        under_review=ctx.under_review,
        under_review_index=ctx.extra.get("under_review_index"),
        suppress_heading=True,
    )


def _directive_candidate_info(ctx: RenderContext, out: IO[str]) -> None:
    """Section III (A.1-A.7) — bundled directive. Skipped when no
    candidate_info YAML loaded. The III + A. + A.X heading sequence is
    template-mandated; the directive emits it as one atomic unit so the
    `suppress_page_break` + `restart_numbering` flags stay encapsulated.
    """
    if ctx.candidate_info is None:
        return
    from .rtf import render_candidate_information_section
    render_candidate_information_section(ctx.candidate_info, out)


def _directive_self_evaluation(ctx: RenderContext, out: IO[str]) -> None:
    """Section IV (B.1-B.5) — bundled directive. Skipped when no B.X
    entries exist in section-prose. The B. group heading + each B.X
    sub-heading emit together; prose auto-fires from the prose dict
    via `_emit_section_heading`."""
    from .rtf import _section_prose, render_self_evaluation_section
    if not any(c in _section_prose for c in ("B.1", "B.2", "B.3", "B.4", "B.5")):
        return
    render_self_evaluation_section(out)


def _directive_v_appendix(ctx: RenderContext, out: IO[str]) -> None:
    """Section V — bundled appendix directive.

    Emits the Roman-numeral V. heading IF either A.1 (under-review) or
    A.2 (pending proposals) will fire, then emits each sub-section in
    order. Matches the legacy conditional behavior so an empty appendix
    silently emits nothing (no orphan Roman heading)."""
    from .rtf import (
        _emit_roman_section_heading,
        render_pending_proposals_section,
        render_under_review_section,
    )
    under_review = ctx.under_review or []
    pending = ctx.pending_proposals or []
    if not under_review and not pending:
        return
    _emit_roman_section_heading(
        out, "V", "Supporting Documentation for Pending Publications.",
    )
    if under_review:
        render_under_review_section(under_review, out)
    if pending:
        render_pending_proposals_section(pending, out)


def _directive_key_works(ctx: RenderContext, out: IO[str]) -> None:
    """C.1: dispatch the legacy renderer's body emit."""
    from .rtf import render_key_works_section
    render_key_works_section(
        ctx.extra.get("key_works", []), ctx.paper_index, out,
        suppress_heading=True,
    )


def _directive_other_publications(ctx: RenderContext, out: IO[str]) -> None:
    """C.5: dispatch the legacy renderer's body emit."""
    from .rtf import render_other_pubs_section
    publications = ctx.publications or {}
    citations = publications.get("Other publications and products", [])
    render_other_pubs_section(
        citations, ctx.paper_index, ctx.extra.get("key_work_index", {}), out,
        suppress_heading=True,
    )


def _emit_generic_publications_body(
    section_name: Section, ctx: RenderContext, out: IO[str],
) -> None:
    """Helper: the generic-loop body for C.2 / C.3 / C.4 — heading is
    emitted by the walker via the outline markdown line; this just
    emits the citation list at the section's hanging indent."""
    from .config import SECTION_CODES
    from .rtf import (
        _emit_list_item, _hanging_indent_for_codes, _maybe_emit_career_phase_divider,
        _section_codes_up_to, render_citation,
    )
    publications = ctx.publications or {}
    citations = publications.get(section_name, [])
    if not citations:
        return
    code = SECTION_CODES[section_name]
    expansion_done: set[str] = set()
    indent = _hanging_indent_for_codes(
        _section_codes_up_to(code, len(citations))
    )
    phase = ""
    for idx, cit in enumerate(citations, 1):
        phase = _maybe_emit_career_phase_divider(
            out, cit.year, phase, indent,
        )
        body = render_citation(
            cit, expansion_done, ctx.paper_index,
            ctx.extra.get("key_work_index", {}),
        )
        _emit_list_item(out, f"{code}.{idx}", body, indent=indent)


def _directive_journals(ctx: RenderContext, out: IO[str]) -> None:
    """C.2: generic-loop body."""
    _emit_generic_publications_body("Journals", ctx, out)


def _directive_books_and_chapters(ctx: RenderContext, out: IO[str]) -> None:
    """C.3: generic-loop body."""
    _emit_generic_publications_body("Books and Chapters", ctx, out)


def _directive_conferences_and_workshops(
    ctx: RenderContext, out: IO[str],
) -> None:
    """C.4: generic-loop body."""
    _emit_generic_publications_body("Conferences and Workshops", ctx, out)


def _directive_undergrad_students_table(
    ctx: RenderContext, out: IO[str],
) -> None:
    """C.16 body: students table for undergraduates."""
    from .rtf import render_students_section
    render_students_section(
        "Undergraduate Students", ctx.undergraduate_students or [],
        ctx.extra.get("bib_entries", []), ctx.paper_index, out,
        under_review=ctx.under_review,
        under_review_index=ctx.extra.get("under_review_index"),
        suppress_heading=True,
    )


def _directive_undergrad_pathways(ctx: RenderContext, out: IO[str]) -> None:
    """C.16.2.2 body."""
    from .rtf import render_undergrad_pathways_section
    render_undergrad_pathways_section(
        ctx.undergrad_pathways or [], out, suppress_heading=True,
    )


def _directive_undergrad_products(ctx: RenderContext, out: IO[str]) -> None:
    """C.16.2.3 body."""
    from .rtf import render_undergrad_products_section
    render_undergrad_products_section(
        ctx.undergrad_products or [], out, suppress_heading=True,
    )


def _directive_undergrad_awards(ctx: RenderContext, out: IO[str]) -> None:
    """C.16.2.4 body."""
    from .rtf import render_student_awards_section
    render_student_awards_section(
        "Undergraduate Student Awards", ctx.student_awards or [], out,
        suppress_heading=True,
    )


def _directive_graduate_awards(ctx: RenderContext, out: IO[str]) -> None:
    """C.16.3.2 body."""
    from .rtf import render_student_awards_section
    render_student_awards_section(
        "Graduate Student Awards", ctx.student_awards or [], out,
        suppress_heading=True,
    )


def _directive_postdocs(ctx: RenderContext, out: IO[str]) -> None:
    """C.15: dispatch the legacy renderer's body emit. The legacy
    renderer needs bib_entries + under_review back-refs; these come
    from `ctx.extra` since they aren't currently top-level on
    RenderContext. (Phase 4 may promote them.)"""
    from .rtf import render_postdocs_section
    render_postdocs_section(
        ctx.postdocs_visiting or [],
        ctx.extra.get("bib_entries", []),
        ctx.paper_index,
        out,
        under_review=ctx.under_review,
        under_review_index=ctx.extra.get("under_review_index"),
        suppress_heading=True,
    )


# ---------------------------------------------------------------------------
# Tabular-directive factory (declarative-schema path; Q6 brought into Phase 2)
# ---------------------------------------------------------------------------


def _apply_escape_policy(value: object, policy: str) -> str:
    """Convert a row attribute value to an RTF-emittable cell string.

    Two policies today:
      * `escape_rtf` — the default; runs `builders.escape_rtf` over the
        stringified value. Right choice for any field carrying
        author-typed text.
      * `raw` — passes the value through unmodified. Use ONLY for
        fields that arrive pre-formatted with RTF control words
        already in place (e.g., `Patent.co_inventors` carries `\\b`
        bold-for-me markers that escape would clobber).

    Function-local import: keeps `directives` ↔ `builders` cycle off
    the module-load critical path.
    """
    from .builders import escape_rtf
    text = str(value)
    if policy == "escape_rtf":
        return escape_rtf(text)
    if policy == "raw":
        return text
    raise ValueError(f"unknown escape policy: {policy!r}")


def _make_tabular_directive(name: str, schema: dict) -> Callable[[RenderContext, IO[str]], None]:
    """Build a directive renderer from a validated tabular schema.

    Returns a `(ctx, out) → None` callable that:
      1. Reads `ctx.<source>` (a list of row objects).
      2. If empty, emits nothing (matches `render_patents_section`'s
         skip-when-empty behavior).
      3. Builds an `RtfTable` with column widths + headers from the
         schema.
      4. For each row, projects schema fields onto cells with the
         schema's per-column escape policy.
      5. If `bookmark_column` is set, prefixes that column's cell with
         the bookmarked entry-code anchor (`{bold C.X.N.} value`) —
         the canonical "this is row N, click me" shape used by the
         existing patents + grant renderers.
      6. Emits the table + trailing `\\pard\\par`.

    Each step is the same shape regardless of which schema is being
    rendered — that's the load-bearing affordance of the declarative
    approach.
    """
    source_field = schema["source"]
    code_key = schema["code_key"]
    columns: list[dict] = schema["columns"]
    bookmark_column = schema.get("bookmark_column")

    def directive(ctx: RenderContext, out: IO[str]) -> None:
        # Function-local imports: dial in the cycle-free order on EVERY
        # call so module-load doesn't depend on import ordering.
        from .config import SECTION_CODES
        from .rtf import RtfTable, _ref_anchor
        from .styles import styled_inline
        rows = getattr(ctx, source_field, None) or []
        if not rows:
            return
        code_base = SECTION_CODES[code_key]
        widths = [c["width"] for c in columns]
        headers = [c["header"] for c in columns]
        table = RtfTable(column_widths=widths)
        table.add_header(headers)
        for idx, row in enumerate(rows, 1):
            entry_code = f"{code_base}.{idx}"
            cells: list[str] = []
            for col_idx, col in enumerate(columns):
                raw_value = getattr(row, col["field"])
                escape_policy = col.get("escape", "escape_rtf")
                rendered = _apply_escape_policy(raw_value, escape_policy)
                if col_idx == bookmark_column:
                    # Bookmarked entry-code prefix: bold-wrapped anchor +
                    # period + space + the cell value. Brace-scoping
                    # keeps the trailing space literal (the
                    # delimiter-eats-space trap from CLAUDE.md).
                    rendered = (
                        f"{{{styled_inline('entry_name', _ref_anchor(entry_code))}.}} "
                        f"{rendered}"
                    )
                cells.append(rendered)
            table.add_row(cells)
        out.write(table.render())
        out.write("\\pard\\par\n")

    # Decorate the closure for log messages / introspection.
    directive.__name__ = f"_directive_{name.lower()}"
    directive.__doc__ = (
        f"Auto-generated tabular directive for {name} (source={source_field}, "
        f"code_key={code_key}, {len(columns)} columns)."
    )
    return directive


# ---------------------------------------------------------------------------
# Registry assembly
# ---------------------------------------------------------------------------


# Public registry. Keys are the bare token form (no `!` wrap).
# Initialized with the bespoke Python directives; the tabular-schema
# loader extends it at import time below.
DIRECTIVES: dict[str, Callable[[RenderContext, IO[str]], None]] = {
    "HELLO": _directive_hello,
    "CANDIDATE_INFO": _directive_candidate_info,
    "SELF_EVALUATION": _directive_self_evaluation,
    "V_APPENDIX": _directive_v_appendix,
    "KEY_WORKS": _directive_key_works,
    "JOURNALS": _directive_journals,
    "BOOKS_AND_CHAPTERS": _directive_books_and_chapters,
    "CONFERENCES_AND_WORKSHOPS": _directive_conferences_and_workshops,
    "OTHER_PUBLICATIONS": _directive_other_publications,
    "INVITED_TALKS": _directive_invited_talks,
    "LEADERSHIP_ROLES": _directive_leadership_roles,
    "MEDIA_APPEARANCES": _directive_media_appearances,
    "CONFERENCE_PRESENTATIONS": _directive_conference_presentations,
    "GRANTS_PI": _directive_grants_pi,
    "GRANTS_COPI": _directive_grants_copi,
    "GIFTS": _directive_gifts,
    "INTERNAL_GRANTS": _directive_internal_grants,
    "UNIVERSITY_SERVICE": _directive_university_service,
    "PROFESSION_SERVICE": _directive_profession_service,
    "NATIONAL_SERVICE": _directive_national_service,
    "OTHER_SERVICE": _directive_other_service,
    "GRADUATE_STUDENTS": _directive_graduate_students,
    "UNDERGRAD_STUDENTS_TABLE": _directive_undergrad_students_table,
    "UNDERGRAD_PATHWAYS": _directive_undergrad_pathways,
    "UNDERGRAD_PRODUCTS": _directive_undergrad_products,
    "UNDERGRAD_AWARDS": _directive_undergrad_awards,
    "GRADUATE_AWARDS": _directive_graduate_awards,
    "POSTDOCS_VISITING": _directive_postdocs,
    "COURSES_TAUGHT": _directive_courses_taught,
    "COURSE_DEVELOPMENT": _directive_course_development,
    "ENTREPRENEURIAL_ACTIVITIES": _directive_entrepreneurial_activities,
    "TECHNOLOGY_TRANSFER": _directive_technology_transfer,
    "SOFTWARE_PRODUCTS": _directive_software_products,
}


# Project-root-relative default path for the tabular schemas file.
# The CLI can override via `--table-schemas`. Set lazily at import
# time so test code can stage a fixture path via the env var (see
# `_DEFAULT_TABLE_SCHEMAS_PATH` resolution below).
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_DEFAULT_TABLE_SCHEMAS_PATH = str(_REPO_ROOT / "assets" / "table-schemas.yaml")


def register_tabular_directives(schemas_path: str | None = None) -> None:
    """Load `table-schemas.yaml` and register every entry as a directive.

    Idempotent — re-running replaces any existing tabular entries
    with fresh closures from the latest schema. Call sites:

      * Module-load (below) — registers the production schema by
        default so the CLI's default `--use-markdown-master` flow
        finds `!PATENTS_TABLE!` already in the registry.
      * Tests — can rebuild against a fixture schema by passing a
        path; the production registry is mutated in place so test
        teardown should restore it.

    Missing schema file is permissive (logged + skipped) — the walker
    will fail-loud on an unknown directive name at emit time.
    """
    # Function-local import: builders.py imports happen later in the
    # module load graph than directives.py' first-use path; keep the
    # import on the call edge to avoid load-order cycles.
    from .builders import load_table_schemas
    path = schemas_path or os.environ.get(
        "PUBS_EMITTER_TABLE_SCHEMAS", _DEFAULT_TABLE_SCHEMAS_PATH,
    )
    schemas = load_table_schemas(path)
    for name, schema in schemas.items():
        DIRECTIVES[name] = _make_tabular_directive(name, schema)
    log.debug(
        "directives: registered %d tabular directive(s) from %s",
        len(schemas), path,
    )


# Load + register at module import time. Tests that want to swap the
# schema file out can either set `PUBS_EMITTER_TABLE_SCHEMAS` before
# import, or call `register_tabular_directives(<path>)` after import
# to overwrite the entries.
register_tabular_directives()


# ----- IR-shaped directives (Phase 4+ of the IR refactor) ---------------
#
# Each migrated directive ships as a `(ctx) -> list[Block]` pure
# function (in `pubs_emitter.renderers.*`) plus a legacy-API adapter
# `(ctx, out) -> None` that drives the blocks through `RtfWriter`. The
# adapter overwrites the auto-tabular entry in `DIRECTIVES` so the
# walker dispatches the IR path; byte-identity is preserved because
# the writer produces the same RTF.


def _directive_patents_table_ir(ctx: RenderContext, out: IO[str]) -> None:
    """C.19 Patents — IR adapter. Builds `list[Block]` via
    `renderers.patents.render_patents_section_blocks`, then renders
    via `RtfWriter`. Byte-identical to the legacy tabular-schema
    auto-generated directive."""
    from .renderers.patents import render_patents_section_blocks
    from .writer_rtf import RtfWriter
    patents = ctx.patents or []
    blocks = render_patents_section_blocks(patents)
    out.write(RtfWriter().render(blocks))


def _directive_invited_talks_ir(ctx: RenderContext, out: IO[str]) -> None:
    """C.6 Invited Talks — IR adapter."""
    from .renderers.simple_lists import render_invited_talks_blocks
    from .writer_rtf import RtfWriter
    blocks = render_invited_talks_blocks(ctx.invited_talks or [])
    out.write(RtfWriter().render(blocks))


def _directive_leadership_roles_ir(ctx: RenderContext, out: IO[str]) -> None:
    """C.7 Leadership Roles — IR adapter."""
    from .renderers.simple_lists import render_leadership_roles_blocks
    from .writer_rtf import RtfWriter
    blocks = render_leadership_roles_blocks(ctx.leadership_roles or [])
    out.write(RtfWriter().render(blocks))


def _directive_media_appearances_ir(ctx: RenderContext, out: IO[str]) -> None:
    """C.8 Media Appearances — IR adapter."""
    from .renderers.simple_lists import render_media_appearances_blocks
    from .writer_rtf import RtfWriter
    blocks = render_media_appearances_blocks(ctx.media_appearances or [])
    out.write(RtfWriter().render(blocks))


def _directive_conference_presentations_ir(
    ctx: RenderContext, out: IO[str],
) -> None:
    """C.9 Conference Presentations — IR adapter. Pulls bib_entries +
    paper_index out of the ctx.extra bag for cross-ref resolution."""
    from .renderers.simple_lists import render_conference_presentations_blocks
    from .writer_rtf import RtfWriter
    bib_entries = ctx.extra.get("bib_entries", []) or []
    paper_index = ctx.paper_index or {}
    blocks = render_conference_presentations_blocks(
        ctx.conference_presentations or [], bib_entries, paper_index,
    )
    out.write(RtfWriter().render(blocks))


# Replace the auto-generated tabular entry for PATENTS_TABLE with the
# IR-based adapter; replace the bespoke list directives with their IR
# variants. The walker dispatch is unchanged; byte-identity holds
# because the writer emits the same RTF as the legacy emitters.
DIRECTIVES["PATENTS_TABLE"] = _directive_patents_table_ir
DIRECTIVES["INVITED_TALKS"] = _directive_invited_talks_ir
DIRECTIVES["LEADERSHIP_ROLES"] = _directive_leadership_roles_ir
DIRECTIVES["MEDIA_APPEARANCES"] = _directive_media_appearances_ir
DIRECTIVES["CONFERENCE_PRESENTATIONS"] = _directive_conference_presentations_ir
