"""EVL009: the evaluation file against its `evaluation.completed` ledger event (Task D, spec 16.4). Read-only checks.

Events are appended directly with `ledger.append_events` (the test plays the part of `careeros evaluate`, which Task E
adds); every `validate` call is shown to leave the workspace files and `ledger.jsonl` byte-identical.
"""

from __future__ import annotations

import shutil

import pytest
from test_evaluation_record import agent_half, make_job, write_agent

from careeros.core import ids as ids_mod
from careeros.core import ledger
from careeros.core.evaluation import canon, integrity, ledgercheck, record

GOOD_HASH = "sha256:" + "0" * 64


def files(root) -> dict:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


def validate(root) -> list:
    """`validate`'s EVL009 warnings, asserting that nothing on disk changed."""
    before = files(root)
    found = [i for i in integrity.validate_workspace(root) if i.code == "EVL009"]
    assert files(root) == before
    assert all(i.severity == "warning" for i in found)
    return found


def rec_of(root, job) -> record.Record:
    return record.load(job["eval"], root)


def complete(root, rec: record.Record, *, artifacts=None, entity=None) -> None:
    if artifacts is None:
        artifacts = [rec.rel, "sha256:" + canon.result_sha256(rec.result)]
    ledger.append_events(root, [{"type": "evaluation.completed", "actor": "system", "entity": entity or rec.id,
                                 "action": "evaluated", "artifacts": artifacts}])


def other_event(root, type_, entity, artifacts=None, reason="EVL004") -> None:
    spec = {"type": type_, "actor": "system", "entity": entity, "action": "x", "reason": reason}
    if artifacts is not None:
        spec["artifacts"] = artifacts
    ledger.append_events(root, [spec])


@pytest.fixture
def ws(career):
    root, ids = career
    job = make_job(root)
    write_agent(root, job, agent_half(ids))
    return root, ids, job


def evaluate_again(root, ids, job, judgement: str) -> record.Record:
    agent = agent_half(ids)
    agent["fit"][1]["judgement"] = judgement
    write_agent(root, job, agent, extra={"id": rec_of(root, job).id})
    rec = rec_of(root, job)
    complete(root, rec)
    return rec


# 1
def test_matching_completion_event_gives_no_warning(ws):
    root, ids, job = ws
    complete(root, rec_of(root, job))
    assert validate(root) == []


# 2
def test_result_without_a_completion_event_is_missing(ws):
    root, ids, job = ws
    found = validate(root)
    assert len(found) == 1 and found[0].message.startswith("missing")
    assert found[0].path == "jobs/discovered/example-platform/evaluation.md"


# 3
@pytest.mark.parametrize("artifacts", [
    ["PATH"],                                                   # no hash
    ["PATH", "HASH", "HASH"],                                   # two hashes
    ["PATH", "sha256:" + "g" * 64],                             # not hex
    ["PATH", "sha256:" + "a" * 63],                             # wrong length
    ["PATH", "sha256:" + "A" * 64],                             # not lower case
    ["HASH"],                                                   # no path
    ["PATH", "PATH", "HASH"],                                   # two paths
    "not a list",
])
def test_malformed_completion_event(ws, artifacts):
    root, ids, job = ws
    rec = rec_of(root, job)
    real = "sha256:" + canon.result_sha256(rec.result)
    if isinstance(artifacts, list):
        artifacts = [rec.rel if a == "PATH" else real if a == "HASH" else a for a in artifacts]
    complete(root, rec, artifacts=artifacts)
    found = validate(root)
    assert len(found) == 1 and found[0].message.startswith("malformed"), found


def test_completion_event_with_a_different_hash_is_mismatched(ws):
    root, ids, job = ws
    rec = rec_of(root, job)
    complete(root, rec, artifacts=[rec.rel, GOOD_HASH])
    found = validate(root)
    assert len(found) == 1 and found[0].message.startswith("mismatched")


def test_extra_non_path_artifacts_such_as_the_job_id_are_ignored(ws):
    root, ids, job = ws
    rec = rec_of(root, job)
    complete(root, rec, artifacts=[job["id"], rec.rel, "sha256:" + canon.result_sha256(rec.result)])
    assert validate(root) == []


def test_an_unhashable_hand_edited_result_is_mismatched_not_a_crash(ws):
    root, ids, job = ws
    complete(root, rec_of(root, job))
    text = job["eval"].read_text(encoding="utf-8").replace("combined: 0.8", "combined: high")
    job["eval"].write_text(text, encoding="utf-8")
    found = validate(root)
    assert len(found) == 1 and found[0].message.startswith("mismatched")


# 4
def test_never_evaluated_and_skeleton_give_nothing(career):
    root, ids = career
    job = make_job(root)
    assert validate(root) == []
    job["eval"].write_bytes(record.skeleton(job["id"], "2026-10-10T09:15:00Z"))
    assert validate(root) == []
    write_agent(root, job, agent_half(ids), evaluate=False)
    assert validate(root) == []


def test_result_deleted_after_a_completion_is_mismatched(ws):
    root, ids, job = ws
    rec = rec_of(root, job)
    complete(root, rec)
    write_agent(root, job, agent_half(ids), evaluate=False, extra={"id": rec.id})
    found = validate(root)
    assert len(found) == 1 and found[0].message.startswith("mismatched")


# 5
def test_only_the_latest_completion_counts_and_a_hand_reverted_file_is_mismatched(ws):
    root, ids, job = ws
    first = rec_of(root, job)
    complete(root, first)
    first_bytes = job["eval"].read_bytes()
    second = evaluate_again(root, ids, job, "no")
    third = evaluate_again(root, ids, job, "yes")
    hashes = {canon.result_sha256(r.result) for r in (first, second, third)}
    assert len(hashes) == 3 and first.id == second.id == third.id
    assert validate(root) == []
    job["eval"].write_bytes(first_bytes)
    found = validate(root)
    assert len(found) == 1 and found[0].message.startswith("mismatched")


# 6
def test_rejections_failed_attempts_and_replacements_never_affect_the_comparison(ws):
    root, ids, job = ws
    complete(root, rec_of(root, job))
    other_event(root, "evaluation.rejected", job["id"], reason="EVL004")
    kept = evaluate_again(root, ids, job, "no")
    kept_bytes = job["eval"].read_bytes()
    # a later attempt whose ledger append failed: the file was restored and no completion was written
    write_agent(root, job, agent_half(ids), extra={"id": kept.id})
    job["eval"].write_bytes(kept_bytes)
    other_event(root, "evaluation.result_replaced", kept.id, artifacts=[kept.rel, GOOD_HASH], reason="tampered")
    assert validate(root) == []


# 7
def test_crash_window_is_detected_read_only_and_healed_by_the_next_completion(ws):
    root, ids, job = ws
    assert validate(root)[0].message.startswith("missing")          # first evaluation: file written, no event
    complete(root, rec_of(root, job))
    agent = agent_half(ids)
    agent["fit"][1]["judgement"] = "no"
    write_agent(root, job, agent, extra={"id": rec_of(root, job).id})  # second evaluation: file written, append lost
    found = validate(root)
    assert len(found) == 1 and found[0].message.startswith("mismatched")
    complete(root, rec_of(root, job))                                 # the next `evaluate` writes a fresh completion
    assert validate(root) == []


# 8
def test_copied_job_directory_is_ambiguous_for_both(ws):
    root, ids, job = ws
    complete(root, rec_of(root, job))
    shutil.copytree(job["path"].parent, root / "jobs" / "discovered" / "copy-of-platform")
    found = validate(root)
    assert sorted(i.path for i in found) == ["jobs/discovered/copy-of-platform/evaluation.md",
                                              "jobs/discovered/example-platform/evaluation.md"]
    assert all(i.message.startswith("ambiguous") for i in found)


def test_record_moved_to_another_job_directory_is_ambiguous(ws):
    root, ids, job = ws
    complete(root, rec_of(root, job))
    other = make_job(root, "other-platform")
    shutil.move(job["eval"], other["eval"])
    found = validate(root)
    assert len(found) == 1 and found[0].path == "jobs/discovered/other-platform/evaluation.md"
    assert found[0].message.startswith("ambiguous") and "example-platform" in found[0].message


def test_events_for_other_entities_never_interfere(ws):
    root, ids, job = ws
    rec = rec_of(root, job)
    complete(root, rec)
    complete(root, rec, entity="evl_zzzzzzzzzz", artifacts=["jobs/discovered/x/evaluation.md", GOOD_HASH])
    other_event(root, "evaluation.completed", job["id"], artifacts=[rec.rel, GOOD_HASH])
    assert validate(root) == []


def test_verify_directly_with_explicit_records_and_events(ws):
    root, ids, job = ws
    rec = rec_of(root, job)
    good = {"seq": 3, "type": "evaluation.completed", "entity": rec.id,
            "artifacts": [rec.rel, "sha256:" + canon.result_sha256(rec.result)]}
    older = dict(good, seq=2, artifacts=[rec.rel, GOOD_HASH])
    assert ledgercheck.verify(rec, [rec], [good, older]) == []
    assert ledgercheck.verify(rec, [rec], [dict(good, seq=1), older])[0].message.startswith("mismatched")
    assert ledgercheck.verify(rec, [rec], [{"seq": "9", "type": "evaluation.completed", "entity": rec.id}, good]) == []


# 9
def test_init_never_reuses_an_evaluation_id_from_files_or_the_ledger(ws, monkeypatch):
    root, ids, job = ws
    on_disk = rec_of(root, job).id
    other_event(root, "evaluation.result_replaced", "evl_qqqqqqqqqq", reason="stale")
    taken = record.taken_ids(root, ledger.read_events(root))
    assert {on_disk, "evl_qqqqqqqqqq"} <= taken
    assert not any(t.startswith("job_") for t in taken)
    seen = {}
    real = ids_mod.new_id

    def spy(kind, existing=()):
        seen["existing"] = set(existing)
        return real(kind, existing)

    monkeypatch.setattr(record.ids, "new_id", spy)
    data = record.skeleton(job["id"], "2026-10-10T09:15:00Z", taken=taken)
    assert {on_disk, "evl_qqqqqqqqqq"} <= seen["existing"]
    assert b"id: evl_" in data and on_disk.encode() not in data
