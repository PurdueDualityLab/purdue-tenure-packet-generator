# CLAUDE.md — pubs-emitter

A small Python tool that generates a formatted RTF publication list for a
tenure packet from a BibTeX file (`my_papers.bib`, exported from Google
Scholar) and a YAML side file for non-Scholar work
(`non-scholar-work.yaml` — talks, grants, students, CVEs, etc.).

User-facing description lives in `README.md`. This file captures the
**non-derivable** rules, conventions, and pitfalls — read this before
editing.

## Pipeline (5 phases in `cli.main`)

1. **`plan_lookups`** — walks every bib entry + CVE, emits `NetworkTask`s
   only for items missing from the SQLite cache.
2. **`dispatch_parallel`** — runs all tasks concurrently via
   `ThreadPoolExecutor`. Per-host `RateLimiter` serializes workers hitting
   the same API. Retry-with-backoff on `429`/`5xx`/connection errors.
3. **`commit_results`** — persists fetched data into `doi_cache` /
   `patent_cache` / `cve_cache` tables in `lookup_cache.sqlite`.
4. **`build_*`** — converts bib entries → `Citation` / `Patent`;
   converts YAML records → `KeyWork` / `InvitedTalk` / `LeadershipRole` /
   `MediaAppearance` / `ConferencePresentation` / `Grant` / `Student`.
   By this point the cache is warm; build is local-only.
5. **`write_rtf`** — emits the final document. Sections are
   chrono-sorted, paper-index built for back-pointers, then rendered.

## Modules (src/pubs_emitter/)

| File | What lives there |
|------|------------------|
| `types.py` | `Citation`, `Patent`, `KeyWork`, `InvitedTalk`, `LeadershipRole`, `MediaAppearance`, `ConferencePresentation`, `Grant`, `Student`, `ServiceEntry`, `NetworkTask`, type aliases (`Category`, `Rank`, `Section`, `BibEntry`, `Publications`, `StudentType`) |
| `config.py` | Loads `assets/config.yaml` (ME, ADVISORS, STUDENTS, RANKS, MANUAL_LINKS, BIB_IGNORE) + code-side constants (SECTION_*, TIER_LABELS, ORG_EXPANSIONS, GRANT_TOTAL_LABELS, RANKED_SECTIONS, PATENT_TABLE_WIDTHS, API URLs, etc.) |
| `latex.py` | `decode_latex` (LaTeX → Unicode via pylatexenc, with bare-`&` sentinel-wrap) + `rtf_escape_unicode` (Unicode → RTF `\\u<signed16>?` escape, with surrogate-pair handling) |
| `db.py` | SQLite cache schema + read-only accessors + `seed_manual_links` + `populate_students` + `LOOKUP_STATS` counter dict |
| `network.py` | `RateLimiter`, `polite_get`, the four `try_*` API fetchers (Crossref/DBLP/NVD/PatentsView), `titles_similar`, `is_no_doi_venue` |
| `authors.py` | Name parsing + `format_author` (citation form: bold-for-me, role markers) + `format_inventors` (patent-table form: bold-for-me, NO role markers) + structural `lookup_student_type` |
| `venue.py` | `parse_venue`, `lookup_rank`, `classify_entry`, `is_patent_entry`/`is_thesis_entry`/`is_book_chapter_entry`, `extract_arxiv_id`, `extract_figshare_id`, `extract_cve_id`, `normalize_title` |
| `lookup.py` | `NetworkTask` planning, parallel dispatch, result commit, cache-aware fetchers (`fetch_doi_or_url`, `fetch_patent_date`, `fetch_cve_data`); also `extract_patent_number` |
| `builders.py` | `build_citation` / `build_book_chapter` / `build_thesis` / `build_patent` / `build_cve_from_yaml` / `build_disclosure_from_yaml` / `build_invited_talk` / `build_leadership_role` / `build_media_appearance` / `build_conference_presentation` / `build_grant` / `build_student` / `build_service_entry`; also `escape_rtf`, `parse_year`, `derive_section`, `load_non_scholar`, `validate_non_scholar` |
| `rtf.py` | `RtfTable` class, `render_citation`, `render_key_works_section`, `render_invited_talks_section`, `render_leadership_section`, `render_media_appearances_section`, `render_conference_presentations_section`, `render_grants_section`, `render_students_section`, `render_service_section`, `render_patents_section`, `build_paper_index`, `write_rtf` |
| `cli.py` | `parse_args` + `main` (the 5-phase orchestrator) |

Dependency direction: everything depends on `types` + `config`. `db` depends
on `types`. `network` depends on `config` + `types`. Higher-level modules
(`lookup`, `builders`, `rtf`) compose the lower-level ones. **No cycles.**
If you find yourself wanting a circular import, you've put a function in
the wrong module.

## Conventions enforced at runtime (fatal errors)

1. **`journal` / `booktitle` MUST begin with `[ACRONYM'YY]`** — checked in
   `build_citation`. Acronym is looked up in `assets/config.yaml`'s
   `ranks:` block; unknown acronym → fatal. Add to YAML, don't hard-code.
   Theses (`@phdthesis`, `@mastersthesis`) and book chapters
   (`@incollection`, `@inbook`) are exempt — they have no venue field
   for the tag to live on.
2. **arXiv entries MUST carry an arXiv or figshare ID** — checked in
   `builders.resolve_link`. arXiv: ID from `eprint = {...}` OR `arXiv:NNNN.NNNN`
   in the journal field; DOI is built as `https://doi.org/10.48550/arXiv.<id>`.
   figshare: ID like `m9.figshare.<id>` in any text field; DOI is built as
   `https://doi.org/10.6084/m9.figshare.<id>`. Both are deterministic — no
   API call.
3. **YAML `paper_title` references MUST match a bib entry** —
   `validate_non_scholar` cross-checks every `paper_title` field across
   `cves`, `security_disclosures`, `conference_presentations`, `key_works`,
   and the `inspired_by` / `publication_outcomes` lists on each grant.
   Match is case-insensitive + whitespace-normalized + LaTeX-decoded.
   Unresolved → fatal. The bib stays Scholar-canonical; the YAML is the
   user's hand-curated side data.
4. **YAML CVE entries MUST have `cve_id`** and at least one of
   (`paper_title`, `disclosers`). `organization` is OPTIONAL — auto-derived
   from NVD's CPE product name if absent (e.g. `freertos-plus-tcp`); supply
   explicitly when you want the friendly form (e.g. `"AWS (FreeRTOS)"`).
5. **YAML grant entries MUST have `title`, `agency`, `role`, `start_year`,
   `end_year`, `amount`** — checked in `validate_non_scholar` for every list
   under `grants_as_pi` / `grants_as_co_pi` / `gifts` / `internal_grants`.
   `agency_short` is OPTIONAL: when set, it prefixes the bolded head line
   as `{agency_short}[ #grant_number]: {title}` (the canonical NSF /
   Rolls Royce / Cisco shape). When empty, the head renders as just the
   bolded title — the CV convention for fellowships, internal Purdue
   programs, and other entries with no canonical funder-name prefix.
6. **YAML student entries MUST have `name`, `degree`, `role`, `grad_year`**.
   Use `grad_year: 9999` and `graduation: ongoing` for in-flight students.
7. **YAML service entries (C.23–C.26) MUST have `description`**. `year` is
   OPTIONAL — accepts int (`2025`), multi-year string (`"2025, 2026, 2027"`),
   or range (`"2024-2025"` / `"2023-present"`). Omit entirely for ongoing
   service with no fixed date (e.g. journal reviewing); the renderer
   suppresses the year and sorts such entries to the end of the list.
   All checks run at load time, batched.

## Section-by-section emission

The full SECTION_ORDER lives in `config.py`. Each entry has a renderer
that lives in `rtf.py` and a YAML key it consumes:

| Code | Section | YAML key | Renderer |
|------|---------|----------|----------|
| C.1 | Key Scholarly Publications | `key_works` | `render_key_works_section` |
| C.2 | Journals | (bib `@article`) | inline in `write_rtf` |
| C.3 | Books and chapters in books | (bib `@incollection` / `@inbook`) | inline in `write_rtf` |
| C.4 | Conferences and Workshops | (bib `@inproceedings`) | inline in `write_rtf` |
| C.5 | Other publications and products | (bib arXiv + magazine) + `cves` + `security_disclosures` | inline in `write_rtf` |
| C.6 | Invited Talks | `invited_talks` | `render_invited_talks_section` |
| C.7 | Leadership Roles | `leadership_roles` | `render_leadership_section` |
| C.8 | Media Appearances | `media_appearances` | `render_media_appearances_section` |
| C.9 | Conference Presentations | `conference_presentations` | `render_conference_presentations_section` |
| C.10 | Grants PI | `grants_as_pi` | `render_grants_section` |
| C.11 | Grants Co-PI | `grants_as_co_pi` | `render_grants_section` |
| C.12 | Gifts | `gifts` | `render_grants_section` |
| C.13 | Internal Grants | `internal_grants` | `render_grants_section` |
| C.14 | Graduate Students | `graduate_students` | `render_students_section` |
| C.16 | Undergraduate Students | `undergraduate_students` | `render_students_section` |
| C.19 | Issued Patents | (bib `@misc` with `note = US Patent ...`) | `render_patents_section` |
| C.23 | Service to Purdue | `university_service` | `render_service_section` |
| C.24 | Service through professional societies | `profession_service` | `render_service_section` |
| C.25 | National / International service | `national_service` | `render_service_section` |
| C.26 | Other external service (journal reviewing etc.) | `other_service` | `render_service_section` |

C.14 and C.19 use `RtfTable`; everything else is paragraph-based.
`GRANT_TOTAL_LABELS` (in `config.py`) determines which grant sections render
a `Total amount: $X` line above the numbered list.

## Cross-references

Three index tables get built between phases 4 and 5:

1. **`paper_index`** (`build_paper_index`) — maps normalized bib-title →
   `C.X.Y`. Built AFTER chronological sort so numbering is stable. Used by:
   - CVE / security-disclosure entries → `(see C.4.7)` back-pointer to the
     associated paper.
   - Conference presentations (C.9) → "Associated with publication C.4.7".
   - Student tables (C.14, C.16) — auto-populates the "Related Publications"
     column via `_student_pub_refs` (structural last-name + initials-prefix match).
2. **`key_work_index`** — maps normalized bib-title → `C.1.N`. Used by
   `render_citation` to append `(listed as C.1.2)` when a regular C.2/C.4
   citation is also designated a Key Work.
3. **Grant ↔ Paper links** (`inspired_by`, `publication_outcomes` on each
   `Grant`) are **validated against the bib but NOT rendered in C.10-C.13**.
   The intended consumer is C.1 Key Works (paper → originating grant
   connections will render with the highlighted papers). The grant renderer
   intentionally omits these lines; cross-link rendering is deferred until
   the linkage shape is defined. Validation in `validate_non_scholar` stays
   so the user can populate the data now and have it surface once C.1
   wiring lands.

## Things that look weird but are intentional

- **The bib is read-only / Scholar-canonical.** Don't add CVEs / talks /
  grants to it; they go in `non-scholar-work.yaml`. Don't add custom fields
  you'd hate to lose on the next Scholar export.
- **`format_inventors` returns RTF-marked-up text; do NOT `escape_rtf` it.**
  The patent table cell's `\b ...\b0` markup must survive to the output.
  `render_patents_section` skips escaping for that cell specifically.
- **Non-ASCII chars go through `rtf_escape_unicode`, not raw UTF-8.** The
  RTF header declares `\ansicpg1252`, so Word reads the file as cp1252.
  Raw UTF-8 bytes (e.g. `\xc3\x87` for Ç) get mangled to `Ã‡`. Every
  user-visible string emission path (`escape_rtf`, `format_author`,
  `format_inventors`) routes through `rtf_escape_unicode` to emit
  `\u<signed16>?` escapes. Supplementary-plane codepoints (>U+FFFF) emit
  as UTF-16 surrogate pairs.
- **NVD `published` date is the CVE's chrono-sort key** — not the bib year.
  CVE C.5 entries land at their disclosure date, which is what reviewers want
  to see.
- **Crossref skipped entirely for USENIX venues.** USENIX doesn't register
  DOIs with Crossref; asking returns a fuzzy-wrong match. DBLP is still tried
  and usually returns a `usenix.org` URL. See `NO_DOI_ACRONYM_PREFIXES`.
- **Title-similarity check on Crossref + DBLP results.** Jaccard overlap of
  significant tokens; below `TITLE_MATCH_THRESHOLD` (0.6) the match is
  rejected and we fall through. Logged as "likely-wrong match" — those
  warnings are signal for bib refinement.
- **Per-host rate limiters live in `network.py` module-level dict.**
  `_HOST_LIMITERS` is shared across all `polite_get` callers and threads.
  NVD's interval is set based on `NVD_API_KEY` env var at import time.
- **Lookup planning uses `functools.partial`, not lambdas.** Lambdas with
  default args (`lambda x=foo: ...`) trip mypy and are easy to mis-bind in
  loops. `partial(fn, arg)` is mypy-clean and binds cleanly.
- **Student-publication matching is structural, not substring.** `lookup_student_type`
  and `_student_pub_refs` require last-name equality + bib initials being a
  prefix of the canonical initials. So "Amusuo, P." matches "Paschal C. Amusuo"
  (bib "P" prefixes canonical "PC") and "Amusuo, R" does not. The matcher
  takes the first character of each space-separated first-name part — initials
  like "P.X." count as one part ("P"), not two.
- **Theses are built internally but not emitted.** `is_thesis_entry` → routed
  to `build_thesis` → held in `theses_internal` for future cross-references
  (e.g., CVE → thesis back-pointer). No section renders them today.
- **`config.yaml` STUDENTS unioned with YAML graduate_students at startup.**
  `cli.main` reads non_scholar BEFORE calling `populate_students`, then
  unions the YAML graduate_students names into the marker-matching pool —
  so adding a student to `graduate_students:` automatically marks their
  bib coauthorship with `G` without a duplicate edit to `students.G` in
  `config.yaml`.

## Adding new things

- **New venue?** Add the acronym under the appropriate rank in
  `assets/config.yaml`. No code change.
- **New student?** Add to `graduate_students:` (or `undergraduate_students:`)
  in `non-scholar-work.yaml` — that's both the C.14 table source AND the
  marker-matching pool. `config.yaml`'s `students.G` / `students.U` stay
  as a baseline for students who don't warrant a full C.14 entry yet.
- **New section type** (e.g., datasets)? Steps:
  1. Add to `Section` Literal in `types.py`
  2. Add to `SECTION_ORDER`, `SECTION_CODES`, `SECTION_HEADINGS` in `config.py`
  3. Decide where its source data lives (probably a new top-level key in
     `non-scholar-work.yaml`)
  4. Add a NamedTuple in `types.py` if the shape is new
  5. Add a `build_X(...)` in `builders.py`
  6. Add validation in `validate_non_scholar` (same batched-collect pattern)
  7. Wire into `plan_lookups` (if it needs API calls) and `cli.main`
  8. Add a `render_X_section` in `rtf.py` mirroring the existing renderers,
     then call it from `write_rtf` and add the section to the "skip in the
     generic loop" tuple
  9. Add a test class in `tests/test_rtf.py` (rendering) + extend
     `tests/fixtures/non-scholar.yaml` + a check in `tests/test_e2e.py`
- **New API source?** Add a `try_X(...)` in `network.py`. Add the host to
  `_HOST_LIMITERS`. Add a new `kind` and cache table if needed, then update
  `plan_lookups` + `commit_results` + a `fetch_X` in `lookup.py`.

## Quality gates

```bash
.venv/bin/pylint src/pubs_emitter   # 9.95/10 baseline
.venv/bin/mypy                      # 0 errors baseline
.venv/bin/pytest                    # 192 tests baseline
./pubs-emitter.py --bib my_papers_sample.bib   # smoke
```

Keep all three at green. The pylint config in `pyproject.toml` disables the
noisy-style nags but keeps every substantive check; new code is held to the
same bar. If you need to suppress an unavoidable warning, prefer an inline
`# pylint: disable=<name>` with a same-line comment explaining *why*.

The only persistent pylint warning is R0801 (duplicate-code) — a
similarity-heuristic flag on the `_bib_entry_by_title` helper in `builders.py`
vs `rtf.py`. Pre-existing and intentional (keeps imports local to each
caller's module).

## Test suite (`tests/`)

`pytest` with stdlib `unittest`-style test classes. Layout:

| File | Coverage |
|------|----------|
| `conftest.py` | Sets `PUBS_EMITTER_CONFIG` to `tests/fixtures/config.yaml` BEFORE any package import (config.py reads at import time, so the env var must be in place first). Provides `fixtures_dir` and an in-memory `conn` fixture (the schema `open_db` creates + seeded student rows). |
| `fixtures/config.yaml` | Minimal config — a few RANKS entries, two ME spellings, two students, two manual_links. |
| `fixtures/sample.bib` | One @inproceedings + @article + @misc-arXiv + @misc-patent + @incollection + @phdthesis. Includes a Çakar coauthor for Unicode coverage. |
| `fixtures/non-scholar.yaml` | One entry under every YAML key the renderer consumes. |
| `test_latex.py` | `decode_latex` (incl. bare-`&` preservation), `rtf_escape_unicode` (incl. surrogate pairs). |
| `test_venue.py` | `parse_venue`, `lookup_rank`, `classify_entry`, `is_*_entry`, `extract_arxiv_id` (modern / legacy / versioned), `extract_figshare_id`, `extract_cve_id`, `normalize_title` (incl. PDF-paste artifact handling). |
| `test_authors.py` | `parse_name_parts`, `name_matches`, `lookup_student_type` (structural matching), `format_author` (bold-for-me, role markers, Unicode escape), `format_inventors`. |
| `test_builders.py` | `escape_rtf`, `format_details`, `parse_year`, `derive_section`, date formatters, `build_invited_talk` / `build_grant` / `build_student` / etc., `validate_non_scholar` (each failure class via `SystemExit`), `load_non_scholar`. |
| `test_lookup.py` | `extract_patent_number`, `plan_lookups` (arXiv-skipped, cache-hit short-circuit, each task `kind`), `dispatch_parallel`, `commit_results`. |
| `test_network.py` | `RateLimiter` timing semantics, `titles_similar`, `is_no_doi_venue`. NO real HTTP. |
| `test_rtf.py` | `RtfTable` arity + bold + cellx, `apply_acronym_expansions`, `render_link_field` (DOI / NVD / generic / empty), `render_citation` (ranked vs unranked prefix, back-ref, key-work cross-link), per-record renderers, `build_paper_index`, `_student_pub_refs`, `render_grants_section` (must NOT emit Inspired by / Publication outcomes), `render_key_works_section`. |
| `test_e2e.py` | Drives `cli.main` against the fixtures with `try_doi` / `try_nvd` / `try_patentsview` monkey-patched in `pubs_emitter.lookup`. Asserts every section heading lands, the grant section omits cross-link lines, Unicode escapes survive, arXiv DOI is constructed (not fetched), CVE link is present, RTF is well-formed. |

**Test discipline:**

- **Tests do not perform real HTTP.** The three `try_*` calls are
  monkey-patched in `pubs_emitter.lookup` (not `pubs_emitter.network`)
  because `lookup.py` did `from .network import try_*`, binding the names
  locally. Patching `network.try_doi` would NOT redirect `lookup`'s use.
- **`conftest.py` sets `PUBS_EMITTER_CONFIG` BEFORE any pubs_emitter import.**
  This must remain the first effective statement after `pytest` is imported.
  Putting any `from pubs_emitter import X` higher in the file would load
  the real `assets/config.yaml` and break test isolation.
- **`tests/fixtures/sample.bib` covers every entry kind the build phase
  branches on.** When you add a new entry kind (e.g., a new `is_*_entry`
  predicate), extend the fixture in the same PR so the e2e test exercises
  the new branch.
- **The e2e test verifies presence, not exact format.** Section headings,
  key substrings, well-formed `\rtf1 ... }` envelope. Don't golden-diff the
  whole RTF — every cosmetic tweak would then require a fixture refresh,
  which inverts the cost calculus.

## Setup / env

- **`./setup.sh`** is the one-command bootstrap: creates `.venv`, installs
  the package editable with dev extras (`pylint`, `mypy`, `pytest`, type
  stubs), and copies `assets/config.example.yaml` → `assets/config.yaml`
  on first run. Idempotent — safe to re-run.
- **`assets/config.yaml` is gitignored**; only `assets/config.example.yaml`
  is committed. The example carries the schema documentation + a starter
  set of venue rankings. The live config holds personal data (real
  students, your actual venue judgments) and stays local.
- **`pyproject.toml`** is the source of truth for deps + lint configs +
  pytest config. No `requirements.txt`.
- **`PUBS_EMITTER_CONFIG`** env var overrides `assets/config.yaml` path.
  Tests use this to point at `tests/fixtures/config.yaml`.
- **`NVD_API_KEY` / `PATENTSVIEW_API_KEY`** are optional — both are
  rate-limit boosters; the tool degrades gracefully without them
  (NVD goes from 50/30s to 5/30s; PatentsView lookup is skipped entirely
  and patents fall back to the bib date).

## Pitfalls

- **`assets/config.yaml` not found** → import-time crash from `config.py`.
  Either the file is missing or `PUBS_EMITTER_CONFIG` points somewhere
  invalid. Don't catch — fail loud.
- **DBLP can connection-reset under load.** Already retried 3× with
  backoff. If you see persistent `Connection reset by peer`, slow the
  `dblp.org` `RateLimiter` interval from `0.2` to `0.5` (or higher).
- **Sequential-fallback fetch** (in `lookup.fetch_*`) is a defensive
  backstop, not the primary path. If you see "sequential fallback" in
  the log, planning missed something — investigate `plan_lookups` rather
  than relying on the fallback.
- **arXiv DOI is constructed, not fetched.** The arXiv DOI scheme
  `10.48550/arXiv.<id>` has been canonical since 2022. Don't add a Crossref
  query path for arXiv entries — it'll either fail or return a fuzzy match.
- **Modifying `Citation` is a cross-cutting change.** Every callsite must
  pass the new field. The NamedTuple gives default values for backwards
  compat (`back_ref_title: Optional[str] = None`), but that's a comfort,
  not a contract.
- **A grant's `inspired_by` / `publication_outcomes` won't show up in
  C.10–C.13 output.** Validation runs (so the bib titles must match), but
  rendering is deferred to the future C.1 cross-link wiring. If you're
  surprised by the data not appearing in the RTF, that's expected — see
  the "Cross-references" section above.
