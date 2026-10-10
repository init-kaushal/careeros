"""Number, year and duration extraction, name normalisation and the lexicon."""

from __future__ import annotations

from pathlib import Path

import pytest

from careeros.core.memory import facets
from careeros.core.memory.lexicon import LexiconError, load_lexicon
from careeros.core.memory.normalize import norm_org, norm_text, scan, split_sentences

LX = load_lexicon()


def numbers(text: str) -> list[tuple[float, str, bool]]:
    return [(n.value, n.unit, n.plus) for n in scan(text).numbers]


@pytest.mark.parametrize("text,expected", [
    ("35%", [(35.0, "percent", False)]),
    ("35 percent", [(35.0, "percent", False)]),
    ("99.95% uptime", [(99.95, "percent", False)]),
    ("1M+ users", [(1_000_000.0, "count", True)]),
    ("5,000 requests", [(5000.0, "count", False)]),
    ("1,00,000 users", [(100000.0, "count", False)]),
    ("$2.4M ARR", [(2_400_000.0, "currency:USD", False)]),
    ("₹5 crore", [(50_000_000.0, "currency:INR", False)]),
    ("10x faster", [(10.0, "multiplier", False)]),
    ("CGPA 7.7/10", [(0.77, "score", False)]),
    ("40+ services", [(40.0, "count", True)]),
    ("2 million events", [(2_000_000.0, "count", False)]),
])
def test_numbers(text: str, expected: list) -> None:
    assert numbers(text) == expected


@pytest.mark.parametrize("text", [
    "S3 and EC2 and p99", "Call +91 80032 93018", "https://example.com/2031/45", "a@b.com 12345678",
    "on-call 24/7", "v1.2.3 released", "6/2022", "a 4-year degree", "3 years ago",
])
def test_things_that_are_not_claims_are_not_numbers(text: str) -> None:
    assert numbers(text) == []


def test_years_and_ranges() -> None:
    found = scan("Jan 2024 - Present, 2021–2023, since 2019, and 2000 users")
    assert [y.year for y in found.years] == [2024, 2021, 2023, 2019]
    assert [(n.value, n.unit) for n in found.numbers] == [(2000.0, "count")]


def test_durations() -> None:
    found = scan("7+ years of experience, a 5-year plan and 3 years ago")
    assert [(d.years, d.plus) for d in found.durations] == [(7, True), (5, False)]


def test_versions_after_a_technology_are_not_numbers() -> None:
    analysis = facets.analyze("Used Python 3.11 and Go 1.22 for 3x speedups", LX)
    assert [(n.value, n.unit) for n in analysis.scan.numbers] == [(3.0, "multiplier")]
    assert [t.canonical for t in analysis.techs] == ["Python", "Go"]


def test_derive_facets() -> None:
    derived = facets.derive_facets("Reduced AWS costs by 35% using Kafka and k8s.", LX)
    assert derived["technologies"] == ["AWS", "Kafka", "Kubernetes"]
    assert derived["metrics"] == [{"raw": "35%", "value": 35.0, "unit": "percent", "plus": False}]


def test_name_normalisation() -> None:
    assert norm_text("Procter & Gamble") == "procter and gamble"
    assert norm_org("Eka Care Pvt. Ltd.", LX.org_suffixes) == "eka care"
    assert norm_org("Ltd", LX.org_suffixes) == "ltd"
    assert split_sentences("Built X. Cut 3.5% with Node.js. Done!") == ["Built X.", "Cut 3.5% with Node.js.", "Done!"]


@pytest.mark.parametrize("text,expected", [
    ("Used golang and k8s with Postgres", ["Go", "Kubernetes", "PostgreSQL"]),
    ("C++, C# and C", ["C++", "C#", "C"]),
    ("Node.js beats Node", ["Node.js", "Node.js"]),
    ("I will go ahead; Rust is fun; rust never sleeps", ["Rust"]),
    ("R rules", ["R"]),
    ("Ran spark jobs on Spark", ["Spark"]),
])
def test_technology_recognition(text: str, expected: list[str]) -> None:
    assert [t.canonical for t in LX.find_techs(text)] == expected


def test_user_additions_extend_the_lexicon(tmp_path: Path) -> None:
    (tmp_path / "career").mkdir()
    (tmp_path / "career" / "lexicon.yaml").write_text("technologies:\n  - {name: Terragrunt, aliases: [tg]}\n", encoding="utf-8")
    lexicon = load_lexicon(tmp_path)
    assert [t.canonical for t in lexicon.find_techs("We use tg daily")] == ["Terragrunt"]
    assert lexicon.with_extra_techs(["Quasar"]).canonical_tech("quasar") == "Quasar"


def test_a_broken_user_lexicon_is_a_clear_error(tmp_path: Path) -> None:
    (tmp_path / "career").mkdir()
    (tmp_path / "career" / "lexicon.yaml").write_text("technologies:\n  - {aliases: [x]}\n", encoding="utf-8")
    with pytest.raises(LexiconError, match="needs a name"):
        load_lexicon(tmp_path)
    (tmp_path / "career" / "lexicon.yaml").write_text("- not a mapping\n", encoding="utf-8")
    with pytest.raises(LexiconError, match="mapping"):
        load_lexicon(tmp_path)
