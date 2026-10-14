"""`careeros comp`: the service, the two ledger events and their dedupe, and the CMP section of `validate`. Invented data."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from helpers import add_job, assert_only_ledger_appended, make_legacy_workspace, make_workspace, snapshot, state
from typer.testing import CliRunner

from careeros.cli.main import app
from careeros.core import ledger
from fractions import Fraction

from careeros.core.compensation import record, service
from careeros.core.compensation.derive import FloorInfo, cap_applies, derive_compensation, to_basis
from careeros.core.evaluation import markets as markets_mod
from careeros.core.validation import validate_workspace

runner = CliRunner()

PROFILE = """# Example Career Profile

## Markets
| Market | Min base (per year) | Sponsorship | Priority |
|--------|---------------------|-------------|----------|
| India (home) | ₹35,00,000 | not needed | 1 |
| Elsewhere | TBD | preferred | 2 |
| Twice | ₹40,00,000 | required | 3 |
| Twice | ₹41,00,000 | required | 4 |
"""

PROFILE_TWO_HEADINGS = PROFILE + "\n## Markets\n| Market | Min base (per year) |\n|---|---|\n| Other | ₹1,00,00,000 |\n"
PROFILE_NO_MARKETS = "# Example Career Profile\n"

JD = "Senior Engineer\n\nCompensation: INR 3,000,000 to 4,500,000 per year plus benefits.\nApply now.\n"
QUOTE = "INR 3,000,000 to 4,500,000 per year"


def obs(ident: str = "c1", *, kind: str = "posted", low: str = "3000000", high: str = "4500000", currency: str = "INR",
        as_of: str = "2026-10-12", confidence: str = "high", quote: str | None = QUOTE) -> str:
    quote_line = f'\n    quote: "{quote}"' if quote else ""
    return (f"  - id: {ident}\n    kind: {kind}\n    low: {low}\n    high: {high}\n    currency: {currency}\n"
            f"    basis: base\n    period: year\n    source: \"posting text\"{quote_line}\n    as_of: {as_of}\n"
            f"    confidence: {confidence}\n")


def doc(market: str | None = '"India (home)"', observations: tuple[str, ...] = (), body: str = "notes") -> str:
    lines = f"schema: 1\nmarket: {market if market is not None else 'null'}\n"
    lines += "observations:\n" + "".join(observations) if observations else "observations: []\n"
    return f"---\n{lines}conversions: []\n---\n{body}\n"


def codes_of(root: Path) -> list[str]:
    return sorted(i.code for i in validate_workspace(root) if i.code.startswith("CMP"))


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def ws(tmp_path: Path, clock):
    root = make_workspace(tmp_path / "ws")
    (root / "profile.md").write_text(PROFILE, encoding="utf-8")
    job = add_job(root, "example-platform")
    (job["path"].parent / "jd.md").write_text(JD, encoding="utf-8")
    job["comp"] = job["path"].parent / "compensation.md"
    return root, job


def comp(root: Path, job: dict, *args: str):
    return runner.invoke(app, ["comp", job["id"], *args, "--workspace", str(root)])


def events(root: Path, prefix: str = "compensation.") -> list[dict]:
    return [e for e in ledger.read_events(root) if e["type"].startswith(prefix)]


def run_checked(root: Path, job: dict, expect_exit: int, expected_types: list[str], *args: str):
    """Run `comp` and assert exit code, that compensation.md is untouched and that only the expected events were appended."""
    before = state(root)
    file_before = job["comp"].read_bytes() if job["comp"].exists() else None
    res = comp(root, job, *args)
    assert res.exit_code == expect_exit, res.output
    assert_only_ledger_appended(before, state(root), expected_types)
    assert (job["comp"].read_bytes() if job["comp"].exists() else None) == file_before
    return res


GOOD = lambda: doc(observations=(obs(),))                                        # noqa: E731
OTHER = lambda: doc(observations=(obs(high="4400000"),))                         # noqa: E731
BAD_A = lambda: doc(observations=(obs(low="5", high="1"),))                      # noqa: E731
BAD_B = lambda: doc(observations=(obs(low="6", high="1"),))                      # noqa: E731


# --- happy path and recorded dedupe -----------------------------------------------------------

def test_init_edit_record_and_dedupe(ws) -> None:
    root, job = ws
    assert comp(root, job, "--init").exit_code == 0
    skeleton = job["comp"].read_bytes()
    assert b"observations: []" in skeleton
    job["comp"].write_text(GOOD(), encoding="utf-8")
    res = run_checked(root, job, 0, ["compensation.recorded"])
    assert "1 observation" in res.output
    (event,) = events(root)
    rel = "jobs/discovered/example-platform/compensation.md"
    loaded = record.load(job["comp"], root)
    rec, _ = record.parse(loaded)
    assert event["entity"] == job["id"] and event["artifacts"] == [rel, f"sha256:{record.stamp(rec, None)}"]
    assert "1 observation" in event["action"]
    run_checked(root, job, 0, [])                                              # unchanged: no new event
    job["comp"].write_text(OTHER(), encoding="utf-8")
    run_checked(root, job, 0, ["compensation.recorded"])                       # edit: new event
    job["comp"].write_text(GOOD(), encoding="utf-8")
    run_checked(root, job, 0, ["compensation.recorded"])                       # revert after an intervening valid record
    job["comp"].write_text(BAD_A(), encoding="utf-8")
    run_checked(root, job, 1, ["compensation.rejected"])
    job["comp"].write_text(GOOD(), encoding="utf-8")
    run_checked(root, job, 0, [])                                              # revert right after a rejection: equal to latest valid
    assert ledger.verify_chain(root) == []


def test_comment_only_change_is_not_a_new_record(ws) -> None:
    root, job = ws
    job["comp"].write_text(GOOD(), encoding="utf-8")
    run_checked(root, job, 0, ["compensation.recorded"])
    job["comp"].write_text(doc(observations=(obs(),), body="different free notes"), encoding="utf-8")
    run_checked(root, job, 0, [])                                              # notes are not part of the canonical form


def test_skeleton_is_recorded_once(ws) -> None:
    root, job = ws
    assert comp(root, job, "--init").exit_code == 0
    run_checked(root, job, 0, ["compensation.recorded"])
    run_checked(root, job, 0, [])


def test_absent_file_is_the_empty_record_and_records_nothing(ws) -> None:
    root, job = ws
    res = run_checked(root, job, 0, [])
    assert "no compensation.md" in res.output and not job["comp"].exists()


# --- rejected dedupe ----------------------------------------------------------------------------

def test_rejection_dedupe(ws) -> None:
    root, job = ws
    job["comp"].write_text(BAD_A(), encoding="utf-8")
    res = run_checked(root, job, 1, ["compensation.rejected"])
    assert "CMP003" in res.output
    (event,) = events(root)
    assert event["entity"] == job["id"] and event["reason"] == "CMP003"
    assert event["artifacts"] == ["jobs/discovered/example-platform/compensation.md", f"sha256:{sha(job['comp'])}"]
    run_checked(root, job, 1, [])                                              # same invalid file: no new event
    job["comp"].write_text(BAD_B(), encoding="utf-8")
    run_checked(root, job, 1, ["compensation.rejected"])                       # different bytes, same codes
    job["comp"].write_text(GOOD(), encoding="utf-8")
    run_checked(root, job, 0, ["compensation.recorded"])
    job["comp"].write_text(BAD_B(), encoding="utf-8")
    run_checked(root, job, 1, ["compensation.rejected"])                       # invalid again after a valid record
    run_checked(root, job, 1, [])


def test_rejection_reason_is_the_sorted_code_set(ws) -> None:
    root, job = ws
    job["comp"].write_text(doc(market=None, observations=(obs(low="5", high="1"),)), encoding="utf-8")
    run_checked(root, job, 1, ["compensation.rejected"])
    assert events(root)[0]["reason"] == "CMP002,CMP003"


def test_unparseable_file_is_rejected_with_cmp001(ws) -> None:
    root, job = ws
    job["comp"].write_bytes(b"\xff\xfe not text")
    run_checked(root, job, 1, ["compensation.rejected"])
    assert events(root)[0]["reason"] == "CMP001"
    run_checked(root, job, 1, [])


# --- ledger failure -----------------------------------------------------------------------------

def test_ledger_failure_claims_nothing_and_the_next_run_succeeds(ws, monkeypatch) -> None:
    root, job = ws
    job["comp"].write_text(GOOD(), encoding="utf-8")
    real = ledger.append_events

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(ledger, "append_events", boom)
    res = run_checked(root, job, 1, [])
    assert "nothing was recorded" in res.output and "disk full" in res.output
    monkeypatch.setattr(ledger, "append_events", real)
    run_checked(root, job, 0, ["compensation.recorded"])


def test_ledger_failure_on_a_rejection_still_exits_1(ws, monkeypatch) -> None:
    root, job = ws
    job["comp"].write_text(BAD_A(), encoding="utf-8")
    monkeypatch.setattr(ledger, "append_events", lambda *a, **k: (_ for _ in ()).throw(OSError("nope")))
    res = run_checked(root, job, 1, [])
    assert "nothing was recorded" in res.output and "CMP003" in res.output


def test_corrupt_prior_events_are_no_baseline(ws) -> None:
    root, job = ws
    job["comp"].write_text(GOOD(), encoding="utf-8")
    good_hash = record.stamp(record.parse(record.load(job["comp"], root))[0], None)
    for artifacts in ([], ["only-one"], ["x", "sha256:nothex"], ["x", f"sha256:{good_hash.upper()}"], ["x", good_hash]):
        ledger.append_event(root, type="compensation.recorded", actor="system", entity=job["id"], action="bad",
                            artifacts=artifacts)
    ledger.append_event(root, type="compensation.recorded", actor="system", entity="job_zzzzzzzzzz", action="other job",
                        artifacts=["x", f"sha256:{good_hash}"])
    run_checked(root, job, 0, ["compensation.recorded"])


# --- --init -------------------------------------------------------------------------------------

def test_init_refuses_an_existing_file(ws) -> None:
    root, job = ws
    job["comp"].write_text(GOOD(), encoding="utf-8")
    res = run_checked(root, job, 1, [], "--init")
    assert "already exists" in res.output


def test_init_writes_the_skeleton_and_no_event(ws) -> None:
    root, job = ws
    before = state(root)
    res = comp(root, job, "--init")
    assert res.exit_code == 0 and job["comp"].is_file()
    after = state(root)
    assert after["ledger.jsonl"] == before["ledger.jsonl"]
    assert set(after) - set(before) == {"jobs/discovered/example-platform/compensation.md"}
    assert codes_of(root) == []


# --- the market matrix ------------------------------------------------------------------------

def _reasons(root: Path, job: dict) -> list:
    res = comp(root, job, "--json")
    return [o["reason"] for o in json.loads(res.output)["observations"]]


CASES = [
    # id, profile, market, observations, exit, validate codes, event, reasons
    ("null-empty", PROFILE, None, (), 0, [], "recorded", []),
    ("null-observations", PROFILE, None, (obs(),), 1, ["CMP002"], "rejected", None),
    ("ambiguous", PROFILE, '"Twice"', (obs(),), 1, ["CMP007"], "rejected", None),
    ("ambiguous-empty", PROFILE, '"Twice"', (), 1, ["CMP007"], "rejected", None),
    ("unknown", PROFILE, '"Nowhere"', (obs(),), 1, ["CMP007"], "rejected", None),
    ("unknown-empty", PROFILE, '"Nowhere"', (), 1, ["CMP007"], "rejected", None),
    ("no-markets-table", PROFILE_NO_MARKETS, '"India (home)"', (obs(),), 1, ["CMP007"], "rejected", None),
    ("tbd-floor", PROFILE, '"Elsewhere"', (obs(),), 0, [], "recorded", ["FLOOR_UNVERIFIED"]),
    ("two-headings", PROFILE_TWO_HEADINGS, '"India (home)"', (obs(),), 0, [], "recorded", ["FLOOR_UNVERIFIED"]),
    ("normal-row", PROFILE, '"India (home)"', (obs(),), 0, [], "recorded", [None]),
    ("label-case-folded", PROFILE, '"india   (HOME)"', (obs(),), 0, [], "recorded", [None]),
]


@pytest.mark.parametrize("case", CASES, ids=[c[0] for c in CASES])
def test_market_matrix(ws, case) -> None:
    _, profile, market, observations, exit_code, vcodes, event, reasons = case
    root, job = ws
    (root / "profile.md").write_text(profile, encoding="utf-8")
    job["comp"].write_text(doc(market=market, observations=observations), encoding="utf-8")
    assert codes_of(root) == vcodes                                            # validate gives the verdict first
    run_checked(root, job, exit_code, [f"compensation.{event}"])
    assert [e["type"] for e in events(root)] == [f"compensation.{event}"]
    if event == "rejected":
        assert events(root)[0]["reason"] == ",".join(vcodes)
    if reasons is not None:
        assert _reasons(root, job) == reasons
    assert codes_of(root) == vcodes                                            # comp changed nothing validate sees


def test_verified_floor_and_cap_in_the_json(ws) -> None:
    root, job = ws
    job["comp"].write_text(doc(observations=(obs(low="2000000", high="3000000"),)), encoding="utf-8")
    out = json.loads(comp(root, job, "--json").output)
    assert out["floor"] == {"amount": 3500000, "currency": "INR", "basis": "base", "verified": True}
    assert out["effective"]["kind"] == "posted" and out["effective"]["low"] == 2000000 and out["effective"]["high"] == 3000000
    assert out["cap_fires"] is True and out["valid"] is True
    ob = out["observations"][0]
    assert ob["original"] == {"low": 2000000, "high": 3000000, "currency": "INR", "period": "year", "source": "posting text"}
    assert ob["converted"] == {"low": 2000000, "high": 3000000} and ob["comparable"] is True


def test_conversion_and_incomparable_reasons_in_the_output(ws) -> None:
    root, job = ws
    usd = (obs("u1", currency="USD", low="40000", high="50000", as_of="2026-10-12"),)
    text = doc(observations=usd).replace("conversions: []",
                                          'conversions:\n  - {from: USD, to: INR, rate: "80", source: "bank", as_of: 2026-10-10}')
    job["comp"].write_text(text, encoding="utf-8")
    out = json.loads(comp(root, job, "--json").output)
    assert out["observations"][0]["converted"] == {"low": 3200000, "high": 4000000}
    job["comp"].write_text(doc(observations=usd), encoding="utf-8")
    res = comp(root, job)
    assert res.exit_code == 0 and "FX_CONVERSION_MISSING" in res.output and "cap: would not fire" in res.output
    job["comp"].write_text(doc(observations=(obs(), obs("c2", low="5000000", high="6000000", quote=None, kind="posted"))),
                           encoding="utf-8")
    assert "CMP004" in comp(root, job).output
    job["comp"].write_text(doc(observations=(obs(), obs("c2", kind="recruiter", low="1", high="2", quote=None),
                                             obs("c3", low="9000000", high="9500000"))), encoding="utf-8")
    res = comp(root, job)
    assert res.exit_code == 0 and "conflict" in res.output


# --- validate ---------------------------------------------------------------------------------

@pytest.mark.parametrize("text", [GOOD, BAD_A, lambda: doc(observations=(obs(as_of="2025-01-01"),))], ids=["valid", "invalid", "stale"])
def test_validate_is_read_only(ws, text) -> None:
    root, job = ws
    job["comp"].write_text(text(), encoding="utf-8")
    before = state(root)
    res = runner.invoke(app, ["validate", "--workspace", str(root)])
    assert state(root) == before
    assert res.exit_code == (1 if text is BAD_A else 0), res.output


def test_cmp100_is_a_warning_relative_to_now(ws, monkeypatch) -> None:
    root, job = ws
    job["comp"].write_text(doc(observations=(obs(as_of="2026-07-01"),)), encoding="utf-8")
    issues = [i for i in validate_workspace(root, now="2026-10-01T00:00:00Z") if i.code == "CMP100"]       # 92 days
    assert [i.severity for i in issues] == ["warning"] and issues[0].path == "jobs/discovered/example-platform/compensation.md"
    assert not [i for i in validate_workspace(root, now="2026-09-29T00:00:00Z") if i.code == "CMP100"]     # exactly 90 days
    monkeypatch.setattr("careeros.core.models.utc_now", lambda: "2026-10-14T00:00:00Z")
    plain = runner.invoke(app, ["validate", "--workspace", str(root)])
    strict = runner.invoke(app, ["validate", "--strict", "--workspace", str(root)])
    assert plain.exit_code == 0 and "CMP100" in plain.output and strict.exit_code == 1
    res = comp(root, job)
    assert res.exit_code == 0 and "CMP100" in res.output
    assert json.loads(comp(root, job, "--json").output)["issues"][0]["code"] == "CMP100"


def test_validate_reports_each_cmp_error_with_a_path(ws) -> None:
    root, job = ws
    job["comp"].write_text(BAD_A(), encoding="utf-8")
    (issue,) = [i for i in validate_workspace(root) if i.code == "CMP003"]
    assert issue.severity == "error" and issue.path == "jobs/discovered/example-platform/compensation.md"


def test_no_compensation_file_produces_nothing(ws, tmp_path) -> None:
    root, job = ws
    assert codes_of(root) == []
    legacy = tmp_path / "legacy"
    make_legacy_workspace(legacy)
    before = snapshot(legacy)
    assert codes_of(legacy) == [] and snapshot(legacy) == before


def test_validate_reads_each_posting(ws) -> None:
    root, job = ws
    (job["comp"].parent / "jd.md").unlink()
    job["comp"].write_text(GOOD(), encoding="utf-8")
    assert codes_of(root) == ["CMP004"]


# --- interface --------------------------------------------------------------------------------

def test_json_is_one_object_and_usage_errors_are_plain(ws, tmp_path) -> None:
    root, job = ws
    job["comp"].write_text(BAD_A(), encoding="utf-8")
    res = comp(root, job, "--json")
    out = json.loads(res.output)                                               # the whole stdout parses
    assert res.exit_code == 1 and out["valid"] is False and out["recorded"] is False
    assert [i["code"] for i in out["issues"]] == ["CMP003"] and out["job"] == job["id"]
    nowhere = runner.invoke(app, ["comp", "x", "--json", "--workspace", str(tmp_path / "nothing")])
    assert nowhere.exit_code == 2
    with pytest.raises(ValueError):
        json.loads(nowhere.output)                                             # plain text on stderr, not JSON
    nojob = runner.invoke(app, ["comp", "no-such-job", "--workspace", str(root)])
    assert nojob.exit_code == 2 and "no job matches" in nojob.output


def test_json_reports_whether_it_recorded(ws) -> None:
    root, job = ws
    job["comp"].write_text(GOOD(), encoding="utf-8")
    first = json.loads(comp(root, job, "--json").output)
    second = json.loads(comp(root, job, "--json").output)
    assert (first["valid"], first["recorded"], second["recorded"]) == (True, True, False)


def test_comp_accepts_a_slug(ws) -> None:
    root, job = ws
    res = runner.invoke(app, ["comp", "example-platform", "--workspace", str(root)])
    assert res.exit_code == 0, res.output


# --- ledger integration -----------------------------------------------------------------------

def test_ledger_append_refuses_the_reserved_compensation_prefix(ws) -> None:
    root, job = ws
    assert ledger.is_reserved("compensation.recorded") and ledger.is_reserved("compensation.x")
    before = state(root)
    res = runner.invoke(app, ["ledger", "append", "--type", "compensation.x", "--action", "nope", "--workspace", str(root)])
    assert res.exit_code == 2 and "reserved" in res.output and state(root) == before


def test_rejected_needs_a_reason(ws) -> None:
    root, _ = ws
    with pytest.raises(ledger.LedgerError):
        ledger.append_event(root, type="compensation.rejected", actor="system", action="x")


def test_led003_accepts_compensation_events_for_a_job(ws) -> None:
    root, job = ws
    job["comp"].write_text(GOOD(), encoding="utf-8")
    comp(root, job)
    assert not [i for i in validate_workspace(root) if i.code in ("LED003", "LED001", "LED002")]


# --- pure units -------------------------------------------------------------------------------

def test_floor_for_resolution_order() -> None:
    m = markets_mod.parse(PROFILE)
    assert service.floor_for(m, None) == (None, service.UNVERIFIED)
    row, floor = service.floor_for(m, "India (home)")
    assert row.label == "India (home)" and floor == service.FloorInfo(3500000, "INR", "base", True)
    for label in ("Twice", "Nowhere"):
        with pytest.raises(markets_mod.MarketError):
            service.floor_for(m, label)
    row, floor = service.floor_for(m, "Elsewhere")
    assert row.label == "Elsewhere" and floor == service.UNVERIFIED
    unusable = markets_mod.parse(PROFILE_TWO_HEADINGS)
    assert service.floor_for(unusable, "India (home)")[1] == service.UNVERIFIED
    with pytest.raises(markets_mod.MarketError):
        service.floor_for(unusable, "Twice")
    with pytest.raises(markets_mod.MarketError):
        service.floor_for(markets_mod.parse(PROFILE_NO_MARKETS), "India (home)")


def ev(type_: str, entity: str = "job_a", artifacts=None, reason=None) -> dict:
    return {"type": type_, "entity": entity, "artifacts": artifacts if artifacts is not None else ["f", "sha256:" + "a" * 64],
            "reason": reason}


def test_latest_valid_recorded() -> None:
    h1, h2 = "1" * 64, "2" * 64
    good = lambda h, e="job_a": ev("compensation.recorded", e, ["f", f"sha256:{h}"])      # noqa: E731
    assert service.latest_valid_recorded([], "job_a") is None
    assert service.latest_valid_recorded([good(h1), good(h2)], "job_a") == h2
    assert service.latest_valid_recorded([good(h1), good(h2, "job_b")], "job_a") == h1
    assert service.latest_valid_recorded([good(h1), ev("compensation.rejected", reason="CMP001")], "job_a") == h1
    for bad in (["f"], [], ["f", "sha256:xyz"], ["f", "a" * 64], ["f", "sha256:" + "G" * 64], ["f", 7], "str"):
        assert service.latest_valid_recorded([good(h1), ev("compensation.recorded", artifacts=bad)], "job_a") == h1
    assert service.latest_valid_recorded([good(h1), {"type": "compensation.recorded", "entity": "job_a"}], "job_a") == h1
    assert service.latest_valid_recorded([good(h1), "junk", 3, None, {"type": 5}], "job_a") == h1


def test_is_duplicate_rejection() -> None:
    h = "b" * 64
    rej = lambda codes="CMP001,CMP003", hh=h, e="job_a": ev("compensation.rejected", e, ["f", f"sha256:{hh}"], codes)   # noqa: E731
    assert service.is_duplicate_rejection([rej()], "job_a", h, ["CMP003", "CMP001"])
    assert not service.is_duplicate_rejection([], "job_a", h, ["CMP001"])
    assert not service.is_duplicate_rejection([rej()], "job_a", h, ["CMP001"])                # other code set
    assert not service.is_duplicate_rejection([rej()], "job_a", "c" * 64, ["CMP001", "CMP003"])  # other bytes
    assert not service.is_duplicate_rejection([rej(e="job_b")], "job_a", h, ["CMP001", "CMP003"])
    later = [rej(), ev("compensation.recorded")]
    assert not service.is_duplicate_rejection(later, "job_a", h, ["CMP001", "CMP003"])        # latest is not a rejection
    assert service.is_duplicate_rejection([ev("compensation.recorded"), rej()], "job_a", h, ["CMP001", "CMP003"])
    assert not service.is_duplicate_rejection([rej(), "junk", {"type": None}, ev("job.imported")][:3] + [rej(hh="d" * 64)],
                                              "job_a", h, ["CMP001", "CMP003"])


# --- one cap decision --------------------------------------------------------------------------

def comp_basis(status="effective", kind="posted", low=1, high=10, complete=True, confident=True) -> dict:
    return {"status": status, "kind": kind, "low": low, "high": high, "kind_complete": complete, "kind_confident": confident}


CAP_TABLE = [
    ("below floor caps", comp_basis(), 11, True, True),
    ("recruiter caps", comp_basis(kind="recruiter"), 11, True, True),
    ("exact string high", comp_basis(high="21/2"), 11, True, True),
    ("equality does not cap", comp_basis(), 10, True, False),
    ("above floor", comp_basis(), 9, True, False),
    ("null high", comp_basis(high=None), 11, True, False),
    ("low confidence", comp_basis(confident=False), 11, True, False),
    ("incomplete kind", comp_basis(complete=False), 11, True, False),
    ("unverified floor", comp_basis(), 11, False, False),
    ("no floor amount", comp_basis(), None, True, False),
    ("estimate only", comp_basis(kind="estimate"), 11, True, False),
    ("conflict", comp_basis(status="conflict"), 11, True, False),
    ("none", comp_basis(status="none"), 11, True, False),
    ("no kind", comp_basis(kind=None), 11, True, False),
]


@pytest.mark.parametrize("case", CAP_TABLE, ids=[c[0] for c in CAP_TABLE])
def test_cap_applies_table(case) -> None:
    _, basis, floor, verified, expected = case
    assert cap_applies(basis, None if floor is None else Fraction(floor), verified) is expected


def test_comp_display_agrees_with_cap_applies(ws) -> None:
    root, job = ws
    texts = [doc(observations=(obs(low="2000000", high="3000000"),)),                     # caps
             doc(observations=(obs(low="2000000", high="3500000"),)),                     # equality
             doc(observations=(obs(low="2000000", high="3000000", confidence="low"),)),   # low confidence
             doc(observations=(obs(kind="estimate", quote=None, low="1", high="2"),)),    # estimate only
             doc(observations=(obs(low="2000000", high="3000000"), obs("c2", low="5000000", high="6000000")))]  # conflict
    expected = []
    for text in texts:
        job["comp"].write_text(text, encoding="utf-8")
        out = json.loads(comp(root, job, "--json").output)
        rec, _ = record.parse(record.load(job["comp"], root))
        floor = FloorInfo(3500000, "INR", "base", True)
        want = cap_applies(to_basis(derive_compensation(rec, floor)), Fraction(3500000), True)
        assert out["cap_fires"] is want
        expected.append(want)
        assert ("WOULD fire" in comp(root, job).output) is want
    assert expected == [True, False, False, False, False]


# --- failure paths -----------------------------------------------------------------------------

def test_init_is_atomic(ws, monkeypatch) -> None:
    root, job = ws

    def boom(path, data):
        raise OSError("disk full")

    monkeypatch.setattr(service, "atomic_write_bytes", boom)
    before = state(root)
    res = comp(root, job, "--init")
    assert res.exit_code == 1 and not job["comp"].exists() and state(root) == before


def test_rejection_reason_is_sorted_not_scan_order(ws) -> None:
    root, job = ws
    bad = doc(observations=(obs("c1", low="5", high="1"), obs("c2").replace("kind: posted", "kind: bogus")))
    job["comp"].write_text(bad, encoding="utf-8")
    scan = [i.code for i in service.check_job(job["comp"].parent, JD, markets_mod.parse(PROFILE))[1]]
    assert scan == ["CMP003", "CMP002"]                                        # scan order is not sorted order
    run_checked(root, job, 1, ["compensation.rejected"])
    assert events(root)[0]["reason"] == "CMP002,CMP003"


def test_malformed_last_ledger_line_is_a_ledger_failure(ws) -> None:
    root, job = ws
    job["comp"].write_text(GOOD(), encoding="utf-8")
    with (root / "ledger.jsonl").open("ab") as f:
        f.write(b'{"type":"x"}\n')                                             # valid JSON, no seq
    for args in ((), ("--json",)):
        before = state(root)
        res = comp(root, job, *args)
        assert res.exit_code == 1 and state(root) == before
        if args:
            out = json.loads(res.output)
            assert out["recorded"] is False and out["ledger_error"]
        else:
            assert "nothing was recorded" in res.output


def test_rejection_hash_is_of_the_bytes_that_were_parsed(ws, monkeypatch) -> None:
    root, job = ws
    job["comp"].write_text(BAD_A(), encoding="utf-8")
    first = job["comp"].read_bytes()
    real = Path.read_bytes
    calls = {"n": 0}

    def flaky(self):
        data = real(self)
        if self.name == "compensation.md":
            calls["n"] += 1
            if calls["n"] > 1:
                return b"changed after the first read"
        return data

    monkeypatch.setattr(Path, "read_bytes", flaky)
    comp(root, job)
    monkeypatch.undo()
    assert calls["n"] == 1
    assert events(root)[0]["artifacts"][1] == f"sha256:{hashlib.sha256(first).hexdigest()}"
