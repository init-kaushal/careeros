"""`careeros evaluate`: the transactional service, the reserved events, and the validate integration. Invented data only."""

from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest
from helpers import make_workspace, snapshot
from test_evaluation_record import JD, agent_half, edit, make_job, write_agent
from typer.testing import CliRunner

from careeros.cli.main import app
from careeros.core import ledger, models
from careeros.core.evaluation import canon, integrity, ledgercheck, record, service
from careeros.core.memory import store
from careeros.core.validation import validate_workspace
from careeros.core.workspace import split_frontmatter

runner = CliRunner()


def run(root: Path, *args: str):
    return runner.invoke(app, [*args, "--workspace", str(root)])


def evl_events(root: Path, prefix: str = "evaluation.") -> list[dict]:
    return [e for e in ledger.read_events(root) if e["type"].startswith(prefix)]


def codes(root: Path) -> list[str]:
    return sorted(i.code for i in validate_workspace(root) if i.code.startswith("EVL") or i.code == "LED003")


@pytest.fixture
def fresh(career):
    """A workspace with a job, jd.md and a filled agent half but no result yet."""
    root, ids = career
    job = make_job(root)
    write_agent(root, job, agent_half(ids), evaluate=False)
    return root, ids, job


@pytest.fixture
def done(fresh):
    """The same, evaluated once through the command."""
    root, ids, job = fresh
    assert run(root, "evaluate", job["id"]).exit_code == 0
    return root, ids, job


def boom(*a, **k):
    raise OSError("disk full")


# --- happy path and idempotence --------------------------------------------------------------

def test_init_then_fill_then_evaluate(career) -> None:
    root, ids = career
    job = make_job(root)
    before_events = len(ledger.read_events(root))
    res = run(root, "evaluate", job["slug"], "--init")
    assert res.exit_code == 0, res.output
    assert len(ledger.read_events(root)) == before_events          # --init logs nothing
    rec = record.load(job["eval"], root)
    assert rec.id.startswith("evl_") and rec.job == job["id"] and rec.agent == {}
    write_agent(root, job, agent_half(ids), evaluate=False, extra={"id": rec.id})
    res = run(root, "evaluate", job["slug"])
    assert res.exit_code == 0, res.output
    result = record.load(job["eval"], root).result
    assert (result["tier"], result["combined"], result["confidence"]) == ("pursue", 0.8, "low")
    (event,) = evl_events(root)
    assert event["type"] == "evaluation.completed" and event["entity"] == rec.id
    assert event["new_state"] == "pursue" and "pursue" in event["action"] and "low" in event["action"]
    assert event["artifacts"] == ["jobs/discovered/example-platform/evaluation.md", f"sha256:{canon.result_sha256(result)}"]
    assert ledger.verify_chain(root) == []
    assert codes(root) == []


def test_second_run_is_a_byte_identical_no_op(done) -> None:
    root, _, job = done
    first, count = job["eval"].read_bytes(), len(ledger.read_events(root))
    res = run(root, "evaluate", job["id"])
    assert res.exit_code == 0
    assert job["eval"].read_bytes() == first and len(ledger.read_events(root)) == count


def test_json_success_is_pure_json(fresh) -> None:
    root, _, job = fresh
    res = run(root, "evaluate", job["id"], "--json")
    assert res.exit_code == 0
    data = json.loads(res.stdout)
    assert data["tier"] == "pursue" and data["job"] == job["id"] and data["id"].startswith("evl_")
    again = json.loads(run(root, "evaluate", job["id"], "--json").stdout)
    assert again["outcome"] == "unchanged"


# --- rejections -------------------------------------------------------------------------------

def bad_agents(ids: dict) -> dict:
    def mutate(fn):
        agent = copy.deepcopy(agent_half(ids))
        fn(agent)
        return agent
    return {
        "EVL002": mutate(lambda a: a["requirements"][1].update(id="r1")),
        "EVL003": mutate(lambda a: a["requirements"][0].update(**{"class": "bogus"})),
        "EVL004": mutate(lambda a: a["requirements"][0].update(quote="not in the posting")),
        "EVL005": mutate(lambda a: a["requirements"][2]["links"][0].update(fact="ach_zzzzzzzzzz")),
        "EVL006": mutate(lambda a: a["job_facts"].update(market="Nowhere")),
    }


@pytest.mark.parametrize("code", ["EVL002", "EVL003", "EVL004", "EVL005", "EVL006"])
def test_invalid_inputs_are_rejected_once_and_change_nothing(fresh, code) -> None:
    root, ids, job = fresh
    write_agent(root, job, bad_agents(ids)[code], evaluate=False)
    snap = snapshot(root)
    res = run(root, "evaluate", job["id"])
    assert res.exit_code == 1
    assert snapshot(root) == snap
    (event,) = evl_events(root)
    assert event["type"] == "evaluation.rejected" and event["entity"] == job["id"] and code in event["reason"]
    assert run(root, "evaluate", job["id"]).exit_code == 1
    assert len(evl_events(root)) == 1                                # the identical attempt is not logged twice


def test_rejection_json_lists_issues_and_only_json(fresh) -> None:
    root, ids, job = fresh
    write_agent(root, job, bad_agents(ids)["EVL004"], evaluate=False)
    res = run(root, "evaluate", job["id"], "--json")
    assert res.exit_code == 1
    data = json.loads(res.stdout)
    assert data["issues"][0]["code"] == "EVL004"


def test_a_missing_jd_is_rejected(fresh) -> None:
    root, _, job = fresh
    (job["path"].parent / "jd.md").unlink()
    assert run(root, "evaluate", job["id"]).exit_code == 1
    assert "EVL004" in evl_events(root)[0]["reason"]


def test_a_different_rejection_is_logged_again(fresh) -> None:
    root, ids, job = fresh
    write_agent(root, job, bad_agents(ids)["EVL004"], evaluate=False)
    run(root, "evaluate", job["id"])
    write_agent(root, job, bad_agents(ids)["EVL002"], evaluate=False)
    run(root, "evaluate", job["id"])
    assert [e["type"] for e in evl_events(root)] == ["evaluation.rejected"] * 2


def test_evaluate_without_a_record_says_to_init(fresh) -> None:
    root, _, job = fresh
    job["eval"].unlink()
    res = run(root, "evaluate", job["id"])
    assert res.exit_code == 1 and "--init" in res.output
    assert not job["eval"].exists()


# --- usage errors ------------------------------------------------------------------------------

def test_memory_with_errors_is_refused_with_exit_2(fresh) -> None:
    root, _, job = fresh
    fact = next((root / "career").rglob("*.md"))
    fact.write_text("---\nbroken: [\n---\n", encoding="utf-8")
    assert store.load_career(root).errors
    snap, count = snapshot(root), len(ledger.read_events(root))
    res = run(root, "evaluate", job["id"])
    assert res.exit_code == 2
    assert snapshot(root) == snap and len(ledger.read_events(root)) == count


def test_workspace_newer_than_installed_is_refused(tmp_path: Path, clock) -> None:
    root = make_workspace(tmp_path / "ws", framework_version="9.0.0")
    job = make_job(root)
    res = run(root, "evaluate", job["id"], "--init")
    assert res.exit_code == 2 and "newer" in res.output
    assert not job["eval"].exists()


def test_unknown_job_is_exit_2(career) -> None:
    root, _ = career
    assert run(root, "evaluate", "job_nope", "--init").exit_code == 2
    assert run(root, "evaluate", "job_nope").exit_code == 2


def test_init_refuses_an_existing_record(done) -> None:
    root, _, job = done
    before = job["eval"].read_bytes()
    res = run(root, "evaluate", job["id"], "--init")
    assert res.exit_code == 1 and job["eval"].read_bytes() == before


def test_init_json_is_pure_json(career) -> None:
    root, _ = career
    job = make_job(root)
    data = json.loads(run(root, "evaluate", job["id"], "--init", "--json").stdout)
    assert data["path"] == "jobs/discovered/example-platform/evaluation.md"
    res = run(root, "evaluate", job["id"], "--init", "--json")
    assert res.exit_code == 1 and "error" in json.loads(res.stdout)


def test_init_makes_a_fresh_id_when_the_ledger_knows_one(career, monkeypatch) -> None:
    root, _ = career
    job = make_job(root)
    taken = "evl_aaaaaaaaaa"
    ledger.append_event(root, type="note.added", actor="system", entity=taken, action="x")
    monkeypatch.setattr("careeros.core.ids.new_id",
                        lambda kind, taken_ids=(): taken if taken not in taken_ids else "evl_bbbbbbbbbb")
    run(root, "evaluate", job["id"], "--init")
    assert record.load(job["eval"], root).id == "evl_bbbbbbbbbb"


# --- failure injection ---------------------------------------------------------------------------

def test_ledger_failure_restores_the_previous_file_and_a_rerun_succeeds(done, monkeypatch) -> None:
    root, _, job = done
    edit(root, job, lambda fm: fm["agent"]["fit"][1].update(judgement="yes"))   # a changed judgement: new result
    previous, count = job["eval"].read_bytes(), len(ledger.read_events(root))
    with monkeypatch.context() as m:
        m.setattr(service.ledger, "append_events", boom)
        res = run(root, "evaluate", job["id"])
        assert res.exit_code != 0 and "Traceback" not in res.output
    assert job["eval"].read_bytes() == previous and len(ledger.read_events(root)) == count
    assert run(root, "evaluate", job["id"]).exit_code == 0
    assert len(evl_events(root)) == 2 and codes(root) == []


def test_ledger_failure_on_first_evaluation_leaves_no_result(fresh, monkeypatch) -> None:
    root, _, job = fresh
    previous = job["eval"].read_bytes()
    monkeypatch.setattr(service.ledger, "append_events", boom)
    assert run(root, "evaluate", job["id"]).exit_code != 0
    assert job["eval"].read_bytes() == previous and evl_events(root) == []


def test_init_writes_no_event_and_a_write_failure_leaves_no_file(career, monkeypatch) -> None:
    root, _ = career
    job = make_job(root)
    with monkeypatch.context() as m:
        m.setattr(service.ledger, "append_events", boom)                         # --init must not touch the ledger
        assert run(root, "evaluate", job["id"], "--init").exit_code == 0
    job["eval"].unlink()
    monkeypatch.setattr(store.Transaction, "write", boom)
    assert run(root, "evaluate", job["id"], "--init").exit_code != 0
    assert not job["eval"].exists()


# --- tamper, stale, versions ---------------------------------------------------------------------

def test_tamper_is_reported_by_validate_then_healed_by_evaluate(done) -> None:
    root, _, job = done
    good = job["eval"].read_bytes()
    edit(root, job, lambda fm: fm["result"].update(tier="skip"))
    tampered = job["eval"].read_bytes()
    snap = snapshot(root)
    assert "EVL007" in codes(root)
    assert snapshot(root) == snap                                   # validate wrote nothing
    count = len(ledger.read_events(root))
    assert run(root, "evaluate", job["id"]).exit_code == 0
    new = ledger.read_events(root)[count:]
    assert [e["type"] for e in new] == ["evaluation.result_replaced", "evaluation.completed"]
    assert new[0]["reason"] == "tampered" and new[0]["entity"] == new[1]["entity"]
    assert job["eval"].read_bytes() == good != tampered
    assert codes(root) == []


def test_tamper_and_stale_logs_both_reasons(done) -> None:
    root, _, job = done
    edit(root, job, lambda fm: fm["result"].update(tier="skip"))
    (job["path"].parent / "jd.md").write_text(JD.replace("Example Corp", "Other Corp"), encoding="utf-8")
    run(root, "evaluate", job["id"])
    assert [e["reason"] for e in evl_events(root, "evaluation.result_replaced")] == ["tampered,stale"]


def test_stale_only_warns_and_evaluate_logs_completed_without_replacement(done) -> None:
    root, _, job = done
    (job["path"].parent / "jd.md").write_text(JD.replace("Example Corp", "Other Corp"), encoding="utf-8")
    assert "EVL008" in codes(root) and "EVL007" not in codes(root)
    count = len(ledger.read_events(root))
    assert run(root, "evaluate", job["id"]).exit_code == 0
    assert [e["type"] for e in ledger.read_events(root)[count:]] == ["evaluation.completed"]
    assert codes(root) == []


def test_unsupported_version_is_replaced_and_validates_clean(done) -> None:
    root, _, job = done
    edit(root, job, lambda fm: fm["result"]["basis"].update(basis_version="99"))
    assert "EVL010" in codes(root)
    count = len(ledger.read_events(root))
    assert run(root, "evaluate", job["id"]).exit_code == 0
    replaced, completed = ledger.read_events(root)[count:]
    assert replaced["type"] == "evaluation.result_replaced" and replaced["reason"] not in ("tampered", "tampered,stale")
    assert replaced["reason"] == "stale" and completed["type"] == "evaluation.completed"
    assert codes(root) == []


# --- the crash window (spec 16.4) --------------------------------------------------------------------

def test_crash_window_leaves_the_file_and_validate_reports_evl009_and_a_rerun_heals(fresh, monkeypatch) -> None:
    root, _, job = fresh
    with monkeypatch.context() as m:
        m.setattr(service.ledger, "append_events", boom)
        m.setattr(store.Transaction, "rollback", lambda self: None)             # the process died: no rollback
        assert run(root, "evaluate", job["id"]).exit_code != 0
    assert record.load(job["eval"], root).result is not None                    # the new file is on disk
    assert evl_events(root, "evaluation.completed") == []
    snap = snapshot(root)
    assert "EVL009" in codes(root)
    assert snapshot(root) == snap
    assert run(root, "evaluate", job["id"]).exit_code == 0
    assert len(evl_events(root, "evaluation.completed")) == 1
    assert "EVL009" not in codes(root)


def test_the_same_fault_with_a_working_rollback_restores_the_previous_file(fresh, monkeypatch) -> None:
    root, _, job = fresh
    previous = job["eval"].read_bytes()
    monkeypatch.setattr(service.ledger, "append_events", boom)
    run(root, "evaluate", job["id"])
    assert job["eval"].read_bytes() == previous


def test_a_completion_with_a_wrong_hash_is_reported_by_validate(fresh, monkeypatch) -> None:
    root, _, job = fresh
    monkeypatch.setattr(service.canon, "result_sha256", lambda result: "0" * 64)
    assert run(root, "evaluate", job["id"]).exit_code == 0
    monkeypatch.undo()
    assert any("mismatched" in i.message for i in validate_workspace(root) if i.code == "EVL009")


# --- guarantees ----------------------------------------------------------------------------------

def test_evaluate_never_changes_job_state_jd_or_career(fresh) -> None:
    root, _, job = fresh
    before = snapshot(root)
    job_events = [e for e in ledger.read_events(root) if e["entity"] == job["id"]]
    assert run(root, "evaluate", job["id"]).exit_code == 0
    after = snapshot(root)
    changed = {k for k in set(before) | set(after) if before.get(k) != after.get(k)}
    assert changed == {"jobs/discovered/example-platform/evaluation.md"}
    assert not [k for k in changed if k.startswith("career/")]
    status = lambda: split_frontmatter(job["path"].read_text(encoding="utf-8"))[0]["status"]  # noqa: E731
    assert status() == "DISCOVERED"
    assert [e for e in ledger.read_events(root) if e["entity"] == job["id"]] == job_events


def test_ledger_append_refuses_the_reserved_evaluation_types(career) -> None:
    root, _ = career
    for kind in ("evaluation.completed", "evaluation.rejected", "evaluation.result_replaced", "evaluation.other"):
        assert ledger.is_reserved(kind)
        res = run(root, "ledger", "append", "--type", kind, "--action", "x")
        assert res.exit_code == 2
    with pytest.raises(ledger.LedgerError, match="reason"):
        ledger.append_event(root, type="evaluation.rejected", actor="system", action="x", entity="job_x")


def test_led003_fires_when_an_evaluation_file_is_deleted(done) -> None:
    root, _, job = done
    entity = record.load(job["eval"], root).id
    job["eval"].unlink()
    issues = [i for i in validate_workspace(root) if i.code == "LED003"]
    assert len(issues) == 1 and entity in issues[0].message


def test_validate_cli_lists_evaluation_issues(done) -> None:
    root, _, job = done
    edit(root, job, lambda fm: fm["result"].update(tier="skip"))
    res = run(root, "validate", "--json")
    assert any(i["code"] == "EVL007" for i in json.loads(res.stdout)["issues"])


def test_import_order_has_no_cycle() -> None:
    for first in ("careeros.core.validation", "careeros.cli.main", "careeros.core.evaluation.service"):
        out = subprocess.run([sys.executable, "-c", f"import {first}; import careeros.core.validation; "
                              "import careeros.cli.main; import careeros.core.evaluation.service"],
                             capture_output=True, text=True)
        assert out.returncode == 0, out.stderr


# --- a copied job directory (duplicate evl_ id) -------------------------------------------------------

def test_duplicated_evaluation_id_is_refused_and_recovers_after_deleting_the_copy(done) -> None:
    import shutil
    root, _, job = done
    copy_dir = root / "jobs" / "discovered" / "example-copy"
    shutil.copytree(job["path"].parent, copy_dir)
    from helpers import add_job  # noqa: F401
    fm, body = split_frontmatter((copy_dir / "job.md").read_text(encoding="utf-8"))
    fm["id"] = "job_copycopy12"
    from careeros.core.workspace import join_frontmatter
    (copy_dir / "job.md").write_text(join_frontmatter(fm, body), encoding="utf-8")
    efm, ebody = split_frontmatter((copy_dir / "evaluation.md").read_text(encoding="utf-8"))
    efm["job"] = "job_copycopy12"
    (copy_dir / "evaluation.md").write_text(join_frontmatter(efm, ebody), encoding="utf-8")
    completed = len(evl_events(root, "evaluation.completed"))
    snap = snapshot(root)
    for ref in (job["id"], "example-copy", job["id"], "example-copy"):
        res = run(root, "evaluate", ref)
        assert res.exit_code == 1 and "--init" in res.output
        assert snapshot(root) == snap
    assert len(evl_events(root, "evaluation.completed")) == completed
    rejected = evl_events(root, "evaluation.rejected")
    assert rejected and all(e["reason"].startswith("EVL009: duplicate evaluation id (also used by ") for e in rejected)
    assert len(rejected) == 2                                   # one per job; identical repeats are deduped
    res = run(root, "evaluate", job["id"], "--json")
    assert res.exit_code == 1 and json.loads(res.stdout)["issues"][0]["code"] == "EVL009"
    (copy_dir / "evaluation.md").unlink()
    assert run(root, "evaluate", job["id"]).exit_code == 0
