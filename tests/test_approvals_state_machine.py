"""The APPLIED invariant (spec section 7): a job reaches APPLIED only when no application_submit item for it is
unresolved and, if it has any, at least one is consumed. Set semantics: there is no "latest" item. Invented data only."""

from __future__ import annotations

import itertools
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from helpers import add_job, make_workspace

from careeros.core import ids, ledger
from careeros.core import state_machine as sm
from careeros.core import workspace as ws
from careeros.core.approvals import fold, rules, service
from careeros.core.models import State

T0 = "2026-10-06T09:00:00Z"
FMT = "%Y-%m-%dT%H:%M:%SZ"
STATES = ("pending", "approved", "in_flight", "consumed", "declined", "voided", "superseded", "expired")
CHAINS = {
    "pending": [rules.EV_PROPOSED],
    "approved": [rules.EV_PROPOSED, rules.EV_APPROVED],
    "in_flight": [rules.EV_PROPOSED, rules.EV_APPROVED, rules.EV_STARTED],
    "consumed": [rules.EV_PROPOSED, rules.EV_APPROVED, rules.EV_STARTED, rules.EV_CONSUMED],
    "declined": [rules.EV_PROPOSED, rules.EV_DECLINED],
    "voided": [rules.EV_PROPOSED, rules.EV_APPROVED, rules.EV_STARTED, rules.EV_VOIDED],
    "superseded": [rules.EV_PROPOSED, rules.EV_SUPERSEDED],
    "expired": [rules.EV_PROPOSED, rules.EV_APPROVED],      # expired only once the clock has moved a day on
}
UNRESOLVED = {"pending", "approved", "in_flight"}


def shift(ts: str, **delta) -> str:
    return (datetime.strptime(ts, FMT) + timedelta(**delta)).strftime(FMT)


class Clock:
    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> str:
        return self.now


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> Clock:
    fake = Clock()
    monkeypatch.setattr("careeros.core.models.utc_now", fake)
    return fake


@pytest.fixture
def env(tmp_path: Path, clock: Clock):
    root = make_workspace(tmp_path / "ws")
    job = add_job(root, "example-platform", status=State.APPROVAL_REQUIRED, bullet="approval-required")
    return root, job


def item(root, job_id: str, kind: str, *, actor: str = "agent:test") -> str:
    """An item for the job in the given state, written as ledger events only (the gate reads nothing else)."""
    apr = ids.new_id("approval")
    for etype in CHAINS[kind]:
        extra = fold.proposal_event_fields(rules.APPLICATION_SUBMIT, job_id, f"approvals/{apr}.md", "0" * 64) if etype == rules.EV_PROPOSED else {}
        ledger.append_event(root, type=etype, actor=actor, entity=apr, source="cli", action=extra.pop("action", etype), **extra)
    return apr


def make_items(root, clock: Clock, job_id: str, kinds) -> list[str]:
    ids_: list[str] = []
    kinds = list(kinds)
    if "expired" in kinds:                                    # expired items are made first, then the clock moves on
        for kind in kinds:
            if kind == "expired":
                ids_.append(item(root, job_id, kind))
        clock.now = shift(T0, hours=30)
        kinds = [k for k in kinds if k != "expired"]
        ids_ = ids_ + [item(root, job_id, k) for k in kinds]
        return ids_
    return [item(root, job_id, k) for k in kinds]


def approve(root, job) -> None:
    sm.approve_job(root, job["id"], confirm=lambda p: True)


def to_applied(root, job, **kw):
    return sm.apply_transition(root, job["id"], State.APPLIED, actor="user", **kw)


def job_status(root, job) -> str:
    return ws.read_job(job["path"]).status.value


def expected(kinds) -> str | None:
    if set(kinds) & UNRESOLVED:
        return rules.SUBMISSION_UNRESOLVED
    return None if "consumed" in kinds else rules.SUBMISSION_NOT_CONSUMED


GRID = ([(k,) for k in STATES] + list(itertools.permutations(STATES, 2)) + list(itertools.combinations(STATES, 3)))


@pytest.mark.parametrize("kinds", GRID, ids=lambda k: "+".join(k))
def test_set_semantics_grid(env, clock, kinds):
    root, job = env
    make_items(root, clock, job["id"], kinds)
    approve(root, job)
    want = expected(kinds)
    if want is None:
        assert to_applied(root, job).outcome == "changed"
        assert job_status(root, job) == "APPLIED"
    else:
        with pytest.raises(sm.TransitionError) as exc:
            to_applied(root, job)
        assert exc.value.code == want
        assert job_status(root, job) == "APPROVAL_REQUIRED"


def test_a_job_without_submit_items_behaves_as_before(env):
    root, job = env
    approve(root, job)
    assert to_applied(root, job).outcome == "changed"


def test_other_kinds_and_other_jobs_do_not_count(env, clock):
    root, job = env
    other = add_job(root, "other-platform", status=State.APPROVAL_REQUIRED, bullet="approval-required")
    item(root, other["id"], "in_flight")
    apr = ids.new_id("approval")
    extra = fold.proposal_event_fields(rules.LINKEDIN_INVITE, "per_x", "approvals/x.md", "0" * 64, "https://www.linkedin.com/in/alex-example")
    ledger.append_event(root, type=rules.EV_PROPOSED, actor="agent:test", entity=apr, source="cli", action=extra.pop("action"), **extra)
    approve(root, job)
    assert to_applied(root, job).outcome == "changed"


def test_unresolved_precedence_and_message(env, clock):
    root, job = env
    consumed, pending, flight = make_items(root, clock, job["id"], ("consumed", "pending", "in_flight"))
    approve(root, job)
    with pytest.raises(sm.TransitionError) as exc:
        to_applied(root, job)
    assert exc.value.code == rules.SUBMISSION_UNRESOLVED
    msg = str(exc.value)
    assert pending in msg and "pending" in msg and flight in msg and "in_flight" in msg and consumed not in msg
    assert "careeros approval resolve" in msg


def test_message_for_pending_only_has_no_resolve_hint(env, clock):
    root, job = env
    (pending,) = make_items(root, clock, job["id"], ("pending",))
    approve(root, job)
    with pytest.raises(sm.TransitionError) as exc:
        to_applied(root, job)
    assert pending in str(exc.value) and "approval resolve" not in str(exc.value)


def test_rejection_is_recorded_and_deduped(env, clock):
    root, job = env
    make_items(root, clock, job["id"], ("declined",))
    approve(root, job)
    for _ in range(2):
        with pytest.raises(sm.TransitionError):
            to_applied(root, job)
    rejected = [e for e in ledger.read_events(root) if e["type"] == "job.transition_rejected"]
    assert len(rejected) == 1 and rejected[0]["reason"].startswith("submission_not_consumed: ")


def test_a_rejection_never_changes_a_later_gate_result(env, clock):
    root, job = env
    (flight,) = make_items(root, clock, job["id"], ("in_flight",))
    approve(root, job)
    with pytest.raises(sm.TransitionError):
        to_applied(root, job)
    ledger.append_event(root, type=rules.EV_CONSUMED, actor="agent:test", entity=flight, source="cli", action="done")
    assert to_applied(root, job).outcome == "changed"          # the earlier rejection does not block

    root2 = root.parent / "ws2"
    other_root = make_workspace(root2)
    other = add_job(other_root, "second", status=State.APPROVAL_REQUIRED, bullet="approval-required")
    item(other_root, other["id"], "consumed")
    approve(other_root, other)
    # an approval.rejected (audit-only) naming a consumed item changes nothing either
    ledger.append_event(other_root, type=rules.EV_REJECTED, actor="agent:test", entity=ids.new_id("approval"), source="cli", action="rejected", reason="x")
    assert to_applied(other_root, other).outcome == "changed"


def test_repair_path_enforces_the_same_rule(env, clock):
    root, job = env
    (flight,) = make_items(root, clock, job["id"], ("in_flight",))
    approve(root, job)
    frontmatter, body = ws.split_frontmatter(job["path"].read_text(encoding="utf-8"))
    frontmatter["status"] = "APPLIED"
    job["path"].write_text(ws.join_frontmatter(frontmatter, body), encoding="utf-8")
    with pytest.raises(sm.TransitionError) as exc:
        to_applied(root, job)
    assert exc.value.code == rules.SUBMISSION_UNRESOLVED
    ledger.append_event(root, type=rules.EV_CONSUMED, actor="agent:test", entity=flight, source="cli", action="done")
    assert to_applied(root, job).outcome == "repaired"


def test_repair_path_not_consumed(env, clock):
    root, job = env
    make_items(root, clock, job["id"], ("voided",))
    approve(root, job)
    frontmatter, body = ws.split_frontmatter(job["path"].read_text(encoding="utf-8"))
    frontmatter["status"] = "APPLIED"
    job["path"].write_text(ws.join_frontmatter(frontmatter, body), encoding="utf-8")
    with pytest.raises(sm.TransitionError) as exc:
        to_applied(root, job)
    assert exc.value.code == rules.SUBMISSION_NOT_CONSUMED


def test_forced_transition_is_gated_too(env, clock):
    root, job = env
    make_items(root, clock, job["id"], ("pending",))
    approve(root, job)
    with pytest.raises(sm.TransitionError) as exc:
        to_applied(root, job, force=True, reason="applied by email")
    assert exc.value.code == rules.SUBMISSION_UNRESOLVED


def test_approval_required_still_comes_first(env, clock):
    root, job = env
    make_items(root, clock, job["id"], ("pending",))
    with pytest.raises(sm.TransitionError) as exc:
        to_applied(root, job)
    assert exc.value.code == "approval_required"


def test_backward_clock_keeps_an_expired_approval_unresolved(env, clock):
    root, job = env
    (apr,) = make_items(root, clock, job["id"], ("approved",))     # approved at T0, expires at T0 + 24h
    approve(root, job)
    clock.now = shift(T0, hours=30)
    ledger.append_event(root, type="note.added", actor="user", entity=job["id"], source="cli", action="later event")
    clock.now = shift(T0, hours=25)                                # past expiry, but behind the ledger's clock floor
    with pytest.raises(sm.TransitionError) as exc:
        to_applied(root, job)
    assert exc.value.code == rules.SUBMISSION_UNRESOLVED and apr in str(exc.value)
    clock.now = shift(T0, hours=31)                                # a sane clock: expired, so resolved, but not consumed
    with pytest.raises(sm.TransitionError) as exc:
        to_applied(root, job)
    assert exc.value.code == rules.SUBMISSION_NOT_CONSUMED


def test_backward_clock_before_the_approval_stays_unresolved(env, clock):
    root, job = env
    clock.now = shift(T0, hours=2)
    (apr,) = make_items(root, clock, job["id"], ("approved",))
    approve(root, job)
    clock.now = T0
    with pytest.raises(sm.TransitionError) as exc:
        to_applied(root, job)
    assert exc.value.code == rules.SUBMISSION_UNRESOLVED


def test_done_and_resolve_leave_the_job_file_unchanged(env, clock):
    root, job = env
    approve(root, job)
    before = job["path"].read_bytes()
    first, second = make_items(root, clock, job["id"], ("in_flight", "in_flight"))
    service.done(root, first, actor="agent:test", clock=clock)
    assert job["path"].read_bytes() == before
    service.resolve(root, second, sent=True, clock=clock)
    assert job["path"].read_bytes() == before
    assert to_applied(root, job).outcome == "changed"


def test_interrupted_submission_end_to_end(env, clock):
    root, job = env
    folder = job["path"].parent
    (folder / "resume.pdf").write_bytes(b"%PDF resume")
    (folder / "cover.pdf").write_bytes(b"%PDF cover")
    paths = ["jobs/discovered/example-platform/resume.pdf", "jobs/discovered/example-platform/cover.pdf"]
    propose = lambda: service.propose(root, kind=rules.APPLICATION_SUBMIT, person_url=None, job_ref=job["id"], label="Example job",
                                      content_text="Answers\n", manifest_paths=paths, actor="agent:test")
    got = propose()
    service.decide(root, got.id, confirm=lambda t: "approve", clock=clock)
    service.begin(root, got.id, actor="agent:test", clock=clock)            # the external action is then interrupted
    with pytest.raises(service.Refused) as exc:
        propose()
    assert exc.value.code == rules.IN_FLIGHT_CONFLICT
    approve(root, job)
    with pytest.raises(sm.TransitionError) as exc:
        to_applied(root, job)
    assert exc.value.code == rules.SUBMISSION_UNRESOLVED and "careeros approval resolve" in str(exc.value)
    service.resolve(root, got.id, sent=True, clock=clock)
    assert to_applied(root, job).outcome == "changed"
