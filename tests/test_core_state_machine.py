import itertools
from pathlib import Path

import pytest
from helpers import add_job, make_workspace

from careeros.core import ledger, versions
from careeros.core import state_machine as sm
from careeros.core import workspace as ws
from careeros.core.models import State
from careeros.core.validation import validate_workspace

PRE = [State.DISCOVERED, State.EVALUATED, State.SHORTLISTED, State.RESEARCHED, State.PREPARING, State.READY_TO_APPLY]
POST = [State.APPLIED, State.RECRUITER_REPLIED, State.SCREEN, State.TECHNICAL, State.HM, State.FINAL, State.OFFER]


def _legal_pairs() -> set[tuple[State, State]]:
    """The spec's transition table, written out independently of the implementation."""
    legal: set[tuple[State, State]] = set()
    for i, a in enumerate(PRE):
        legal.update((a, b) for b in PRE[i + 1:])
    legal.add((State.READY_TO_APPLY, State.APPROVAL_REQUIRED))
    legal.add((State.APPROVAL_REQUIRED, State.APPLIED))
    for i, a in enumerate(POST):
        legal.update((a, b) for b in POST[i + 1:])
        legal.add((a, State.REJECTED))
    legal.add((State.OFFER, State.ACCEPTED))
    for a in PRE + [State.APPROVAL_REQUIRED] + POST:
        legal.add((a, State.WITHDRAWN))
    return legal


def test_is_legal_matches_the_spec_table_for_every_pair() -> None:
    legal = _legal_pairs()
    wrong = [(f.name, t.name) for f, t in itertools.product(State, State) if sm.is_legal(f, t) != ((f, t) in legal)]
    assert wrong == []


def test_display_icon_and_bullet_helpers() -> None:
    assert sm.display_for(State.DISCOVERED) == "discovered"
    assert sm.display_for(State.SCREEN) == "interview"
    assert sm.display_for(State.APPROVAL_REQUIRED) == "approval-required"
    assert sm.display_for(State.WITHDRAWN) == "closed"
    assert sm.bullet_matches("declined", State.WITHDRAWN)
    assert sm.bullet_matches("Interview", State.TECHNICAL)
    assert not sm.bullet_matches("applied", State.DISCOVERED)
    assert [sm.icon_for(s) for s in (State.DISCOVERED, State.APPROVAL_REQUIRED, State.APPLIED, State.HM, State.OFFER, State.ACCEPTED, State.REJECTED)] == [
        " ", " ", "~", "?", "✓", "✓", "x",
    ]


@pytest.fixture
def workspace(tmp_path: Path, clock) -> Path:
    return make_workspace(tmp_path / "ws")


def _events(root: Path) -> list[dict]:
    return ledger.read_events(root)


def test_legal_transition_updates_job_bullet_pipeline_and_ledger(workspace: Path) -> None:
    job = add_job(workspace, "acme-backend", status=State.APPLIED, bullet="applied")
    before = job["path"].read_text(encoding="utf-8")
    result = sm.apply_transition(workspace, job["id"], State.SCREEN, actor="user")
    assert result.outcome == "changed"
    reread = ws.read_job(job["path"])
    assert reread.status is State.SCREEN
    assert reread.updated_at != "2026-10-01T00:00:00Z"
    assert "- **Status:** interview" in job["path"].read_text(encoding="utf-8")
    assert reread.body == before.split("---\n", 2)[2].replace("- **Status:** applied", "- **Status:** interview")
    assert (workspace / "jobs" / "pipeline.md").read_text(encoding="utf-8").count("- [?] **Acme**") == 1
    event = _events(workspace)[-1]
    assert (event["type"], event["prev_state"], event["new_state"], event["entity"], event["actor"]) == (
        "job.status_changed", "APPLIED", "SCREEN", job["id"], "user",
    )


def test_transition_accepts_slug_and_leaves_other_jobs_alone(workspace: Path) -> None:
    one = add_job(workspace, "one")
    two = add_job(workspace, "two")
    sm.apply_transition(workspace, "one", State.EVALUATED, actor="user")
    assert ws.read_job(one["path"]).status is State.EVALUATED
    assert ws.read_job(two["path"]).status is State.DISCOVERED


def test_repeating_a_transition_is_a_no_op(workspace: Path) -> None:
    job = add_job(workspace, "acme")
    sm.apply_transition(workspace, job["id"], State.EVALUATED, actor="user")
    snapshot = (job["path"].read_bytes(), len(_events(workspace)))
    again = sm.apply_transition(workspace, job["id"], State.EVALUATED, actor="user")
    assert again.outcome == "unchanged"
    assert (job["path"].read_bytes(), len(_events(workspace))) == snapshot


def test_retry_after_a_crash_between_job_write_and_ledger_append_repairs_the_ledger_once(workspace: Path) -> None:
    job = add_job(workspace, "acme")
    frontmatter, body = ws.split_frontmatter(job["path"].read_text(encoding="utf-8"))
    frontmatter["status"] = "EVALUATED"  # the process died after this write and before the ledger append
    job["path"].write_text(ws.join_frontmatter(frontmatter, body), encoding="utf-8")
    result = sm.apply_transition(workspace, job["id"], State.EVALUATED, actor="user")
    assert result.outcome == "repaired"
    event = _events(workspace)[-1]
    assert (event["source"], event["prev_state"], event["new_state"]) == ("recovery", "DISCOVERED", "EVALUATED")
    count = len(_events(workspace))
    assert sm.apply_transition(workspace, job["id"], State.EVALUATED, actor="user").outcome == "unchanged"
    assert len(_events(workspace)) == count


def test_illegal_transition_is_rejected_unchanged_and_logged_once(workspace: Path) -> None:
    job = add_job(workspace, "acme")
    before = job["path"].read_bytes()
    for _ in range(2):
        with pytest.raises(sm.TransitionError) as exc:
            sm.apply_transition(workspace, job["id"], State.OFFER, actor="agent:claude")
        assert exc.value.code == "illegal_transition"
    assert job["path"].read_bytes() == before
    rejected = [e for e in _events(workspace) if e["type"] == "job.transition_rejected"]
    assert len(rejected) == 1
    assert rejected[0]["reason"].startswith("illegal_transition")
    assert (rejected[0]["prev_state"], rejected[0]["new_state"]) == ("DISCOVERED", "OFFER")


def test_applied_needs_a_recorded_approval(workspace: Path) -> None:
    job = add_job(workspace, "acme", status=State.APPROVAL_REQUIRED, bullet="approval-required")
    with pytest.raises(sm.TransitionError, match="careeros approve") as exc:
        sm.apply_transition(workspace, job["id"], State.APPLIED, actor="agent:claude")
    assert exc.value.code == "approval_required"
    sm.approve_job(workspace, job["id"], confirm=lambda prompt: True)
    assert sm.apply_transition(workspace, job["id"], State.APPLIED, actor="user").outcome == "changed"


def test_latest_approval_decision_wins_and_resets_on_reentry(workspace: Path) -> None:
    job = add_job(workspace, "acme", status=State.APPROVAL_REQUIRED, bullet="approval-required")
    assert sm.approve_job(workspace, job["id"], confirm=lambda p: False)["outcome"] == "denied"
    with pytest.raises(sm.TransitionError):
        sm.apply_transition(workspace, job["id"], State.APPLIED, actor="user")
    assert sm.approve_job(workspace, job["id"], confirm=lambda p: True)["outcome"] == "approved"
    sm.apply_transition(workspace, job["id"], State.READY_TO_APPLY, actor="user", force=True, reason="needs another look")
    sm.apply_transition(workspace, job["id"], State.APPROVAL_REQUIRED, actor="user")
    assert sm.approval_status(_events(workspace), job["id"]) is None
    with pytest.raises(sm.TransitionError):
        sm.apply_transition(workspace, job["id"], State.APPLIED, actor="user")


def test_approve_is_idempotent_and_records_user_actor(workspace: Path) -> None:
    job = add_job(workspace, "acme", status=State.APPROVAL_REQUIRED, bullet="approval-required")
    first = sm.approve_job(workspace, job["id"], confirm=lambda p: True)
    event = first["event"]
    assert (event["type"], event["actor"], event["approval"], event["source"]) == (
        "application.approved", "user", "approved", "tty",
    )
    count = len(_events(workspace))

    def must_not_ask(prompt: str) -> bool:
        raise AssertionError("already approved; the user must not be asked again")

    again = sm.approve_job(workspace, job["id"], confirm=must_not_ask)
    assert again["outcome"] == "unchanged"
    assert len(_events(workspace)) == count


def test_repeated_denial_logs_once(workspace: Path) -> None:
    job = add_job(workspace, "acme", status=State.APPROVAL_REQUIRED, bullet="approval-required")
    assert sm.approve_job(workspace, job["id"], confirm=lambda p: False)["outcome"] == "denied"
    assert sm.approve_job(workspace, job["id"], confirm=lambda p: False)["outcome"] == "unchanged"
    assert [e["type"] for e in _events(workspace)].count("application.approval_denied") == 1


def test_approve_requires_the_job_to_be_awaiting_approval(workspace: Path) -> None:
    job = add_job(workspace, "acme")
    with pytest.raises(sm.TransitionError) as exc:
        sm.approve_job(workspace, job["id"], confirm=lambda p: True)
    assert exc.value.code == "not_awaiting_approval"


def test_approve_does_not_hold_the_lock_while_the_user_decides_and_detects_changes(workspace: Path) -> None:
    job = add_job(workspace, "acme", status=State.APPROVAL_REQUIRED, bullet="approval-required")

    def withdraw_meanwhile(prompt: str) -> bool:
        # would deadlock or time out if approve held the workspace lock during the prompt
        sm.apply_transition(workspace, job["id"], State.WITHDRAWN, actor="user")
        return True

    with pytest.raises(sm.TransitionError) as exc:
        sm.approve_job(workspace, job["id"], confirm=withdraw_meanwhile)
    assert exc.value.code == "not_awaiting_approval"


def test_force_requires_a_reason(workspace: Path) -> None:
    job = add_job(workspace, "acme", status=State.SCREEN, bullet="interview")
    with pytest.raises(sm.TransitionError) as exc:
        sm.apply_transition(workspace, job["id"], State.EVALUATED, actor="user", force=True)
    assert exc.value.code == "reason_required"


def test_force_records_a_correction_not_a_normal_change(workspace: Path) -> None:
    job = add_job(workspace, "acme", status=State.SCREEN, bullet="interview")
    result = sm.apply_transition(workspace, job["id"], State.EVALUATED, actor="user", force=True, reason="entered by mistake")
    assert result.outcome == "corrected"
    event = _events(workspace)[-1]
    assert (event["type"], event["prev_state"], event["new_state"], event["actor"], event["reason"]) == (
        "job.status_corrected", "SCREEN", "EVALUATED", "user", "entered by mistake",
    )
    assert ws.read_job(job["path"]).status is State.EVALUATED


def test_force_on_an_already_legal_move_is_a_normal_transition(workspace: Path) -> None:
    job = add_job(workspace, "acme")
    result = sm.apply_transition(workspace, job["id"], State.EVALUATED, actor="user", force=True, reason="n/a")
    assert result.outcome == "changed"
    assert _events(workspace)[-1]["type"] == "job.status_changed"


def test_force_cannot_leave_a_terminal_state(workspace: Path) -> None:
    job = add_job(workspace, "acme", status=State.WITHDRAWN, bullet="closed")
    with pytest.raises(sm.TransitionError) as exc:
        sm.apply_transition(workspace, job["id"], State.PREPARING, actor="user", force=True, reason="changed my mind")
    assert exc.value.code == "terminal"


def test_force_into_applied_still_needs_approval(workspace: Path) -> None:
    job = add_job(workspace, "acme", status=State.PREPARING, bullet="preparing")
    with pytest.raises(sm.TransitionError) as exc:
        sm.apply_transition(workspace, job["id"], State.APPLIED, actor="user", force=True, reason="applied by email")
    assert exc.value.code == "approval_required"


def test_failed_ledger_append_leaves_job_and_pipeline_byte_identical(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    job = add_job(workspace, "acme", status=State.APPLIED, bullet="applied")  # pipeline line shows "[ ]", so SCREEN rewrites it
    pipeline = workspace / "jobs" / "pipeline.md"
    before = (job["path"].read_bytes(), pipeline.read_bytes())

    def boom(*args: object, **kwargs: object) -> None:
        raise OSError("ledger disk full")

    monkeypatch.setattr(ledger, "append_event", boom)
    with pytest.raises(OSError, match="ledger disk full"):
        sm.apply_transition(workspace, job["id"], State.SCREEN, actor="user")
    assert (job["path"].read_bytes(), pipeline.read_bytes()) == before
    assert not [p for p in workspace.rglob("*") if p.name.endswith(".tmp")]


def test_failed_restore_saves_the_original_under_recovery(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    job = add_job(workspace, "acme")  # DISCOVERED -> EVALUATED leaves the pipeline icon alone: one file written
    original = job["path"].read_bytes()
    calls = {"n": 0}
    real = sm.atomic_write_bytes

    def flaky(path: Path, data: bytes) -> None:
        calls["n"] += 1
        if calls["n"] >= 2:
            raise OSError("restore failed")
        real(path, data)

    def boom(*args: object, **kwargs: object) -> None:
        raise OSError("ledger disk full")

    monkeypatch.setattr(ledger, "append_event", boom)
    monkeypatch.setattr(sm, "atomic_write_bytes", flaky)
    with pytest.raises(sm.RecoveryError, match="saved at"):
        sm.apply_transition(workspace, job["id"], State.EVALUATED, actor="user")
    saved = list((workspace / ".careeros" / "recovery").iterdir())
    assert len(saved) == 1 and saved[0].read_bytes() == original


def test_workspace_newer_than_installed_is_refused_without_writing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, clock
) -> None:
    root = make_workspace(tmp_path / "ws", framework_version="9.0.0")
    job = add_job(root, "acme")
    before = job["path"].read_bytes()
    with pytest.raises(ws.WorkspaceError, match="newer than the installed"):
        sm.apply_transition(root, job["id"], State.EVALUATED, actor="user")
    assert job["path"].read_bytes() == before


def test_archive_flags_the_job_blocks_transitions_and_is_idempotent(workspace: Path) -> None:
    job = add_job(workspace, "acme")
    first = sm.archive_job(workspace, job["id"], actor="user", reason="not interested")
    assert first["outcome"] == "archived"
    reread = ws.read_job(job["path"])
    assert reread.archived and reread.archived_at
    assert _events(workspace)[-1]["type"] == "job.archived"
    assert _events(workspace)[-1]["reason"] == "not interested"
    count = len(_events(workspace))
    assert sm.archive_job(workspace, job["id"], actor="user")["outcome"] == "unchanged"
    assert len(_events(workspace)) == count
    with pytest.raises(sm.TransitionError) as exc:
        sm.apply_transition(workspace, job["id"], State.EVALUATED, actor="user")
    assert exc.value.code == "archived"
    undone = sm.archive_job(workspace, job["id"], actor="user", undo=True)
    assert undone["outcome"] == "unarchived"
    again = ws.read_job(job["path"])
    assert not again.archived and "archived_at" not in again.frontmatter
    assert sm.apply_transition(workspace, job["id"], State.EVALUATED, actor="user").outcome == "changed"


def test_archive_keeps_directory_and_body_in_place(workspace: Path) -> None:
    job = add_job(workspace, "acme")
    body_before = ws.read_job(job["path"]).body
    sm.archive_job(workspace, job["id"], actor="user")
    assert job["path"].exists()
    assert ws.read_job(job["path"]).body == body_before


def _hand_edit(job: dict, status: State) -> bytes:
    frontmatter, body = ws.split_frontmatter(job["path"].read_text(encoding="utf-8"))
    frontmatter["status"] = status.value
    job["path"].write_text(ws.join_frontmatter(frontmatter, body), encoding="utf-8")
    return job["path"].read_bytes()


def test_repair_into_applied_without_approval_is_rejected(workspace: Path) -> None:
    job = add_job(workspace, "acme", status=State.APPROVAL_REQUIRED, bullet="approval-required")
    _hand_edit(job, State.APPLIED)
    count = len(_events(workspace))
    with pytest.raises(sm.TransitionError) as exc:
        sm.apply_transition(workspace, job["id"], State.APPLIED, actor="user")
    assert exc.value.code == "approval_required"
    new = _events(workspace)[count:]
    assert [e["type"] for e in new] == ["job.transition_rejected"]


def test_repair_into_applied_with_approval_is_repaired(workspace: Path) -> None:
    job = add_job(workspace, "acme", status=State.APPROVAL_REQUIRED, bullet="approval-required")
    sm.approve_job(workspace, job["id"], confirm=lambda p: True)
    _hand_edit(job, State.APPLIED)
    assert sm.apply_transition(workspace, job["id"], State.APPLIED, actor="user").outcome == "repaired"


def test_repair_cannot_leave_a_terminal_state(workspace: Path) -> None:
    job = add_job(workspace, "acme", status=State.WITHDRAWN, bullet="closed")
    after_edit = _hand_edit(job, State.PREPARING)
    with pytest.raises(sm.TransitionError) as exc:
        sm.apply_transition(workspace, job["id"], State.PREPARING, actor="user")
    assert exc.value.code == "terminal"
    assert job["path"].read_bytes() == after_edit


def test_repair_of_an_illegal_unrecorded_move_needs_force_and_reason(workspace: Path) -> None:
    job = add_job(workspace, "acme", status=State.SCREEN, bullet="interview")
    _hand_edit(job, State.DISCOVERED)
    for kwargs in ({}, {"force": True}):
        with pytest.raises(sm.TransitionError) as exc:
            sm.apply_transition(workspace, job["id"], State.DISCOVERED, actor="user", **kwargs)
        assert exc.value.code == "unrecorded_change"
    assert "--force --reason" in str(exc.value)
    result = sm.apply_transition(
        workspace, job["id"], State.DISCOVERED, actor="user", force=True, reason="edited by hand"
    )
    assert result.outcome == "corrected"
    event = _events(workspace)[-1]
    assert (event["type"], event["source"], event["reason"], event["prev_state"], event["new_state"]) == (
        "job.status_corrected", "recovery", "edited by hand", "SCREEN", "DISCOVERED",
    )


def _crash_before_pipeline_write(workspace: Path):
    job = add_job(workspace, "acme", status=State.APPLIED, bullet="applied")
    _hand_edit(job, State.SCREEN)  # job file written, pipeline icon and ledger left behind
    return job, workspace / "jobs" / "pipeline.md"


def test_retry_after_crash_before_pipeline_write_fixes_ledger_and_icon(workspace: Path) -> None:
    job, pipeline = _crash_before_pipeline_write(workspace)
    assert sm.apply_transition(workspace, job["id"], State.SCREEN, actor="user").outcome == "repaired"
    assert pipeline.read_text(encoding="utf-8").count("- [?] **Acme**") == 1
    count = len(_events(workspace))
    assert sm.apply_transition(workspace, job["id"], State.SCREEN, actor="user").outcome == "unchanged"
    assert len(_events(workspace)) == count


def test_stale_pipeline_with_consistent_ledger_is_resynced_without_an_event(workspace: Path) -> None:
    job = add_job(workspace, "acme", status=State.APPLIED, bullet="applied")
    sm.apply_transition(workspace, job["id"], State.SCREEN, actor="user")
    pipeline = workspace / "jobs" / "pipeline.md"
    pipeline.write_text(pipeline.read_text(encoding="utf-8").replace("- [?]", "- [ ]"), encoding="utf-8")
    count = len(_events(workspace))
    assert sm.apply_transition(workspace, job["id"], State.SCREEN, actor="user").outcome == "unchanged"
    assert pipeline.read_text(encoding="utf-8").count("- [?] **Acme**") == 1
    assert len(_events(workspace)) == count


def test_failed_ledger_append_on_repair_restores_the_pipeline(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    job, pipeline = _crash_before_pipeline_write(workspace)
    before = (job["path"].read_bytes(), pipeline.read_bytes())

    def boom(*args: object, **kwargs: object) -> None:
        raise OSError("ledger disk full")

    monkeypatch.setattr(ledger, "append_event", boom)
    with pytest.raises(OSError, match="ledger disk full"):
        sm.apply_transition(workspace, job["id"], State.SCREEN, actor="user")
    assert (job["path"].read_bytes(), pipeline.read_bytes()) == before


# --- a transition must not extend history the ledger does not agree with ------------------

def _hand_edit_status(job: dict, status: State) -> None:
    """Change the job file's status without going through careeros, as a person (or an agent) might."""
    frontmatter, body = ws.split_frontmatter(job["path"].read_text(encoding="utf-8"))
    frontmatter["status"] = status.value
    job["path"].write_text(ws.join_frontmatter(frontmatter, body), encoding="utf-8")


def _status_events(root: Path) -> list[dict]:
    return [e for e in _events(root) if e["type"] in ("job.status_changed", "job.status_corrected")]


def test_a_transition_does_not_silently_extend_history_after_a_hand_edit(workspace: Path) -> None:
    job = add_job(workspace, "acme")  # ledger: DISCOVERED
    _hand_edit_status(job, State.EVALUATED)  # file: EVALUATED, ledger still DISCOVERED
    before = job["path"].read_bytes()
    with pytest.raises(sm.TransitionError, match="--to EVALUATED") as exc:
        sm.apply_transition(workspace, job["id"], State.SHORTLISTED, actor="agent:claude")
    assert exc.value.code == "history_mismatch"
    assert job["path"].read_bytes() == before
    assert _events(workspace)[-1]["type"] == "job.transition_rejected"
    assert _status_events(workspace) == []  # nothing was added to the history

    # the supported recovery: record what the file shows, then carry on
    assert sm.apply_transition(workspace, job["id"], State.EVALUATED, actor="user").outcome == "repaired"
    assert sm.apply_transition(workspace, job["id"], State.SHORTLISTED, actor="user").outcome == "changed"
    assert [(e["prev_state"], e["new_state"]) for e in _status_events(workspace)] == [
        ("DISCOVERED", "EVALUATED"), ("EVALUATED", "SHORTLISTED"),
    ]
    assert validate_workspace(workspace) == []


def test_the_mismatch_rejection_is_logged_once_per_identical_attempt(workspace: Path) -> None:
    job = add_job(workspace, "acme")
    _hand_edit_status(job, State.EVALUATED)
    for _ in range(2):
        with pytest.raises(sm.TransitionError):
            sm.apply_transition(workspace, job["id"], State.SHORTLISTED, actor="agent:claude")
    assert [e["type"] for e in _events(workspace)].count("job.transition_rejected") == 1


def test_force_does_not_skip_reconciling_a_mismatched_history(workspace: Path) -> None:
    job = add_job(workspace, "acme")
    _hand_edit_status(job, State.EVALUATED)
    with pytest.raises(sm.TransitionError) as exc:
        sm.apply_transition(workspace, job["id"], State.OFFER, actor="user", force=True, reason="skip ahead")
    assert exc.value.code == "history_mismatch"
    assert _status_events(workspace) == []


def test_a_hand_edited_applied_cannot_be_extended_to_bypass_approval(workspace: Path) -> None:
    job = add_job(workspace, "acme", status=State.APPROVAL_REQUIRED, bullet="approval-required")
    _hand_edit_status(job, State.APPLIED)  # no approval was ever recorded
    with pytest.raises(sm.TransitionError) as exc:
        sm.apply_transition(workspace, job["id"], State.SCREEN, actor="agent:claude")
    assert exc.value.code == "history_mismatch"
    for force in (False, True):  # the recovery step itself keeps the approval rule
        with pytest.raises(sm.TransitionError) as exc:
            sm.apply_transition(
                workspace, job["id"], State.APPLIED, actor="agent:claude", force=force, reason="x" if force else None
            )
        assert exc.value.code == "approval_required"
    assert _status_events(workspace) == []  # neither APPLIED nor SCREEN ever reached the history

    # supported path: put the file back, get a real approval, then move
    _hand_edit_status(job, State.APPROVAL_REQUIRED)
    sm.approve_job(workspace, job["id"], confirm=lambda prompt: True)
    assert sm.apply_transition(workspace, job["id"], State.APPLIED, actor="user").outcome == "changed"


def test_a_hand_edit_out_of_a_terminal_state_cannot_be_extended(workspace: Path) -> None:
    job = add_job(workspace, "acme", status=State.WITHDRAWN, bullet="closed")
    _hand_edit_status(job, State.PREPARING)
    with pytest.raises(sm.TransitionError) as exc:
        sm.apply_transition(workspace, job["id"], State.READY_TO_APPLY, actor="agent:claude")
    assert exc.value.code == "history_mismatch"
    for force in (False, True):
        with pytest.raises(sm.TransitionError) as exc:
            sm.apply_transition(
                workspace, job["id"], State.PREPARING, actor="user", force=force, reason="x" if force else None
            )
        assert exc.value.code == "terminal"
    assert _status_events(workspace) == []


def test_an_illegal_hand_edit_needs_a_forced_correction_before_the_next_move(workspace: Path) -> None:
    job = add_job(workspace, "acme", status=State.SCREEN, bullet="interview")
    _hand_edit_status(job, State.DISCOVERED)  # SCREEN -> DISCOVERED is not a legal move
    with pytest.raises(sm.TransitionError) as exc:
        sm.apply_transition(workspace, job["id"], State.EVALUATED, actor="user")
    assert exc.value.code == "history_mismatch"
    with pytest.raises(sm.TransitionError) as exc:
        sm.apply_transition(workspace, job["id"], State.DISCOVERED, actor="user")
    assert exc.value.code == "unrecorded_change"
    corrected = sm.apply_transition(
        workspace, job["id"], State.DISCOVERED, actor="user", force=True, reason="status was edited by hand"
    )
    assert corrected.outcome == "corrected"
    event = _events(workspace)[-1]
    assert (event["type"], event["prev_state"], event["new_state"], event["reason"]) == (
        "job.status_corrected", "SCREEN", "DISCOVERED", "status was edited by hand",
    )
    assert sm.apply_transition(workspace, job["id"], State.EVALUATED, actor="user").outcome == "changed"


def test_a_job_without_any_recorded_state_still_transitions(workspace: Path) -> None:
    job = add_job(workspace, "acme", baseline=False)  # no ledger event to compare with
    assert sm.apply_transition(workspace, job["id"], State.EVALUATED, actor="user").outcome == "changed"
