#!/usr/bin/env python3
"""Driver: thin CLI shim for the pubs_emitter package.

After `./setup.sh` (or `pip install -e .`), you can also run the shorter
`pubs-emitter` from your PATH — same entry point.
"""
from __future__ import annotations

import os
import sys


# Allow running this script directly from a checkout (no install required):
# add `src/` to sys.path so `import pubs_emitter` resolves.
_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.join(_HERE, "src")
if os.path.isdir(_SRC) and _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from pubs_emitter.cli import main  # noqa: E402  (sys.path tweak above must come first)


if __name__ == "__main__":
    main()
