# pubs-emitter

Generates a formatted RTF publication list for a tenure packet, driven by
two files:

- A **BibTeX file** exported from Google Scholar (publications + patents).
- A **YAML side file** for everything Scholar doesn't track — invited
  talks, leadership roles, media appearances, conference presentations,
  grants, students, CVEs, security disclosures, key works.

Output is ready to paste into Word with formatting preserved: bold for
you, superscripts for student / advisor / last-author roles, italic
venue + tier, clickable hyperlinks to DOIs / NVD / USPTO / publisher
pages, hanging-indent layout, RTF tables for patents + student lists.

## Why it's set up this way

1. **BibTeX is the canonical publication store.** This repo assumes you
   are using Google Scholar as the master reference for your publications,
   and that you use the *"James Davis (TM)" venue notation*: prefix every
   `journal` / `booktitle` with `[ACRONYM'YY]`. The acronyms are
   required because Purdue wants to see venue rankings in the packet,
   and the acronym is what we look up in `assets/config.yaml`.
2. **Everything else lives in `non-scholar-work.yaml`.** Talks, grants,
   students, CVEs, etc. — Scholar can't carry them, so we keep them in
   one hand-edited YAML file.
3. **Cross-references are automatic.** CVE entries link back to the
   originating paper (`(see C.4.7)`); Key Works cite the paper's
   canonical location (`(listed as C.4.7)`); the conference-presentation
   section name-drops the linked paper's section ref; student tables
   auto-populate the "Related Publications" column from the bib.

> **Tip:** A GenAI tool --- Codex, Claude, etc. --- can help you populate
> `non-scholar-work.yaml` from your CV. Just give it screenshots of each
> section.

## Quick start

```bash
./setup.sh                                          # one-time: venv + editable install + dev extras
./pubs-emitter.py --bib my_papers.bib \
                  --non-scholar non-scholar-work.yaml
```

Or once `setup.sh` has run, the shorter form works from anywhere in the venv:

```bash
source .venv/bin/activate
pubs-emitter --bib my_papers.bib --non-scholar non-scholar-work.yaml
```

This writes `publications.rtf`. Open it in Word, then copy → "Paste
Special → Unformatted Text" into your tenure-packet template if you
want it to inherit the host doc's font.

## Layout

```
publications/
├── pubs-emitter.py            # root driver (delegates to src/pubs_emitter/cli.py)
├── setup.sh                   # one-command bootstrap (venv + editable install)
├── pyproject.toml             # build + pylint + mypy + pytest config + project deps
├── README.md
├── CLAUDE.md                  # editor-facing notes; non-derivable rules + pitfalls
├── .gitignore
├── assets/
│   ├── config.example.yaml    # committed schema + starter venue rankings
│   └── config.yaml            # ME, ADVISORS, STUDENTS, RANKS (gitignored — your data)
├── src/
│   └── pubs_emitter/
│       ├── __init__.py
│       ├── types.py           # NamedTuples + type aliases
│       ├── config.py          # loads assets/config.yaml + code-side constants
│       ├── latex.py           # decode_latex + rtf_escape_unicode
│       ├── db.py              # SQLite cache + LOOKUP_STATS
│       ├── network.py         # RateLimiter, polite_get, try_{crossref,dblp,nvd,patentsview}
│       ├── authors.py         # name parsing + format_author / format_inventors
│       ├── venue.py           # parse_venue, lookup_rank, classify_entry, ID extractors
│       ├── lookup.py          # plan / dispatch / commit + cache-aware fetchers
│       ├── builders.py        # build_* + load_non_scholar + validate_non_scholar
│       ├── rtf.py             # RtfTable, render_*, write_rtf
│       └── cli.py             # parse_args + main()
└── tests/                     # pytest; ~190 tests, full coverage of the public API
    ├── conftest.py
    ├── fixtures/
    │   ├── config.yaml
    │   ├── sample.bib
    │   └── non-scholar.yaml
    └── test_*.py
```

The bib (`my_papers.bib`), the YAML (`non-scholar-work.yaml`), the live
user config (`assets/config.yaml`), the cache (`lookup_cache.sqlite`),
and the output (`publications.rtf`) are all gitignored. Only the example
config (`assets/config.example.yaml`) is committed.

## Pipeline

`main()` runs five phases:

1. **`plan_lookups`** — walks every entry + CVE, emits `NetworkTask`s for
   cache misses only.
2. **`dispatch_parallel`** — runs all tasks concurrently via
   `ThreadPoolExecutor`. Each host has its own `RateLimiter` (Crossref
   ~10/s, DBLP ~5/s, NVD 5/30s without key or 50/30s with, PatentsView
   ~2/s). Retries transient HTTP failures with exponential backoff
   (1s, 2s, 4s) and honors `Retry-After` headers.
3. **`commit_results`** — persists results into the appropriate cache table
   (`doi_cache`, `patent_cache`, `cve_cache`).
4. **`build_*`** — assembles `Citation` / `Patent` / `KeyWork` /
   `InvitedTalk` / `LeadershipRole` / `MediaAppearance` /
   `ConferencePresentation` / `Grant` / `Student` records. By now the
   cache is warm; build is local-only.
5. **`write_rtf`** — emits the final RTF, sections rendered in
   `SECTION_ORDER`, with cross-reference indexes built between sort and
   render.

## BibTeX conventions

- **Citations** — `journal` / `booktitle` MUST begin with a bracketed
  acronym + year tag. The acronym is looked up in `assets/config.yaml`
  under `ranks:`. Examples:
  ```
  journal   = {[JSS'25] The Journal of Systems and Software}
  booktitle = {[ICSE'25] Proceedings of the International Conference on Software Engineering}
  journal   = {[arXiv'26] arXiv preprint arXiv:2605.10712}
  ```
- **Patents** — `@misc` whose `publisher` or `note` contains `patent`.
  `note = {US Patent 11,176,090}` carries the number; USPTO date lookup is
  attempted via PatentsView when `PATENTSVIEW_API_KEY` is set.
- **Book chapters** — `@incollection` or `@inbook`. No bracket-tag
  needed (no venue field for it). DOI / URL comes from `manual_links:`
  in `assets/config.yaml`.
- **Theses** — `@phdthesis` / `@mastersthesis`. Built internally but not
  emitted in any section yet — held for future cross-references.
- **CVEs are NOT in the bib.** Bib stays Scholar-canonical. CVEs go in
  `non-scholar-work.yaml`.

## Output sections

The renderer always emits sections in this order (skipped if the
corresponding YAML / bib source is empty):

| Code | Heading | Source |
|------|---------|--------|
| C.1 | Key Scholarly Publications or Patents | `key_works:` in YAML |
| C.2 | Journals | bib `@article` |
| C.3 | Books and chapters in books | bib `@incollection` / `@inbook` |
| C.4 | Conferences and Workshops | bib `@inproceedings` |
| C.5 | Other publications and products | bib arXiv + magazines + YAML `cves:` + `security_disclosures:` |
| C.6 | Invited Talks | `invited_talks:` |
| C.7 | Leadership Roles | `leadership_roles:` |
| C.8 | Media Appearances | `media_appearances:` |
| C.9 | Conference Presentations | `conference_presentations:` |
| C.10 | Externally sponsored grants as PI | `grants_as_pi:` |
| C.11 | Externally sponsored grants as Co-PI or Co-I | `grants_as_co_pi:` |
| C.12 | External gifts and voluntary support | `gifts:` |
| C.13 | Internal competitive grants as PI or Co-PI | `internal_grants:` |
| C.14 | Graduate students advised | `graduate_students:` (RTF table) |
| C.16 | Undergraduate students advised | `undergraduate_students:` (RTF table) |
| C.19 | Issued U.S. and International Patents | bib `@misc` with `note = US Patent ...` (RTF table) |

Within each section, entries are sorted chronologically (oldest first).
For peer-reviewed venues (C.2, C.4), the tier marker renders inline as
`Venue rank: Tier 1` / `Tier 2` / etc. — italicized + underlined. Other
sections use a bare label (`Preprint`, `CVE`, `Magazine`).

Grant sections C.10–C.13 each render a `Total amount of ...: $X,XXX,XXX`
line above the numbered list.

## Non-Scholar YAML (`--non-scholar`)

The YAML file is one big map; each top-level key drives one or more
sections. Every section's schema is validated at load time; ALL errors
are batched into one report (no first-fail crashes).

```yaml
key_works:
  - paper_title: Engineering Patterns for Trust and Safety on Social Media Platforms
    impact: |
      A 100-word-ish impact statement on why this paper matters.

cves:
  # CVE attached to a paper. C.5 entry inherits the paper's author list
  # (with student markers) and gets a "(see C.4.N)" back-pointer.
  - cve_id: CVE-2024-38373
    organization: FreeRTOS                              # optional; auto-derived from NVD's CPE
    paper_title: Engineering Patterns for Trust and Safety on Social Media Platforms

  # Stand-alone CVE — no associated paper. Provide the disclosers explicitly.
  - cve_id: CVE-2025-1675
    organization: Zephyr-RTOS
    disclosers:
      - Davis, James C
      - Amusuo, Paschal C

security_disclosures:
  # Vendor-acknowledged disclosure with no CVE assigned. Always linked to a paper.
  - paper_title: Engineering Patterns for Trust and Safety on Social Media Platforms
    vendor: VendorName
    description: One-sentence description of the disclosure.
    year: 2024

invited_talks:
  - topic: Regular Expression Denial of Service
    subtitle: ""                                        # optional
    venue: Some University, City
    year: 2024

leadership_roles:
  - role: Co-Chair
    description: The 12th International Workshop on X
    society: ACM SIGSOFT
    year: 2023

media_appearances:
  - title: How software supply chains break
    venue: Some Podcast
    year: 2024
    url: "https://example.com/episode"

conference_presentations:
  # Links back to the bib via paper_title. Renders as
  # "Talk at <venue> in <year>. Associated with publication C.4.N."
  - paper_title: Engineering Patterns for Trust and Safety on Social Media Platforms

grants_as_pi:
  - title: NSF CAREER PROJECT
    agency: US National Science Foundation
    agency_short: NSF
    grant_number: "2541917"
    role: PI
    start_year: 2025
    end_year: 2030
    amount: 600000
    activities: Optional multi-sentence description.
    responsibility: Optional free-text role/percent statement.
    # Validated against the bib (titles must match) but NOT rendered in
    # C.10. Reserved for C.1 Key Works cross-link wiring (TODO).
    inspired_by: []
    publication_outcomes: []

grants_as_co_pi: []
gifts: []
internal_grants: []

graduate_students:
  - name: Paschal C. Amusuo
    degree: PhD                                          # PhD / DEng / MS-Thesis / MS-Non-Thesis
    role: Chair                                          # Chair / Co-Chair / Committee member
    grad_year: 2025                                      # 9999 for ongoing; sorts to bottom
    graduation: "2025 Spring"                            # display label
    position: Software Engineer @ Some Co.               # optional

undergraduate_students: []
```

**Validation rules** (all checked at YAML load time, batched into one report):

- `cves[].cve_id` required; must look like `CVE-YYYY-NNNN`.
- Each `cves[]` entry must have at least one of `paper_title` or `disclosers`.
- Every `paper_title` across `cves` / `security_disclosures` /
  `conference_presentations` / `key_works` AND every title in each grant's
  `inspired_by` / `publication_outcomes` MUST match a bib entry
  (case + whitespace-insensitive).
- Grants must have `title`, `agency`, `role`, `start_year`, `end_year`,
  `amount`. `agency_short` is optional — when present it prefixes the
  bolded head line as `{agency_short}[ #grant_number]: {title}`; when empty
  the head renders as just the title (use this for fellowships and internal
  Purdue programs that don't carry a canonical funder-name prefix).
- Students must have `name`, `degree`, `role`, `grad_year`.
- Invited talks must have `venue`, `year`, and at least one of `topic` /
  `subtitle`.

A failed validation prints every error and exits 1 — fix them all and re-run.

## Environment variables

| Variable | Effect |
|----------|--------|
| `LOG_LEVEL` | `DEBUG` / `INFO` (default) / `WARNING` / `ERROR` |
| `PATENTSVIEW_API_KEY` | Enables USPTO issue-date lookup for patents |
| `NVD_API_KEY` | Raises NVD rate limit ~10x (5/30s → 50/30s) |
| `PUBS_EMITTER_CONFIG` | Override path to `assets/config.yaml` |
| `PUBS_EMITTER_USER_AGENT` | Override the HTTP User-Agent (mailto:) |

## Dev tools

```bash
.venv/bin/pylint src/pubs_emitter    # 9.95/10 baseline
.venv/bin/mypy                       # type-clean
.venv/bin/pytest                     # ~190 tests, sub-second
```

Pylint, mypy, and pytest configs all live in `pyproject.toml`. The
pylint config disables the noisy style/code-org categories
(`missing-docstring`, `too-many-arguments`, etc.) but keeps every
substantive check (unused imports, broad except, dangerous default
value, etc.).

### Test suite

The test suite is structured as one file per package module
(`test_latex.py`, `test_venue.py`, …) plus `test_e2e.py` which drives
`cli.main` end-to-end with the three network entry points monkey-patched.
No real HTTP, no real network. Fixtures live in `tests/fixtures/`:

- `config.yaml` — minimal config used by all tests (loaded via
  `PUBS_EMITTER_CONFIG` set in `conftest.py`).
- `sample.bib` — one of each entry kind (article / inproceedings /
  arXiv / patent / incollection / phdthesis), including a Çakar
  coauthor for Unicode-escape coverage.
- `non-scholar.yaml` — one entry under every YAML key.

Run a single file with `pytest tests/test_latex.py`; a single class with
`pytest tests/test_rtf.py::TestRtfTable`; a single test with `pytest
tests/test_e2e.py::TestE2eContentInvariants::test_unicode_round_trip_through_rtf_escape`.
