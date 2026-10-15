"""`careeros evaluate` reads compensation.md (basis/rules version 2): the cap, the `compensation` stamp, the agent-half
change and version-1 compatibility (golden 0.5.0 fixture). Invented data only."""

from __future__ import annotations

import copy
import dataclasses
import shutil
from pathlib import Path

import pytest
from helpers import snapshot
from test_evaluation_command import codes, evl_events, run
from test_evaluation_record import (
    COMP_OBS, JD, PROFILE, agent_half, edit, legacy_agent, load, make_job, report, rewrite_result, stored_result,
    write_agent, write_comp,
)

from careeros.core import ledger
from careeros.core.compensation import record as comp_record
from careeros.core.evaluation import integrity, record, rules
from careeros.core.validation import validate_workspace
from careeros.core.workspace import join_frontmatter, split_frontmatter

PAY_LINE = "Pay: INR 2,500,000 to 3,000,000 per year (base)."
POSTED = dict(COMP_OBS, kind="posted", low=2500000, high=3000000, source="posting text", quote=PAY_LINE,
              confidence="high")
FIXTURE = Path(__file__).parent / "fixtures" / "evaluation_v1"
V1_JOB = "jobs/discovered/example-platform"


@pytest.fixture
def ws(career):
    """A job whose posting states pay, no compensation.md yet, and an agent half without the legacy block."""
    root, ids = career
    job = make_job(root, comp=False)
    (job["path"].parent / "jd.md").write_text(JD + PAY_LINE + "\n", encoding="utf-8")
    write_agent(root, job, agent_half(ids), evaluate=False)
    return root, ids, job


def evaluate_ok(root, job) -> dict:
    res = run(root, "evaluate", job["id"])
    assert res.exit_code == 0, res.output
    return load(root, job).result


def stale_names(root, job) -> tuple:
    return report(root, job).stale


def state(root) -> tuple:
    return snapshot(root), (root / "ledger.jsonl").read_bytes()


# --- the cap and the compensation stamp ------------------------------------------------------

def test_posted_figure_below_the_floor_caps_and_raising_it_stales_then_uncaps(ws) -> None:
    root, ids, job = ws
    write_comp(job, [POSTED])
    result = evaluate_ok(root, job)
    assert result["basis"]["basis_version"] == result["basis"]["rules_version"] == result["stamps"]["rules"] == "2"
    assert result["tier"] == "weak" and [c["code"] for c in result["caps"]] == ["compensation"]
    assert "posted range tops out at 3000000 INR (base)" in result["caps"][0]["reason"]
    comp = result["basis"]["cap_inputs"]["compensation"]
    assert (comp["status"], comp["kind"], comp["high"]) == ("effective", "posted", 3000000)
    assert result["basis"]["cap_inputs"]["floor"] == {"amount": 3500000, "currency": "INR", "basis": "base",
                                                      "verification": "verified", "source_cell": "₹35,00,000",
                                                      "row_state": "parseable"}
    rec, _ = comp_record.parse(comp_record.load(job["comp"], root))
    assert result["stamps"]["compensation"] == comp_record.stamp(rec, None)
    assert codes(root) == []

    eval_bytes, evl = job["eval"].read_bytes(), evl_events(root)
    assert run(root, "comp", job["id"]).exit_code == 0                 # comp and validate never re-evaluate
    validate_workspace(root)
    assert job["eval"].read_bytes() == eval_bytes and evl_events(root) == evl

    write_comp(job, [dict(POSTED, high=4000000)])
    assert stale_names(root, job) == ("compensation",)
    assert "EVL008" in codes(root) and "EVL007" not in codes(root)
    assert run(root, "comp", job["id"]).exit_code == 0
    validate_workspace(root)
    assert job["eval"].read_bytes() == eval_bytes and evl_events(root) == evl

    result = evaluate_ok(root, job)
    assert result["caps"] == [] and result["tier"] == "pursue"
    assert codes(root) == []
    assert [e["type"] for e in evl_events(root)[len(evl):]] == ["evaluation.completed"]   # stale only: no replacement


def test_absent_skeleton_and_empty_record_are_one_result_and_never_stale(ws) -> None:
    root, ids, job = ws
    absent = evaluate_ok(root, job)
    assert absent["stamps"]["compensation"] == comp_record.EMPTY_STAMP
    assert absent["basis"]["cap_inputs"]["compensation"]["status"] == "none"
    assert "pay: not disclosed in the posting; ask the recruiter" in absent["action_items"]
    assert run(root, "comp", job["id"], "--init").exit_code == 0
    assert job["comp"].is_file() and stale_names(root, job) == () and codes(root) == []
    count = len(evl_events(root))
    res = run(root, "evaluate", job["id"])
    assert res.exit_code == 0 and "(unchanged)" in res.output and len(evl_events(root)) == count
    for market in ("Singapore", "Atlantis (home)", None):              # market is ignored without observations
        write_comp(job, [], market=market)
        assert stale_names(root, job) == ()
        assert load(root, job).result == absent


def test_foreign_currency_needs_a_conversion_inside_the_window(ws) -> None:
    root, ids, job = ws
    usd = dict(COMP_OBS, id="u1", low=30000, high=35000, currency="USD", as_of="2026-09-15")
    write_comp(job, [usd])
    result = evaluate_ok(root, job)
    assert result["caps"] == [] and codes(root) == []
    assert any("u1" in item and "FX_CONVERSION_MISSING" in item for item in result["action_items"])

    rate = {"from": "USD", "to": "INR", "rate": "83.20", "source": "bank", "as_of": "2026-09-10"}
    write_comp(job, [usd], conversions=[rate])
    assert stale_names(root, job) == ("compensation",)
    result = evaluate_ok(root, job)                                    # 35000 x 83.20 = 2912000 < 3500000
    assert [c["code"] for c in result["caps"]] == ["compensation"] and result["tier"] == "weak"
    assert "2912000" in result["caps"][0]["reason"]

    write_comp(job, [usd], conversions=[dict(rate, as_of="2026-09-16")])   # dated after the observation
    result = evaluate_ok(root, job)
    assert result["caps"] == []
    assert any("u1" in item and "FX_RATE_OUTSIDE_WINDOW" in item for item in result["action_items"])


def test_the_fx_window_comes_from_the_rules_version(ws, monkeypatch) -> None:
    root, ids, job = ws
    usd = dict(COMP_OBS, id="u1", low=30000, high=35000, currency="USD", as_of="2026-09-15")
    write_comp(job, [usd], conversions=[{"from": "USD", "to": "INR", "rate": "83.20", "source": "bank",
                                         "as_of": "2026-09-10"}])
    monkeypatch.setitem(rules.RULES, "2", dataclasses.replace(rules.RULES["2"], fx_window_days=3))
    result = evaluate_ok(root, job)                                    # 5 days old: outside a 3-day window
    assert result["caps"] == []
    assert any("FX_RATE_OUTSIDE_WINDOW" in item for item in result["action_items"])


def test_conflicting_figures_never_cap_and_ask_to_resolve(ws) -> None:
    root, ids, job = ws
    write_comp(job, [dict(COMP_OBS, id="c1", low=2000000, high=2500000),
                     dict(COMP_OBS, id="c2", low=2800000, high=3000000)])
    result = evaluate_ok(root, job)
    assert result["caps"] == []
    assert "pay: resolve conflicting recruiter pay figures" in result["action_items"]
    assert result["basis"]["cap_inputs"]["compensation"]["status"] == "conflict"


def test_a_low_confidence_posted_figure_never_caps(ws) -> None:
    root, ids, job = ws
    write_comp(job, [dict(POSTED, confidence="low")])
    result = evaluate_ok(root, job)
    assert result["caps"] == []
    assert "pay: a posted pay figure has low source confidence; confirm it with the recruiter" in result["action_items"]


# --- invalid compensation.md and the market rule ---------------------------------------------

def test_invalid_compensation_is_rejected_once_with_its_cmp_codes(ws) -> None:
    root, ids, job = ws
    write_comp(job, [dict(COMP_OBS, low=5, high=1)])                   # CMP003
    before = snapshot(root)
    res = run(root, "evaluate", job["id"])
    assert res.exit_code == 1 and "CMP003" in res.output
    assert snapshot(root) == before and not job["eval"].read_bytes().count(b"result:")
    (event,) = evl_events(root)
    assert event["type"] == "evaluation.rejected" and event["reason"] == "CMP003"
    assert run(root, "evaluate", job["id"]).exit_code == 1
    assert len(evl_events(root)) == 1                                  # deduped
    with pytest.raises(record.RecordError) as err:
        record.compute(load(root, job), record.read_inputs(root, job["eval"].parent))
    assert err.value.code == "CMP003"


def test_an_invalid_compensation_md_after_evaluation_reads_stale_not_tampered(ws) -> None:
    root, ids, job = ws
    write_comp(job, [POSTED])
    evaluate_ok(root, job)
    job["comp"].write_text("---\nschema: [unclosed\n---\n", encoding="utf-8")
    assert stale_names(root, job) == ("compensation",)
    found = validate_workspace(root)
    assert "CMP001" in {i.code for i in found} and "EVL007" not in {i.code for i in found}
    assert [i.code for i in found if i.code.startswith("CMP")] == ["CMP001"]   # reported once, by the CMP section


def test_job_facts_market_must_equal_the_compensation_market(ws) -> None:
    root, ids, job = ws
    write_comp(job, [dict(COMP_OBS, low=10000, high=12000, currency="SGD")], market="Singapore")
    before = snapshot(root)
    res = run(root, "evaluate", job["id"])
    assert res.exit_code == 1 and "EVL006" in res.output and snapshot(root) == before
    (event,) = evl_events(root)
    assert event["reason"] == "EVL006"
    inputs = record.read_inputs(root, job["eval"].parent)
    found = record.validate_agent(load(root, job), inputs.jd_text, inputs.career, inputs.rubric, inputs.markets,
                                  inputs.job_id, compensation=inputs.compensation)
    assert [i.code for i in found] == ["EVL006"] and "compensation.md" in found[0].message
    write_comp(job, [], market="Singapore")                            # no observations: no market rule
    assert run(root, "evaluate", job["id"]).exit_code == 0


# --- the agent half: job_facts.compensation --------------------------------------------------

def test_the_legacy_block_is_evl003_without_a_result_and_on_a_v2_result(ws) -> None:
    root, ids, job = ws
    write_agent(root, job, legacy_agent(ids), evaluate=False)
    res = run(root, "evaluate", job["id"])
    assert res.exit_code == 1 and "EVL003" in res.output
    assert "pay now lives in compensation.md; see the evaluate skill" in res.output
    write_agent(root, job, agent_half(ids), evaluate=False)
    evaluate_ok(root, job)
    edit(root, job, lambda fm: fm["agent"]["job_facts"].update(compensation=copy.deepcopy(legacy_agent(ids)["job_facts"]["compensation"])))
    found = [i for i in validate_workspace(root) if i.code == "EVL003"]
    assert len(found) == 1 and "pay now lives in compensation.md" in found[0].message


# --- integrity of a v2 result ----------------------------------------------------------------

def test_forging_kind_confident_in_a_fresh_basis_is_evl007(ws) -> None:
    root, ids, job = ws
    write_comp(job, [POSTED])
    result = evaluate_ok(root, job)
    assert result["caps"]
    basis = copy.deepcopy(result["basis"])
    basis["cap_inputs"]["compensation"]["kind_confident"] = False      # the basis observations cannot cross-check it
    forged = record.assemble(basis, result["stamps"], result["requirements"])
    assert forged["caps"] == [] and forged["tier"] == "pursue"         # self-consistent: the scorer agrees with it
    rewrite_result(root, job, forged)
    rep = report(root, job)
    assert rep.stale == () and rep.tampered == ("basis",)
    assert [i.code for i in rep.issues] == ["EVL007"]


def test_a_v2_result_needs_all_six_stamps(ws) -> None:
    root, ids, job = ws
    evaluate_ok(root, job)
    edit(root, job, lambda fm: fm["result"]["stamps"].pop("compensation"))
    assert "result" in report(root, job).tampered


def test_a_floor_edit_in_profile_md_marks_the_evaluation_stale(ws) -> None:
    root, ids, job = ws
    write_comp(job, [POSTED])
    evaluate_ok(root, job)
    (root / "profile.md").write_text(PROFILE.replace("₹35,00,000", "₹25,00,000"), encoding="utf-8")
    rep = report(root, job)
    assert rep.stale == ("rubric",) and rep.tampered == ()
    assert evaluate_ok(root, job)["caps"] == []                        # 3000000 is above the new floor


def test_compensation_stamp_ignores_notes_and_follows_figures(ws) -> None:
    root, ids, job = ws
    write_comp(job, [POSTED])
    evaluate_ok(root, job)
    write_comp(job, [POSTED], body="Other free notes.\n")
    assert stale_names(root, job) == ()
    write_comp(job, [dict(POSTED, confidence="medium")])
    assert stale_names(root, job) == ("compensation",)


def test_v2_builder_output_is_always_accepted_by_the_scorer() -> None:
    import json
    from datetime import date
    from fractions import Fraction

    from test_evaluation_score import GRID_NEEDS, GRID_TABLES, entry, judgements

    from careeros.core.compensation import derive as comp_derive, service as comp_service
    from careeros.core.compensation.model import CompRecord, Conversion, Observation
    from careeros.core.evaluation import canon, markets, score
    from careeros.core.evaluation.derive import Basis
    from careeros.core.evaluation.model import JobFacts, Listing, MarketBasis
    from careeros.core.evaluation.rubric import FitRow, Rubric

    def obs(ident, kind="recruiter", high=7000, currency="SGD", basis=MarketBasis.BASE, confidence="high"):
        return Observation(ident, kind, 5000, high, currency, basis, "year", "s", None, date(2026, 9, 15), confidence)

    usd = Conversion("USD", "SGD", Fraction(13, 10), "bank", date(2026, 9, 10))
    pays = [(), (obs("a"),), (obs("a", high=None),), (obs("a", currency="USD"),), (obs("a", basis=MarketBasis.TOTAL),),
            (obs("a"), obs("b", kind="estimate")), (obs("a", kind="estimate"),), (obs("a", confidence="low"),),
            (obs("a", high=6000), obs("b", high=9000)), (obs("a", high=5500), Observation(
                "b", "recruiter", 6000, 9000, "SGD", MarketBasis.BASE, "year", "s", None, date(2026, 9, 15), "high"))]
    rubric = Rubric((FitRow("Role title matches", 3),), (), "ok")
    reqs = Basis((entry("r1"), entry("r2", "strong"), entry("r3", "nice")), 3)
    count = 0
    for table, name in GRID_TABLES.values():
        floor = comp_service.floor_for(table, name)[1]
        for need in GRID_NEEDS:
            facts = JobFacts(market=name, needed=need, listing=Listing.NOT_OFFERED, sponsorship_quote="q")
            for pay in pays:
                for conversions in ((), (usd,)):
                    d = comp_derive.derive_compensation(CompRecord(name, pay, conversions, ""), floor)
                    out = reqs.complete(judgements(("Role title matches", "yes")), facts,
                                        markets.verify_market(facts, table), rubric,
                                        compensation=comp_derive.to_basis(d), floor=floor)
                    out["basis_version"] = out["rules_version"] = "2"
                    score.score(json.loads(canon.canonical_json(out)))
                    count += 1
    assert count == 5 * 7 * 10 * 2


# --- the golden 0.5.0 record -----------------------------------------------------------------

@pytest.fixture
def v1(tmp_path: Path, clock) -> tuple[Path, dict]:
    root = tmp_path / "v1"
    shutil.copytree(FIXTURE, root)
    job_dir = root / V1_JOB
    fm, _ = split_frontmatter((job_dir / "job.md").read_text(encoding="utf-8"))
    return root, {"id": fm["id"], "eval": job_dir / "evaluation.md", "comp": job_dir / "compensation.md",
                  "path": job_dir / "job.md"}


def v1_codes(root: Path) -> set[str]:
    return {i.code for i in validate_workspace(root)}


def refuse(root: Path, job: dict) -> str:
    """`evaluate` refuses: exit 1, EVL003, nothing changed but at most one deduped `evaluation.rejected`."""
    before, events = snapshot(root), ledger.read_events(root)
    res = run(root, "evaluate", job["id"])
    assert res.exit_code == 1 and "EVL003" in res.output, res.output
    assert snapshot(root) == before
    added = ledger.read_events(root)[len(events):]
    assert len(added) <= 1 and all(e["type"] == "evaluation.rejected" for e in added)
    assert ledger.read_events(root)[:len(events)] == events
    again = len(ledger.read_events(root))
    assert run(root, "evaluate", job["id"]).exit_code == 1 and len(ledger.read_events(root)) == again
    return " ".join(res.output.split())


def test_golden_v1_validates_stale_and_never_tampered(v1) -> None:
    root, job = v1
    rep = integrity.check(record.load(job["eval"], root), record.read_inputs(root, job["eval"].parent))
    assert {"basis", "rules"} <= set(rep.stale) and "compensation" not in rep.stale
    assert rep.tampered == () and rep.unsupported is None
    found = v1_codes(root)
    assert "EVL008" in found and not found & {"EVL007", "EVL010", "EVL003"}
    assert set(record.load(job["eval"], root).result["stamps"]) == set(record.STAMP_KEYS["1"])


def test_golden_v1_refusal_names_the_situation_then_migrates(v1) -> None:
    root, job = v1
    assert "migrate" in refuse(root, job) and "`evaluate` skill" in refuse(root, job)       # no compensation.md
    write_comp(job, [dict(COMP_OBS, low=5, high=1)])
    assert "fix the CMP errors" in refuse(root, job)                                         # an invalid one
    write_comp(job, [dict(COMP_OBS, high=3200000)])
    message = refuse(root, job)                                                              # a valid one
    assert "confirm" in message and "remove the block" in message

    edit(root, job, lambda fm: fm["agent"]["job_facts"].pop("compensation"))                 # the skill's step 3
    events = ledger.read_events(root)
    result = evaluate_ok(root, job)
    assert result["basis"]["basis_version"] == "2" and result["stamps"]["rules"] == "2"
    assert [c["code"] for c in result["caps"]] == ["compensation"]                           # same cap as 0.5.0
    added = ledger.read_events(root)[len(events):]
    assert [e["type"] for e in added] == ["evaluation.completed"]                            # stale only: no replacement
    res = run(root, "evaluate", job["id"])
    assert res.exit_code == 0 and "(unchanged)" in res.output and len(ledger.read_events(root)) == len(events) + 1
    assert not v1_codes(root) & {"EVL007", "EVL008", "EVL009", "EVL010"}


# --- fix round 1 -----------------------------------------------------------------------------

def test_golden_v1_refusal_for_an_empty_compensation_md_asks_to_record_the_legacy_pay(v1) -> None:
    root, job = v1
    for empty in (lambda: write_comp(job, []), lambda: run(root, "comp", job["id"], "--init")):   # empty, skeleton
        if job["comp"].exists():
            job["comp"].unlink()
        empty()
        message = refuse(root, job)
        assert "compensation.md is empty" in message and "record the legacy pay as an observation" in message
        assert "confirm the job has no pay information" in message and "before removing the block" in message
        assert "confirm the record covers the legacy block" not in message
    job["comp"].unlink()
    absent = refuse(root, job)                                         # the other three wordings are unchanged
    assert "there is no compensation.md yet, so migrate the block" in absent
    write_comp(job, [dict(COMP_OBS, low=5, high=1)])
    assert "compensation.md has errors (CMP003): fix the CMP errors first" in refuse(root, job)
    write_comp(job, [dict(COMP_OBS, high=3200000)])
    assert ("compensation.md exists and is valid: confirm the record covers the legacy block, then remove the block"
            in refuse(root, job))


def test_cmp_and_agent_half_errors_are_one_rejection_with_both_code_sets(ws) -> None:
    root, ids, job = ws
    write_comp(job, [dict(COMP_OBS, low=5, high=1)])                   # CMP003
    agent = agent_half(ids)
    agent["requirements"][0]["quote"] = "not in the posting"           # EVL004
    write_agent(root, job, agent, evaluate=False)
    res = run(root, "evaluate", job["id"])
    assert res.exit_code == 1
    (event,) = evl_events(root)
    assert event["reason"] == "CMP003,EVL004"


@pytest.mark.parametrize("version", ["99", 1, None, ["1"]])
def test_the_legacy_block_on_an_unsupported_or_malformed_version_is_evl003(ws, version) -> None:
    root, ids, job = ws
    write_agent(root, job, legacy_agent(ids), evaluate=False, extra={"result": {"basis": {"basis_version": version}}})
    inputs = record.read_inputs(root, job["eval"].parent)
    found = record.validate_agent(load(root, job), inputs.jd_text, inputs.career, inputs.rubric, inputs.markets,
                                  inputs.job_id, compensation=inputs.compensation)
    assert [i.code for i in found] == ["EVL003"] and "pay now lives in compensation.md" in found[0].message


def test_v2_evl011_fix_points_at_the_markets_row_not_job_facts(ws) -> None:
    root, ids, job = ws
    agent = agent_half(ids)
    agent["job_facts"]["market"] = "Elsewhere"
    write_agent(root, job, agent, evaluate=False)
    evaluate_ok(root, job)
    found = [i for i in report(root, job).issues if i.code == "EVL011"]
    (floor,) = [i for i in found if i.message.startswith("floor")]
    assert "profile.md" in floor.fix and "## Markets" in floor.fix
    assert "same values in `job_facts`" not in floor.fix and "floor" in floor.fix
    (need,) = [i for i in found if i.message.startswith("sponsorship")]
    assert "profile.md" in need.fix and "job_facts.sponsorship" in need.fix


def test_an_unparseable_file_after_an_absent_one_reads_stale(ws) -> None:
    root, ids, job = ws
    assert evaluate_ok(root, job)["stamps"]["compensation"] == comp_record.EMPTY_STAMP
    job["comp"].write_text("---\nschema: [unclosed\n---\n", encoding="utf-8")
    assert stale_names(root, job) == ("compensation",)
    job["comp"].write_bytes(b"\xff\xfe not utf-8")
    assert stale_names(root, job) == ("compensation",)


def test_an_unparseable_job_facts_market_gives_no_second_evl006(ws) -> None:
    root, ids, job = ws
    write_comp(job, [POSTED])
    agent = agent_half(ids)
    agent["job_facts"]["market"] = 7
    write_agent(root, job, agent, evaluate=False)
    inputs = record.read_inputs(root, job["eval"].parent)
    found = record.validate_agent(load(root, job), inputs.jd_text, inputs.career, inputs.rubric, inputs.markets,
                                  inputs.job_id, compensation=inputs.compensation)
    assert [i.code for i in found] == ["EVL003"]


def test_complete_never_switches_layout_implicitly() -> None:
    from careeros.core.evaluation.derive import Basis
    from careeros.core.evaluation.markets import MarketCheck, FieldCheck
    from careeros.core.evaluation.model import JobFacts, Verification
    from careeros.core.evaluation.rubric import Rubric

    none = FieldCheck(Verification.UNVERIFIED, None)
    check, rubric = MarketCheck(None, none, none), Rubric((), (), "missing")
    with pytest.raises(TypeError):
        Basis((), 0).complete((), JobFacts(), check, rubric)           # v2 needs compensation and floor
    v1 = Basis((), 0).complete_v1((), JobFacts(), check, rubric)
    assert (v1["basis_version"], v1["rules_version"]) == ("1", "1") and "range" in v1["cap_inputs"]
