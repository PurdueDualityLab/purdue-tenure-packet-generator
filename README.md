# purdue-tenure-packet-generator

Generate the **scholarly contributions, self-evaluation, and general-
information** portions of a Purdue tenure packet from a small set of
editable text files.

You bring: a BibTeX export of your publications, a CV, and a roster of
your students + advisors + grants. The tool builds the formatted tables,
links every paper to its DOI + page numbers (auto-fetched from
Crossref), annotates every author with their role (your student? a
senior advisor? you?), and emits a single RTF file you can use directly
as your packet or copy into the official Purdue Word template.

## Who this is for

You're an Assistant Professor at Purdue going up for tenure. Your CV is
in shape. You're now staring at the Purdue P&T template and realizing
you have to:

* Number every paper as `C.X.Y` and reference it by that number in your
  self-evaluation prose.
* Build tables for grants, students, courses-taught, patents, software
  products, technology transfer, …
* Mark every author of every paper to indicate whether they were your
  student (with superscript `G` for graduate, `U` for undergraduate),
  your advisor (`#`), the corresponding author (`*`), or you (bold).
* Look up the DOI for every paper that has one — and the page numbers,
  if the bib entry is missing them.
* Embed clickable hyperlinks so the reviewer can follow each paper
  citation, NSF award number, USPTO patent number, LinkedIn profile,
  etc.
* Re-do all of the above **every time you add a new paper, win a new
  grant, or graduate a student** — because the cross-refs cascade.

Doing this by hand in Word is the **40–80 hour soul-crushing** part of
preparing a tenure packet. This tool collapses it to **~5 hours of
data entry** (mostly automated by a chat-based AI working from
screenshots of your CV), and then the rebuild on every subsequent
change is **~3 seconds**.

The Purdue P&T template ships the *structure* of a tenure packet. This
tool ships **everything else** — the data substrate, the cross-ref
machinery, the table generation, the author-annotation lookup tables,
the venue-rank database — so all you have to write by hand is the
prose (B.1–B.5 self-evaluation).

## Two ways to use the output

The build emits one RTF file. You have a choice in how to use it:

### Option 1: Use the RTF directly as your packet (recommended)

The RTF is fully formatted — title styles, heading levels (so Word's
TOC scanner picks up the structure), tables with borders, hanging
indents, hyperlinks, bookmarks. **Open it in Word and submit it as your
packet.** The tool produces output that matches the Purdue template's
visual conventions out of the box.

### Option 2: Use it as a "table + reference builder" inside the Purdue Word template

Some candidates prefer to **own the master document in Microsoft
Word** — typing prose directly into the official template, dragging in
formatted blocks from elsewhere. If that's you:

1. Run this tool to produce the RTF.
2. Open the RTF in Word.
3. **Copy the formatted blocks you want** (the C.10 grants table,
   the C.19 patents table, the C.14 graduate-students table, the
   numbered C.4 conferences list, etc.) and **paste them into your
   master Word document**.
4. Write your self-evaluation (B.1–B.5) prose directly in the master
   Word document.
5. **Cross-references become your problem** in this mode — Word's
   hyperlinks copy across cleanly, but adding a new paper means
   re-running the tool, re-copying the regenerated section, and
   updating any prose that references it. The build still validates
   that every `@id` reference in your YAML resolves, and surfaces
   missing-page-number / unknown-author-role warnings — so even in
   this mode you get the data hygiene wins.

Both modes work. Option 1 is faster end-to-end; option 2 keeps you in
the Word ecosystem if that's where you're most comfortable.

## What the tool does for you, beyond formatting

The **time-consuming** parts of preparing a packet aren't the headings
or fonts — Word handles those. The time-consuming parts are:

* **Building tables.** Grants, students, courses, patents — these are
  multi-column tables with consistent formatting requirements.
  Generating them from a YAML list takes ~10 ms; doing them by hand
  takes hours and they're brittle to every later edit.
* **Annotating students + advisors + corresponding authors.** Every
  citation needs superscript markers showing who was whom. Doing this
  by hand means cross-referencing your student roster against every
  author list, every time you add a paper. The tool does the lookup
  automatically from `assets/config.yaml`'s `students:` and `advisors:`
  blocks.
* **Looking up DOIs and page numbers.** Most candidates have papers
  whose bib entries are missing page numbers or DOIs — and the
  Purdue template wants them. The tool **auto-fetches missing DOIs +
  page numbers from Crossref** at build time (cached in SQLite so
  re-runs are instant), and embeds them as clickable hyperlinks.
  Patent issue dates come from USPTO via PatentsView; CVE descriptions
  from NVD.
* **Numbering cross-refs.** When you add a new C.4 paper, every
  reference to a later C.4 paper in your prose shifts by one. The
  tool re-numbers the whole document automatically and updates every
  `@id` reference in your self-evaluation prose to match.

This is the time-consuming part of a tenure packet, and this is what
the tool eliminates.

> **Tested on macOS only** (Davis's dev box). Should be portable to
> Linux — `setup.sh` uses standard `python3 -m venv` + `pip install`.
> Windows is untested.
>
> **Tested against the Purdue tenure-doc format as of May 2026** —
> Section III General Information (A.1–A.7), Section IV Self-Evaluation
> (B.1–B.5), Section V Scholarly Contributions (C.1–C.26 + appendix
> with under-review and pending proposals). When the Purdue template
> updates (it has revised wording a few times), check that
> `SECTION_HEADINGS` in
> [`src/pubs_emitter/config.py`](src/pubs_emitter/config.py) still
> matches the live template heading text before submitting.

The system covers:

| Section | What lands here | Driven by |
|---|---|---|
| **III** General Information (A.1–A.7) | name + IDs, degrees, positions, licenses, awards, memberships | `candidate-information.yaml` |
| **IV** Self-Evaluation (B.1–B.5) | summary, impact, vision, external-events note, COVID statement | `self-evaluation.md` |
| **V** Scholarly Contributions (C.1–C.26 + appendix) | publications, talks, grants, students, courses, services + appendix (V.A.1 under-review, V.A.2 pending proposals) | `my_papers.bib` + `non-scholar-work.yaml` + EvaluationKit CSVs |

## What's automatic, what's yours

| You do this | The tool does this |
|---|---|
| Export BibTeX from Google Scholar (one-time + delta on each new paper) | Parse the bib, classify each entry, pull missing DOIs + page numbers from Crossref, build the C.1–C.5 sections |
| Tag each Scholar entry with its venue acronym (`[ICSE'25]`) | Look up the venue's tier from `assets/config.yaml`, annotate Tier 1 entries with the underlined marker |
| List your students + advisors in `assets/config.yaml` | Annotate every author of every paper with the right superscript (`G` / `U` / `#` / `*` / bold-for-you) automatically |
| Write your self-evaluation prose (B.1–B.5) | Resolve every `@bibkey` reference in your prose to its rendered section code (e.g., `C.4.7`), emit a clickable hyperlink |
| Fill out YAML for grants, students, courses, patents, … | Build the multi-column tables; compute section-total funding amounts; number entries; cross-link to the source documents |
| Hand-curate the Section V appendix entries (`under_review:`, `status: pending`) | Route them to V.A.1 / V.A.2 with the appropriate Roman-numeral cross-ref form ("Section V, A.1.3") |

## Why this beats "just type into the template"

The Purdue P&T template gives you the **skeleton** of a tenure packet
— the headings, the heading levels, the front-matter layout. So does
this tool. The difference is in what happens between the headings:

* **The Purdue template is a static document.** Adding a paper means
  hand-formatting a citation, renumbering every later cross-ref,
  rebuilding the C.10 grants table to insert a new row, … and
  re-checking that your B.2 Impact statement still references the
  right paper number.
* **This tool is a build pipeline.** Adding a paper means editing one
  bib entry. The renumbering, the table rebuild, the cross-ref
  resolution, the auto-lookup of the new paper's DOI + page numbers
  from Crossref — all of that runs in ~3 seconds.

If you're going to maintain your tenure packet across the **2-3 years
of preparation + on-the-job updates**, "edit a structured database +
rebuild" wins over "edit a Word document" by orders of magnitude.

---

## Prerequisites

The system is opinionated about **what data you bring**:

### Hard requirements

1. **A structured publication store.** Recommended: **Google Scholar**
   as the master reference for your papers, exported to BibTeX. The
   exporter labels every venue with a bracket-tag (`[ICSE'25]`) you
   add yourself; the tag drives venue-rank lookup. See `BibTeX
   conventions` below.
2. **An up-to-date CV with equivalent fields to Davis's.** Use Davis's
   CV as a template — a read-only copy is available on Overleaf for
   forking:
   <https://www.overleaf.com/read/ccxmympbnmzn#136112>
   Items you'll want listed in your CV (the YAML / markdown substrate
   reads them directly):
   - publications (BibTeX from Scholar)
   - awards + recognitions (external + internal, with dates + significance)
   - grants (PI / Co-PI / gifts / internal, with personnel + amounts)
   - students supervised (graduates, undergraduates, with placements / LinkedIn)
   - postdocs + visiting scholars
   - invited talks + leadership roles + media appearances
   - service to Purdue / profession / state-and-nation / other
   - courses taught (EvaluationKit CSVs cover the score columns)
   - software products + patents + entrepreneurial activities + tech transfer
3. **Venue rankings.** A list of conference / journal acronyms → Tier 1/2/3
   / Workshop / Magazine, edited in `assets/config.yaml` under `ranks:`.
   Add a new venue once; every paper that cites it picks up the tier.
4. **A list of your students.** Names go in `assets/config.yaml` under
   `students.G` (graduate) and `students.U` (undergraduate). The
   author-rendering code adds a superscript `G` / `U` whenever it
   spots a student name in the bib's author list — no markup needed
   in the bib.
5. **A list of senior co-authors / advisors.** Names go in
   `assets/config.yaml` under `advisors:`. They get a superscript `#`
   marker in every citation.

### Soft requirements

- **PatentsView API key** for issue-date lookup on US patents (free at
  <https://patentsview.org/>). Without it, patents fall back to the
  bib's `note`-field date.
- **NVD API key** for CVE description lookup, 10× rate-limit raise
  (free at <https://nvd.nist.gov/>).
- **EvaluationKit raw-data + question-mapper CSVs** from Purdue's CIE
  system, if you want C.17 (Courses Taught) populated automatically.
  Without these, you populate `courses_taught:` by hand in
  `non-scholar-work.yaml`. **See "EvaluationKit export" below** for
  the exact click-path.
- **Crossref polite-pool mailto** (set via `PUBS_EMITTER_USER_AGENT`
  env var) — already configured to your email by default.

---

## Quick start

```bash
./setup.sh   # one-time: venv + editable install + dev deps
python3 pubs-emitter.py \
    --bib my_papers.bib \
    --non-scholar non-scholar-work.yaml \
    --candidate-info candidate-information.yaml \
    --self-eval self-evaluation.md \
    --evaluationkit-rawdata evaluationkit-rawdata.csv
```

Or once the venv is active:

```bash
source .venv/bin/activate
pubs-emitter --bib my_papers.bib  --non-scholar non-scholar-work.yaml
```

Defaults: all the asset-flag paths default to `assets/*` — pass an
empty string (`--candidate-info ""`) to skip a section entirely.

Output: `publications.rtf` (override via `--out`). Open in Word, then
**Paste Special → Unformatted Text** into your tenure-packet template
if you want it to inherit the host doc's font.

---

## Populating your data

All your tenure-packet inputs (CV facts, publications, course
evaluations, …) live in a small set of text files under `assets/`.
Most of the populating work goes through an AI assistant — you should
rarely need to read or write YAML by hand.

### The AI workflow

You should never be reading or writing YAML by hand. The ergonomic
path: **work with a chat-based AI model and feed it screenshots of
each part of your CV.**

Concretely, what Davis did:

1. Point a chat-based AI assistant (Davis used Claude) **at this git
   repository** so it has the YAML schemas + the renderer code in
   context. (Easiest: clone the repo locally and run the assistant in
   the same workspace, e.g. Claude Code or a Cursor / Copilot session.)
2. **Screenshot each section** of your existing CV — one section at a
   time (awards table, grants list, student roster, service list, etc.).
3. Tell the assistant: *"add these entries to my tenure-packet
   database — match the existing entries in `non-scholar-work.yaml` /
   `candidate-information.yaml`."* The assistant edits the files
   directly. You never touch the YAML.
4. Re-run the build. Validation errors come back in one batch — paste
   them to the assistant and ask it to fix them in the files.

Three inputs DON'T go through this workflow — they need a one-time
manual step on your part (described in the next three subsections):

- **Publications** — edit each entry on Google Scholar first
- **Course evaluations** — export EvaluationKit CSVs from Purdue's
  CIE system
- **Self-evaluation prose (B.1–B.5)** — plain markdown you write by
  hand; the prose is the human-judgment part of the packet

### Editing publications in Google Scholar

Your Google Scholar profile IS the master publication store. **For
each paper on Scholar:** click into the entry → **Edit** → prefix the
`journal` / `conference` field with the bracket-tag (`[ICSE'25]`,
`[FSE'24]`, etc.). Once every paper is tagged, **export the profile
as BibTeX**; the tags ride along into the bib file and drive
venue-tier lookup at build time.

New venue acronyms also need to be added to `assets/config.yaml`
under `ranks:` — one entry per acronym, mapping to the appropriate
tier.

Davis's profile is a worked example you can mirror the editing
convention from:
<https://scholar.google.com/citations?user=VSAWPQ4AAAAJ>

### Exporting EvaluationKit CSVs

Course-evaluation scores in C.17 come straight from Purdue's
EvaluationKit web UI:

1. **Build report** — in EvaluationKit, build a new report.
2. **All classes, and core-10 Qs** — scope: every class you've taught;
   questions: the 10 core CIE questions.
3. **Download Excel.**
4. **Export the two tabs as CSVs** — the workbook has two tabs:
   - The "question mapper" tab → save as `assets/evaluationkit-questionmapper.csv`
   - The "raw data" tab → save as `assets/evaluationkit-rawdata.csv`

The build picks both up automatically from `assets/` on the next run.

### BibTeX entry conventions

A few conventions to know if you ever inspect the bib file or need
to add an entry by hand:

- **Citations** — every `journal` / `booktitle` MUST begin with a
  bracketed `[ACRONYM'YY]` tag. The acronym is looked up in
  `assets/config.yaml` under `ranks:` to determine venue tier.
  Examples:
  ```bibtex
  journal   = {[JSS'25] The Journal of Systems and Software}
  booktitle = {[ICSE'25] Proceedings of the International Conference on Software Engineering}
  journal   = {[arXiv'26] arXiv preprint arXiv:2605.10712}
  ```
- **Patents** — `@misc` whose `publisher` or `note` contains `patent`.
  `note = {US Patent 11,176,090}` carries the number; USPTO date lookup is
  attempted via PatentsView when `PATENTSVIEW_API_KEY` is set.
- **Book chapters** — `@incollection` or `@inbook`. No bracket-tag
  needed (no venue field). DOI / URL comes from `manual_links:` in
  `assets/config.yaml`.
- **Theses** — `@phdthesis` / `@mastersthesis`. Built internally but
  not emitted in any section yet — held for future cross-references.
- **CVEs are NOT in the bib.** Bib stays Scholar-canonical. CVEs go in
  `non-scholar-work.yaml`.

---

## What the output looks like

The build emits one RTF file — `publications.rtf`. This section
describes the structure and visual conventions of that file, so you
know what to look for when reviewing.

### Section emission order

The build emits sections in this fixed order. Sub-sections nest under
their parent and indent visually:

```
A. GENERAL INFORMATION   ← group heading (fs32)
  A.1  Name + ORCID + Google Scholar             ← bullet list
  A.2  Degrees                                   ← numbered (A.2.N)
  A.3  Positions at Purdue                       ← prose
  A.4  Positions at other institutions           ← numbered
  A.5  Licenses                                  ← prose ("N/A" allowed)
  A.6  Recognitions / Awards                     ← 2-tier table (ext + int)
  A.7  Memberships                               ← numbered

B. SELF-EVALUATION       ← group heading (fs32)
  B.1  Summary of achievements                   ← prose (cap: 1000 words)
  B.2  Impact of accomplishments                 ← prose (cap: 250 words)
  B.3  Vision                                    ← prose (cap: 500 words)
  B.4  Candidate comments on external events     ← prose (no cap)
  B.5  Professional COVID-19 Impact Statement    ← prose (no cap; "N/A")

(Roman "C." is implicit — never emitted as a group heading)
  C.1   Key Scholarly Publications                ← 2-paragraph per entry
  C.2   Journals                                  ← numbered, hanging indent
  C.3   Books and chapters                        ← numbered
  C.4   Conferences and Workshops                 ← numbered
  C.5   Other publications (subcat: Magazine /    ← subcat headings,
                            Tech Reports /          flat numbering
                            Direct industry impacts)
  C.6   Invited Talks                             ← numbered
  C.7   Leadership Roles                          ← numbered
  C.8   Media Appearances                         ← numbered
  C.9   Conference Presentations                  ← numbered
  C.10  Grants as PI            ← 4-row table per grant + section total
  C.11  Grants as Co-PI / Co-I  ← same shape + section total
  C.12  Gifts                   ← same shape + section total
  C.13  Internal grants         ← same shape + section total
  C.14  Graduate students       ← 6-column table, tier-grouped
  C.15  Postdocs + Visiting     ← 6-column table (N/A when empty)
  C.16  Undergraduate students  ← multi-sub-section:
        C.16.1  Overview                          ← placeholder
        C.16.2  Undergraduate Student Mentoring   ← placeholder
        C.16.2.1  VIP                             ← placeholder
        C.16.2.2  Other Pathways                  ← placeholder
        C.16.2.3  Research Products               ← numbered (auto)
        C.16.2.4  Awards (External + Internal)    ← numbered, tier-grouped
        C.16.3  Graduate Student Mentoring        ← placeholder
        C.16.3.3  Awards (External + Internal)    ← numbered, tier-grouped
  C.17  Courses Taught          ← 6-column table; CSV-driven
                                  (per-course CIE avg/min/max + responsibility)
  C.18  Course Development      ← numbered
  C.19  Patents                 ← 5-column table
  C.20  Entrepreneurial Activities  ← numbered ("N/A" when empty)
  C.21  Technology Transfer     ← table ("N/A" when empty)
  C.22  Software Products       ← 2-paragraph per entry
  C.23  Service to Purdue       ← numbered
  C.24  Service to the profession   ← numbered (acronym auto-expand)
  C.25  Service to State / Nation   ← numbered
  C.26  Other external service      ← numbered

(Section V appendix — emitted last, after C.26)
  A.1  Products under review     ← cross-refs render as "Section V, A.1.N"
  A.2  Pending proposals         ← cross-refs render as "Section V, A.2.N"
                                   (bookmarks namespaced "V_A_2_N" to
                                    avoid collision with Section III A.2)
```

### Cross-references

Anywhere in any prose field, you can write:

- **`@id`** — looks up the named id across every YAML / bib entry, resolves
  to the entry's section code, renders as a clickable hyperlink.
  Example: `@nsf-career-ptm-2026` → `C.10.7`.
- **`@C.X.Y`** — a raw section code. Renders as a clickable hyperlink.
  Example: `@C.16.2.1` → "C.16.2.1" with a jump to the VIP heading.
- **`@@`** — escape: emits a literal `@`. Use for email addresses or
  to talk about `@id` syntax in prose.

Refs in Section V get the Roman prefix verbatim: under-review entries
display as `Section V, A.1.N`; pending proposals as `Section V, A.2.N`.

### Author markers (citation rendering)

Every author in every citation flows through `format_author`, which
adds superscript markers based on lookup tables:

| Marker | Meaning | Source |
|---|---|---|
| **bold** | the candidate (Davis) | `me:` in `config.yaml` |
| `*` | corresponding author (last by default; per-paper override) | `CORRESPONDING_AUTHORS` |
| `#` | senior co-author / PhD or post-doc advisor | `advisors:` in `config.yaml` |
| `G` | graduate student supervised by candidate | `students.G` in `config.yaml` |
| `U` | undergraduate student supervised by candidate | `students.U` in `config.yaml` |

A legend explaining these markers is auto-emitted at the top of C.1.

### Career-phase dividers

Publication sections (C.1–C.5) emit visual dividers between two
regions:

- **PhD studies at Virginia Tech** — papers with year ≤ 2020
- **Assistant Professor at Purdue University** — papers with year ≥ 2021

Numbering does NOT reset across the boundary — it's a visual cue so
the reader can see continuity of publication output post-PhD.

### Section V appendix (under-review + pending proposals)

Section V is the appendix-style trailing block at the end of the
output. Two sub-sections:

- **V.A.1 — Products under review.** Driven by `under_review:` in
  `non-scholar-work.yaml`. The bib stays clean of unpublished work;
  in-flight submissions live here with a `due_date` for sort order.
- **V.A.2 — Pending proposals.** Driven by `grants_as_pi:` /
  `grants_as_co_pi:` entries tagged `status: pending`. Same grant
  schema as awarded — the tag is the only difference. The renderer
  routes pending entries out of C.10 / C.11 to V.A.2 at build time,
  inlining a "Purdue is (not) lead institution" annotation that's
  suppressed for awarded grants.

Cross-references to Section V render with the Roman parent verbatim
("Section V, A.1.3", "Section V, A.2.2") so a reader can tell which
`A.X` is being referenced — the appendix's A.1 / A.2 codes are
distinct from Section III's A.1 / A.2 codes.

---

## Validation + build warnings

Every input is validated at load time; **all errors are batched into
one report** (no first-fail crashes). Build warnings surface where
fixable:

- B.1 / B.2 / B.3 word-count caps (Purdue template limits)
- Self-evaluation file missing `## B.X` headings or duplicate headings
- Courses without a matching `courses_responsibility:` entry render
  blank + log a warning
- 100+ word `key_works` impact statements (warning only)
- Stale `bib_ignore:` / `publication_hide:` keys (titles listed but
  no longer in the bib — possibly Scholar renamed them)
- Unresolved `@id` refs (with the full list of known ids, so you can
  fix typos in one pass)

A failed validation prints every error and exits 1 — fix them all and re-run.

---

## Environment variables

All optional — you can ignore them in a typical run. They exist for
the cases where the defaults don't fit:

| Variable | When you'd set it | What it does |
|---|---|---|
| `LOG_LEVEL` | Build is silent on a problem and you want more output, or it's too noisy | Logging verbosity: `DEBUG` / `INFO` (default) / `WARNING` / `ERROR`. Read at startup in [`cli.py`](src/pubs_emitter/cli.py). |
| `PATENTSVIEW_API_KEY` | You have patents and want accurate USPTO issue dates instead of the bib's `note`-field date (free key at <https://patentsview.org/>) | Authenticates calls to USPTO PatentsView. Without it, patent issue-date lookups are skipped and the bib date is used. Read in [`network.py`](src/pubs_emitter/network.py). |
| `NVD_API_KEY` | You have a lot of CVE entries and the default 6.5s-per-call rate limit is too slow (free key at <https://nvd.nist.gov/>) | Raises the NVD per-call interval from 6.5s → 0.7s (≈ 10× throughput on CVE description lookups). Read in [`network.py`](src/pubs_emitter/network.py). |
| `PUBS_EMITTER_CONFIG` | You're running the tool from a directory where `assets/config.yaml` doesn't resolve (testing, CI, multi-packet workflow) | Overrides the path to the venue-rankings + students + advisors config file. Default is `assets/config.yaml` relative to the package. Read in [`config.py`](src/pubs_emitter/config.py). |
| `PUBS_EMITTER_USER_AGENT` | You're forking the tool and want your own email in the Crossref polite-pool header | Overrides the `User-Agent` header on all outbound HTTP (Crossref, DBLP, NVD, PatentsView). Default carries `mailto:davisjam@purdue.edu`. Read in [`config.py`](src/pubs_emitter/config.py). |

---

---

# Gory details (skip unless you're hacking the renderer)

Everything below this point is for someone who wants to read the
source, file a bug, fork the tool, or adapt it for a different
format. A typical user populating their own packet doesn't need any
of it.

## Architecture diagram

The data-flow shape of one build:

```
                          ┌─────────────────┐
                          │   *.bib (BibTeX)│  ← Google Scholar export
                          └────────┬────────┘
                                   │
                                   │   (DOI / patent / CVE lookup,
                                   │    parallel, cached in SQLite)
                                   ▼
   ┌──────────────────────┐   ┌──────────────────────┐   ┌──────────────────────┐
   │ candidate-information│   │   non-scholar-work   │   │  evaluationkit-*.csv │
   │       .yaml          │   │       .yaml          │   │  (Purdue CIE data)   │
   │                      │   │                      │   │                      │
   │  identifiers         │   │  key_works           │   │  question mapper:    │
   │  degrees             │   │  invited_talks       │   │   QuestionKey → text │
   │  positions           │   │  leadership_roles    │   │                      │
   │  licenses            │   │  media_appearances   │   │  raw data:           │
   │  awards (ext+int)    │   │  grants_as_pi        │   │   per-section Likert │
   │  memberships         │   │  grants_as_co_pi     │   │   response counts    │
   │                      │   │  gifts               │   │                      │
   └──────────┬───────────┘   │  internal_grants     │   └──────────┬───────────┘
              │               │  graduate_students   │              │
              │               │  postdocs_visiting   │              │
              │               │  undergrad_students  │              │
              │               │  student_awards      │              │
              │               │  courses_taught      │              │
              │               │  course_development  │              │
              │               │  technology_transfer │              │
              │               │  software_products   │              │
              │               │  patent_impacts      │              │
              │               │  cves                │              │
              │               │  security_disclosures│              │
              │               │  conference_presents │              │
              │               │  university_service  │              │
              │               │  profession_service  │              │
              │               │  national_service    │              │
              │               │  other_service       │              │
              │               │  under_review        │              │
              │               └──────────┬───────────┘              │
              │                          │                          │
              │                          │     ┌────────────────────┘
              │                          │     │ (per-question pooling,
              │                          │     │  10-concept aggregation,
              │                          │     │  VIP cross-section merge)
              │                          ▼     ▼
              │              ┌──────────────────────────────┐
              │              │   builders.py (typed records)│
              │              │   + cross-ref index +        │
              │              │   resolve_refs (@id → C.X.Y) │
              │              └──────────────┬───────────────┘
              │                             │
              ▼                             ▼
   ┌──────────────────┐         ┌──────────────────────┐
   │ self-evaluation  │         │      rtf.py          │
   │      .md         │────────►│  write_rtf (emission │
   │                  │         │   in canonical order)│
   │  ## B.1 …        │         └──────────┬───────────┘
   │  ## B.2 …        │                    │
   │  ## B.3 …        │                    │
   │  ## B.4 …        │                    │
   │  ## B.5 …        │                    │
   └──────────────────┘                    ▼
                                ┌──────────────────────┐
                                │   publications.rtf   │
                                │  (Word → Paste-Spec) │
                                └──────────────────────┘
```

## File layout

```
purdue-tenure-packet-generator/
├── pubs-emitter.py                 # root entry; delegates to src/pubs_emitter/cli.py
├── setup.sh                        # one-command bootstrap (venv + editable install)
├── pyproject.toml                  # build + tests + dev-deps + lint config
├── README.md                       # ← this file
├── CLAUDE.md                       # editor-facing notes; non-derivable rules + pitfalls
├── .gitignore
├── assets/
│   ├── config.example.yaml         # committed schema + starter venue rankings
│   ├── config.yaml                 # me / advisors / students / venue-ranks   (gitignored)
│   ├── my_papers_full.bib          # BibTeX export from Google Scholar         (gitignored)
│   ├── non-scholar-work.yaml       # everything Scholar doesn't track          (gitignored)
│   ├── candidate-information.yaml  # Section III front matter (A.1-A.7)        (gitignored)
│   ├── self-evaluation.md          # Section IV self-evaluation (B.1-B.5)      (gitignored)
│   ├── evaluationkit-rawdata.csv   # CIE response data → C.17                  (gitignored)
│   └── evaluationkit-questionmapper.csv   # QuestionKey → text aliases         (gitignored)
├── src/pubs_emitter/
│   ├── types.py            # NamedTuples + Section literal + Publications alias
│   ├── config.py           # loads assets/config.yaml + code-side constants
│   ├── latex.py            # decode_latex + rtf_escape_unicode
│   ├── db.py               # SQLite cache (DOI / patent / CVE)
│   ├── network.py          # RateLimiter, polite_get, try_{crossref,dblp,nvd,patentsview}
│   ├── authors.py          # name parsing + format_author / format_inventors
│   ├── venue.py            # parse_venue, lookup_rank, classify_entry, ID extractors
│   ├── lookup.py           # plan / dispatch / commit + cache-aware fetchers
│   ├── builders.py         # build_*, load_*, validate_*, resolve_refs
│   ├── evaluations.py      # EvaluationKit CSV → C.17 CourseTaught pipeline
│   ├── rtf.py              # RtfTable, render_*_section, write_rtf
│   └── cli.py              # parse_args + main()
└── tests/                  # ~470 tests; sub-second; no real network
    ├── conftest.py
    ├── fixtures/
    │   ├── config.yaml
    │   ├── sample.bib
    │   ├── non-scholar.yaml
    │   ├── candidate-information.yaml
    │   └── self-evaluation.md
    └── test_*.py
```

Every input file under `assets/` is gitignored — your data stays
local. Only the example config (`assets/config.example.yaml`) is
committed.

## EvaluationKit ingest internals

When you supply the EvaluationKit raw-data CSV
(`--evaluationkit-rawdata`), `evaluations.py` parses it and produces
C.17 rows:

- 5 question-key revisions are aliased onto **10 canonical concepts**:
  course organized, assignments / projects / exams aid objectives,
  instructor explains clearly / answers questions / cares / makes time
  / fair / inclusive. The mapping handles wording drift across
  semesters (`v496` ↔ `v614` ↔ `v657` ↔ `v679` ↔ `v737`).
- Multi-section courses (especially VIP) are pooled across sections
  via raw-count math: `Σ(Value × OptionRespondents) / Σ(OptionRespondents)`,
  per concept, per merged course.
- Research / thesis-supervision / independent-study courses (titles
  containing "research" / "thesis" / "independent study" / "directed
  reading") are dropped — they're not classroom teaching.
- Per-row CIE summary is **avg of the 10 concept means**, with min +
  max across the same 10. A row backed by fewer than 10 concepts (some
  question revisions ship only 7) gets a `*` marker + a footnote
  ("Computed on the relevant subset of questions asked").

Per-course responsibility text is supplied via `courses_responsibility:`
in `non-scholar-work.yaml` — a flat per-course list (one explicit
entry per `(year, semester_str, course_number)` triple).

Grey "no course taught" note rows (parental leave, ABET self-study
release, etc.) are authored as `courses_taught:` entries with
`is_note_row: true` in the YAML; the renderer merges all 6 cells into
a grey-shaded row and prepends the semester label inline so the row
reads in context.

## Section V bookmark namespace

V.A.1 + V.A.2 entries reuse the `A.X.N` code prefix the Section III
front matter uses. To prevent bookmark-target collisions (clicking
"Section V, A.2.3" should not land on Section III's Degrees entry
A.2.3), every Section V bookmark is namespaced with a `V_` prefix at
emission time: the displayed code is `A.2.3` but the bookmark target
is `V_A_2_3`. Wired in `_ref_anchor(code, bookmark_prefix="V.")`.

## Dev tools

```bash
.venv/bin/pylint src/pubs_emitter    # 9.95/10 baseline
.venv/bin/mypy                       # type-clean
.venv/bin/pytest                     # ~470 tests, sub-second
```

### Test suite

Structured as one file per package module (`test_latex.py`,
`test_venue.py`, …) plus `test_e2e.py` which drives `cli.main`
end-to-end with all three network entry points monkey-patched (no
real HTTP, no real network).

Key invariants pinned in tests:

- **Table of Contents** — every expected section heading (40 total —
  Section III A.1–A.7, Section IV B.1–B.5, C.1–C.26, Section V A.1
  + A.2) appears in the rendered RTF, in canonical emission order.
- **Section bookmark placement** — every `\*\bkmkstart NAME` marker
  falls inside the byte-range of the section that owns its named
  code (catches the V.A.2 vs Section III A.2 bookmark collision class).
- **Numerical ordering** — entry codes within each section emit in
  tuple-monotone order; subcategory grouping (C.5) doesn't restart
  the section-wide counter.
- **Grant math** — section totals match the YAML sum, excluding
  pending grants (which route to V.A.2 and don't contribute to
  C.10 / C.11 totals).

Run a single file: `pytest tests/test_latex.py`. Single class:
`pytest tests/test_rtf.py::TestRtfTable`. Single test: `pytest
tests/test_e2e.py::TestE2eTableOfContents::test_every_canonical_heading_present`.

Fixtures live in `tests/fixtures/`:

- `config.yaml` — minimal config (loaded via `PUBS_EMITTER_CONFIG` in `conftest.py`)
- `sample.bib` — one of each entry kind (article / inproceedings / arXiv / patent / incollection / phdthesis), including a Çakar coauthor for Unicode-escape coverage
- `non-scholar.yaml` — one entry under every YAML key, including a `status: pending` grant + an under-review entry for Section V coverage
- `candidate-information.yaml` — minimal Section III for coverage of A.1–A.7
- `self-evaluation.md` — minimal Section IV for coverage of B.1–B.5
