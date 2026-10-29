"""The approvals enforcement of outreach (sub-project 4a, Task D): `person_closed` at `approval propose` and `begin`,
`cap_reached` and `cap_config_invalid` at `begin` for invites, the atomic cap check plus claim under the workspace lock,
the refusal audit, and real cross-process races. Invented data only.

Time comes from ONE source (the `W` helper of the records tests patches `models.utc_now`), except in the real-process
races, where the children and the parent use the real clock."""

from __future__ import annotations

import dataclasses
import json
import subprocess
import sys
import threading

import pytest
from helpers import make_workspace
from test_approvals_claims import Cmd, race_many
from test_outreach_records import ALEX, KIM, SAM, T0, W, full_state, shift

from careeros.cli import _util
from careeros.core import ledger, models
from careeros.core import workspace as wsmod
from careeros.core.approvals import cap, fold, people, rules
from careeros.core.approvals import service as aservice
from careeros.core.outreach import derive, service

PAT = "https://www.linkedin.com/in/pat-example"
LEE = "https://www.linkedin.com/in/lee-example"
URLS = [ALEX, SAM, KIM, PAT, LEE] + [f"https://www.linkedin.com/in/extra-{i}" for i in range(20)]


@pytest.fixture
def w(tmp_path, monkeypatch) -> W:
    monkeypatch.delenv("CAREEROS_WORKSPACE", raising=False)
    return W(make_workspace(tmp_path / "ws"), monkeypatch)


def approve(w: W, item: str) -> None:
    w.mp.setattr(_util, "is_interactive", lambda: True)
    res = w.run("approve", item, input="a\n")
    w.mp.setattr(_util, "is_interactive", lambda: False)
    assert res.exit_code == 0, res.output


def approved(w: W, kind: str, url: str) -> dict:
    item = w.propose(kind, url)
    approve(w, item["id"])
    return item


def close(w: W, per: str, reason: str = "declined") -> None:
    w.ok("outreach", "record", per, "--closed", "--reason", reason)


def set_cap(root, line: str | None) -> None:
    (root / "profile.md").write_text("# Profile\n\n## Outreach\n" + (f"{line}\n" if line else ""), encoding="utf-8")


def approval_rejections(w: W, item: str) -> list[dict]:
    return [e for e in w.events("approval.rejected") if e["entity"] == item]


def started(w: W) -> list[dict]:
    return w.events("approval.started")


# =========================================================================================================================
# person_closed
# =========================================================================================================================

@pytest.mark.parametrize("kind", ["linkedin_invite", "linkedin_message"])
def test_propose_refuses_a_closed_person_and_writes_nothing(w: W, kind: str) -> None:
    per = w.person(ALEX)["id"]
    close(w, per)
    f = w.root / "draft.txt"
    f.write_text("Hi there, a short invented note.\n", encoding="utf-8")
    before = full_state(w.root)
    res = w.run("approval", "propose", "--kind", kind, "--person-url", ALEX, "--content-file", str(f), "--json")
    assert res.exit_code == 1 and json.loads(res.stdout)["code"] == "person_closed", res.output
    assert full_state(w.root) == before


def test_propose_still_works_for_another_person_and_for_submits(w: W) -> None:
    close(w, w.person(ALEX)["id"])
    w.propose("linkedin_invite", SAM)


@pytest.mark.parametrize("kind", ["linkedin_invite", "linkedin_message"])
@pytest.mark.parametrize("when", ["after proposal", "after approval"])
def test_begin_refuses_a_person_closed_after_proposal_or_approval(w: W, kind: str, when: str) -> None:
    per = w.person(ALEX)["id"]
    item = w.propose(kind, ALEX)
    if when == "after proposal":
        close(w, per)
        approve(w, item["id"])
    else:
        approve(w, item["id"])
        close(w, per)
    body = w.refused("approval", "begin", item["id"], code="person_closed")
    assert per in body["error"]
    assert started(w) == []
    (rej,) = approval_rejections(w, item["id"])
    assert rej["reason"].startswith("person_closed: ")
    w.refused("approval", "begin", item["id"], code="person_closed")
    assert len(approval_rejections(w, item["id"])) == 1             # deduped


def test_begin_refuses_a_closed_person_without_a_record(w: W) -> None:
    item = approved(w, "linkedin_message", KIM)        # no `person add`: OUT003; closed is ledger-only
    close(w, item["target"], "not_a_fit")
    w.refused("approval", "begin", item["id"], code="person_closed")


def test_a_closed_event_with_an_unreadable_ts_still_blocks_begin(w: W) -> None:
    item = approved(w, "linkedin_invite", ALEX)
    w.mp.setattr(models, "utc_now", lambda: "garbage")
    w.append({"type": "outreach.closed", "actor": "user", "entity": item["target"], "source": "user", "reason": "other",
              "action": "closed by hand"})
    w.mp.setattr(models, "utc_now", lambda: w.now)
    assert people.closed_people(w.events())[0] == {item["target"]}
    w.refused("approval", "begin", item["id"], code="person_closed")


def test_closing_someone_else_does_not_block(w: W) -> None:
    item = approved(w, "linkedin_invite", ALEX)
    close(w, w.person(SAM)["id"])
    w.ok("approval", "begin", item["id"])


def test_a_submit_item_is_never_person_closed(w: W) -> None:
    from helpers import add_job
    from careeros.core.models import State
    job = add_job(w.root, "example-platform", status=State.APPROVAL_REQUIRED, bullet="approval-required")
    (job["path"].parent / "resume.pdf").write_bytes(b"%PDF")
    res = w.run("approval", "propose", "--kind", "application_submit", "--job", job["id"], "--content-file",
                str(w.root / "profile.md"), "--manifest", "jobs/discovered/example-platform/resume.pdf", "--json")
    assert res.exit_code == 0, res.output
    w.append({"type": "outreach.closed", "actor": "user", "entity": job["id"], "source": "user", "reason": "other",
              "action": "a hand-made close on a job id"})
    item = json.loads(res.stdout)["id"]
    assert aservice._closed_problem(fold.fold_all(w.events(), w.now)[item], w.events()) is None


# =========================================================================================================================
# the cap at begin
# =========================================================================================================================

def sent_invites(w: W, n: int, *, done: bool = True, start: int = 0) -> list[str]:
    out = []
    for url in URLS[start:start + n]:
        item = approved(w, "linkedin_invite", url)
        w.ok("approval", "begin", item["id"])
        if done:
            w.ok("approval", "done", item["id"])
        out.append(item["id"])
    return out


@pytest.mark.parametrize("value", [1, 3])
def test_cap_boundary(w: W, value: int) -> None:
    set_cap(w.root, f"Weekly invite cap: {value}")
    sent_invites(w, value - 1)                                  # cap - 1 counted: the next begin proceeds
    last = approved(w, "linkedin_invite", URLS[value - 1])
    w.ok("approval", "begin", last["id"])
    over = approved(w, "linkedin_invite", URLS[value])          # cap counted: refused
    body = w.refused("approval", "begin", over["id"], code="cap_reached")
    assert f"{value}" in body["error"] and "profile.md" in body["error"]
    (rej,) = approval_rejections(w, over["id"])
    assert rej["reason"].startswith("cap_reached: ")
    assert len(started(w)) == value


def test_the_default_cap_is_15(w: W) -> None:
    sent_invites(w, 15)
    item = approved(w, "linkedin_invite", URLS[15])
    w.refused("approval", "begin", item["id"], code="cap_reached")
    set_cap(w.root, "Weekly invite cap: 16")                    # the only override: raise the cap
    w.ok("approval", "begin", item["id"])


def test_an_in_flight_invite_older_than_7_days_blocks_until_resolved(w: W) -> None:
    set_cap(w.root, "Weekly invite cap: 1")
    (old,) = sent_invites(w, 1, done=False)
    w.now = shift(T0, days=8)
    item = approved(w, "linkedin_invite", SAM)
    w.refused("approval", "begin", item["id"], code="cap_reached")
    w.mp.setattr(_util, "is_interactive", lambda: True)
    res = w.run("approval", "resolve", old, "--not-sent", input="y\n")
    w.mp.setattr(_util, "is_interactive", lambda: False)
    assert res.exit_code == 0, res.output
    w.ok("approval", "begin", item["id"])


def test_a_consumed_invite_leaves_the_window_after_exactly_7_days(w: W) -> None:
    set_cap(w.root, "Weekly invite cap: 1")
    sent_invites(w, 1)
    w.now = shift(T0, days=7, seconds=-1)
    item = approved(w, "linkedin_invite", SAM)
    w.refused("approval", "begin", item["id"], code="cap_reached")
    w.now = shift(T0, days=7)
    w.ok("approval", "begin", item["id"])


def test_in_flight_then_consumed_counts_once(w: W) -> None:
    set_cap(w.root, "Weekly invite cap: 2")
    (a,) = sent_invites(w, 1, done=False)
    b = approved(w, "linkedin_invite", SAM)
    w.ok("approval", "begin", b["id"])                       # a in flight counts 1: b is the 2nd
    w.ok("approval", "done", a)
    c = approved(w, "linkedin_invite", KIM)
    w.refused("approval", "begin", c["id"], code="cap_reached")


def test_cap_zero_blocks_every_invite_but_not_messages(w: W) -> None:
    set_cap(w.root, "Weekly invite cap: 0")
    inv = approved(w, "linkedin_invite", ALEX)
    w.refused("approval", "begin", inv["id"], code="cap_reached")
    msg = approved(w, "linkedin_message", SAM)
    w.ok("approval", "begin", msg["id"])


INVALID = ["Weekly invite cap: abc", "Weekly invite cap: -1", "Weekly invite cap: 1001", "Weekly invite cap: 1.5",
           "Weekly invite cap:", "Weekly invite cap: 5\nWeekly invite cap: 5"]


@pytest.mark.parametrize("line", INVALID)
def test_an_invalid_cap_blocks_invite_begin_and_nothing_else(w: W, line: str) -> None:
    flying = approved(w, "linkedin_invite", PAT)
    w.ok("approval", "begin", flying["id"])
    inv = approved(w, "linkedin_invite", ALEX)
    msg = approved(w, "linkedin_message", SAM)
    set_cap(w.root, line)
    body = w.refused("approval", "begin", inv["id"], code="cap_config_invalid")
    assert "Weekly invite cap" in body["error"] and "0 to 1000" in body["error"]
    (rej,) = approval_rejections(w, inv["id"])
    assert rej["reason"].startswith("cap_config_invalid: ")
    w.ok("approval", "begin", msg["id"])                      # other approvals commands keep working
    w.ok("approval", "done", msg["id"])
    w.ok("approval", "done", flying["id"])
    w.propose("linkedin_invite", KIM)
    w.ok("approval", "list")
    w.ok("approval", "show", inv["id"])


def test_an_undecodable_profile_blocks_invite_begin(w: W) -> None:
    inv = approved(w, "linkedin_invite", ALEX)
    (w.root / "profile.md").write_bytes(b"# Profile\n\xff\n")
    w.refused("approval", "begin", inv["id"], code="cap_config_invalid")


def test_no_profile_means_the_default(w: W) -> None:
    inv = approved(w, "linkedin_invite", ALEX)
    (w.root / "profile.md").unlink()
    w.ok("approval", "begin", inv["id"])


def test_the_checks_run_after_the_existing_ones(w: W) -> None:
    """Order: the existing chain first (here `wrong_state`), then `person_closed`, then the cap."""
    set_cap(w.root, "Weekly invite cap: abc")
    per = w.person(ALEX)["id"]
    item = w.propose("linkedin_invite", ALEX)                 # pending, not approved
    close(w, per)
    w.refused("approval", "begin", item["id"], code="wrong_state")
    approve(w, item["id"])
    w.refused("approval", "begin", item["id"], code="person_closed")


def test_the_new_codes_are_refusal_codes() -> None:
    for code in ("person_closed", "cap_reached", "cap_config_invalid"):
        assert code in rules.REFUSAL_CODES
    assert rules.CAP_CONFIG_INVALID == cap.CAP_CONFIG_INVALID


# =========================================================================================================================
# atomic enforcement: every decision input is read inside the lock
# =========================================================================================================================

def contended_begin(w: W, item: str, change) -> dict:
    """Hold the workspace lock, start `begin` in another thread, wait until it is BLOCKED on the lock, run `change()` while
    still holding the lock, then release it; return begin's result."""
    blocked, holding, release = threading.Event(), threading.Event(), threading.Event()
    real_try = wsmod._try_lock

    def try_lock(fd):
        try:
            return real_try(fd)
        except wsmod._CONTENDED:
            if holding.is_set():
                blocked.set()
            raise
    w.mp.setattr(wsmod, "_try_lock", try_lock)
    out: dict = {}

    def run_begin():
        try:
            got = aservice.begin(w.root, item, actor="agent:test", clock=lambda: w.now)
            out["ok"] = got.id
        except aservice.Refused as exc:
            out["code"] = exc.code

    def hold():
        with wsmod.WorkspaceLock(w.root):
            holding.set()
            release.wait(30)
            change()

    holder = threading.Thread(target=hold)
    holder.start()
    assert holding.wait(10)
    t = threading.Thread(target=run_begin)
    t.start()
    assert blocked.wait(10), "begin never waited for the lock"
    release.set()
    holder.join(30)
    t.join(30)
    return out


def test_a_profile_changed_while_begin_waits_is_the_one_used(w: W) -> None:
    item = approved(w, "linkedin_invite", ALEX)
    assert contended_begin(w, item["id"], lambda: set_cap(w.root, "Weekly invite cap: 0")) == {"code": "cap_reached"}
    other = approved(w, "linkedin_invite", SAM)
    assert contended_begin(w, other["id"], lambda: set_cap(w.root, "Weekly invite cap: x")) == {"code": "cap_config_invalid"}
    assert contended_begin(w, other["id"], lambda: set_cap(w.root, "Weekly invite cap: 1")) == {"ok": other["id"]}


def test_a_close_appended_while_begin_waits_is_seen(w: W) -> None:
    item = approved(w, "linkedin_message", ALEX)
    spec = {"type": "outreach.closed", "actor": "user", "entity": item["target"], "source": "user", "reason": "declined",
            "action": "closed while begin waited"}
    assert contended_begin(w, item["id"], lambda: ledger.append_events(w.root, [spec])) == {"code": "person_closed"}


def test_a_claim_appended_while_begin_waits_is_counted(w: W) -> None:
    set_cap(w.root, "Weekly invite cap: 1")
    a = approved(w, "linkedin_invite", ALEX)
    b = approved(w, "linkedin_invite", SAM)
    claim = lambda: aservice.begin(w.root, a["id"], actor="agent:other", clock=lambda: w.now)   # noqa: E731 - same thread holds the lock
    assert contended_begin(w, b["id"], claim) == {"code": "cap_reached"}
    assert [e["entity"] for e in started(w)] == [a["id"]]


# =========================================================================================================================
# refusal audit: a refusal changes no derived state and appends at most one deduped audit event
# =========================================================================================================================

def derived(w: W) -> dict:
    L = service.load(w.root, w.now)
    targets = {s: derive.TargetInfo(L.targets[s], v) for s, v in L.targets_fold.targets.items() if s in L.targets}
    count = cap.invite_count(L.items, w.now)
    return {
        "flags": {k: dataclasses.asdict(v) for k, v in L.people_fold.flags.items()},
        "targets": {k: dataclasses.asdict(v) for k, v in L.targets_fold.targets.items()},
        "queue": derive.build_queue(L.people, L.people_fold.flags, targets, L.jobs, w.now),
        "cap": count,
        "next": derive.build_next(L.people, L.people_fold.flags, targets, L.jobs, L.items, cap.remaining(15, count), w.now),
        "warm": derive.build_warm(L.people, L.people_fold.flags, L.jobs, w.now),
        "items": {k: (v.state, v.kind, v.target_id, v.consumed_at) for k, v in L.items.items()},
    }


AUDIT_TYPES = ("approval.rejected", "outreach.rejected")


def refusal_scenarios():
    def person_closed(w):
        it = approved(w, "linkedin_invite", ALEX)
        close(w, it["target"])
        return ["approval", "begin", it["id"]], "person_closed"

    def cap_reached(w):
        set_cap(w.root, "Weekly invite cap: 0")
        return ["approval", "begin", approved(w, "linkedin_invite", ALEX)["id"]], "cap_reached"

    def cap_config_invalid(w):
        set_cap(w.root, "Weekly invite cap: lots")
        return ["approval", "begin", approved(w, "linkedin_invite", ALEX)["id"]], "cap_config_invalid"

    def wrong_state(w):
        return ["outreach", "record", w.person(ALEX)["id"], "--accepted"], "wrong_state"

    def clock_skew(w):
        per = w.person(ALEX)["id"]
        w.now = shift(T0, seconds=-5)
        return ["outreach", "record", per, "--closed", "--reason", "other"], "clock_skew"

    def ledger_corrupt(w):
        per = w.person(ALEX)["id"]
        path = w.root / "ledger.jsonl"
        lines = path.read_bytes().split(b"\n")
        lines[0] = lines[0].replace(b'"action":"', b'"action":"edited ', 1)
        path.write_bytes(b"\n".join(lines))
        return ["outreach", "record", per, "--closed", "--reason", "other"], "ledger_corrupt"

    return [person_closed, cap_reached, cap_config_invalid, wrong_state, clock_skew, ledger_corrupt]


@pytest.mark.parametrize("scenario", refusal_scenarios(), ids=lambda f: f.__name__)
def test_refusals_change_no_derived_state(w: W, scenario) -> None:
    w.target()
    w.approve_target("example-corp")
    w.person(KIM, role="recruiter", found_in=["target:example-corp"])
    args, code = scenario(w)
    before = derived(w)
    count_before = len(ledger.read_events(w.root))
    for _ in range(3):
        w.refused(*args, code=code)
    after = ledger.read_events(w.root)
    assert derived(w) == before
    added = after[count_before:]
    assert len(added) <= 1 and all(e["type"] in AUDIT_TYPES for e in added), added
    if code == "ledger_corrupt":
        assert added == []


# =========================================================================================================================
# isolation
# =========================================================================================================================

def test_approvals_import_nothing_from_outreach() -> None:
    code = ("import sys, careeros.core.approvals.service, careeros.cli.approval; "
            "print([m for m in sys.modules if m.startswith('careeros.core.outreach')])")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60, check=True).stdout
    assert out.strip() == "[]"


# =========================================================================================================================
# real cross-process races
# =========================================================================================================================

def real_root(tmp_path, name="ws"):
    return make_workspace(tmp_path / name)


def real_approved(root, kind, url) -> aservice.Proposed:
    got = aservice.propose(root, kind=kind, person_url=url, job_ref=None, label="Someone, Example Corp",
                           content_text="Hi, an invented note.\n", manifest_paths=[], actor="agent:test")
    aservice.decide(root, got.id, confirm=lambda text: "approve", clock=models.utc_now)
    return got


def WS(root):
    return ["--workspace", str(root)]


@pytest.mark.parametrize("widen", [False, True])
def test_race_begins_at_cap_minus_one_exactly_one_proceeds(tmp_path, widen) -> None:
    rounds, roots = [], []
    for k in range(3):
        root = real_root(tmp_path, f"ws{k}")
        set_cap(root, "Weekly invite cap: 2")
        first = real_approved(root, rules.LINKEDIN_INVITE, URLS[0])
        aservice.begin(root, first.id, actor="agent:test", clock=models.utc_now)        # one slot left
        items = [real_approved(root, rules.LINKEDIN_INVITE, u).id for u in URLS[1:4]]
        rounds.append([Cmd("approval", "begin", i, "--json", *WS(root)) for i in items])
        roots.append(root)
    for root, results in zip(roots, race_many(tmp_path, rounds, widen)):
        codes = sorted(d.get("code", "ok") if code else "ok" for code, d in results)
        assert codes == ["cap_reached", "cap_reached", "ok"], results
        assert len([e for e in ledger.read_events(root) if e["type"] == rules.EV_STARTED]) == 2
        assert not ledger.verify_chain(root)


def _close_round(tmp_path, k, kind, delay_begin, delay_record):
    root = real_root(tmp_path, f"c{k}")
    got = real_approved(root, kind, URLS[0])
    cmds = [Cmd("approval", "begin", got.id, "--json", *WS(root), delay=delay_begin),
            Cmd("outreach", "record", got.target_id, "--closed", "--reason", "declined", "--json", *WS(root),
                delay=delay_record)]
    return root, got, cmds


@pytest.mark.parametrize("widen", [False, True])
def test_race_record_closed_against_begin(tmp_path, widen) -> None:
    made = []
    k = 0
    for kind in (rules.LINKEDIN_INVITE, rules.LINKEDIN_MESSAGE):
        for delay_begin, delay_record in ((0.0, 0.0), (0.0, 0.05), (0.0, 0.1), (0.4, 0.0)):
            made.append(_close_round(tmp_path, k, kind, delay_begin, delay_record))
            k += 1
    results = race_many(tmp_path, [cmds for _, _, cmds in made], widen)
    outcomes = []
    for (root, got, _), ((b_code, b_out), (r_code, r_out)) in zip(made, results):
        assert r_code == 0, r_out                               # closing is always legal here
        events = ledger.read_events(root)
        closed = [e for e in events if e["type"] == "outreach.closed"]
        claims = [e for e in events if e["type"] == rules.EV_STARTED]
        assert len(closed) == 1
        if b_code == 0:
            assert len(claims) == 1 and claims[0]["seq"] < closed[0]["seq"], (b_out, events)   # begin won, close after
            outcomes.append("begin first")
        else:
            assert b_out.get("code") == "person_closed" and claims == [], b_out
            outcomes.append("closed first")
        assert not ledger.verify_chain(root)
    assert "closed first" in outcomes                           # the delayed begin always loses


@pytest.mark.parametrize("widen", [False, True])
def test_race_two_records_of_one_outcome(tmp_path, widen) -> None:
    rounds, roots = [], []
    for k in range(3):
        root = real_root(tmp_path, f"r{k}")
        got = real_approved(root, rules.LINKEDIN_INVITE, URLS[0])
        aservice.begin(root, got.id, actor="agent:test", clock=models.utc_now)
        aservice.done(root, got.id, actor="agent:test", clock=models.utc_now)
        rounds.append([Cmd("outreach", "record", got.target_id, "--accepted", "--json", *WS(root))] * 2)
        roots.append(root)
    for root, results in zip(roots, race_many(tmp_path, rounds, widen)):
        codes = sorted(d.get("code", "ok") if code else "ok" for code, d in results)
        assert codes == ["ok", "wrong_state"], results
        assert len([e for e in ledger.read_events(root) if e["type"] == "outreach.accepted"]) == 1
