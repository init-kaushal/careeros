"""The interview views (`interview upcoming`, `interview needs-debrief`), the prep status of a job and the `INT` section of
`careeros validate` (sub-project 4b, Task C), through the real CLI on invented data.

Covers: the views equal the pure derivations on the same data (text and `--json`), read-only proof by a whole-workspace
byte-identical snapshot (mtimes included, no lock file created), the overdue and `--days` boundaries, the terminal-job
rules, prep status against real `careeros check --record` runs (newline, CRLF and BOM variants hashed exactly as `check`
does), hostile prep files (symlink, FIFO, directory, oversized, non-UTF-8), the shared `draft.checked` wording constants,
every `INT` code positive and negative, hostile ledgers and records, the one clock read per `validate`, no truncation and
incomplete rounds in the views.

Time comes from ONE source in-process: `models.utc_now` is patched and the same value is the commands' `now`. Child
processes get their `now` from CAREEROS_TEST_NOW."""

from __future__ import annotations

import dataclasses
import json
import os
import random
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from helpers import T0, full_state, make_career, make_workspace, shift
from test_interview_records import W, debrief_argv, hand_edit, read_round, rel_of, run_crashing

from careeros.cli import check as cli_check
from careeros.core import draft_check, ids, ledger, models, validation
from careeros.core.interview import derive, prep, rules, service
from careeros.core.interview import round as rround
from careeros.core.interview.model import Debrief, Round
from careeros.core.models import State
from careeros.core.workspace import join_frontmatter, split_frontmatter

GOOD = "Reduced AWS costs by 35% using Kafka-based pipelines at Acme Corp."
GOOD2 = "Built an internal developer platform in Go used by 120 engineers at Acme Corp."
BAD = "Reduced AWS costs by 50% using Kafka. I worked at FakeCorp."
MIB = 1 << 20


@pytest.fixture
def w(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> W:
    monkeypatch.delenv("CAREEROS_WORKSPACE", raising=False)
    return W(make_workspace(tmp_path / "ws"), monkeypatch)


@pytest.fixture
def cw(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> W:
    """A workspace holding the small invented career memory (written on the one test clock), so `careeros check` can
    pass."""
    monkeypatch.delenv("CAREEROS_WORKSPACE", raising=False)
    w = W(make_workspace(tmp_path / "ws"), monkeypatch)
    make_career(w.root)
    return w


def job_dir(w: W, slug: str = "acme-sre") -> Path:
    return w.root / "jobs" / "discovered" / slug


def set_status(w: W, slug: str, status: State) -> None:
    path = job_dir(w, slug) / "job.md"
    fm, body = split_frontmatter(path.read_text(encoding="utf-8"))
    fm["status"] = status.value
    path.write_text(join_frontmatter(fm, body), encoding="utf-8")


def upcoming(w: W, *extra: str) -> list[dict]:
    return w.ok("interview", "upcoming", *extra)["rounds"]


def needs(w: W) -> dict:
    return w.ok("interview", "needs-debrief")


def ids_of(rows: list[dict]) -> list[str]:
    return [r["round_id"] for r in rows]


def jsonable(value) -> object:
    return json.loads(json.dumps(value))


def prep_map(w: W, events: list) -> dict[str, str]:
    out = {}
    for path in sorted((w.root / "jobs" / "discovered").glob("*/job.md")):
        fm, _ = split_frontmatter(path.read_text(encoding="utf-8"))
        out[fm["id"]] = prep.prep_status(w.root, path.parent, fm["id"], events)
    return out


def int_issues(w: W, now: str | None = None) -> list[tuple[str, str, str]]:
    return sorted((i.code, i.severity, i.path) for i in validation.validate_workspace(w.root, now or w.now)
                  if i.code.startswith("INT"))


def int_messages(w: W, code: str) -> list[str]:
    return [i.message for i in validation.validate_workspace(w.root, w.now) if i.code == code]


def without_lock(w: W) -> None:
    lock = w.root / ".careeros" / "lock"
    if lock.exists():
        lock.unlink()


def check_file(w: W, path: Path, *extra: str) -> int:
    return w.run("check", str(path), "--record", *extra).exit_code


def checked_events(w: W) -> list[dict]:
    return w.events(draft_check.EVENT_TYPE)


# =========================================================================================================================
# the views equal the pure derivations, end to end
# =========================================================================================================================

def _mixed(w: W) -> dict[str, str]:
    a = w.job("acme-sre")
    w.job("beta-sre")
    r = {}
    r["soon"] = w.add(a, when="2026-10-08")["id"]
    r["sooner"] = w.add(a, stage="system_design", when="2026-10-07T09:00:00Z",
                        interviewers=("Dana Example:Staff Engineer", "Lee Example"))["id"]
    r["far"] = w.add(a, stage="final", when="2026-10-30")["id"]
    r["late"] = w.add("beta-sre", when="2026-10-01")["id"]
    r["held"] = w.add("beta-sre", stage="panel", when="2026-10-02")["id"]
    r["cancelled"] = w.add(a, stage="hiring_manager", when="2026-10-09")["id"]
    r["debriefed"] = w.add(a, stage="recruiter_screen", when="2026-10-03")["id"]
    w.now = shift(T0, minutes=1)
    w.held(r["held"])
    w.cancel(r["cancelled"])
    w.held(r["debriefed"])
    w.debrief(r["debriefed"])
    (job_dir(w) / "interview-prep.md").write_text("# Prep\n", encoding="utf-8")
    w.now = shift(T0, hours=2)
    return r


def test_views_equal_the_pure_functions_json_and_text(w: W) -> None:
    r = _mixed(w)
    L = service.load(w.root, w.now)
    want_up = derive.build_upcoming(L.rounds, L.flags, L.jobs, prep_map(w, L.events), w.now, rules.UPCOMING_DEFAULT_DAYS)
    want_nd = derive.build_needs_debrief(L.rounds, L.flags, L.jobs, w.now)
    got = w.ok("interview", "upcoming")
    assert got["rounds"] == jsonable([dataclasses.asdict(i) for i in want_up])
    assert got["days"] == 14 and got["now"] == w.now
    assert ids_of(got["rounds"]) == [r["sooner"], r["soon"]]
    assert [x["prep"] for x in got["rounds"]] == ["drafted", "drafted"]
    assert got["rounds"][0]["interviewers"] == [{"name": "Dana Example", "title": "Staff Engineer"},
                                                {"name": "Lee Example", "title": ""}]
    nd = needs(w)
    assert nd["needs_debrief"] == jsonable([dataclasses.asdict(x) for x in want_nd.needs_debrief])
    assert nd["needs_update"] == jsonable([dataclasses.asdict(x) for x in want_nd.needs_update])
    assert ids_of(nd["needs_debrief"]) == [r["held"]] and ids_of(nd["needs_update"]) == [r["late"]]
    # the service functions are the same data
    assert service.upcoming(w.root, now=w.now) == want_up
    assert service.needs_debrief(w.root, now=w.now) == want_nd
    # text
    text = w.run("interview", "upcoming")
    assert text.exit_code == 0, text.output
    lines = text.stdout.splitlines()
    assert r["sooner"] in lines[0] and r["soon"] in lines[1] and "prep: drafted" in lines[0]
    assert "Dana Example (Staff Engineer)" in lines[0]
    assert lines[-1] == "2 upcoming round(s) in the next 14 day(s)"
    text = w.run("interview", "needs-debrief")
    assert text.exit_code == 0, text.output
    out = text.stdout
    assert "Needs debrief (1)" in out and "Needs update (1)" in out
    assert out.index(r["held"]) < out.index("Needs update") < out.index(r["late"])


def test_an_empty_workspace_gives_empty_views(w: W) -> None:
    assert upcoming(w) == [] and needs(w) == {"now": w.now, "needs_debrief": [], "needs_update": []}
    res = w.run("interview", "upcoming")
    assert res.exit_code == 0 and res.stdout.splitlines() == ["0 upcoming round(s) in the next 14 day(s)"]
    res = w.run("interview", "needs-debrief")
    assert res.exit_code == 0 and "Needs debrief (0)" in res.stdout and "Needs update (0)" in res.stdout


def test_views_and_round_list_show_are_read_only(w: W) -> None:
    r = _mixed(w)
    without_lock(w)
    before = full_state(w.root, lock=True)
    for argv in (["interview", "upcoming"], ["interview", "upcoming", "--days", "60"], ["interview", "needs-debrief"],
                 ["interview", "round", "list"], ["interview", "round", "show", r["held"]]):
        for extra in ((), ("--json",)):
            res = w.run(*argv, *extra)
            assert res.exit_code == 0, res.output
            assert full_state(w.root, lock=True) == before, argv
    assert not (w.root / ".careeros" / "lock").exists()


def test_the_clock_moves_a_round_from_upcoming_to_needs_update_exactly_at_the_boundary(w: W) -> None:
    job = w.job()
    day = w.add(job, when="2026-10-08")["id"]
    at = w.add(job, stage="final", when="2026-10-08T15:00:00Z")["id"]
    for now, up, late in (("2026-10-08T14:59:59Z", [day, at], []),
                          ("2026-10-08T15:00:00Z", [day, at], []),
                          ("2026-10-08T15:00:01Z", [day], [at]),
                          ("2026-10-08T23:59:59Z", [day], [at]),
                          ("2026-10-09T00:00:00Z", [], [day, at]),
                          ("2026-10-09T00:00:01Z", [], [day, at])):
        w.now = now
        assert ids_of(upcoming(w)) == up, now
        late_rows = needs(w)["needs_update"]
        assert sorted(ids_of(late_rows)) == sorted(late), now
    # oldest scheduled_for first: the date-only one is at its UTC midnight, before 15:00
    assert ids_of(needs(w)["needs_update"]) == [day, at]


def test_days_boundary_is_inclusive_and_bounded(w: W) -> None:
    job = w.job()
    exact = w.add(job, when="2026-10-09T10:00:00Z")["id"]           # T0 + 3 days exactly
    after = w.add(job, stage="final", when="2026-10-09T10:00:01Z")["id"]
    today = w.add(job, stage="panel", when="2026-10-06")["id"]
    later_today = w.add(job, stage="other", when="2026-10-06T18:00:00Z")["id"]
    assert ids_of(upcoming(w, "--days", "3")) == [today, later_today, exact]
    assert ids_of(upcoming(w, "--days", "2")) == [today, later_today]
    assert ids_of(upcoming(w, "--days", "0")) == [today]             # a date-only round today; not a datetime later
    assert ids_of(upcoming(w, "--days", "3650")) == [today, later_today, exact, after]
    for bad in ("-1", "3651", "99999999999", "abc"):
        res = w.run("interview", "upcoming", "--days", bad)
        assert res.exit_code == 2, (bad, res.output)
    res = w.run("interview", "upcoming", "--days", "3651", "--json")
    assert res.exit_code == 2 and "error" in json.loads(res.stdout)


def test_terminal_job_rules_in_both_groups(w: W) -> None:
    job = w.job()
    late = w.add(job, when="2026-10-01")["id"]
    held = w.add(job, stage="final", when="2026-10-02")["id"]
    w.held(held)
    assert ids_of(needs(w)["needs_update"]) == [late] and ids_of(needs(w)["needs_debrief"]) == [held]
    for status in (State.REJECTED, State.WITHDRAWN, State.ACCEPTED):
        set_status(w, "acme-sre", status)
        nd = needs(w)
        assert nd["needs_update"] == [], status                     # stale by definition
        assert ids_of(nd["needs_debrief"]) == [held], status        # the lessons are still worth capturing


# =========================================================================================================================
# prep status
# =========================================================================================================================

def _prep_of(w: W, job: str, slug: str = "acme-sre") -> str:
    return prep.prep_status(w.root, job_dir(w, slug), job, ledger.read_events(w.root))


def test_prep_none_drafted_checked_with_a_real_check(cw: W) -> None:
    job = cw.job()
    rid = cw.add(job, when="2026-10-08")["id"]
    path = job_dir(cw) / "interview-prep.md"
    assert _prep_of(cw, job) == "none" and [x["prep"] for x in upcoming(cw)] == ["none"]
    path.write_text(GOOD, encoding="utf-8")
    assert _prep_of(cw, job) == "drafted" and [x["prep"] for x in upcoming(cw)] == ["drafted"]
    assert check_file(cw, path, "--against", job) == 0
    assert _prep_of(cw, job) == "checked" and [x["prep"] for x in upcoming(cw)] == ["checked"]
    assert upcoming(cw)[0]["round_id"] == rid
    # editing the file afterwards goes back to drafted
    path.write_text(GOOD + "\n\nAnd one more line.", encoding="utf-8")
    assert _prep_of(cw, job) == "drafted"
    # putting the checked text back makes it checked again (the hash is of the current text)
    path.write_text(GOOD, encoding="utf-8")
    assert _prep_of(cw, job) == "checked"


def test_a_failed_check_another_job_and_different_text_never_count(cw: W, tmp_path: Path) -> None:
    job = cw.job()
    other = cw.job("beta-sre")
    path = job_dir(cw) / "interview-prep.md"
    # a failed check of the exact text
    path.write_text(BAD, encoding="utf-8")
    assert check_file(cw, path, "--against", job) == 1
    assert checked_events(cw)[-1]["action"].startswith(draft_check.FAILED)
    assert _prep_of(cw, job) == "drafted"
    # a passing check of the exact text for ANOTHER job, and with no job at all
    path.write_text(GOOD, encoding="utf-8")
    assert check_file(cw, path, "--against", other) == 0
    assert check_file(cw, path) == 0
    assert _prep_of(cw, job) == "drafted"
    # a passing check for this job of DIFFERENT text
    elsewhere = tmp_path / "other.md"
    elsewhere.write_text(GOOD2, encoding="utf-8")
    assert check_file(cw, elsewhere, "--against", job) == 0
    assert _prep_of(cw, job) == "drafted"
    # the other job's own prep file with that text is checked
    (job_dir(cw, "beta-sre") / "interview-prep.md").write_text(GOOD, encoding="utf-8")
    assert _prep_of(cw, other, "beta-sre") == "checked"


@pytest.mark.parametrize("raw", [
    GOOD.encode(),
    (GOOD + "\n").encode(),
    (GOOD + "\n\n\n").encode(),
    (GOOD + "  \n").encode(),
    (GOOD + "\r\n").encode(),
    ("First line.\r\n" + GOOD + "\r\n").encode(),
    ("First line.\r" + GOOD + "\r").encode(),
    ("\ufeff" + GOOD + "\n").encode(),
], ids=["plain", "newline", "newlines", "trailing-space", "crlf", "crlf-two", "cr", "bom"])
def test_prep_hashes_exactly_as_check_does(cw: W, raw: bytes) -> None:
    job = cw.job()
    path = job_dir(cw) / "interview-prep.md"
    path.write_bytes(raw)
    code = check_file(cw, path, "--against", job)
    (event,) = checked_events(cw)
    # the real check recorded the hash of the text it read; the reader computes the same hash from the same bytes
    assert event["artifacts"] == [f"sha256:{draft_check.draft_sha256(draft_check.decode_draft(raw))}"]
    assert event["artifacts"] == [f"sha256:{draft_check.draft_sha256(Path(path).read_text(encoding='utf-8'))}"]
    assert _prep_of(cw, job) == ("checked" if code == 0 else "drafted")
    assert code == 0 or b"\r" not in raw            # the newline style never changes the check's verdict here


def test_a_crlf_file_is_checked_by_a_check_of_its_lf_text(cw: W, tmp_path: Path) -> None:
    job = cw.job()
    lf = tmp_path / "lf.md"
    lf.write_bytes(("Intro.\n" + GOOD + "\n").encode())
    assert check_file(cw, lf, "--against", job) == 0
    (job_dir(cw) / "interview-prep.md").write_bytes(("Intro.\r\n" + GOOD + "\r\n").encode())
    assert _prep_of(cw, job) == "checked"           # `check` reads text with universal newlines; so does the reader
    (job_dir(cw) / "interview-prep.md").write_bytes(("\ufeffIntro.\n" + GOOD + "\n").encode())
    assert _prep_of(cw, job) == "drafted"           # a BOM is text to `check`, so it is different text


def test_hostile_prep_files_are_none(cw: W, tmp_path: Path) -> None:
    job = cw.job()
    cw.add(job, when="2026-10-08")
    path = job_dir(cw) / "interview-prep.md"
    target = tmp_path / "real-prep.md"
    target.write_text(GOOD, encoding="utf-8")
    assert check_file(cw, target, "--against", job) == 0
    # symlink to a checked file
    path.symlink_to(target)
    assert _prep_of(cw, job) == "none" and [x["prep"] for x in upcoming(cw)] == ["none"]
    path.unlink()
    # a directory
    path.mkdir()
    assert _prep_of(cw, job) == "none" and [x["prep"] for x in upcoming(cw)] == ["none"]
    path.rmdir()
    # non-UTF-8
    path.write_bytes(b"\xff\xfe not text")
    assert _prep_of(cw, job) == "none"
    # the 1 MiB boundary
    path.write_bytes(b"a" * MIB)
    assert _prep_of(cw, job) == "drafted"
    path.write_bytes(b"a" * (MIB + 1))
    assert _prep_of(cw, job) == "none" and [x["prep"] for x in upcoming(cw)] == ["none"]
    # a valid file again
    path.write_text(GOOD, encoding="utf-8")
    assert _prep_of(cw, job) == "checked"


VIEW_CHILD = r"""
import os, sys
from careeros.core import models
models.utc_now = lambda: os.environ["CAREEROS_TEST_NOW"]
from careeros.cli.main import app
sys.argv = ["careeros", *sys.argv[1:]]
app()
"""


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="needs FIFOs")
def test_fifo_prep_and_record_files_never_hang_the_views_or_validate(w: W, tmp_path: Path) -> None:
    job = w.job()
    rid = w.add(job, when="2026-10-08")["id"]
    os.mkfifo(job_dir(w) / "interview-prep.md")
    os.mkfifo(w.root / "rounds" / "rnd_aaaaaaaaaa.md")
    script = tmp_path / "view_child.py"
    script.write_text(VIEW_CHILD)
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(sys.path), "CAREEROS_TEST_NOW": w.now}
    env.pop("CAREEROS_WORKSPACE", None)

    def run(*argv: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(script), *argv, "--workspace", str(w.root)], env=env,
                              capture_output=True, timeout=60)

    got = run("interview", "upcoming", "--json")
    assert got.returncode == 0, got.stderr
    assert [(x["round_id"], x["prep"]) for x in json.loads(got.stdout)["rounds"]] == [(rid, "none")]
    assert run("interview", "needs-debrief", "--json").returncode == 0
    got = run("validate")
    assert b"INT001" in got.stdout + got.stderr


# =========================================================================================================================
# the shared draft.checked wording
# =========================================================================================================================

def test_the_wording_constants_keep_the_old_text() -> None:
    assert draft_check.EVENT_TYPE == "draft.checked"
    assert draft_check.PASSED == "checked draft: passed" and draft_check.FAILED == "checked draft: failed"


def test_the_producer_uses_the_shared_constants(cw: W, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    job = cw.job()
    draft = tmp_path / "d.md"
    draft.write_text(GOOD, encoding="utf-8")
    assert check_file(cw, draft, "--against", job) == 0
    action = checked_events(cw)[-1]["action"]
    assert re.fullmatch(re.escape(draft_check.PASSED) + r" \(\d+ supported, \d+ need review, \d+ not evaluated\)", action)
    draft.write_text(BAD, encoding="utf-8")
    assert check_file(cw, draft, "--against", job) == 1
    assert checked_events(cw)[-1]["action"].startswith(draft_check.FAILED + " (")
    # change the wording in one place and both the producer and the reader follow it
    monkeypatch.setattr(draft_check, "PASSED", "checked draft: ok")
    draft.write_text(GOOD, encoding="utf-8")
    shutil.copy(draft, job_dir(cw) / "interview-prep.md")
    assert check_file(cw, draft, "--against", job) == 0
    assert checked_events(cw)[-1]["action"].startswith("checked draft: ok (")
    assert _prep_of(cw, job) == "checked"
    assert "checked draft" not in Path(cli_check.__file__).read_text(encoding="utf-8")


# =========================================================================================================================
# validate: the INT section
# =========================================================================================================================

def test_a_healthy_interview_workspace_has_no_int_findings(w: W) -> None:
    job = w.job()
    a = w.add(job, when="2026-10-20")["id"]
    b = w.add(job, stage="final", when="2026-10-21")["id"]
    c = w.add(job, stage="panel", when="2026-10-22")["id"]
    w.held(b)
    w.held(c)
    w.debrief(c)
    w.resched(a, "2026-10-23")
    assert int_issues(w) == []


def test_int001_record_shape(w: W) -> None:
    job = w.job()
    rid = w.add(job)["id"]
    assert int_issues(w) == []
    path = w.root / rel_of(rid)
    path.write_text(path.read_text(encoding="utf-8").replace("stage: technical", "stage: chat"), encoding="utf-8")
    assert int_issues(w) == [("INT001", "error", rel_of(rid))]
    path.write_text("not a record\n", encoding="utf-8")
    assert int_issues(w) == [("INT001", "error", rel_of(rid))]


def test_int002_orphan_record(w: W) -> None:
    job = w.job()
    w.add(job)
    orphan = ids.new_id("round")
    (w.root / rel_of(orphan)).write_bytes(rround.to_bytes(Round(orphan, job, "final", "video", "2026-10-08")))
    assert int_issues(w) == [("INT002", "warning", rel_of(orphan))]
    assert upcoming(w, "--days", "60") and orphan not in ids_of(upcoming(w, "--days", "60"))   # feeds nothing


def test_int003_missing_record_message(w: W) -> None:
    job = w.job()
    rid = w.add(job)["id"]
    (w.root / rel_of(rid)).unlink()
    assert int_issues(w) == [("INT003", "warning", rel_of(rid))]
    (msg,) = int_messages(w, "INT003")
    assert "restore the file from a backup or version control" in msg
    assert f"hand-create {rel_of(rid)} with the same id and job" in msg


def test_int004_mismatched_record_and_one_id_under_two_jobs(w: W) -> None:
    job = w.job()
    other = w.job("beta-sre")
    rid = w.add(job, when="2026-10-08")["id"]
    hand_edit(w.root, rid, job=other)
    assert int_issues(w) == [("INT004", "error", rel_of(rid))]
    assert upcoming(w) == []                                        # a mismatched record feeds no view
    hand_edit(w.root, rid, job=job)
    assert int_issues(w) == [] and ids_of(upcoming(w)) == [rid]
    # a second round_added for the same id under another job
    w.append({"type": rules.EV_ROUND_ADDED, "actor": "user", "entity": rid, "source": "cli",
              "action": "forged", "artifacts": [rel_of(rid), "sha256:" + "0" * 64, f"job:{other}", "when:2026-10-08"]})
    assert int_issues(w) == [("INT004", "error", "ledger.jsonl"), ("INT005", "error", "ledger.jsonl")]


def test_int005_illegal_unknown_and_malformed_events(w: W) -> None:
    job = w.job()
    rid = w.add(job)["id"]
    w.held(rid)
    assert int_issues(w) == []
    w.append({"type": rules.EV_HELD, "actor": "user", "entity": rid, "source": "user", "action": "again"})
    assert int_issues(w) == [("INT005", "error", "ledger.jsonl")]
    w.append({"type": "interview.bogus", "actor": "user", "entity": rid, "source": "user", "action": "what"})
    w.append({"type": rules.EV_CANCELLED, "actor": "user", "entity": rid, "source": "robot", "reason": "x", "action": "x"})
    assert int_issues(w) == [("INT005", "error", "ledger.jsonl")] * 3
    assert all(m.startswith("event ") for m in int_messages(w, "INT005"))
    assert w.show(rid)["label"] == "held"                          # nothing malformed or illegal was applied


def test_int006_round_job_names_no_saved_job(w: W) -> None:
    job = w.job()
    w.job("beta-sre")
    rid = w.add(job)["id"]
    assert int_issues(w) == []
    shutil.rmtree(job_dir(w))
    assert ("INT006", "warning", rel_of(rid)) in int_issues(w)
    (w.root / rel_of(rid)).unlink()                                 # an incomplete round keeps its job from the ledger
    assert int_issues(w) == [("INT003", "warning", rel_of(rid)), ("INT006", "warning", rel_of(rid))]


@pytest.mark.parametrize("status,act,late,want", [
    (State.DISCOVERED, "scheduled", False, ["INT007"]),
    (State.APPROVAL_REQUIRED, "scheduled", False, ["INT007"]),
    (State.APPLIED, "held", False, ["INT007"]),
    (State.READY_TO_APPLY, "held", False, ["INT007"]),
    (State.SCREEN, "scheduled", False, []),
    (State.TECHNICAL, "held", False, []),
    (State.APPLIED, "debriefed", False, []),
    (State.APPLIED, "cancelled", False, []),
    (State.REJECTED, "scheduled", False, ["INT007"]),
    (State.WITHDRAWN, "scheduled", True, ["INT007", "INT100"]),
    (State.ACCEPTED, "held", False, []),
    (State.REJECTED, "debriefed", False, []),
    (State.SCREEN, "scheduled", True, ["INT100"]),
    (State.SCREEN, "held", True, []),
])
def test_int007_status_consistency_and_int100(w: W, status: State, act: str, late: bool, want: list[str]) -> None:
    job = w.job()
    rid = w.add(job, when="2026-10-01" if late else "2026-10-20")["id"]
    if act in ("held", "debriefed"):
        w.held(rid)
    if act == "debriefed":
        w.debrief(rid)
    if act == "cancelled":
        w.cancel(rid)
    set_status(w, "acme-sre", status)
    got = int_issues(w)
    assert [c for c, _, _ in got] == want
    severity = {"INT007": "warning", "INT100": "info"}
    assert got == [(c, severity[c], rel_of(rid)) for c in want]
    if "INT007" in want:
        (issue,) = [i for i in validation.validate_workspace(w.root, w.now) if i.code == "INT007"]
        if status.value in rules.PRE_INTERVIEW_STATUSES:
            assert "careeros transition" in issue.message
            assert issue.fix == ("nothing is run for you: if the interview process has started, move the job with "
                                 f"`careeros transition {job} --to <STATE>`")
        else:                                   # `careeros transition` refuses a terminal job, so it is never advised
            assert "careeros transition" not in issue.message + issue.fix
            assert issue.fix == (f"nothing is run for you: job {job} is {status.value}, which is terminal, so record the "
                                 f"round's outcome with `careeros interview round cancel {rid} --reason ...` or "
                                 f"`round held {rid}`")


def test_interview_events_without_a_rounds_directory_are_still_validated(w: W) -> None:
    job = w.job()
    rid = w.add(job)["id"]
    w.held(rid)
    shutil.rmtree(w.root / "rounds")
    assert int_issues(w) == [("INT003", "warning", rel_of(rid))]
    (row,) = needs(w)["needs_debrief"]
    assert row["round_id"] == rid and row["incomplete"] is True


def test_int008_hand_edit_and_interrupted_reschedule_warn_the_same(w: W, tmp_path: Path) -> None:
    job = w.job()
    a = w.add(job, when="2026-10-08")["id"]
    b = w.add(job, stage="final", when="2026-10-09")["id"]
    assert int_issues(w) == []
    hand_edit(w.root, a, scheduled_for="2026-10-10")
    w.now = shift(T0, minutes=5)
    assert run_crashing(tmp_path, w.root, "before", w.now, "interview", "round", "reschedule", b, "--when", "2026-10-11") == 137
    assert int_issues(w) == sorted([("INT008", "warning", rel_of(a)), ("INT008", "warning", rel_of(b))])
    ma, mb = sorted(int_messages(w, "INT008"), key=lambda m: m.find(b) >= 0)
    assert ma.replace(a, "X").replace("2026-10-10", "D").replace("2026-10-08", "O") == \
        mb.replace(b, "X").replace("2026-10-11", "D").replace("2026-10-09", "O")
    assert "round reschedule" in ma
    # views use the record's date and flag it
    rows = upcoming(w)
    assert [(x["round_id"], x["scheduled_for"], x["date_unrecorded"]) for x in rows] == \
        [(a, "2026-10-10", True), (b, "2026-10-11", True)]
    w.now = "2026-10-12T00:00:00Z"
    assert [(x["round_id"], x["scheduled_for"], x["date_unrecorded"]) for x in needs(w)["needs_update"]] == \
        [(a, "2026-10-10", True), (b, "2026-10-11", True)]
    # recovery: re-run with the date the record holds
    w.resched(a, "2026-10-10")
    w.resched(b, "2026-10-11")
    assert [c for c, _, _ in int_issues(w)] == ["INT100", "INT100"]
    assert [x["date_unrecorded"] for x in needs(w)["needs_update"]] == [False, False]


def test_int009_hand_edit_and_interrupted_debrief_warn_the_same(w: W, tmp_path: Path) -> None:
    job = w.job()
    a = w.add(job)["id"]
    b = w.add(job, stage="final")["id"]
    w.held(a)
    w.now = shift(T0, minutes=1)
    w.held(b)
    rec = read_round(w.root, a)
    (w.root / rel_of(a)).write_bytes(rround.to_bytes(dataclasses.replace(
        rec, debrief=Debrief("strong", ("q",), ("d",), "wait", None))))
    w.now = shift(T0, minutes=5)
    assert run_crashing(tmp_path, w.root, "before", w.now, *debrief_argv(b)) == 137
    assert int_issues(w) == sorted([("INT009", "warning", rel_of(a)), ("INT009", "warning", rel_of(b))])
    ma, mb = sorted(int_messages(w, "INT009"), key=lambda m: m.find(b) >= 0)
    assert ma.replace(a, "X") == mb.replace(b, "X") and "careeros interview debrief" in ma
    # fails safe: both stay in the debrief queue
    assert ids_of(needs(w)["needs_debrief"]) == [a, b]
    w.debrief(a)
    w.debrief(b)
    assert int_issues(w) == [] and needs(w)["needs_debrief"] == []


def test_int008_and_int009_say_nothing_for_incomplete_or_orphan_rounds(w: W) -> None:
    job = w.job()
    rid = w.add(job, when="2026-10-08")["id"]
    hand_edit(w.root, rid, scheduled_for="2026-10-10")
    assert [c for c, _, _ in int_issues(w)] == ["INT008"]
    w.held(rid)
    hand_edit(w.root, rid, debrief=Debrief("weak", (), (), "wait", None))
    assert [c for c, _, _ in int_issues(w)] == ["INT009"]
    hand_edit(w.root, rid, job=w.job("beta-sre"))                   # mismatched: incomplete
    assert int_issues(w) == [("INT004", "error", rel_of(rid))]
    orphan = ids.new_id("round")
    (w.root / rel_of(orphan)).write_bytes(rround.to_bytes(
        Round(orphan, job, "final", "video", "2026-10-08", debrief=Debrief("weak", (), (), "wait", None))))
    assert int_issues(w) == sorted([("INT004", "error", rel_of(rid)), ("INT002", "warning", rel_of(orphan))])


@pytest.mark.parametrize("label", ["scheduled", "held", "debriefed", "cancelled"])
def test_int008_and_int009_only_where_their_remedy_is_legal(w: W, label: str) -> None:
    """INT008 only for a scheduled round (reschedule is legal only then), INT009 only for a held one (debrief is legal
    only then); the raw facts still show in `round show` for every label."""
    job = w.job()
    rid = w.add(job, when="2026-10-20")["id"]
    if label in ("held", "debriefed"):
        w.held(rid)
    if label == "debriefed":
        w.debrief(rid)
    if label == "cancelled":
        w.cancel(rid)
    hand_edit(w.root, rid, scheduled_for="2026-10-22")
    assert int_issues(w) == ([("INT008", "warning", rel_of(rid))] if label == "scheduled" else [])
    assert w.show(rid)["date_unrecorded"] is True
    hand_edit(w.root, rid, scheduled_for="2026-10-20")
    if label != "debriefed":                                        # a debriefed round's block is recorded
        hand_edit(w.root, rid, debrief=Debrief("weak", ("q",), (), "wait", None))
        assert int_issues(w) == ([("INT009", "warning", rel_of(rid))] if label == "held" else [])
        show = w.show(rid)
        assert show["debrief_recorded"] is False and show["unrecorded_debrief"]["rating"] == "weak"


def test_a_killed_reschedule_then_held_gives_no_int008(w: W, tmp_path: Path) -> None:
    job = w.job()
    rid = w.add(job, when="2026-10-20")["id"]
    w.now = shift(T0, minutes=1)
    argv = ("interview", "round", "reschedule", rid, "--when", "2026-10-22")
    assert run_crashing(tmp_path, w.root, "before", w.now, *argv) == 137
    assert int_issues(w) == [("INT008", "warning", rel_of(rid))]
    w.held(rid)                                                     # reschedule is no longer legal, so no warning
    assert int_issues(w) == []
    show = w.show(rid)
    assert show["date_unrecorded"] is True and show["scheduled_for"] == "2026-10-22" and show["recorded_when"] == "2026-10-20"


def test_a_hand_added_debrief_block_on_a_cancelled_round_gives_no_int009(w: W) -> None:
    job = w.job()
    rid = w.add(job)["id"]
    w.cancel(rid)
    hand_edit(w.root, rid, debrief=Debrief("mixed", (), ("ask earlier",), "none", None))
    assert int_issues(w) == []
    assert w.show(rid)["debrief_recorded"] is False and w.show(rid)["unrecorded_debrief"]["rating"] == "mixed"


def test_round_rows_and_view_rows_use_different_key_names(w: W) -> None:
    """Documented in docs/interviews.md: `round list|show` use `id`/`job`; `upcoming`/`needs-debrief` rows use
    `round_id`/`job_id`."""
    job = w.job()
    a = w.add(job, when="2026-10-08")["id"]
    b = w.add(job, stage="final", when="2026-10-02")["id"]
    w.held(b)
    (row,) = [r for r in w.rows() if r["id"] == a]
    assert row["job"] == job and "round_id" not in row and "job_id" not in row
    show = w.show(a)
    assert (show["id"], show["job"]) == (a, job) and "round_id" not in show
    for view_row in (upcoming(w)[0], needs(w)["needs_debrief"][0]):
        assert view_row["job_id"] == job and "id" not in view_row and "job" not in view_row
    assert upcoming(w)[0]["round_id"] == a and needs(w)["needs_debrief"][0]["round_id"] == b
    doc = (Path(__file__).resolve().parent.parent / "docs" / "interviews.md").read_text(encoding="utf-8")
    assert ROW_KEYS_DOC in doc


ROW_KEYS_DOC = ("`round add`, `round list` and `round show` name a round `id` and its job `job`; the rows of `upcoming` "
                "and `needs-debrief` name them `round_id` and `job_id`.")


def test_orphan_and_mismatched_records_feed_no_view_but_are_reported(w: W) -> None:
    job = w.job()
    other = w.job("beta-sre")
    mism = w.add(job, when="2026-10-08")["id"]
    held = w.add(job, stage="final", when="2026-10-02")["id"]
    w.held(held)
    hand_edit(w.root, mism, job=other)
    hand_edit(w.root, held, job=other)
    orphan = ids.new_id("round")
    (w.root / rel_of(orphan)).write_bytes(rround.to_bytes(Round(orphan, job, "panel", "video", "2026-10-07")))
    late_orphan = ids.new_id("round")
    (w.root / rel_of(late_orphan)).write_bytes(rround.to_bytes(Round(late_orphan, job, "panel", "video", "2026-10-01")))
    assert upcoming(w, "--days", "60") == []
    nd = needs(w)
    assert nd["needs_update"] == []
    assert [(x["round_id"], x["incomplete"], x["stage"]) for x in nd["needs_debrief"]] == [(held, True, None)]
    assert {c for c, _, _ in int_issues(w)} == {"INT002", "INT004"}


def test_a_workspace_without_interview_data_gives_the_same_findings(w: W, monkeypatch: pytest.MonkeyPatch) -> None:
    w.job()
    w.job("beta-sre", status=State.APPLIED)
    w.append({"type": "note.added", "actor": "user", "action": "something unrelated"})
    events = validation._lenient_events(w.root)
    assert service.validate_workspace(w.root, events, w.now) == []
    real = validation.validate_workspace(w.root, w.now)
    with monkeypatch.context() as m:
        m.setattr(service, "validate_workspace", lambda *a, **k: [])
        assert validation.validate_workspace(w.root, w.now) == real
    (w.root / "rounds").mkdir()
    assert service.validate_workspace(w.root, events, w.now) == []


def test_validate_reads_the_clock_once(w: W, monkeypatch: pytest.MonkeyPatch) -> None:
    job = w.job()
    w.add(job, when="2026-10-01")
    calls = []

    def clock() -> str:
        calls.append(1)
        return w.now

    monkeypatch.setattr(models, "utc_now", clock)
    issues = validation.validate_workspace(w.root)
    assert len(calls) == 1 and "INT100" in [i.code for i in issues]


def test_hostile_ledger_and_records_never_crash_validate_or_write(w: W) -> None:
    job = w.job()
    rid = w.add(job)["id"]
    w.held(rid)
    with (w.root / "ledger.jsonl").open("ab") as f:
        for line in (b"{not json", b"[1, 2, 3]", b'"a string"', b"null",
                     b'{"type": "interview.held", "entity": 12, "seq": "x", "ts": []}',
                     b'{"type": "interview.round_added", "entity": "rnd_zzzzzzzzzz", "artifacts": "nope", "ts": "2026-10-06T10:00:00Z"}',
                     b'{"type": "interview.round_added", "entity": "rnd_yyyyyyyyyy", "artifacts": [1, 2, 3, 4], "ts": "2026-10-06T10:00:00Z"}',
                     b'{"type": "interview.debriefed", "entity": "' + rid.encode() + b'", "ts": "bad"}',
                     b'{"type": 7}', b'{"seq": 99999999999999999999999}'):
            f.write(line + b"\n")
    base = w.root / "rounds"
    (base / "rnd_bbbbbbbbbb.md").write_bytes(b"\xff\xfe\x00")
    (base / "rnd_cccccccccc.md").write_bytes(b"---\nid: &a rnd_cccccccccc\nschema: *a\n---\n")
    (base / "rnd_dddddddddd.md").write_bytes(b"a" * (MIB + 10))
    (base / "rnd_eeeeeeeeee.md").mkdir()
    (base / "rnd_ffffffffff.md").symlink_to(w.root / rel_of(rid))
    (base / "rnd_gggggggggg.md").write_bytes(b"---\n" + b"[" * 5000 + b"\n---\n")
    without_lock(w)
    before = full_state(w.root, lock=True)
    issues = validation.validate_workspace(w.root, w.now)
    events = validation._lenient_events(w.root)
    direct = service.validate_workspace(w.root, events, w.now)
    assert full_state(w.root, lock=True) == before
    codes = {i.code for i in direct}
    assert {"INT001", "INT005"} <= codes and all(i.code.startswith("INT") for i in direct)
    assert {i.code for i in issues} >= codes
    assert [(i.path, i.severity) for i in direct if i.code == "INT001"] == [
        (f"rounds/rnd_{c * 10}.md", "error") for c in "bcdefg"]
    # nonsense inputs to the section itself
    assert isinstance(service.validate_workspace(w.root, [None, 3, "x", {"type": None}], w.now), list)
    assert isinstance(service.validate_workspace(w.root, events, "not a time"), list)


# =========================================================================================================================
# no truncation
# =========================================================================================================================

def test_no_truncation_in_needs_debrief_upcoming_and_round_list(w: W) -> None:
    job = w.job()
    held = [w.add(job, when=f"2026-09-{n:02d}")["id"] for n in range(1, 26)]
    order = held[:]
    random.Random(4).shuffle(order)
    for n, rid in enumerate(order):
        w.now = shift(T0, minutes=n + 1)
        w.held(rid)
    soon = [w.add(job, stage="final", when=shift("2026-10-07T00:00:00Z", hours=n))["id"] for n in range(25)]
    nd = needs(w)
    assert ids_of(nd["needs_debrief"]) == order                     # oldest held_at first, all 25
    assert [x["held_at"] for x in nd["needs_debrief"]] == sorted(x["held_at"] for x in nd["needs_debrief"])
    assert ids_of(upcoming(w)) == soon
    assert len(w.rows()) == 50
    res = w.run("interview", "needs-debrief")
    assert all(rid in res.stdout for rid in held)
    res = w.run("interview", "upcoming")
    assert all(rid in res.stdout for rid in soon)


def test_more_than_max_debrief_items_is_refused(w: W) -> None:
    job = w.job()
    rid = w.add(job)["id"]
    w.held(rid)
    w.refused(*debrief_argv(rid, asked=tuple(f"q{n}" for n in range(rules.MAX_DEBRIEF_ITEMS + 1))), code="invalid_input")
    w.refused(*debrief_argv(rid, improve=tuple(f"d{n}" for n in range(rules.MAX_DEBRIEF_ITEMS + 1))), code="invalid_input")
    w.debrief(rid, asked=tuple(f"q{n}" for n in range(rules.MAX_DEBRIEF_ITEMS)))


def test_a_date_only_and_a_datetime_of_the_same_day_are_both_accepted(w: W) -> None:
    job = w.job()
    a = w.add(job, when="2026-10-20")["id"]
    b = w.add(job, when="2026-10-20T15:00:00Z")["id"]
    assert a != b and len(w.rows()) == 2
    w.refused("interview", "round", "add", "--job", job, "--stage", "technical", "--format", "video", "--when",
              "2026-10-20T15:00:00Z", code="duplicate")


# =========================================================================================================================
# incomplete rounds in the views
# =========================================================================================================================

def test_incomplete_rounds_in_the_views(w: W) -> None:
    job = w.job()
    missing = w.add(job, when="2026-10-08")["id"]
    invalid = w.add(job, stage="final", when="2026-10-01")["id"]
    held = w.add(job, stage="panel", when="2026-10-02")["id"]
    ok = w.add(job, stage="other", when="2026-10-09")["id"]
    w.held(held)
    (w.root / rel_of(missing)).unlink()
    (w.root / rel_of(invalid)).write_text("garbage\n", encoding="utf-8")
    (w.root / rel_of(held)).unlink()
    assert ids_of(upcoming(w)) == [ok]
    nd = needs(w)
    assert nd["needs_update"] == []
    (row,) = nd["needs_debrief"]
    assert row["round_id"] == held and row["incomplete"] is True and row["job_id"] == job
    assert row["stage"] is None and row["scheduled_for"] is None and row["date_unrecorded"] is False
    text = w.run("interview", "needs-debrief").stdout
    assert held in text and "record unavailable" in text
    assert {r["id"]: r["incomplete"] for r in w.rows()} == {missing: True, invalid: True, held: True, ok: False}
    assert not (w.root / rel_of(missing)).exists()                  # nothing recreated
