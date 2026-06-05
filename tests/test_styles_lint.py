"""Pytest hook for `tools/lint_styles.py`.

Runs the raw-control-code lint against `src/pubs_emitter/rtf.py` so
new drift (a developer typing `\\b ... \\b0` inline instead of
routing through `styles.styled_inline`) gets caught at PR review
time instead of visual-diff time.

The lint owns its own allowlist + exemption protocol — this test
just invokes it and reports any findings as a single assertion
failure with the full finding list embedded.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools.lint_styles import scan_file


def test_no_raw_control_codes_outside_style_registry() -> None:
    target = ROOT / "src" / "pubs_emitter" / "rtf.py"
    findings = scan_file(target)
    if findings:
        # Format the full finding list inside the assertion so a CI run
        # surfaces exactly which line broke the invariant.
        report = "\n  - ".join(
            f"{target}:{ln}: [{kind}] {code} — {text.strip()[:120]}"
            for ln, kind, code, text in findings
        )
        pytest.fail(
            f"{len(findings)} raw-control-code finding(s) in {target.name}:\n  - {report}\n\n"
            "Route the emit through `styles.emit_styled` / `styles.styled_inline` "
            "OR add a `# lint-allow: raw-rtf — <justification>` marker on or "
            "near the offending line.",
        )
