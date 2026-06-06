"""Phase-1 tests for the markdown-master walker.

Covers the 12-test checklist in
`docs/design/markdown-master-outline-refactor.md` §"Tests (Phase 1)":

  parser:
    1. test_parse_headings_by_depth
    2. test_parse_extracts_code_and_title
    3. test_parse_directive_isolated_line
    4. test_parse_directive_inline_is_literal
    5. test_parse_paragraphs_split_on_blank_lines
    6. test_parse_strips_c_style_comments

  walker:
    7. test_walker_emits_bookmark_per_heading
    8. test_walker_dispatches_directive
    9. test_walker_missing_directive_exits
    10. test_walker_resolves_at_refs_in_paragraphs
    11. test_walker_substitutes_macros_in_paragraphs
    12. test_walker_collects_declared_codes_into_ref_index
"""
from __future__ import annotations

import io
import logging
from typing import IO

import pytest

from pubs_emitter import directives as directives_mod
from pubs_emitter.section_walker import (
    DirectiveNode,
    HeadingNode,
    ParagraphNode,
    RenderContext,
    parse_section_prose,
    walk_section_prose,
)


# ---------------------------------------------------------------------------
# Parser tests
# ---------------------------------------------------------------------------


def test_parse_headings_by_depth() -> None:
    """`#`/`##`/`###`/`####` map to depths 1/2/3/4 respectively."""
    text = (
        "# III. MATERIAL FOR EVALUATION\n"
        "\n"
        "## A. GENERAL INFORMATION\n"
        "\n"
        "### A.1 Name and identifiers\n"
        "\n"
        "#### A.1.1 Subentry\n"
    )
    nodes = parse_section_prose(text)
    headings = [n for n in nodes if isinstance(n, HeadingNode)]
    assert [h.depth for h in headings] == [1, 2, 3, 4]


def test_parse_extracts_code_and_title() -> None:
    """`## A.1 Name and identifiers` → code='A.1', title='Name and identifiers'.
    Roman + letter + dotted codes all extract; trailing-period on code is
    stripped."""
    text = (
        "# III. MATERIAL\n"
        "\n"
        "## A. GENERAL INFO\n"
        "\n"
        "### A.1 Name and identifiers\n"
        "\n"
        "#### C.16.2.3 Products\n"
    )
    nodes = parse_section_prose(text)
    headings = [n for n in nodes if isinstance(n, HeadingNode)]
    assert (headings[0].code, headings[0].title) == ("III", "MATERIAL")
    assert (headings[1].code, headings[1].title) == ("A", "GENERAL INFO")
    assert (headings[2].code, headings[2].title) == ("A.1", "Name and identifiers")
    assert (headings[3].code, headings[3].title) == ("C.16.2.3", "Products")


def test_parse_directive_isolated_line() -> None:
    """`!FOO!` on its own line → DirectiveNode."""
    text = (
        "### C.19 Patents\n"
        "\n"
        "!PATENTS_TABLE!\n"
    )
    nodes = parse_section_prose(text)
    directives = [n for n in nodes if isinstance(n, DirectiveNode)]
    assert len(directives) == 1
    assert directives[0].name == "PATENTS_TABLE"


def test_parse_directive_inline_is_literal() -> None:
    """`text !FOO! more text` is literal prose, not a directive."""
    text = (
        "### A.1 Identifiers\n"
        "\n"
        "Hello !INLINE! more text.\n"
    )
    nodes = parse_section_prose(text)
    directives = [n for n in nodes if isinstance(n, DirectiveNode)]
    paragraphs = [n for n in nodes if isinstance(n, ParagraphNode)]
    assert directives == []
    assert len(paragraphs) == 1
    assert "!INLINE!" in paragraphs[0].text


def test_parse_paragraphs_split_on_blank_lines() -> None:
    """Blank-line-separated text becomes separate ParagraphNodes; internal
    soft-wraps within a paragraph collapse to spaces."""
    text = (
        "### A.1 Test\n"
        "\n"
        "First paragraph here.\n"
        "Second line of first paragraph.\n"
        "\n"
        "Second paragraph here.\n"
        "\n"
        "Third paragraph.\n"
    )
    nodes = parse_section_prose(text)
    paragraphs = [n for n in nodes if isinstance(n, ParagraphNode)]
    assert len(paragraphs) == 3
    assert paragraphs[0].text == "First paragraph here. Second line of first paragraph."
    assert paragraphs[1].text == "Second paragraph here."
    assert paragraphs[2].text == "Third paragraph."


def test_parse_strips_c_style_comments() -> None:
    """`/* ... */` comment blocks are removed before parsing — the
    rendered packet must not include editor-only authoring guidance."""
    text = (
        "### A.1 Test\n"
        "\n"
        "/* TEMPLATE PROMPT: edit me, do not ship. */\n"
        "\n"
        "Real prose that should appear.\n"
        "\n"
        "/* Another reminder block\n"
        "   spanning multiple lines. */\n"
        "\n"
        "More real prose.\n"
    )
    nodes = parse_section_prose(text)
    paragraphs = [n for n in nodes if isinstance(n, ParagraphNode)]
    bodies = [p.text for p in paragraphs]
    assert any("Real prose" in b for b in bodies)
    assert any("More real prose" in b for b in bodies)
    # Neither comment body string survives anywhere.
    joined = "\n".join(bodies)
    assert "TEMPLATE PROMPT" not in joined
    assert "Another reminder" not in joined


# ---------------------------------------------------------------------------
# Walker tests
# ---------------------------------------------------------------------------


def _walk(text: str, ctx: RenderContext | None = None) -> tuple[str, RenderContext]:
    """Helper — run the walker against `text`, return (rtf_output, ctx)."""
    ctx = ctx or RenderContext()
    buf = io.StringIO()
    walk_section_prose(text, ctx, buf)
    return buf.getvalue(), ctx


def test_walker_emits_bookmark_per_heading() -> None:
    """Every heading the walker emits is wrapped in a `\\bkmkstart`
    bookmark whose name matches the code with `.` → `_`."""
    text = "### C.16.2 Subentry\n"
    rtf, _ = _walk(text)
    # `_emit_section_heading` for level-2 (dot-count 2 → level 2) emits
    # `_ref_anchor(code)` which uses `\\bkmkstart C_16_2`. Check both
    # the bookmark name and that it appears in the output.
    assert r"\bkmkstart C_16_2" in rtf


def test_walker_dispatches_directive() -> None:
    """A registered `!NAME!` directive's renderer is invoked."""
    seen: list[str] = []

    def _probe(ctx: RenderContext, out: IO[str]) -> None:
        seen.append("fired")
        out.write("\\pard probe\\par\n")

    directives_mod.DIRECTIVES["PROBE_PHASE1"] = _probe
    try:
        rtf, _ = _walk("!PROBE_PHASE1!\n")
        assert seen == ["fired"]
        assert "probe" in rtf
    finally:
        directives_mod.DIRECTIVES.pop("PROBE_PHASE1", None)


def test_walker_missing_directive_exits() -> None:
    """An unknown `!NAME!` is build-fatal (Q5 of the design doc)."""
    with pytest.raises(SystemExit) as exc:
        _walk("!NOT_REGISTERED_ANYWHERE!\n")
    assert exc.value.code == 1


def test_walker_resolves_at_refs_in_paragraphs() -> None:
    """`@bibkey` / `@id` / `@C.X.Y` in paragraph prose is resolved
    against `ctx.ref_index`. Resolved refs emit sentinel-wrapped codes
    that the final pass converts to RTF hyperlinks; verify the sentinel
    appears.

    Sentinel chars per builders.REF_LINK_OPEN / REF_LINK_CLOSE: 0x01 /
    0x02.
    """
    from pubs_emitter.builders import REF_LINK_CLOSE, REF_LINK_OPEN
    ctx = RenderContext(ref_index={"davis2018impact": "C.2.5"})
    text = (
        "### A.1 Test\n"
        "\n"
        "Reference to @davis2018impact appears here.\n"
    )
    rtf, _ = _walk(text, ctx)
    # The resolved code appears wrapped in the link sentinels.
    assert f"{REF_LINK_OPEN}C.2.5{REF_LINK_CLOSE}" in rtf


def test_walker_substitutes_macros_in_paragraphs() -> None:
    """`#MACRO_NAME` in paragraph prose substitutes against `ctx.macros`."""
    ctx = RenderContext(macros={"NUM_PAPERS": "42"})
    text = (
        "### B.1 Self-evaluation\n"
        "\n"
        "I have published #NUM_PAPERS papers.\n"
    )
    rtf, _ = _walk(text, ctx)
    assert "42" in rtf
    # `#NUM_PAPERS` did not survive unsubstituted.
    assert "#NUM_PAPERS" not in rtf


def test_walker_collects_declared_codes_into_ref_index() -> None:
    """Every heading code the walker sees is registered as a
    self-resolving entry in `ctx.ref_index` so `@C.X.Y` cross-refs
    target markdown-declared sections even when no YAML entry exists.

    Pre-existing entries (e.g., a YAML-derived `C.16.2 → C.16.2.5`)
    are NOT overwritten — `setdefault` semantics.
    """
    from pubs_emitter.builders import REF_LINK_CLOSE, REF_LINK_OPEN
    ctx = RenderContext()
    text = (
        "### C.16.2 Outline section\n"
        "\n"
        "See @C.16.2.1 for the full discussion.\n"
        "\n"
        "#### C.16.2.1 Subsection\n"
        "\n"
        "Body.\n"
    )
    rtf, ctx_out = _walk(text, ctx)
    # Both declared codes are in ref_index after the walk.
    assert "C.16.2" in ctx_out.ref_index
    assert "C.16.2.1" in ctx_out.ref_index
    # Self-resolving — declared code maps to itself.
    assert ctx_out.ref_index["C.16.2"] == "C.16.2"
    assert ctx_out.ref_index["C.16.2.1"] == "C.16.2.1"
    # The `@C.16.2.1` in the paragraph resolved via the raw-code path
    # to a sentinel-wrapped code regardless of ref_index population —
    # but the augmentation is what makes `@C.16.2.1` viable as an
    # `@id`-form ref when a YAML entry binds it. Smoke-check that the
    # paragraph emitted ANY sentinel pair.
    assert REF_LINK_OPEN in rtf and REF_LINK_CLOSE in rtf


# ---------------------------------------------------------------------------
# Extra Phase-1 smoke: end-to-end heading + paragraph + directive run
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Phase 2 — declarative table-schema loader (Q6 brought forward)
# ---------------------------------------------------------------------------


class TestLoadTableSchemas:
    """Tests for `builders.load_table_schemas` — the loader that reads
    `assets/table-schemas.yaml` into validated `{name: schema}` dicts.

    Validation is fail-loud at LOAD time so a misconfigured schema file
    surfaces as a build error, not a render-time surprise. Missing file
    is permissive (returns empty dict; directive-emit time will fail
    loud on the unknown-directive path).
    """

    def test_load_missing_path_returns_empty(self, tmp_path) -> None:
        from pubs_emitter.builders import load_table_schemas
        assert load_table_schemas(None) == {}
        assert load_table_schemas(str(tmp_path / "nope.yaml")) == {}

    def test_load_valid_schema_returns_normalized(self, tmp_path) -> None:
        from pubs_emitter.builders import load_table_schemas
        p = tmp_path / "schemas.yaml"
        p.write_text(
            "PATENTS_TABLE:\n"
            "  source: patents\n"
            "  code_key: Patents\n"
            "  bookmark_column: 0\n"
            "  columns:\n"
            "    - {field: title, header: Title, width: 2400, escape: escape_rtf}\n"
            "    - {field: number, header: Number, width: 1500, escape: escape_rtf}\n",
            encoding="utf-8",
        )
        result = load_table_schemas(str(p))
        assert "PATENTS_TABLE" in result
        entry = result["PATENTS_TABLE"]
        assert entry["source"] == "patents"
        assert entry["code_key"] == "Patents"
        assert entry["bookmark_column"] == 0
        assert len(entry["columns"]) == 2

    def test_load_rejects_non_mapping_root(self, tmp_path) -> None:
        from pubs_emitter.builders import load_table_schemas
        p = tmp_path / "bad.yaml"
        p.write_text("- not a mapping\n", encoding="utf-8")
        with pytest.raises(SystemExit) as exc:
            load_table_schemas(str(p))
        assert exc.value.code == 1

    def test_load_rejects_missing_required_column_keys(self, tmp_path) -> None:
        from pubs_emitter.builders import load_table_schemas
        p = tmp_path / "bad.yaml"
        p.write_text(
            "PATENTS_TABLE:\n"
            "  source: patents\n"
            "  code_key: Patents\n"
            "  columns:\n"
            "    - {field: title, header: Title}\n",  # missing 'width'
            encoding="utf-8",
        )
        with pytest.raises(SystemExit) as exc:
            load_table_schemas(str(p))
        assert exc.value.code == 1

    def test_load_rejects_unknown_escape_policy(self, tmp_path) -> None:
        from pubs_emitter.builders import load_table_schemas
        p = tmp_path / "bad.yaml"
        p.write_text(
            "PATENTS_TABLE:\n"
            "  source: patents\n"
            "  code_key: Patents\n"
            "  columns:\n"
            "    - {field: title, header: Title, width: 2400, escape: html_encode}\n",
            encoding="utf-8",
        )
        with pytest.raises(SystemExit) as exc:
            load_table_schemas(str(p))
        assert exc.value.code == 1

    def test_load_rejects_bookmark_column_out_of_range(self, tmp_path) -> None:
        from pubs_emitter.builders import load_table_schemas
        p = tmp_path / "bad.yaml"
        p.write_text(
            "PATENTS_TABLE:\n"
            "  source: patents\n"
            "  code_key: Patents\n"
            "  bookmark_column: 5\n"
            "  columns:\n"
            "    - {field: title, header: Title, width: 2400}\n",
            encoding="utf-8",
        )
        with pytest.raises(SystemExit) as exc:
            load_table_schemas(str(p))
        assert exc.value.code == 1

    def test_load_rejects_non_positive_width(self, tmp_path) -> None:
        from pubs_emitter.builders import load_table_schemas
        p = tmp_path / "bad.yaml"
        p.write_text(
            "PATENTS_TABLE:\n"
            "  source: patents\n"
            "  code_key: Patents\n"
            "  columns:\n"
            "    - {field: title, header: Title, width: 0}\n",
            encoding="utf-8",
        )
        with pytest.raises(SystemExit) as exc:
            load_table_schemas(str(p))
        assert exc.value.code == 1

    def test_load_rejects_unknown_top_level_key(self, tmp_path) -> None:
        from pubs_emitter.builders import load_table_schemas
        p = tmp_path / "bad.yaml"
        p.write_text(
            "PATENTS_TABLE:\n"
            "  source: patents\n"
            "  code_key: Patents\n"
            "  banana: yes\n"  # typo / unknown key
            "  columns:\n"
            "    - {field: title, header: Title, width: 2400}\n",
            encoding="utf-8",
        )
        with pytest.raises(SystemExit) as exc:
            load_table_schemas(str(p))
        assert exc.value.code == 1


# ---------------------------------------------------------------------------
# Phase 2 — tabular-directive factory + auto-registration
# ---------------------------------------------------------------------------


class TestTabularDirectiveFactory:
    """Tests for `directives._make_tabular_directive` — the schema-driven
    closure that emits an RtfTable for a registered `!NAME!` directive.

    The factory's job: read `ctx.<source>`, build an RtfTable from the
    column spec, project per-row attributes onto cells, optionally
    prepend a bookmarked entry-code anchor to the configured bookmark
    column, emit the rendered table.
    """

    def _patent_schema(self) -> dict:
        return {
            "source": "patents",
            "code_key": "Patents",
            "bookmark_column": 0,
            "columns": [
                {"field": "title", "header": "Title", "width": 2400, "escape": "escape_rtf"},
                {"field": "co_inventors", "header": "Co-Inventors", "width": 2000, "escape": "raw"},
                {"field": "date", "header": "Issue Date", "width": 1600, "escape": "escape_rtf"},
                {"field": "number", "header": "Number", "width": 1500, "escape": "escape_rtf"},
                {"field": "impact", "header": "Impact", "width": 1860, "escape": "escape_rtf"},
            ],
        }

    def _patent(self, **overrides) -> "Patent":  # type: ignore[name-defined]
        from pubs_emitter.types import Patent
        return Patent(
            year=2024, year_str="2024", title="Sample patent",
            co_inventors="\\b Davis, J.C.\\b0", date="2024-01-15",
            number="US12345678", impact="Cited in industry standard X.",
        )._replace(**overrides)

    def test_empty_source_emits_nothing(self) -> None:
        from pubs_emitter.directives import _make_tabular_directive
        d = _make_tabular_directive("PATENTS_TABLE", self._patent_schema())
        ctx = RenderContext(patents=[])
        buf = io.StringIO()
        d(ctx, buf)
        assert buf.getvalue() == ""

    def test_emits_header_and_row_with_bookmarked_entry_code(self) -> None:
        """First column gets `{bold C.19.N.} ${value}` prefix; other
        columns render verbatim through their escape policy."""
        from pubs_emitter.directives import _make_tabular_directive
        d = _make_tabular_directive("PATENTS_TABLE", self._patent_schema())
        ctx = RenderContext(patents=[self._patent(title="Wibble", number="US42")])
        buf = io.StringIO()
        d(ctx, buf)
        out = buf.getvalue()
        # Header row includes each column header.
        for h in ("Title", "Co-Inventors", "Issue Date", "Number", "Impact"):
            assert h in out
        # Bookmark anchor on the first column for row 1 ("C.19.1").
        assert "\\bkmkstart C_19_1" in out
        # Body data appears unescaped (no `\\`-mangling for simple ASCII).
        assert "Wibble" in out
        assert "US42" in out
        # Trailing pad after the table.
        assert "\\pard\\par" in out

    def test_raw_escape_passes_through_rtf_control_codes(self) -> None:
        """`escape: raw` (used for Patent.co_inventors) does NOT escape
        the value — the field carries pre-formatted `\\b ... \\b0` markup
        from `format_inventors` and escaping would clobber it."""
        from pubs_emitter.directives import _make_tabular_directive
        d = _make_tabular_directive("PATENTS_TABLE", self._patent_schema())
        ctx = RenderContext(patents=[
            self._patent(co_inventors=r"\b Davis, J.C.\b0, Other, T.S."),
        ])
        buf = io.StringIO()
        d(ctx, buf)
        out = buf.getvalue()
        # The literal `\b` control word survives un-doubled.
        assert r"\b Davis, J.C.\b0" in out

    def test_unknown_escape_policy_raises(self) -> None:
        """Schema validation catches this at load time; the factory's
        runtime dispatcher also raises if a schema slips through with a
        bad policy (defense in depth)."""
        from pubs_emitter.directives import _make_tabular_directive
        schema = self._patent_schema()
        schema["columns"][0]["escape"] = "html_encode"
        d = _make_tabular_directive("PATENTS_TABLE", schema)
        ctx = RenderContext(patents=[self._patent()])
        buf = io.StringIO()
        with pytest.raises(ValueError):
            d(ctx, buf)


class TestSchemaAutoRegistration:
    """Tests for `directives.register_tabular_directives` — the
    import-time hook that loads `assets/table-schemas.yaml` into the
    `DIRECTIVES` registry. Re-running with a different schema path
    swaps the registered closures.
    """

    def test_register_against_fixture_path_overrides_production(
        self, tmp_path,
    ) -> None:
        from pubs_emitter import directives as directives_mod
        # Save current registry so we can restore after the test.
        saved = dict(directives_mod.DIRECTIVES)
        try:
            p = tmp_path / "schemas.yaml"
            p.write_text(
                "PROBE_TABLE:\n"
                "  source: patents\n"
                "  code_key: Patents\n"
                "  columns:\n"
                "    - {field: title, header: Title, width: 2400}\n",
                encoding="utf-8",
            )
            directives_mod.register_tabular_directives(str(p))
            assert "PROBE_TABLE" in directives_mod.DIRECTIVES
        finally:
            directives_mod.DIRECTIVES.clear()
            directives_mod.DIRECTIVES.update(saved)

    def test_production_schema_registers_patents_table(self) -> None:
        """The on-disk `assets/table-schemas.yaml` (loaded at module
        import) wires `!PATENTS_TABLE!` into the registry."""
        from pubs_emitter import directives as directives_mod
        assert "PATENTS_TABLE" in directives_mod.DIRECTIVES


# ---------------------------------------------------------------------------
# Phase 2 — walker-vs-legacy parity for C.19 Patents (REMOVED in Phase 4)
# ---------------------------------------------------------------------------
#
# Was: TestC19WalkerVsLegacyParity. The legacy emit-order block was
# deleted in Phase 4 (the walker is the sole emit path), so the
# walker-vs-legacy comparison is no longer meaningful — there's no
# legacy path to compare against. The 14 schema-load + tabular-factory
# + auto-registration tests above are the surviving Phase-2 coverage.


def test_walker_smoke_full_sequence() -> None:
    """One mixed source runs end-to-end without raising — sanity check
    that the three node kinds interleave cleanly."""
    text = (
        "# III. MATERIAL\n"
        "\n"
        "## A. GENERAL INFORMATION\n"
        "\n"
        "### A.1 Identifiers\n"
        "\n"
        "Some prose at A.1.\n"
        "\n"
        "!HELLO!\n"
        "\n"
        "#### A.1.1 Subentry\n"
        "\n"
        "More prose.\n"
    )
    rtf, _ = _walk(text)
    # Depth-3 and depth-4 emits go through `_emit_section_heading`
    # which wraps the code in `\\bkmkstart …`. Depth-1 (roman) and
    # depth-2 (group) emitters today render title-only — bookmark
    # emission for those depths is a Phase-4 cleanup item (the
    # walker may choose to emit its own bookmark wrap for III / A
    # before delegating to the title emitter).
    for bn in ("A_1", "A_1_1"):
        assert f"\\bkmkstart {bn}" in rtf
    # Depth-1 and depth-2 titles still rendered.
    assert "MATERIAL" in rtf
    assert "GENERAL INFORMATION" in rtf
    # Hello directive fired.
    assert "!HELLO! directive fired" in rtf
