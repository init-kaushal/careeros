"""The golden 0.5.0 evaluation workspace (tests/fixtures/evaluation_v1) stays verifiable and its v1 rules unchanged.

The fixture was produced by the real 0.5.0 CLI (invented data). It holds a compensation-capped result stored under
rules/basis version 1. Whatever the current default versions become, this record must keep scoring to its stored
result, report no tamper and no EVL010, and its ledger chain must verify.
"""

from __future__ import annotations

import shutil
from fractions import Fraction
from pathlib import Path

import pytest

from careeros.core import ledger
from careeros.core.evaluation import canon, integrity, record, rules
from careeros.core.evaluation.score import action_item_text, score, stored_score
from careeros.core.validation import validate_workspace

FIXTURE = Path(__file__).parent / "fixtures" / "evaluation_v1"
JOB = "jobs/discovered/example-platform"


@pytest.fixture
def ws(tmp_path: Path) -> Path:
    root = tmp_path / "ws"
    shutil.copytree(FIXTURE, root)
    return root


def stored(root: Path) -> record.Record:
    return record.load(root / JOB / "evaluation.md", root)


def test_validate_has_no_tamper_or_unsupported_issue(ws: Path) -> None:
    codes = {i.code for i in validate_workspace(ws)}
    assert "EVL007" not in codes and "EVL010" not in codes
    assert not [i for i in validate_workspace(ws) if i.severity == "error"]


def test_integrity_reports_no_tamper(ws: Path) -> None:
    rec = stored(ws)
    report = integrity.check(rec, record.read_inputs(ws, ws / JOB))
    assert report.tampered == () and report.unsupported is None
    assert not [i for i in report.issues if i.code in ("EVL007", "EVL010")]


def test_score_of_the_stored_basis_equals_the_stored_result(ws: Path) -> None:
    result = stored(ws).result
    assert result["stamps"]["rules"] == "1" and result["basis"]["basis_version"] == "1"
    got = score(result["basis"])
    assert stored_score(got.coverage) == result["coverage"]
    assert stored_score(got.fit) == result["fit"]
    assert stored_score(got.combined) == result["combined"]
    assert got.tier.value == result["tier"]
    assert got.confidence.value == result["confidence"]
    assert [{"code": c.code, "ceiling": c.ceiling.value, "reason": c.reason} for c in got.caps] == result["caps"]
    assert [action_item_text(a) for a in got.action_items] == result["action_items"]
    assert [c.code for c in got.caps] == ["compensation"]                   # the fixture exercises the compensation cap


def test_stored_basis_hash_matches(ws: Path) -> None:
    result = stored(ws).result
    assert canon.sha256_hex(canon.canonical_json(result["basis"])) == result["basis_sha256"]


def test_ledger_chain_verifies(ws: Path) -> None:
    assert ledger.verify_chain(ws) == []


def test_rules_and_basis_version_1_are_unchanged() -> None:
    r = rules.rules_for("1")
    assert isinstance(r, rules.RulesV1)
    assert r.version == "1"
    assert (r.coverage_share, r.fit_share) == (Fraction(65, 100), Fraction(35, 100))
    assert (r.tier_pursue, r.tier_stretch, r.tier_weak) == (Fraction(7, 10), Fraction(1, 2), Fraction(3, 10))
    assert dict(r.class_weights) == {"must": 3, "strong": 2, "nice": 1}
    assert dict(r.credits) == {"covered": 1, "adjacent": Fraction(1, 2), "gap": 0}
    assert dict(r.fit_weights) == {"high": 3, "medium": 2, "low": 1}
    assert dict(r.fit_credits) == {"yes": 1, "partial": Fraction(1, 2), "no": 0}
    assert (r.claimed_share, r.unverifiable_share, r.unverifiable_share_severe, r.min_requirements) == (
        Fraction(1, 2), Fraction(1, 4), Fraction(1, 2), Fraction(3))
    assert not hasattr(r, "fx_window_days")
    schema = rules.basis_schema_for("1")
    assert schema.version == "1"
    assert schema.keys == ("basis_version", "rules_version", "requirement_count", "rubric_state", "requirements", "fit",
                           "cap_inputs")
