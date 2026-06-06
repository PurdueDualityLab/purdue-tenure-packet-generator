"""`Writer` protocol — the abstract translator from IR to a
format-specific string.

`RtfWriter` (in `writer_rtf.py`) is the concrete RTF implementation.
A future `HtmlWriter` would parallel it for HTML output, consuming the
same `list[Block]` input.

Design: `docs/design/ir-based-emit-disentangling-260606.md`.
"""
from __future__ import annotations

from typing import Protocol

from .ir import Block


class Writer(Protocol):
    """Translate IR blocks to a complete document body string.

    The protocol is structural (typing.Protocol) — a class becomes a
    `Writer` by exposing `render(blocks) -> str`, no inheritance
    required. The IR layer accepts any `Writer`; format choice is
    decided at call time.
    """

    def render(self, blocks: list[Block]) -> str:
        """Produce the format-specific output for the given IR."""
        ...
