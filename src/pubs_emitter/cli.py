"""CLI entry point: parse args + orchestrate the 5-phase pipeline.

Pipeline (see module docstrings for each phase):
  1. plan_lookups()     → list[NetworkTask] for items not in cache
  2. dispatch_parallel() → run concurrently via ThreadPoolExecutor
  3. commit_results()   → persist into the appropriate cache table
  4. build_*()          → assemble Citation / Patent records (warm cache)
  5. write_rtf()        → emit final RTF (citations + patent table)
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from collections import defaultdict
from typing import Optional, cast

import bibtexparser
import yaml

from .builders import (
    build_book_chapter,
    build_citation,
    build_conference_presentation,
    build_cve_from_yaml,
    build_disclosure_from_yaml,
    build_grant,
    build_invited_talk,
    build_leadership_role,
    build_media_appearance,
    build_patent,
    build_service_entry,
    build_course_development,
    build_course_taught,
    build_entrepreneurial_activity,
    build_software_product,
    build_student_award,
    build_technology_transfer,
    build_undergrad_pathway,
    build_undergrad_products,
    build_student,
    build_thesis,
    build_under_review,
    load_candidate_information,
    load_non_scholar,
    load_outline,
    load_section_prose,
    validate_non_scholar,
    _warn_section_prose_word_counts,
)
from .config import (
    BIB_IGNORE,
    PUBLICATION_HIDE,
    DEFAULT_CANDIDATE_INFO_FILE,
    DEFAULT_PAGES_CACHE_FILE,
    DEFAULT_DB_FILE,
    DEFAULT_EVALUATIONKIT_RAWDATA_FILE,
    DEFAULT_MAX_WORKERS,
    DEFAULT_OUT_FILE,
    MANUAL_LINKS,
    SECTION_CODES,
    SECTION_ORDER,
    STUDENTS,
)
from .db import LOOKUP_STATS, open_db, populate_students, seed_manual_links
from .lookup import commit_results, dispatch_parallel, plan_lookups
from .rtf import build_paper_index, write_rtf
from .types import (
    BibEntry, Citation, ConferencePresentation, Grant, InvitedTalk, KeyWork,
    LeadershipRole, MediaAppearance, Patent, PostdocVisiting, Publications,
    CourseDevelopment, CourseTaught, EntrepreneurialActivity,
    Section, ServiceEntry, SoftwareProduct, Student, StudentAward,
    TechnologyTransfer, UndergradPathway, UndergradProduct, UnderReview,
)
from .venue import (
    EntryParseError,
    MissingArxivId,
    MissingBracketTag,
    UnrankedVenue,
    is_book_chapter_entry,
    is_patent_entry,
    is_thesis_entry,
    normalize_title,
)


log = logging.getLogger("pubs-emitter")


def log_phase(name: str) -> None:
    """Banner to visually demarcate pipeline phases in the log."""
    log.info("%%%%%%%% %s %%%%%%%%", name.upper())


# ----- I/O helpers --------------------------------------------------------


def load_bib(path: str) -> list[BibEntry]:
    log.info("Loading BibTeX from %s", path)
    with open(path, "r", encoding="utf-8") as f:
        db = bibtexparser.load(f)
    log.info("Loaded %d entries", len(db.entries))
    return cast(list[BibEntry], db.entries)


def merge_pages_cache(entries: list[BibEntry], cache_path: str) -> None:
    """Overlay the Crossref-backfilled page cache onto bib entries in-place.

    The cache is a YAML mapping `bib_key -> {pages, doi, source, fetched,
    bib_year}` populated by `tools/crossref_pages_backfill.py`. Only
    entries that LACK a `pages` field receive the injection — author-
    written pages always win. Missing cache file is a silent no-op
    (first run / cache not yet populated). Logs one INFO line per
    injected entry so a deploy log shows the backfill coverage.
    """
    if not cache_path:
        return
    if not os.path.exists(cache_path):
        log.debug("Page cache not present at %s (skipping merge)", cache_path)
        return
    with open(cache_path, "r", encoding="utf-8") as f:
        cache = yaml.safe_load(f) or {}
    if not isinstance(cache, dict):
        log.warning("Page cache %s root is not a mapping; skipping", cache_path)
        return
    injected = 0
    for entry in entries:
        if entry.get("pages"):
            continue
        key = entry.get("ID", "")
        cached = cache.get(key)
        if not cached:
            continue
        pages = cached.get("pages")
        if not pages:
            continue
        entry["pages"] = pages
        injected += 1
    log.info(
        "Page cache: %d entries had pages injected from %s",
        injected, cache_path,
    )


def filter_ignored(entries: list[BibEntry], ignore_titles: list[str]) -> list[BibEntry]:
    """Drop bib entries whose title matches any of `ignore_titles`.

    Match is case- and whitespace-insensitive (same normalization as the
    CVE.paper_title resolver). Scholar re-exports clobber manual deletions
    from my_papers.bib, so this config-driven filter is the durable way to
    suppress unwanted entries.
    """
    if not ignore_titles:
        return entries
    ignore_norm = {normalize_title(t) for t in ignore_titles}
    seen_norm: set[str] = set()
    kept: list[BibEntry] = []
    for e in entries:
        norm = normalize_title(e.get("title", ""))
        if norm in ignore_norm:
            seen_norm.add(norm)
            log.info("Filtered (bib_ignore): %s", e.get("title", "")[:80])
            continue
        kept.append(e)
    # Surface stale `bib_ignore:` entries — titles listed but no longer in the bib.
    unused = ignore_norm - seen_norm
    if unused:
        log.warning(
            "%d `bib_ignore:` entries didn't match any bib title "
            "(possibly Scholar renamed/removed them):",
            len(unused),
        )
        for title in ignore_titles:
            if normalize_title(title) in unused:
                log.warning("  · %s", title)
    return kept


def filter_hidden(entries: list[BibEntry], hide_keys: list[str]) -> list[BibEntry]:
    """Drop bib entries whose citation key appears in `hide_keys`.

    Distinct from `filter_ignored` (which matches by title): the hide list
    is keyed by stable BibTeX citation keys so the suppression survives
    title edits. Logged at INFO so the user sees which entries were
    trimmed. Hidden entries are filtered BEFORE `paper_index` /
    `key_work_index` / `ref_index` assembly so no cross-ref ever resolves
    to a hidden paper and the visible-only sequence renders without gaps.

    Also surfaces stale `publication_hide:` keys (listed but no longer
    matched in the bib) so the user notices if Scholar's bibtex key
    changed under us.
    """
    if not hide_keys:
        return entries
    hide_set = set(hide_keys)
    seen: set[str] = set()
    kept: list[BibEntry] = []
    for e in entries:
        key = e.get("ID", "")
        if key in hide_set:
            seen.add(key)
            log.info("Filtered (publication_hide): %s — %s",
                     key, e.get("title", "")[:80])
            continue
        kept.append(e)
    unused = hide_set - seen
    if unused:
        log.warning(
            "%d `publication_hide:` keys didn't match any bib citation key "
            "(possibly Scholar renamed them):",
            len(unused),
        )
        for key in unused:
            log.warning("  · %s", key)
    return kept


def log_section_summary(publications: Publications, patents: list[Patent]) -> None:
    for section in SECTION_ORDER:
        if section == "Patents":
            if patents:
                log.info(
                    "Section %s '%s': %d patents",
                    SECTION_CODES[section], section, len(patents),
                )
            continue
        cits = publications.get(section, [])
        if cits:
            log.info(
                "Section %s '%s': %d papers",
                SECTION_CODES[section], section, len(cits),
            )


def report_parse_errors(errors: list[EntryParseError]) -> None:
    """Group + dedupe parse errors by class so the user sees one actionable
    line per unique problem (one acronym, not N repeats of it)."""
    unranked: dict[str, list[str]] = defaultdict(list)   # acronym → [titles]
    no_arxiv: list[str] = []
    no_bracket: list[str] = []
    other: list[str] = []

    for e in errors:
        if isinstance(e, UnrankedVenue):
            unranked[e.acronym].append(e.title)
        elif isinstance(e, MissingArxivId):
            no_arxiv.append(e.title)
        elif isinstance(e, MissingBracketTag):
            no_bracket.append(e.title)
        else:
            other.append(str(e))

    total = len(errors)
    log.error("=" * 60)
    log.error("%d parse error(s) across %d unique problem class(es):",
              total, sum(1 for s in (unranked, no_arxiv, no_bracket, other) if s))

    if unranked:
        n_papers = sum(len(v) for v in unranked.values())
        log.error("")
        log.error(
            "[unranked venues] %d unique acronym(s), %d paper(s) total.",
            len(unranked), n_papers,
        )
        log.error("Add each acronym under the appropriate rank in assets/config.yaml:")
        for acronym in sorted(unranked, key=str.lower):
            papers = unranked[acronym]
            log.error("  %s  (%d)", acronym, len(papers))
            for title in papers:
                log.error("      · %s", title)

    if no_arxiv:
        log.error("")
        log.error("[arxiv missing ID] %d paper(s).", len(no_arxiv))
        log.error("Add `eprint = {<id>}` or include `arXiv:<id>` in the journal field:")
        for title in no_arxiv:
            log.error("  · %s", title)

    if no_bracket:
        log.error("")
        log.error("[missing bracket tag] %d paper(s).", len(no_bracket))
        log.error("Every journal/booktitle must begin with [ACRONYM'YY], e.g. [ICSE'25]:")
        for title in no_bracket:
            log.error("  · %s", title)

    if other:
        log.error("")
        log.error("[other] %d error(s).", len(other))
        for msg in other:
            log.error("  · %s", msg)
    log.error("=" * 60)


def log_lookup_stats() -> None:
    """Summarize lookup-cache effectiveness across DOI, patent, CVE caches."""
    def fmt(label: str, hits: int, misses: int) -> None:
        total = hits + misses
        if total:
            rate = 100.0 * hits / total
            log.info(
                "%s cache: %d/%d hits (%.0f%%); %d sequential fallback fetch(es)",
                label, hits, total, rate, misses,
            )

    fmt("DOI", LOOKUP_STATS["doi_cache_hits"], LOOKUP_STATS["doi_cache_misses"])
    fmt("Patent", LOOKUP_STATS["patent_cache_hits"], LOOKUP_STATS["patent_cache_misses"])
    fmt("CVE", LOOKUP_STATS["cve_cache_hits"], LOOKUP_STATS["cve_cache_misses"])
    arxiv = LOOKUP_STATS["arxiv_constructed"]
    if arxiv:
        log.info("arXiv: %d DOIs constructed (cache bypassed)", arxiv)


# ----- CLI ----------------------------------------------------------------


def _parse_sections_filter(arg: Optional[str]) -> Optional[set[str]]:
    """Parse the `--sections` CLI flag into a set of section codes.

    Returns `None` when the flag isn't supplied (signal: emit everything,
    the default). Returns a set of trimmed code strings when supplied —
    e.g. `"C.4, C.10, C.18"` → `{"C.4", "C.10", "C.18"}`.

    The set is consumed by `write_rtf`'s skip logic; a section emits
    when its code is in the set OR when it's a CHILD code of one in the
    set (so `--sections C.16` also emits `C.16.2.3`, `C.16.2.4`,
    `C.16.3.3`).
    """
    if not arg:
        return None
    return {s.strip() for s in arg.split(",") if s.strip()}


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="pubs-emitter",
        description=(
            "Generate a formatted RTF publication list from a BibTeX file. "
            "Output is suitable for pasting into Word with formatting preserved "
            "(bold for me, superscripts for student/advisor roles, hyperlinks, "
            "patent table, CVE entries)."
        ),
        epilog=(
            "Environment:\n"
            "  LOG_LEVEL              Logging verbosity (DEBUG/INFO/WARNING/ERROR). Default INFO.\n"
            "  PATENTSVIEW_API_KEY    Optional; enables USPTO issue-date lookup for patents.\n"
            "  NVD_API_KEY            Optional; raises NVD rate limit ~10x.\n"
            "  PUBS_EMITTER_CONFIG    Override path to assets/config.yaml.\n"
            "  PUBS_EMITTER_USER_AGENT Override the HTTP User-Agent (mailto:).\n\n"
            "BibTeX conventions:\n"
            "  - Citations: journal / booktitle must begin with a bracketed tag,\n"
            "    e.g. `[ICSE'25]`. Acronym must appear in assets/config.yaml.\n"
            "  - Patents: @misc whose `publisher` or `note` contains 'patent';\n"
            "    `note = {US Patent 11,176,090}` carries the number.\n"
            "  - CVEs live in the YAML side file, NOT the bib. See --non-scholar.\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--bib", required=True, metavar="PATH",
        help="Path to the input BibTeX file (required).",
    )
    parser.add_argument(
        "--out", default=DEFAULT_OUT_FILE, metavar="PATH",
        help=f"Path to write the output RTF file. Default: {DEFAULT_OUT_FILE}",
    )
    parser.add_argument(
        "--cache", default=DEFAULT_DB_FILE, metavar="PATH",
        help=(
            f"Path to the SQLite lookup cache. Created if missing; safe to delete "
            f"to force re-lookup. Default: {DEFAULT_DB_FILE}"
        ),
    )
    parser.add_argument(
        "--workers", type=int, default=DEFAULT_MAX_WORKERS, metavar="N",
        help=f"Parallel network workers. Default: {DEFAULT_MAX_WORKERS}",
    )
    parser.add_argument(
        "--non-scholar", metavar="PATH", default=None,
        help=(
            "Path to YAML side file with non-Scholar work (CVEs etc.). "
            "Bib stays Scholar-canonical; this file fills the gaps. Optional."
        ),
    )
    parser.add_argument(
        "--candidate-info", metavar="PATH",
        default=DEFAULT_CANDIDATE_INFO_FILE,
        help=(
            "Path to the Section III front-matter YAML (A.1-A.7). "
            "Default: %(default)s. Pass an empty string ('') to skip front "
            "matter entirely (renders just C.X and Section V)."
        ),
    )
    parser.add_argument(
        "--evaluationkit-rawdata", metavar="PATH",
        default=DEFAULT_EVALUATIONKIT_RAWDATA_FILE,
        help=(
            "Path to the EvaluationKit raw-data CSV. Aggregated rows are "
            "merged into C.17 alongside any YAML-authored entries. "
            "Default: %(default)s. Pass an empty string ('') to skip "
            "CSV-derived C.17 rows."
        ),
    )
    parser.add_argument(
        "--pages-cache", metavar="PATH",
        default=DEFAULT_PAGES_CACHE_FILE,
        help=(
            "Path to the Crossref-backfilled page-count cache (populated "
            "by tools/crossref_pages_backfill.py). Entries missing a "
            "`pages` field receive the cached value at load time. "
            "Default: %(default)s. Missing file is a silent no-op. "
            "Pass an empty string ('') to disable the merge."
        ),
    )
    parser.add_argument(
        "--sections", metavar="CODES", default=None,
        help=(
            "Comma-separated list of section codes to emit (e.g. "
            "'C.4,C.10,C.18'). Codes match the C.X heading codes from "
            "SECTION_CODES (top-level or sub-section). When omitted, "
            "ALL sections are emitted (default behavior). Sub-section "
            "codes like 'C.16' also include their child codes "
            "(C.16.2.3, C.16.2.4, C.16.3.3) automatically. The build "
            "still computes every section (so cross-refs / @id "
            "resolution / paper_index numbering match the full "
            "document) — only emission is filtered. Useful for "
            "spot-checking a single section in Word without re-rendering "
            "the whole packet."
        ),
    )
    parser.add_argument(
        "--list-styles", action="store_true",
        help=(
            "Print the style registry (STYLES + SPACING + page-break "
            "+ border markers from `pubs_emitter.styles`) and exit. "
            "Diagnostic — shows what RTF open-tag sequence each named "
            "style produces, useful when debugging an unexpected visual."
        ),
    )
    parser.add_argument(
        "--section-prose", default="assets/section-prose.md",
        help=(
            "Path to the section-prose markdown file. Provides "
            "optional hand-authored introductory prose for any section "
            "heading (`## A.1`, `## C.5.4`, `## C.16.2.1`, …). At "
            "render time every section heading is followed by the "
            "prose body looked up by code; sections with no entry "
            "render heading-only. Pass an empty string ('') to skip "
            "the file entirely. Default: assets/section-prose.md. "
            "Note: B.X self-evaluation prose lives in self-evaluation.md, "
            "not here."
        ),
    )
    parser.add_argument(
        "--word-counts", action="store_true",
        help=(
            "Log a one-line summary of B.1 / B.2 / B.3 word counts vs "
            "the Purdue template's recommended limits (1000 / 250 / "
            "500) at build time. Over-cap also fires the existing "
            "per-section warning; this flag surfaces the totals "
            "unconditionally so the draft state is visible during "
            "polish passes without grep'ing for warnings."
        ),
    )
    parser.add_argument(
        "--outline", default="assets/outline.md",
        help=(
            "Path to the markdown-master walker outline file — the "
            "single source of truth for the document outline. Headings "
            "here become the rendered packet's outline; !DIRECTIVE! "
            "lines dispatch to renderers registered in "
            "pubs_emitter.directives. Default: assets/outline.md."
        ),
    )
    parser.add_argument(
        "--table-schemas", default="assets/table-schemas.yaml",
        help=(
            "Path to the declarative tabular-directive schema file. "
            "Each entry auto-registers as a `!NAME!` directive whose "
            "renderer emits an RtfTable from the row data on "
            "RenderContext. Adding a new simple-table section requires "
            "only (a) a YAML stanza here and (b) a `!DIRECTIVE!` line "
            "in the outline file — no Python edit. Default: "
            "assets/table-schemas.yaml."
        ),
    )
    return parser.parse_args(argv)


def _print_style_registry() -> None:
    """Dump the styles.STYLES registry + sidecar dicts. Format:
        STYLE NAME              OPEN TAGS            SPACING  PAGE? BORDER?
    """
    from .styles import (
        BORDER_BLOCKS, PAGE_BREAK_BEFORE, SPACING, STYLES,
    )
    name_w = max(len(n) for n in STYLES) + 2
    print(f"{'STYLE':<{name_w}} {'OPEN':<28} {'SPACING':<11} {'PAGE':<5} BORDER")
    print(f"{'-' * (name_w - 1):<{name_w}} {'-' * 27:<28} {'-' * 10:<11} {'-' * 4:<5} ------")
    for name in sorted(STYLES):
        opens = STYLES[name] or "(empty)"
        sb, sa = SPACING.get(name, (0, 0))
        spacing = f"sb={sb},sa={sa}" if (sb or sa) else "-"
        page = "yes" if name in PAGE_BREAK_BEFORE else "-"
        border = "yes" if name in BORDER_BLOCKS else "-"
        print(f"{name:<{name_w}} {opens:<28} {spacing:<11} {page:<5} {border}")


def main(argv: Optional[list[str]] = None) -> None:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stderr,
    )
    # `--list-styles` is a diagnostic short-circuit: dump the registry
    # and exit before required-arg validation forces `--bib`.
    raw_argv = argv if argv is not None else sys.argv[1:]
    if "--list-styles" in raw_argv:
        _print_style_registry()
        return
    args = parse_args(argv)
    conn = open_db(args.cache)
    try:
        log_phase("init")
        seed_manual_links(conn, MANUAL_LINKS)

        log_phase("load input")
        entries = filter_hidden(
            filter_ignored(load_bib(args.bib), BIB_IGNORE),
            PUBLICATION_HIDE,
        )
        # Merge in Crossref-backfilled page counts from the side cache
        # (`tools/crossref_pages_backfill.py` populates this asynchronously).
        # Renderer treats injected pages identically to bib-native pages;
        # `format_details` converts ranges to "N pages" on emit.
        merge_pages_cache(entries, args.pages_cache)
        # Load + validate YAML side file (CVEs etc.) before any network work.
        non_scholar = load_non_scholar(args.non_scholar)
        validate_non_scholar(non_scholar, entries)
        # Section III front matter (A.1-A.7). Optional — None when no YAML
        # path is supplied or the file is missing.
        candidate_info = load_candidate_information(args.candidate_info or None)
        # Optional intro prose for any section heading, keyed by dotted
        # code (`A.1`, `B.1`, `C.16.2.1`, …). Empty dict when no path /
        # file missing → all sections render heading-only with no intro
        # body. B.1-B.5 entries get extra treatment: word-count warnings
        # against the Purdue template caps, #MACRO_NAME substitution,
        # and the rendered markdown emphasis. Everything else flows
        # through the generic `_emit_section_heading` prose pipe.
        section_prose = load_section_prose(args.section_prose or None)
        _warn_section_prose_word_counts(section_prose)
        if args.word_counts:
            from .builders import _count_words, _B_WORD_CAPS
            parts = []
            for code in ("B.1", "B.2", "B.3"):
                key = f"b{code[2:]}"  # "B.1" → "b1"
                n = _count_words("\n\n".join(section_prose.get(code, [])))
                cap = _B_WORD_CAPS.get(key)
                parts.append(
                    f"{code} word count: {n} (recommended ≤ {cap})"
                )
            log.info(", ".join(parts))

        # Populate students AFTER loading non-scholar YAML so we can union the
        # rich C.14 graduate_students names into STUDENTS["G"] for marker matching.
        # Static config.yaml STUDENTS["G"] stays as a baseline; YAML graduate_students
        # extends it. Undergrads stay in STUDENTS["U"] (config.yaml) for now.
        merged_students = {k: list(v) for k, v in STUDENTS.items()}
        grad_yaml_names = [
            s.get("name", "") for s in (non_scholar.get("graduate_students") or [])
            if s.get("name")
        ]
        merged_students["G"] = list({*merged_students.get("G", []), *grad_yaml_names})
        populate_students(conn, merged_students)

        log_phase("lookups")
        # plan / dispatch / commit
        tasks = plan_lookups(conn, entries, non_scholar)
        if tasks:
            results = dispatch_parallel(tasks, max_workers=args.workers)
            commit_results(conn, results)
        else:
            log.info("Cache is fully warm; no network lookups needed.")

        log_phase("build")
        # Build citations / patents / theses from bib (warm cache).
        # Theses are built internally but NOT emitted in any section — held
        # in `theses_internal` for future cross-references.
        # Per-entry parse errors are batched: the loop continues past each
        # failure and ALL errors are reported together at the end.
        publications: Publications = defaultdict(list)
        patents: list[Patent] = []
        theses_internal: list[Citation] = []
        parse_errors: list[EntryParseError] = []
        patent_impacts: dict[str, str] = dict(non_scholar.get("patent_impacts") or {})
        for entry in entries:
            try:
                if is_patent_entry(entry):
                    patents.append(build_patent(conn, entry, patent_impacts))
                elif is_thesis_entry(entry):
                    theses_internal.append(build_thesis(conn, entry))
                elif is_book_chapter_entry(entry):
                    cit = build_book_chapter(conn, entry)
                    publications[cit.section].append(cit)
                else:
                    cit = build_citation(conn, entry)
                    publications[cit.section].append(cit)
            except EntryParseError as e:
                parse_errors.append(e)

        if parse_errors:
            report_parse_errors(parse_errors)
            sys.exit(1)

        # Phase 4b: build CVE + security-disclosure citations from YAML, route to C.5.
        for cve in non_scholar.get("cves") or []:
            cit = build_cve_from_yaml(conn, cve, entries)
            publications[cit.section].append(cit)
        for disc in non_scholar.get("security_disclosures") or []:
            cit = build_disclosure_from_yaml(conn, disc, entries)
            publications[cit.section].append(cit)

        # Phase 4c: build key-works list (C.1 highlight section).
        # Each key_work links to a bib paper; we re-use build_citation to
        # produce the standard Citation, then attach the impact statement.
        key_works: list[KeyWork] = []
        for kw_yaml in non_scholar.get("key_works") or []:
            from .builders import _bib_entry_by_title  # local: not a public surface
            paper = _bib_entry_by_title(entries, kw_yaml["paper_title"])
            # validate_non_scholar guarantees paper exists.
            assert paper is not None
            paper_cit = build_citation(conn, paper)
            key_works.append(KeyWork(
                citation=paper_cit, impact=kw_yaml["impact"],
                id=str(kw_yaml.get("id", "") or ""),
            ))

        # Phase 4d: build invited-talks + leadership-roles + media-appearances from YAML.
        invited_talks: list[InvitedTalk] = [
            build_invited_talk(t) for t in (non_scholar.get("invited_talks") or [])
        ]
        leadership_roles: list[LeadershipRole] = [
            build_leadership_role(r) for r in (non_scholar.get("leadership_roles") or [])
        ]
        media_appearances: list[MediaAppearance] = [
            build_media_appearance(m) for m in (non_scholar.get("media_appearances") or [])
        ]
        conference_presentations: list[ConferencePresentation] = [
            build_conference_presentation(p)
            for p in (non_scholar.get("conference_presentations") or [])
        ]
        # Phase 4e: build the 4 grant lists (C.10/C.11/C.12/C.13) PLUS the
        # Section V, A.2 pending-proposals list. Pending entries carry
        # `status: pending` in YAML; they're peeled off C.10 / C.11 (the
        # only two categories where "submitted but not yet awarded" makes
        # sense — gifts and internal grants don't take pending state) and
        # routed to V.A.2 while preserving their `role` field (PI vs Co-PI)
        # for display inside the pending-proposals appendix.
        _all_pi_grants: list[Grant] = [
            build_grant(g) for g in (non_scholar.get("grants_as_pi") or [])
        ]
        _all_co_pi_grants: list[Grant] = [
            build_grant(g) for g in (non_scholar.get("grants_as_co_pi") or [])
        ]
        grants_as_pi: list[Grant] = [
            g for g in _all_pi_grants if g.status != "pending"
        ]
        grants_as_co_pi: list[Grant] = [
            g for g in _all_co_pi_grants if g.status != "pending"
        ]
        pending_proposals: list[Grant] = [
            g for g in (_all_pi_grants + _all_co_pi_grants)
            if g.status == "pending"
        ]
        gifts: list[Grant] = [
            build_grant(g) for g in (non_scholar.get("gifts") or [])
        ]
        internal_grants: list[Grant] = [
            build_grant(g) for g in (non_scholar.get("internal_grants") or [])
        ]
        graduate_students: list[Student] = [
            build_student(s) for s in (non_scholar.get("graduate_students") or [])
        ]
        # C.15 postdocs_visiting: minimal builder. Empty list is the canonical
        # case (user has no postdocs); the renderer emits "N/A" then.
        postdocs_visiting: list[PostdocVisiting] = [
            PostdocVisiting(
                year=int(p.get("year", 9999) or 9999),
                name=p.get("name", "") or "",
                last_degree_date=p.get("last_degree_date", "") or "",
                prior_affiliation=p.get("prior_affiliation", "") or "",
                position_title_dates=p.get("position_title_dates", "") or "",
                current_position=p.get("current_position", "") or "",
                id=str(p.get("id", "") or ""),
            )
            for p in (non_scholar.get("postdocs_visiting") or [])
        ]
        undergraduate_students: list[Student] = [
            build_student(s) for s in (non_scholar.get("undergraduate_students") or [])
        ]
        # Phase 4f: build the four service sections (C.23-C.26).
        # Service entries: build all, then drop `show: false` BEFORE
        # numbering / ref_index assembly so hidden entries don't consume
        # a C.X.N slot and the visible-only sequence renders without gaps.
        university_service: list[ServiceEntry] = [
            s for s in (build_service_entry(e) for e in (non_scholar.get("university_service") or []))
            if s.show
        ]
        profession_service: list[ServiceEntry] = [
            s for s in (build_service_entry(e) for e in (non_scholar.get("profession_service") or []))
            if s.show
        ]
        national_service: list[ServiceEntry] = [
            s for s in (build_service_entry(e) for e in (non_scholar.get("national_service") or []))
            if s.show
        ]
        other_service: list[ServiceEntry] = [
            s for s in (build_service_entry(e) for e in (non_scholar.get("other_service") or []))
            if s.show
        ]
        # A.1: in-flight submissions. Sorted ASC by `due_date` (empty → bottom).
        under_review: list[UnderReview] = [
            build_under_review(conn, e) for e in (non_scholar.get("under_review") or [])
        ]
        under_review.sort(key=lambda u: u.due_date)
        # C.22: software products. Renderer sorts by year ASC.
        software_products: list[SoftwareProduct] = [
            build_software_product(e) for e in (non_scholar.get("software_products") or [])
        ]
        # C.16.2.4 / C.16.3.3: student awards / fellowships, level-routed.
        student_awards: list[StudentAward] = [
            build_student_award(e) for e in (non_scholar.get("student_awards") or [])
        ]
        # C.17 / C.18 / C.20 / C.21: empty-allowed YAML lists. C.18
        # populated; C.17 + C.20 + C.21 currently empty (render "N/A" until
        # the user populates them).
        courses_taught: list[CourseTaught] = [
            build_course_taught(e)
            for e in (non_scholar.get("courses_taught") or [])
        ]
        # C.17 EvaluationKit ingest. CSV-derived rows are appended to the
        # YAML-authored list (which carries grey-row notes + any manual
        # cross-institution rows); the renderer sorts the combined list
        # by (year, semester_order). A YAML entry with matching
        # (year, semester_str, course_number) WINS — manual overrides
        # take precedence so the candidate can hand-edit a problem row
        # without losing it on the next CSV refresh.
        eval_csv_path = args.evaluationkit_rawdata or None
        if eval_csv_path:
            from .evaluations import load_courses_taught_from_csv
            try:
                csv_rows = load_courses_taught_from_csv(eval_csv_path)
            except FileNotFoundError:
                log.warning(
                    "EvaluationKit CSV not found at %s — skipping CSV-derived "
                    "C.17 rows.", eval_csv_path,
                )
                csv_rows = []
            yaml_keys = {
                (c.year, c.semester_str, c.course_number)
                for c in courses_taught
            }
            for c in csv_rows:
                if (c.year, c.semester_str, c.course_number) in yaml_keys:
                    continue
                courses_taught.append(c)

        # Responsibility text — single flat per-course table. Every data
        # row in C.17 looks up its text by (year, semester_str,
        # course_number). No default fallback: a row whose key isn't in
        # the table renders with blank responsibility AND logs a warning
        # so the gap surfaces. Note rows skip the lookup (no cell).
        resp_table_raw = non_scholar.get("courses_responsibility") or []
        resp_table: dict[tuple[int, str, str], str] = {}
        for o in resp_table_raw:
            if not isinstance(o, dict):
                continue
            resp_table[(
                int(o.get("year", 0) or 0),
                str(o.get("semester_str", "") or ""),
                str(o.get("course_number", "") or ""),
            )] = str(o.get("text", "") or "")
        for i, ct in enumerate(courses_taught):
            if ct.is_note_row or ct.responsibility:
                continue
            resp_key: tuple[int, str, str] = (ct.year, ct.semester_str, ct.course_number)
            if resp_key not in resp_table:
                log.warning(
                    "C.17 row %s %s %s has no `courses_responsibility:` "
                    "entry — responsibility cell will render blank. "
                    "Add an entry to assets/non-scholar-work.yaml.",
                    ct.semester_str, ct.course_number, ct.title[:40],
                )
                continue
            courses_taught[i] = ct._replace(responsibility=resp_table[resp_key])
        course_development: list[CourseDevelopment] = [
            build_course_development(e)
            for e in (non_scholar.get("course_development") or [])
        ]
        entrepreneurial_activities: list[EntrepreneurialActivity] = [
            build_entrepreneurial_activity(e)
            for e in (non_scholar.get("entrepreneurial_activities") or [])
        ]
        technology_transfer: list[TechnologyTransfer] = [
            build_technology_transfer(e)
            for e in (non_scholar.get("technology_transfer") or [])
        ]

        # Chronological order (oldest first), then alphabetical by venue
        # WITHIN each year so co-located papers cluster (e.g. multiple
        # USENIX papers in 2025 emit adjacently rather than interleaving
        # with ICSE papers of the same year). Sort is stable, so ties on
        # (year, venue) preserve the bib's insertion order.
        for section in publications:
            publications[section].sort(key=lambda c: (c.year, c.venue.lower()))
        patents.sort(key=lambda p: p.year)
        key_works.sort(key=lambda kw: kw.citation.year)
        invited_talks.sort(key=lambda t: t.year)
        leadership_roles.sort(key=lambda r: r.year)
        media_appearances.sort(key=lambda m: m.year)
        for grant_list in (grants_as_pi, grants_as_co_pi, gifts,
                           internal_grants, pending_proposals):
            grant_list.sort(key=lambda g: g.start_year)
        graduate_students.sort(key=lambda s: s.grad_year)
        undergraduate_students.sort(key=lambda s: s.grad_year)
        for svc_list in (
            university_service, profession_service, national_service, other_service,
        ):
            svc_list.sort(key=lambda e: e.year)

        # Back-pointer index — must run AFTER chrono-sort so C.X.Y is stable.
        paper_index = build_paper_index(publications)
        # key_work_index: title → "C.1.N". Built AFTER key_works are sorted.
        kw_code = SECTION_CODES["Key Works"]
        key_work_index: dict[str, str] = {}
        for idx, kw in enumerate(key_works, 1):
            key_work_index[normalize_title(kw.citation.title)] = f"{kw_code}.{idx}"

        # C.16.2.3: auto-derived from publications + bib by counting undergrad
        # coauthors. MUST run after paper_index since each record carries the
        # back-ref C.X.Y from paper_index. Section V A.1 under-review entries
        # are scanned the same way; their `submission_year` mixes into the
        # `-year` sort order so in-flight submissions slot among published
        # papers from the same year (newest first).
        undergrad_products: list[UndergradProduct] = build_undergrad_products(
            conn, publications, paper_index, entries,
            under_review=under_review,
        )

        # C.16.2.2 Other Undergraduate Research Pathways — 4-column table
        # authored in YAML. Missing key → empty list → renderer falls
        # back to the placeholder (heading + blank body) so the C.16
        # outline still emits in order.
        undergrad_pathways: list[UndergradPathway] = [
            build_undergrad_pathway(e)
            for e in (non_scholar.get("undergraduate_research_pathways") or [])
        ]

        # ----- @id cross-reference resolution ---------------------------
        # Build a global ref_index of (user-assigned id) → C.X.Y by walking
        # every YAML-authored list in its FINAL render order. Then substitute
        # `@id` tokens in registered prose fields throughout each list.
        # See builders.resolve_refs + builders.PROSE_FIELDS_BY_TYPE.
        from .builders import resolve_refs_in_list
        from .rtf import index_awards, index_student_awards

        ref_index: dict[str, str] = {}
        # Per-bibkey section-override map for `@bibkey^SECTION` resolution.
        # Populated by the bib citation registration (paper main bucket)
        # and the Key Works registration (papers re-emitted at C.1). When
        # a paper appears in both a main bucket and Key Works, both
        # entries land here:
        #   section_bibkey_index["davis2024impact"] = {
        #       "C.4": "C.4.7",   # the main paper entry
        #       "C.1": "C.1.3",   # the Key Works re-emit
        #   }
        # Bare `@davis2024impact` still resolves via ref_index → "C.4.7";
        # `@davis2024impact^C.1` resolves via this map → "C.1.3".
        section_bibkey_index: dict[str, dict[str, str]] = {}

        def _record_section_bibkey(bib_key: str, code: str) -> None:
            """Record `bib_key` as a known location at the section
            prefix derived from `code` (e.g., "C.4.7" → prefix "C.4")."""
            if not bib_key or not code:
                return
            # Section prefix is everything up to (but not including) the
            # last `.N` segment. For "C.4.7" → "C.4". For "C.1.3" → "C.1".
            # For "Section V, A.1.N|V.A.1.N" — pipe-form codes — skip
            # since under-review entries aren't paper re-emits; the
            # bibkey-vs-V.A.1 disambiguation problem doesn't arise here.
            if "|" in code:
                return
            if "." not in code:
                return
            prefix = code.rsplit(".", 1)[0]
            section_bibkey_index.setdefault(bib_key, {})[prefix] = code

        seen_ids: set[str] = set()

        def _register(item: object, code: str) -> None:
            item_id = str(getattr(item, "id", "") or "")
            if not item_id:
                return
            if item_id in seen_ids:
                log.error(
                    "Duplicate @id %r — second occurrence assigned %s",
                    item_id, code,
                )
                sys.exit(1)
            seen_ids.add(item_id)
            ref_index[item_id] = code

        def _register_simple(items: list, section: Section) -> None:
            code = SECTION_CODES[section]
            for idx, item in enumerate(items, 1):
                _register(item, f"{code}.{idx}")

        # Bib citation keys: each bib entry's BibTeX `ID` (the `key` in
        # `@inproceedings{key, ...}`) → the paper's resolved C.X.Y from
        # paper_index. Held-internal theses (no paper_index entry) skip.
        # Collision with a YAML id surfaces as the duplicate-ref error
        # via _register.
        for entry in entries:
            bib_key = str(entry.get("ID", "") or "")
            title = entry.get("title", "") or ""
            if not bib_key or not title:
                continue
            resolved_code = paper_index.get(normalize_title(title))
            if not resolved_code:
                continue  # not emitted (e.g., thesis)
            if bib_key in seen_ids:
                log.error(
                    "Duplicate @id %r: bib citation key collides with a "
                    "YAML id. Rename one.", bib_key,
                )
                sys.exit(1)
            seen_ids.add(bib_key)
            ref_index[bib_key] = resolved_code
            _record_section_bibkey(bib_key, resolved_code)

        # Under-review entries live under Section V, A.1.1 (Pending
        # Publications). Codes A.1.1.N; bookmark namespace V_A_1_1_N
        # (the `V.` namespace prevents collision with Section III's
        # A.1.1 Identifiers entries). The ref_index value is the
        # pipe-form "Section V, A.1.1.N|V.A.1.1.N" honored by
        # `_finalize_ref_hyperlinks` (display=LHS, bookmark target=RHS).
        # A.1.2 (Evidence) entries are not @-refable from prose — they're
        # internal pointers from A.1.1.N to their evidence screenshot.
        ur_code = SECTION_CODES["Under Review"]  # "A.1"
        for idx, ur in enumerate(under_review, 1):
            bare = f"{ur_code}.1.{idx}"  # A.1.1.N
            _register(ur, f"Section V, {bare}|V.{bare}")
        _register_simple(key_works, "Key Works")
        # Key Works are re-emits of papers also present in C.4 / C.5 /
        # etc. Add a `@bibkey^C.1` entry to the section override index
        # for every key-work whose underlying bib entry resolves: each
        # KeyWork.citation.title maps via bib_entries to a bibkey, and
        # that bibkey is the same one used to register the C.4/C.5 entry
        # above. Without this loop, `@davis2024impact` would still
        # resolve to the C.4 emit (the only entry in ref_index) and
        # there would be no way to reach the C.1 location.
        kw_section_code = SECTION_CODES["Key Works"]
        title_to_bibkey: dict[str, str] = {}
        for entry in entries:
            t = entry.get("title", "") or ""
            bk = str(entry.get("ID", "") or "")
            if t and bk:
                title_to_bibkey[normalize_title(t)] = bk
        for idx, kw in enumerate(key_works, 1):
            kw_bib_key = title_to_bibkey.get(normalize_title(kw.citation.title))
            if kw_bib_key:
                _record_section_bibkey(kw_bib_key, f"{kw_section_code}.{idx}")
        _register_simple(invited_talks, "Invited Talks")
        _register_simple(leadership_roles, "Leadership Roles")
        _register_simple(media_appearances, "Media Appearances")
        _register_simple(grants_as_pi, "Grants PI")
        _register_simple(grants_as_co_pi, "Grants Co-PI")
        _register_simple(gifts, "Gifts")
        _register_simple(internal_grants, "Internal Grants")
        # Section V, A.2 pending proposals — pipe-form ref_index value
        # ("Section V, A.2.N|V.A.2.N") parallels the under-review
        # registration: display "Section V, A.2.N" + bookmark "V_A_2_N".
        # The "V." prefix on the bookmark prevents collision with
        # Section III A.2 Degrees bookmarks at the same numeric code.
        pp_code = SECTION_CODES["Pending Proposals"]
        for idx, g in enumerate(pending_proposals, 1):
            bare = f"{pp_code}.{idx}"
            _register(g, f"Section V, {bare}|V.{bare}")
        _register_simple(graduate_students, "Graduate Students")
        _register_simple(postdocs_visiting, "Postdocs and Visiting Scholars")
        _register_simple(undergraduate_students, "Undergraduate Students")
        # C.16.2.4 / C.16.3.3 use the dedicated indexer to match the
        # renderer's level + tier + year-DESC sort exactly.
        for ref, award in index_student_awards(
            "Undergraduate Student Awards", student_awards,
        ):
            _register(award, ref)
        for ref, award in index_student_awards(
            "Graduate Student Awards", student_awards,
        ):
            _register(award, ref)
        # A.6 awards: use the dedicated indexer to match the renderer's
        # tier-group + chrono sort exactly (externals first, then internals).
        if candidate_info is not None and candidate_info.awards:
            for ref, aw in index_awards(candidate_info.awards):
                _register(aw, ref)
        _register_simple(courses_taught, "Courses Taught")
        _register_simple(course_development, "Course Development")
        _register_simple(entrepreneurial_activities, "Entrepreneurial Activities")
        _register_simple(technology_transfer, "Technology Transfer")
        _register_simple(software_products, "Software Products")
        _register_simple(university_service, "University Service")
        _register_simple(profession_service, "Profession Service")
        _register_simple(national_service, "National Service")
        _register_simple(other_service, "Other Service")

        # Resolve `@id` refs in prose fields. Collect ALL unresolved refs
        # before exiting so the user sees the full list in one pass.
        _all_errors: list[tuple[str, int | str, str]] = []

        def _resolve(items: list, type_name: str) -> list:
            # link_format=True wraps each resolved code with sentinel chars
            # so write_rtf's final pass can convert them to RTF hyperlinks.
            # section_bibkey_index enables `@bibkey^C.1` overrides for
            # papers that appear in both a main section and Key Works.
            new_items, errors = resolve_refs_in_list(
                items, type_name, ref_index, link_format=True,
                section_bibkey_index=section_bibkey_index,
            )
            for idx, unresolved in errors:
                _all_errors.append((type_name, idx, unresolved))
            return new_items

        under_review = _resolve(under_review, "UnderReview")
        key_works = _resolve(key_works, "KeyWork")
        invited_talks = _resolve(invited_talks, "InvitedTalk")
        leadership_roles = _resolve(leadership_roles, "LeadershipRole")
        media_appearances = _resolve(media_appearances, "MediaAppearance")
        grants_as_pi = _resolve(grants_as_pi, "Grant")
        pending_proposals = _resolve(pending_proposals, "Grant")
        grants_as_co_pi = _resolve(grants_as_co_pi, "Grant")
        gifts = _resolve(gifts, "Grant")
        internal_grants = _resolve(internal_grants, "Grant")
        graduate_students = _resolve(graduate_students, "Student")
        postdocs_visiting = _resolve(postdocs_visiting, "PostdocVisiting")
        undergraduate_students = _resolve(undergraduate_students, "Student")
        student_awards = _resolve(student_awards, "StudentAward")
        # Section-prose paragraphs: each prose body can carry @bibkey /
        # @grant-id / @C.X.Y raw-section-code refs. Resolve them now
        # (link_format=True so the post-pass converts to RTF hyperlinks)
        # and surface unresolved refs as build-blocking errors via the
        # shared `_all_errors` list (same fail-loud semantics as the
        # NamedTuple resolver). B.X prose ALSO gets #MACRO_NAME
        # substitution applied first (the macros include Statistics
        # computed below); unresolved macros are also build-blocking.
        from .builders import resolve_refs
        from .statistics import StatsContext, compute_all, substitute
        stats_ctx = StatsContext(
            publications=publications,
            bib_entries=entries,
            paper_index=paper_index,
            conn=conn,
            patents=patents,
            invited_talks=invited_talks,
            profession_service=profession_service,
            grants_as_pi=grants_as_pi,
            grants_as_co_pi=grants_as_co_pi,
            gifts=gifts,
        )
        macros = compute_all(stats_ctx)
        log.info(
            "Statistics macros computed: %s",
            ", ".join(f"{k}={v}" for k, v in sorted(macros.items())),
        )
        _unresolved_macros: list[tuple[str, str]] = []
        resolved_section_prose: dict[str, list[str]] = {}
        for code, paragraphs in section_prose.items():
            new_paragraphs: list[str] = []
            for p in paragraphs:
                # #MACRO_NAME substitution: applied to every section's
                # prose (typo'd macros must not ship), not just B.X.
                p, unresolved_macros = substitute(p, macros)
                for name in sorted(unresolved_macros):
                    _unresolved_macros.append((code, name))
                # @-ref resolution: bibkey / grant-id / raw section code.
                # section_bibkey_index enables `@bibkey^C.1` overrides.
                new_p, unresolved = resolve_refs(
                    p, ref_index, link_format=True,
                    section_bibkey_index=section_bibkey_index,
                )
                for u in unresolved:
                    _all_errors.append(("SectionProse", code, u))
                new_paragraphs.append(new_p)
            resolved_section_prose[code] = new_paragraphs
        section_prose = resolved_section_prose
        if _unresolved_macros:
            for code, name in _unresolved_macros:
                log.error(
                    "Unresolved #macro #%s in section-prose %s; "
                    "known macros: %s",
                    name, code, sorted(macros.keys()),
                )
            log.error(
                "Build aborted — %d unresolved #macro reference(s). "
                "Fix the typo or register the macro in statistics.py "
                "before re-running.",
                len(_unresolved_macros),
            )
            sys.exit(1)
        # A.6 awards: live INSIDE candidate_info, so rebuild the NamedTuple
        # with the resolved list. Skip when no candidate_info was loaded.
        if candidate_info is not None and candidate_info.awards:
            resolved_awards = _resolve(list(candidate_info.awards), "Award")
            candidate_info = candidate_info._replace(awards=resolved_awards)
        courses_taught = _resolve(courses_taught, "CourseTaught")
        course_development = _resolve(course_development, "CourseDevelopment")
        entrepreneurial_activities = _resolve(
            entrepreneurial_activities, "EntrepreneurialActivity",
        )
        technology_transfer = _resolve(technology_transfer, "TechnologyTransfer")
        software_products = _resolve(software_products, "SoftwareProduct")
        for svc_list_name, svc_var in (
            ("university_service", university_service),
            ("profession_service", profession_service),
            ("national_service", national_service),
            ("other_service", other_service),
        ):
            # Mutate the local binding to point to the resolved list.
            new_svc = _resolve(svc_var, "ServiceEntry")
            if svc_list_name == "university_service":
                university_service = new_svc
            elif svc_list_name == "profession_service":
                profession_service = new_svc
            elif svc_list_name == "national_service":
                national_service = new_svc
            else:
                other_service = new_svc

        if _all_errors:
            for type_name, where, unresolved_id in _all_errors:
                # `where` is an int for list-item paths (Grant[3], Student[7])
                # and a string code for the section-prose path
                # (SectionProse[B.1]); `%s` accommodates both. Hard-coded
                # `%d` here previously crashed the log call itself on every
                # section-prose unresolved ref, masking the underlying typo.
                known_ids: list[str] | str = sorted(ref_index.keys()) or "(none)"
                log.error(
                    "Unresolved @id ref @%s in %s[%s]; known ids: %s",
                    unresolved_id, type_name, where, known_ids,
                )
            sys.exit(1)
        log.info(
            "Resolved %d @id cross-references (%d registered ids)",
            sum(1 for _ in seen_ids),
            len(ref_index),
        )

        log_phase("summary")
        log_section_summary(publications, patents)
        if theses_internal:
            log.info(
                "Theses (held internally, not emitted): %d",
                len(theses_internal),
            )
        log_lookup_stats()

        log_phase("render")
        write_rtf(
            args.out, publications, patents, paper_index,
            key_works=key_works, key_work_index=key_work_index,
            invited_talks=invited_talks,
            leadership_roles=leadership_roles,
            media_appearances=media_appearances,
            conference_presentations=conference_presentations,
            bib_entries=entries,
            grants_as_pi=grants_as_pi,
            grants_as_co_pi=grants_as_co_pi,
            gifts=gifts,
            internal_grants=internal_grants,
            graduate_students=graduate_students,
            postdocs_visiting=postdocs_visiting,
            undergraduate_students=undergraduate_students,
            university_service=university_service,
            profession_service=profession_service,
            national_service=national_service,
            other_service=other_service,
            under_review=under_review,
            software_products=software_products,
            student_awards=student_awards,
            undergrad_pathways=undergrad_pathways,
            undergrad_products=undergrad_products,
            entrepreneurial_activities=entrepreneurial_activities,
            technology_transfer=technology_transfer,
            course_development=course_development,
            courses_taught=courses_taught,
            candidate_info=candidate_info,
            section_prose=section_prose,
            pending_proposals=pending_proposals,
            sections_filter=_parse_sections_filter(args.sections),
            ref_index=ref_index,
            macros=macros,
            outline_text=load_outline(args.outline or None),
            table_schemas_path=args.table_schemas or None,
            section_bibkey_index=section_bibkey_index,
        )
    finally:
        conn.close()


if __name__ == "__main__":
    main()
