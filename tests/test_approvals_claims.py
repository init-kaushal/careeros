"""`careeros approve` for items (single and queue), `approval begin|done|resolve`, the rejection events, locking and real
cross-process races. Invented data only."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from helpers import add_job, assert_only_ledger_appended, make_workspace, state
from typer.testing import CliRunner

from careeros.cli import _util
from careeros.cli.main import app
from careeros.core import ids, ledger, models
from careeros.core import state_machine as sm
from careeros.core.approvals import fold, item as item_mod, manifest, rules, service
from careeros.core.approvals.model import Item, Target
from careeros.core.models import State
from careeros.core.validation import validate_workspace as validate_all
from careeros.core.workspace import WorkspaceLock

runner = CliRunner()

URL = "https://www.linkedin.com/in/alex-example"
URL2 = "https://www.linkedin.com/in/sam-example"
URL3 = "https://www.linkedin.com/in/kim-example"
NOW = "2026-10-06T10:00:00Z"        # later than every event the `clock` fixture stamps in these tests
FMT = "%Y-%m-%dT%H:%M:%SZ"


@pytest.fixture
def ws(tmp_path: Path, clock):
    root = make_workspace(tmp_path / "ws")
    job = add_job(root, "example-platform", status=State.APPROVAL_REQUIRED, bullet="approval-required")
    folder = job["path"].parent
    (folder / "resume.pdf").write_bytes(b"%PDF resume")
    (folder / "cover.pdf").write_bytes(b"%PDF cover")
    job["resume"], job["cover"] = "jobs/discovered/example-platform/resume.pdf", "jobs/discovered/example-platform/cover.pdf"
    return root, job


@pytest.fixture
def interactive(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_util, "is_interactive", lambda: True)


def P(root, kind=rules.LINKEDIN_INVITE, *, url=URL, content="Hi Alex, small world.\n", label="Alex Example, Example Corp"):
    return service.propose(root, kind=kind, person_url=url, job_ref=None, label=label, content_text=content,
                           manifest_paths=[], actor="agent:test")


def S(root, job, content="Answers: 5 years of Go.\n"):
    return service.propose(root, kind=rules.APPLICATION_SUBMIT, person_url=None, job_ref=job["id"], label="Example job",
                           content_text=content, manifest_paths=[job["resume"], job["cover"]], actor="agent:test")


def decide(root, apr, choice="approve", now=NOW):
    return service.decide(root, apr, confirm=lambda text: choice, now=now)


def begin(root, apr, now=NOW, actor="agent:test"):
    return service.begin(root, apr, actor=actor, now=now)


def done(root, apr, now=NOW, note=None):
    return service.done(root, apr, actor="agent:test", note=note, now=now)


def folded(root, apr, now=NOW):
    return fold.fold_item(ledger.read_events(root), apr, now)


def current(root, apr, now=NOW):
    return folded(root, apr, now).state.value


def item_events(root, apr):
    return [e for e in ledger.read_events(root) if e.get("entity") == apr]


def rejections(root, apr):
    return [e for e in item_events(root, apr) if e["type"] == rules.EV_REJECTED]


def refused(fn, *args, **kwargs) -> service.Refused:
    with pytest.raises(service.Refused) as exc:
        fn(*args, **kwargs)
    return exc.value


def shift(ts: str, **delta) -> str:
    return (datetime.strptime(ts, FMT) + timedelta(**delta)).strftime(FMT)


def craft(root, kind, target_id, *, url=None, content="crafted twin", entries=(), label="Twin"):
    """A second item created WITHOUT `propose` (a hand-made file plus a hand-appended proposal event on a valid chain),
    the way a raced or hand-edited ledger could hold two live items for one (kind, target)."""
    channel = service.CHANNEL_SUBMIT if kind == rules.APPLICATION_SUBMIT else service.CHANNEL_PERSON
    made = Item(ids.new_id("approval"), kind, Target(target_id, channel, label, url), "2026-10-06T09:00:00Z", content,
                tuple(entries))
    rel = f"approvals/{made.id}.md"
    (root / "approvals").mkdir(exist_ok=True)
    (root / rel).write_bytes(item_mod.to_bytes(made))
    ledger.append_event(root, type=rules.EV_PROPOSED, actor="agent:x", entity=made.id, source="cli", new_state=rules.PENDING,
                        **fold.proposal_event_fields(kind, target_id, rel, item_mod.action_hash(made), url))
    return made.id


def raw_event(root, type_, entity, reason=None):
    ledger.append_event(root, type=type_, actor="user", entity=entity, action=type_, source="tty", reason=reason)


def cli(root, *args, input=None):
    return runner.invoke(app, [*args, "--workspace", str(root)], input=input)


def rewrite_ledger(root, edit):
    path = root / "ledger.jsonl"
    path.write_bytes(edit(path.read_bytes().split(b"\n")[:-1]))


def tamper_first_line(root):
    """Edit the first event (still valid JSON and a valid event): the next line's `prev` no longer matches."""
    def edit(lines):
        assert b'"actor":"system"' in lines[0]
        return b"\n".join([lines[0].replace(b'"actor":"system"', b'"actor":"agent:x"'), *lines[1:]]) + b"\n"
    rewrite_ledger(root, edit)


# --- happy paths -----------------------------------------------------------------------------------------------------------

def test_invite_full_cycle_through_the_service(ws):
    root, _ = ws
    got = P(root)
    decision = decide(root, got.id)
    assert decision.outcome == "approved"
    (approved,) = [e for e in item_events(root, got.id) if e["type"] == rules.EV_APPROVED]
    assert (approved["actor"], approved["source"], approved["approval"]) == ("user", "tty", "approved")
    assert approved["artifacts"] == [got.rel, f"sha256:{got.hash}"]
    assert (approved["prev_state"], approved["new_state"]) == ("pending", "approved")
    assert current(root, got.id) == "approved"
    started = begin(root, got.id)
    assert started.id == got.id and started.hash == got.hash and started.item.content == "Hi Alex, small world.\n"
    assert current(root, got.id) == "in_flight"
    done(root, got.id, note="sent at 10:01")
    assert current(root, got.id) == "consumed"
    types = [e["type"] for e in item_events(root, got.id)]
    assert types == [rules.EV_PROPOSED, rules.EV_APPROVED, rules.EV_STARTED, rules.EV_CONSUMED]
    consumed = item_events(root, got.id)[-1]
    assert consumed["actor"] == "agent:test" and consumed["reason"] == "sent at 10:01"


@pytest.mark.parametrize("kind", [rules.LINKEDIN_INVITE, rules.LINKEDIN_MESSAGE, rules.APPLICATION_SUBMIT])
def test_full_cycle_per_kind_through_the_cli(ws, kind, interactive, monkeypatch):
    root, job = ws
    got = S(root, job) if kind == rules.APPLICATION_SUBMIT else P(root, kind)
    r = cli(root, "approve", got.id, input="a\n")
    assert r.exit_code == 0, r.output
    assert "Approved" in r.output and got.hash[:12] in r.output
    monkeypatch.setattr(_util, "is_interactive", lambda: False)
    r = cli(root, "approval", "begin", got.id, "--json")
    assert r.exit_code == 0, r.output
    out = json.loads(r.stdout)
    assert out["outcome"] == "started" and out["id"] == got.id and out["hash"] == got.hash and out["state"] == "in_flight"
    assert out["content"] == item_mod.parse_bytes((root / got.rel).read_bytes(), got.rel)[0].content
    started = item_events(root, got.id)[-1]
    assert started["type"] == rules.EV_STARTED and started["actor"] == "agent:cli"
    r = cli(root, "approval", "done", got.id, "--note", "confirmation seen", "--json")
    assert r.exit_code == 0, r.output
    assert json.loads(r.stdout)["state"] == "consumed"
    assert current(root, got.id) == "consumed"


def test_begin_text_output_names_the_next_step(ws):
    root, _ = ws
    got = P(root)
    decide(root, got.id)
    r = cli(root, "approval", "begin", got.id)
    assert r.exit_code == 0 and f"careeros approval done {got.id}" in r.output


def test_done_never_touches_the_job_and_writes_only_the_ledger(ws):
    root, job = ws
    got = S(root, job)
    decide(root, got.id)
    before = state(root)
    begin(root, got.id)
    done(root, got.id)
    assert_only_ledger_appended(before, state(root), [rules.EV_STARTED, rules.EV_CONSUMED])
    assert sm.recorded_state(ledger.read_events(root), job["id"]) == "APPROVAL_REQUIRED"


def test_decide_writes_only_the_ledger(ws):
    root, job = ws
    got = S(root, job)
    before = state(root)
    decide(root, got.id)
    assert_only_ledger_appended(before, state(root), [rules.EV_APPROVED, "application.approved"])


# --- wrong-state matrix and rejection dedupe ---------------------------------------------------------------------------------

def test_approve_a_declined_item_is_refused_and_the_rejection_deduped(ws, interactive):
    root, _ = ws
    got = P(root)
    decide(root, got.id, "decline")
    assert current(root, got.id) == "declined"
    for _ in range(2):
        r = cli(root, "approve", got.id, input="a\n")
        assert r.exit_code == 1 and "wrong_state" in r.output and "declined" in r.output
    (rej,) = rejections(root, got.id)
    assert rej["reason"].startswith("wrong_state") and rej["actor"] == "user" and rej["source"] == "tty"
    assert current(root, got.id) == "declined"


def test_begin_a_pending_item(ws):
    root, _ = ws
    got = P(root)
    for _ in range(3):
        r = cli(root, "approval", "begin", got.id, "--json")
        assert r.exit_code == 1 and json.loads(r.stdout)["code"] == "wrong_state" and "pending" in json.loads(r.stdout)["error"]
    (rej,) = rejections(root, got.id)
    assert rej["reason"].startswith("wrong_state:") and rej["actor"] == "agent:cli" and rej["source"] == "cli"
    assert current(root, got.id) == "pending"


def test_done_a_non_started_item(ws):
    root, _ = ws
    got = P(root)
    decide(root, got.id)
    for _ in range(2):
        r = cli(root, "approval", "done", got.id, "--json")
        assert r.exit_code == 1 and json.loads(r.stdout)["code"] == "wrong_state"
    assert len(rejections(root, got.id)) == 1 and current(root, got.id) == "approved"


def test_resolve_an_unstarted_item(ws, interactive):
    root, _ = ws
    got = P(root)
    decide(root, got.id)
    for flag in ("--sent", "--not-sent"):
        r = cli(root, "approval", "resolve", got.id, flag)
        assert r.exit_code == 1 and "wrong_state" in r.output
    assert current(root, got.id) == "approved"
    assert len(rejections(root, got.id)) == 1      # same refusal from the same command: deduped


def test_dedupe_only_skips_an_identical_latest_event(ws):
    root, _ = ws
    got = P(root)
    assert refused(begin, root, got.id).code == "wrong_state"
    assert refused(done, root, got.id).code == "wrong_state"        # different action: recorded
    assert refused(begin, root, got.id).code == "wrong_state"       # the latest event is the done rejection: recorded again
    assert refused(begin, root, got.id).code == "wrong_state"       # identical to the latest: skipped
    assert [e["action"].split()[1] for e in rejections(root, got.id)] == ["begin", "done", "begin"]


def test_unknown_id_appends_nothing(ws, interactive):
    root, _ = ws
    P(root)
    before = state(root)
    for args in (("approval", "begin", "apr_zzzzzzzzzz"), ("approval", "done", "apr_zzzzzzzzzz"),
                 ("approval", "resolve", "apr_zzzzzzzzzz", "--sent"), ("approve", "apr_zzzzzzzzzz")):
        r = cli(root, *args, input="a\n")
        assert r.exit_code == 1 and "not_found" in r.output, (args, r.output)
    assert state(root) == before


def test_rejections_are_audit_only(ws):
    root, _ = ws
    got = P(root)
    refused(begin, root, got.id)
    refused(done, root, got.id)
    assert current(root, got.id) == "pending"
    decide(root, got.id)
    assert begin(root, got.id).id == got.id


# --- interactive-only commands ------------------------------------------------------------------------------------------------

def test_non_interactive_approve_queue_single_and_job(ws):
    root, job = ws
    got = P(root)
    before = state(root)
    r = cli(root, "approve")
    assert r.exit_code == 2 and "! careeros approve" in r.output and "not_interactive" in r.output
    assert state(root) == before
    r = cli(root, "approve", job["id"])
    assert r.exit_code == 2 and "interactive terminal" in r.output and "Ask the user" in r.output
    assert "! careeros approve <job>" in r.output
    assert state(root) == before
    for _ in range(2):
        r = cli(root, "approve", got.id)
        assert r.exit_code == 2 and f"! careeros approve {got.id}" in r.output and "not_interactive" in r.output
    (rej,) = rejections(root, got.id)
    assert rej["reason"].startswith("not_interactive") and rej["actor"] == "agent:cli"
    assert current(root, got.id) == "pending"


def test_non_interactive_resolve(ws):
    root, _ = ws
    got = P(root)
    decide(root, got.id)
    begin(root, got.id)
    for _ in range(2):
        r = cli(root, "approval", "resolve", got.id, "--sent")
        assert r.exit_code == 2 and "not_interactive" in r.output and f"! careeros approval resolve {got.id}" in r.output
    assert len(rejections(root, got.id)) == 1 and current(root, got.id) == "in_flight"
    before = state(root)
    assert cli(root, "approval", "resolve", "apr_zzzzzzzzzz", "--sent").exit_code == 2
    assert state(root) == before


def test_resolve_needs_exactly_one_flag(ws, interactive):
    root, _ = ws
    got = P(root)
    decide(root, got.id)
    begin(root, got.id)
    assert cli(root, "approval", "resolve", got.id).exit_code == 2
    assert cli(root, "approval", "resolve", got.id, "--sent", "--not-sent").exit_code == 2
    assert current(root, got.id) == "in_flight"


def test_job_level_approval_still_works_without_items(ws, interactive):
    root, job = ws
    r = cli(root, "approve", job["id"], input="y\n")
    assert r.exit_code == 0 and "Approved" in r.output
    assert sm.approval_status(ledger.read_events(root), job["id"]) == "approved"


# --- the queue ------------------------------------------------------------------------------------------------------------

def test_queue_approves_skips_and_declines(ws, interactive):
    root, _ = ws
    a = P(root)
    b = P(root, rules.LINKEDIN_MESSAGE, url=URL2, content="Thanks Sam")
    c = P(root, url=URL3, content="Hi Kim")
    r = cli(root, "approve", input="a\ns\nd\n")
    assert r.exit_code == 0, r.output
    assert [current(root, x.id) for x in (a, b, c)] == ["approved", "pending", "declined"]
    for x in (a, b, c):
        assert x.id in r.output and x.hash[:12] in r.output
    assert "Thanks Sam" in r.output and "1 approved, 1 declined, 1 skipped" in r.output


def test_queue_never_approves_without_an_explicit_answer(ws, interactive):
    root, _ = ws
    a = P(root)
    r = cli(root, "approve", input="\nyes please\nx\n")       # blank and unclear answers re-prompt; then EOF
    assert r.exit_code == 1
    assert current(root, a.id) == "pending" and not rejections(root, a.id)
    r = cli(root, "approve", input="")                        # EOF straight away
    assert r.exit_code == 1 and current(root, a.id) == "pending"
    assert [e["type"] for e in item_events(root, a.id)] == [rules.EV_PROPOSED]


def test_queue_with_nothing_pending(ws, interactive):
    root, _ = ws
    r = cli(root, "approve")
    assert r.exit_code == 0 and "nothing to approve" in r.output


def test_queue_detects_a_draft_changed_between_display_and_confirm(ws):
    root, _ = ws
    a = P(root)
    b = P(root, url=URL2, content="Hi Sam")
    path = root / a.rel

    def prompt(text):
        if a.id in text:
            path.write_bytes(path.read_bytes().replace(b"small world", b"SMALL WORLD"))
        return "approve"

    results = service.queue(root, prompt=prompt, now=NOW)
    assert [(x.id, x.outcome, x.code) for x in results] == [(a.id, "refused", "hash_mismatch"), (b.id, "approved", None)]
    assert current(root, a.id) == "pending" and current(root, b.id) == "approved"
    assert rejections(root, a.id)[0]["reason"].startswith("hash_mismatch")


def test_queue_reports_a_state_change_during_the_decision(ws):
    root, _ = ws
    a = P(root)

    def prompt(text):
        P(root, content="a newer draft")        # supersedes `a` while the user is deciding (the lock is not held)
        return "approve"

    (result,) = [x for x in service.queue(root, prompt=prompt, now=NOW) if x.id == a.id]
    assert result.outcome == "refused" and result.code == "wrong_state" and "superseded" in result.message
    assert current(root, a.id) == "superseded"


def test_queue_skips_items_that_left_pending_before_their_turn(ws):
    root, _ = ws
    a = P(root)
    b = P(root, url=URL2, content="Hi Sam")

    def prompt(text):
        if a.id in text:
            P(root, url=URL2, content="Hi Sam, newer")       # supersedes b before it is shown
        return "approve"

    results = service.queue(root, prompt=prompt, now=NOW)
    assert [(x.id, x.outcome) for x in results] == [(a.id, "approved"), (b.id, "changed")]
    assert not rejections(root, b.id)


def test_queue_shows_terminal_escapes_escaped(ws, interactive):
    root, _ = ws
    a = P(root, content="Hi \x1b[31mred\x1b[0m ‮evil")
    r = cli(root, "approve", input="s\n")
    assert r.exit_code == 0
    assert "\x1b" not in r.output and "‮" not in r.output
    assert "\\x1b[31mred" in r.output and "\\u202e" in r.output
    show = cli(root, "approval", "show", a.id)
    assert "\x1b" not in show.output and "\\x1b[31mred" in show.output


def test_single_item_decision_does_not_hold_the_lock_while_asking(ws):
    root, _ = ws
    a = P(root)
    took = []

    def confirm(text):
        # another thread opens its own lock file descriptor, so it contends with this one exactly like another process
        def other():
            with WorkspaceLock(root, timeout=1.0):
                took.append(True)
        t = threading.Thread(target=other)
        t.start()
        t.join()
        return "approve"

    assert service.decide(root, a.id, confirm=confirm, now=NOW).outcome == "approved"
    assert took == [True]


def test_single_decision_detects_a_changed_draft(ws):
    root, _ = ws
    a = P(root)
    path = root / a.rel

    def confirm(text):
        assert a.hash[:12] in text and "small world" in text
        path.write_bytes(path.read_bytes().replace(b"small world", b"big world"))
        return "approve"

    exc = refused(service.decide, root, a.id, confirm=confirm, now=NOW)
    assert exc.code == "hash_mismatch" and current(root, a.id) == "pending"


def test_decide_refuses_an_already_changed_draft_before_asking(ws):
    root, _ = ws
    a = P(root)
    (root / a.rel).write_bytes((root / a.rel).read_bytes() + b"P.S.")

    def confirm(text):
        raise AssertionError("must not ask about a changed draft")

    assert refused(service.decide, root, a.id, confirm=confirm, now=NOW).code == "hash_mismatch"


def test_skip_records_nothing(ws):
    root, _ = ws
    a = P(root)
    before = state(root)
    assert decide(root, a.id, "skip").outcome == "skipped"
    assert state(root) == before


def test_decide_refuses_non_pending_states(ws):
    root, _ = ws
    a = P(root)
    decide(root, a.id)
    exc = refused(decide, root, a.id)
    assert exc.code == "wrong_state" and "approved" in str(exc)
    begin(root, a.id)
    assert refused(decide, root, a.id, "decline").code == "wrong_state"


# --- submit pairing --------------------------------------------------------------------------------------------------------------

def test_approving_a_submit_pairs_the_job_approval_in_one_batch(ws, monkeypatch):
    root, job = ws
    got = S(root, job)
    calls = []
    real = ledger.append_events
    monkeypatch.setattr(ledger, "append_events", lambda r, specs: calls.append([s["type"] for s in specs]) or real(r, specs))
    decide(root, got.id)
    assert calls == [[rules.EV_APPROVED, "application.approved"]]
    last2 = ledger.read_events(root)[-2:]
    assert [e["type"] for e in last2] == [rules.EV_APPROVED, "application.approved"]
    assert last2[1]["seq"] == last2[0]["seq"] + 1 and last2[1]["prev"] != last2[0]["prev"]
    assert (last2[1]["entity"], last2[1]["actor"], last2[1]["source"], last2[1]["approval"]) == (job["id"], "user", "tty", "approved")
    assert sm.approval_status(ledger.read_events(root), job["id"]) == "approved"


def test_declining_a_submit_appends_the_denial(ws):
    root, job = ws
    got = S(root, job)
    decide(root, got.id, "decline")
    last2 = ledger.read_events(root)[-2:]
    assert [e["type"] for e in last2] == [rules.EV_DECLINED, "application.approval_denied"]
    assert last2[0]["approval"] == "denied" and last2[1]["entity"] == job["id"]
    assert sm.approval_status(ledger.read_events(root), job["id"]) == "denied"


@pytest.mark.parametrize("how", ["status", "archived"])
def test_deciding_a_submit_needs_the_job_awaiting_approval(ws, how):
    root, job = ws
    got = S(root, job)
    if how == "status":
        sm.apply_transition(root, job["id"], State.WITHDRAWN, actor="user")
    else:
        sm.archive_job(root, job["id"], actor="user")
    for choice in ("approve", "decline"):
        assert refused(decide, root, got.id, choice).code == "job_not_awaiting_approval"
    assert current(root, got.id) == "pending"


def test_submit_job_change_during_the_decision_is_reported(ws):
    root, job = ws
    got = S(root, job)

    def confirm(text):
        sm.apply_transition(root, job["id"], State.WITHDRAWN, actor="user")
        return "approve"

    assert refused(service.decide, root, got.id, confirm=confirm, now=NOW).code == "job_not_awaiting_approval"
    assert current(root, got.id) == "pending"


def test_begin_submit_needs_the_job_approved_and_awaiting(ws):
    root, job = ws
    got = S(root, job)
    decide(root, got.id)
    raw_event(root, "application.approval_denied", job["id"])        # the user later declined at the job level
    assert refused(begin, root, got.id).code == "job_not_awaiting_approval"
    raw_event(root, "application.approved", job["id"])
    sm.archive_job(root, job["id"], actor="user")
    assert refused(begin, root, got.id).code == "job_not_awaiting_approval"
    sm.archive_job(root, job["id"], actor="user", undo=True)
    assert begin(root, got.id).id == got.id


# --- begin revalidation ------------------------------------------------------------------------------------------------------

def test_begin_refuses_an_edited_item_file(ws):
    root, _ = ws
    got = P(root)
    decide(root, got.id)
    (root / got.rel).write_bytes((root / got.rel).read_bytes().replace(b"small world", b"small world!"))
    assert refused(begin, root, got.id).code == "hash_mismatch"
    assert current(root, got.id) == "approved"


def test_begin_refuses_an_unparseable_or_retargeted_file(ws):
    root, _ = ws
    got = P(root)
    decide(root, got.id)
    original = (root / got.rel).read_bytes()
    (root / got.rel).write_bytes(b"not an item")
    assert refused(begin, root, got.id).code == "hash_mismatch"
    parsed, _ = item_mod.parse_bytes(original, got.rel)
    other = Item(parsed.id, parsed.kind, Target(ids.new_id("person"), parsed.target.channel, parsed.target.label, URL2),
                 parsed.created_at, parsed.content)
    (root / got.rel).write_bytes(item_mod.to_bytes(other))
    assert refused(begin, root, got.id).code == "hash_mismatch"


def test_begin_refuses_a_missing_file(ws):
    root, _ = ws
    got = P(root)
    decide(root, got.id)
    (root / got.rel).unlink()
    assert refused(begin, root, got.id).code == "content_missing"


@pytest.mark.parametrize("how,code", [("changed", "manifest_changed"), ("missing", "manifest_invalid"),
                                      ("symlink_outside", "manifest_invalid"), ("symlink_inside", "manifest_changed")])
def test_begin_rehashes_the_manifest(ws, tmp_path, how, code):
    root, job = ws
    got = S(root, job)
    decide(root, got.id)
    resume = root / job["resume"]
    if how == "changed":
        resume.write_bytes(b"%PDF resume v2")
    elif how == "missing":
        resume.unlink()
    elif how == "symlink_outside":
        outside = tmp_path / "outside.pdf"
        outside.write_bytes(b"%PDF resume")
        resume.unlink()
        resume.symlink_to(outside)
    else:
        resume.unlink()
        resume.symlink_to(root / job["cover"])
    assert refused(begin, root, got.id).code == code
    assert current(root, got.id) == "approved"


def test_expiry_boundary(ws):
    root, _ = ws
    got = P(root)
    decide(root, got.id)
    approved_at = folded(root, got.id).approved_at
    assert refused(begin, root, got.id, now=shift(approved_at, hours=24)).code == "expired"
    assert begin(root, got.id, now=shift(approved_at, hours=23, minutes=59, seconds=59)).id == got.id


def test_clock_skew_before_the_approval(ws):
    root, _ = ws
    got = P(root)
    decide(root, got.id)
    approved_at = folded(root, got.id).approved_at
    exc = refused(begin, root, got.id, now=shift(approved_at, seconds=-1))
    assert exc.code == "clock_skew" and current(root, got.id) == "approved"


def test_a_backward_clock_never_bypasses_another_check(ws):
    root, job = ws
    got = S(root, job)
    decide(root, got.id)
    approved_at = folded(root, got.id).approved_at
    (root / job["resume"]).write_bytes(b"%PDF changed")
    for now in (shift(approved_at, seconds=-1), shift(approved_at, days=-30), approved_at, shift(approved_at, hours=1)):
        exc = refused(begin, root, got.id, now=now)
        assert exc.code in ("clock_skew", "manifest_changed")
    assert current(root, got.id) == "approved"
    # an expired item stays refused when the clock moves backwards below the approval
    msg = P(root, content="other")
    decide(root, msg.id)
    t = folded(root, msg.id).approved_at
    assert refused(begin, root, msg.id, now=shift(t, hours=25)).code == "expired"
    assert refused(begin, root, msg.id, now=shift(t, hours=-1)).code == "clock_skew"


def test_clock_skew_against_the_latest_item_event(ws):
    root, _ = ws
    got = P(root)
    decide(root, got.id)
    begin(root, got.id)
    started_at = folded(root, got.id).started_at
    assert refused(done, root, got.id, now=shift(started_at, seconds=-1)).code == "clock_skew"


def test_in_flight_conflict_for_a_crafted_twin(ws):
    root, _ = ws
    a = P(root)
    person = folded(root, a.id).target_id
    twin = craft(root, rules.LINKEDIN_INVITE, person, url=URL)
    decide(root, a.id), decide(root, twin)
    begin(root, a.id)
    assert refused(begin, root, twin).code == "in_flight_conflict"
    done(root, a.id)
    assert refused(begin, root, twin).code == "invite_conflict"


def test_already_submitted_for_a_crafted_twin(ws):
    root, job = ws
    a = S(root, job)
    twin = craft(root, rules.APPLICATION_SUBMIT, job["id"], entries=manifest.hash_files(root, [job["resume"]]))
    decide(root, a.id), decide(root, twin)
    begin(root, a.id)
    assert refused(begin, root, twin).code == "in_flight_conflict"
    done(root, a.id)
    assert refused(begin, root, twin).code == "already_submitted"


def test_messages_may_repeat_after_consumption(ws):
    root, _ = ws
    a = P(root, rules.LINKEDIN_MESSAGE)
    decide(root, a.id), begin(root, a.id), done(root, a.id)
    b = P(root, rules.LINKEDIN_MESSAGE, content="Following up")
    decide(root, b.id)
    assert begin(root, b.id).id == b.id


def test_illegal_history_is_refused(ws):
    root, _ = ws
    got = P(root)
    decide(root, got.id)
    raw_event(root, rules.EV_CONSUMED, got.id)        # consumed without started: APR005, not applied by the fold
    assert refused(begin, root, got.id).code == "wrong_state"
    assert current(root, got.id) == "approved"


# --- crash scenarios and recovery ---------------------------------------------------------------------------------------------

def test_begin_then_the_process_dies(ws, interactive):
    root, _ = ws
    got = P(root)
    decide(root, got.id)
    begin(root, got.id)           # ... the external action is interrupted and `done` never runs
    assert current(root, got.id) == "in_flight"
    assert refused(begin, root, got.id).code == "wrong_state"
    r = cli(root, "approval", "list", "--json")
    assert [i["state"] for i in json.loads(r.stdout)["items"]] == ["in_flight"]
    r = cli(root, "approval", "list")
    assert "in_flight" in r.output and f"careeros approval resolve {got.id}" in r.output
    assert "APR008" in {i.code for i in validate_all(root, NOW)}
    r = cli(root, "approval", "resolve", got.id, "--sent")
    assert r.exit_code == 0, r.output
    assert current(root, got.id) == "consumed"
    assert "APR008" not in {i.code for i in validate_all(root, NOW)}


def test_resolve_sent_records_the_user(ws):
    root, _ = ws
    got = P(root)
    decide(root, got.id), begin(root, got.id)
    result = service.resolve(root, got.id, sent=True, now=NOW)
    assert result.outcome == "consumed"
    last = item_events(root, got.id)[-1]
    assert (last["type"], last["actor"], last["source"]) == (rules.EV_CONSUMED, "user", "tty")


def test_resolve_not_sent_releases_with_the_original_approval_time(ws, interactive):
    root, _ = ws
    got = P(root)
    decide(root, got.id), begin(root, got.id)
    approved_at = folded(root, got.id).approved_at
    r = cli(root, "approval", "resolve", got.id, "--not-sent")
    assert r.exit_code == 0 and "released" in r.output
    after = folded(root, got.id)
    assert after.state.value == "approved" and after.approved_at == approved_at
    last = item_events(root, got.id)[-1]
    assert (last["type"], last["actor"], last["source"]) == (rules.EV_RELEASED, "user", "tty")
    assert begin(root, got.id).id == got.id         # usable again (still unexpired)


def test_resolve_not_sent_after_expiry_releases_into_expired(ws):
    root, _ = ws
    got = P(root)
    decide(root, got.id), begin(root, got.id)
    late = shift(folded(root, got.id).approved_at, hours=24)
    result = service.resolve(root, got.id, sent=False, now=late)
    assert result.outcome == "expired"
    assert item_events(root, got.id)[-1]["type"] == rules.EV_RELEASED
    assert current(root, got.id, now=late) == "expired"


@pytest.mark.parametrize("failure,code", [("hash", "hash_mismatch"), ("missing", "content_missing"),
                                          ("manifest", "manifest_changed"), ("manifest_gone", "manifest_invalid")])
def test_resolve_not_sent_voids_on_a_failed_revalidation(ws, failure, code):
    root, job = ws
    got = S(root, job)
    decide(root, got.id), begin(root, got.id)
    if failure == "hash":
        (root / got.rel).write_bytes((root / got.rel).read_bytes() + b"x")
    elif failure == "missing":
        (root / got.rel).unlink()
    elif failure == "manifest":
        (root / job["resume"]).write_bytes(b"%PDF other")
    else:
        (root / job["cover"]).unlink()
    late = shift(folded(root, got.id).approved_at, hours=30)      # expiry also failed: any other failure still voids
    result = service.resolve(root, got.id, sent=False, now=late if failure == "hash" else NOW)
    assert result.outcome == "voided" and result.reason.startswith(code)
    last = item_events(root, got.id)[-1]
    assert last["type"] == rules.EV_VOIDED and last["reason"].startswith(code)
    assert current(root, got.id) == "voided"


def test_resolve_not_sent_voids_on_invite_uniqueness(ws):
    root, _ = ws
    a = P(root)
    decide(root, a.id), begin(root, a.id)
    person = folded(root, a.id).target_id
    twin = craft(root, rules.LINKEDIN_INVITE, person, url=URL)
    for t in (rules.EV_APPROVED, rules.EV_STARTED, rules.EV_CONSUMED):      # a raced/hand-made twin that was sent
        raw_event(root, t, twin)
    result = service.resolve(root, a.id, sent=False, now=NOW)
    assert result.outcome == "voided" and result.reason.startswith("invite_conflict")


def test_resolve_refuses_non_in_flight(ws):
    root, _ = ws
    a = P(root)
    for sent in (True, False):
        assert refused(service.resolve, root, a.id, sent=sent, now=NOW).code == "wrong_state"
    decide(root, a.id), begin(root, a.id), done(root, a.id)
    assert refused(service.resolve, root, a.id, sent=True, now=NOW).code == "wrong_state"


# --- ledger failure injection ----------------------------------------------------------------------------------------------------

def _boom(*args, **kwargs):
    raise OSError(28, "No space left on device")


@pytest.mark.parametrize("op", ["approve", "decline", "submit_approve", "begin", "done", "resolve_sent", "resolve_not_sent"])
def test_a_failing_append_changes_nothing_and_the_next_run_succeeds(ws, monkeypatch, op):
    root, job = ws
    got = S(root, job) if op == "submit_approve" else P(root)
    if op in ("begin", "done", "resolve_sent", "resolve_not_sent"):
        decide(root, got.id)
    if op in ("done", "resolve_sent", "resolve_not_sent"):
        begin(root, got.id)
    run = {
        "approve": lambda: decide(root, got.id),
        "decline": lambda: decide(root, got.id, "decline"),
        "submit_approve": lambda: decide(root, got.id),
        "begin": lambda: begin(root, got.id),
        "done": lambda: done(root, got.id),
        "resolve_sent": lambda: service.resolve(root, got.id, sent=True, now=NOW),
        "resolve_not_sent": lambda: service.resolve(root, got.id, sent=False, now=NOW),
    }[op]
    before = state(root)
    was = current(root, got.id)
    monkeypatch.setattr(ledger, "append_events", _boom)
    with pytest.raises(OSError):
        run()
    assert state(root) == before and current(root, got.id) == was
    monkeypatch.undo()
    run()
    assert current(root, got.id) != was


def test_cli_maps_a_failing_append_to_exit_1(ws, monkeypatch):
    root, _ = ws
    got = P(root)
    decide(root, got.id)
    monkeypatch.setattr(ledger, "append_events", _boom)
    r = cli(root, "approval", "begin", got.id, "--json")
    assert r.exit_code == 1 and "No space left" in json.loads(r.stdout)["error"]


def test_a_failing_rejection_write_still_refuses(ws, monkeypatch):
    root, _ = ws
    got = P(root)
    monkeypatch.setattr(ledger, "append_events", _boom)
    assert refused(begin, root, got.id).code == "wrong_state"


# --- broken chain: fail closed --------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("op", ["decide", "queue", "begin", "done", "resolve"])
def test_every_state_change_fails_closed_on_a_broken_chain(ws, op):
    root, _ = ws
    got = P(root)
    if op in ("begin", "done", "resolve"):
        decide(root, got.id)
    if op in ("done", "resolve"):
        begin(root, got.id)
    tamper_first_line(root)
    before = state(root)

    def never(text):
        raise AssertionError("must not ask on a broken chain")

    run = {
        "decide": lambda: service.decide(root, got.id, confirm=never, now=NOW),
        "queue": lambda: service.queue(root, prompt=never, now=NOW),
        "begin": lambda: begin(root, got.id),
        "done": lambda: done(root, got.id),
        "resolve": lambda: service.resolve(root, got.id, sent=True, now=NOW),
    }[op]
    assert refused(run).code == "ledger_corrupt"
    assert state(root) == before


def test_cli_ledger_corrupt_exits_1(ws):
    root, _ = ws
    got = P(root)
    decide(root, got.id)
    tamper_first_line(root)
    r = cli(root, "approval", "begin", got.id, "--json")
    assert r.exit_code == 1 and json.loads(r.stdout)["code"] == "ledger_corrupt"


# --- the clock is read once per operation, by the CLI -----------------------------------------------------------------------------

def _count_clock_reads(monkeypatch) -> list[str]:
    """Clock reads by anything other than the ledger (which stamps each event's `ts` itself)."""
    reads: list[str] = []
    real = models.utc_now

    def counted():
        caller = sys._getframe(1).f_globals.get("__name__", "")
        if caller != "careeros.core.ledger":
            reads.append(caller)
        return real()

    monkeypatch.setattr(models, "utc_now", counted)
    return reads


def test_service_operations_never_read_the_clock(ws, monkeypatch):
    root, job = ws
    a, b = P(root), S(root, job)
    reads = _count_clock_reads(monkeypatch)
    decide(root, a.id), decide(root, b.id)
    service.queue(root, prompt=lambda text: "skip", now=NOW)
    begin(root, a.id), done(root, a.id)
    begin(root, b.id), service.resolve(root, b.id, sent=False, now=NOW)
    assert reads == []


@pytest.mark.parametrize("args", [("approval", "begin", "{id}"), ("approval", "done", "{id}"),
                                  ("approval", "resolve", "{id}", "--not-sent"), ("approve", "{id}"), ("approve",)])
def test_each_cli_operation_reads_the_clock_once(ws, monkeypatch, interactive, args):
    root, _ = ws
    got = P(root)
    if args[1:2] in (("begin",), ("done",), ("resolve",)):
        decide(root, got.id)
    if args[1:2] in (("done",), ("resolve",)):
        begin(root, got.id)
    reads = _count_clock_reads(monkeypatch)
    r = cli(root, *[a.format(id=got.id) for a in args], input="a\n")
    assert r.exit_code == 0, r.output
    assert reads == ["careeros.cli.approval" if args[0] == "approval" else "careeros.cli.approve"]


# --- real cross-process races --------------------------------------------------------------------------------------------------

CHILD = r"""
import os, sys, time
from careeros.cli.main import app
if os.environ.get("CAREEROS_TEST_WIDEN"):
    # widen the window between reading the ledger and appending, inside the command's critical section
    from careeros.core.approvals import fold
    real = fold.fold_events
    def slow(*a, **k):
        out = real(*a, **k)
        time.sleep(0.3)
        return out
    fold.fold_events = slow
go = sys.argv[1]
while not os.path.exists(go):
    time.sleep(0.001)
sys.argv = ["careeros", *sys.argv[2:]]
app()
"""


def race(tmp_path: Path, commands: list[list[str]], widen: bool) -> list[tuple[int, dict]]:
    """Start one OS process per command, release them together, return (exit code, JSON output) each."""
    script = tmp_path / "child.py"
    script.write_text(CHILD)
    go = tmp_path / f"go-{time.monotonic_ns()}"
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(sys.path)}
    env.pop("CAREEROS_TEST_WIDEN", None)
    if widen:
        env["CAREEROS_TEST_WIDEN"] = "1"
    procs = [subprocess.Popen([sys.executable, str(script), str(go), *cmd], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              env=env, stdin=subprocess.DEVNULL) for cmd in commands]
    time.sleep(1.0)          # every child has imported careeros and is spinning on the barrier
    go.write_text("go")
    out = []
    for p in procs:
        stdout, stderr = p.communicate(timeout=60)
        try:
            data = json.loads(stdout)
        except ValueError:
            data = {"raw": stdout.decode(), "stderr": stderr.decode()}
        out.append((p.returncode, data))
    return out


@pytest.fixture
def real_ws(tmp_path: Path):
    """No `clock` fixture: the children use the real clock, so the parent does too."""
    root = make_workspace(tmp_path / "ws")
    job = add_job(root, "example-platform", status=State.APPROVAL_REQUIRED, bullet="approval-required")
    folder = job["path"].parent
    (folder / "resume.pdf").write_bytes(b"%PDF resume")
    job["resume"] = "jobs/discovered/example-platform/resume.pdf"
    return root, job


def real_decide(root, apr):
    return service.decide(root, apr, confirm=lambda text: "approve", now=models.utc_now())


def started_events(root):
    return [e for e in ledger.read_events(root) if e["type"] == rules.EV_STARTED]


@pytest.mark.parametrize("widen", [False, True])
def test_race_two_begins_on_one_item(real_ws, tmp_path, widen):
    root, _ = real_ws
    got = P(root)
    real_decide(root, got.id)
    results = race(tmp_path, [["approval", "begin", got.id, "--json", "--workspace", str(root)]] * 3, widen)
    assert sorted(code for code, _ in results) == [0, 1, 1], results
    assert sorted(d.get("code", "ok") for _, d in results) == ["ok", "wrong_state", "wrong_state"]
    assert len(started_events(root)) == 1
    assert not ledger.verify_chain(root)


@pytest.mark.parametrize("widen", [False, True])
def test_race_two_approved_invites_for_one_person(real_ws, tmp_path, widen):
    root, _ = real_ws
    a = P(root)
    twin = craft(root, rules.LINKEDIN_INVITE, folded(root, a.id, models.utc_now()).target_id, url=URL)
    real_decide(root, a.id), real_decide(root, twin)
    results = race(tmp_path, [["approval", "begin", x, "--json", "--workspace", str(root)] for x in (a.id, twin)], widen)
    assert sorted(d.get("code", "ok") for _, d in results) == ["in_flight_conflict", "ok"], results
    assert len(started_events(root)) == 1


@pytest.mark.parametrize("widen", [False, True])
def test_race_two_approved_submits_for_one_job(real_ws, tmp_path, widen):
    root, job = real_ws
    a = service.propose(root, kind=rules.APPLICATION_SUBMIT, person_url=None, job_ref=job["id"], label="Example job",
                        content_text="Answers", manifest_paths=[job["resume"]], actor="agent:test")
    twin = craft(root, rules.APPLICATION_SUBMIT, job["id"], entries=manifest.hash_files(root, [job["resume"]]))
    real_decide(root, a.id), real_decide(root, twin)
    results = race(tmp_path, [["approval", "begin", x, "--json", "--workspace", str(root)] for x in (a.id, twin)], widen)
    assert sorted(d.get("code", "ok") for _, d in results) == ["in_flight_conflict", "ok"], results
    assert len(started_events(root)) == 1


@pytest.mark.parametrize("widen", [False, True])
def test_race_propose_against_begin(real_ws, tmp_path, widen):
    root, _ = real_ws
    a = P(root)
    real_decide(root, a.id)
    content = tmp_path / "newer.txt"
    content.write_text("a newer draft")
    results = race(tmp_path, [
        ["approval", "begin", a.id, "--json", "--workspace", str(root)],
        ["approval", "propose", "--kind", "linkedin_invite", "--person-url", URL, "--content-file", str(content),
         "--json", "--workspace", str(root)],
    ], widen)
    (begin_code, begin_out), (propose_code, propose_out) = results
    now = models.utc_now()
    states = {i: st.state.value for i, st in fold.fold_all(ledger.read_events(root), now).items()}
    if begin_code == 0:
        # the claim won: the proposal was refused and nothing new exists
        assert propose_code == 1 and propose_out["code"] == "in_flight_conflict", results
        assert states == {a.id: "in_flight"}
    else:
        # the proposal won: it superseded the approval before the claim, which then failed
        assert begin_out["code"] == "wrong_state" and propose_code == 0, results
        assert states == {a.id: "superseded", propose_out["id"]: "pending"}
    assert not (states.get(a.id) == "in_flight" and any(s in ("pending", "approved") for s in states.values()))
