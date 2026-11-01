"""The round record `rounds/<rnd_id>.md` (spec 4.1 and 4.2): strict parsing and the canonical bytes. Pure.

`parse` reads the file with the strict frontmatter loader (aliases, anchors, merge keys and duplicate keys refused),
refuses unknown keys and reports every shape problem as `INT001`; it never raises and repairs nothing. `id` and `job`
are identity-bearing and immutable (the caller compares them with the round's events); `id` must equal the file stem.
The record holds no state: stage, format, `scheduled_for`, interviewers and the debrief block are editable source data.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime

from careeros.core import ids
from careeros.core.interview import _common as c
from careeros.core.interview import rules
from careeros.core.interview.model import Debrief, Interviewer, Round
from careeros.core.models import Issue
from careeros.core.workspace import join_frontmatter

_REQUIRED = ("schema", "id", "job", "stage", "format", "scheduled_for", "interviewers")
_OPTIONAL = ("debrief",)
_DEBRIEF_REQUIRED = ("rating", "questions_asked", "do_differently", "next_step")
_DEBRIEF_OPTIONAL = ("next_step_date",)
_INTERVIEWER_KEYS = {"name", "title"}


def parse_when(value: object) -> datetime | None:
    """The instant of an ISO date (UTC midnight) or a UTC datetime `YYYY-MM-DDTHH:MM:SSZ` (naive UTC); None otherwise."""
    return c.parse_when(value)


def _interviewers(raw: object, problems: list[str]) -> tuple[Interviewer, ...] | None:
    if not isinstance(raw, list):
        problems.append("interviewers must be a list of {name, title} entries")
        return None
    if len(raw) > rules.MAX_INTERVIEWERS:
        problems.append(f"interviewers has more than {rules.MAX_INTERVIEWERS} entries")
        return None
    out, ok = [], True
    for n, entry in enumerate(raw):
        if not isinstance(entry, dict) or set(entry) != _INTERVIEWER_KEYS:
            problems.append(f"interviewers entry {n} must be a mapping with exactly name and title")
            ok = False
            continue
        name = c.text_value(entry["name"], f"interviewers entry {n} name", problems)
        title = c.text_value(entry["title"], f"interviewers entry {n} title", problems, allow_empty=True)
        if name is None or title is None:
            ok = False
        else:
            out.append(Interviewer(name, title))
    return tuple(out) if ok else None


def _text_list(raw: object, name: str, problems: list[str]) -> tuple[str, ...] | None:
    if not isinstance(raw, list):
        problems.append(f"{name} must be a list of strings")
        return None
    if len(raw) > rules.MAX_DEBRIEF_ITEMS:
        problems.append(f"{name} has more than {rules.MAX_DEBRIEF_ITEMS} entries")
        return None
    out = [c.text_value(v, f"{name} entry {n}", problems) for n, v in enumerate(raw)]
    return None if None in out else tuple(out)


def _debrief(raw: object, problems: list[str]) -> Debrief | None:
    if not isinstance(raw, dict):
        problems.append("debrief must be a mapping")
        return None
    before = len(problems)
    problems.extend(c.keys_problems(raw, _DEBRIEF_REQUIRED, _DEBRIEF_OPTIONAL, where="debrief: "))
    rating = raw.get("rating")
    if not isinstance(rating, str) or rating not in rules.RATINGS:
        problems.append(f"debrief rating must be one of {', '.join(rules.RATINGS)}")
    asked = _text_list(raw.get("questions_asked"), "debrief questions_asked", problems)
    improve = _text_list(raw.get("do_differently"), "debrief do_differently", problems)
    next_step = c.text_value(raw.get("next_step"), "debrief next_step", problems)
    next_date = c.date_value(raw["next_step_date"], "debrief next_step_date", problems) if "next_step_date" in raw else None
    if len(problems) > before:
        return None
    return Debrief(rating, asked, improve, next_step, next_date)


def _validate(fm: dict, body: str, rel: str) -> tuple[Round | None, list[str]]:
    problems = c.keys_problems(fm, _REQUIRED, _OPTIONAL)
    if (p := c.schema_problem(fm)) is not None:
        problems.append(p)
    rnd = fm.get("id")
    if not ids.is_valid_id("round", rnd):
        problems.append(f"id {rnd!r} is not a rnd_ id")
    elif (p := c.file_name_problem(rel, f"{rnd}.md")) is not None:
        problems.append(f"id {rnd!r}: {p}")
    job = fm.get("job")
    if not ids.is_valid_id("job", job):
        problems.append(f"job {job!r} is not a job_ id")
    stage, fmt = fm.get("stage"), fm.get("format")
    if not isinstance(stage, str) or stage not in rules.STAGES:
        problems.append(f"stage must be one of {', '.join(rules.STAGES)}")
    if not isinstance(fmt, str) or fmt not in rules.FORMATS:
        problems.append(f"format must be one of {', '.join(rules.FORMATS)}")
    when = c.when_value(fm.get("scheduled_for"), "scheduled_for", problems)
    interviewers = _interviewers(fm.get("interviewers"), problems)
    debrief = _debrief(fm["debrief"], problems) if "debrief" in fm else None
    if problems:
        return None, problems
    return Round(rnd, job, stage, fmt, when, interviewers, debrief, body), problems


def parse(text: object, rel: str) -> tuple[Round | None, list[Issue]]:
    """`(round, [])` or `(None, [INT001 issues])`. Never raises."""
    fm, body, problems = c.load(text, rel)
    parsed = None
    if fm is not None:
        try:
            parsed, problems = _validate(fm, body, rel)
        except Exception as exc:  # noqa: BLE001 - hostile input must never escape as an exception
            parsed, problems = None, [f"{rel} cannot be read: {type(exc).__name__}"]
    if parsed is not None:
        return parsed, []
    return None, c.issues(rel, problems)


def to_fm(record: Round) -> dict:
    """The frontmatter mapping in the documented key order."""
    fm: dict = {"schema": rules.SCHEMA, "id": record.id, "job": record.job, "stage": record.stage, "format": record.format,
                "scheduled_for": record.scheduled_for,
                "interviewers": [{"name": i.name, "title": i.title} for i in record.interviewers]}
    if record.debrief is not None:
        d = record.debrief
        fm["debrief"] = {"rating": d.rating, "questions_asked": list(d.questions_asked),
                         "do_differently": list(d.do_differently), "next_step": d.next_step}
        if d.next_step_date is not None:
            fm["debrief"]["next_step_date"] = d.next_step_date
    return fm


def to_bytes(record: Round) -> bytes:
    """The exact bytes of the file: frontmatter in the documented key order, then the record's body (LF line endings)."""
    body = record.body.replace("\r\n", "\n").replace("\r", "\n")
    return join_frontmatter(to_fm(record), body).encode("utf-8")


def with_debrief(record: Round, debrief: Debrief | None) -> Round:
    """`record` with its debrief block replaced (an unrecorded block is replaced too, spec 7.1)."""
    return replace(record, debrief=debrief)


def with_when(record: Round, when: str) -> Round:
    """`record` with `scheduled_for` set to `when`. Raises ValueError unless `when` is an ISO date or a UTC datetime."""
    if c.parse_when(when) is None:
        raise ValueError(f"when must be an ISO date or a UTC datetime, not {when!r}")
    return replace(record, scheduled_for=when)
