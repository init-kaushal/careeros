"""Outreach derivations (Task B): the weekly invite cap, the follow-up queue, `next` and `warm`. Pure; invented data only.
The reference models here (the brute-force cap counter, the timing arithmetic) are written independently of the
production code and share no table with it.
"""

from __future__ import annotations

import ast
import inspect
import random
import subprocess
import sys
from datetime import datetime, timedelta

import pytest

from careeros.core.approvals import cap
from careeros.core.approvals import fold as afold
from careeros.core.approvals.model import ItemState, State
from careeros.core.outreach import derive
from careeros.core.outreach.derive import TargetInfo
from careeros.core.outreach.model import FoundIn, JobInfo, Person, PersonFlags, Target, TargetView
from careeros.core.outreach.slug import slugify

FMT = "%Y-%m-%dT%H:%M:%SZ"
T0 = datetime(2026, 1, 1, 0, 0, 0)
NOW = "2026-03-01T00:00:00Z"
NOW_DT = datetime(2026, 3, 1)
DAY = timedelta(days=1)
SHA = "c" * 64


def at(delta=timedelta(0), base=NOW_DT):
    return (base + delta).strftime(FMT)


def ago(**kw):
    return at(-timedelta(**kw))


# ---------------------------------------------------------------------------------------------------------------------
# cap setting
# ---------------------------------------------------------------------------------------------------------------------

FOLD = "<!-- do not read past this line -->"


def profile(*lines, heading="## Outreach"):
    return "# Profile\n\nSome text.\n\n" + (heading + "\n" if heading else "") + "\n".join(lines) + "\n"


VALID = [
    (profile("Weekly invite cap: 5"), 5),
    (profile("- Weekly invite cap: 5"), 5),
    (profile("* Weekly invite cap: 5"), 5),
    (profile("+ Weekly invite cap: 5"), 5),
    (profile("1. Weekly invite cap: 5"), 5),
    (profile("- **Weekly invite cap: 5**"), 5),
    (profile("**Weekly invite cap:** 5"), 5),
    (profile("- **Weekly invite cap**: 5"), 5),
    (profile("__Weekly invite cap: 5__"), 5),
    (profile("Weekly invite cap: 0"), 0),
    (profile("Weekly invite cap: 1000"), 1000),
    (profile("Weekly invite cap:   12   "), 12),
    (profile("weekly invite cap: 7"), 7),
    (profile("Weekly invite cap: 007"), 7),
    (profile("", "Other prose.", "", "- Weekly invite cap: 9", "", "More prose."), 9),
    (profile("Weekly invite cap: 5", heading="## outreach"), 5),
    (profile("### Sub heading", "Weekly invite cap: 4"), 4),
    ("## Outreach\r\nWeekly invite cap: 6\r\n", 6),
    ("## Outreach\nWeekly invite cap: 3\n## Later\nWeekly invite cap: 99\n", 3),
    ("# Profile\n## Outreach\nWeekly invite cap: 8", 8),
]


@pytest.mark.parametrize("text,value", VALID)
def test_valid_cap_settings(text, value):
    assert cap.parse_cap_setting(text) == cap.CapSetting(value, None)
    assert cap.effective_cap(cap.parse_cap_setting(text)) == value


ABSENT = [
    "",
    "# Profile\nno setting here\n",
    profile("Some words only."),
    profile(heading="## Outreach"),
    profile("Weekly invite cap: 5", heading=""),
    profile("Weekly invite cap: 5", heading="## Other"),
    profile("Weekly invite cap: 5", heading="### Outreach"),
    profile("Weekly invite cap: 5", heading="# Outreach notes"),
    f"## Outreach\nIntro\n{FOLD}\nWeekly invite cap: 5\n",
    f"{FOLD}\n## Outreach\nWeekly invite cap: 5\n",
    "## Outreach\n## Later\nWeekly invite cap: 5\n",
    "## Outreach\n# Top\nWeekly invite cap: 5\n",
    "## Outreach\nThe weekly invite cap is whatever.\n",
]


@pytest.mark.parametrize("text", ABSENT)
def test_absent_cap_setting_means_the_default(text):
    setting = cap.parse_cap_setting(text)
    assert setting == cap.CapSetting(None, None)
    assert cap.effective_cap(setting) == 15 == cap.DEFAULT_WEEKLY_CAP


INVALID = [
    "Weekly invite cap: abc",
    "Weekly invite cap: -1",
    "Weekly invite cap: 1001",
    "Weekly invite cap: 1.5",
    "Weekly invite cap: ",
    "Weekly invite cap:",
    "Weekly invite cap: 5 per week",
    "Weekly invite cap 5",
    "Weekly invite cap = 5",
    "Weekly invite cap: +5",
    "Weekly invite cap: 1e1",
    "Weekly invite cap: 5,0",
    "Weekly invite cap: ５",
    "Weekly invite cap: ٥",
    "Weekly invite cap: 99999999999999999999",
    "- Weekly invite cap: unlimited",
]


@pytest.mark.parametrize("line", INVALID)
def test_invalid_cap_settings_fail_closed(line):
    setting = cap.parse_cap_setting(profile(line))
    assert setting.value is None and setting.problem
    refusal = cap.effective_cap(setting)
    assert isinstance(refusal, cap.CapRefusal) and refusal.code == "cap_config_invalid" == cap.CAP_CONFIG_INVALID
    assert "Weekly invite cap: N" in refusal.message and "0 to 1000" in refusal.message


def test_two_cap_lines_are_invalid_even_when_both_are_valid_or_equal():
    for lines in (("Weekly invite cap: 5", "Weekly invite cap: 6"), ("Weekly invite cap: 5", "- Weekly invite cap: 5"),
                  ("Weekly invite cap: 5", "Weekly invite cap: abc")):
        setting = cap.parse_cap_setting(profile(*lines))
        assert setting.value is None and "more than one" in setting.problem
        assert isinstance(cap.effective_cap(setting), cap.CapRefusal)


def test_the_problem_quotes_the_offending_line():
    assert "'Weekly invite cap: abc'" in cap.parse_cap_setting(profile("Weekly invite cap: abc")).problem
    assert "1001" in cap.parse_cap_setting(profile("Weekly invite cap: 1001")).problem


def test_a_second_line_below_the_fold_or_in_another_section_is_not_counted():
    assert cap.parse_cap_setting(f"## Outreach\nWeekly invite cap: 5\n{FOLD}\nWeekly invite cap: 6\n").value == 5
    assert cap.parse_cap_setting("## Outreach\nWeekly invite cap: 5\n## Other\nWeekly invite cap: 6\n").value == 5


def test_huge_numbers_are_a_problem_not_a_crash():
    for line in ("Weekly invite cap: " + "9" * 5000, "Weekly invite cap: " + "0" * 5000 + "7", "Weekly invite cap: 10000", "Weekly invite cap: 00007"):
        setting = cap.parse_cap_setting(profile(line))
        assert setting.value is None and setting.problem, line[:40]
    assert cap.parse_cap_setting(profile("Weekly invite cap: 0007")).value == 7


def test_pathological_lines_are_bounded_in_time():
    import time
    cases = ["## Outreach\nWeekly invite cap:" + " " * 200000 + "x\n",
             "## Outreach\nWeekly invite cap:" + " " * 200000 + "5\n",
             "## Outreach" + " " * 200000 + "x\nWeekly invite cap: 5\n",
             "## Outreach\n" + " " * 200000 + "\nWeekly invite cap: 5\n",
             "## Outreach\n" + "*" * 200000 + "\nWeekly invite cap: 5\n",
             "## Outreach\nWeekly invite cap" + "*" * 200000 + ": 5\n"]
    start = time.monotonic()
    results = [cap.parse_cap_setting(text) for text in cases]
    assert time.monotonic() - start < 1.0
    assert results[0].problem and results[1].problem          # an over-long line that starts with the label is an invalid setting
    assert results[2] == cap.CapSetting() and results[3].value == 5 and results[4].value == 5
    assert results[5].problem


def test_the_200_character_line_limit_is_pinned():
    assert cap.parse_cap_setting(profile("Weekly invite cap: 5" + "*" * 300)).problem
    assert cap.parse_cap_setting(profile("Weekly invite cap: 5" + "x" * 300)).problem
    head = "- **Weekly invite cap:** 5"
    for total, ok in ((200, True), (201, False)):
        line = head + "*" * (total - len(head))
        assert len(line.strip()) == total
        setting = cap.parse_cap_setting(profile(line))
        assert (setting.value == 5) if ok else (setting.value is None and setting.problem)
    long_heading = "## Outreach" + " x" * 150
    setting = cap.parse_cap_setting(f"## Outreach\n{long_heading}\nWeekly invite cap: 5\n")
    assert setting == cap.CapSetting()                       # an over-long `## Outreach` heading never opens the section
    closing = "## Other" + " x" * 150
    assert cap.parse_cap_setting(f"## Outreach\n{closing}\nWeekly invite cap: 5\n") == cap.CapSetting()


def test_a_leading_bom_does_not_hide_the_setting():
    assert cap.parse_cap_setting("\ufeff## Outreach\nWeekly invite cap: 4\n").value == 4


def test_cap_constants_equal_the_outreach_rules():
    from careeros.core.outreach import rules
    assert (cap.CAP_WINDOW_DAYS, cap.DEFAULT_WEEKLY_CAP, cap.CAP_CONFIG_INVALID) == (
        rules.CAP_WINDOW_DAYS, rules.DEFAULT_WEEKLY_CAP, rules.CAP_CONFIG_INVALID)


def test_non_string_profile_text_is_absent_not_an_exception():
    assert cap.parse_cap_setting(None) == cap.CapSetting()       # type: ignore[arg-type]


# ---------------------------------------------------------------------------------------------------------------------
# invite_count
# ---------------------------------------------------------------------------------------------------------------------

def item(state, *, kind="linkedin_invite", consumed_at=None, target_id="per_abcdefghjk", **extra):
    return ItemState(State(state), kind=kind, target_id=target_id, consumed_at=consumed_at, **extra)


def test_in_flight_counts_at_any_age():
    states = {"apr_1": item("in_flight", started_at=ago(days=400), last_ts=ago(days=400))}
    assert cap.invite_count(states, NOW) == cap.CapCount(1, 0, None)


def test_consumed_counts_inside_the_window_using_the_consumed_ts():
    states = {"apr_1": item("consumed", consumed_at=ago(days=6, hours=23, minutes=59, seconds=59), started_at=ago(days=30))}
    count = cap.invite_count(states, NOW)
    assert (count.in_flight, count.consumed_in_window, count.total) == (0, 1, 1)
    assert count.oldest_leaves == at(timedelta(seconds=1))


def test_exactly_seven_days_is_not_counted_and_one_second_inside_is():
    out = cap.invite_count({"a": item("consumed", consumed_at=ago(days=7))}, NOW)
    assert (out.total, out.oldest_leaves) == (0, None)
    inside = cap.invite_count({"a": item("consumed", consumed_at=ago(days=7) .replace("00Z", "01Z"))}, NOW)
    assert inside.total == 1 and inside.oldest_leaves == at(timedelta(seconds=1))


def test_started_ts_is_never_used():
    old_start = item("consumed", consumed_at=ago(days=1), started_at=ago(days=20), last_ts=ago(days=1))
    recent_start = item("consumed", consumed_at=ago(days=20), started_at=ago(days=1), last_ts=ago(days=1))
    assert cap.invite_count({"a": old_start}, NOW).total == 1
    assert cap.invite_count({"a": recent_start}, NOW).total == 0


def test_in_flight_to_consumed_is_counted_once():
    before = {"a": item("in_flight", started_at=ago(days=2))}
    after = {"a": item("consumed", consumed_at=ago(days=1), started_at=ago(days=2))}
    assert cap.invite_count(before, NOW).total == 1
    assert cap.invite_count(after, NOW) == cap.CapCount(0, 1, at(6 * DAY))


@pytest.mark.parametrize("state", ["pending", "approved", "declined", "expired", "superseded", "voided"])
def test_other_states_do_not_count(state):
    assert cap.invite_count({"a": item(state, consumed_at=ago(days=1))}, NOW).total == 0


def test_messages_and_submits_do_not_count():
    states = {"a": item("in_flight", kind="linkedin_message"), "b": item("consumed", kind="linkedin_message", consumed_at=ago(days=1)),
              "c": item("in_flight", kind="application_submit", target_id="job_abcdefghjk"), "d": ItemState(None)}
    assert cap.invite_count(states, NOW).total == 0


def test_unparseable_consumed_at_counts_and_has_no_leave_date():
    for garbage in (None, "", "yesterday", "2026-03-01", "2026-13-40T00:00:00Z", "2026-03-01T00:00:00+00:00", 17):
        out = cap.invite_count({"a": item("consumed", consumed_at=garbage)}, NOW)
        assert (out.consumed_in_window, out.oldest_leaves) == (1, None), garbage
    mixed = cap.invite_count({"a": item("consumed", consumed_at="garbage"), "b": item("consumed", consumed_at=ago(days=2))}, NOW)
    assert mixed.total == 2 and mixed.oldest_leaves == at(5 * DAY)


def test_a_consumed_ts_in_the_future_counts():
    assert cap.invite_count({"a": item("consumed", consumed_at=at(3 * DAY))}, NOW).total == 1


def test_exclude_omits_only_that_item():
    states = {"a": item("in_flight"), "b": item("in_flight"), "c": item("consumed", consumed_at=ago(days=1))}
    assert cap.invite_count(states, NOW).total == 3
    assert cap.invite_count(states, NOW, exclude="a").total == 2
    assert cap.invite_count(states, NOW, exclude="c") == cap.CapCount(2, 0, None)
    assert cap.invite_count(states, NOW, exclude="zzz").total == 3
    assert cap.invite_count(states, NOW, exclude=None).total == 3


def test_oldest_leaves_is_for_consumed_invites_only():
    assert cap.invite_count({"a": item("in_flight")}, NOW).oldest_leaves is None
    states = {"a": item("in_flight"), "b": item("consumed", consumed_at=ago(days=5)), "c": item("consumed", consumed_at=ago(days=2))}
    assert cap.invite_count(states, NOW).oldest_leaves == at(2 * DAY)


def test_unreadable_now_raises():
    for bad in ("", "now", None, "2026-03-01"):
        with pytest.raises(ValueError):
            cap.invite_count({}, bad)       # type: ignore[arg-type]


def test_remaining():
    count = cap.CapCount(2, 3, None)
    assert [cap.remaining(c, count) for c in (0, 4, 5, 6, 15)] == [0, 0, 0, 1, 10]
    assert cap.remaining(15, cap.CapCount(0, 0, None)) == 15


# --- the independent brute-force reference over random approval event sequences -------------------------------------------

def ev(seq, etype, entity, when, **extra):
    base = {"id": f"evt_{seq:010d}", "seq": seq, "ts": when, "type": etype, "actor": "agent:test", "entity": entity,
            "prev_state": None, "new_state": None, "action": etype, "reason": None, "approval": "not_required",
            "artifacts": [], "source": None, "prev": "0" * 64}
    base.update(extra)
    return base


def proposed(seq, apr, per, kind, when):
    arts = [f"approvals/{apr}.md", "sha256:" + SHA] + ([f"url:https://www.linkedin.com/in/{per}"] if kind != "application_submit" else [])
    return ev(seq, "approval.proposed", apr, when, action=f"propose {kind} for {per}", artifacts=arts)


EVENT_TYPES = ("approved", "started", "consumed", "released", "voided", "declined", "superseded")
# the reference's own legality table: (raw state, event) -> new raw state
LEGAL = {("pending", "approved"): "approved", ("pending", "declined"): "declined", ("pending", "superseded"): "superseded",
         ("approved", "superseded"): "superseded", ("approved", "started"): "in_flight", ("in_flight", "consumed"): "consumed",
         ("in_flight", "released"): "approved", ("in_flight", "voided"): "voided"}


def reference_count(events, now, exclude=None):
    """Walk the events of each invite item in seq order; count in_flight at any age and consumed inside the window."""
    now_dt = datetime.strptime(now, FMT)
    kinds, raw, consumed_ts = {}, {}, {}
    for e in sorted(events, key=lambda e: e["seq"]):
        item_id = e["entity"]
        if e["type"] == "approval.proposed":
            kinds[item_id] = e["action"].split()[1]
            raw[item_id] = "pending"
            continue
        new = LEGAL.get((raw.get(item_id), e["type"].split(".")[1]))
        if new is None:
            continue
        raw[item_id] = new
        if new == "consumed":
            consumed_ts[item_id] = e["ts"]
    in_flight, window, leaves = 0, 0, []
    for item_id, state in raw.items():
        if item_id == exclude or kinds[item_id] != "linkedin_invite":
            continue
        if state == "in_flight":
            in_flight += 1
        elif state == "consumed":
            start = datetime.strptime(consumed_ts[item_id], FMT)
            if now_dt < start + timedelta(days=7):
                window += 1
                leaves.append(start + timedelta(days=7))
    return in_flight, window, (min(leaves).strftime(FMT) if leaves else None)


def random_events(rng):
    events, seq, clock = [], 0, T0
    items = []
    for n in range(rng.randint(1, 5)):
        kind = rng.choice(("linkedin_invite", "linkedin_invite", "linkedin_message", "application_submit"))
        items.append((f"apr_{n}", kind))
    plan = [("proposed", apr, kind) for apr, kind in items]
    plan += [(rng.choice(EVENT_TYPES), apr, None) for apr, _ in items for _ in range(rng.randint(0, 6))]
    rng.shuffle(plan)
    plan.sort(key=lambda p: p[0] != "proposed")             # proposals first (stray events before a proposal are a separate path)
    for what, apr, kind in plan:
        seq += 1
        clock += timedelta(seconds=rng.choice((1, 60, 3600, 86400, 3 * 86400, 6 * 86400 + 86399, 7 * 86400)))
        when = clock.strftime(FMT)
        if what == "proposed":
            events.append(proposed(seq, apr, "per_" + apr[4:], kind, when))
        else:
            extra = {"approved": {}, "voided": {"reason": "no"}}.get(what, {})
            events.append(ev(seq, f"approval.{what}", apr, when, **extra))
    return events


def test_brute_force_reference_agrees_over_random_approval_event_sequences():
    rng = random.Random(20261019)
    checked = 0
    for _ in range(1500):
        events = random_events(rng)
        last = datetime.strptime(events[-1]["ts"], FMT)
        for offset in (0, 1, 86399, 86400, 3 * 86400, 6 * 86400 + 86399, 7 * 86400, 7 * 86400 + 1, 12 * 86400, rng.randint(0, 20 * 86400)):
            now = (last + timedelta(seconds=offset)).strftime(FMT)
            states = afold.fold_all(events, now)
            for exclude in (None, rng.choice(["apr_0", "apr_1", "apr_9"])):
                got = cap.invite_count(states, now, exclude=exclude)
                assert (got.in_flight, got.consumed_in_window, got.oldest_leaves) == reference_count(events, now, exclude), (events, now)
                checked += 1
    assert checked > 10000


def test_shuffled_event_order_does_not_change_the_count():
    rng = random.Random(7)
    for _ in range(200):
        events = random_events(rng)
        now = (datetime.strptime(events[-1]["ts"], FMT) + timedelta(days=2)).strftime(FMT)
        shuffled = events[:]
        rng.shuffle(shuffled)
        assert cap.invite_count(afold.fold_all(shuffled, now), now) == cap.invite_count(afold.fold_all(events, now), now)


def test_the_cap_module_is_pure_and_imports_only_approvals_and_the_fold_leaf():
    tree = ast.parse(inspect.getsource(cap))
    modules = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module} | \
              {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    careeros = {m for m in modules if m.startswith("careeros")}
    assert all(m.startswith("careeros.core.approvals") or m == "careeros.core.fold" for m in careeros), careeros
    assert not {m.split(".")[0] for m in modules} & {"os", "pathlib", "time", "shutil", "tempfile", "subprocess", "socket", "random"}
    source = inspect.getsource(cap)
    assert "utc_now" not in source and "datetime.now" not in source and "open(" not in source


def test_importing_the_cap_pulls_in_no_outreach_and_no_heavy_module():
    code = ("import sys; import careeros.core.approvals.cap;"
            "bad=[m for m in sys.modules if m.startswith('careeros.core.outreach') or m in ('careeros.core.validation',"
            "'careeros.core.state_machine','careeros.core.approvals.service','careeros.core.ledger') or m.startswith('careeros.cli')];"
            "print(bad); sys.exit(1 if bad else 0)")
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stdout + proc.stderr


# ---------------------------------------------------------------------------------------------------------------------
# builders for the outreach derivations
# ---------------------------------------------------------------------------------------------------------------------

JOB = "job_abcdefghjk"
JOB2 = "job_bcdefghjkm"
TGT = "tgt_abcdefghjk"
TGT2 = "tgt_bcdefghjkm"
_COUNTER = iter(range(1, 10**6))


def per_id(n):
    alphabet = "abcdefghjkmnpqrstuvwxyz23456789"
    return "per_" + "".join(alphabet[(n * 7 + i * 3) % len(alphabet)] for i in range(10))


def person(n=1, *, role="recruiter", company="Acme Corp", found_in=(FoundIn("job", JOB),), found_at="2026-01-01", name=None):
    pid = per_id(n)
    return Person(pid, f"https://www.linkedin.com/in/p{n}", name or f"Person {n}", "Headline", company, slugify(company), role,
                  None, tuple(found_in), found_at)


def people_of(*ps):
    return {p.id: p for p in ps}


def job(job_id=JOB, company="Acme Corp", status="SHORTLISTED", events=()):
    return JobInfo(job_id, company, status, tuple(events))


def tinfo(slug="acme-corp", *, tid=TGT, decision="approved", fit=5, sponsorship="n/a", stale=False, market="Somewhere"):
    record = Target(tid, slug, slug.title(), market, fit, sponsorship, ())
    view = TargetView(tid, slug, decision, decision, stale, None, ())
    return TargetInfo(record, view)


def targets_of(*infos):
    return {i.record.slug: i for i in infos}


# ---------------------------------------------------------------------------------------------------------------------
# queue
# ---------------------------------------------------------------------------------------------------------------------

def invited(days=0, seconds=0, **flags):
    return PersonFlags(invited_at=ago(days=days, seconds=seconds), **flags)


def queue_for(ps, fl, jobs=(), targets=None, now=NOW):
    return derive.build_queue(people_of(*ps), fl, targets or {}, list(jobs), now)


def test_invite_due_exactly_at_seven_days_and_not_one_second_before():
    p = person(1)
    due = queue_for([p], {p.id: invited(days=7)})
    assert [(i.kind, i.entity_id, i.days_overdue) for i in due.items] == [("invite", p.id, 0)]
    assert due.items[0].due_at == NOW
    assert queue_for([p], {p.id: invited(days=7, seconds=-1)}).items == ()
    assert queue_for([p], {p.id: invited(days=6)}).items == ()


def test_days_overdue_counts_whole_days_past_the_due_moment():
    p = person(1)
    for days, seconds, expected in ((7, 0, 0), (8, 0, 1), (10, 0, 3), (7, 86399, 0), (7, 86400, 1), (30, 5, 23)):
        item = queue_for([p], {p.id: invited(days=days, seconds=seconds)}).items[0]
        assert item.days_overdue == expected, (days, seconds)


@pytest.mark.parametrize("field", ["accepted_at", "replied_at", "closed_at"])
def test_an_invite_with_an_outcome_is_not_queued(field):
    p = person(1)
    assert queue_for([p], {p.id: invited(days=9, **{field: ago(days=1)})}).items == ()


def test_one_follow_up_message_per_invite():
    p = person(1)
    assert queue_for([p], {p.id: invited(days=9, messaged_at=ago(days=1))}).items == ()
    assert queue_for([p], {p.id: invited(days=9, messaged_at=ago(days=9, seconds=-1))}).items == ()
    # a message that is not after the invite does not count as the follow-up
    assert len(queue_for([p], {p.id: invited(days=9, messaged_at=ago(days=9))}).items) == 1
    assert len(queue_for([p], {p.id: invited(days=9, messaged_at=ago(days=10))}).items) == 1


def test_people_without_an_invite_or_flags_are_not_queued():
    p, q = person(1), person(2)
    assert queue_for([p, q], {p.id: PersonFlags(messaged_at=ago(days=30))}).items == ()


def test_closed_people_are_never_queued():
    p = person(1)
    assert queue_for([p], {p.id: invited(days=30, closed_at=ago(days=1))}).items == ()


def test_pre_opening_contacts_are_excluded_until_a_job_exists_at_the_company():
    p = person(1, company="Acme Corp", found_in=(FoundIn("target", TGT),))
    fl = {p.id: invited(days=9)}
    assert queue_for([p], fl).items == ()
    assert queue_for([p], fl, jobs=[job(company="Other Corp")]).items == ()
    for status in ("SHORTLISTED", "REJECTED", "APPLIED"):          # a saved job of any status ends the pre-opening state
        got = queue_for([p], fl, jobs=[job(company="ACME  corp!", status=status)])
        assert [i.entity_id for i in got.items] == [p.id], status


def test_a_job_at_the_targets_slug_ends_the_pre_opening_state_even_when_the_person_company_differs():
    p = person(1, company="Acme Inc", found_in=(FoundIn("target", TGT),))
    fl = {p.id: invited(days=9)}
    targets = targets_of(tinfo("acme-corp", tid=TGT))
    assert queue_for([p], fl, jobs=[job(company="Acme Corp")]).items == ()
    assert len(queue_for([p], fl, jobs=[job(company="Acme Corp")], targets=targets).items) == 1


def test_a_person_found_in_a_job_is_queued_even_without_a_job_at_the_company():
    p = person(1, found_in=(FoundIn("job", JOB),))
    assert len(queue_for([p], {p.id: invited(days=9)}).items) == 1
    mixed = person(2, found_in=(FoundIn("target", TGT), FoundIn("job", JOB)))
    assert len(queue_for([mixed], {mixed.id: invited(days=9)}).items) == 1


def test_a_flags_only_person_without_a_record_is_still_queued():
    items = derive.build_queue({}, {per_id(9): invited(days=9)}, {}, [], NOW).items
    assert [(i.kind, i.entity_id, i.label) for i in items] == [("invite", per_id(9), None)]


def applied(days=0, seconds=0, extra=(), job_id=JOB, company="Acme Corp", status="APPLIED"):
    events = [(ago(days=40), "SHORTLISTED"), (ago(days=days, seconds=seconds), "APPLIED"), *extra]
    return job(job_id, company, status, events)


def test_application_due_exactly_at_ten_days_and_not_one_second_before():
    got = queue_for([], {}, jobs=[applied(days=10)])
    assert [(i.kind, i.entity_id, i.days_overdue, i.due_at) for i in got.items] == [("application", JOB, 0, NOW)]
    assert queue_for([], {}, jobs=[applied(days=10, seconds=-1)]).items == ()
    assert queue_for([], {}, jobs=[applied(days=9)]).items == ()
    assert queue_for([], {}, jobs=[applied(days=13)]).items[0].days_overdue == 3


def test_application_not_due_after_a_later_status_change_or_when_not_applied():
    assert queue_for([], {}, jobs=[applied(days=12, extra=[(ago(days=1), "SCREEN")], status="SCREEN")]).items == ()
    assert queue_for([], {}, jobs=[applied(days=12, extra=[(ago(days=11), "RECRUITER_REPLIED")], status="RECRUITER_REPLIED")]).items == ()
    # the file says APPLIED but the latest status event does not: nothing is due
    assert queue_for([], {}, jobs=[applied(days=12, extra=[(ago(days=1), "SCREEN")])]).items == ()
    assert queue_for([], {}, jobs=[job(status="APPLIED")]).items == ()              # no status events: nothing to age
    assert queue_for([], {}, jobs=[job(status="SHORTLISTED", events=[(ago(days=20), "SHORTLISTED")])]).items == ()


def test_terminal_jobs_are_never_queued():
    for status in ("ACCEPTED", "REJECTED", "WITHDRAWN"):
        assert queue_for([], {}, jobs=[applied(days=30, extra=[(ago(days=29), status)], status=status)]).items == ()
        assert queue_for([], {}, jobs=[applied(days=30, status=status)]).items == ()   # even a stale APPLIED event on a terminal job


def test_latest_application_event_wins_regardless_of_event_order_in_the_input():
    j = job(JOB, "Acme Corp", "APPLIED", [(ago(days=20), "APPLIED"), (ago(days=30), "SHORTLISTED")])
    assert len(queue_for([], {}, jobs=[j]).items) == 1
    again = job(JOB, "Acme Corp", "APPLIED", [(ago(days=30), "SHORTLISTED"), (ago(days=20), "APPLIED")])
    assert queue_for([], {}, jobs=[again]) == queue_for([], {}, jobs=[j])


def test_the_queue_says_email_and_interview_follow_ups_are_not_covered():
    note = queue_for([], {}).uncovered_note
    assert note == derive.UNCOVERED_NOTE
    assert "email" in note.lower() and "interview" in note.lower() and "not covered" in note.lower()
    assert queue_for([person()], {}).uncovered_note == note


def test_queue_reasons_are_deterministic_text():
    p = person(1, name="Alex Example")
    item = queue_for([p], {p.id: invited(days=9)}).items[0]
    assert item.label == "Alex Example"
    assert item.reason == f"invited {ago(days=9)}; follow-up was due {ago(days=2)}"
    app = queue_for([], {}, jobs=[applied(days=12)]).items[0]
    assert app.label == "Acme Corp"
    assert app.reason == f"applied {ago(days=12)}; follow-up was due {ago(days=2)}"


def test_queue_order_is_most_overdue_first_then_kind_then_id():
    a, b, c = person(1), person(2), person(3)
    fl = {a.id: invited(days=9), b.id: invited(days=12), c.id: invited(days=9)}
    j = applied(days=15, job_id=JOB2)
    got = queue_for([a, b, c], fl, jobs=[j]).items
    ordered_ids = sorted([a.id, c.id])
    # b (invited 12 days ago) and the application (applied 15 days ago) fall due at the same moment: invite before application; then the two 9-day invites by id
    assert [(i.kind, i.entity_id) for i in got] == [("invite", b.id), ("application", JOB2), ("invite", ordered_ids[0]), ("invite", ordered_ids[1])]


def test_queue_is_deterministic_under_shuffling():
    rng = random.Random(3)
    ps = [person(n, found_in=(FoundIn("job", JOB),) if n % 3 else (FoundIn("target", TGT),), company=f"Co {n % 4}") for n in range(1, 25)]
    fl = {p.id: invited(days=rng.randint(0, 20), **({"closed_at": ago(days=1)} if rng.random() < 0.15 else {})) for p in ps}
    jobs = [applied(days=rng.randint(0, 20), job_id=f"job_{n:010d}", company=f"Co {n % 5}") for n in range(8)]
    expected = derive.build_queue(people_of(*ps), fl, {}, jobs, NOW)
    assert expected.items
    for _ in range(25):
        a, b, c = ps[:], list(fl.items()), jobs[:]
        rng.shuffle(a), rng.shuffle(b), rng.shuffle(c)
        assert derive.build_queue(people_of(*a), dict(b), {}, c, NOW) == expected


def test_queue_rejects_an_unreadable_now():
    with pytest.raises(ValueError):
        derive.build_queue({}, {}, {}, [], "soon")


# ---------------------------------------------------------------------------------------------------------------------
# next
# ---------------------------------------------------------------------------------------------------------------------

def next_for(ps, *, targets=(), jobs=(), states=None, flags=None, remaining=10, now=NOW):
    return derive.build_next(people_of(*ps), flags or {}, targets_of(*targets), list(jobs), states or {}, remaining, now)


def ids_of(result):
    return [c.person_id for c in result]


def tp(n=1, *, role="recruiter", slug="acme-corp", tid=TGT, **kw):
    return person(n, role=role, company=slug.replace("-", " "), found_in=(FoundIn("target", tid),), **kw)


def test_a_discovered_person_of_an_approved_target_is_next():
    p = tp(1)
    got = next_for([p], targets=[tinfo()])
    assert ids_of(got) == [p.id]
    assert (got[0].name, got[0].role, got[0].group, got[0].target_id, got[0].source) == ("Person 1", "recruiter", "acme-corp", TGT, "target")


@pytest.mark.parametrize("decision", ["candidate", "dropped"])
def test_only_approved_targets_qualify(decision):
    assert next_for([tp(1)], targets=[tinfo(decision=decision)]) == []


def test_a_target_the_person_is_not_linked_to_does_not_qualify_them():
    assert next_for([tp(1, tid=TGT2)], targets=[tinfo(tid=TGT)]) == []
    assert next_for([tp(1)], targets=[]) == []


def test_a_person_found_in_a_saved_non_terminal_job_is_next_without_a_target():
    p = person(1, found_in=(FoundIn("job", JOB),))
    got = next_for([p], jobs=[job(JOB, "Acme Corp", "SHORTLISTED")])
    assert ids_of(got) == [p.id] and got[0].source == "job" and got[0].target_id is None and got[0].group == "acme-corp"
    assert next_for([p], jobs=[]) == []
    assert next_for([p], jobs=[job(JOB2, "Acme Corp")]) == []                    # a different job
    for status in ("ACCEPTED", "REJECTED", "WITHDRAWN"):
        assert next_for([p], jobs=[job(JOB, "Acme Corp", status)]) == []


def test_a_person_in_two_live_jobs_is_reported_with_the_smallest_job_id():
    p = person(1, found_in=(FoundIn("job", JOB2), FoundIn("job", JOB)))
    got = next_for([p], jobs=[job(JOB2, "Acme Corp"), job(JOB, "Acme Corp")])
    assert got[0].reason == f"saved job {JOB} at acme-corp"


def test_terminal_job_statuses_match_the_state_machine():
    from careeros.core.state_machine import TERMINAL
    assert set(derive.TERMINAL_JOB_STATUSES) == {s.value for s in TERMINAL}


@pytest.mark.parametrize("flags", [
    PersonFlags(invited_at=ago(days=1)), PersonFlags(invited_at=ago(days=30), accepted_at=ago(days=20)),
    PersonFlags(messaged_at=ago(days=1)), PersonFlags(accepted_at=ago(days=2)), PersonFlags(replied_at=ago(days=2)),
    PersonFlags(closed_at=ago(days=2)), PersonFlags(invite_in_flight=True),
    PersonFlags(invite_in_flight=True, invited_at=None), PersonFlags(closed_at=ago(days=2), closed_reason="declined"),
])
def test_only_discovered_people_are_next(flags):
    p = tp(1)
    assert next_for([p], targets=[tinfo()], flags={p.id: flags}) == []


def test_a_person_with_empty_flags_or_none_is_discovered():
    p, q = tp(1), tp(2, role="hiring_manager")
    got = next_for([p, q], targets=[tinfo()], flags={p.id: PersonFlags()})
    assert sorted(ids_of(got)) == sorted([p.id, q.id])


LIVE = ["pending", "approved", "in_flight"]
NOT_LIVE = ["expired", "declined", "superseded", "voided", "consumed"]


@pytest.mark.parametrize("kind", ["linkedin_invite", "linkedin_message"])
@pytest.mark.parametrize("state", LIVE)
def test_a_live_approval_item_excludes_the_person(state, kind):
    p = tp(1)
    st = ItemState(State(state), kind=kind, target_id=p.id, approved_at=ago(hours=1))
    assert next_for([p], targets=[tinfo()], states={"apr_1": st}) == []


@pytest.mark.parametrize("kind", ["linkedin_invite", "linkedin_message"])
@pytest.mark.parametrize("state", NOT_LIVE)
def test_a_non_live_approval_item_does_not_exclude_the_person(state, kind):
    p = tp(1)
    st = ItemState(State(state), kind=kind, target_id=p.id, approved_at=ago(hours=1), consumed_at=ago(days=1))
    assert ids_of(next_for([p], targets=[tinfo()], states={"apr_1": st})) == [p.id]


def test_an_approved_item_past_its_ttl_is_not_live_even_if_the_state_says_approved():
    p = tp(1)
    stale = ItemState(State.APPROVED, kind="linkedin_invite", target_id=p.id, approved_at=ago(hours=24))
    fresh = ItemState(State.APPROVED, kind="linkedin_invite", target_id=p.id, approved_at=ago(hours=23, minutes=59, seconds=59))
    assert ids_of(next_for([p], targets=[tinfo()], states={"a": stale})) == [p.id]
    assert next_for([p], targets=[tinfo()], states={"a": fresh}) == []
    garbage = ItemState(State.APPROVED, kind="linkedin_invite", target_id=p.id, approved_at="garbage")
    assert ids_of(next_for([p], targets=[tinfo()], states={"a": garbage})) == [p.id]       # unreadable approval counts as expired


def test_items_of_other_people_kinds_or_without_a_state_do_not_exclude():
    p, q = tp(1), tp(2, role="hiring_manager")
    states = {"a": ItemState(State.PENDING, kind="linkedin_invite", target_id=q.id),
              "b": ItemState(State.PENDING, kind="application_submit", target_id=p.id),
              "c": ItemState(None, kind="linkedin_invite", target_id=p.id)}
    assert ids_of(next_for([p, q], targets=[tinfo()], states=states)) == [p.id]


def test_one_recruiter_and_one_hiring_manager_per_target():
    r1, r2 = tp(1, found_at="2026-01-02"), tp(2, found_at="2026-01-01")
    h1, h2 = tp(3, role="hiring_manager", found_at="2026-01-05"), tp(4, role="hiring_manager", found_at="2026-01-05")
    peer, other = tp(5, role="peer"), tp(6, role="other")
    got = next_for([r1, r2, h1, h2, peer, other], targets=[tinfo()])
    assert ids_of(got) == [r2.id, min(h1.id, h2.id)]                            # earliest found_at, then id; roles recruiter first
    assert derive.MAX_CONTACTS_PER_TARGET == 2


def test_an_ineligible_first_choice_does_not_block_the_next_person_of_the_role():
    r1, r2 = tp(1, found_at="2026-01-01"), tp(2, found_at="2026-01-02")
    assert ids_of(next_for([r1, r2], targets=[tinfo()], flags={r1.id: PersonFlags(closed_at=ago(days=1))})) == [r2.id]
    live = ItemState(State.PENDING, kind="linkedin_invite", target_id=r1.id)
    assert ids_of(next_for([r1, r2], targets=[tinfo()], states={"a": live})) == [r2.id]


def test_the_limit_is_per_target_not_global():
    a = [tp(1), tp(2, role="hiring_manager")]
    b = [tp(3, slug="beta-inc", tid=TGT2), tp(4, role="hiring_manager", slug="beta-inc", tid=TGT2)]
    got = next_for(a + b, targets=[tinfo(), tinfo("beta-inc", tid=TGT2)])
    assert len(got) == 4


def test_a_job_sourced_person_shares_the_per_target_limit_of_its_company():
    t = tp(1)
    j = person(2, found_in=(FoundIn("job", JOB),))                               # same company, same role, found via a job
    got = next_for([t, j], targets=[tinfo()], jobs=[job(JOB, "Acme Corp")])
    assert len(got) == 1 and got[0].group == "acme-corp"


def test_peers_and_others_are_not_contacted():
    assert next_for([tp(1, role="peer"), tp(2, role="other")], targets=[tinfo()]) == []


def test_remaining_limits_the_result_and_zero_or_negative_gives_none():
    ps = [tp(n, slug=f"co-{n}", tid=f"tgt_{n:010d}") for n in range(1, 6)]
    infos = [tinfo(f"co-{n}", tid=f"tgt_{n:010d}", fit=n) for n in range(1, 6)]
    full = next_for(ps, targets=infos, remaining=99)
    assert len(full) == 5
    for limit in (0, 1, 3, 5):
        assert next_for(ps, targets=infos, remaining=limit) == full[:limit]
    assert next_for(ps, targets=infos, remaining=-3) == []


def test_ranking_home_by_fit_then_non_home_by_tier_then_fit():
    specs = [("home-a", "n/a", 5), ("home-b", "n/a", 9), ("off-low", "offered", 3), ("off-high", "offered", 8),
             ("likely-top", "likely", 10), ("unknown-top", "unknown", 10), ("unknown-low", "unknown", 1), ("no-offer", "not_offered", 10)]
    infos, ps = [], []
    for n, (slug, sponsorship, fit) in enumerate(specs, 1):
        tid = f"tgt_{n:010d}"
        infos.append(tinfo(slug, tid=tid, fit=fit, sponsorship=sponsorship))
        ps.append(tp(n, slug=slug, tid=tid))
    got = next_for(ps, targets=infos)
    assert [c.group for c in got] == ["home-b", "home-a", "off-high", "off-low", "likely-top", "unknown-top", "unknown-low"]
    assert "no-offer" not in [c.group for c in got]


def test_ties_break_by_slug():
    infos = [tinfo("zeta", tid="tgt_0000000001", fit=5), tinfo("alpha", tid="tgt_0000000002", fit=5)]
    ps = [tp(1, slug="zeta", tid="tgt_0000000001"), tp(2, slug="alpha", tid="tgt_0000000002")]
    assert [c.group for c in next_for(ps, targets=infos)] == ["alpha", "zeta"]


def test_not_offered_excludes_the_target_route_only():
    p = person(1, found_in=(FoundIn("job", JOB),))
    for decision in ("candidate", "approved"):                  # the job route ignores the unlinked target, ranks unranked
        got = next_for([p], targets=[tinfo(sponsorship="not_offered", decision=decision)], jobs=[job()])
        assert ids_of(got) == [p.id] and got[0].fit is None and got[0].reason == f"saved job {JOB} at acme-corp"
    assert next_for([tp(1)], targets=[tinfo(sponsorship="not_offered")]) == []


def test_both_routes_fall_through_to_the_job_route_when_the_target_route_fails():
    both = person(1, found_in=(FoundIn("target", TGT), FoundIn("job", JOB)))
    for decision in ("candidate", "approved"):
        got = next_for([both], targets=[tinfo(sponsorship="not_offered", decision=decision)], jobs=[job()])
        assert ids_of(got) == [both.id] and got[0].source == "job" and got[0].fit is None, decision
        assert got[0].reason == f"saved job {JOB} at acme-corp"
    only_target = person(2, found_in=(FoundIn("target", TGT),))
    assert next_for([only_target], targets=[tinfo(sponsorship="not_offered")], jobs=[job()]) == []
    terminal = [job(status="REJECTED")]
    assert next_for([both], targets=[tinfo(sponsorship="not_offered")], jobs=terminal) == []
    # a usable approved target still wins over the job route
    assert next_for([both], targets=[tinfo(sponsorship="likely")], jobs=[job()])[0].source == "target"


def test_a_job_sourced_person_at_a_dropped_target_company_is_unranked_but_present():
    p = person(1, found_in=(FoundIn("job", JOB),))
    got = next_for([p], targets=[tinfo(decision="dropped", sponsorship="not_offered")], jobs=[job()])
    assert ids_of(got) == [p.id] and got[0].fit is None and got[0].sponsorship is None


def test_untargeted_job_people_rank_after_targeted_ones():
    t = tp(1, slug="zzz-co", tid=TGT2)
    j = person(2, company="Aaa Co", found_in=(FoundIn("job", JOB),))
    got = next_for([j, t], targets=[tinfo("zzz-co", tid=TGT2, fit=0, sponsorship="unknown")], jobs=[job(JOB, "Aaa Co")])
    assert [c.group for c in got] == ["zzz-co", "aaa-co"]


def test_stale_targets_are_flagged_and_still_present():
    p = tp(1)
    got = next_for([p], targets=[tinfo(stale=True)])
    assert ids_of(got) == [p.id] and got[0].stale is True
    assert next_for([p], targets=[tinfo(stale=False)])[0].stale is False


def test_reasons_are_deterministic_text():
    home = next_for([tp(1)], targets=[tinfo(fit=8, sponsorship="n/a")])[0]
    assert home.reason == "approved target acme-corp: home market, fit 8"
    away = next_for([tp(1)], targets=[tinfo(fit=7, sponsorship="likely")])[0]
    assert away.reason == "approved target acme-corp: sponsorship likely, fit 7"
    stale = next_for([tp(1)], targets=[tinfo(fit=7, sponsorship="likely", stale=True)])[0]
    assert stale.reason == "approved target acme-corp: sponsorship likely, fit 7 (stale: no activity for more than 60 days)"
    jobbed = next_for([person(1, found_in=(FoundIn("job", JOB),))], jobs=[job()])[0]
    assert jobbed.reason == f"saved job {JOB} at acme-corp"
    both = next_for([person(1, found_in=(FoundIn("job", JOB),))], jobs=[job()],
                    targets=[tinfo(decision="candidate", fit=6, sponsorship="offered")])[0]
    assert both.reason == f"saved job {JOB}; target acme-corp: sponsorship offered, fit 6"


def test_people_without_flags_entries_and_flags_without_records():
    p = tp(1)
    assert ids_of(next_for([p], targets=[tinfo()], flags={})) == [p.id]
    assert ids_of(next_for([p], targets=[tinfo()], flags={per_id(99): PersonFlags(closed_at=ago(days=1))})) == [p.id]


def test_next_is_deterministic_under_shuffling():
    rng = random.Random(11)
    ps, infos, jobs = [], [], []
    for n in range(1, 13):
        slug, tid = f"co-{n % 5}", f"tgt_{n % 5:010d}"
        role = rng.choice(["recruiter", "hiring_manager", "peer"])
        ps.append(tp(n, role=role, slug=slug, tid=tid, found_at=f"2026-01-{rng.randint(1, 9):02d}"))
    for k in range(5):
        infos.append(tinfo(f"co-{k}", tid=f"tgt_{k:010d}", fit=rng.randint(0, 10), sponsorship=rng.choice(["n/a", "offered", "likely", "unknown", "not_offered"]),
                           decision=rng.choice(["approved", "approved", "candidate"])))
    jobs = [job(f"job_{k:010d}", f"Co {k}") for k in range(3)]
    fl = {p.id: PersonFlags(closed_at=ago(days=1)) for p in ps if rng.random() < 0.2}
    expected = derive.build_next(people_of(*ps), fl, targets_of(*infos), jobs, {}, 7, NOW)
    assert expected
    for _ in range(25):
        a, b, c, d = ps[:], infos[:], jobs[:], list(fl.items())
        rng.shuffle(a), rng.shuffle(b), rng.shuffle(c), rng.shuffle(d)
        assert derive.build_next(people_of(*a), dict(d), targets_of(*b), c, {}, 7, NOW) == expected


# ---------------------------------------------------------------------------------------------------------------------
# warm
# ---------------------------------------------------------------------------------------------------------------------

def warm_for(ps, fl, jobs, now=NOW):
    return derive.build_warm(people_of(*ps), fl, list(jobs), now)


def test_connected_and_replied_people_at_a_company_with_a_saved_non_terminal_job_are_warm():
    c, r = person(1), person(2)
    got = warm_for([c, r], {c.id: PersonFlags(invited_at=ago(days=9), accepted_at=ago(days=3)),
                            r.id: PersonFlags(invited_at=ago(days=9), accepted_at=ago(days=5), replied_at=ago(days=2))},
                   [job(status="SHORTLISTED")])
    assert [(w.person_id, w.label, w.since) for w in got] == [(r.id, "replied", ago(days=2)), (c.id, "connected", ago(days=3))]
    assert got[0].job_ids == (JOB,) and got[0].company == "Acme Corp" and got[0].role == "recruiter"
    assert got[0].reason == f"replied at acme-corp, which has a saved job ({JOB})"
    assert got[1].reason == f"connected at acme-corp, which has a saved job ({JOB})"


def test_warm_excludes_terminal_jobs_other_companies_and_other_labels():
    c = person(1)
    fl = {c.id: PersonFlags(invited_at=ago(days=9), accepted_at=ago(days=3))}
    for status in ("ACCEPTED", "REJECTED", "WITHDRAWN"):
        assert warm_for([c], fl, [job(status=status)]) == []
    assert warm_for([c], fl, [job(company="Other Corp")]) == []
    assert warm_for([c], fl, []) == []
    # a terminal and a live job at the company: the live one counts
    assert warm_for([c], fl, [job(JOB2, status="REJECTED"), job(JOB, status="SCREEN")])[0].job_ids == (JOB,)
    for other in (PersonFlags(), PersonFlags(invited_at=ago(days=9)), PersonFlags(invited_at=ago(days=9), messaged_at=ago(days=1)),
                  PersonFlags(invited_at=ago(days=9), accepted_at=ago(days=3), closed_at=ago(days=1))):
        assert warm_for([c], {c.id: other}, [job()]) == []


def test_warm_matches_the_company_through_the_slug():
    c = person(1, company="Acme Corp")
    fl = {c.id: PersonFlags(invited_at=ago(days=9), accepted_at=ago(days=3))}
    assert len(warm_for([c], fl, [job(company="acme-corp")])) == 1
    assert len(warm_for([c], fl, [job(company="ACME   Corp.")])) == 1


def test_warm_lists_every_live_job_id_sorted():
    c = person(1)
    fl = {c.id: PersonFlags(invited_at=ago(days=9), accepted_at=ago(days=3))}
    got = warm_for([c], fl, [job(JOB2), job(JOB)])
    assert got[0].job_ids == tuple(sorted((JOB, JOB2)))


def test_warm_is_deterministic_under_shuffling():
    rng = random.Random(5)
    ps = [person(n, company=f"Co {n % 4}", role=rng.choice(["recruiter", "peer"])) for n in range(1, 21)]
    fl = {p.id: PersonFlags(invited_at=ago(days=20), accepted_at=ago(days=rng.randint(1, 9)),
                            **({"replied_at": ago(days=rng.randint(0, 5))} if rng.random() < 0.4 else {})) for p in ps}
    jobs = [job(f"job_{k:010d}", f"Co {k}", status=rng.choice(["SCREEN", "REJECTED"])) for k in range(4)]
    expected = derive.build_warm(people_of(*ps), fl, jobs, NOW)
    assert expected
    for _ in range(25):
        a, b, c = ps[:], list(fl.items()), jobs[:]
        rng.shuffle(a), rng.shuffle(b), rng.shuffle(c)
        assert derive.build_warm(people_of(*a), dict(b), c, NOW) == expected


# ---------------------------------------------------------------------------------------------------------------------
# purity
# ---------------------------------------------------------------------------------------------------------------------

def test_derive_is_pure_and_does_not_import_the_heavy_modules():
    tree = ast.parse(inspect.getsource(derive))
    modules = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module} | \
              {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    assert not {m.split(".")[0] for m in modules} & {"os", "pathlib", "time", "shutil", "tempfile", "subprocess", "socket", "random"}
    source = inspect.getsource(derive)
    assert "utc_now" not in source and "datetime.now" not in source and "open(" not in source
    code = ("import sys; import careeros.core.outreach.derive;"
            "bad=[m for m in sys.modules if m in ('careeros.core.validation','careeros.core.state_machine',"
            "'careeros.core.approvals.service','careeros.core.ledger') or m.startswith('careeros.cli')];"
            "print(bad); sys.exit(1 if bad else 0)")
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_derivations_do_not_mutate_their_inputs():
    p = tp(1)
    fl = {p.id: PersonFlags()}
    ppl, tgts, jobs = people_of(p), targets_of(tinfo()), [job()]
    before = (dict(ppl), dict(fl), dict(tgts), list(jobs))
    derive.build_queue(ppl, fl, tgts, jobs, NOW), derive.build_next(ppl, fl, tgts, jobs, {}, 5, NOW), derive.build_warm(ppl, fl, jobs, NOW)
    assert (ppl, fl, tgts, jobs) == before
