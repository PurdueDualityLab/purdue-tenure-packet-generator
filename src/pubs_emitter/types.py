"""Typed data containers + type aliases shared across the package."""
from __future__ import annotations

from typing import Any, Callable, Literal, NamedTuple, Optional


Category = Literal[
    "Journals",
    "Conferences and Workshops",
    "arXiv / Preprints",
]

# Open enum (not Literal): unranked venues are caught at lookup time. Values
# in practice are "Rank 1" | "Rank 2" | "Rank 3" | "Workshop" | "Magazine"
# | "Preprint" | "CVE".
Rank = str

Section = Literal[
    # Section III (front matter) — A.1-A.7. Codes A.1-A.7 here REUSE the
    # "A." prefix that Section V (Under Review) also uses; the two trees
    # coexist because the doc's Roman-numeral top sections (III vs V)
    # disambiguate them. Cross-refs to Section V's A.1.N render as
    # "Section V, A.1.N"; the Section III tree has no @id resolution yet.
    "Identifiers",
    "Degrees",
    "Positions at Purdue",
    "Positions at Other Institutions",
    "Licenses",
    "Awards",
    "Professional Memberships",
    # Section IV (Self-Evaluation) — B.1-B.5. Plain prose statements with
    # @-ref cross-references resolved at build time.
    "B1 Summary",
    "B2 Impact",
    "B3 Vision",
    "B4 External Events",
    "B5 COVID Impact",
    # Section V (appendix) — Products under review + Pending proposals.
    "Under Review",
    "Pending Proposals",
    "Key Works",
    "Journals",
    "Books and Chapters",
    "Conferences and Workshops",
    "Other publications and products",
    "Invited Talks",
    "Leadership Roles",
    "Media Appearances",
    "Conference Presentations",
    "Grants PI",
    "Grants Co-PI",
    "Gifts",
    "Internal Grants",
    "Graduate Students",
    "Postdocs and Visiting Scholars",
    "Undergraduate Students",
    "Undergraduate Research Products",
    "Undergraduate Student Awards",
    "Graduate Student Awards",
    "Courses Taught",
    "Course Development",
    "Patents",
    "Entrepreneurial Activities",
    "Technology Transfer",
    "Software Products",
    "University Service",
    "Profession Service",
    "National Service",
    "Other Service",
]

StudentType = Literal["G", "U"]

BibEntry = dict[str, str]


class Citation(NamedTuple):
    section: Section
    rank: Rank
    year: int           # sort key; 9999 for "In Press" / unparseable
    year_str: str
    authors_rtf: str
    title: str
    venue: str
    details: str
    link: str
    back_ref_title: Optional[str] = None  # CVE → paper title (resolved to "(see C.4.7)" at render)


class Patent(NamedTuple):
    year: int
    year_str: str
    title: str
    co_inventors: str   # already RTF-marked (\b for me); do NOT escape at render
    date: str
    number: str
    impact: str


class InvitedTalk(NamedTuple):
    """A C.6 invited talk. All talks are type 'Seminar' for now."""
    year: int           # sort key (first 4-digit year in year_str for ranges)
    year_str: str       # display, e.g. "2024" or "Annual, 2015-2019"
    topic: str          # main subject; e.g. "Regular Expression Denial of Service"
    subtitle: str       # optional specific talk title within that topic ("" if none)
    venue: str          # institution / forum + location
    id: str = ""        # OPTIONAL cross-ref id; @id resolves to "C.6.N" at build time


class LeadershipRole(NamedTuple):
    """A C.7 leadership role (workshop chair, journal editor, etc.)."""
    year: int           # sort key
    year_str: str       # display
    role: str           # "Co-Chair", "Organizer", "Mentor", "Editor", etc.
    description: str    # the event/journal/committee name + venue context
    society: str        # affiliated professional society ("ACM SIGSOFT", "IEEE", "ASEE")
    id: str = ""        # OPTIONAL cross-ref id; @id resolves to "C.7.N" at build time


class MediaAppearance(NamedTuple):
    """A C.8 media interview / podcast / coverage appearance."""
    year: int           # sort key
    year_str: str       # display
    title: str          # the episode/article/segment title
    venue: str          # podcast/publication/show name
    url: str            # optional link
    id: str = ""        # OPTIONAL cross-ref id; @id resolves to "C.8.N" at build time


class ConferencePresentation(NamedTuple):
    """A C.9 contributed conference talk; venue/year/code come from the linked paper."""
    paper_title: str    # exact bib title (case+whitespace insensitive)


class Student(NamedTuple):
    """A C.14 graduate student record. Related publications are auto-derived.

    `aliases`: OPTIONAL list of alternate names the student appears under
    in the bib / A.1 author lists. Needed when bib entries use a
    different last name (accent fold, hyphenation drop, or just an
    earlier-publication shorter form). The C.14 "Related Publications"
    column scans bib + A.1 author lists for `name` AND every alias and
    unions the results. Match semantics: structural (last-name equality
    + initials-prefix), same as the canonical name match — applied once
    per alias. Example: Ricardo's bib forms include "Méndez, Ricardo
    Andrés Calvo" (canonical), "Mendez, ..." (ASCII-folded), "Calvo,
    Ricardo" (short form) — three different last names; aliases capture
    the latter two.
    """
    grad_year: int           # sort key (use 9999 for ongoing students)
    grad_display: str        # "2025 Spring" / "ongoing" / etc.
    name: str
    degree: str              # "PhD" | "PhD candidate" | "PhD student" | "MSc" | "DEng" | "MS-Thesis" | "MS-Non-Thesis"
    role: str                # "Chair" | "Co-Chair" | "Committee member"
    position: str            # current job + affiliation (optional)
    co_advisor: str = ""     # set when role == "Co-Chair"; renders "(with NAME)" in Role column
    id: str = ""             # OPTIONAL cross-ref id; @id resolves to "C.14.N" / "C.16.N"
    aliases: tuple[str, ...] = ()  # alternate bib forms for pub-back-mapping
    linkedin: str = ""       # OPTIONAL LinkedIn profile URL — rendered as
                             # a clickable "LinkedIn" link in the Position
                             # cell so the reader can verify current
                             # position. Not auto-scraped (LinkedIn ToS
                             # forbids; reality: their 999/login-wall
                             # response makes it unworkable anyway).
                             # Periodic refresh via tools/refresh-positions.py
                             # is the intended freshness path.


class GrantPerson(NamedTuple):
    """A person on a grant other than the candidate.

    Captures the full structured tuple needed for the Purdue mentoring-format
    personnel line: role label, name, departmental affiliation, and (for
    external collaborators) the institution + their separate NSF award number.

      * Purdue Co-PI:    role="Co-PI", name=..., department=...,
                          institution="" (implicit Purdue), nsf_award=""
      * External lead PI on a collab: role="PI", name=..., department=...,
                          institution="Columbia University", nsf_award="2526620"
    """
    name: str
    role: str = "Co-PI"           # "PI" / "Co-PI" / "Co-I"
    department: str = ""          # e.g., "Computer Science", "ECE"
    institution: str = ""         # external only; empty → Purdue (implicit)
    nsf_award: str = ""           # NSF collab partner's separate award #


class Grant(NamedTuple):
    """A C.10/C.11/C.12/C.13 grant entry (same datatype, routed by YAML key).

    Three explicit USD fields capture the financial scope:
      * `total_amount`   — whole award across all institutions (matters for
                            multi-institution NSF Collab and similar)
      * `purdue_amount`  — Purdue's share of the award (== total for
                            single-institution grants)
      * `my_amount`      — your credited share (== purdue when sole Purdue
                            PI; smaller when multiple Purdue PIs split credit)

    Personnel is fully structured via `GrantPerson` records:
      * `personnel: list[GrantPerson]` — every other person on the grant.
        Sole-PI grants → empty list.
      * `lead_institution` — multi-inst only: name of the lead institution
        ("Purdue University" / "Columbia University" / ...). When set, the
        renderer annotates Davis's role line accordingly.

    inspired_by + publication_outcomes are paper-title lists that get
    resolved against the bib (case+whitespace insensitive) to C.X.Y refs.
    """
    start_year: int               # sort key + display
    end_year: int                 # display
    title: str                    # grant title (e.g. "CAREER: PTM-SEER: ...")
    agency: str                   # full name (e.g. "US National Science Foundation")
    agency_short: str             # short form for prefix (e.g. "NSF")
    grant_number: str             # funder's award ID (e.g. "2541917")
    role: str                     # "PI" / "Co-PI" / etc. (Davis's role only)
    lead_institution: str         # multi-inst only: which institution is lead
    personnel: list[GrantPerson]  # other personnel, structured
    responsibility_percent: int   # user's responsibility share % (0 = unspecified)
    total_amount: int             # full award across institutions (USD)
    purdue_amount: int            # Purdue's share (USD)
    my_amount: int                # user's credited share (USD)
    activities: str               # optional multi-sentence description
    responsibility: str           # optional free-text role/percent statement
    inspired_by: list[str]        # bib titles that motivated this grant → C.X.Y refs
    publication_outcomes: list[str]  # bib titles funded by this grant → C.X.Y refs
    status: str = "awarded"       # "awarded" (default; renders in C.10-C.13)
                                  # or "pending" (routes to Section V, A.2
                                  # "Pending proposals" appendix). Same schema
                                  # either way — the tag changes only the
                                  # destination section + cross-ref display
                                  # ("Section V, A.2.N" vs "C.10.N" etc.).
    id: str = ""                  # OPTIONAL cross-ref id; @id resolves to "C.10.N" / "C.11.N" / "C.12.N" / "C.13.N" / "Section V, A.2.N"


class UnderReview(NamedTuple):
    """An A.1 in-flight submission (paper / book / software under review).

    `due_date` is the review-deadline ISO date used as the sort key; entries
    with no known date use "9999-99-99" so they sort to the bottom.
    `authors_rtf` carries the per-author RTF markup (bold-for-me, G / # / *
    role markers) from `format_author`; YAML stores raw author names.
    `raw_authors` carries the bib-form names (pre-format_author) so the
    student-table "Related Publications" column can match A.1 entries
    against student records the same way it matches bib entries.
    """
    due_date: str        # YYYY-MM-DD; sort key
    title: str
    authors_rtf: str
    venue: str
    pages: str           # e.g., "30 pages"
    raw_authors: tuple[str, ...] = ()  # bib-form strings, one per author
    id: str = ""         # OPTIONAL cross-ref id; @id resolves to "A.1.N" at build time


class KeyWork(NamedTuple):
    """A paper designated as a key/highlight scholarly publication (C.1 entry)."""
    citation: Citation   # the resolved paper's standard Citation
    impact: str          # 100-word impact statement
    id: str = ""         # OPTIONAL cross-ref id; @id resolves to "C.1.N" at build time


class PostdocVisiting(NamedTuple):
    """A C.15 postdoc / visiting-scholar record (Purdue mentoring section).

    Auto-derived Related Publications uses the same `_student_pub_refs`
    structural matcher as students. Field shape mirrors Purdue's mandated
    C.15 columns: Name | Last Degree/Date | Prior Affiliation | Position
    Title/Dates | Related Publications | Current Position and Affiliation.
    """
    year: int                       # sort key (start year)
    name: str
    last_degree_date: str           # e.g., "PhD/2010"
    prior_affiliation: str          # e.g., "University X"
    position_title_dates: str       # e.g., "Postdoc, 03/01/10 - present"
    current_position: str           # optional
    id: str = ""                    # OPTIONAL cross-ref id; @id resolves to "C.15.N"


class UndergradProduct(NamedTuple):
    """A C.16.2.3 record: a publication / artifact that includes ≥1 undergrad
    coauthor. Auto-derived from each bib entry's `author` list at build
    time — never authored in YAML.

    Renders as: "{product_label} {ref} ({n} undergraduate co-author{s}
    [undergraduate is lead author])".
      * `product_label` — short kind hint ("Paper" / "Book chapter")
      * `ref` — back-pointer like `C.4.7` (from paper_index, source of truth)
      * `n_coauthors` — count of undergrads in the bib author list
      * `lead_is_undergrad` — True when the FIRST bib author is an undergrad
        (triggers the "[undergraduate is lead author]" trailing tag)
    """
    year: int               # sort key (cit.year, for newest-first ordering)
    product_label: str      # "Paper" / "Book chapter"
    ref: str                # "C.4.7"
    n_coauthors: int        # ≥1; entries with 0 are filtered out at build time
    lead_is_undergrad: bool


class CourseTaught(NamedTuple):
    """A C.17 row: one course-section taught at Purdue (or elsewhere).

    Mirrors the Purdue tenure-template 6-column table exactly so the
    renderer can emit `RtfTable` rows directly. Sort order: year ASC,
    then `semester_order` ASC (Spring → Summer → Fall) — chrono-ascending
    matches how the rest of the packet presents teaching activity.

    CIE-score fields are OPTIONAL so a row can be added BEFORE the CIE
    survey results come back (or for cross-institution course sections
    where CIE data doesn't apply). Missing values render as "—" rather
    than "0.00" so they're distinguishable from a zero score.

    `is_new_course` controls the "*" prefix on the title cell per the
    Purdue convention ("indicate with asterisk if new course introduced").
    """
    year: int                # sort key (calendar year of the semester)
    semester_order: int      # within-year sort: 1=Spring, 2=Summer, 3=Fall
    semester_str: str        # display: "F20" / "Sp21" / "Su22"
    title: str               # course title (no asterisk; renderer adds it)
    is_new_course: bool      # True → "*" prefix on title cell
    course_number: str       # "ECE 30861/46100" (slash-form allowed)
    responsibility: str      # free-form: "Instructor designed, prepared, …"
    responses: Optional[int]      # CIE survey responses (numerator)
    enrolled: Optional[int]       # # enrolled in class (denominator)
    cie_average: Optional[float]  # 5.0 scale; avg of 10 CIE reported averages
    cie_min: Optional[float]      # min among the 10 averages
    cie_max: Optional[float]      # max among the 10 averages
    cie_partial: bool = False     # True when fewer than 10 core concepts
                                  # backed the avg/min/max (v657 semester
                                  # had only 7 of 10; v737 "where relevant"
                                  # questions drop to 9 when silent).
                                  # Renderer adds "*" + footnote.
    is_note_row: bool = False     # True → grey row that spans all columns
                                  # carrying ONLY `title` as the message
                                  # (used for "no 3-credit course taught"
                                  # ABET-release entries). All other
                                  # CIE-related fields are ignored.
    id: str = ""                  # OPTIONAL cross-ref id; @id resolves to "C.17.N" at build time


class CourseDevelopment(NamedTuple):
    """A C.18 entry: one course / curricular contribution. Numbered list shape.

    Schema mirrors `EntrepreneurialActivity` (bold-summary + free-form
    description) so the same two-field pattern covers any "open-ended
    fill-in" Purdue section. The C.18 packet heading asks for course,
    location, date, enrollment, and nature of participation — fold all
    of that into the summary + description rather than carving out
    discrete fields.
    """
    summary: str       # bold prefix; typically "{course-number}: {course-title}"
    description: str   # free-form prose
    id: str = ""       # OPTIONAL cross-ref id; @id resolves to "C.18.N" at build time


class EntrepreneurialActivity(NamedTuple):
    """A C.20 entry: "Summary: natural-language description".

    `summary` renders bold; `description` follows after a colon. Empty
    `entrepreneurial_activities` list → renderer emits the section heading
    plus indented "N/A" (same pattern as C.15 postdocs — empty IS a signal,
    not a skip).
    """
    summary: str
    description: str
    id: str = ""       # OPTIONAL cross-ref id; @id resolves to "C.20.N" at build time


class TechnologyTransfer(NamedTuple):
    """A C.21 entry: a table row capturing a technology-transfer contribution.

    Mirrors the Purdue tenure-template column shape exactly so the renderer
    can emit `RtfTable` rows directly. Empty `technology_transfer` list →
    renderer emits the section heading plus indented "N/A".

    `cited_publications` carries bib-title strings; the renderer resolves
    each via `paper_index` to a `C.X.Y` ref (same pattern as the grant
    `publication_outcomes` field). Unresolved titles surface as a build-time
    error in `validate_non_scholar`.
    """
    code_standard: str           # e.g. "AASHTO LRFD Bridge Design Specs"
    change_subject: str          # e.g. "Ultra-High-Performance Concrete (UHPC)"
    reason: str                  # "Reason For The Change" cell
    research_supporting: str     # "Research Supporting The Change" cell
    cited_publications: list[str]  # bib titles → resolved to "C.X.Y, C.X.Y" at render
    impact: str                  # "Impact" cell (e.g., "Enable Robotics in ...")
    id: str = ""                 # OPTIONAL cross-ref id; @id resolves to "C.21.N" at build time


class StudentAward(NamedTuple):
    """A student award/fellowship — students Davis mentored.

    Tenure-packet codes split by recipient level:
      * `level = "U"` → C.16.2.4 (undergraduate student awards)
      * `level = "G"` → C.16.3.3 (graduate student awards)

    Level is REQUIRED in YAML (no auto-detection): the award NAME is the
    strongest signal ("ECE Undergraduate Research Award" vs "ECE Magoon
    Graduate Student ..."), but enough awards are ambiguous (Astronaut
    Scholar, CSGrad4US, NDSEG) that auto-classification gets specific cases
    wrong. Explicit tagging avoids that failure class.

    Tier is the within-section subheading group:
      * `tier = "National and International Awards"`
      * `tier = "Institutional Awards"`

    Other tier strings are allowed (will render alphabetically after the two
    canonical tiers); keep them rare. Within a tier the renderer sorts by
    year descending (newest first).
    """
    year: int            # sort key
    year_str: str        # display ("2025")
    level: StudentType   # "U" or "G" — routes to C.16.2.4 vs C.16.3.3
    tier: str            # subsection grouping label
    recipient: str       # student name(s); free-form (multi-name, "VIP Team", etc.)
    award: str           # award title (may contain semicolons for multi-award entries)
    id: str = ""         # OPTIONAL cross-ref id; @id resolves to "C.16.2.4.N" / "C.16.3.3.N"


class SoftwareProduct(NamedTuple):
    """A C.22 software product entry — tools, libraries, datasets,
    reproducibility artifacts released independently or alongside papers.

    Schema is intentionally minimal: name + description + year. Add a URL,
    license, or paper cross-refs inline in `description` when relevant.
    """
    year: int            # sort key (first release year)
    year_str: str        # display (e.g., "2018", "2018-present", "Annual 2015-2019")
    name: str            # tool / project name (may include a parenthetical URL)
    description: str     # free-form prose, typically 1-2 paragraphs
    id: str = ""         # OPTIONAL cross-ref id; @id resolves to "C.22.N" at build time


class ServiceEntry(NamedTuple):
    """A C.23–C.26 service activity (university, profession, national, other).

    `description` carries the full role+venue string (e.g. "PC Member, ICSE").
    `year_str` is the display string: "2025", "2025, 2026, 2027",
    "2024-2025", "2023-present", or "" for ongoing service with no fixed date
    (e.g. journal reviewing).

    `show: false` hides this entry from rendering — useful for trimming the
    packet by audience (e.g., minor reviewing roles for a tenure-and-
    promotion-to-full audience but not for a tenure-only one). Hidden
    entries don't register in `ref_index` and don't consume a C.X.N slot;
    visible entries renumber accordingly so the rendered output has no
    gaps.
    """
    year: int            # sort key; 9999 when year_str is empty
    year_str: str        # display
    description: str
    id: str = ""         # OPTIONAL cross-ref id; @id resolves to "C.23.N" / "C.24.N" / "C.25.N" / "C.26.N"
    show: bool = True    # set false to suppress this entry from the rendered packet


class NetworkTask(NamedTuple):
    """One unit of network work to be dispatched in parallel.

    `kind` selects which SQLite cache table the result writes into;
    `key` is the primary key for that cache row;
    `fetcher` is the pure network call (no DB writes — main thread commits).
    """
    kind: str                       # "doi" | "cve" | "patent"
    key: str
    fetcher: Callable[[], Any]


Publications = dict[Section, list[Citation]]


# ----- Candidate Information (Section III front matter, A.1-A.7) ----------


class Identifiers(NamedTuple):
    """A.1: name + scholarly identifier URLs.

    Renders as a bullet list with bold field labels. `orcid` and
    `google_scholar` MUST be URLs; the renderer wraps them in clickable
    RTF HYPERLINK fields. Empty fields are skipped (no orphan bullet).
    """
    name: str
    orcid: str            # URL — rendered as hyperlink
    google_scholar: str   # URL — rendered as hyperlink


class Degree(NamedTuple):
    """A.2.N: one earned degree.

    Renders as: "{institution}, {years}. {degree}. {thesis_kind}:
    /italic thesis_title/, supervised by {advisor}." Thesis bits are
    omitted when `thesis_title` is empty (degrees without a thesis).
    """
    institution: str
    years: str
    degree: str
    thesis_kind: str   # "Undergraduate thesis" / "Doctoral thesis" / ""
    thesis_title: str  # italicized in output
    advisor: str       # display name incl. "Dr."/"Prof." prefix


class OtherPosition(NamedTuple):
    """A.4.N: one position at another institution / organization.

    Renders as: "{title}, {years}. {organization} ({acronym})." The
    parenthetical acronym is omitted when `acronym` is empty.
    """
    title: str
    years: str
    organization: str
    acronym: str       # parenthetical short form; "" → no parenthetical


class ProfessionalMembership(NamedTuple):
    """A.7.N: one professional-society membership.

    Renders as: "{level}, {organization} ({acronym})." Common levels:
    "Member", "Senior Member", "Fellow". Parenthetical acronym is
    omitted when `acronym` is empty.
    """
    level: str
    organization: str
    acronym: str


class Award(NamedTuple):
    """A.6.N: one award / recognition.

    Rendered as one row in the A.6 table (3 columns: Name | Date | Significance).
    Awards are split into two tier groups — EXTERNAL and INTERNAL — each
    running in independent chronological order. The within-group sort is
    by `year` ascending; within a year, by YAML order (stable).

    `significance` is the "Brief Description of Significance" cell text;
    it accepts `@bibkey` / `@id` / `@C.X.Y` cross-references and gets
    routed through `resolve_refs_in_list` at build time so the rendered
    cell carries clickable hyperlinks.
    """
    year: int                                # sort key
    year_str: str                            # display ("2018" / "2024-25")
    tier: Literal["external", "internal"]    # routes to EXTERNAL / INTERNAL group
    name: str                                # award title (rendered verbatim)
    significance: str                        # description; supports @-refs
    id: str = ""                             # optional cross-ref id → A.6.N


class SelfEvaluation(NamedTuple):
    """Section IV — the B.X self-evaluation statements.

    Loaded once from `assets/self-evaluation.md`. Each field is the raw
    prose (one string per section, paragraphs separated by '\\n\\n') for
    a single B.X sub-section. `@bibkey` / `@id` / `@C.X.Y` refs in any
    field are resolved at build time via the standard PROSE_FIELDS_BY_TYPE
    pipeline, same as YAML prose fields.

    Word-count discipline (Purdue template caps):
      * B.1 ≤ 1,000 words
      * B.2 ≤   250 words
      * B.3 ≤   500 words
      * B.4 no cap
      * B.5 no cap (optional section)

    Over-cap is a build warning, not an error — draft passes routinely
    run over before the polish round trims them down.
    """
    b1: str   # Summary of achievements
    b2: str   # Impact of accomplishments
    b3: str   # Vision
    b4: str   # Candidate comments on external events
    b5: str   # Professional COVID-19 Impact Statement


class CandidateInformation(NamedTuple):
    """Section III aggregate — the full front-matter payload (A.1-A.7).

    Loaded once from `assets/candidate-information.yaml`. Sub-sections:
      * `identifiers`              → A.1 (Identifiers NamedTuple)
      * `degrees`                  → A.2 (list[Degree])
      * `positions_at_purdue`      → A.3 (free-form prose string)
      * `positions_at_other`       → A.4 (list[OtherPosition])
      * `licenses`                 → A.5 (free-form prose; "N/A" allowed)
      * `awards`                   → A.6 (list[Award], 2 tiers)
      * `professional_memberships` → A.7 (list[ProfessionalMembership])
    """
    identifiers: Identifiers
    degrees: list[Degree]
    positions_at_purdue: str
    positions_at_other: list[OtherPosition]
    licenses: str
    awards: list[Award]
    professional_memberships: list[ProfessionalMembership]
