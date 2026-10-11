"""The "do not read past this line" fold in markdown files. A leaf module: it imports nothing from careeros."""

from __future__ import annotations

FOLD_MARKER = "do not read past this line"


def above_fold(text: str) -> str:
    kept: list[str] = []
    for line in text.splitlines(keepends=True):
        if FOLD_MARKER in line:
            break
        kept.append(line)
    return "".join(kept)
