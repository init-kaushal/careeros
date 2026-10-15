"""Final-review fixes: the floor-only migration path, an empty record in the derivation check, one FX window for `comp`
and `evaluate`, the evaluation dedupe, and the `comp` floor line. Invented data only."""

from __future__ import annotations

import copy
import dataclasses
import json

from helpers import snapshot
from test_evaluation_command import evl_events, run
from test_evaluation_compensation import COMP_OBS, evaluate_ok, refuse, v1, ws          # noqa: F401  (fixtures)
from test_evaluation_record import agent_half, edit, load, report, rewrite_result, write_agent, write_comp

from careeros.core import ledger
from careeros.core.compensation import derive as comp_derive
from careeros.core.evaluation import record, rules


def test_the_derive_module_has_no_fx_window_alias() -> None:
    assert not hasattr(comp_derive, "FX_WINDOW_DAYS")


def test_comp_and_evaluate_read_the_same_fx_window(ws, monkeypatch) -> None:        # noqa: F811
    root, ids, job = ws
    usd = dict(COMP_OBS, id="u1", low=30000, high=35000, currency="USD", as_of="2026-09-15")
    write_comp(job, [usd], conversions=[{"from": "USD", "to": "INR", "rate": "83.20", "source": "bank", "as_of": "2026-09-10"}])

    def comp_reasons() -> list:
        res = run(root, "comp", job["id"], "--json")
        assert res.exit_code == 0, res.output
        return [o["reason"] for o in json.loads(res.stdout)["observations"]]

    assert comp_reasons() == [None]                                 # 5 days old: inside the 30-day window
    monkeypatch.setitem(rules.RULES, "2", dataclasses.replace(rules.RULES["2"], fx_window_days=3))
    assert comp_reasons() == ["FX_RATE_OUTSIDE_WINDOW"]             # comp follows the registered rules, like evaluate
    result = evaluate_ok(root, job)
    assert any("FX_RATE_OUTSIDE_WINDOW" in item for item in result["action_items"])


def test_an_empty_record_with_a_bad_market_still_gets_the_derivation_check(ws) -> None:   # noqa: F811
    root, ids, job = ws
    write_comp(job, [dict(COMP_OBS, kind="posted", low=2500000, high=3000000, source="posting text",
                          quote="Pay: INR 2,500,000 to 3,000,000 per year (base).", confidence="high")])
    (job["path"].parent / "jd.md").write_text((job["path"].parent / "jd.md").read_text(encoding="utf-8")
                                              + "Pay: INR 2,500,000 to 3,000,000 per year (base).\n", encoding="utf-8")
    capped = copy.deepcopy(evaluate_ok(root, job)["basis"]["cap_inputs"]["compensation"])
    job["comp"] = job["path"].parent / "compensation.md"
    job["comp"].unlink()
    clean = evaluate_ok(root, job)
    assert clean["caps"] == []
    basis = copy.deepcopy(clean["basis"])
    basis["cap_inputs"]["compensation"] = capped
    forged = record.assemble(basis, clean["stamps"], clean["requirements"])
    assert forged["caps"]                                            # a self-consistent forged cap
    write_comp(job, [], market="Bogus")                              # an empty record stamps like an absent file (CMP007)
    rewrite_result(root, job, forged)
    rep = report(root, job)
    assert rep.stale == () and rep.tampered == ("basis",)
    assert [i.code for i in rep.issues] == ["EVL007"]


def test_a_comp_event_does_not_repeat_an_evaluation_rejection(ws) -> None:               # noqa: F811
    root, ids, job = ws
    agent = agent_half(ids)
    agent["requirements"][0]["quote"] = "not in the posting"
    write_agent(root, job, agent, evaluate=False)
    assert run(root, "evaluate", job["id"]).exit_code == 1
    write_comp(job, [COMP_OBS])
    assert run(root, "comp", job["id"]).exit_code == 0               # a compensation.recorded event, same entity
    assert run(root, "evaluate", job["id"]).exit_code == 1
    assert [e["type"] for e in evl_events(root)] == ["evaluation.rejected"]


def test_comp_says_nothing_about_the_floor_for_an_invalid_record(ws) -> None:             # noqa: F811
    root, ids, job = ws
    write_comp(job, [dict(COMP_OBS, kind="posted", quote="not in the posting", confidence="high")])      # CMP004 keeps the record
    res = run(root, "comp", job["id"])
    assert res.exit_code == 1 and "CMP004" in res.output
    assert "floor:" not in res.output


# --- a 0.5.0 block with a floor and no range (golden fixture copy) ----------------------------------

def test_a_floor_only_block_migrates_by_confirmed_removal_without_a_compensation_md(v1) -> None:   # noqa: F811
    root, job = v1
    edit(root, job, lambda fm: fm["agent"]["job_facts"]["compensation"].pop("range"))
    assert "migrate" in refuse(root, job)                            # evaluate refuses: no compensation.md yet
    assert not job["comp"].exists()
    edit(root, job, lambda fm: fm["agent"]["job_facts"].pop("compensation"))     # steps 3, after the user confirmed
    assert not job["comp"].exists()                                  # nothing created
    events = ledger.read_events(root)
    result = evaluate_ok(root, job)                                  # step 4
    assert result["caps"] == [] and result["basis"]["basis_version"] == "2"
    assert [e["type"] for e in ledger.read_events(root)[len(events):]] == ["evaluation.completed"]
    assert snapshot(root).get("jobs/discovered/example-platform/compensation.md") is None
