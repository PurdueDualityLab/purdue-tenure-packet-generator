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
| `types.py` | `Citation`, `Patent`, `KeyWork`, `InvitedTalk`, `LeadershipRole`, `MediaAppearance`, `ConferencePresentation`, `Grant`, `GrantPerson`, `UnderReview`, `Student`, `PostdocVisiting`, `StudentAward`, `UndergradProduct`, `CourseTaught`, `CourseDevelopment`, `EntrepreneurialActivity`, `TechnologyTransfer`, `SoftwareProduct`, `ServiceEntry`, `NetworkTask`, type aliases (`Category`, `Rank`, `Section`, `BibEntry`, `Publications`, `StudentType`) |
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
   `end_year`, `purdue_amount`** — checked in `validate_non_scholar` for every
   list under `grants_as_pi` / `grants_as_co_pi` / `gifts` / `internal_grants`.
   `agency_short` is OPTIONAL: when set, it prefixes the bolded head line
   as `{agency_short}[ #grant_number]: {title}` (the canonical NSF /
   Rolls Royce / Cisco shape). When empty, the head renders as just the
   bolded title — the CV convention for fellowships, internal Purdue
   programs, and other entries with no canonical funder-name prefix.
   Grants carry three explicit USD fields: `total_amount` (whole award
   across all institutions; matters for multi-institution NSF Collab),
   `purdue_amount` (Purdue's share; == total for single-institution),
   `my_amount` (Davis's credited share; == purdue when sole Purdue PI).
   `total_amount` and `my_amount` default to `purdue_amount` if omitted.
   `personnel: list[GrantPerson]` is OPTIONAL (empty = sole PI; renderer
   always emits a "Sole PI" row — no conditional skip). Each
   `GrantPerson` carries `name`, `role` (`"PI"` / `"Co-PI"` / `"Co-I"` —
   default `"Co-PI"`), `department`, `institution` (empty → implicit Purdue),
   and `nsf_award` (external collab partner's separate NSF award #).
   `role=""` is the escape hatch for non-standard framings (e.g. Qualcomm
   "Project Supervisor") — renders the name verbatim with no role prefix.
   `lead_institution` is OPTIONAL: when set, the renderer annotates the
   role line as `Purdue {role} ({lead_institution} is lead)` for
   externally-led collabs, or `{role} (Purdue University is lead)` when
   Purdue leads a multi-inst grant. Empty = single-institution (no
   annotation).
6. **YAML student entries MUST have `name`, `degree`, `role`, `grad_year`**.
   Use `grad_year: 9999` and `graduation: ongoing` for in-flight students.
7. **YAML service entries (C.23–C.26) MUST have `description`**. `year` is
   OPTIONAL — accepts int (`2025`), multi-year string (`"2025, 2026, 2027"`),
   or range (`"2024-2025"` / `"2023-present"`). Omit entirely for ongoing
   service with no fixed date (e.g. journal reviewing); the renderer
   suppresses the year and sorts such entries to the end of the list.
   All checks run at load time, batched.
8. **YAML student_awards (C.16.2.4 / C.16.3.3) MUST have `level`, `tier`,
   `recipient`, `award`, `year`** — checked in `validate_non_scholar`.
   `level` must be exactly `"U"` (→ C.16.2.4 undergrad) or `"G"` (→ C.16.3.3
   grad); any other value is a fatal config error. The level is the
   AWARD's nature (e.g. NDSEG = grad), NOT necessarily the student's
   current degree program — Matt Hyatt is in `students.U` but his NDSEG/GRFP
   awards are tagged `level: G` because the awards themselves are grad-only.
   Auto-detection was deliberately rejected: the recipient-name lookup is
   unreliable (level changes over time) and award-name heuristics fail on
   ambiguous cases (Astronaut Scholar, CSGrad4US, Rolls Royce Fellow).
9. **YAML entrepreneurial_activities (C.20) and technology_transfer (C.21)
   accept empty lists** — these are the canonical pre-promotion-to-full
   state. The renderer emits the section heading + indented `N/A` rather
   than skipping (same convention as C.15 postdocs). Populated entries:
   C.20 needs `summary` + `description`; C.21 needs `code_standard`,
   `change_subject`, `reason`, `research_supporting`, `impact` (all
   required) plus an OPTIONAL `cited_publications: [bib title, …]` whose
   titles must resolve against the bib (same pattern as grant
   `inspired_by`).

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
| C.16.2.3 | Undergrad Research Products | (auto-derived from bib) | `render_undergrad_products_section` |
| C.16.2.4 | Undergraduate Student Awards | `student_awards` (level=U) | `render_student_awards_section("Undergraduate Student Awards", …)` |
| C.16.3.3 | Graduate Student Awards | `student_awards` (level=G) | `render_student_awards_section("Graduate Student Awards", …)` |
| C.17 | Courses Taught | `courses_taught` | `render_courses_taught_section` |
| C.18 | Course Development | `course_development` | `render_course_development_section` |
| C.19 | Issued Patents | (bib `@misc` with `note = US Patent ...`) | `render_patents_section` |
| C.20 | Major Entrepreneurial Activities | `entrepreneurial_activities` | `render_entrepreneurial_activities_section` |
| C.21 | Technology Transfer | `technology_transfer` | `render_technology_transfer_section` |
| C.22 | Software Products | `software_products` | `render_software_products_section` |
| C.23 | Service to Purdue | `university_service` | `render_service_section` |
| C.24 | Service through professional societies | `profession_service` | `render_service_section` |
| C.25 | National / International service | `national_service` | `render_service_section` |
| C.26 | Other external service (journal reviewing etc.) | `other_service` | `render_service_section` |

C.14 and C.19 use `RtfTable`; everything else is paragraph-based.
`GRANT_TOTAL_LABELS` (in `config.py`) determines which grant sections render
a `Total amount: $X` line above the numbered list.

## `@id` cross-references in YAML prose

Any YAML-authored entry can carry an OPTIONAL `id: somekey` field. Free-form
text fields can then write `@somekey` to embed a back-pointer that auto-
resolves to the final `C.X.Y` at build time.

```yaml
course_development:
  - id: ece-30861
    summary: "ECE 30861/46100: Software Engineering"
    description: "..."
  - summary: "Curricular reform"
    description: "Integrated @ece-30861 (and @ece-50861, @ece-30864) into ..."
    #            ^^^^^^^^^^^^ rendered as "C.18.1" at build time
```

**Rules:**
* IDs must match `[a-zA-Z][a-zA-Z0-9_-]*`. Use kebab-case (`ece-30861`,
  `nsf-career`, `qualcomm-2025`) — readable and bibtex-familiar.
* IDs are GLOBAL across the whole YAML. Duplicate id → fatal build error
  with both occurrences identified.
* Unknown `@id` → fatal build error listing every unresolved id and the
  known ids (typo-friendly).
* Email-safe: `davis@purdue.edu` is NOT matched (negative lookbehind for
  word char before `@`).
* Escape: `@@id` renders as a literal `@id` (rare, mostly for social-
  handle prose).
* Only specific PROSE fields get substituted (see
  `builders.PROSE_FIELDS_BY_TYPE`); structured fields like `title`,
  `grant_number`, `course_number` do NOT scan for refs even though the
  string parser would technically match.

**Implementation:** `cli.main` builds a global `ref_index` AFTER all lists
are sorted (so the C.X.Y assignment is final), then calls
`resolve_refs_in_list` on each list. For sections with non-trivial sort
order (student awards: level + tier + year-DESC), the sort logic lives in
ONE place — `rtf.index_student_awards` — and is used by BOTH the cli's
ref-index builder AND the renderer. Don't duplicate the sort.

**Bib citation keys are first-class refs.** Every BibTeX entry's citation
key (`davis2019testing` in `@inproceedings{davis2019testing, ...}`) is
registered in `ref_index` automatically, alongside YAML `id` fields. So
`@davis2019testing` in a YAML prose field resolves to whatever C.X.Y the
paper landed at. The two namespaces share one global ref space; a YAML
`id` that collides with a bib key is a fatal error pointing at both
sources.

**Refs render as clickable RTF hyperlinks**. The flow:

1. `builders.resolve_refs(..., link_format=True)` substitutes each `@id`
   with `\x01CODE\x02` sentinels (cli.py uses `link_format=True`).
2. The sentinels are <0x80 and pass through `escape_rtf` /
   `rtf_escape_unicode` unchanged.
3. Each paragraph renderer wraps its leading code emission via
   `_ref_anchor(f'{code}.{idx}')` → `{\*\bkmkstart C_18_1}C.18.1{\*\bkmkend C_18_1}`.
4. After streaming all renderers into a `StringIO` buffer, `write_rtf`
   runs `_finalize_ref_hyperlinks(buf.getvalue())` to convert sentinels
   into `{\field{\*\fldinst HYPERLINK \\l "C_18_1"}{\fldrslt \cf1\ul C.18.1\ul0\cf0}}`,
   then writes the final string to disk.
5. In Word, the resolved code displays as blue+underlined text;
   Ctrl-click (Cmd-click on macOS) jumps to the corresponding section.

**Single source of truth for C.X.Y cross-refs.** Use `_code_link(code)`
(in `rtf.py`) WHENEVER you emit a `C.X.Y` text that's a reference to
another section's entry. The helper returns the sentinel-wrapped form
that `_finalize_ref_hyperlinks` converts to a clickable, styled
hyperlink at write time. Five emission sites currently use it:

| Site | Renderer | What it emits |
|---|---|---|
| Citation back-ref | `render_citation` | `(see {code})` for CVE → paper |
| Citation key-work cross-link | `render_citation` | `(listed as {code})` |
| Key Work canonical link | `render_key_work_citation` | `(listed as {code})` |
| Conference presentation paper | `render_conference_presentation` | `Associated with publication {code}` |
| Undergrad product back-ref | `render_undergrad_products_section` | `Paper {code}` / `Book chapter {code}` |
| Technology-transfer cited pubs | `render_technology_transfer_section` | comma-joined `cited_publications` cell |

Adding a new C.X.Y emission site? **Use `_code_link(code)` from day
one.** Plain text C.X.Y refs are silent regression bugs — they render
fine but lose clickability + miss the brand-aligned blue+underline
styling. A test that pins styled output would catch the regression
(see `TestE2eSectionsFilter` for the cross-ref-survives-styling
pattern).

**Bookmark naming convention.** Bookmark names use `_` in place of `.`
(RTF spec restriction: bookmark names are limited to alphanumerics +
underscore, 40 chars max). The `_ref_anchor` helper and the
`_finalize_ref_hyperlinks` post-pass MUST agree on this sanitization —
both call `code.replace(".", "_")`. The
`TestRefAnchorAndHyperlinkFinalize::test_bookmark_name_matches_hyperlink_target`
test pins this contract.

**Table-section entries do NOT currently have bookmarks.** C.14, C.15,
C.16 (student tables), C.17, C.19 (patents), C.21 (technology
transfer table) emit rows without per-row `C.X.Y` text and so don't
call `_ref_anchor`. An `@id` pointing at one of those entries will
still render as a hyperlink, but clicking it falls through (broken
internal link). If you need refs into tables, the renderer for that
section needs to add a hidden bookmark on each row's first cell.

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

## RTF — spec mirror + cribsheet

Local cache + extracted notes live in [`docs/rtf-spec/`](docs/rtf-spec/):

- [`NOTES.md`](docs/rtf-spec/NOTES.md) — **read this BEFORE writing
  new RTF code**. Cribsheet of the 9 rules I've learned the hard way:
  `\pard\intbl` per cell, per-cell border block, control-word
  delimiter-eating-spaces (brace-scope fix), bookmark naming,
  internal hyperlink `\\l` flag, `\cs1` Hyperlink character style for
  Word copy-paste preservation, page-range normalization, etc.
- `rtf15-biblioscape.html` — RTF 1.5 spec mirror (canonical reference)
- `latex2rtf-rtfspec-7.html` — Document Area section with the
  tables-and-`\intbl` rule
- `pindari-rtf3-tables.html` — Hands-on table tutorial

When future-me hits a rendering bug, start with NOTES.md's "Common
gotchas" table. If the bug isn't in there, dig into the cached spec
HTMLs locally (don't re-fetch — the URLs are stable but offline use
is faster + reproducible).

## RTF table emission — minimum required syntax

Every table renderer (`RtfTable._render_row`, `_render_student_*`,
`render_postdocs_section`, `_format_grant_table`) MUST emit
`\pard\intbl` before each cell's content. This is non-negotiable per
the [RTF 1.5 spec](https://www.biblioscape.com/rtf15_spec.htm) /
[latex2rtf reference](https://latex2rtf.sourceforge.net/rtfspec_7.html):

> Every paragraph that is contained in a table row must have the
> `\intbl` control word specified or inherited from the previous
> paragraph.

Without `\pard\intbl` per cell:
* TextEdit renders the row as inline text and the `\cell` separators
  collapse into a single continuous line (`Wenxin JiangPhD`,
  `TitleCo-Inventors`).
* Word displays *something* but pasting the table from TextEdit → Word
  does NOT produce a real Word table — the `\cell` markers vanish.

The canonical per-row pattern (cribbed from latex2rtf):

```
\trowd\trgaph108\trleft0
\clbrdrt\brdrs\brdrw15\clbrdrb\brdrs\brdrw15\clbrdrl\brdrs\brdrw15\clbrdrr\brdrs\brdrw15\cellx1800
\clbrdrt\brdrs\brdrw15\clbrdrb\brdrs\brdrw15\clbrdrl\brdrs\brdrw15\clbrdrr\brdrs\brdrw15\cellx9360
\pard\intbl Cell A\cell
\pard\intbl Cell B\cell
\row
```

Per-cell formatting (e.g., right-align `\qr` on the grant amount cell)
comes AFTER `\intbl` on the same paragraph:
`\pard\intbl\qr $123,456\cell` — no need for a closing `\ql` because
the next cell's `\pard` resets paragraph alignment.

**Cell borders** are a separate must-have. The 4-side border block
`_CELL_BORDER_BLOCK` (`\clbrdrt\brdrs\brdrw15\clbrdrb...\clbrdrr...`)
goes BEFORE each `\cellx` position in the row definition section.
Without per-cell borders, viewers don't draw vertical separators
between cells even when `\intbl` is correct.

**Multi-row tables with merged divider rows** (the C.14 tier-divider
case) must use the SAME `\cellx` positions as the data rows, with the
first cell carrying `\clmgf` (merge-first) and the rest carrying
`\clmrg` (merge-continuation). If consecutive rows have different
`\cellx` structures, TextEdit / Word visually collapse them into one.

## Things that look weird but are intentional

- **YAML schemas are renderer-neutral, NOT LaTeX-shaped.** The tenure
  packet has a LaTeX version too, but YAML data does NOT mimic LaTeX
  macro argument shapes. Structured fields beat positional free-form
  strings — `GrantPerson` is a NamedTuple with `name` / `role` /
  `department` / `institution` / `nsf_award`, not "first arg is name,
  third is dept". RTF and LaTeX are presentation choices over the same
  structured data. If a field "feels like a LaTeX argument," redesign
  it as a typed field instead.
- **"Empty list emits 'N/A'" is a three-section pattern, not section-
  specific.** Sections that fall in this bucket (currently C.15
  postdocs, C.20 entrepreneurial activities, C.21 technology transfer)
  are MANDATORY in the Purdue packet even when no entries exist — the
  section must appear with "N/A" so reviewers know it was considered,
  not silently dropped. Distinct from the more common "empty list →
  skip section entirely" pattern (C.6/C.7/C.22/etc.) and from C.16.2.3
  "empty → skip" (heading would be misleading without entries). When
  in doubt: the renderer is the source of truth; check what
  `if not <list>: return` vs `if not <list>: emit "N/A"` says.
- **Section codes can have multiple dots (C.16.2.3, C.16.2.4, C.16.3.3).**
  The renderer treats them as opaque strings — `SECTION_CODES[section]`
  emits whatever's there. Entry numbering appends `.N`, so a section at
  `C.16.2.3` produces `C.16.2.3.1`, `C.16.2.3.2`, … with 4 dot levels.
  Don't try to "validate" the code shape; it's intentionally flexible
  to map to whatever Purdue's template wants.
- **Two-section split for the same logical concept (student awards).**
  `student_awards` is ONE YAML list with per-entry `level: U` or
  `level: G`. The renderer `render_student_awards_section(section_key,
  awards, out)` is called TWICE — once with `"Undergraduate Student
  Awards"`, once with `"Graduate Student Awards"` — and each call
  filters the shared list by its expected level (via `_SECTION_TO_LEVEL`).
  This shape generalizes if more level-routed sections appear later
  (e.g., grad research products). Don't bifurcate the YAML into
  `undergrad_awards:` + `grad_awards:` — it duplicates the schema, and
  the filter-at-render is cheap.
- **Auto-derived sections coexist with YAML-authored sections.** C.16.2.3
  is built in `cli.py` AFTER `paper_index` is materialized (because each
  record's `ref` field is a back-pointer into paper_index). `build_undergrad_products`
  scans every emitted citation's bib `author` field, calls
  `lookup_student_type` per author, and emits `UndergradProduct` records.
  If you want a new auto-derived section, the pattern is: build it in
  the cli `# Back-pointer index` section AFTER paper_index, pass into
  write_rtf as a kwarg, render with a normal renderer. The renderer
  itself doesn't need to know it came from the bib.
- **Structural matcher prefix direction is canonical-initials.startswith(bib-initials),
  NOT the inverse.** Means a single-initial canonical entry like
  `"J Bushagour"` does NOT match bib `"Bushagour, J.R."` (canonical
  initials "J" do not start-with "JR"). Fix is to give the canonical
  entry at least as many initials as the bib uses — `"Joseph R Bushagour"`
  → canonical initials "JR" → match. When adding `students.U` / `students.G`
  entries, prefer the full name from the bib over an abbreviated form.
  Symptom of the trap: the name shows up with `\super *\nosupersub`
  (last-author marker) instead of `\super U\nosupersub` / `\super G\nosupersub`.
- **Literal `$` in YAML descriptions must be written as `\\$` (double-
  quoted) or `\$` (single-quoted / block scalar).** All YAML free-form
  text fields flow through `decode_latex` for unified LaTeX-escape
  handling, and pylatexenc treats bare `$` as math-mode delimiter and
  strips it (or eats the surrounding content). Canonical incident:
  C.18 entry rendered "supported by a 150K grant" instead of "$150K".
  Fix at the YAML, NOT in decode_latex — the math-mode handling is
  load-bearing for any bib entries that legitimately use math.
  Symptom: a literal `$N` substring in YAML disappears (or surrounding
  text gets eaten) in the rendered RTF. Other LaTeX-special chars
  follow the same rule: `\&`, `\%`, `\#`, `\_` (escape with `\\` in
  YAML double-quoted, with a single `\` in YAML single-quoted).
- **C.20 / C.21 schemas exist NOW, populate LATER.** Both are tagged
  for promotion-to-full-professor and intentionally empty in
  `non-scholar-work.yaml` (rendered as "N/A"). The schemas are pinned
  in `types.py` so populating later is just YAML edits, no code change.
  Don't delete the empty-list stubs — the renderer hits the `N/A`
  branch from a missing key the same way, but the explicit stub +
  comment documents the schema for future-self.
- **Sole-PI grants render a "Sole PI" personnel row, not a blank.** The
  C.10-C.13 table emits 4 rows unconditionally — the absence of co-PIs is
  a positive fact to surface, not a missing data point to suppress. Tests
  pin this shape (`TestGrantTableStructure`).
- **`role=""` on `GrantPerson` is the non-standard-framing escape hatch.**
  Renders the name verbatim with no `Co-PI:` / `NSF #...:` prefix.
  Canonical case: Qualcomm "Project Supervisor" framing (an industry
  contact who isn't a standard PI/Co-PI/Co-I). Keep this rare — prefer
  structured roles when they fit.
- **Half-dollar grant amounts are rounded up to int in YAML.** The schema's
  three USD fields (`total_amount`, `purdue_amount`, `my_amount`) are
  `int`, so e.g. $90,723.50 stores as `90724`. LaTeX preserves the
  decimal (different source). If reviewers ask, the rounded number is
  authoritative for RTF; the LaTeX figure is the same to within 50¢.
- **`my_amount` is always shown, never collapsed.** Even when it equals
  `purdue_amount`, the renderer emits the "Davis's share" line. Reviewers
  read this to assess scope of personal contribution; collapsing the line
  when "redundant" loses signal.
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

  **Decisions to make up front:**
  * **Empty-list policy.** Pick one explicitly: (a) silent skip (most
    sections), (b) emit heading + "N/A" (C.15/C.20/C.21 — mandatory-
    section pattern), or (c) auto-derived from another source (C.16.2.3).
    Pin the choice in a test.
  * **Auto-derived from bib?** If yes: the builder takes `conn`,
    `publications`, `paper_index`, and `bib_entries` and runs AFTER
    `build_paper_index` in `cli.main`. The output type's `ref` field
    holds the back-pointer; the renderer doesn't need to know about
    paper_index. See `build_undergrad_products` for the canonical shape.
  * **Level-routed (same data, multiple sections)?** Add one entry type
    with a `level` (or analogous) field, define ONE renderer that takes a
    `Section` key and filters by that field via a `_SECTION_TO_LEVEL`
    map, then call the renderer once per section from `write_rtf`. See
    `render_student_awards_section` for the canonical shape.
- **New API source?** Add a `try_X(...)` in `network.py`. Add the host to
  `_HOST_LIMITERS`. Add a new `kind` and cache table if needed, then update
  `plan_lookups` + `commit_results` + a `fetch_X` in `lookup.py`.

## Section-by-section rendering

The `--sections` CLI flag emits only the requested top-level / child
section codes. Useful for spot-checking ONE section in Word without
re-rendering the whole packet (and for keeping e2e tests focused —
several `TestE2eSectionsFilter` cases use it).

```bash
# Single section
.venv/bin/python pubs-emitter.py --bib assets/my_papers_full.bib \
    --non-scholar assets/non-scholar-work.yaml \
    --sections C.4 --out /tmp/c4-only.rtf

# Multiple sections — comma-separated
.venv/bin/python pubs-emitter.py --bib assets/my_papers_full.bib \
    --non-scholar assets/non-scholar-work.yaml \
    --sections C.10,C.11,C.12,C.13 --out /tmp/grants-only.rtf

# Parent code includes children automatically: --sections C.16 emits
# C.16 + C.16.2.3 + C.16.2.4 + C.16.3.3 (any code whose dotted form
# starts with "C.16.")
.venv/bin/python pubs-emitter.py --bib assets/my_papers_full.bib \
    --non-scholar assets/non-scholar-work.yaml \
    --sections C.16 --out /tmp/mentoring.rtf
```

**Critical invariant — `--sections` does NOT change C.X.Y numbering.**
The build still COMPUTES every section before filtering emission, so
the C.X.Y back-pointers (paper_index, `@id` resolution, key-work cross
links, CVE → paper "(see C.X.Y)" markers) all match the full-document
build exactly. Pinned by
`TestE2eSectionsFilter::test_filter_preserves_full_document_numbering`.

The filter logic lives entirely inside `write_rtf` via a closure
`_emit(section_key) -> bool`. Default `sections_filter=None` → emit
everything; otherwise a section emits iff its code (from
`SECTION_CODES`) matches a filter code exactly OR a filter code is a
dotted-prefix of it (the parent → children rule).

## `@id` ref validation

The `@id` cross-reference system (see `## @id cross-references in YAML
prose` above) is gated by `validate_non_scholar` at load time + the
ref-resolution pass after `build_paper_index`. Three test classes pin
the gate:

- **Unit**: `TestResolveRefs` (`tests/test_builders.py`) — the
  `resolve_refs` helper returns `(text, unresolved_ids)`; unresolved
  IDs surface in the returned list.
- **End-to-end failure**: `TestE2eUnresolvedAtIdRef`
  (`tests/test_e2e.py`) — `cli.main` exits with code 1 + logs the
  offending id name when a YAML prose field contains an unresolved
  `@id`. Covers both `course_development` and grant `responsibility`
  prose; positive control verifies self-refs work.
- **End-to-end success**: every passing `e2e_outputs` test exercises
  the resolution path (the live fixture YAML contains valid `@id`
  refs that auto-resolve to their target codes).

When adding a new YAML field that should scan for refs, extend
`builders.PROSE_FIELDS_BY_TYPE` and add a focused test asserting both
the success and failure paths.

## Quality gates

```bash
.venv/bin/pylint src/pubs_emitter   # 9.95/10 baseline
.venv/bin/mypy                      # 0 errors baseline
.venv/bin/pytest                    # 341 tests baseline
.venv/bin/python pubs-emitter.py \
    --bib assets/my_papers_full.bib \
    --non-scholar assets/non-scholar-work.yaml \
    --out /tmp/smoke.rtf            # full smoke (the bare invocation reads
                                    #  the bib only — pass --non-scholar to
                                    #  exercise grants/talks/students/C.22)
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
