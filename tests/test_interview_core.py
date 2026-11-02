"""Interview core (Task A): rules, the round record, the round fold and the derivations. Pure; invented data only. The
reference model here (the round simulator) is written independently of the production fold and shares no table with
rules.py.
"""

from __future__ import annotations

import itertools
import random
import subprocess
import sys
from dataclasses import replace
from datetime import datetime, timedelta

import pytest

from careeros.core import ids, models
from careeros.core import state_machine
from careeros.core.interview import derive, rules
from careeros.core.interview import fold as ifold
from careeros.core.interview import round as rround
from careeros.core.interview.model import Debrief, Interviewer, JobRef, Round, RoundFlags
from careeros.core.workspace import join_frontmatter

RND = "rnd_abcdefghjk"
RND2 = "rnd_bcdefghjkm"
RND3 = "rnd_cdefghjkmn"
JOB = "job_abcdefghjk"
JOB2 = "job_bcdefghjkm"
SHA = "c" * 64
REL = f"rounds/{RND}.md"
T0 = datetime(2026, 1, 1, 0, 0, 0)
FMT = "%Y-%m-%dT%H:%M:%SZ"


def ts(delta: int | timedelta = 0) -> str:
    d = timedelta(seconds=delta) if isinstance(delta, int) else delta
    return (T0 + d).strftime(FMT)


NOW = ts(timedelta(days=400))

SPEC = f"""---
schema: 1
id: {RND}
job: {JOB}
stage: technical
format: video
scheduled_for: "2026-11-05T15:00:00Z"
interviewers:
- name: Alex Example
  title: Staff Engineer
- name: Sam Example
  title: ""
debrief:
  rating: mixed
  questions_asked:
  - Design a rate limiter
  do_differently:
  - Ask about scale first
  next_step: Wait for the recruiter
  next_step_date: "2026-11-10"
---
Free notes.
"""


def parse(text, rel=REL):
    return rround.parse(text, rel)


def only_int001(parsed):
    record, issues = parsed
    assert record is None
    assert issues and all(i.code == "INT001" and i.severity == "error" for i in issues), issues
    return issues


# --- rules -----------------------------------------------------------------------------------------------------------------

def test_the_rules_constants_and_tables():
    assert (rules.UPCOMING_DEFAULT_DAYS, rules.MAX_INTERVIEWERS, rules.MAX_DEBRIEF_ITEMS, rules.MAX_TEXT) == (14, 10, 10, 500)
    assert rules.STAGES == ("recruiter_screen", "hiring_manager", "technical", "system_design", "panel", "final", "other")
    assert rules.FORMATS == ("phone", "video", "onsite", "take_home", "other")
    assert rules.RATINGS == ("strong", "mixed", "weak")
    assert rules.SOURCES == ("user", "observed")
    assert rules.EVENT_PREFIX == "interview."
    assert (rules.EV_ROUND_ADDED, rules.EV_HELD, rules.EV_CANCELLED, rules.EV_RESCHEDULED, rules.EV_DEBRIEFED,
            rules.EV_REJECTED) == ("interview.round_added", "interview.held", "interview.cancelled",
                                   "interview.rescheduled", "interview.debriefed", "interview.rejected")
    assert rules.AUDIT_ONLY_TYPES == ("interview.rejected",)
    assert set(rules.REFUSAL_CODES) == {"not_found", "wrong_state", "duplicate", "invalid_input", "record_unavailable",
                                        "clock_skew", "ledger_corrupt"}
    assert rules.RECORD_UNAVAILABLE == "record_unavailable"
    assert (rules.INT001, rules.INT002, rules.INT003, rules.INT004, rules.INT005, rules.INT006, rules.INT007, rules.INT008,
            rules.INT009, rules.INT100) == tuple(f"INT00{n}" for n in range(1, 10)) + ("INT100",)
    assert rules.LABEL_PRECEDENCE == ("cancelled", "debriefed", "held", "scheduled")
    assert ids.PREFIXES["round"] == "rnd" and ids.is_valid_id("round", RND)


def test_the_job_status_groups_match_the_state_machine():
    assert set(rules.TERMINAL_JOB_STATUSES) == {s.value for s in state_machine.TERMINAL}
    order = [s.value for s in models.State]
    assert list(rules.PRE_INTERVIEW_STATUSES) == order[:order.index("APPLIED") + 1]
    assert "APPROVAL_REQUIRED" in rules.PRE_INTERVIEW_STATUSES


# --- the record parser -------------------------------------------------------------------------------------------------------

def test_the_spec_example_parses():
    record, issues = parse(SPEC)
    assert issues == []
    assert record == Round(
        id=RND, job=JOB, stage="technical", format="video", scheduled_for="2026-11-05T15:00:00Z",
        interviewers=(Interviewer("Alex Example", "Staff Engineer"), Interviewer("Sam Example", "")),
        debrief=Debrief("mixed", ("Design a rate limiter",), ("Ask about scale first",), "Wait for the recruiter", "2026-11-10"),
        body="Free notes.\n")


def test_no_debrief_no_interviewers_and_a_date_only_value_parse():
    text = SPEC.split("debrief:")[0].replace('"2026-11-05T15:00:00Z"', "2026-11-05") + "---\n"
    text = text.replace("interviewers:\n- name: Alex Example\n  title: Staff Engineer\n- name: Sam Example\n  title: \"\"\n",
                        "interviewers: []\n")
    record, issues = parse(text)
    assert issues == [] and record.debrief is None and record.interviewers == () and record.scheduled_for == "2026-11-05"


def test_unquoted_yaml_dates_and_utc_datetimes_read_as_their_canonical_strings():
    r1, _ = parse(SPEC.replace('"2026-11-05T15:00:00Z"', "2026-11-05T15:00:00Z"))
    r2, _ = parse(SPEC.replace('"2026-11-10"', "2026-11-10"))
    assert r1.scheduled_for == "2026-11-05T15:00:00Z" and r2.debrief.next_step_date == "2026-11-10"


def test_crlf_is_read_as_lf():
    assert parse(SPEC.replace("\n", "\r\n")) == parse(SPEC)


@pytest.mark.parametrize("key", ["schema", "id", "job", "stage", "format", "scheduled_for", "interviewers"])
def test_every_required_key_is_required(key):
    lines = [ln for ln in SPEC.splitlines(keepends=True)]
    start = next(i for i, ln in enumerate(lines) if ln.startswith(f"{key}:"))
    end = start + 1
    while end < len(lines) and lines[end].startswith((" ", "-")):
        end += 1
    only_int001(parse("".join(lines[:start] + lines[end:])))


DEBRIEF_FIELDS = ("rating", "questions_asked", "do_differently", "next_step")


@pytest.mark.parametrize("key", DEBRIEF_FIELDS)
def test_every_required_debrief_key_is_required(key):
    fm = rround.to_fm(parse(SPEC)[0])
    del fm["debrief"][key]
    only_int001(parse(join_frontmatter(fm, "")))


def fm_with(**over):
    fm = rround.to_fm(parse(SPEC)[0])
    for k, v in over.items():
        if k.startswith("debrief__"):
            fm["debrief"][k[len("debrief__"):]] = v
        else:
            fm[k] = v
    return join_frontmatter(fm, "")


WRONG = [None, 5, 1.5, True, [], {}, ["x"], {"a": 1}]
BAD_FIELDS = (
    [("schema", v) for v in WRONG + [0, 2, "1", float("nan")]]
    + [("id", v) for v in WRONG + ["rnd_short", "job_abcdefghjk", RND2, RND.upper(), RND + " "]]
    + [("job", v) for v in WRONG + ["job_bad", RND, ""]]
    + [("stage", v) for v in WRONG + ["Technical", "screen", ""]]
    + [("format", v) for v in WRONG + ["Video", "in_person", ""]]
    + [("scheduled_for", v) for v in WRONG + ["2026-02-30", "2026-2-3", "26-02-03", "2026-02-03T10:00:00",
                                              "2026-02-03T10:00:00+01:00", "2026-02-03T10:00:00+00:00",
                                              "2026-02-03T25:00:00Z", "2026-02-03t10:00:00z", "2026-02-03T10:00Z",
                                              "2026-02-03T10:00:00.000Z", "2026-02-03 10:00:00Z", " 2026-02-03",
                                              "2026-02-03\n", "\uff12026-02-03", ""]]
    + [("interviewers", v) for v in [None, 5, "Alex", {"name": "a", "title": "b"}, [None], ["Alex"], [{"name": "a"}],
                                     [{"name": "a", "title": "b", "x": 1}], [{"name": "", "title": "b"}],
                                     [{"name": 5, "title": "b"}], [{"name": "a", "title": None}],
                                     [{"name": "a", "title": "b"}] * 11, [{"name": "x" * 501, "title": ""}],
                                     [{"name": "a", "title": "x" * 501}]]]
    + [("debrief", v) for v in [None, 5, "strong", [], {"rating": "strong"}]]
    + [("debrief__rating", v) for v in WRONG + ["Strong", "great", ""]]
    + [("debrief__questions_asked", v) for v in [None, 5, "q", {}, [None], [5], [""], ["q"] * 11, ["x" * 501]]]
    + [("debrief__do_differently", v) for v in [None, 5, "q", {}, [None], [5], [""], ["q"] * 11, ["x" * 501]]]
    + [("debrief__next_step", v) for v in WRONG + ["", "  ", "x" * 501]]
    + [("debrief__next_step_date", v) for v in WRONG + ["2026-02-30", "2026-11-10T10:00:00Z", ""]]
    + [("debrief__extra", "x"), ("extra", "x")]
)


@pytest.mark.parametrize("key,value", BAD_FIELDS, ids=[f"{k}={v!r}"[:60] for k, v in BAD_FIELDS])
def test_every_bad_field_value_is_int001(key, value):
    only_int001(parse(fm_with(**{key: value})))


@pytest.mark.parametrize("char", ["\x00", "\x07", "\x1b", "\x7f", "\x85", "\u202e", "\u2066", "\u200b", "\u200f", "\ufeff",
                                  "\u2028", "\u00ad", "\n", "\t", "\r"])
@pytest.mark.parametrize("where", ["interviewer name", "interviewer title", "question", "next_step"])
def test_control_bidi_and_invisible_characters_are_int001(char, where):
    # written as a YAML double-quoted escape, so the character reaches the parsed value (a raw NEL is folded by YAML)
    bad = f'"Alex\\u{ord(char):04x}Example"'
    old = {"interviewer name": "name: Alex Example", "interviewer title": "title: Staff Engineer",
           "question": "- Design a rate limiter", "next_step": "next_step: Wait for the recruiter"}[where]
    new = old.split(": ", 1)[0] + ": " + bad if ": " in old else "- " + bad
    assert old in SPEC
    only_int001(parse(SPEC.replace(old, new)))


def test_unquoted_datetimes_without_utc_are_int001():
    only_int001(parse(SPEC.replace('"2026-11-05T15:00:00Z"', "2026-11-05T15:00:00")))
    only_int001(parse(SPEC.replace('"2026-11-05T15:00:00Z"', "2026-11-05T15:00:00+05:30")))
    only_int001(parse(SPEC.replace('"2026-11-05T15:00:00Z"', "2026-11-05T15:00:00.5Z")))


def test_the_id_must_equal_the_file_stem():
    only_int001(parse(SPEC, f"rounds/{RND2}.md"))
    only_int001(parse(SPEC, "rounds/other.md"))
    assert parse(SPEC, f"rounds/{RND}.md")[1] == []


def test_unquoted_yaml_scalars_that_are_not_strings_are_refused():
    only_int001(parse(SPEC.replace("name: Alex Example", "name: yes")))
    only_int001(parse(SPEC.replace("name: Alex Example", "name: 12")))
    only_int001(parse(SPEC.replace("stage: technical", "stage: null")))
    only_int001(parse(SPEC.replace("schema: 1", "schema: 1.0")))
    only_int001(parse(SPEC.replace("schema: 1", "schema: 0x1")))
    only_int001(parse(SPEC.replace("schema: 1", "schema: true")))


HOSTILE_TEXT = {
    "empty": "", "no fence": "id: x\n", "unterminated": "---\nid: x\n", "list": "---\n- a\n---\n", "scalar": "---\nx\n---\n",
    "alias": SPEC.replace("stage: technical", "stage: &a technical\nformat2: *a"),
    "alias in value": SPEC.replace("stage: technical", "stage: &a technical").replace("format: video", "format: *a"),
    "merge key": SPEC.replace("debrief:\n", "debrief:\n  <<: {rating: strong}\n"),
    "merge key top": "---\n<<: {a: 1}\n---\n",
    "duplicate key": SPEC.replace("stage: technical", "stage: technical\nstage: panel"),
    "duplicate nested key": SPEC.replace("  rating: mixed", "  rating: mixed\n  rating: weak"),
    "unknown key": SPEC.replace("stage: technical", "stage: technical\nnotes: x"),
    "python tag": "---\nid: !!python/object/apply:os.system [x]\n---\n", "binary tag": "---\nid: !!binary aGk=\n---\n",
    "bad yaml": "---\ninterviewers: [unclosed\n---\n", "tab indent": "---\ninterviewers:\n\t- x\n---\n",
    "nul": "---\nid: a\x00b\n---\n",
    "lone surrogate": SPEC.replace("Alex Example", "Alex \ud800 Example"),
    "huge int": SPEC.replace("schema: 1", "schema: 1" + "0" * 100_000),
    "nan": SPEC.replace("schema: 1", "schema: .nan"),
    "inf": SPEC.replace("schema: 1", "schema: .inf"),
    "huge": "---\nid: " + "x" * 3_000_000 + "\n---\n",
    "5 MB body": SPEC + "n" * 5_000_000,
    "deep nesting": "---\ninterviewers: " + "[" * 100_000 + "]" * 100_000 + "\n---\n",
    "deep mapping": "---\ndebrief: " + "{a: " * 20_000 + "1" + "}" * 20_000 + "\n---\n",
    "many keys": "---\n" + "\n".join(f"k{n}: {n}" for n in range(20_000)) + "\n---\n",
    "billion laughs": "---\na: &a [x]\nb: &b [*a,*a,*a,*a]\nc: [*b,*b,*b,*b]\n---\n",
    "bom": "\ufeff" + SPEC,
    "not text": b"---\nid: x\n---\n",
    "none": None,
}


@pytest.mark.parametrize("name", list(HOSTILE_TEXT))
def test_hostile_input_is_int001_and_never_raises(name):
    only_int001(parse(HOSTILE_TEXT[name]))


AWKWARD = ["yes", "no", "null", "~", "123", "1e3", "- x", "a: b", "#hash", "'quoted'", '"dq"', "  lead", "trail  ",
           "caf\u00e9", "\u5317\u4eac", "\U0001F600", "back\\slash", "[x]", "{x}", "&a", "*a", "!tag", "@at", "%pct",
           "2026-11-05", "x" * 500]


def test_to_bytes_round_trips_over_awkward_text():
    rng = random.Random(11)
    base = parse(SPEC)[0]
    for _ in range(500):
        pick = lambda: rng.choice(AWKWARD)       # noqa: E731
        record = replace(
            base,
            stage=rng.choice(rules.STAGES), format=rng.choice(rules.FORMATS),
            scheduled_for=rng.choice(["2026-11-05", "2026-11-05T15:00:00Z", "2028-02-29", "2026-12-31T23:59:59Z"]),
            interviewers=tuple(Interviewer(pick().strip() or "n", rng.choice(["", pick()]))
                               for _ in range(rng.randrange(0, rules.MAX_INTERVIEWERS + 1))),
            debrief=rng.choice([None, Debrief(rng.choice(rules.RATINGS),
                                              tuple(pick() for _ in range(rng.randrange(0, 11))),
                                              tuple(pick() for _ in range(rng.randrange(0, 11))),
                                              pick(), rng.choice([None, "2026-11-10"]))]),
            body=rng.choice(["", "\nFree notes.\n", "body: with --- fence\n---\n"]))
        data = rround.to_bytes(record)
        assert data.endswith(record.body.encode()) and b"\r" not in data
        assert parse(data.decode("utf-8")) == (record, [])


def test_to_bytes_is_canonical_and_stable():
    record = parse(SPEC)[0]
    once = rround.to_bytes(record)
    assert rround.to_bytes(parse(once.decode())[0]) == once
    assert once.startswith(b"---\nschema: 1\nid: " + RND.encode() + b"\njob: " + JOB.encode() + b"\n")


def test_with_debrief_and_with_when():
    record = parse(SPEC)[0]
    d = Debrief("strong", ("q",), (), "wait", None)
    assert rround.with_debrief(record, d).debrief == d and rround.with_debrief(record, None).debrief is None
    assert rround.with_when(record, "2026-12-01").scheduled_for == "2026-12-01"
    assert rround.with_when(record, "2026-12-01T09:30:00Z").scheduled_for == "2026-12-01T09:30:00Z"
    for bad in ("2026-02-30", "2026-12-01T09:30:00", 5, None, ""):
        with pytest.raises(ValueError):
            rround.with_when(record, bad)
    assert record.scheduled_for == "2026-11-05T15:00:00Z"        # frozen: the original is unchanged


@pytest.mark.parametrize("value,expected", [
    ("2026-11-05", datetime(2026, 11, 5)), ("2026-11-05T15:00:01Z", datetime(2026, 11, 5, 15, 0, 1)),
    ("2028-02-29", datetime(2028, 2, 29)),
])
def test_parse_when_reads_the_two_forms(value, expected):
    assert rround.parse_when(value) == expected


@pytest.mark.parametrize("value", ["2026-02-30", "2027-02-29", "2026-11-05T15:00:01", "2026-11-05T15:00:01+00:00",
                                   "2026-11-05 15:00:01Z", "2026-11-05T24:00:00Z", "2026-11-05\n", "\uff12026-11-05",
                                   "", None, 5, datetime(2026, 1, 1), ["2026-11-05"]])
def test_parse_when_refuses_anything_else(value):
    assert rround.parse_when(value) is None


# --- events ----------------------------------------------------------------------------------------------------------------

class Ledger:
    def __init__(self):
        self.events: list[dict] = []

    def add(self, etype, entity, at, **kw):
        event = {"seq": len(self.events) + 1, "ts": at, "type": etype, "actor": "user", "entity": entity, **kw}
        self.events.append(event)
        return event

    def round_added(self, entity, at, job=JOB, when="2026-11-05", **kw):
        return self.add(rules.EV_ROUND_ADDED, entity, at,
                        **{**ifold.round_added_fields(f"rounds/{entity}.md", SHA, job, when, stage="technical"), **kw})

    def held(self, entity, at, **kw):
        return self.add(rules.EV_HELD, entity, at, **{"source": "user", "action": f"held {entity}", **kw})

    def cancelled(self, entity, at, **kw):
        return self.add(rules.EV_CANCELLED, entity, at, **{"source": "user", "reason": "role closed",
                                                           "action": f"cancel {entity}", **kw})

    def rescheduled(self, entity, at, when="2026-11-06", **kw):
        return self.add(rules.EV_RESCHEDULED, entity, at, **{**ifold.rescheduled_fields(when), **kw})

    def debriefed(self, entity, at, **kw):
        return self.add(rules.EV_DEBRIEFED, entity, at,
                        **{"action": f"debrief {entity}", "artifacts": [f"rounds/{entity}.md", f"sha256:{SHA}"], **kw})


def test_round_added_and_rescheduled_fields_are_the_writers_of_their_shapes():
    f = ifold.round_added_fields(REL, SHA, JOB, "2026-11-05T15:00:00Z", stage="technical")
    assert f["artifacts"] == [REL, f"sha256:{SHA}", f"job:{JOB}", "when:2026-11-05T15:00:00Z"]
    assert "technical" in f["action"] and "2026-11-05T15:00:00Z" in f["action"]
    assert ifold.rescheduled_fields("2026-11-06") == {"action": "reschedule to 2026-11-06", "artifacts": ["when:2026-11-06"]}
    for args in [("", SHA, JOB, "2026-11-05"), (REL, "C" * 64, JOB, "2026-11-05"), (REL, SHA, "job_bad", "2026-11-05"),
                 (REL, SHA, JOB, "2026-02-30"), (REL, SHA, JOB, "2026-11-05T15:00:00")]:
        with pytest.raises(ValueError):
            ifold.round_added_fields(*args)
    for bad in ("2026-02-30", "x", None, "2026-11-05T15:00"):
        with pytest.raises(ValueError):
            ifold.rescheduled_fields(bad)


def test_rejected_fields():
    f = ifold.rejected_fields("held", RND, "wrong_state", "the round is cancelled")
    assert f == {"action": f"rejected held of {RND}", "reason": "wrong_state: the round is cancelled"}
    with pytest.raises(ValueError):
        ifold.rejected_fields("held", "per_abcdefghjk", "wrong_state", "x")
    with pytest.raises(ValueError):
        ifold.rejected_fields("held", RND, "nope", "x")


def test_a_full_lifecycle_folds_to_the_expected_flags():
    L = Ledger()
    L.round_added(RND, ts(1), when="2026-11-05")
    L.rescheduled(RND, ts(2), when="2026-11-07T10:00:00Z")
    L.held(RND, ts(3), source="observed")
    L.debriefed(RND, ts(4))
    result = ifold.fold_rounds(L.events, NOW)
    assert result.errors == ()
    assert result.flags == {RND: RoundFlags(job=JOB, added_at=ts(1), held_at=ts(3), debriefed_at=ts(4),
                                            recorded_when="2026-11-07T10:00:00Z")}
    assert result.flags[RND].label == "debriefed"


def test_cancel_keeps_its_reason_and_is_terminal():
    L = Ledger()
    L.round_added(RND, ts(1))
    L.cancelled(RND, ts(2), reason="they withdrew")
    for op in (L.held, L.cancelled, L.rescheduled, L.debriefed):
        op(RND, ts(3))
    result = ifold.fold_rounds(L.events, NOW)
    assert result.flags[RND] == RoundFlags(job=JOB, added_at=ts(1), cancelled_at=ts(2), cancel_reason="they withdrew",
                                           recorded_when="2026-11-05")
    assert len(result.errors) == 4


def test_rescheduled_any_number_of_times_only_while_scheduled():
    L = Ledger()
    L.round_added(RND, ts(1))
    for n in range(1, 8):
        L.rescheduled(RND, ts(1 + n), when=f"2026-11-{10 + n:02d}")
    result = ifold.fold_rounds(L.events, NOW)
    assert result.errors == () and result.flags[RND].recorded_when == "2026-11-17"
    L.held(RND, ts(20))
    L.rescheduled(RND, ts(21), when="2026-12-01")
    result = ifold.fold_rounds(L.events, NOW)
    assert len(result.errors) == 1 and result.flags[RND].recorded_when == "2026-11-17"


def test_label_precedence_for_every_flag_combination():
    for held, cancelled, debriefed in itertools.product([None, ts(2)], repeat=3):
        flags = RoundFlags(job=JOB, added_at=ts(1), held_at=held, cancelled_at=cancelled, debriefed_at=debriefed,
                           recorded_when="2026-11-05")
        expected = ("cancelled" if cancelled else "debriefed" if debriefed else "held" if held else "scheduled")
        assert flags.label == expected


def test_apply_event_table():
    s = RoundFlags(job=JOB, added_at=ts(1), recorded_when="2026-11-05")
    held = ifold.apply_event(s, rules.EV_HELD, ts(2))
    assert held.held_at == ts(2) and held.label == "held"
    assert ifold.apply_event(s, rules.EV_RESCHEDULED, ts(2), when="2026-11-09").recorded_when == "2026-11-09"
    assert ifold.apply_event(s, rules.EV_CANCELLED, ts(2), reason="r").cancel_reason == "r"
    assert ifold.apply_event(s, rules.EV_DEBRIEFED, ts(2)) == ifold.Refusal("wrong_state", "scheduled")
    assert ifold.apply_event(held, rules.EV_HELD, ts(3)) == ifold.Refusal("wrong_state", "held")
    assert ifold.apply_event(held, rules.EV_CANCELLED, ts(3), reason="r") == ifold.Refusal("wrong_state", "held")
    assert ifold.apply_event(held, rules.EV_RESCHEDULED, ts(3), when="2026-11-09") == ifold.Refusal("wrong_state", "held")
    deb = ifold.apply_event(held, rules.EV_DEBRIEFED, ts(3))
    assert deb.label == "debriefed" and ifold.apply_event(deb, rules.EV_DEBRIEFED, ts(4)) == ifold.Refusal("wrong_state", "debriefed")
    for bad in ("interview.nope", rules.EV_ROUND_ADDED, rules.EV_REJECTED):
        with pytest.raises(ValueError):
            ifold.apply_event(s, bad, ts(2))
    with pytest.raises(ValueError):
        ifold.apply_event(s, rules.EV_RESCHEDULED, ts(2))          # a reschedule needs its date


def test_events_before_round_added_and_a_second_round_added_are_reported_and_not_applied():
    L = Ledger()
    L.held(RND, ts(1))
    result = ifold.fold_rounds(L.events, NOW)
    assert result.flags == {} and len(result.errors) == 1
    L.round_added(RND, ts(2))
    L.round_added(RND, ts(3), job=JOB2, when="2026-12-12")
    result = ifold.fold_rounds(L.events, NOW)
    assert result.flags[RND] == RoundFlags(job=JOB, added_at=ts(2), recorded_when="2026-11-05")
    assert len(result.errors) == 2


def test_unknown_interview_types_are_reported_and_other_prefixes_ignored():
    L = Ledger()
    L.round_added(RND, ts(1))
    L.add("interview.held_maybe", RND, ts(2))
    L.add("interview.", RND, ts(3))
    L.add("outreach.accepted", RND, ts(4), source="user")
    L.add("state.transition", JOB, ts(5))
    result = ifold.fold_rounds(L.events, NOW)
    assert len(result.errors) == 2 and result.flags[RND].label == "scheduled"
    assert all("unknown" in e.message for e in result.errors)


def test_non_object_events_unreadable_ts_and_seq_order():
    L = Ledger()
    L.round_added(RND, ts(1))
    L.held(RND, "yesterday")
    events = ["junk", 5, None] + L.events
    result = ifold.fold_rounds(events, NOW)
    assert len(result.errors) == 4 and result.flags[RND].label == "scheduled"
    L2 = Ledger()
    L2.round_added(RND, ts(1))
    L2.held(RND, ts(2))
    assert ifold.fold_rounds(list(reversed(L2.events)), NOW) == ifold.fold_rounds(L2.events, NOW)


def test_fold_rounds_needs_a_readable_now():
    with pytest.raises(ValueError):
        ifold.fold_rounds([], "now")


# --- malformed events: reported (INT005), never applied, no flags entry, no slot used ---------------------------------------

BAD_SOURCES = (None, "carrier-pigeon", "USER", 7, "")
MALFORMED = (
    [("held", {"source": s}) for s in BAD_SOURCES]
    + [("cancelled", {"source": s}) for s in BAD_SOURCES]
    + [("cancelled", {"reason": r}) for r in (None, "", "   ", 3)]
    + [("rescheduled", {"artifacts": a}) for a in (None, [], ["2026-11-06"], ["when:2026-02-30"], ["when:"], [5],
                                                    ["when:2026-11-06", "when:2026-11-07"], "when:2026-11-06",
                                                    ["when:2026-11-06T10:00:00"])]
)
MALFORMED_IDS = [f"{k}-{'-'.join(f'{a}={b!r}' for a, b in kw.items())}"[:60] for k, kw in MALFORMED]
BAD_ADDS = [
    {"artifacts": None}, {"artifacts": [REL, f"sha256:{SHA}"]}, {"artifacts": [REL, f"sha256:{SHA}", f"job:{JOB}"]},
    {"artifacts": [REL, f"sha256:{SHA}", "job:job_bad", "when:2026-11-05"]},
    {"artifacts": [REL, f"sha256:{SHA}", f"job:{JOB}", "when:2026-02-30"]},
    {"artifacts": [REL, f"sha256:{SHA}", f"job:{JOB}", "when:tomorrow"]},
    {"artifacts": [REL, f"sha256:{SHA}", f"jobs:{JOB}", "when:2026-11-05"]},
    {"artifacts": [REL, f"sha256:{SHA}", "when:2026-11-05", f"job:{JOB}"]},
    {"artifacts": [REL, f"sha256:{SHA}", f"job:{JOB}", "when:2026-11-05", "extra"]},
    {"artifacts": [REL, f"sha256:{SHA}", f"job:{JOB}", 5]},
]


@pytest.mark.parametrize("kind,bad", MALFORMED, ids=MALFORMED_IDS)
def test_a_malformed_event_is_reported_not_applied_and_a_later_valid_one_applies(kind, bad):
    L = Ledger()
    L.round_added(RND, ts(1))
    getattr(L, kind)(RND, ts(2), **bad)
    result = ifold.fold_rounds(L.events, NOW)
    assert len(result.errors) == 1 and result.flags[RND] == RoundFlags(job=JOB, added_at=ts(1), recorded_when="2026-11-05")
    getattr(L, kind)(RND, ts(3))
    result = ifold.fold_rounds(L.events, NOW)
    assert len(result.errors) == 1
    applied = result.flags[RND]
    assert {"held": applied.held_at, "cancelled": applied.cancelled_at,
            "rescheduled": applied.recorded_when == "2026-11-06" and ts(3)}[kind] == ts(3)


@pytest.mark.parametrize("bad", BAD_ADDS, ids=[str(b)[:60] for b in BAD_ADDS])
def test_a_malformed_round_added_creates_no_flags_entry_and_a_later_valid_one_applies(bad):
    L = Ledger()
    L.round_added(RND, ts(1), **bad)
    L.held(RND, ts(2))
    result = ifold.fold_rounds(L.events, NOW)
    assert result.flags == {} and len(result.errors) == 2
    L.round_added(RND, ts(3))
    L.held(RND, ts(4))
    result = ifold.fold_rounds(L.events, NOW)
    assert result.flags[RND] == RoundFlags(job=JOB, added_at=ts(3), held_at=ts(4), recorded_when="2026-11-05")


@pytest.mark.parametrize("entity", [None, "", 5, "rnd_bad", "per_abcdefghjk", RND.upper(), RND + " "])
@pytest.mark.parametrize("kind", ["round_added", "held", "cancelled", "rescheduled", "debriefed"])
def test_a_bad_entity_is_reported_and_creates_nothing(kind, entity):
    L = Ledger()
    getattr(L, kind)(entity, ts(1))
    result = ifold.fold_rounds(L.events, NOW)
    assert result.flags == {} and len(result.errors) == 1


def test_a_round_added_with_a_bad_rel_or_sha_is_reported_but_applied():
    for arts in ([5, f"sha256:{SHA}", f"job:{JOB}", "when:2026-11-05"], [REL, "sha256:x", f"job:{JOB}", "when:2026-11-05"]):
        L = Ledger()
        L.round_added(RND, ts(1), artifacts=arts)
        result = ifold.fold_rounds(L.events, NOW)
        assert len(result.errors) == 1 and result.flags[RND].recorded_when == "2026-11-05"


def test_rejections_are_audit_only():
    L = Ledger()
    L.add(rules.EV_REJECTED, RND, ts(1), reason="wrong_state: x")
    assert ifold.fold_rounds(L.events, NOW) == ifold.RoundsFold({}, ())


# --- the fold against an independent table-driven simulator -------------------------------------------------------------------

# The simulator keeps its own literal rules; it shares nothing with rules.py or fold.py.
SIM_OPS = ("add", "held", "cancelled", "rescheduled", "debriefed",
           "bad_add", "bad_held", "bad_cancelled", "bad_rescheduled", "bad_entity", "unknown")


def simulate(ops):
    """ops: [(kind, ts, when)] in ledger order -> (flags dict or None, error count)."""
    s = None
    errors = 0
    for kind, at, when in ops:
        if kind.startswith("bad_") or kind == "unknown":
            errors += 1
            continue
        if kind == "add":
            if s is not None:
                errors += 1
            else:
                s = {"job": JOB, "added_at": at, "held_at": None, "cancelled_at": None, "debriefed_at": None,
                     "cancel_reason": None, "recorded_when": when}
            continue
        if s is None:
            errors += 1
            continue
        scheduled = s["held_at"] is None and s["cancelled_at"] is None and s["debriefed_at"] is None
        if kind == "held" and scheduled:
            s["held_at"] = at
        elif kind == "cancelled" and scheduled:
            s["cancelled_at"], s["cancel_reason"] = at, "reason " + at
        elif kind == "rescheduled" and scheduled:
            s["recorded_when"] = when
        elif kind == "debriefed" and s["held_at"] is not None and s["debriefed_at"] is None and s["cancelled_at"] is None:
            s["debriefed_at"] = at
        else:
            errors += 1
    return s, errors


def sim_label(s):
    if s["cancelled_at"]:
        return "cancelled"
    if s["debriefed_at"]:
        return "debriefed"
    return "held" if s["held_at"] else "scheduled"


def build(ops, rng, noise):
    L = Ledger()
    for kind, at, when in ops:
        if noise and rng.random() < 0.4:
            L.add("interview.rejected", RND, at, reason="wrong_state: refused")
        if kind == "add":
            L.round_added(RND, at, when=when)
        elif kind == "held":
            L.held(RND, at)
        elif kind == "cancelled":
            L.cancelled(RND, at, reason="reason " + at)
        elif kind == "rescheduled":
            L.rescheduled(RND, at, when=when)
        elif kind == "debriefed":
            L.debriefed(RND, at)
        elif kind == "bad_add":
            L.round_added(RND, at, artifacts=rng.choice(BAD_ADDS)["artifacts"])
        elif kind == "bad_entity":
            getattr(L, rng.choice(["round_added", "held", "cancelled", "rescheduled", "debriefed"]))("rnd_bad", at)
        elif kind == "unknown":
            L.add(rng.choice(["interview.nope", "interview.Held", "interview.held "]), RND, at, source="user")
        else:
            real, bad = rng.choice([m for m in MALFORMED if m[0] == kind[4:]])
            getattr(L, real)(RND, at, **bad)
        if noise and rng.random() < 0.3:
            L.add("interview.rejected", RND, at, reason="wrong_state: refused again")
    return L.events


def test_fold_agrees_with_the_independent_simulator_over_20000_random_sequences_and_noise_changes_nothing():
    rng = random.Random(20261031)
    weights = (6, 3, 2, 3, 2, 1, 1, 1, 1, 1, 1)
    for n in range(20_000):
        ops = [(kind, ts(i + 1), rng.choice(["2026-11-05", "2026-11-06T09:00:00Z", f"2026-12-{rng.randrange(1, 29):02d}"]))
               for i, kind in enumerate(rng.choices(SIM_OPS, weights, k=rng.randrange(1, 10)))]
        events = build(ops, random.Random(n), noise=False)
        noisy = build(ops, random.Random(n), noise=True)
        expected, errors = simulate(ops)
        result = ifold.fold_rounds(events, NOW)
        if expected is None:
            assert result.flags == {}, ops
        else:
            assert result.flags == {RND: RoundFlags(**expected)}, ops
            assert result.flags[RND].label == sim_label(expected), ops
        assert len(result.errors) == errors, (ops, result.errors)
        noisy_result = ifold.fold_rounds(noisy, NOW)
        assert noisy_result.flags == result.flags and len(noisy_result.errors) == errors, ops


# --- derivations ---------------------------------------------------------------------------------------------------------------

def rec(rid=RND, when="2026-11-05", job=JOB, debrief=None, stage="technical"):
    return Round(rid, job, stage, "video", when, (Interviewer("Alex Example", ""),), debrief, "")


def fl(when="2026-11-05", job=JOB, held=None, cancelled=None, debriefed=None, added=ts(1)):
    return RoundFlags(job=job, added_at=added, held_at=held, cancelled_at=cancelled, debriefed_at=debriefed,
                      cancel_reason="r" if cancelled else None, recorded_when=when)


@pytest.mark.parametrize("now,overdue", [
    ("2026-11-05T23:59:59Z", False), ("2026-11-06T00:00:00Z", True), ("2026-11-06T00:00:01Z", True),
    ("2026-11-05T00:00:00Z", False), ("2026-11-04T12:00:00Z", False),
])
def test_needs_update_for_a_date_only_value(now, overdue):
    assert derive.needs_update(rec(when="2026-11-05"), fl(), now) is overdue


@pytest.mark.parametrize("now,overdue", [
    ("2026-11-05T14:59:59Z", False), ("2026-11-05T15:00:00Z", False), ("2026-11-05T15:00:01Z", True),
])
def test_needs_update_for_a_datetime_value(now, overdue):
    assert derive.needs_update(rec(when="2026-11-05T15:00:00Z"), fl(), now) is overdue


def test_needs_update_needs_the_scheduled_label_and_a_record():
    late = "2027-01-01T00:00:00Z"
    assert derive.needs_update(rec(), fl(), late)
    assert not derive.needs_update(rec(), fl(held=ts(2)), late)
    assert not derive.needs_update(rec(), fl(cancelled=ts(2)), late)
    assert not derive.needs_update(None, fl(), late)
    with pytest.raises(ValueError):
        derive.needs_update(rec(), fl(), "late")


def jobs(*refs):
    return [JobRef(j, status, status in rules.TERMINAL_JOB_STATUSES) for j, status in refs]


def test_upcoming_window_is_inclusive_at_n_days():
    now = "2026-11-01T12:00:00Z"
    rounds = {RND: rec(RND, "2026-11-15T12:00:00Z"), RND2: rec(RND2, "2026-11-15T12:00:01Z"), RND3: rec(RND3, "2026-11-15")}
    flags = {r: fl(when=v.scheduled_for) for r, v in rounds.items()}
    got = derive.build_upcoming(rounds, flags, jobs((JOB, "SCREEN")), {}, now, 14)
    assert [i.round_id for i in got] == [RND3, RND]
    assert [i.round_id for i in derive.build_upcoming(rounds, flags, [], {}, now, 13)] == []
    rounds2 = {RND: rec(RND, "2026-11-16")}
    assert derive.build_upcoming(rounds2, {RND: fl(when="2026-11-16")}, [], {}, now, 14) == []


def test_upcoming_includes_today_and_now_but_not_overdue_rounds():
    now = "2026-11-05T15:00:00Z"
    rounds = {RND: rec(RND, "2026-11-05"), RND2: rec(RND2, "2026-11-05T15:00:00Z"), RND3: rec(RND3, "2026-11-05T14:59:59Z")}
    flags = {r: fl(when=v.scheduled_for) for r, v in rounds.items()}
    got = derive.build_upcoming(rounds, flags, [], {}, now, 0)
    assert [i.round_id for i in got] == [RND, RND2]


def test_upcoming_rows_order_labels_prep_and_date_unrecorded():
    now = "2026-11-01T00:00:00Z"
    rounds = {RND: rec(RND, "2026-11-03"), RND2: rec(RND2, "2026-11-02T10:00:00Z", job=JOB2), RND3: rec(RND3, "2026-11-03")}
    flags = {RND: fl(when="2026-11-02"), RND2: fl(when="2026-11-02T10:00:00Z", job=JOB2), RND3: fl(when="2026-11-03")}
    got = derive.build_upcoming(rounds, flags, jobs((JOB, "SCREEN"), (JOB2, "HM")), {JOB: "checked"}, now, 14)
    assert [(i.round_id, i.scheduled_for, i.prep, i.date_unrecorded) for i in got] == [
        (RND2, "2026-11-02T10:00:00Z", "none", False), (RND, "2026-11-03", "checked", True), (RND3, "2026-11-03", "checked", False)]
    first = got[0]
    assert (first.job_id, first.stage, first.format, first.interviewers) == (JOB2, "technical", "video",
                                                                               (Interviewer("Alex Example", ""),))
    held = {**flags, RND2: fl(when="2026-11-02T10:00:00Z", job=JOB2, held=ts(5))}
    assert RND2 not in [i.round_id for i in derive.build_upcoming(rounds, held, [], {}, now, 14)]


def test_int007_advice_for_a_pre_interview_job_names_no_target_state():
    # no single `--to` is legal from every status DISCOVERED..APPLIED (state_machine.is_legal), so none is suggested
    targets = {s for s in models.State if all(state_machine.is_legal(models.State(p), s) for p in PRE)} - {models.State.WITHDRAWN}
    assert targets == set()
    for status in PRE:
        issues = derive.status_issues({RND: rec(RND, "2026-12-20")}, {RND: fl(when="2026-12-20")}, jobs((JOB, status)),
                                      "2026-12-01T00:00:00Z")
        assert len(issues) == 1 and "careeros transition" in issues[0][2] and "--to" not in issues[0][2], status


def test_upcoming_refuses_a_bad_days_or_now():
    for days in (-1, True, 1.5, "3", None, 3_000_000, 10**9, 10**30):
        with pytest.raises(ValueError):
            derive.build_upcoming({}, {}, [], {}, NOW, days)
    with pytest.raises(ValueError):
        derive.build_upcoming({}, {}, [], {}, "now", 14)


def test_needs_debrief_groups_ordering_and_the_terminal_job_exclusion():
    now = "2026-12-01T00:00:00Z"
    R = [f"rnd_{c}bcdefghjk" for c in "abcdefg"]
    rounds = {R[0]: rec(R[0], "2026-11-05"), R[1]: rec(R[1], "2026-11-04"), R[2]: rec(R[2], "2026-11-03", job=JOB2),
              R[3]: rec(R[3], "2026-11-02"), R[4]: rec(R[4], "2026-11-02T10:00:00Z"), R[5]: rec(R[5], "2026-12-25"),
              R[6]: rec(R[6], "2026-11-01", job=JOB2)}
    flags = {R[0]: fl(held=ts(30)), R[1]: fl(when="2026-11-04", held=ts(20)), R[2]: fl(when="2026-11-03", held=ts(25), job=JOB2),
             R[3]: fl(when="2026-11-02"), R[4]: fl(when="2026-11-02T10:00:00Z"), R[5]: fl(when="2026-12-25"),
             R[6]: fl(when="2026-11-01", job=JOB2)}
    j = jobs((JOB, "TECHNICAL"), (JOB2, "REJECTED"))
    out = derive.build_needs_debrief(rounds, flags, j, now)
    assert [r.round_id for r in out.needs_debrief] == [R[1], R[2], R[0]]
    assert [r.round_id for r in out.needs_update] == [R[3], R[4]]
    assert [r.held_at for r in out.needs_debrief] == [ts(20), ts(25), ts(30)]
    assert all(not r.incomplete and not r.date_unrecorded for r in out.needs_debrief + out.needs_update)
    for n in range(50):
        keys = list(rounds)
        random.Random(n).shuffle(keys)
        assert derive.build_needs_debrief({k: rounds[k] for k in keys}, {k: flags[k] for k in reversed(keys)},
                                          list(reversed(j)), now) == out


def test_ties_break_by_id_and_nothing_is_truncated():
    now = "2026-12-01T00:00:00Z"
    rng = random.Random(5)
    alphabet = "0123456789abcdefghjkmnpqrstvwxyz"
    rids = sorted({"rnd_" + "".join(rng.choice(alphabet) for _ in range(10)) for _ in range(500)})
    shuffled = list(rids)
    rng.shuffle(shuffled)                   # insertion order must not survive: a stable sort without the id would keep it
    assert shuffled != rids
    rounds = {r: rec(r, "2026-11-05") for r in shuffled}
    flags = {r: fl(held=ts(7)) for r in shuffled}
    out = derive.build_needs_debrief(rounds, flags, [], now)
    assert [r.round_id for r in out.needs_debrief] == rids
    late = derive.build_needs_debrief(rounds, {r: fl(when="2026-11-05") for r in shuffled}, [], now)
    assert [r.round_id for r in late.needs_update] == rids and late.needs_debrief == ()
    up_rounds = {r: rec(r, "2026-12-05") for r in shuffled}
    up = derive.build_upcoming(up_rounds, {r: fl(when="2026-12-05") for r in shuffled}, [], {}, now, 14)
    assert [i.round_id for i in up] == rids


def test_findings_are_ordered_by_round_id_then_code():
    now = "2026-12-01T00:00:00Z"
    d = Debrief("weak", (), (), "wait", None)
    order = [RND3, RND, RND2]               # deliberately not sorted
    rounds = {r: rec(r, "2026-11-01", debrief=d) for r in order}
    flags = {r: fl(when="2026-10-30") for r in order}
    status = derive.status_issues(rounds, flags, jobs((JOB, "APPLIED")), now)
    assert [(c, r) for c, r, _ in status] == [("INT007", RND), ("INT100", RND), ("INT007", RND2), ("INT100", RND2),
                                              ("INT007", RND3), ("INT100", RND3)]
    found = derive.record_issues(rounds, flags)                     # scheduled: INT008 only (INT009 needs held)
    assert [(c, r) for c, r, _ in found] == [("INT008", RND), ("INT008", RND2), ("INT008", RND3)]
    held = {r: fl(when="2026-10-30", held=ts(2)) for r in order}    # held: INT009 only (INT008 needs scheduled)
    assert [(c, r) for c, r, _ in derive.record_issues(rounds, held)] == [("INT009", RND), ("INT009", RND2),
                                                                         ("INT009", RND3)]


def test_incomplete_rounds_in_the_views():
    now = "2026-12-01T00:00:00Z"
    rounds = {RND2: rec(RND2, "2026-11-05")}                         # RND and RND3 have no usable record
    flags = {RND: fl(held=ts(20)), RND2: fl(held=ts(30)), RND3: fl(when="2026-11-02")}
    out = derive.build_needs_debrief(rounds, flags, [], now)
    assert [(r.round_id, r.incomplete) for r in out.needs_debrief] == [(RND, True), (RND2, False)]
    inc = out.needs_debrief[0]
    assert (inc.job_id, inc.stage, inc.format, inc.scheduled_for, inc.held_at) == (JOB, None, None, None, ts(20))
    assert out.needs_update == ()
    explicit_none = derive.build_needs_debrief({**rounds, RND: None, RND3: None}, flags, [], now)
    assert explicit_none == out
    up_flags = {RND: fl(when="2026-12-03"), RND2: fl(when="2026-12-03")}
    up = derive.build_upcoming({RND2: rec(RND2, "2026-12-03"), RND: None}, up_flags, [], {}, now, 14)
    assert [i.round_id for i in up] == [RND2]


def test_orphan_records_feed_no_view():
    now = "2026-12-01T00:00:00Z"
    rounds = {RND: rec(RND, "2026-12-03"), RND2: rec(RND2, "2026-11-01")}
    assert derive.build_upcoming(rounds, {}, [], {}, now, 14) == []
    assert derive.build_needs_debrief(rounds, {}, [], now) == derive.NeedsDebrief((), ())
    assert derive.status_issues(rounds, {}, [], now) == [] and derive.record_issues(rounds, {}) == []


def test_needs_update_rows_carry_date_unrecorded():
    now = "2026-12-01T00:00:00Z"
    out = derive.build_needs_debrief({RND: rec(RND, "2026-11-07")}, {RND: fl(when="2026-11-05")}, [], now)
    assert [(r.round_id, r.scheduled_for, r.date_unrecorded) for r in out.needs_update] == [(RND, "2026-11-07", True)]


PRE = ("DISCOVERED", "EVALUATED", "SHORTLISTED", "RESEARCHED", "PREPARING", "READY_TO_APPLY", "APPROVAL_REQUIRED", "APPLIED")
STAGED = ("RECRUITER_REPLIED", "SCREEN", "TECHNICAL", "HM", "FINAL", "OFFER")
TERMINAL = ("ACCEPTED", "REJECTED", "WITHDRAWN")


@pytest.mark.parametrize("status", PRE + STAGED + TERMINAL)
@pytest.mark.parametrize("label", ["scheduled", "overdue", "held", "debriefed", "cancelled"])
def test_int007_status_consistency(status, label):
    now = "2026-12-01T00:00:00Z"
    when = "2026-11-01" if label == "overdue" else "2026-12-20"
    flags = {"scheduled": fl(when=when), "overdue": fl(when=when), "held": fl(when=when, held=ts(2)),
             "debriefed": fl(when=when, held=ts(2), debriefed=ts(3)), "cancelled": fl(when=when, cancelled=ts(2))}[label]
    got = [code for code, rid, _ in derive.status_issues({RND: rec(RND, when)}, {RND: flags}, jobs((JOB, status)), now)
           if code == "INT007"]
    expected = ((status in PRE and label in ("scheduled", "overdue", "held"))
                or (status in TERMINAL and label in ("scheduled", "overdue")))
    assert got == (["INT007"] if expected else [])


def test_int007_messages_name_a_command_and_int100_for_needs_update():
    now = "2026-12-01T00:00:00Z"
    issues = derive.status_issues({RND: rec(RND, "2026-12-20"), RND2: rec(RND2, "2026-11-01", job=JOB2)},
                                  {RND: fl(when="2026-12-20"), RND2: fl(when="2026-11-01", job=JOB2)},
                                  jobs((JOB, "APPLIED"), (JOB2, "WITHDRAWN")), now)
    assert [(c, r) for c, r, _ in issues] == [("INT007", RND), ("INT007", RND2), ("INT100", RND2)]
    assert "careeros transition" in issues[0][2] and JOB in issues[0][2] and "--to" not in issues[0][2]
    assert "WITHDRAWN" in issues[1][2] and "careeros interview round" in issues[1][2]
    assert derive.status_issues({RND: rec(RND, "2026-12-20")}, {RND: fl(when="2026-12-20")}, [], now) == []


def test_status_issues_for_an_incomplete_round_use_the_ledger_job_and_label():
    now = "2026-12-01T00:00:00Z"
    issues = derive.status_issues({}, {RND: fl(when="2026-11-01")}, jobs((JOB, "APPLIED")), now)
    assert [(c, r) for c, r, _ in issues] == [("INT007", RND)]          # no INT100: an incomplete round has no date


def test_record_issues_int008_and_int009():
    d = Debrief("weak", (), (), "wait", None)
    rounds = {RND: rec(RND, "2026-11-05"), RND2: rec(RND2, "2026-11-05", debrief=d), RND3: rec(RND3, "2026-11-05T00:00:00Z")}
    flags = {RND: fl(when="2026-11-05"), RND2: fl(when="2026-11-05", held=ts(2)), RND3: fl(when="2026-11-05")}
    assert [(c, r) for c, r, _ in derive.record_issues(rounds, flags)] == [("INT009", RND2), ("INT008", RND3)]
    flags[RND2] = fl(when="2026-11-05", held=ts(2), debriefed=ts(3))
    assert [(c, r) for c, r, _ in derive.record_issues(rounds, flags)] == [("INT008", RND3)]
    both = {RND: rec(RND, "2026-11-09", debrief=d)}
    (int008,) = derive.record_issues(both, {RND: fl(when="2026-11-05")})
    (int009,) = derive.record_issues(both, {RND: fl(when="2026-11-05", held=ts(2))})
    assert int008[0] == "INT008" and "round reschedule" in int008[2]
    assert int009[0] == "INT009" and "debrief" in int009[2]
    # past the label where the remedy is legal, a differing date or an unrecorded block is not warned
    for past in (fl(when="2026-11-05", held=ts(2), debriefed=ts(3)), fl(when="2026-11-05", cancelled=ts(2))):
        assert derive.record_issues(both, {RND: past}) == []
    assert derive.record_issues({RND: None}, {RND: fl(when="2026-11-09")}) == []        # incomplete
    assert derive.record_issues({}, {RND: fl(when="2026-11-09")}) == []
    assert derive.record_issues({RND: rec(RND, "2026-11-09", debrief=d)}, {}) == []     # orphan


def test_recorded_when_follows_the_latest_applied_event_and_ignores_malformed_ones():
    L = Ledger()
    L.round_added(RND, ts(1), when="2026-11-05")
    L.rescheduled(RND, ts(2), when="2026-11-08")
    L.rescheduled(RND, ts(3), artifacts=["when:2026-02-30"])
    L.rescheduled(RND, ts(4), artifacts=["2026-11-09"])
    flags = ifold.fold_rounds(L.events, NOW).flags
    assert flags[RND].recorded_when == "2026-11-08"
    rounds = {RND: rec(RND, "2026-11-08")}
    assert derive.record_issues(rounds, flags) == []
    assert [c for c, _, _ in derive.record_issues({RND: rec(RND, "2026-11-09")}, flags)] == ["INT008"]


# --- purity and import isolation ------------------------------------------------------------------------------------------------

def _imports(code):
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_importing_the_interview_package_pulls_in_no_heavy_module_and_nothing_from_outreach():
    _imports("import sys, importlib;"
             "[importlib.import_module('careeros.core.interview.' + m) for m in ('rules','_common','model','round','fold','derive')];"
             "import careeros.core.interview;"
             "bad=[m for m in sys.modules if m in ('careeros.core.validation','careeros.core.state_machine',"
             "'careeros.core.approvals.service','careeros.core.ledger') or m.startswith('careeros.cli') "
             "or m.startswith('careeros.core.outreach')];"
             "print(bad); sys.exit(1 if bad else 0)")


def test_modules_read_no_clock_and_no_filesystem():
    import ast
    import inspect

    from careeros.core.interview import _common, model
    for module in (rules, _common, model, rround, ifold, derive):
        tree = ast.parse(inspect.getsource(module))
        imported = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names} | \
                   {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
        roots = {m.split(".")[0] for m in imported}
        assert not roots & {"os", "pathlib", "time", "shutil", "tempfile", "subprocess", "socket", "random", "secrets"}, module.__name__
        assert not any(m.startswith("careeros.core.outreach") for m in imported), module.__name__
        src = inspect.getsource(module)
        assert "utc_now" not in src and "datetime.now" not in src and "datetime.utcnow" not in src and "open(" not in src, module.__name__
