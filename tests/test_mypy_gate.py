"""Mypy gate — the codebase must pass `mypy` cleanly.

This test runs mypy via subprocess and asserts exit 0. It uses the
config in `pyproject.toml` (the `files = [...]` list there is the source
of truth for what gets checked).

Why a test gate rather than CI-only:
  * `pytest -q` is the canonical fast feedback loop; type errors should
    surface in the same place as runtime errors.
  * Type discipline (see CLAUDE.md §"Typed Python") is load-bearing for
    catching regressions during refactors (styles → IR). The gate makes
    "I refactored and didn't run mypy" impossible.

The test is skipped if `mypy` is not installed (the dev-extras path:
`pip install -e .[dev]`). When skipped, surface a clear message rather
than silently passing — that's the same posture as the rest of the
discipline-tests in this suite.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent


def _mypy_available() -> bool:
    """`python -m mypy --version` is the canonical install-check —
    avoids PATH-resolution flakiness (`shutil.which('mypy')` is None
    inside a venv where only `.venv/bin/mypy` is shadowed)."""
    try:
        r = subprocess.run(
            [sys.executable, "-m", "mypy", "--version"],
            capture_output=True, check=False, timeout=10,
        )
        return r.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def test_mypy_gate() -> None:
    """Codebase passes `mypy` cleanly.

    Source of truth for what's checked: `[tool.mypy].files` in
    pyproject.toml. We invoke `python -m mypy` (no extra args) so the
    pyproject config drives everything.
    """
    if not _mypy_available():
        # Tolerate environments where dev-extras aren't installed; only
        # CI + a properly-set-up dev shell run the gate. The message
        # makes the gap obvious.
        pytest.skip(
            "mypy not installed; run `pip install -e .[dev]` to enable "
            "the type-check gate."
        )

    result = subprocess.run(
        [sys.executable, "-m", "mypy"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        # Surface the full mypy report so the failure is actionable
        # from the pytest output alone — no need to re-run mypy by hand.
        pytest.fail(
            f"mypy gate failed (exit {result.returncode}).\n\n"
            f"stdout:\n{result.stdout}\n\nstderr:\n{result.stderr}",
            pytrace=False,
        )
