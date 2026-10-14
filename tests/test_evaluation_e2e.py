"""One end-to-end walk through the evaluation feature on invented data: init, fill, evaluate, idempotence, tamper and
recovery, every staleness cause on its own, ledger failure, the crash window, isolation, and the delivered skills."""

from __future__ import annotations

import hashlib
from pathlib import Path

from helpers import snapshot
from test_evaluation_command import boom, codes, evl_events, run
from test_evaluation_record import JD, PROFILE, agent_half, edit, make_job, write_agent
from test_skills_evaluation import PREP_GATE_BEFORE, skill

from careeros.core import ledger
from careeros.core.evaluation import record, rules, service
from careeros.core.memory import ops, store
from careeros.core.validation import validate_workspace
from careeros.core.workspace import split_frontmatter
from careeros.workspace.scaffold import scaffold


def test_evaluation_end_to_end(career, monkeypatch, tmp_path: Path) -> None:
    root, ids = career
    ledger_file = root / "ledger.jsonl"

    def guarded(job: dict) -> dict:
        """What evaluation must never touch: the career memory, the job file and the posting."""
        keep = {k: v for k, v in snapshot(root).items() if k.startswith("career/")}
        keep["job.md"] = job["path"].read_bytes()
        keep["jd.md"] = (job["path"].parent / "jd.md").read_bytes()
        return keep

    def stale_stamps() -> list[str]:
        (issue,) = [i for i in validate_workspace(root) if i.code == "EVL008"]
        head, _, _ = issue.message.partition(" changed since")
        assert head.startswith("stale: ")
        return head.removeprefix("stale: ").split(", ")

    def evaluate_clears_staleness() -> None:
        count = len(evl_events(root, "evaluation.completed"))
        assert run(root, "evaluate", job["id"]).exit_code == 0
        assert len(evl_events(root, "evaluation.completed")) == count + 1
        assert codes(root) == []

    # 1. workspace, memory, job + jd.md; evaluate --init (writes no event)
    job = make_job(root)
    events_before = len(ledger.read_events(root))
    assert run(root, "evaluate", job["slug"], "--init").exit_code == 0
    assert len(ledger.read_events(root)) == events_before
    rec = record.load(job["eval"], root)
    assert rec.agent == {} and rec.result is None
    keep = guarded(job)
    job_events = [e for e in ledger.read_events(root) if e["entity"] == job["id"]]

    # 2. the agent half, then evaluate: hand-computed expectations (fit (3*1 + 2*0.5)/5 = 0.8; coverage 0.8;
    #    the Go-years requirement is unverifiable; every supporting fact is only claimed -> low confidence)
    write_agent(root, job, agent_half(ids), evaluate=False, extra={"id": rec.id})
    assert run(root, "evaluate", job["slug"]).exit_code == 0
    result = record.load(job["eval"], root).result
    assert (result["tier"], result["combined"], result["fit"], result["coverage"]) == ("pursue", 0.8, 0.8, 0.8)
    assert result["confidence"] == "low" and result["caps"] == []
    assert [(r["id"], r["outcome"]) for r in result["requirements"]] == [
        ("r1", "covered"), ("r2", "unverifiable"), ("r3", "adjacent")]
    items = result["action_items"]
    assert len(items) == 3 and items[0].startswith("r2: cannot be derived from memory")
    assert items[1].startswith("r1: supported only by claimed facts (") and items[2].startswith("r3: supported only by claimed facts (")
    assert codes(root) == [] and ledger.verify_chain(root) == []
    (completed,) = evl_events(root)
    assert completed["type"] == "evaluation.completed" and completed["new_state"] == "pursue"
    assert guarded(job) == keep

    # 3. re-run: no file change, no new event
    good, count = job["eval"].read_bytes(), len(ledger.read_events(root))
    assert run(root, "evaluate", job["slug"]).exit_code == 0
    assert job["eval"].read_bytes() == good and len(ledger.read_events(root)) == count

    # 4. hand-edit result.tier: validate reports EVL007 and changes nothing; evaluate replaces it and heals
    edit(root, job, lambda fm: fm["result"].update(tier="skip"))
    snap, ledger_bytes = snapshot(root), ledger_file.read_bytes()
    assert "EVL007" in codes(root)
    assert run(root, "validate").exit_code == 1
    assert snapshot(root) == snap and ledger_file.read_bytes() == ledger_bytes
    assert run(root, "evaluate", job["slug"]).exit_code == 0
    replaced = evl_events(root, "evaluation.result_replaced")
    assert [e["reason"] for e in replaced] == ["tampered"]
    assert job["eval"].read_bytes() == good and codes(root) == []
    snap, ledger_bytes = snapshot(root), ledger_file.read_bytes()
    assert run(root, "validate").exit_code == 0
    assert snapshot(root) == snap and ledger_file.read_bytes() == ledger_bytes
    assert guarded(job) == keep

    # 5. each staleness cause on its own: EVL008 names exactly that stamp, then it is cleared
    jd = job["path"].parent / "jd.md"
    jd.write_text(JD.replace("Example Corp", "Other Corp"), encoding="utf-8")
    assert stale_stamps() == ["jd"]
    evaluate_clears_staleness()
    keep = guarded(job)

    ops.dispute_fact(root, ids["skill_go"], "not true")                       # a fact's status
    assert stale_stamps() == ["memory"]
    keep = guarded(job)                                                       # the dispute is the edit, not evaluate's
    evaluate_clears_staleness()
    assert guarded(job) == keep

    (root / "profile.md").write_text(PROFILE.replace("| Location match or remote-friendly | Medium |",
                                                     "| Location match or remote-friendly | High |"), encoding="utf-8")
    assert stale_stamps() == ["rubric"]                                        # a rubric weight
    evaluate_clears_staleness()

    with monkeypatch.context() as m:                                           # the rules version
        m.setattr(rules, "RULES_VERSION", "3")
        assert stale_stamps() == ["rules"]
    assert codes(root) == []

    edit(root, job, lambda fm: fm["agent"]["requirements"][0].update(text="Kubernetes in production"))   # the agent half
    assert stale_stamps() == ["inputs"]
    evaluate_clears_staleness()
    assert guarded(job) == keep

    # 6. a ledger failure restores the file and logs nothing; a re-run succeeds
    edit(root, job, lambda fm: fm["agent"]["fit"][1].update(judgement="yes"))
    previous, count = job["eval"].read_bytes(), len(ledger.read_events(root))
    with monkeypatch.context() as m:
        m.setattr(service.ledger, "append_events", boom)
        res = run(root, "evaluate", job["slug"])
        assert res.exit_code != 0 and "Traceback" not in res.output
    assert job["eval"].read_bytes() == previous and len(ledger.read_events(root)) == count
    assert run(root, "evaluate", job["slug"]).exit_code == 0

    # 6b. the crash window: the append fails and the rollback never runs -> EVL009 (read-only), a re-run heals
    edit(root, job, lambda fm: fm["agent"]["fit"][1].update(judgement="no"))
    count = len(ledger.read_events(root))
    with monkeypatch.context() as m:
        m.setattr(service.ledger, "append_events", boom)
        m.setattr(store.Transaction, "rollback", lambda self: None)
        assert run(root, "evaluate", job["slug"]).exit_code != 0
    assert len(ledger.read_events(root)) == count
    snap, ledger_bytes = snapshot(root), ledger_file.read_bytes()
    assert "EVL009" in codes(root)
    assert snapshot(root) == snap and ledger_file.read_bytes() == ledger_bytes
    assert run(root, "evaluate", job["slug"]).exit_code == 0
    assert codes(root) == [] and ledger.verify_chain(root) == []

    # 7. isolation across the whole flow: career/ (apart from the deliberate dispute), job status and jd.md
    assert guarded(job) == keep
    assert split_frontmatter(job["path"].read_text(encoding="utf-8"))[0]["status"] == "DISCOVERED"
    assert [e for e in ledger.read_events(root) if e["entity"] == job["id"]] == job_events

    # 8. a refresh delivers the evaluate skill; prep carries the read-only freshness rule and the intact evidence gate
    ws = tmp_path / "scaffolded"
    scaffold(ws, runtime="claude")
    target = ws / ".claude" / "skills" / "evaluate" / "SKILL.md"
    target.unlink()
    assert ".claude/skills/evaluate/SKILL.md" in scaffold(ws, refresh=True, runtime="claude")
    assert target.read_text(encoding="utf-8") == skill("evaluate")
    prep = (ws / ".claude" / "skills" / "prep" / "SKILL.md").read_text(encoding="utf-8")
    step2 = prep[prep.index("## STEP 2"):prep.index("## STEP 3")]
    assert "careeros validate" in step2 and "do not run `careeros evaluate` to test freshness" in step2
    gate = prep[prep.index("## EVIDENCE GATE (mandatory)"):]
    gate = gate[:gate.index("\n---\n")]
    assert hashlib.sha256(gate.encode("utf-8")).hexdigest() == PREP_GATE_BEFORE
