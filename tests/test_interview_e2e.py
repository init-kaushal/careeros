"""One end-to-end walk through interview rounds and debriefs (sub-project 4b) on invented data, through the real CLI:
two rounds in `upcoming` (soonest first) with prep `none`, `drafted`, then `checked` by a real `careeros check --record`;
a clock advance moving a round from `upcoming` to `needs_update`, `held` moving it to `needs_debrief`, a debrief taking it
out, a second debrief refused; a reschedule, a cancel that cannot be reopened, a duplicate refused, the terminal-job rules;
`INT007` and `INT100` in `validate`; a hand-edited `scheduled_for`, an orphan and a mismatched record; a malformed
hand-appended event (`INT005`); interrupted `reschedule` and `debrief` (real child processes killed between the file
write and the event append) reported (`INT008`, `INT009`) and recovered; an incomplete round; a byte-identical workspace
after every read-only command; event counts, rejection dedupe, the ledger chain, isolation; 25 held rounds all listed;
and one real-process race (two `held` on one round).

Time comes from ONE source: `W` (the interview test workspace) patches `models.utc_now`, and the walk moves it
explicitly; the crashing children get the same value through CAREEROS_TEST_NOW. The race is the only part on the real
clock."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pytest
from helpers import T0, Cmd, add_job, full_state, make_career, make_workspace, race_many, shift, snapshot
from test_interview_records import W, add_argv, debrief_argv, hand_edit, rel_of, run_crashing

from careeros.core import ids, ledger
from careeros.core.interview import rules
from careeros.core.interview import round as rround
from careeros.core.interview.model import Round
from careeros.core.models import State

PREP = "Reduced AWS costs by 35% using Kafka-based pipelines at Acme Corp."   # passes `check` on the invented memory
EXPECTED_COUNTS = {                     # raw ledger lines per type: `held` includes the one malformed, unapplied line
    "interview.round_added": 6, "interview.held": 5, "interview.debriefed": 3, "interview.rescheduled": 3,
    "interview.cancelled": 1, "interview.rejected": 6,
}
EXPECTED_REJECTIONS = ["duplicate", "record_unavailable", "wrong_state", "wrong_state", "wrong_state", "wrong_state"]


def ids_of(rows: list[dict], key: str = "round_id") -> list[str]:
    return [r[key] for r in rows]


def validate(w: W) -> tuple[int, list[str]]:
    res = w.run("validate", "--json")
    return res.exit_code, sorted(i["code"] for i in json.loads(res.stdout)["issues"] if i["code"].startswith("INT"))


def read_only_unchanged(w: W, rids: list[str]) -> None:
    """Run every read-only interview command (and validate); the WHOLE workspace (ledger, `.careeros/`, mtimes) must
    stay as it was."""
    before = full_state(w.root, lock=True)
    commands = [("interview", "upcoming"), ("interview", "upcoming", "--days", "30"), ("interview", "needs-debrief"),
                ("interview", "round", "list"), ("validate",)] + [("interview", "round", "show", r) for r in rids]
    for args in commands:
        for extra in ((), ("--json",)):
            res = w.run(*args, *extra)
            assert res.exit_code in (0, 1), (args, res.output)
            assert full_state(w.root, lock=True) == before, args


def test_interview_end_to_end(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CAREEROS_WORKSPACE", raising=False)
    root = make_workspace(tmp_path / "ws")
    w = W(root, monkeypatch)                                             # the clock is patched before any event is stamped
    make_career(root)                                                    # invented career memory, so `check` can pass
    other = add_job(root, "other-job", company="Other Co")               # a different job, never touched
    (other["path"].parent / "jd.md").write_text("# Another invented posting\n", encoding="utf-8")
    guarded = {k: v for k, v in snapshot(root).items() if k.startswith("career/") or "other-job" in k}
    job = w.job("example-sre")                                           # status SCREEN
    jd = root / "jobs" / "discovered" / "example-sre" / "jd.md"
    jd.write_text("# An invented posting\n", encoding="utf-8")
    jd_bytes = jd.read_bytes()

    # (a) two rounds (a screen in 3 days, a technical in 10); upcoming soonest first; prep none -> drafted -> checked
    screen = w.add(job, stage="recruiter_screen", fmt="phone", when=shift(T0, days=3)[:10])["id"]
    tech = w.add(job, stage="technical", fmt="video", when=shift(T0, days=10)[:10],
                 interviewers=("Dana Example:Staff Engineer",))["id"]
    rows = w.ok("interview", "upcoming")["rounds"]
    assert [(r["round_id"], r["scheduled_for"], r["prep"]) for r in rows] == \
        [(screen, "2026-10-09", "none"), (tech, "2026-10-16", "none")]
    prep = root / "jobs" / "discovered" / "example-sre" / "interview-prep.md"
    prep.write_text(PREP, encoding="utf-8")
    assert [r["prep"] for r in w.ok("interview", "upcoming")["rounds"]] == ["drafted", "drafted"]
    assert w.run("check", str(prep), "--record", "--against", job).exit_code == 0
    assert [r["prep"] for r in w.ok("interview", "upcoming")["rounds"]] == ["checked", "checked"]

    # (b) past the screen's day: it leaves upcoming for needs_update; `held` moves it to needs_debrief
    w.now = "2026-10-10T00:00:00Z"                                       # the start of the next UTC day: overdue
    assert ids_of(w.ok("interview", "upcoming")["rounds"]) == [tech]
    nd = w.ok("interview", "needs-debrief")
    assert (ids_of(nd["needs_debrief"]), ids_of(nd["needs_update"])) == ([], [screen])
    assert w.held(screen)["label"] == "held"
    nd = w.ok("interview", "needs-debrief")
    assert (ids_of(nd["needs_debrief"]), ids_of(nd["needs_update"])) == ([screen], [])

    # (c) debrief it: it leaves needs-debrief; a second debrief is refused wrong_state (recorded once for two tries)
    assert w.debrief(screen, rating="mixed")["label"] == "debriefed"
    nd = w.ok("interview", "needs-debrief")
    assert nd["needs_debrief"] == nd["needs_update"] == []
    w.refused(*debrief_argv(screen), code="wrong_state")
    w.refused(*debrief_argv(screen), code="wrong_state")
    assert w.show(screen)["debrief"]["rating"] == "mixed"

    # (d) reschedule the technical; add and cancel a third; a cancelled round is not reopenable
    assert w.resched(tech, "2026-10-20")["scheduled_for"] == "2026-10-20"
    assert [(r["round_id"], r["scheduled_for"]) for r in w.ok("interview", "upcoming")["rounds"]] == [(tech, "2026-10-20")]
    final = w.add(job, stage="final", fmt="onsite", when="2026-10-23")["id"]
    assert w.cancel(final, reason="the role was filled")["label"] == "cancelled"
    w.refused("interview", "round", "held", final, code="wrong_state")
    w.refused("interview", "round", "reschedule", final, "--when", "2026-10-24", code="wrong_state")
    w.refused("interview", "round", "cancel", final, "--reason", "again", code="wrong_state")
    assert w.show(final)["label"] == "cancelled"
    assert ids_of(w.ok("interview", "upcoming")["rounds"]) == [tech]

    # (e) a duplicate round is refused (audited once on the live round); a terminal job: add refused, held and debrief
    # allowed
    n = len(w.events())
    w.refused(*add_argv(job, stage="technical", when="2026-10-20"), code="duplicate")
    (dup,) = w.events()[n:]
    assert (dup["type"], dup["entity"], dup["reason"].split(":")[0]) == (rules.EV_REJECTED, tech, "duplicate")
    term = w.job("example-platform")
    hm = w.add(term, stage="hiring_manager", fmt="video", when="2026-10-12")["id"]
    assert w.run("transition", term, "--to", "REJECTED").exit_code == 0
    before = w.ledger_bytes()
    w.refused(*add_argv(term, stage="final", when="2026-10-26"), code="wrong_state")
    assert w.ledger_bytes() == before
    assert w.held(hm)["label"] == "held"
    assert w.debrief(hm, rating="weak")["label"] == "debriefed"

    # (f) validate: INT100 for a needs_update round and INT007 for a round on a job still at APPLIED, with exit codes
    applied = w.job("example-data", status=State.APPLIED)
    early = w.add(applied, stage="recruiter_screen", fmt="phone", when="2026-10-08")["id"]
    code, found = validate(w)
    assert (code, found) == (0, ["INT007", "INT100"])
    assert w.held(early)["label"] == "held"
    code, found = validate(w)
    assert (code, found) == (0, ["INT007"])
    assert ids_of(w.ok("interview", "needs-debrief")["needs_debrief"]) == [early]

    # (g) a hand-edited scheduled_for changes upcoming, not identity or history; orphan and mismatched records feed nothing
    show_before = w.show(tech)
    hand_edit(root, tech, scheduled_for="2026-10-18")
    (row,) = w.ok("interview", "upcoming")["rounds"]
    assert (row["round_id"], row["scheduled_for"], row["date_unrecorded"]) == (tech, "2026-10-18", True)
    show = w.show(tech)
    assert (show["id"], show["job"], show["label"], show["history"]) == \
        (tech, job, show_before["label"], show_before["history"])
    assert validate(w) == (0, ["INT007", "INT008"])
    w.resched(tech, "2026-10-18")                                        # recorded: the record already holds the date
    assert validate(w) == (0, ["INT007"])
    orphan = ids.new_id("round")
    (root / rel_of(orphan)).write_bytes(rround.to_bytes(Round(orphan, job, "panel", "video", "2026-10-15")))
    assert orphan not in ids_of(w.rows(), "id") and ids_of(w.ok("interview", "upcoming")["rounds"]) == [tech]
    assert validate(w) == (0, ["INT002", "INT007"])
    (root / rel_of(orphan)).unlink()
    good = (root / rel_of(tech)).read_bytes()
    hand_edit(root, tech, job=other["id"])                               # the job edited: mismatched
    assert w.ok("interview", "upcoming")["rounds"] == []
    (row,) = [r for r in w.rows() if r["id"] == tech]
    assert (row["incomplete"], row["record"], row["job"], row["label"]) == (True, "mismatched", job, "scheduled")
    assert validate(w) == (1, ["INT004", "INT007"])
    (root / rel_of(tech)).write_bytes(good)
    assert ids_of(w.ok("interview", "upcoming")["rounds"]) == [tech] and validate(w) == (0, ["INT007"])

    # (h) a malformed interview.held hand-appended (no source) is INT005 and changes nothing
    derived_before = (w.rows(), w.ok("interview", "upcoming"), w.ok("interview", "needs-debrief"))
    w.append({"type": rules.EV_HELD, "actor": "user", "entity": tech, "action": f"held {tech}"})
    assert validate(w) == (1, ["INT005", "INT007"])
    assert (w.rows(), w.ok("interview", "upcoming"), w.ok("interview", "needs-debrief")) == derived_before
    assert w.show(tech)["label"] == "scheduled"

    # (m) an interrupted reschedule and an interrupted debrief (child killed between file and event): INT008, INT009,
    # the crashed debrief stays in the debrief queue; re-running each command recovers it
    assert run_crashing(tmp_path, root, "before", w.now, "interview", "round", "reschedule", tech, "--when",
                        "2026-10-21") == 137
    assert run_crashing(tmp_path, root, "before", w.now, *debrief_argv(early, rating="strong")) == 137
    assert ledger.verify_chain(root) == []
    assert validate(w) == (1, ["INT005", "INT007", "INT008", "INT009"])
    assert ids_of(w.ok("interview", "needs-debrief")["needs_debrief"]) == [early]
    assert [r["date_unrecorded"] for r in w.ok("interview", "upcoming")["rounds"]] == [True]
    w.resched(tech, "2026-10-21")
    assert w.debrief(early, rating="strong")["label"] == "debriefed"
    assert validate(w) == (1, ["INT005"])                                # INT007 gone: the APPLIED job's round is debriefed
    assert w.ok("interview", "needs-debrief")["needs_debrief"] == []

    # (n) a held round whose record was deleted: listed as incomplete, still in needs-debrief, never recreated
    panel = w.add(job, stage="panel", fmt="video", when="2026-10-09")["id"]
    w.held(panel)
    (root / rel_of(panel)).unlink()
    (row,) = [r for r in w.rows() if r["id"] == panel]
    assert (row["incomplete"], row["record"], row["label"]) == (True, "missing", "held")
    show = w.show(panel)
    assert show["stage"] is None and [h["type"] for h in show["history"]] == [rules.EV_ROUND_ADDED, rules.EV_HELD]
    (row,) = w.ok("interview", "needs-debrief")["needs_debrief"]
    assert (row["round_id"], row["incomplete"]) == (panel, True)
    w.refused(*debrief_argv(panel), code="record_unavailable")
    assert not (root / rel_of(panel)).exists()
    assert "INT003" in validate(w)[1]

    # (i) the read-only commands leave the WHOLE workspace byte-identical (ledger and .careeros/ included)
    read_only_unchanged(w, [screen, tech, final, hm, early, panel])

    # (j) events per type, deduped rejections, an intact chain
    counts = Counter(e["type"] for e in w.events() if e["type"].startswith(rules.EVENT_PREFIX))
    assert counts == EXPECTED_COUNTS, counts
    rejects = [(e["entity"], e.get("reason")) for e in w.events(rules.EV_REJECTED)]
    assert len(rejects) == len(set(rejects))
    assert sorted(r.split(":")[0] for _, r in rejects) == EXPECTED_REJECTIONS, rejects
    assert ledger.verify_chain(root) == []

    # (k) isolation: career/, the other job and every jd.md are untouched
    assert {k: v for k, v in snapshot(root).items() if k.startswith("career/") or "other-job" in k} == guarded
    assert jd.read_bytes() == jd_bytes
    assert not [e for e in w.events() if e.get("entity") == other["id"] and e["type"] != "job.imported"]


def test_twenty_five_held_rounds_are_all_listed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """(o) No view or list truncates: 25 held rounds all appear in needs-debrief and round list."""
    monkeypatch.delenv("CAREEROS_WORKSPACE", raising=False)
    w = W(make_workspace(tmp_path / "ws"), monkeypatch)
    job = w.job("example-sre")
    rids = []
    for n in range(25):
        w.now = shift(T0, minutes=n)
        rid = w.add(job, when=f"2026-10-{n + 1:02d}T09:00:00Z")["id"]
        w.held(rid)
        rids.append(rid)
    w.now = shift(T0, days=1)
    assert ids_of(w.ok("interview", "needs-debrief")["needs_debrief"]) == rids      # oldest held first
    assert sorted(ids_of(w.rows(), "id")) == sorted(rids)
    assert "25 round(s)" in w.run("interview", "round", "list").output


def test_a_real_process_race_for_held_has_exactly_one_winner(tmp_path: Path) -> None:
    """(l) Two real processes run `round held` on one round at once: exactly one succeeds, the other is refused
    wrong_state, and the ledger holds one held event. Real processes, the real clock."""
    from careeros.core import models
    from careeros.core.interview import service
    root = make_workspace(tmp_path / "ws")
    job = add_job(root, "example-sre", status=State.SCREEN)["id"]
    got = service.round_add(root, job=job, stage="technical", format="video", when="2026-12-01", interviewers=[],
                            actor="agent:test", clock=models.utc_now)
    cmds = [Cmd("interview", "round", "held", got.id, "--json", "--workspace", str(root)) for _ in range(2)]
    (results,) = race_many(tmp_path, [cmds], widen=False)
    assert sorted(d.get("code", "ok") if code else "ok" for code, d in results) == ["ok", "wrong_state"], results
    assert len([e for e in ledger.read_events(root) if e["type"] == rules.EV_HELD]) == 1
    assert ledger.verify_chain(root) == []
