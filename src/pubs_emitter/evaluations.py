"""EvaluationKit CIE-data ingestion for C.17 (Courses taught at Purdue).

This module parses the two CSVs Purdue's EvaluationKit exports — a question
mapper and a per-section raw-data dump — and aggregates them into per-course
records suitable for the C.17 teaching-scores table.

Two CSVs:
  * `assets/evaluationkit-questionmapper.csv` — QuestionKey → question text.
    Three QuestionKey families exist, each corresponding to a wording
    revision of Purdue's CIE survey (496xxx → 657xxx → 737xxx).
  * `assets/evaluationkit-rawdata.csv` — one row per
    (course-section × question × Likert-value), carrying the per-Likert
    response count (column K=value, column L=OptionRespondents) AND a
    pre-computed `Mean` column for that (section, question).

The C.17 table demands per-course (semester × course-number) rows with
"Average of 10 CIE Reported Averages" + min + max. Multi-section courses
(typical for VIP, occasional for ECE 49595 / 59500) need pooled-count math
across sections to produce the merged-course averages.

Stdlib-only — no pandas dependency. The dataset is small (~800 rows).
"""
from __future__ import annotations

import csv
import logging
import re
from collections import defaultdict
from typing import NamedTuple

log = logging.getLogger(__name__)


# ----- 10 core CIE concepts ------------------------------------------------
#
# Purdue's CIE survey has gone through three published wordings; the
# question text changes but the SEMANTIC CONCEPT is stable. The 10
# concepts below are derived from the screenshot the candidate provided
# (matches Version 1, which is the canonical / current published "10
# core questions" wording). Each QuestionKey maps to exactly one concept
# via `QUESTION_KEY_TO_CONCEPT`.

CoreConcept = str  # Open enum — values below are the only ones in use.

CORE_CONCEPTS: tuple[CoreConcept, ...] = (
    "course_organized",          # screenshot Q7
    "assignments_objectives",    # screenshot Q8
    "projects_objectives",       # screenshot Q9 (where relevant)
    "exams_objectives",          # screenshot Q10 (where relevant)
    "instructor_explains_clearly",  # screenshot Q1
    "instructor_answers_questions", # screenshot Q2
    "instructor_cares_learning",    # screenshot Q3
    "instructor_makes_time",        # screenshot Q4
    "instructor_fair",              # screenshot Q5
    "instructor_inclusive",         # screenshot Q6
)

# QuestionKey (as it appears in column E of rawdata.csv) → canonical
# concept. LLM-derived mapping across the three published wordings.
#
# Cross-version notes:
#   * v496 is the screenshot wording (the candidate's canonical list of
#     10). All 10 concepts present.
#   * v657 ships only 7 concepts — `course_organized`,
#     `projects_objectives`, and `exams_objectives` are absent. Used by
#     ONE semester in the data (Spring 2022 Co-admin).
#   * v737 is the current Purdue wording (Fall 2023+). All 10 concepts
#     present. Rewords two questions but conveys the same concept:
#       - `instructor_explains_clearly`: v496 "clearly explains material"
#         vs v737 "communicates clearly" — both target instructor's
#         pedagogical clarity. Treating as alias.
#       - `course_organized`: v496 "class activities are well prepared
#         and organized" vs v737 "the course is well organized" — both
#         target course-level preparation. Treating as alias.
QUESTION_KEY_TO_CONCEPT: dict[str, CoreConcept] = {
    # ----- Version 1 (496xxx) — matches the candidate's screenshot --------
    "496875-0": "instructor_explains_clearly",   # "clearly explains material"
    "496878-0": "instructor_answers_questions",  # "open to my questions"
    "496880-0": "instructor_cares_learning",     # "cares that I learned"
    "496882-0": "instructor_makes_time",         # "willingly makes time"
    "496883-0": "instructor_fair",               # "fair and consistent"
    "496793-0": "instructor_inclusive",          # "welcoming and inclusive"
    "496794-0": "course_organized",              # "well prepared and organized"
    "496872-0": "assignments_objectives",        # "assignments aid"
    "496873-0": "projects_objectives",           # "projects or laboratories"
    "496874-0": "exams_objectives",              # "examinations aid"

    # ----- Version 2 (657xxx) — 7 concepts only ----------------------------
    # Missing: course_organized, projects_objectives, exams_objectives.
    "657534-0": "instructor_explains_clearly",   # "communicates clearly" (alias)
    "657535-0": "instructor_answers_questions",  # "effectively answers"
    "657536-0": "instructor_cares_learning",     # "care about my learning"
    "657537-0": "instructor_makes_time",         # "makes time to help"
    "657538-0": "instructor_fair",               # "fair in evaluating"
    "657539-0": "instructor_inclusive",          # "inclusive learning environment"
    "657523-0": "assignments_objectives",        # "assignments aid"

    # ----- Version 614xxx — Fall 2021 wording — 9 of 10 concepts ----------
    # Numbering: 614820 is absent in this revision (the "tests or exams"
    # question was skipped); aggregator sees 9 concept scores for any
    # course that ran this survey → cie_partial=True, footnote fires.
    "614817-0": "course_organized",              # "course is well organized" (alias)
    "614818-0": "assignments_objectives",        # "assignments aid"
    "614819-0": "projects_objectives",           # "projects or laboratories"
    # 614820-0 absent — exams_objectives concept missing from this revision.
    "614821-0": "instructor_explains_clearly",   # "communicates clearly" (alias)
    "614822-0": "instructor_answers_questions",  # "effectively answers"
    "614823-0": "instructor_cares_learning",     # "care about my learning"
    "614824-0": "instructor_makes_time",         # "makes time to help"
    "614825-0": "instructor_fair",               # "fair in evaluating"
    "614826-0": "instructor_inclusive",          # "inclusive learning environment"

    # ----- Version 679xxx — Fall 2022 wording — 8 of 10 concepts ----------
    # Same as v614 but missing instructor_inclusive too. Only 8 concept
    # scores produced → cie_partial=True.
    "679118-0": "course_organized",
    "679119-0": "assignments_objectives",
    "679120-0": "projects_objectives",
    "679121-0": "instructor_explains_clearly",
    "679122-0": "instructor_answers_questions",
    "679123-0": "instructor_cares_learning",
    "679124-0": "instructor_makes_time",
    "679125-0": "instructor_fair",

    # ----- Version 3 (737xxx) — current Purdue wording ---------------------
    "737245-0": "instructor_explains_clearly",   # "communicates clearly" (alias of v496 Q1)
    "737246-0": "instructor_answers_questions",  # "effectively answers"
    "737247-0": "instructor_cares_learning",     # "care about my learning"
    "737248-0": "instructor_makes_time",         # "makes time to help"
    "737249-0": "instructor_fair",               # "fair in evaluating"
    "737250-0": "instructor_inclusive",          # "inclusive learning environment"
    "737241-0": "course_organized",              # "course is well organized" (alias of v496 Q7)
    "737242-0": "assignments_objectives",        # "assignments aid"
    "737243-0": "projects_objectives",           # "projects or laboratories"
    "737244-0": "exams_objectives",              # "tests or exams aid"
}


# ----- Raw row + parsing --------------------------------------------------


class RawEvalRow(NamedTuple):
    """One row of `evaluationkit-rawdata.csv`.

    Each row carries the (Likert value, # who chose it) pair for ONE
    (section × question). The `mean` column is a pre-computed per-section
    average for that question — same value across the 5 rows that share a
    (section, question) group.
    """
    project: str             # "Fall 2020 16 Week ..."
    course_code: str         # "wl.202110.ECE.36800.001.17766"
    course_title: str        # "Data Structures"
    course_uid: str          # often == course_code
    question_key: str        # "496875-0"
    enrollments: int         # # enrolled in section
    respondents: int         # # who responded (denominator for response rate)
    mean: float              # pre-computed per-(section, question) average
    value: int               # Likert value 1..5 (column K)
    option_respondents: int  # # of respondents who selected this value (column L)


def _maybe_int(s: str) -> int:
    """Tolerate floats-shaped-as-int ("99.0") and blanks ("" → 0)."""
    s = s.strip()
    if not s:
        return 0
    return int(float(s))


def _maybe_float(s: str) -> float:
    s = s.strip()
    if not s:
        return 0.0
    return float(s)


def parse_raw_data(path: str) -> list[RawEvalRow]:
    """Load `evaluationkit-rawdata.csv` into typed records.

    `encoding='utf-8-sig'` strips the BOM that EvaluationKit's CSV ships
    with so the first column header doesn't carry a hidden `\\ufeff`.
    """
    out: list[RawEvalRow] = []
    with open(path, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            out.append(RawEvalRow(
                project=row["Project"],
                course_code=row["Course Code"],
                course_title=row["Course Title"],
                course_uid=row["Course UniqueID"],
                question_key=row["QuestionKey"],
                enrollments=_maybe_int(row.get("Enrollments", "")),
                respondents=_maybe_int(row.get("Respondents", "")),
                mean=_maybe_float(row.get("Mean", "")),
                value=_maybe_int(row.get("Value", "")),
                option_respondents=_maybe_int(row.get("OptionRespondents", "")),
            ))
    return out


def parse_question_mapper(path: str) -> dict[str, str]:
    """QuestionKey → question text. Useful for debugging + future tests."""
    out: dict[str, str] = {}
    with open(path, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            out[row["QuestionKey"]] = row["Question"]
    return out


# ----- Semester + course-number parsing ------------------------------------


_SEMESTER_RE = re.compile(r"^(Fall|Spring|Summer)\s+(\d{4})\b", re.IGNORECASE)
_SEMESTER_ORDER = {"spring": 1, "summer": 2, "fall": 3}
_SEMESTER_DISPLAY = {"spring": "Sp", "summer": "Su", "fall": "F"}


def parse_semester(project: str) -> tuple[int, int, str]:
    """('Fall 2020 16 Week ...') → (year=2020, order=3, display='F20').

    Returns (9999, 0, project[:8]) when the project string doesn't fit the
    expected pattern — caller can spot the fallback by sorting last.
    """
    m = _SEMESTER_RE.match(project)
    if not m:
        return 9999, 0, project[:8]
    term = m.group(1).lower()
    year = int(m.group(2))
    yy = year % 100
    return year, _SEMESTER_ORDER[term], f"{_SEMESTER_DISPLAY[term]}{yy:02d}"


# Course Code parse: "wl.202110.ECE.36800.001.17766" → ("ECE", "36800")
# "PWL.202510.ECE.46100.001.18613" → ("ECE", "46100")
# "wl.202410.VIP.17920.023.11485" → ("VIP", "17920")
_COURSE_CODE_RE = re.compile(r"^[A-Za-z]+\.\d+\.([A-Z]+)\.(\d+)\.")


def parse_course_number(course_code: str) -> tuple[str, str]:
    """('wl.202110.ECE.36800.001.17766') → ('ECE', '36800').

    Returns ('', '') when the code doesn't fit the expected pattern.
    """
    m = _COURSE_CODE_RE.match(course_code)
    if not m:
        return "", ""
    return m.group(1), m.group(2)


# ----- VIP detection -------------------------------------------------------


def is_vip_course(course_title: str, course_code: str = "") -> bool:
    """True when this section is part of the Vertically Integrated Projects
    program. The candidate's spec: detect via the course-title substring
    "In VIP" (covers "First Year Part In VIP", "Soph Part In VIP", ...).
    The course-code `.VIP.` segment is a corroborating signal — used as a
    fallback when the title is unusual."""
    if "in vip" in course_title.lower():
        return True
    return ".VIP." in course_code


# ----- Aggregation ---------------------------------------------------------


class ConceptScore(NamedTuple):
    """Per-concept aggregate inside a single course-row aggregate."""
    concept: CoreConcept
    mean: float          # weighted average of Likert values, pooled across sections
    response_count: int  # total respondents across pooled sections


class EvalAggregate(NamedTuple):
    """One row in the C.17 table — one course in one semester.

    `cie_average`, `cie_min`, `cie_max` are the across-concept aggregates
    the C.17 table renders. `concept_scores` carries the per-concept mean
    + response count so callers can debug or override individual concepts.
    """
    year: int
    semester_order: int   # 1=Spring, 2=Summer, 3=Fall
    semester_str: str     # "F20" / "Sp21" / etc.
    project: str          # raw EvaluationKit project string
    course_subject: str   # "ECE" / "VIP"
    course_number: str    # "36800"; VIP rolls up to "VIP (merged all sections)"
    course_title: str     # "Data Structures" / "Vertically Integrated Projects (VIP)"
    is_vip: bool
    enrollments: int      # total enrolled across pooled sections
    respondents: int      # total responded across pooled sections
    concept_scores: list[ConceptScore]  # per the 10 (or fewer for v657)
    cie_average: float    # avg of concept_scores' .mean values
    cie_min: float        # min  of concept_scores' .mean values
    cie_max: float        # max  of concept_scores' .mean values

    @property
    def cie_question_count(self) -> int:
        """How many of the 10 core concepts are represented for this row.
        v657-semester rows report 7; v496 / v737 rows report 10 in
        principle but a v737 row often reports 9 when a "where relevant"
        question (projects_objectives / exams_objectives) is silent."""
        return len(self.concept_scores)

    @property
    def cie_partial(self) -> bool:
        """True when fewer than the canonical 10 core concepts were
        asked / responded to for this course. Renderer adds a "*"
        marker to the CIE cell + a footnote ("Computed on the relevant
        subset of questions asked") so the asymmetry is visible."""
        return self.cie_question_count < len(CORE_CONCEPTS)


# Course titles that are NOT classroom teaching and should be dropped
# from the C.17 table — research / thesis supervision and independent
# study credits. Case-insensitive substring match against course_title.
# Add a pattern here when a new non-teaching course code shows up.
_NON_TEACHING_TITLE_PATTERNS: tuple[str, ...] = (
    "research",          # "Research PhD Thesis" (ECE 69900) etc.
    "thesis",            # any "*Thesis*" course
    "independent study", # ECE 49000 / 59000 etc.
    "directed study",
    "directed reading",
)


def _is_non_teaching(course_title: str) -> bool:
    """True when this course is a research/thesis-supervision or
    independent-study credit, not a classroom teaching activity. Such
    courses are dropped from C.17 per the candidate's spec."""
    t = course_title.lower()
    return any(p in t for p in _NON_TEACHING_TITLE_PATTERNS)


def _pool_concept_score(
    concept: CoreConcept, rows: list[RawEvalRow],
) -> ConceptScore | None:
    """Pool raw counts (Value × OptionRespondents) across all rows that
    belong to this concept, and return the weighted mean + total
    respondents. Returns None when no rows have option_respondents > 0
    (silent question — skip from the avg/min/max computation)."""
    if not rows:
        return None
    weighted_sum = sum(r.value * r.option_respondents for r in rows)
    total_responses = sum(r.option_respondents for r in rows)
    if total_responses == 0:
        return None
    return ConceptScore(
        concept=concept,
        mean=weighted_sum / total_responses,
        response_count=total_responses,
    )


def _course_group_key(row: RawEvalRow) -> tuple[str, bool, str, str]:
    """Aggregation key — what defines "one row in the C.17 table"?

    For VIP rows: (project, True, "", "") so ALL VIP sections of the same
    semester roll up into one row, regardless of First-Year / Soph /
    Senior / Senior-Design course-number variants (per the candidate's
    spec).

    For non-VIP rows: (project, False, subject, number) so multi-section
    same-course rows (e.g., the 5 sections of ECE 49595 in Fall 2020) roll
    up into one row but DIFFERENT courses in the same semester stay
    separate.
    """
    if is_vip_course(row.course_title, row.course_code):
        return (row.project, True, "", "")
    subject, number = parse_course_number(row.course_code)
    return (row.project, False, subject, number)


def aggregate_evaluations(rows: list[RawEvalRow]) -> list[EvalAggregate]:
    """Group raw rows into per-course-row EvalAggregates.

    Within each group:
      * Pool raw Likert counts (value × option_respondents) across all
        constituent sections / questions, partitioned by core concept.
      * Compute per-concept weighted mean.
      * Compute across-concept avg / min / max for the C.17 cell.
      * Compute total enrollments / respondents by deduplicating per
        course_uid (each section appears multiple times in the raw data;
        the headcount fields are constant per-section, so SUM after
        per-section dedupe).

    Output sort: ascending by (year, semester_order, course_subject,
    course_number) so the C.17 table reads chronologically forward.
    """
    groups: dict[tuple, list[RawEvalRow]] = defaultdict(list)
    for r in rows:
        if r.question_key not in QUESTION_KEY_TO_CONCEPT:
            continue  # non-core question — skip (out-of-scope for the 10)
        if _is_non_teaching(r.course_title):
            continue  # research/thesis/independent-study credit — not C.17
        groups[_course_group_key(r)].append(r)

    out: list[EvalAggregate] = []
    for key, group_rows in groups.items():
        project, is_vip, subject, number = key
        year, sem_order, sem_str = parse_semester(project)

        # Per-section enrollment / respondents — dedupe by (course_uid).
        # `enrollments` / `respondents` are constant per section, so we
        # take the first row's value per section and sum.
        per_section: dict[str, tuple[int, int]] = {}
        for r in group_rows:
            if r.course_uid not in per_section:
                per_section[r.course_uid] = (r.enrollments, r.respondents)
        total_enrollments = sum(e for e, _ in per_section.values())
        total_respondents = sum(s for _, s in per_section.values())

        # Per-concept pooled mean.
        by_concept: dict[CoreConcept, list[RawEvalRow]] = defaultdict(list)
        for r in group_rows:
            by_concept[QUESTION_KEY_TO_CONCEPT[r.question_key]].append(r)
        concept_scores: list[ConceptScore] = []
        for concept in CORE_CONCEPTS:
            score = _pool_concept_score(concept, by_concept.get(concept, []))
            if score is not None:
                concept_scores.append(score)

        if not concept_scores:
            # No core-question data in this group — drop silently (e.g.,
            # thesis-supervision courses that don't run a CIE survey).
            continue

        means = [s.mean for s in concept_scores]
        # Title + course_number routing:
        #   * VIP rolls up under a canonical umbrella title +
        #     "VIP (merged all sections)" course-number cell so the C.17
        #     reader sees one row regardless of how many year-level
        #     sections (First Year / Soph / Junior / Senior /
        #     Senior-Design) backed it that semester.
        #   * Non-VIP uses the first section's title — all sections of a
        #     single course offering share the same title.
        if is_vip:
            title = "Vertically Integrated Projects (VIP)"
            course_subject = "VIP"
            course_number_cell = "VIP (merged all sections)"
        else:
            title = group_rows[0].course_title
            course_subject = subject
            course_number_cell = f"{subject} {number}".strip() if subject else number

        out.append(EvalAggregate(
            year=year,
            semester_order=sem_order,
            semester_str=sem_str,
            project=project,
            course_subject=course_subject,
            course_number=course_number_cell,
            course_title=title,
            is_vip=is_vip,
            enrollments=total_enrollments,
            respondents=total_respondents,
            concept_scores=concept_scores,
            cie_average=sum(means) / len(means),
            cie_min=min(means),
            cie_max=max(means),
        ))

    out.sort(key=lambda a: (
        a.year, a.semester_order, a.course_subject, a.course_number,
    ))
    return out


def load_evaluations(rawdata_path: str) -> list[EvalAggregate]:
    """Convenience entry point — `rawdata_path` only; the question mapper
    is consulted via the in-code `QUESTION_KEY_TO_CONCEPT` map, not the
    mapper CSV (which exists for human-readable cross-reference, not
    runtime lookup)."""
    rows = parse_raw_data(rawdata_path)
    return aggregate_evaluations(rows)


# ----- Conversion to the C.17 CourseTaught schema -------------------------


def eval_aggregate_to_course_taught(a: "EvalAggregate") -> "CourseTaught":
    """Convert one aggregated EvaluationKit row into a C.17 CourseTaught.

    `responsibility` is left blank — the candidate populates it on a
    second pass (per the 260603 direction "Leave 'Responsibility' blank
    for now"). `is_new_course` defaults False — that's a publishing-side
    flag not derivable from EvaluationKit data.
    """
    from .types import CourseTaught
    return CourseTaught(
        year=a.year,
        semester_order=a.semester_order,
        semester_str=a.semester_str,
        title=a.course_title,
        is_new_course=False,
        course_number=a.course_number,
        responsibility="",
        responses=a.respondents,
        enrolled=a.enrollments,
        cie_average=a.cie_average,
        cie_min=a.cie_min,
        cie_max=a.cie_max,
        cie_partial=a.cie_partial,
        is_note_row=False,
    )


def load_courses_taught_from_csv(rawdata_path: str) -> "list[CourseTaught]":
    """End-to-end: CSV path → list[CourseTaught] ready to splice into
    the C.17 pipeline."""
    return [eval_aggregate_to_course_taught(a) for a in load_evaluations(rawdata_path)]
