"""Isolated tests for pubs_emitter.evaluations.

Fixtures are inline-built (no asset CSV dependency) so each test pins one
invariant on a minimal synthetic dataset. Integration against the real
EvaluationKit dumps lives in a separate smoke check at module-author time.
"""
from __future__ import annotations

import textwrap

import pytest

from pubs_emitter.evaluations import (
    CORE_CONCEPTS,
    ConceptScore,
    EvalAggregate,
    QUESTION_KEY_TO_CONCEPT,
    RawEvalRow,
    _is_non_teaching,
    _pool_concept_score,
    aggregate_evaluations,
    eval_aggregate_to_course_taught,
    is_vip_course,
    parse_course_number,
    parse_question_mapper,
    parse_raw_data,
    parse_semester,
)


# ----- Question-alias coverage --------------------------------------------


class TestQuestionKeyMapping:
    """The mapping table must cover every QuestionKey shipped in the asset
    `questionmapper.csv` AND every concept must be reachable from at least
    one published wording."""

    def test_every_core_concept_has_at_least_one_alias(self) -> None:
        present = set(QUESTION_KEY_TO_CONCEPT.values())
        missing = set(CORE_CONCEPTS) - present
        assert not missing, f"concepts with no alias: {sorted(missing)}"

    def test_no_concept_outside_the_canonical_10(self) -> None:
        # Every value in the mapping is one of the named 10.
        bad = set(QUESTION_KEY_TO_CONCEPT.values()) - set(CORE_CONCEPTS)
        assert not bad, f"bogus concepts: {sorted(bad)}"

    def test_each_question_family_maps_to_distinct_concepts(self) -> None:
        # Within a single QuestionKey family (496xxx / 657xxx / 737xxx)
        # no concept appears twice — i.e., one question per concept per
        # survey-wording revision.
        from collections import defaultdict
        per_family: dict[str, list[str]] = defaultdict(list)
        for k, concept in QUESTION_KEY_TO_CONCEPT.items():
            per_family[k[:3]].append(concept)
        for fam, concepts in per_family.items():
            assert len(concepts) == len(set(concepts)), (
                f"family v{fam} maps two keys to the same concept: {concepts}"
            )


# ----- Semester + course-number parsing ----------------------------------


class TestParseSemester:
    @pytest.mark.parametrize(
        "project, expected",
        [
            ("Fall 2020 16 Week 11/23-12/6 (1)", (2020, 3, "F20")),
            ("Spring 2021 16 Week 4/19-5/2 (1)", (2021, 1, "Sp21")),
            ("Summer 2024 8 Week ...", (2024, 2, "Su24")),
            ("Fall 2025 Core -4 16 Week 12/1-12/14 (1)", (2025, 3, "F25")),
        ],
    )
    def test_parses_term_year_display(
        self, project: str, expected: tuple[int, int, str]
    ) -> None:
        assert parse_semester(project) == expected

    def test_unparseable_sorts_last(self) -> None:
        y, _, _ = parse_semester("--not a semester--")
        assert y == 9999


class TestParseCourseNumber:
    @pytest.mark.parametrize(
        "course_code, expected",
        [
            ("wl.202110.ECE.36800.001.17766", ("ECE", "36800")),
            ("PWL.202510.ECE.46100.001.18613", ("ECE", "46100")),
            ("wl.202410.VIP.17920.023.11485", ("VIP", "17920")),
        ],
    )
    def test_extracts_subject_and_number(
        self, course_code: str, expected: tuple[str, str]
    ) -> None:
        assert parse_course_number(course_code) == expected

    def test_unparseable_returns_blanks(self) -> None:
        assert parse_course_number("garbage") == ("", "")


# ----- VIP detection ------------------------------------------------------


class TestIsVipCourse:
    @pytest.mark.parametrize(
        "title",
        [
            "First Year Part In VIP",
            "Soph Part In VIP",
            "Senior Design Part In VIP II",
            "First-Year Part In VIP Lim",  # hyphenated variant
        ],
    )
    def test_in_vip_substring_matches(self, title: str) -> None:
        assert is_vip_course(title)

    def test_course_code_vip_segment_corroborates(self) -> None:
        # Title doesn't say "in vip" but the code does — corroborating
        # signal kicks in.
        assert is_vip_course("Strange Title", "wl.202410.VIP.17920.023.11485")

    def test_non_vip(self) -> None:
        assert not is_vip_course("Software Engineering")
        assert not is_vip_course(
            "Data Structures", "wl.202110.ECE.36800.001.17766",
        )


# ----- Pooled concept-score math ------------------------------------------


def _row(
    *, qkey: str, value: int, n: int,
    project: str = "Fall 2020 16 Week ...",
    course_code: str = "wl.202110.ECE.36800.001.17766",
    course_title: str = "Data Structures",
    course_uid: str | None = None,
    enrollments: int = 100,
    respondents: int = 50,
    mean: float = 4.0,
) -> RawEvalRow:
    """Build one RawEvalRow with sensible defaults — only the fields the
    test cares about need to be specified."""
    return RawEvalRow(
        project=project,
        course_code=course_code,
        course_title=course_title,
        course_uid=course_uid or course_code,
        question_key=qkey,
        enrollments=enrollments,
        respondents=respondents,
        mean=mean,
        value=value,
        option_respondents=n,
    )


class TestPoolConceptScore:
    def test_single_likert_5_one_respondent(self) -> None:
        # 1 respondent gave 5/5 → mean=5.0, n=1.
        score = _pool_concept_score(
            "instructor_fair",
            [_row(qkey="496883-0", value=5, n=1)],
        )
        assert score == ConceptScore(
            concept="instructor_fair", mean=5.0, response_count=1,
        )

    def test_weighted_average(self) -> None:
        # 2×5 + 3×4 = 22; n=5 → mean=4.4.
        rows = [
            _row(qkey="496883-0", value=5, n=2),
            _row(qkey="496883-0", value=4, n=3),
        ]
        score = _pool_concept_score("instructor_fair", rows)
        assert score is not None
        assert score.mean == pytest.approx(4.4)
        assert score.response_count == 5

    def test_zero_responses_returns_none(self) -> None:
        # `n=0` for every row → silent question, no score emitted.
        rows = [
            _row(qkey="496883-0", value=5, n=0),
            _row(qkey="496883-0", value=4, n=0),
        ]
        assert _pool_concept_score("instructor_fair", rows) is None

    def test_pools_across_sections(self) -> None:
        # 5 respondents at value=5 in section A; 5 at value=3 in section B
        # → pooled (5+3)/2 = 4.0 weighted by counts.
        section_a = _row(qkey="496883-0", value=5, n=5, course_uid="A")
        section_b = _row(qkey="496883-0", value=3, n=5, course_uid="B")
        score = _pool_concept_score("instructor_fair", [section_a, section_b])
        assert score is not None
        assert score.mean == pytest.approx(4.0)
        assert score.response_count == 10


# ----- End-to-end aggregate_evaluations -----------------------------------


def _v496_full_likert(qkey: str, mean: float, n: int, **kw) -> list[RawEvalRow]:
    """5 rows (one per Likert value 1..5) where ALL n respondents picked
    `int(round(mean))`. Quick-and-dirty fixture builder — caller passes the
    integer target value via `mean`."""
    target = int(round(mean))
    rows: list[RawEvalRow] = []
    for v in range(1, 6):
        rows.append(_row(
            qkey=qkey, value=v,
            n=n if v == target else 0,
            mean=float(target), **kw,
        ))
    return rows


class TestAggregateEvaluations:
    def test_single_course_single_section_v496(self) -> None:
        # One course (Data Structures, ECE 36800 Fall 2020) with N=10
        # respondents all picking 5 on each of the 10 v496 questions.
        rows: list[RawEvalRow] = []
        for qkey in (
            "496875-0", "496878-0", "496880-0", "496882-0", "496883-0",
            "496793-0", "496794-0", "496872-0", "496873-0", "496874-0",
        ):
            rows.extend(_v496_full_likert(qkey, 5.0, 10))
        out = aggregate_evaluations(rows)
        assert len(out) == 1
        a = out[0]
        assert a.semester_str == "F20"
        assert a.course_subject == "ECE"
        # `course_number` is the table-cell string ("ECE 36800"), not the
        # bare digit-only number (the bare form lives in the group key
        # but doesn't leak out of the aggregator).
        assert a.course_number == "ECE 36800"
        assert a.is_vip is False
        assert a.cie_average == 5.0
        assert a.cie_min == 5.0
        assert a.cie_max == 5.0
        assert a.cie_question_count == 10
        assert a.cie_partial is False

    def test_two_sections_same_course_merge(self) -> None:
        # Two sections of the same ECE 49595 course in Fall 2020 — should
        # merge into ONE row. Section A all picks 5; Section B all picks 3.
        # Pooled mean = (5+3)/2 = 4.0 across all 10 concepts.
        rows: list[RawEvalRow] = []
        for qkey in (
            "496875-0", "496878-0", "496880-0", "496882-0", "496883-0",
            "496793-0", "496794-0", "496872-0", "496873-0", "496874-0",
        ):
            rows.extend(_v496_full_likert(
                qkey, 5.0, 5,
                course_code="wl.202110.ECE.49595.001.A",
                course_uid="A",
                course_title="Software Engineering Tools",
            ))
            rows.extend(_v496_full_likert(
                qkey, 3.0, 5,
                course_code="wl.202110.ECE.49595.002.B",
                course_uid="B",
                course_title="Software Engineering Tools",
            ))
        out = aggregate_evaluations(rows)
        assert len(out) == 1
        a = out[0]
        assert a.course_number == "ECE 49595"
        assert a.cie_average == pytest.approx(4.0)
        # Pooled headcount: enrollments dedup'd per course_uid then summed.
        # Each fixture row uses default enrollments=100, respondents=50;
        # 2 sections → 200 enrolled, 100 respondents.
        assert a.enrollments == 200
        assert a.respondents == 100

    def test_vip_sections_roll_up_per_semester(self) -> None:
        # Two VIP sections in same Spring 2024 semester (different course
        # numbers — First Year vs Soph) → one row, course_subject="VIP".
        rows: list[RawEvalRow] = []
        for qkey in ("737245-0", "737246-0", "737247-0",
                     "737248-0", "737249-0", "737250-0"):
            rows.extend(_v496_full_likert(
                qkey, 4.0, 5,
                project="Spring 2024 Core -4 16 Week ...",
                course_code="PWL.202420.VIP.17920.023.A",
                course_uid="VA",
                course_title="First Year Part In VIP",
            ))
            rows.extend(_v496_full_likert(
                qkey, 4.0, 5,
                project="Spring 2024 Core -4 16 Week ...",
                course_code="PWL.202420.VIP.27920.023.B",
                course_uid="VB",
                course_title="Soph Part In VIP",
            ))
        out = aggregate_evaluations(rows)
        assert len(out) == 1
        a = out[0]
        assert a.is_vip is True
        assert a.course_subject == "VIP"
        # Title rolls up under the canonical VIP umbrella, not either
        # section's literal title.
        assert a.course_title == "Vertically Integrated Projects (VIP)"
        # Pooled headcount across the 2 sections.
        assert a.enrollments == 200

    def test_v657_semester_has_seven_concepts_not_ten(self) -> None:
        # Spring 2022 Co-admin used v657 which ships only 7 of the 10
        # concepts. The aggregator must still produce a row but
        # `cie_question_count` should report 7, not 10.
        rows: list[RawEvalRow] = []
        for qkey in (
            "657523-0", "657534-0", "657535-0", "657536-0",
            "657537-0", "657538-0", "657539-0",
        ):
            rows.extend(_v496_full_likert(
                qkey, 5.0, 10,
                project="Spring 2022 Co-admin",
                course_code="wl.202220.ECE.59500.029.X",
            ))
        out = aggregate_evaluations(rows)
        assert len(out) == 1
        a = out[0]
        assert a.cie_question_count == 7
        assert a.cie_average == 5.0


class TestParseCSVFixtures:
    """End-to-end: write inline CSVs, parse them back."""

    def test_parse_question_mapper_round_trip(self, tmp_path) -> None:
        path = tmp_path / "qmap.csv"
        path.write_text(
            "QuestionKey,QuestionType,Question\n"
            "496875-0,Single Selection,The instructor clearly explains material.\n",
            encoding="utf-8",
        )
        m = parse_question_mapper(str(path))
        assert m == {"496875-0": "The instructor clearly explains material."}

    def test_parse_raw_data_typed_round_trip(self, tmp_path) -> None:
        path = tmp_path / "raw.csv"
        path.write_text(textwrap.dedent("""
            Project,Course Code,Course Title,Course UniqueID,QuestionKey,Enrollments,Respondents,ResponseRate,Mean,Std,Value,OptionRespondents,OptionResponseRate,Comments,BenchmarkLabel1,BenchmarkMean1,BenchmarkStd1
            Fall 2020,wl.X,DS,wl.X,496875-0,99,43,43.4,4.488,0.67,5,25,58.1,,Bench,4.07,0.93
        """).strip(), encoding="utf-8")
        rows = parse_raw_data(str(path))
        assert len(rows) == 1
        r = rows[0]
        assert r.question_key == "496875-0"
        assert r.value == 5
        assert r.option_respondents == 25
        assert r.enrollments == 99
        assert r.respondents == 43
        assert r.mean == pytest.approx(4.488)


class TestNonTeachingFilter:
    @pytest.mark.parametrize(
        "title, is_excluded",
        [
            ("Research PhD Thesis", True),
            ("MS Thesis", True),
            ("Independent Study", True),
            ("Directed Study", True),
            ("Directed Reading", True),
            ("Data Structures", False),
            ("Software Engineering", False),
            ("First Year Part In VIP", False),
        ],
    )
    def test_non_teaching_matching(self, title: str, is_excluded: bool) -> None:
        assert _is_non_teaching(title) is is_excluded

    def test_aggregator_drops_research_courses(self) -> None:
        # Research PhD Thesis rows in the raw data should never produce
        # an aggregate, even with valid CIE responses.
        rows: list[RawEvalRow] = []
        for qkey in (
            "496875-0", "496878-0", "496880-0", "496882-0", "496883-0",
            "496793-0", "496794-0", "496872-0", "496873-0", "496874-0",
        ):
            rows.extend(_v496_full_likert(
                qkey, 5.0, 1,
                course_code="wl.202410.ECE.69900.088.A",
                course_title="Research PhD Thesis",
            ))
        assert aggregate_evaluations(rows) == []


class TestCiePartialFlag:
    def test_partial_set_when_fewer_than_ten_concepts(self) -> None:
        # v657 — 7 concepts only → cie_partial True.
        rows: list[RawEvalRow] = []
        for qkey in (
            "657523-0", "657534-0", "657535-0", "657536-0",
            "657537-0", "657538-0", "657539-0",
        ):
            rows.extend(_v496_full_likert(qkey, 5.0, 10))
        out = aggregate_evaluations(rows)
        assert out[0].cie_partial is True
        assert out[0].cie_question_count == 7

    def test_complete_clears_partial_flag(self) -> None:
        # All 10 concepts respond → cie_partial False.
        rows: list[RawEvalRow] = []
        for qkey in (
            "496875-0", "496878-0", "496880-0", "496882-0", "496883-0",
            "496793-0", "496794-0", "496872-0", "496873-0", "496874-0",
        ):
            rows.extend(_v496_full_likert(qkey, 5.0, 10))
        out = aggregate_evaluations(rows)
        assert out[0].cie_partial is False
        assert out[0].cie_question_count == 10


class TestVipRollupCourseNumber:
    def test_vip_course_number_cell_is_canonical_string(self) -> None:
        rows: list[RawEvalRow] = []
        for qkey in (
            "737245-0", "737246-0", "737247-0",
            "737248-0", "737249-0", "737250-0",
        ):
            rows.extend(_v496_full_likert(
                qkey, 4.0, 5,
                project="Spring 2024 Core -4 16 Week ...",
                course_code="PWL.202420.VIP.17920.023.A",
                course_uid="VA",
                course_title="First Year Part In VIP",
            ))
        out = aggregate_evaluations(rows)
        assert len(out) == 1
        a = out[0]
        assert a.course_subject == "VIP"
        # Per the candidate's 260603 spec: VIP rolls up to a single
        # "merged all sections" course-number cell.
        assert a.course_number == "VIP (merged all sections)"


class TestEvalAggregateToCourseTaught:
    def test_converter_round_trips_core_fields(self) -> None:
        agg = EvalAggregate(
            year=2024, semester_order=3, semester_str="F24",
            project="Fall 2024 ...", course_subject="ECE",
            course_number="ECE 46100", course_title="Software Engineering",
            is_vip=False, enrollments=130, respondents=55,
            concept_scores=[
                ConceptScore(c, 4.0, 55) for c in CORE_CONCEPTS
            ],
            cie_average=4.0, cie_min=3.5, cie_max=4.5,
        )
        ct = eval_aggregate_to_course_taught(agg)
        assert ct.year == 2024
        assert ct.semester_order == 3
        assert ct.semester_str == "F24"
        assert ct.title == "Software Engineering"
        assert ct.course_number == "ECE 46100"
        # Responsibility intentionally blank — candidate populates on a
        # second pass.
        assert ct.responsibility == ""
        assert ct.responses == 55
        assert ct.enrolled == 130
        assert ct.cie_average == 4.0
        assert ct.cie_partial is False
        assert ct.is_note_row is False

    def test_converter_propagates_partial_flag(self) -> None:
        agg = EvalAggregate(
            year=2022, semester_order=1, semester_str="Sp22",
            project="Spring 2022 ...", course_subject="ECE",
            course_number="ECE 59500", course_title="Advance SE",
            is_vip=False, enrollments=40, respondents=20,
            concept_scores=[
                # Only 7 of 10 concepts present (v657 outlier).
                ConceptScore(c, 5.0, 20) for c in CORE_CONCEPTS[:7]
            ],
            cie_average=5.0, cie_min=5.0, cie_max=5.0,
        )
        ct = eval_aggregate_to_course_taught(agg)
        assert ct.cie_partial is True
