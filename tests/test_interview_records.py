"""Interview round records, debriefs and the `careeros interview` commands (sub-project 4b, Task B), through the real CLI
on invented data.

Covers: `round add|list|show|held|cancel|reschedule` and `debrief` (file bytes, event shape, ids, `--json`, exit codes),
the terminal-job and duplicate rules, file first and event second with rollback, the crash window (orphans, missing and
mismatched records), the record-editing table of spec 6.0, interrupted updates (spec 7.1) with REAL child processes that
die between the file write and the event append, incomplete rounds (spec 6.5), the refusal audit, the clock rules,
real-process races, hostile record files, the reserved prefix and the import isolation.

Time comes from ONE source in-process: `models.utc_now` is patched and the same value is the commands' `now`. Child
processes get their `now` from CAREEROS_TEST_NOW."""

from __future__ import annotations

import dataclasses
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
from helpers import T0, OutreachW, Probe, add_job, full_state, make_workspace, shift

from careeros.core import ids, ledger
from careeros.core.interview import derive, rules, service
from careeros.core.interview import round as rround
from careeros.core.interview.model import Debrief, Interviewer, Round
from careeros.core.ledger import LedgerError
from careeros.core.models import State
from careeros.core.workspace import WorkspaceLock, join_frontmatter, split_frontmatter

NOW2 = shift(T0, hours=1)


class W(OutreachW):
    """The workspace, one clock and the CLI, with interview shortcuts."""

    def job(self, slug: str = "acme-sre", status: State = State.SCREEN) -> str:
        return add_job(self.root, slug, status=status)["id"]

    def add(self, job: str, *, stage: str = "technical", fmt: str = "video", when: str = "2026-10-20",
            interviewers: tuple[str, ...] = ()) -> dict:
        return self.ok(*add_argv(job, stage=stage, fmt=fmt, when=when, interviewers=interviewers))

    def held(self, rid: str, source: str = "user") -> dict:
        return self.ok("interview", "round", "held", rid, "--source", source)

    def cancel(self, rid: str, reason: str = "the role was filled") -> dict:
        return self.ok("interview", "round", "cancel", rid, "--reason", reason)

    def resched(self, rid: str, when: str) -> dict:
        return self.ok("interview", "round", "reschedule", rid, "--when", when)

    def debrief(self, rid: str, **kw) -> dict:
        return self.ok(*debrief_argv(rid, **kw))

    def show(self, rid: str) -> dict:
        return self.ok("interview", "round", "show", rid)

    def rows(self) -> list[dict]:
        return self.ok("interview", "round", "list")["rounds"]

    def rejected(self) -> list[dict]:
        return self.events(rules.EV_REJECTED)

    def loaded(self, now: str | None = None) -> service.Loaded:
        return service.load(self.root, now or self.now)


def add_argv(job: str, *, stage="technical", fmt="video", when="2026-10-20", interviewers=()) -> list[str]:
    argv = ["interview", "round", "add", "--job", job, "--stage", stage, "--format", fmt, "--when", when]
    for i in interviewers:
        argv += ["--interviewer", i]
    return argv


def debrief_argv(rid: str, *, rating="mixed", asked=("How would you shard the queue?",),
                 improve=("Ask about the team earlier",), next_step="wait for the recruiter", next_date=None) -> list[str]:
    argv = ["interview", "debrief", rid, "--rating", rating, "--next-step", next_step]
    for a in asked:
        argv += ["--asked", a]
    for i in improve:
        argv += ["--improve", i]
    if next_date:
        argv += ["--next-date", next_date]
    return argv


@pytest.fixture
def w(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> W:
    monkeypatch.delenv("CAREEROS_WORKSPACE", raising=False)
    return W(make_workspace(tmp_path / "ws"), monkeypatch)


def rel_of(rid: str) -> str:
    return f"rounds/{rid}.md"


def read_round(root: Path, rid: str) -> Round:
    rec, issues = rround.parse((root / rel_of(rid)).read_text(encoding="utf-8"), rel_of(rid))
    assert rec is not None, issues
    return rec


def hand_edit(root: Path, rid: str, **changes) -> None:
    """Rewrite the record with `changes` (no validation: an `id` or `job` edit is written as is)."""
    (root / rel_of(rid)).write_bytes(rround.to_bytes(dataclasses.replace(read_round(root, rid), **changes)))


def codes(w: W, code: str | None = None) -> list[tuple[str, str, str]]:
    return sorted((i.code, i.severity, i.path) for i in w.loaded().record_issues() if code is None or i.code == code)


def rounds_bytes(root: Path) -> dict[str, bytes | None]:
    base = root / "rounds"
    if not base.exists():
        return {}
    return {p.name: (p.read_bytes() if p.is_file() and not p.is_symlink() else None) for p in sorted(base.iterdir())}


def derived(w: W) -> dict:
    """The derived snapshot: the folded flags, every list row and every show (history included)."""
    L = w.loaded()
    return {"flags": L.flags, "incomplete": L.incomplete, "rows": [v.to_dict() for v in service.round_list(w.root, now=w.now)],
            "shows": {rid: service.round_show(w.root, rid, now=w.now).to_dict() for rid in L.flags}}


# =========================================================================================================================
# happy paths
# =========================================================================================================================

def test_round_add_writes_the_file_then_the_event(w: W) -> None:
    job = w.job()
    got = w.add("acme-sre", stage="system_design", fmt="onsite", when="2026-10-20T15:00:00Z",
                interviewers=("Dana Example:Staff Engineer", "Lee Example"))
    rid = got["id"]
    assert ids.is_valid_id("round", rid) and got["outcome"] == "added" and got["path"] == rel_of(rid) and got["job"] == job
    expected = Round(rid, job, "system_design", "onsite", "2026-10-20T15:00:00Z",
                     (Interviewer("Dana Example", "Staff Engineer"), Interviewer("Lee Example", "")))
    data = (w.root / rel_of(rid)).read_bytes()
    assert data == rround.to_bytes(expected)
    import hashlib
    assert got["hash"] == hashlib.sha256(data).hexdigest()
    (event,) = w.events(rules.EV_ROUND_ADDED)
    assert event["entity"] == rid and event["actor"] == "agent:cli" and event["source"] == "cli"
    assert event["artifacts"] == [rel_of(rid), f"sha256:{got['hash']}", f"job:{job}", "when:2026-10-20T15:00:00Z"]
    assert event["action"] == f"add system_design round for {job} on 2026-10-20T15:00:00Z"
    assert read_round(w.root, rid) == expected


def test_text_output_and_exit_codes(w: W) -> None:
    w.job()
    res = w.run(*add_argv("acme-sre"))
    assert res.exit_code == 0 and res.stdout.startswith("added rnd_")
    rid = res.stdout.split()[1]
    res = w.run("interview", "round", "list")
    assert res.exit_code == 0 and rid in res.stdout and "1 round(s)" in res.stdout
    res = w.run("interview", "round", "show", rid)
    assert res.exit_code == 0 and "scheduled" in res.stdout and "2026-10-20" in res.stdout
    assert w.run("interview", "round", "held", rid).exit_code == 0
    # usage errors exit 2: a missing option, an unknown option, no workspace
    assert w.run("interview", "round", "add", "--job", "acme-sre").exit_code == 2
    assert w.run("interview", "round", "held", rid, "--bogus").exit_code == 2
    from typer.testing import CliRunner
    from careeros.cli.main import app
    res = CliRunner().invoke(app, ["interview", "round", "list", "--workspace", str(w.root / "nope")])
    assert res.exit_code == 2
    # a refusal exits 1 and prints one JSON object with the code
    body = w.refused(*add_argv("no-such-job"), code="not_found")
    assert set(body) == {"error", "code"}


def test_job_is_resolved_by_id_slug_or_path(w: W) -> None:
    job = w.job()
    a = w.add(job, when="2026-10-20")
    b = w.add("acme-sre", when="2026-10-21")
    c = w.add("jobs/discovered/acme-sre", when="2026-10-22")
    assert a["job"] == b["job"] == c["job"] == job
    rows = w.ok("interview", "round", "list", "--job", "acme-sre")["rounds"]
    assert [r["id"] for r in rows] == sorted([a["id"], b["id"], c["id"]])
    other = w.job("globex-sre")
    assert w.ok("interview", "round", "list", "--job", other)["rounds"] == []
    w.refused("interview", "round", "list", "--job", "nope", code="not_found")


@pytest.mark.parametrize("argv, why", [
    (["--stage", "lunch"], "stage"),
    (["--format", "carrier_pigeon"], "format"),
    (["--when", "2026-13-01"], "when"),
    (["--when", "2026-10-20T15:00:00+02:00"], "when"),
    (["--when", "tomorrow"], "when"),
    (["--interviewer", ":No Name"], "name"),
    (["--interviewer", "Bad\u202eName:x"], "bidi"),
    (["--interviewer", "x" * 501], "500"),
])
def test_round_add_invalid_input_writes_nothing(w: W, argv, why) -> None:
    w.job()
    base = {"--stage": "technical", "--format": "video", "--when": "2026-10-20"}
    flags = dict(zip(argv[::2], argv[1::2]))
    args = ["interview", "round", "add", "--job", "acme-sre"]
    for k, v in {**base, **{k: v for k, v in flags.items() if k != "--interviewer"}}.items():
        args += [k, v]
    if "--interviewer" in flags:
        args += ["--interviewer", flags["--interviewer"]]
    before = full_state(w.root)
    body = w.refused(*args, code="invalid_input")
    assert why.split()[0] in body["error"] or why in ("bidi", "500")
    assert full_state(w.root) == before


def test_more_than_max_interviewers_is_refused(w: W) -> None:
    w.job()
    many = tuple(f"Person {n}:Engineer" for n in range(rules.MAX_INTERVIEWERS + 1))
    w.refused(*add_argv("acme-sre", interviewers=many), code="invalid_input")
    assert w.add("acme-sre", interviewers=many[:-1])["id"]


def test_terminal_job_refuses_add_but_allows_held_cancel_and_debrief(w: W) -> None:
    w.job("done-co", status=State.REJECTED)
    before = full_state(w.root)
    w.refused(*add_argv("done-co"), code="wrong_state")
    assert full_state(w.root) == before and w.rejected() == []
    job = w.job()
    r1, r2, r3 = (w.add(job, when=d)["id"] for d in ("2026-10-10", "2026-10-11", "2026-10-12"))
    path = w.root / "jobs" / "discovered" / "acme-sre" / "job.md"
    fm, body = split_frontmatter(path.read_text(encoding="utf-8"))
    path.write_text(join_frontmatter({**fm, "status": "REJECTED"}, body), encoding="utf-8")
    w.refused(*add_argv(job, when="2026-10-13"), code="wrong_state")
    assert w.held(r1)["label"] == "held"
    assert w.cancel(r2)["label"] == "cancelled"
    assert w.held(r3)["label"] == "held"
    assert w.debrief(r3)["label"] == "debriefed"


def test_a_duplicate_live_round_is_refused_and_allowed_after_cancel(w: W) -> None:
    job = w.job()
    first = w.add(job)["id"]
    body = w.refused(*add_argv(job), code="duplicate")
    assert first in body["error"]
    assert [e["entity"] for e in w.rejected()] == [first]          # the audit names the existing round
    w.add(job, stage="final")                                      # another stage is another round
    w.add(job, when="2026-10-20T09:00:00Z")                        # so is another time
    w.cancel(first)
    again = w.add(job)["id"]
    assert again != first
    w.held(again)
    w.refused(*add_argv(job), code="duplicate")                    # a held round is still live


def test_held_twice_and_debrief_unless_held_are_refused(w: W) -> None:
    job = w.job()
    rid = w.add(job)["id"]
    w.refused(*debrief_argv(rid), code="wrong_state")
    w.held(rid)
    w.refused("interview", "round", "held", rid, code="wrong_state")
    w.refused("interview", "round", "cancel", rid, "--reason", "x", code="wrong_state")
    w.refused("interview", "round", "reschedule", rid, "--when", "2026-10-30", code="wrong_state")
    w.debrief(rid)
    w.refused(*debrief_argv(rid), code="wrong_state")
    other = w.add(job, when="2026-10-21")["id"]
    w.cancel(other)
    for argv in (("interview", "round", "held", other), ("interview", "round", "cancel", other, "--reason", "x"),
                 ("interview", "round", "reschedule", other, "--when", "2026-10-30"), tuple(debrief_argv(other))):
        w.refused(*argv, code="wrong_state")
    assert [w.show(r)["label"] for r in (rid, other)] == ["debriefed", "cancelled"]


def test_held_and_cancel_events(w: W) -> None:
    job = w.job()
    a, b = w.add(job)["id"], w.add(job, when="2026-10-21")["id"]
    w.held(a, source="observed")
    w.cancel(b, reason="  the recruiter moved the loop  ")
    held, = w.events(rules.EV_HELD)
    cancelled, = w.events(rules.EV_CANCELLED)
    assert (held["entity"], held["source"], held["prev_state"], held["new_state"]) == (a, "observed", "scheduled", "held")
    assert (cancelled["entity"], cancelled["source"], cancelled["reason"]) == (b, "user", "the recruiter moved the loop")
    show = w.show(b)
    assert show["cancel_reason"] == "the recruiter moved the loop" and show["cancelled_at"] == T0
    w.refused("interview", "round", "cancel", a, "--reason", "x", code="wrong_state")
    c = w.add(job, when="2026-10-22")["id"]
    w.refused("interview", "round", "cancel", c, "--reason", "   ", code="invalid_input")
    w.refused("interview", "round", "held", c, "--source", "email", code="invalid_input")
    assert w.run("interview", "round", "cancel", c, "--json").exit_code == 2          # --reason is required


def test_reschedule_rewrites_the_date_keeps_the_body_and_appends_one_event(w: W) -> None:
    job = w.job()
    rid = w.add(job, interviewers=("Dana Example:EM",))["id"]
    path = w.root / rel_of(rid)
    path.write_bytes(path.read_bytes() + b"my own notes\n")
    before = read_round(w.root, rid)
    got = w.resched(rid, "2026-10-27T10:30:00Z")
    assert got["outcome"] == "rescheduled" and got["scheduled_for"] == "2026-10-27T10:30:00Z"
    after = read_round(w.root, rid)
    assert after == dataclasses.replace(before, scheduled_for="2026-10-27T10:30:00Z")
    assert path.read_bytes().endswith(b"my own notes\n")
    (ev,) = w.events(rules.EV_RESCHEDULED)
    assert ev["artifacts"] == ["when:2026-10-27T10:30:00Z"] and ev["action"] == "reschedule to 2026-10-27T10:30:00Z"
    show = w.show(rid)
    assert show["scheduled_for"] == show["recorded_when"] == "2026-10-27T10:30:00Z" and show["date_unrecorded"] is False
    # the same date again is allowed: identical bytes, one more event
    data = path.read_bytes()
    w.resched(rid, "2026-10-27T10:30:00Z")
    assert path.read_bytes() == data and len(w.events(rules.EV_RESCHEDULED)) == 2
    w.refused("interview", "round", "reschedule", rid, "--when", "next week", code="invalid_input")


def test_debrief_writes_the_block_then_the_event(w: W) -> None:
    job = w.job()
    rid = w.add(job)["id"]
    w.held(rid)
    got = w.debrief(rid, rating="strong", asked=("Q one", "Q two"), improve=("slow down",), next_step="send thanks",
                    next_date="2026-10-25")
    assert got["outcome"] == "debriefed" and got["label"] == "debriefed"
    rec = read_round(w.root, rid)
    assert rec.debrief == Debrief("strong", ("Q one", "Q two"), ("slow down",), "send thanks", "2026-10-25")
    (ev,) = w.events(rules.EV_DEBRIEFED)
    data = (w.root / rel_of(rid)).read_bytes()
    import hashlib
    assert ev["artifacts"] == [rel_of(rid), f"sha256:{hashlib.sha256(data).hexdigest()}"] and got["hash"] == ev["artifacts"][1][7:]
    show = w.show(rid)
    assert show["debrief_recorded"] is True and show["debrief"]["rating"] == "strong" and show["unrecorded_debrief"] is None
    assert show["needs_debrief"] is False


@pytest.mark.parametrize("override, why", [
    ({"rating": "great"}, "rating"),
    ({"asked": tuple(f"q{n}" for n in range(rules.MAX_DEBRIEF_ITEMS + 1))}, "asked"),
    ({"improve": tuple(f"i{n}" for n in range(rules.MAX_DEBRIEF_ITEMS + 1))}, "improve"),
    ({"next_step": "   "}, "next"),
    ({"next_date": "2026-10-25T10:00:00Z"}, "next"),
    ({"asked": ("bad\x07bell",)}, "control"),
])
def test_debrief_invalid_input(w: W, override, why) -> None:
    job = w.job()
    rid = w.add(job)["id"]
    w.held(rid)
    before = rounds_bytes(w.root)
    w.refused(*debrief_argv(rid, **override), code="invalid_input")
    assert rounds_bytes(w.root) == before and w.events(rules.EV_DEBRIEFED) == []
    ok = {k: v[:rules.MAX_DEBRIEF_ITEMS] for k, v in override.items()
          if k in ("asked", "improve") and len(v) > rules.MAX_DEBRIEF_ITEMS}
    if ok:
        w.debrief(rid, **ok)


def test_no_list_truncation(w: W) -> None:
    job = w.job()
    made = [w.add(job, when=f"2026-11-{n:02d}")["id"] for n in range(1, 26)]
    assert [r["id"] for r in w.rows()] == sorted(made)


# =========================================================================================================================
# file first, event second; orphans, missing and mismatched records
# =========================================================================================================================

def test_a_failing_append_leaves_no_file_and_no_event(w: W, monkeypatch: pytest.MonkeyPatch) -> None:
    job = w.job()
    sched = w.add(job)["id"]
    held = w.add(job, when="2026-10-21")["id"]
    w.held(held)
    before = {k: v[0] for k, v in full_state(w.root).items()}

    def boom(*a, **k):
        raise OSError("disk full (invented)")

    monkeypatch.setattr(ledger, "append_events", boom)
    assert w.run(*add_argv(job, when="2026-10-30"), "--json").exit_code == 1
    assert w.run("interview", "round", "reschedule", sched, "--when", "2026-10-29", "--json").exit_code == 1
    assert w.run(*debrief_argv(held), "--json").exit_code == 1
    monkeypatch.undo()
    assert {k: v[0] for k, v in full_state(w.root).items()} == before         # the old date and no block: rolled back
    assert read_round(w.root, sched).scheduled_for == "2026-10-20" and read_round(w.root, held).debrief is None


def test_a_failing_append_on_the_first_round_removes_the_new_directory(w: W, monkeypatch: pytest.MonkeyPatch) -> None:
    job = w.job()
    before = {k: v[0] for k, v in full_state(w.root).items()}
    monkeypatch.setattr(ledger, "append_events", lambda *a, **k: (_ for _ in ()).throw(OSError("invented")))
    assert w.run(*add_argv(job), "--json").exit_code == 1
    monkeypatch.undo()
    assert not (w.root / "rounds").exists() and {k: v[0] for k, v in full_state(w.root).items()} == before


def test_a_failing_file_write_leaves_no_event(w: W, monkeypatch: pytest.MonkeyPatch) -> None:
    from careeros.core.memory import store
    job = w.job()
    sched = w.add(job)["id"]
    held = w.add(job, when="2026-10-21")["id"]
    w.held(held)
    before = {k: v[0] for k, v in full_state(w.root).items()}

    def boom(self, path, data):
        raise OSError("disk full (invented)")

    monkeypatch.setattr(store.Transaction, "write", boom)
    for argv in (add_argv(job, when="2026-10-30"), ["interview", "round", "reschedule", sched, "--when", "2026-10-29"],
                 debrief_argv(held)):
        assert w.run(*argv, "--json").exit_code == 1
    monkeypatch.undo()
    assert {k: v[0] for k, v in full_state(w.root).items()} == before


def test_a_crash_window_leaves_an_orphan_that_is_counted_nowhere(w: W) -> None:
    job = w.job()
    orphan = ids.new_id("round")
    (w.root / "rounds").mkdir()
    (w.root / rel_of(orphan)).write_bytes(rround.to_bytes(Round(orphan, job, "technical", "video", "2026-10-20")))
    assert codes(w) == [("INT002", "warning", rel_of(orphan))]
    L = w.loaded()
    assert orphan not in L.flags and orphan not in L.rounds and orphan not in L.incomplete
    assert w.rows() == []
    w.refused("interview", "round", "show", orphan, code="not_found")
    w.refused("interview", "round", "held", orphan, code="not_found")
    assert w.rejected() == []
    assert derive.build_upcoming(L.rounds, L.flags, L.jobs, {}, w.now, 30) == []
    # a retry creates a new round; the orphan stays reported and is never overwritten
    new = w.add(job)["id"]
    assert new != orphan and codes(w) == [("INT002", "warning", rel_of(orphan))]


def test_a_deleted_record_keeps_identity_and_gives_int003(w: W) -> None:
    job = w.job()
    rid = w.add(job)["id"]
    (w.root / rel_of(rid)).unlink()
    assert codes(w) == [("INT003", "warning", rel_of(rid))]
    assert "hand-create" in next(i.message for i in w.loaded().record_issues())
    assert w.loaded().incomplete == {rid: "missing"} and w.loaded().flags[rid].job == job
    (row,) = w.rows()
    assert row["id"] == rid and row["incomplete"] is True and row["record"] == "missing" and row["job"] == job
    assert not (w.root / rel_of(rid)).exists()


@pytest.mark.parametrize("field", ["id", "job"])
def test_a_hand_edited_id_or_job_is_int004_and_feeds_nothing(w: W, field: str) -> None:
    job = w.job()
    rid = w.add(job)["id"]
    w.held(rid)
    value = ids.new_id("round") if field == "id" else w.job("globex-sre")
    hand_edit(w.root, rid, **{field: value})
    assert codes(w) == [("INT004", "error", rel_of(rid))]
    L = w.loaded()
    assert L.rounds[rid] is None and L.incomplete == {rid: "mismatched"} and L.flags[rid].job == job
    show = w.show(rid)
    assert show["job"] == job and show["label"] == "held" and show["stage"] is None and show["record"] == "mismatched"
    nd = derive.build_needs_debrief(L.rounds, L.flags, L.jobs, w.now)
    assert [(r.round_id, r.incomplete) for r in nd.needs_debrief] == [(rid, True)]


def test_a_copy_of_a_record_under_another_name_is_int004(w: W) -> None:
    job = w.job()
    rid = w.add(job)["id"]
    other = ids.new_id("round")
    (w.root / rel_of(other)).write_bytes((w.root / rel_of(rid)).read_bytes())
    assert codes(w) == [("INT004", "error", rel_of(other))]
    assert w.loaded().incomplete == {} and [r["id"] for r in w.rows()] == [rid]


# =========================================================================================================================
# the record-editing table (spec 6.0): what a hand edit may change and what it never does
# =========================================================================================================================

VIEW_NOW = shift(T0, hours=1)


@pytest.fixture
def world(w: W) -> dict:
    job = w.job()
    sched = w.add(job, when="2026-10-20", interviewers=("Dana Example:EM",))["id"]
    held = w.add(job, when="2026-10-05")["id"]
    w.held(held)
    return {"w": w, "job": job, "sched": sched, "held": held}


def views(w: W) -> dict:
    L = service.load(w.root, VIEW_NOW)
    shows = {rid: service.round_show(w.root, rid, now=VIEW_NOW).to_dict() for rid in L.flags}
    return {"shows": shows,
            "upcoming": derive.build_upcoming(L.rounds, L.flags, L.jobs, {}, VIEW_NOW, 30),
            "needs": derive.build_needs_debrief(L.rounds, L.flags, L.jobs, VIEW_NOW)}


def identity(w: W) -> tuple:
    L = service.load(w.root, VIEW_NOW)
    return L.flags, w.ledger_bytes(), {rid: (s["id"], s["job"], s["label"], s["history"]) for rid, s in views(w)["shows"].items()}


EDITS = [
    # name, which round, changes, show keys that change, upcoming changes, needs-debrief changes
    ("stage", "sched", {"stage": "final"}, {"stage"}, True, False),
    ("format", "sched", {"format": "phone"}, {"format"}, True, False),
    ("future date", "sched", {"scheduled_for": "2026-10-22"}, {"scheduled_for", "date_unrecorded"}, True, False),
    ("past date", "sched", {"scheduled_for": "2026-10-01"}, {"scheduled_for", "date_unrecorded", "needs_update"}, True, True),
    ("interviewers", "sched", {"interviewers": (Interviewer("Kim Example", "CTO"),)}, {"interviewers"}, True, False),
    ("body", "sched", {"body": "notes the user typed\n"}, set(), False, False),
    ("unrecorded debrief", "sched", {"debrief": Debrief("weak", (), (), "n/a")}, {"unrecorded_debrief"}, False, False),
    ("held stage", "held", {"stage": "panel"}, {"stage"}, False, True),
    ("held date", "held", {"scheduled_for": "2026-10-04"}, {"scheduled_for", "date_unrecorded"}, False, True),
    ("held debrief", "held", {"debrief": Debrief("strong", ("q",), (), "wait")}, {"unrecorded_debrief"}, False, False),
]


@pytest.mark.parametrize("name, which, changes, keys, up, needs", EDITS, ids=[e[0] for e in EDITS])
def test_the_record_editing_table(world, name, which, changes, keys, up, needs) -> None:
    w = world["w"]
    rid = world[which]
    before_views, before_id = views(w), identity(w)
    hand_edit(w.root, rid, **changes)
    after_views, after_id = views(w), identity(w)
    assert after_id == before_id                       # identity, held/cancelled/debriefed and history never change
    b, a = before_views["shows"][rid], after_views["shows"][rid]
    assert {k for k in a if a[k] != b[k]} == keys
    other = next(r for r in (world["sched"], world["held"]) if r != rid)
    assert after_views["shows"][other] == before_views["shows"][other]
    assert (after_views["upcoming"] != before_views["upcoming"]) is up
    assert (after_views["needs"] != before_views["needs"]) is needs
    assert codes(w, "INT001") == [] and codes(w, "INT004") == []
    if "debrief" in changes:
        assert a["debrief_recorded"] is False and a["debrief"] is None and a["label"] == b["label"]


# =========================================================================================================================
# interrupted updates (spec 7.1): REAL child processes that die between the file write and the event append
# =========================================================================================================================

CRASH_CHILD = r"""
import os, sys
from careeros.core import ledger, models
models.utc_now = lambda: os.environ["CAREEROS_TEST_NOW"]
mode = os.environ["CAREEROS_TEST_CRASH"]
real = ledger.append_events
def crash(root, specs):
    if mode == "after":
        real(root, specs)
    os._exit(137)              # no exception, no rollback, no finally
ledger.append_events = crash
from careeros.cli.main import app
sys.argv = ["careeros", *sys.argv[1:]]
app()
"""


def run_crashing(tmp: Path, root: Path, mode: str, now: str, *argv: str) -> int:
    script = tmp / "crash_child.py"
    script.write_text(CRASH_CHILD)
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(sys.path), "CAREEROS_TEST_NOW": now, "CAREEROS_TEST_CRASH": mode}
    env.pop("CAREEROS_WORKSPACE", None)
    proc = subprocess.run([sys.executable, str(script), *argv, "--workspace", str(root), "--json"], env=env,
                          capture_output=True, timeout=120)
    return proc.returncode


def no_hang(w: W) -> None:
    started = time.monotonic()
    with WorkspaceLock(w.root, timeout=2):
        pass
    assert time.monotonic() - started < 2
    assert ledger.verify_chain(w.root) == []


def test_reschedule_killed_before_the_event_is_detectable_and_recoverable(w: W, tmp_path: Path) -> None:
    job = w.job()
    rid = w.add(job)["id"]
    ledger_before = w.ledger_bytes()
    w.now = NOW2
    assert run_crashing(tmp_path, w.root, "before", NOW2, "interview", "round", "reschedule", rid, "--when", "2026-10-24") == 137
    no_hang(w)
    assert w.ledger_bytes() == ledger_before                                  # no new event
    assert read_round(w.root, rid).scheduled_for == "2026-10-24"              # the new file
    show = w.show(rid)
    assert show["date_unrecorded"] is True and show["scheduled_for"] == "2026-10-24" and show["recorded_when"] == "2026-10-20"
    assert [r["date_unrecorded"] for r in w.rows()] == [True]
    L = w.loaded()
    assert [i.date_unrecorded for i in derive.build_upcoming(L.rounds, L.flags, L.jobs, {}, w.now, 30)] == [True]
    assert [c for c, _, _ in derive.record_issues(L.rounds, L.flags)] == ["INT008"]
    # recovery: re-run with the date the record already holds
    data = (w.root / rel_of(rid)).read_bytes()
    w.resched(rid, "2026-10-24")
    assert (w.root / rel_of(rid)).read_bytes() == data and len(w.events(rules.EV_RESCHEDULED)) == 1
    assert w.show(rid)["date_unrecorded"] is False
    L = w.loaded()
    assert derive.record_issues(L.rounds, L.flags) == []


def test_debrief_killed_before_the_event_fails_safe_and_is_recoverable(w: W, tmp_path: Path) -> None:
    job = w.job()
    rid = w.add(job)["id"]
    w.held(rid)
    ledger_before = w.ledger_bytes()
    w.now = NOW2
    argv = debrief_argv(rid, rating="weak", asked=("first try",))
    assert run_crashing(tmp_path, w.root, "before", NOW2, *argv) == 137
    no_hang(w)
    assert w.ledger_bytes() == ledger_before
    assert read_round(w.root, rid).debrief.rating == "weak"
    show = w.show(rid)
    assert show["debrief_recorded"] is False and show["label"] == "held" and show["needs_debrief"] is True
    assert show["debrief"] is None and show["unrecorded_debrief"]["rating"] == "weak"
    L = w.loaded()
    assert [r.round_id for r in derive.build_needs_debrief(L.rounds, L.flags, L.jobs, w.now).needs_debrief] == [rid]
    assert [c for c, _, _ in derive.record_issues(L.rounds, L.flags)] == ["INT009"]
    # recovery: re-run with different content; the unrecorded block is replaced
    w.debrief(rid, rating="strong", asked=("second try",))
    rec = read_round(w.root, rid)
    assert rec.debrief.rating == "strong" and rec.debrief.questions_asked == ("second try",)
    assert len(w.events(rules.EV_DEBRIEFED)) == 1
    show = w.show(rid)
    assert show["debrief_recorded"] is True and show["label"] == "debriefed" and show["needs_debrief"] is False
    L = w.loaded()
    assert derive.record_issues(L.rounds, L.flags) == []
    assert derive.build_needs_debrief(L.rounds, L.flags, L.jobs, w.now).needs_debrief == ()


def test_a_death_after_the_event_leaves_a_complete_state(w: W, tmp_path: Path) -> None:
    job = w.job()
    a = w.add(job)["id"]
    b = w.add(job, when="2026-10-21")["id"]
    w.held(b)
    w.now = NOW2
    assert run_crashing(tmp_path, w.root, "after", NOW2, "interview", "round", "reschedule", a, "--when", "2026-10-25") == 137
    assert run_crashing(tmp_path, w.root, "after", NOW2, *debrief_argv(b)) == 137
    no_hang(w)
    assert w.show(a)["date_unrecorded"] is False and w.show(a)["scheduled_for"] == "2026-10-25"
    assert w.show(b)["debrief_recorded"] is True and w.show(b)["label"] == "debriefed"
    L = w.loaded()
    assert derive.record_issues(L.rounds, L.flags) == [] and L.fold.errors == () and codes(w) == []


def test_a_death_during_round_add_leaves_an_orphan(w: W, tmp_path: Path) -> None:
    w.job()
    ledger_before = w.ledger_bytes()
    w.now = NOW2
    assert run_crashing(tmp_path, w.root, "before", NOW2, *add_argv("acme-sre")) == 137
    no_hang(w)
    assert w.ledger_bytes() == ledger_before
    (orphan,) = [p.stem for p in (w.root / "rounds").iterdir()]
    assert codes(w) == [("INT002", "warning", rel_of(orphan))] and w.rows() == [] and w.loaded().flags == {}
    assert w.add("acme-sre")["id"] != orphan


# =========================================================================================================================
# incomplete rounds (spec 6.5)
# =========================================================================================================================

def _missing(w: W, rid: str, job: str) -> None:
    (w.root / rel_of(rid)).unlink()


def _invalid(w: W, rid: str, job: str) -> None:
    p = w.root / rel_of(rid)
    p.write_text(p.read_text(encoding="utf-8").replace("stage: technical", "stage: lunch"), encoding="utf-8")


def _edited_id(w: W, rid: str, job: str) -> None:
    hand_edit(w.root, rid, id=ids.new_id("round"))


def _edited_job(w: W, rid: str, job: str) -> None:
    hand_edit(w.root, rid, job="job_" + "z" * 10)


BREAKS = [("missing", _missing, "missing", "INT003"), ("invalid", _invalid, "invalid", "INT001"),
          ("edited id", _edited_id, "mismatched", "INT004"), ("edited job", _edited_job, "mismatched", "INT004")]


@pytest.mark.parametrize("name, brk, status, code", BREAKS, ids=[b[0] for b in BREAKS])
def test_incomplete_rounds(w: W, name, brk, status, code) -> None:
    job = w.job()
    sched, held, to_cancel = (w.add(job, when=d)["id"] for d in ("2026-10-20", "2026-10-05", "2026-10-22"))
    w.held(held)
    for rid in (sched, held, to_cancel):
        brk(w, rid, job)
    assert {c for c, _, _ in codes(w)} == {code}
    L = w.loaded()
    assert L.incomplete == {sched: status, held: status, to_cancel: status}
    rows = {r["id"]: r for r in w.rows()}
    assert set(rows) == {sched, held, to_cancel}
    for r in rows.values():
        assert r["incomplete"] is True and r["record"] == status and r["job"] == job
        assert all(r[k] is None for k in ("stage", "format", "scheduled_for", "interviewers", "debrief", "unrecorded_debrief"))
        assert r["date_unrecorded"] is False and r["needs_update"] is False
    show = w.show(held)
    assert (show["id"], show["job"], show["label"], show["record"]) == (held, job, "held", status)
    assert [h["type"] for h in show["history"]] == [rules.EV_ROUND_ADDED, rules.EV_HELD]
    assert show["needs_debrief"] is True
    nd = derive.build_needs_debrief(L.rounds, L.flags, L.jobs, shift(T0, days=60))
    assert [(r.round_id, r.incomplete) for r in nd.needs_debrief] == [(held, True)] and nd.needs_update == ()
    assert derive.build_upcoming(L.rounds, L.flags, L.jobs, {}, w.now, 60) == []
    files = rounds_bytes(w.root)
    for _ in range(3):
        body = w.refused("interview", "round", "reschedule", sched, "--when", "2026-10-30", code="record_unavailable")
        assert "restore" in body["error"]
        w.refused(*debrief_argv(held), code="record_unavailable")
    assert [e["entity"] for e in w.rejected()] == [sched, held]
    assert rounds_bytes(w.root) == files
    w.cancel(to_cancel)
    w.held(sched)
    assert rounds_bytes(w.root) == files                       # nothing recreated or repaired
    assert w.show(sched)["label"] == "held" and w.show(to_cancel)["label"] == "cancelled"
    # the user hand-creates a record with the same id and job: the round is complete again
    for rid in (sched, held):
        p = w.root / rel_of(rid)
        if p.exists():
            p.unlink()
        p.write_bytes(rround.to_bytes(Round(rid, job, "final", "onsite", "2026-10-05")))
    L = w.loaded()
    assert L.incomplete == {to_cancel: status} and L.rounds[held].stage == "final"
    assert w.debrief(held)["label"] == "debriefed"
    assert w.show(held)["incomplete"] is False


def test_a_held_round_with_no_record_stays_in_the_debrief_queue(w: W) -> None:
    job = w.job()
    rid = w.add(job)["id"]
    w.held(rid)
    (w.root / rel_of(rid)).unlink()
    L = w.loaded()
    assert [r.round_id for r in derive.build_needs_debrief(L.rounds, L.flags, L.jobs, w.now).needs_debrief] == [rid]


# =========================================================================================================================
# the refusal audit
# =========================================================================================================================

@pytest.fixture
def audit_world(w: W) -> dict:
    job = w.job()
    done = w.job("done-co", status=State.WITHDRAWN)
    r = {name: w.add(job, when=d)["id"] for name, d in
         (("sched", "2026-10-20"), ("held", "2026-10-05"), ("cancelled", "2026-10-06"), ("debriefed", "2026-10-04"),
          ("gone", "2026-10-07"))}
    w.held(r["held"])
    w.cancel(r["cancelled"])
    w.held(r["debriefed"])
    w.debrief(r["debriefed"])
    (w.root / rel_of(r["gone"])).unlink()
    return {"w": w, "job": job, "done": done, **r}


UNKNOWN = "rnd_" + "z" * 10
REFUSALS = [
    # name, entity key (None: nothing appended), argv builder, code
    ("held twice", "held", lambda r: ["interview", "round", "held", r["held"]], "wrong_state"),
    ("held cancelled", "cancelled", lambda r: ["interview", "round", "held", r["cancelled"]], "wrong_state"),
    ("cancel debriefed", "debriefed", lambda r: ["interview", "round", "cancel", r["debriefed"], "--reason", "x"], "wrong_state"),
    ("cancel blank reason", "sched", lambda r: ["interview", "round", "cancel", r["sched"], "--reason", " "], "invalid_input"),
    ("bad source", "sched", lambda r: ["interview", "round", "held", r["sched"], "--source", "email"], "invalid_input"),
    ("reschedule held", "held", lambda r: ["interview", "round", "reschedule", r["held"], "--when", "2026-10-30"], "wrong_state"),
    ("reschedule bad date", "sched", lambda r: ["interview", "round", "reschedule", r["sched"], "--when", "soon"], "invalid_input"),
    ("reschedule gone", "gone", lambda r: ["interview", "round", "reschedule", r["gone"], "--when", "2026-10-30"], "record_unavailable"),
    ("debrief scheduled", "sched", lambda r: debrief_argv(r["sched"]), "wrong_state"),
    ("debrief twice", "debriefed", lambda r: debrief_argv(r["debriefed"]), "wrong_state"),
    ("debrief bad rating", "held", lambda r: debrief_argv(r["held"], rating="ok"), "invalid_input"),
    ("debrief too many", "held", lambda r: debrief_argv(r["held"], asked=tuple("abcdefghijk")), "invalid_input"),
    ("add duplicate", "sched", lambda r: add_argv(r["job"]), "duplicate"),
    ("add terminal job", None, lambda r: add_argv(r["done"]), "wrong_state"),
    ("add unknown job", None, lambda r: add_argv("nope"), "not_found"),
    ("add bad stage", None, lambda r: add_argv(r["job"], stage="x"), "invalid_input"),
    ("held unknown", None, lambda r: ["interview", "round", "held", UNKNOWN], "not_found"),
    ("debrief unknown", None, lambda r: debrief_argv(UNKNOWN), "not_found"),
    ("held not an id", None, lambda r: ["interview", "round", "held", "round-1"], "invalid_input"),
]


@pytest.mark.parametrize("name, key, argv, code", REFUSALS, ids=[x[0] for x in REFUSALS])
def test_refusal_audit(audit_world, name, key, argv, code) -> None:
    w = audit_world["w"]
    before, files = derived(w), full_state(w.root)
    n_before = len(w.events())
    for _ in range(20):
        w.refused(*argv(audit_world), code=code)
    assert derived(w) == before
    rej = w.rejected()[-1:] if len(w.events()) > n_before else []
    new = w.events()[n_before:]
    if key is None:
        assert new == []
    else:
        assert [(e["type"], e["entity"]) for e in new] == [(rules.EV_REJECTED, audit_world[key])]
        assert rej[0]["reason"].startswith(f"{code}: ")
    assert {k: v for k, v in full_state(w.root).items() if k != "ledger.jsonl"} == {
        k: v for k, v in files.items() if k != "ledger.jsonl"}


def test_dedupe_keys_on_the_entitys_latest_event_of_any_type(w: W) -> None:
    job = w.job()
    rid = w.add(job)["id"]
    w.held(rid)
    w.refused("interview", "round", "held", rid, code="wrong_state")
    w.append({"type": "note.added", "actor": "user", "entity": rid, "action": "an unrelated note on the same round"})
    w.refused("interview", "round", "held", rid, code="wrong_state")
    w.refused("interview", "round", "held", rid, code="wrong_state")
    assert len(w.rejected()) == 2


def test_the_interview_prefix_is_reserved_and_reasons_are_required(w: W) -> None:
    assert ledger.is_reserved("interview.held") and ledger.is_reserved("interview.anything")
    assert not ledger.is_reserved("interviewx.held")
    res = w.run("ledger", "append", "--type", "interview.x", "--action", "sneaky", "--actor", "agent:claude")
    assert res.exit_code == 2 and "reserved" in res.output
    assert w.events() == []
    for kind in (rules.EV_REJECTED, rules.EV_CANCELLED):
        assert kind in ledger._NEEDS_REASON
        with pytest.raises(LedgerError, match="reason is required"):
            ledger.append_events(w.root, [{"type": kind, "actor": "user", "action": "x", "entity": UNKNOWN}])
        ledger.append_events(w.root, [{"type": kind, "actor": "user", "action": "x", "entity": UNKNOWN, "reason": "r"}])


@pytest.mark.parametrize("argv", [
    lambda r: add_argv("acme-sre", when="2026-10-30"),
    lambda r: ["interview", "round", "held", r],
    lambda r: ["interview", "round", "cancel", r, "--reason", "x"],
    lambda r: ["interview", "round", "reschedule", r, "--when", "2026-10-30"],
    lambda r: debrief_argv(r),
])
def test_an_invalid_actor_is_refused_before_anything_is_written(w: W, argv) -> None:
    w.job()
    rid = w.add("acme-sre")["id"]
    before = full_state(w.root, lock=True)
    body = w.refused(*argv(rid), "--actor", "not an actor", code="invalid_input")
    assert "actor" in body["error"] and full_state(w.root, lock=True) == before


# =========================================================================================================================
# the clock
# =========================================================================================================================

def test_exactly_one_clock_read_per_operation_taken_under_the_lock(w: W) -> None:
    job = w.job()
    rid = w.add(job)["id"]
    other = w.add(job, when="2026-10-21")["id"]
    ops = [
        lambda c: service.round_add(w.root, job=job, stage="final", format="video", when="2026-10-23", interviewers=(),
                                    actor="user", clock=c),
        lambda c: service.round_add(w.root, job=job, stage="final", format="video", when="2026-10-23", interviewers=(),
                                    actor="user", clock=c),                                       # duplicate
        lambda c: service.round_reschedule(w.root, rid, when="2026-10-24", actor="user", clock=c),
        lambda c: service.round_held(w.root, rid, source="user", actor="user", clock=c),
        lambda c: service.round_held(w.root, rid, source="user", actor="user", clock=c),             # wrong_state
        lambda c: service.debrief(w.root, rid, rating="mixed", asked=(), improve=(), next_step="x", actor="user", clock=c),
        lambda c: service.round_cancel(w.root, other, reason="moved", source="user", actor="user", clock=c),
        lambda c: service.round_held(w.root, UNKNOWN, source="user", actor="user", clock=c),        # not_found
    ]
    for n, op in enumerate(ops):
        w.now = shift(T0, hours=n + 1)
        probe = Probe(w.root, shift(T0, hours=n + 1))
        try:
            op(probe)
        except service.Refused:
            pass
        assert probe.calls == 1 and probe.held == [True], n


def test_the_clock_floor_refuses_a_clock_behind_the_latest_event_of_any_type(w: W) -> None:
    job = w.job()
    rid = w.add(job)["id"]
    w.append({"type": "note.added", "actor": "user", "action": "a later unrelated note"})
    behind = shift(T0, seconds=-1)
    before = full_state(w.root)
    for op in (lambda: service.round_held(w.root, rid, source="user", actor="user", clock=lambda: behind),
               lambda: service.round_add(w.root, job=job, stage="final", format="video", when="2026-10-23",
                                         interviewers=(), actor="user", clock=lambda: behind)):
        with pytest.raises(service.Refused) as exc:
            op()
        assert exc.value.code == "clock_skew"
    assert [e["entity"] for e in w.rejected()] == [rid]           # the known round gets the audit; a new add names none
    w.now = T0
    assert {k: v for k, v in full_state(w.root).items() if k != "ledger.jsonl"} == {
        k: v for k, v in before.items() if k != "ledger.jsonl"}
    service.round_held(w.root, rid, source="user", actor="user", clock=lambda: T0)       # equal to the floor is fine


def test_a_broken_chain_is_refused_before_the_clock_is_read(w: W) -> None:
    job = w.job()
    rid = w.add(job)["id"]
    lines = w.ledger_bytes().split(b"\n")
    lines[0] = lines[0].replace(b'"type":', b'"type": ', 1)
    (w.root / "ledger.jsonl").write_bytes(b"\n".join(lines))
    before = full_state(w.root)
    for op in (lambda c: service.round_held(w.root, rid, source="user", actor="user", clock=c),
               lambda c: service.round_add(w.root, job=job, stage="final", format="video", when="2026-10-23",
                                           interviewers=(), actor="user", clock=c),
               lambda c: service.debrief(w.root, rid, rating="mixed", next_step="x", actor="user", clock=c)):
        calls = []
        with pytest.raises(service.Refused) as exc:
            op(lambda: calls.append(1) or T0)
        assert exc.value.code == "ledger_corrupt" and calls == []
    assert full_state(w.root) == before


# =========================================================================================================================
# real-process races
# =========================================================================================================================

RACE_CHILD = r"""
import os, sys, time
from careeros.core import models
models.utc_now = lambda: os.environ["CAREEROS_TEST_NOW"]
from careeros.core.interview import fold
real = fold.fold_rounds
def slow(*a, **k):
    out = real(*a, **k)
    time.sleep(0.3)            # widen the window between reading the ledger and appending
    return out
fold.fold_rounds = slow
from careeros.cli.main import app
ready, go = sys.argv[1], sys.argv[2]
open(ready, "w").close()
deadline = time.monotonic() + 60
while not os.path.exists(go) and time.monotonic() < deadline:
    time.sleep(0.001)
sys.argv = ["careeros", *sys.argv[3:]]
app()
"""


def race(tmp: Path, root: Path, now: str, *argvs: list[str]) -> list[tuple[int, dict]]:
    script = tmp / "race_child.py"
    script.write_text(RACE_CHILD)
    go = tmp / f"go-{time.monotonic_ns()}"
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(sys.path), "CAREEROS_TEST_NOW": now}
    env.pop("CAREEROS_WORKSPACE", None)
    procs = []
    for n, argv in enumerate(argvs):
        ready = tmp / f"ready-{go.name}-{n}"
        procs.append((ready, subprocess.Popen([sys.executable, str(script), str(ready), str(go), *argv, "--workspace",
                                               str(root), "--json"], env=env, stdout=subprocess.PIPE,
                                              stderr=subprocess.PIPE)))
    deadline = time.monotonic() + 60
    while not all(r.exists() for r, _ in procs):
        assert time.monotonic() < deadline, "children did not start"
        time.sleep(0.01)
    go.write_text("go")
    out = []
    for _, proc in procs:
        stdout, stderr = proc.communicate(timeout=120)
        try:
            out.append((proc.returncode, json.loads(stdout)))
        except ValueError:
            out.append((proc.returncode, {"raw": stdout.decode() + stderr.decode()}))
    return out


def test_two_held_on_one_round_exactly_one_succeeds(w: W, tmp_path: Path) -> None:
    rid = w.add(w.job())["id"]
    got = race(tmp_path, w.root, NOW2, ["interview", "round", "held", rid], ["interview", "round", "held", rid])
    assert sorted(code for code, _ in got) == [0, 1], got
    assert [b.get("code") for c, b in got if c == 1] == ["wrong_state"]
    assert len(w.events(rules.EV_HELD)) == 1 and ledger.verify_chain(w.root) == []


def test_two_debriefs_on_one_held_round_exactly_one_succeeds(w: W, tmp_path: Path) -> None:
    rid = w.add(w.job())["id"]
    w.held(rid)
    got = race(tmp_path, w.root, NOW2, debrief_argv(rid, rating="strong"), debrief_argv(rid, rating="weak"))
    assert sorted(code for code, _ in got) == [0, 1], got
    assert len(w.events(rules.EV_DEBRIEFED)) == 1
    w.now = NOW2
    assert w.show(rid)["debrief_recorded"] is True
    L = w.loaded()
    assert derive.record_issues(L.rounds, L.flags) == []          # the recorded block is the one on disk


def test_two_adds_of_the_same_round_exactly_one_succeeds(w: W, tmp_path: Path) -> None:
    w.job()
    got = race(tmp_path, w.root, NOW2, add_argv("acme-sre"), add_argv("acme-sre"))
    assert sorted(code for code, _ in got) == [0, 1], got
    assert [b.get("code") for c, b in got if c == 1] == ["duplicate"]
    assert len(w.events(rules.EV_ROUND_ADDED)) == 1 and len(list((w.root / "rounds").iterdir())) == 1


# =========================================================================================================================
# hostile record files
# =========================================================================================================================

HOSTILE_CHILD = r"""
import json, os, sys
from careeros.core import models
models.utc_now = lambda: os.environ["CAREEROS_TEST_NOW"]
from typer.testing import CliRunner
from careeros.cli.main import app
root, rid, job = sys.argv[1], sys.argv[2], sys.argv[3]
runner = CliRunner()
out = []
for argv in (["interview", "round", "list"], ["interview", "round", "show", rid],
             ["interview", "round", "reschedule", rid, "--when", "2026-10-30"],
             ["interview", "debrief", rid, "--rating", "mixed", "--next-step", "x"],
             ["interview", "round", "held", rid],
             ["interview", "round", "add", "--job", job, "--stage", "final", "--format", "phone", "--when", "2026-10-29"]):
    res = runner.invoke(app, [*argv, "--workspace", root, "--json"])
    out.append([res.exit_code, json.loads(res.stdout)])
print(json.dumps(out))
"""

ANCHOR_BOMB = ("---\na: &a [x, x, x, x, x, x, x, x, x]\nb: &b [*a, *a, *a, *a, *a, *a, *a, *a, *a]\n"
               "c: &c [*b, *b, *b, *b, *b, *b, *b, *b, *b]\nd: &d [*c, *c, *c, *c, *c, *c, *c, *c, *c]\n"
               "e: [*d, *d, *d, *d, *d, *d, *d, *d, *d]\n---\n")


def _hostile(kind: str, path: Path, valid: bytes) -> None:
    path.unlink()
    if kind == "symlink":
        target = path.parent.parent / "elsewhere.md"
        target.write_bytes(valid)
        path.symlink_to(target)
    elif kind == "directory":
        path.mkdir()
    elif kind == "fifo":
        os.mkfifo(path)
    elif kind == "non-utf8":
        path.write_bytes(b"---\nschema: 1\nid: \xff\xfe\n---\n")
    elif kind == "5 MB":
        path.write_bytes(valid + b"x" * (5 << 20))
    elif kind == "binary":
        path.write_bytes(bytes(range(256)) * 4)
    elif kind == "python tag":
        path.write_bytes(b"---\nschema: !!python/object/apply:os.system ['echo pwned']\n---\n")
    elif kind == "anchor bomb":
        path.write_text(ANCHOR_BOMB, encoding="utf-8")


@pytest.mark.parametrize("kind", ["symlink", "directory", "fifo", "non-utf8", "5 MB", "binary", "python tag", "anchor bomb"])
def test_hostile_record_files_never_crash_or_hang_and_are_reported(w: W, tmp_path: Path, kind: str) -> None:
    job = w.job()
    rid = w.add(job)["id"]
    w.held(rid)
    path = w.root / rel_of(rid)
    _hostile(kind, path, path.read_bytes())
    script = tmp_path / "hostile_child.py"
    script.write_text(HOSTILE_CHILD)
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(sys.path), "CAREEROS_TEST_NOW": NOW2}
    env.pop("CAREEROS_WORKSPACE", None)
    proc = subprocess.run([sys.executable, str(script), str(w.root), rid, job], env=env, capture_output=True, timeout=120)
    assert proc.returncode == 0, proc.stderr.decode()[-2000:]
    results = json.loads(proc.stdout)
    (lc, listed), (sc, shown), (rc, resch), (dc, deb), (hc, held), (ac, added) = results
    assert lc == 0 and any(r["id"] == rid and r["incomplete"] and r["record"] == "invalid" for r in listed["rounds"])
    assert sc == 0 and shown["record"] == "invalid" and shown["label"] == "held"
    assert (rc, resch["code"]) == (1, "wrong_state")
    assert (dc, deb["code"]) == (1, "record_unavailable")
    assert (hc, held["code"]) == (1, "wrong_state")
    assert ac == 0 and added["outcome"] == "added"
    assert ("INT001", "error", rel_of(rid)) in codes(w)
    if kind == "symlink":
        assert path.is_symlink()                      # never followed, never replaced


# =========================================================================================================================
# isolation
# =========================================================================================================================

def test_the_interview_package_imports_nothing_from_outreach() -> None:
    code = ("import sys, careeros.core.interview.service, careeros.cli.interview; "
            "print(sorted(m for m in sys.modules if m.startswith('careeros.core.outreach')))")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60, check=True).stdout
    assert out.strip() == "[]"
    import ast
    for f in Path(service.__file__).parent.glob("*.py"):
        for node in ast.walk(ast.parse(f.read_text(encoding="utf-8"))):
            names = ([a.name for a in node.names] if isinstance(node, ast.Import) else
                     [node.module or ""] if isinstance(node, ast.ImportFrom) else [])
            assert not any("outreach" in n for n in names), (f, names)


def test_a_workspace_without_rounds_is_unchanged_by_reads(w: W) -> None:
    w.job()
    before = full_state(w.root, lock=True)
    assert w.rows() == [] and w.loaded().record_issues() == []
    assert full_state(w.root, lock=True) == before


def test_list_and_show_leave_the_workspace_byte_identical(world) -> None:
    w = world["w"]
    (w.root / rel_of(world["held"])).unlink()
    before = full_state(w.root, lock=True)
    w.rows()
    w.show(world["sched"])
    w.show(world["held"])
    w.ok("interview", "round", "list", "--job", "acme-sre")
    w.refused("interview", "round", "show", UNKNOWN, code="not_found")
    assert full_state(w.root, lock=True) == before
