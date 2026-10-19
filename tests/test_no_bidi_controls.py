"""Trojan-source guard: no tracked text file may contain a Unicode bidirectional control character. Fixtures that need
one write it as an escape (`"\\u202e"`), never as the raw character, so no allow-list is needed."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BIDI = re.compile("[‪-‮⁦-⁩]")
SCANNED = ("docs/", "tests/", "careeros/", "README", "CHANGELOG")


def tracked_files() -> list[str]:
    out = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, text=True, timeout=30, check=True)
    return [f for f in out.stdout.split("\0") if f.startswith(SCANNED)]


def test_no_tracked_text_file_contains_a_bidi_control_character() -> None:
    offenders = []
    for name in tracked_files():
        try:
            text = (ROOT / name).read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue                                     # binary or unreadable: not a text file
        for number, line in enumerate(text.splitlines(), 1):
            if BIDI.search(line):
                offenders.append(f"{name}:{number}")
    assert not offenders, f"raw bidi control characters (write them as escapes): {offenders}"


def test_the_scan_sees_the_files_it_is_meant_to_cover() -> None:
    files = tracked_files()
    assert "docs/END_TO_END_TEST.md" in files and "careeros/core/approvals/render.py" in files
    assert any(f.startswith("tests/") for f in files)


def test_the_pattern_matches_the_characters_it_forbids() -> None:
    for code in [*range(0x202A, 0x202F), *range(0x2066, 0x206A)]:
        assert BIDI.search(chr(code))
    assert not BIDI.search("plain text ​")
