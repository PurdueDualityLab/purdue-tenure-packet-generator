#!/usr/bin/env bash
# One-command bootstrap: create venv if missing, install pubs-emitter
# in editable mode with dev extras (pylint, mypy, type stubs).
#
# Re-running is safe — it just re-syncs deps.
set -euo pipefail

cd "$(dirname "$0")"

VENV_DIR=".venv"

if [[ ! -d "$VENV_DIR" ]]; then
    echo "==> Creating venv in $VENV_DIR"
    python3 -m venv "$VENV_DIR"
fi

echo "==> Upgrading pip"
"$VENV_DIR/bin/python" -m pip install --quiet --upgrade pip

echo "==> Installing pubs-emitter (editable) + dev extras"
"$VENV_DIR/bin/python" -m pip install --quiet -e ".[dev]"

echo ""
echo "Done. Activate with:"
echo "    source $VENV_DIR/bin/activate"
echo ""
echo "Then:"
echo "    pubs-emitter --bib my_papers.bib --non-scholar non-scholar-work.yaml"
echo ""
echo "Or run the root driver directly without activating:"
echo "    ./pubs-emitter.py --bib my_papers.bib"
echo ""
echo "Dev tools:"
echo "    $VENV_DIR/bin/pylint src/pubs_emitter"
echo "    $VENV_DIR/bin/mypy"
