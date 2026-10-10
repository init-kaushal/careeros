"""What a piece of text claims: its technologies and its numbers."""

from __future__ import annotations

import re
from dataclasses import dataclass

from careeros.core.memory.lexicon import Lexicon, TechSpan
from careeros.core.memory.normalize import Scan, scan

_VERSION_AFTER_TECH = re.compile(r"\s?v?\d+(?:\.\d+)*(?![\w%])")


@dataclass
class Analysis:
    techs: list[TechSpan]
    scan: Scan
    version_spans: list[tuple[int, int]]


def analyze(text: str, lexicon: Lexicon) -> Analysis:
    techs = lexicon.find_techs(text)
    versions: list[tuple[int, int]] = []
    for span in techs:
        match = _VERSION_AFTER_TECH.match(text, span.end)
        if match and match.end() > match.start():
            versions.append((span.end, match.end()))
    return Analysis(techs, scan(text, skip=versions), versions)


def derive_facets(text: str, lexicon: Lexicon) -> dict:
    """The `metrics` and `technologies` fields for a fact whose claim is `text`."""
    analysis = analyze(text, lexicon)
    return {
        "metrics": [n.metric() for n in analysis.scan.numbers],
        "technologies": list(dict.fromkeys(t.canonical for t in analysis.techs)),
    }
