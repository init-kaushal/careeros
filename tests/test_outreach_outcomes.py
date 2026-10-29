"""Outreach outcomes (`careeros outreach record`), the read-only derived commands (`queue`, `cap`, `next`, `warm`) and
`OUT007` (sub-project 4a, Task D), through the real CLI on invented data.

Time comes from ONE source: `models.utc_now` is patched (the `W` helper of the records tests) and the same value stamps
the ledger and is the commands' `now`, so advancing `w.now` advances everything at once."""

from __future__ import annotations

import json
import threading

import pytest
from helpers import add_job, make_workspace
from test_outreach_records import ALEX, KIM, SAM, T0, Probe, W, full_state, rejected, shift

from careeros.core import ids, ledger, models
from careeros.core.approvals import cap
from careeros.core.ledger import LedgerError
from careeros.core.models import State
from careeros.core.outreach import derive, rules, service
from careeros.core.validation import validate_workspace as validate_all
from careeros.core.workspace import WorkspaceLock

PAT = "https://www.linkedin.com/in/pat-example"


@pytest.fixture
def w(tmp_path, monkeypatch) -> W:
    monkeypatch.delenv("CAREEROS_WORKSPACE", raising=False)
    return W(make_workspace(tmp_path / "ws"), monkeypatch)


def person(w: W, url=ALEX, **kw) -> str:
    return w.person(url, **kw)["id"]


def invited(w: W, url=ALEX, **kw) -> str:
    """A person record plus a consumed invite: returns the person id."""
    per = person(w, url, **kw)
    got, _ = w.invite_done(url)
    assert got == per
    return per


def messaged(w: W, url: str) -> str:
    """A consumed linkedin_message (no invite)."""
    msg = w.propose("linkedin_message", url)
    w.mp.setattr("careeros.cli._util.is_interactive", lambda: True)
    res = w.run("approve", msg["id"], input="a\n")
    w.mp.setattr("careeros.cli._util.is_interactive", lambda: False)
    assert res.exit_code == 0, res.output
    w.ok("approval", "begin", msg["id"])
    w.ok("approval", "done", msg["id"])
    return msg["target"]


def record(w: W, per: str, *flags: str):
    return w.ok("outreach", "record", per, *flags)


def label(w: W, per: str) -> str:
    return w.ok("outreach", "person", "show", per)["label"]


def outcome_events(w: W) -> list[dict]:
    return [e for e in w.events() if e["type"] in ("outreach.accepted", "outreach.replied", "outreach.closed")]


# =========================================================================================================================
# record: transitions, refusals, event shapes
# =========================================================================================================================

def test_accepted_after_an_invite(w: W) -> None:
    per = invited(w)
    body = record(w, per, "--accepted")
    assert body["outcome"] == "accepted" and body["id"] == per and body["label"] == "connected"
    (ev,) = outcome_events(w)
    assert ev["type"] == "outreach.accepted" and ev["entity"] == per and ev["source"] == "user"
    assert ev["reason"] is None and ev["prev_state"] == "invited" and ev["new_state"] == "connected"
    assert ev["actor"] == "agent:cli" and body["seq"] == ev["seq"]
    assert label(w, per) == "connected"


def test_replied_after_an_invite_and_after_a_message_only(w: W) -> None:
    a = invited(w)
    assert record(w, a, "--replied")["label"] == "replied"
    b = person(w, SAM)
    assert messaged(w, SAM) == b
    assert record(w, b, "--replied")["label"] == "replied"


def test_closed_needs_a_reason_from_the_enum(w: W) -> None:
    per = person(w)
    w.refused("outreach", "record", per, "--closed", code="invalid_input")
    w.refused("outreach", "record", per, "--closed", "--reason", "bored", code="invalid_input")
    assert outcome_events(w) == []
    for reason in rules.CLOSED_REASONS[:1]:
        body = record(w, per, "--closed", "--reason", reason)
    assert body["label"] == "closed"
    (ev,) = outcome_events(w)
    assert ev["type"] == "outreach.closed" and ev["reason"] == "no_response"
    assert w.ok("outreach", "person", "show", per)["state"]["closed_reason"] == "no_response"


@pytest.mark.parametrize("reason", rules.CLOSED_REASONS)
def test_every_closed_reason_is_recorded(w: W, reason: str) -> None:
    per = person(w)
    record(w, per, "--closed", "--reason", reason)
    assert outcome_events(w)[0]["reason"] == reason


def test_a_reason_is_only_for_closed(w: W) -> None:
    per = invited(w)
    w.refused("outreach", "record", per, "--accepted", "--reason", "declined", code="invalid_input")
    assert outcome_events(w) == []


@pytest.mark.parametrize("source", rules.OUTCOME_SOURCES)
def test_each_source_is_recorded(w: W, source: str) -> None:
    per = invited(w)
    record(w, per, "--accepted", "--source", source)
    assert outcome_events(w)[0]["source"] == source


def test_a_bad_source_is_refused(w: W) -> None:
    per = invited(w)
    w.refused("outreach", "record", per, "--accepted", "--source", "cli", code="invalid_input")
    assert outcome_events(w) == [] and len(rejected(w)) == 1


def test_the_note_is_kept_in_the_action_and_checked(w: W) -> None:
    per = invited(w)
    record(w, per, "--accepted", "--note", "accepted on the phone app")
    assert "accepted on the phone app" in outcome_events(w)[0]["action"]
    b = invited(w, SAM, name="Sam Example")
    w.refused("outreach", "record", b, "--accepted", "--note", "bad \u202e note", code="invalid_input")
    w.refused("outreach", "record", b, "--accepted", "--note", "x" * 501, code="invalid_input")
    assert len(outcome_events(w)) == 1


ILLEGAL = [
    ("accepted without an invite", [], "--accepted"),
    ("replied without an invite or message", [], "--replied"),
    ("accepted twice", ["--accepted"], "--accepted"),
    ("replied twice", ["--replied"], "--replied"),
    ("accepted after closed", ["--closed"], "--accepted"),
    ("replied after closed", ["--closed"], "--replied"),
    ("closed twice", ["--closed"], "--closed"),
]


@pytest.mark.parametrize("why,before,flag", ILLEGAL, ids=[i[0] for i in ILLEGAL])
@pytest.mark.parametrize("with_invite", [False, True])
def test_illegal_outcomes_are_wrong_state_and_deduped(w: W, why, before, flag, with_invite) -> None:
    if (not before and with_invite) or (set(before) & {"--accepted", "--replied"} and not with_invite):
        pytest.skip("not a meaningful combination")
    per = invited(w) if with_invite else person(w)
    for b in before:
        record(w, per, b, *(["--reason", "declined"] if b == "--closed" else []))
    extra = ["--reason", "declined"] if flag == "--closed" else []
    events_before = len(outcome_events(w))
    body = w.refused("outreach", "record", per, flag, *extra, code="wrong_state")
    assert per in body["error"]
    assert len(outcome_events(w)) == events_before
    (rej,) = rejected(w)
    assert rej["entity"] == per and rej["reason"].startswith("wrong_state: ")
    w.refused("outreach", "record", per, flag, *extra, code="wrong_state")
    assert len(rejected(w)) == 1                                    # deduped


def test_accepted_after_replied_is_legal(w: W) -> None:
    per = invited(w)
    record(w, per, "--replied")
    assert record(w, per, "--accepted")["label"] == "replied"       # replied outranks connected


def test_a_person_with_approval_events_and_no_record_can_be_recorded(w: W) -> None:
    per, _ = w.invite_done(KIM)                 # no `person add`: an OUT003 case
    assert record(w, per, "--accepted")["label"] == "connected"
    assert record(w, per, "--closed", "--reason", "not_a_fit")["label"] == "closed"


def test_an_unknown_or_invalid_person_appends_nothing(w: W) -> None:
    person(w)
    before = w.ledger_bytes()
    w.refused("outreach", "record", ids.new_id("person"), "--accepted", code="not_found")
    w.refused("outreach", "record", "per_nope", "--accepted", code="invalid_input")
    w.refused("outreach", "record", "job_aaaaaaaaaa", "--closed", "--reason", "other", code="invalid_input")
    assert w.ledger_bytes() == before


def test_exactly_one_outcome_flag_is_a_usage_error(w: W) -> None:
    per = person(w)
    before = w.ledger_bytes()
    for flags in ([], ["--accepted", "--replied"], ["--closed", "--accepted", "--reason", "other"]):
        res = w.run("outreach", "record", per, *flags, "--json")
        assert res.exit_code == 2, res.output
        assert "error" in json.loads(res.stdout)
    assert w.ledger_bytes() == before


def test_json_is_one_object_and_text_output(w: W) -> None:
    per = invited(w)
    res = w.run("outreach", "record", per, "--accepted", "--json")
    assert res.exit_code == 0 and isinstance(json.loads(res.stdout), dict)
    res = w.run("outreach", "record", per, "--replied")
    assert res.exit_code == 0 and res.output.startswith(f"recorded replied for {per}")
    res = w.run("outreach", "record", per, "--replied")
    assert res.exit_code == 1 and "[wrong_state]" in res.output


def test_clock_floor_is_clock_skew_with_one_deduped_rejection(w: W) -> None:
    per = invited(w)
    w.now = shift(T0, seconds=-1)
    before = len(w.events())
    w.refused("outreach", "record", per, "--accepted", code="clock_skew")
    w.refused("outreach", "record", per, "--accepted", code="clock_skew")
    assert len(w.events()) == before + 1 and rejected(w)[0]["reason"].startswith("clock_skew: ")
    w.now = T0                                              # equal to the floor is fine
    record(w, per, "--accepted")


def test_a_broken_chain_is_ledger_corrupt_and_writes_nothing(w: W) -> None:
    per = invited(w)
    path = w.root / "ledger.jsonl"
    lines = path.read_bytes().split(b"\n")
    lines[0] = lines[0].replace(b'"action":"', b'"action":"edited ', 1)
    path.write_bytes(b"\n".join(lines))
    before = full_state(w.root)
    w.refused("outreach", "record", per, "--accepted", code="ledger_corrupt")
    assert full_state(w.root) == before


def test_a_failed_ledger_write_leaves_the_ledger_as_it_was(w: W, monkeypatch) -> None:
    per = invited(w)
    before = w.ledger_bytes()
    real = ledger.append_events

    def failing(root, specs):
        if any(s["type"] == "outreach.accepted" for s in specs):
            raise OSError(28, "No space left on device")
        return real(root, specs)
    monkeypatch.setattr(ledger, "append_events", failing)
    res = w.run("outreach", "record", per, "--accepted", "--json")
    assert res.exit_code == 1 and "could not complete" in json.loads(res.stdout)["error"]
    assert w.ledger_bytes() == before


def test_closed_needs_a_reason_in_the_ledger_too(w: W) -> None:
    per = person(w)
    with pytest.raises(LedgerError):
        ledger.append_events(w.root, [{"type": "outreach.closed", "actor": "user", "entity": per, "source": "user",
                                       "action": "closed"}])


def test_record_reads_the_clock_once_under_the_lock(w: W) -> None:
    per = invited(w)
    for call in (lambda c: service.record(w.root, per, "accepted", source="user", actor="user", clock=c),
                 lambda c: service.record(w.root, per, "accepted", source="user", actor="user", clock=c)):  # the 2nd refuses
        probe = Probe(w.root, shift(T0, hours=1))
        try:
            call(probe)
        except service.Refused:
            pass
        assert probe.calls == 1 and probe.held == [True]


def test_a_closed_event_with_an_unreadable_ts_still_closes(w: W) -> None:
    per = invited(w)
    w.mp.setattr(models, "utc_now", lambda: "not-a-time")
    w.append({"type": "outreach.closed", "actor": "user", "entity": per, "source": "user", "reason": "other",
              "action": "closed by hand"})
    w.mp.setattr(models, "utc_now", lambda: w.now)
    w.refused("outreach", "record", per, "--accepted", code="wrong_state")


# =========================================================================================================================
# the derived commands
# =========================================================================================================================

def world(w: W) -> dict:
    """An invented world: an approved target with a recruiter and a hiring manager, an invited person whose company has
    a saved job, a messaged person, and an APPLIED job."""
    w.target()
    w.approve_target("example-corp")
    rec = person(w, ALEX, role="recruiter", found_in=["target:example-corp"])
    hm = person(w, SAM, name="Sam Example", role="hiring_manager", found_in=["target:example-corp"])
    job = add_job(w.root, "other-co", company="Other Co", status=State.DISCOVERED)
    inv = invited(w, KIM, name="Kim Example", company="Other Co", role="peer", found_in=[f"job:{job['id']}"])
    applied = add_job(w.root, "applied-co", company="Applied Co", status=State.APPLIED)
    w.append({"type": "job.status_changed", "actor": "user", "entity": applied["id"], "prev_state": "DISCOVERED",
              "new_state": "APPLIED", "action": "applied", "source": "cli"})
    return {"rec": rec, "hm": hm, "inv": inv, "job": job, "applied": applied}


def expected(w: W, now: str):
    L = service.load(w.root, now)
    targets = {slug: derive.TargetInfo(L.targets[slug], view) for slug, view in L.targets_fold.targets.items()
               if slug in L.targets}
    return L, targets


def as_dicts(items) -> list[dict]:
    import dataclasses
    return [json.loads(json.dumps(dataclasses.asdict(i))) for i in items]


def test_queue_equals_the_pure_function_and_advances_with_the_one_clock(w: W) -> None:
    ids_ = world(w)
    for days, seconds, due in ((6, 86399, []), (7, 0, ["invite"]), (10, 0, ["invite", "application"])):
        w.now = shift(T0, days=days, seconds=seconds) if days != 6 else shift(T0, days=7, seconds=-1)
        L, targets = expected(w, w.now)
        q = derive.build_queue(L.people, L.people_fold.flags, targets, L.jobs, w.now)
        body = w.ok("outreach", "queue")
        assert body["items"] == as_dicts(q.items)
        assert [i["kind"] for i in body["items"]] == sorted(due, key=["invite", "application"].index)
        assert body["note"] == derive.UNCOVERED_NOTE and body["now"] == w.now
    assert {i["entity_id"] for i in body["items"]} == {ids_["inv"], ids_["applied"]["id"]}
    record(w, ids_["inv"], "--accepted")
    assert [i["kind"] for i in w.ok("outreach", "queue")["items"]] == ["application"]


def test_queue_text_says_email_and_interview_follow_ups_are_not_covered(w: W) -> None:
    res = w.run("outreach", "queue")
    assert res.exit_code == 0 and "Email and interview follow-ups are not covered" in res.output
    assert "nothing is due" in res.output
    world(w)
    w.now = shift(T0, days=11)
    res = w.run("outreach", "queue")
    assert "invite" in res.output and "application" in res.output and "not covered" in res.output


def test_cap_counts_and_default(w: W) -> None:
    world(w)
    body = w.ok("outreach", "cap")
    assert body["cap"] == 15 and body["configured"] is False
    assert (body["count"], body["in_flight"], body["consumed_in_window"], body["remaining"]) == (1, 0, 1, 14)
    assert body["oldest_leaves"] == shift(T0, days=7)
    L = service.load(w.root, w.now)
    assert cap.invite_count(L.items, w.now).total == body["count"]
    w.now = shift(T0, days=7)
    assert w.ok("outreach", "cap")["count"] == 0
    res = w.run("outreach", "cap")
    assert res.exit_code == 0 and "15" in res.output and "remaining" in res.output


def set_cap(root, line: str | None, *, extra: str = "") -> None:
    text = "# Profile\n\n## Outreach\n" + (f"{line}\n" if line is not None else "") + extra
    (root / "profile.md").write_text(text, encoding="utf-8")


def test_cap_reads_the_profile_setting(w: W) -> None:
    world(w)
    set_cap(w.root, "- **Weekly invite cap:** 3")
    body = w.ok("outreach", "cap")
    assert body["cap"] == 3 and body["configured"] is True and body["remaining"] == 2


INVALID = ["Weekly invite cap: abc", "Weekly invite cap: -1", "Weekly invite cap: 1001", "Weekly invite cap: 1.5",
           "Weekly invite cap:", "Weekly invite cap: 5\nWeekly invite cap: 5"]


@pytest.mark.parametrize("line", INVALID)
def test_cap_and_next_refuse_an_invalid_setting_queue_and_warm_do_not(w: W, line: str) -> None:
    world(w)
    set_cap(w.root, line)
    for cmd in ("cap", "next"):
        body = w.refused("outreach", cmd, code="cap_config_invalid")
        assert "Weekly invite cap" in body["error"] and "0 to 1000" in body["error"]
    w.ok("outreach", "queue")
    w.ok("outreach", "warm")


def test_next_equals_the_pure_function_and_is_limited_by_remaining(w: W) -> None:
    ids_ = world(w)
    L, targets = expected(w, w.now)
    want = derive.build_next(L.people, L.people_fold.flags, targets, L.jobs, L.items,
                             cap.remaining(15, cap.invite_count(L.items, w.now)), w.now)
    body = w.ok("outreach", "next")
    assert body["contacts"] == as_dicts(want) and body["remaining"] == 14 and body["cap"] == 15
    assert [c["person_id"] for c in body["contacts"]] == [ids_["rec"], ids_["hm"]]
    set_cap(w.root, "Weekly invite cap: 2")                 # one consumed invite counts: one slot left
    assert [c["person_id"] for c in w.ok("outreach", "next")["contacts"]] == [ids_["rec"]]
    set_cap(w.root, "Weekly invite cap: 0")
    assert w.ok("outreach", "next")["contacts"] == []
    res = w.run("outreach", "next")
    assert res.exit_code == 0 and "nobody" in res.output


def test_warm_equals_the_pure_function(w: W) -> None:
    ids_ = world(w)
    assert w.ok("outreach", "warm")["contacts"] == []
    record(w, ids_["inv"], "--accepted")
    L, _ = expected(w, w.now)
    want = derive.build_warm(L.people, L.people_fold.flags, L.jobs, w.now)
    body = w.ok("outreach", "warm")
    assert body["contacts"] == as_dicts(want) and [c["person_id"] for c in body["contacts"]] == [ids_["inv"]]
    res = w.run("outreach", "warm")
    assert res.exit_code == 0 and "Kim Example" in res.output


@pytest.mark.parametrize("cmd", ["queue", "cap", "next", "warm"])
def test_read_only_commands_change_nothing_and_print_one_json_object(w: W, cmd: str) -> None:
    world(w)
    w.now = shift(T0, days=11)
    before = full_state(w.root, lock=True)
    res = w.run("outreach", cmd, "--json")
    assert res.exit_code == 0 and isinstance(json.loads(res.stdout), dict)
    w.run("outreach", cmd)
    assert full_state(w.root, lock=True) == before


@pytest.mark.parametrize("cmd", ["queue", "cap", "next", "warm"])
def test_read_only_commands_on_a_corrupt_ledger_line_exit_1(w: W, cmd: str) -> None:
    person(w)
    with (w.root / "ledger.jsonl").open("ab") as handle:
        handle.write(b"{not json\n")
    res = w.run("outreach", cmd, "--json")
    assert res.exit_code == 1 and "error" in json.loads(res.stdout)


def test_read_only_commands_do_not_wait_for_the_lock(w: W) -> None:
    world(w)
    holding, release = threading.Event(), threading.Event()

    def hold():
        with WorkspaceLock(w.root):
            holding.set()
            release.wait(30)
    t = threading.Thread(target=hold)
    t.start()
    try:
        holding.wait(5)
        for cmd in ("queue", "cap", "next", "warm"):
            assert w.run("outreach", cmd, "--json").exit_code == 0
    finally:
        release.set()
        t.join(10)


def test_derived_commands_on_an_empty_workspace(w: W) -> None:
    assert w.ok("outreach", "queue")["items"] == []
    assert w.ok("outreach", "cap")["count"] == 0
    assert w.ok("outreach", "next")["contacts"] == []
    assert w.ok("outreach", "warm")["contacts"] == []


# =========================================================================================================================
# OUT007
# =========================================================================================================================

def out007(root, now=T0):
    return [i for i in validate_all(root, now) if i.code == "OUT007"]


POSITIVE = {
    "abc": ("## Outreach\nWeekly invite cap: abc\n", ["Weekly invite cap: abc"]),
    "negative": ("## Outreach\nWeekly invite cap: -1\n", ["Weekly invite cap: -1"]),
    "above 1000": ("## Outreach\nWeekly invite cap: 1001\n", ["Weekly invite cap: 1001"]),
    "fraction": ("## Outreach\nWeekly invite cap: 1.5\n", ["Weekly invite cap: 1.5"]),
    "empty": ("## Outreach\nWeekly invite cap:\n", ["Weekly invite cap:"]),
    "two lines": ("## Outreach\n- Weekly invite cap: 5\n- Weekly invite cap: 9\n",
                  ["- Weekly invite cap: 5", "- Weekly invite cap: 9"]),
}


@pytest.mark.parametrize("name", POSITIVE)
def test_out007_positive(tmp_path, name: str) -> None:
    root = make_workspace(tmp_path / "ws")
    body, quoted = POSITIVE[name]
    (root / "profile.md").write_text("# Profile\n\n" + body, encoding="utf-8")
    (issue,) = out007(root)
    assert issue.severity == "error" and issue.path == "profile.md"
    for line in quoted:
        assert line in issue.message, issue.message
    assert "Weekly invite cap: N" in issue.fix and "0 to 1000" in issue.fix


NEGATIVE = {
    "no outreach heading": "# Profile\n\nWeekly invite cap: abc\n",
    "heading without the line": "# Profile\n\n## Outreach\nSome notes.\n",
    "zero": "# Profile\n\n## Outreach\nWeekly invite cap: 0\n",
    "fifteen": "# Profile\n\n## Outreach\nWeekly invite cap: 15\n",
    "thousand": "# Profile\n\n## Outreach\nWeekly invite cap: 1000\n",
    "list and bold": "# Profile\n\n## Outreach\n- **Weekly invite cap:** 20\n",
    "below the fold": "# Profile\n\n## Outreach\nWeekly invite cap: 5\n\n<!-- do not read past this line -->\n\n## Outreach\nWeekly invite cap: abc\n",
}


@pytest.mark.parametrize("name", NEGATIVE)
def test_out007_negative(tmp_path, name: str) -> None:
    root = make_workspace(tmp_path / "ws")
    (root / "profile.md").write_text(NEGATIVE[name], encoding="utf-8")
    assert out007(root) == []


def test_out007_no_profile(tmp_path) -> None:
    root = make_workspace(tmp_path / "ws")
    (root / "profile.md").unlink()
    assert out007(root) == []


def test_the_fold_marker_used_in_the_negative_case_is_the_real_one() -> None:
    from careeros.core import fold
    assert fold.FOLD_MARKER in NEGATIVE["below the fold"]


def test_validate_with_out007_is_byte_identical_and_exits_1(w: W) -> None:
    world(w)
    set_cap(w.root, "Weekly invite cap: lots")
    before = full_state(w.root, lock=True)
    res = w.run("validate", "--json")
    assert res.exit_code == 1
    body = json.loads(res.stdout)
    assert [i["code"] for i in body["issues"]].count("OUT007") == 1
    assert full_state(w.root, lock=True) == before
    set_cap(w.root, "Weekly invite cap: 7")
    before = full_state(w.root, lock=True)
    res = w.run("validate", "--json")
    assert "OUT007" not in [i["code"] for i in json.loads(res.stdout)["issues"]]
    assert full_state(w.root, lock=True) == before


def test_out007_with_no_other_outreach_data(tmp_path) -> None:
    root = make_workspace(tmp_path / "ws")
    (root / "profile.md").write_text("# Profile\n\n## Outreach\nWeekly invite cap: x\n", encoding="utf-8")
    assert len(out007(root)) == 1


def test_an_undecodable_profile_fails_closed(tmp_path) -> None:
    root = make_workspace(tmp_path / "ws")
    (root / "profile.md").write_bytes(b"# Profile\n\xff\xfe\n")
    (issue,) = out007(root)
    assert issue.severity == "error" and "UTF-8" in issue.message
