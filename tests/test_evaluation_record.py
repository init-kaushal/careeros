"""Evaluation record: parsing, agent-half validation, stamps, rendering and integrity (Task D). Invented data only."""

from __future__ import annotations

import copy
import dataclasses
import hashlib
from fractions import Fraction
from pathlib import Path

import pytest
from helpers import add_job

from careeros.core.evaluation import canon, integrity, record, rules
from careeros.core.evaluation.model import Kind, ReqClass
from careeros.core.memory import ops, store
from careeros.core.memory.lexicon import Lexicon
from careeros.core.memory.store import Career, Fact
from careeros.core.workspace import join_frontmatter, split_frontmatter

FOLD = "## ── HISTORY (on demand; do not read past this line) ──"

PROFILE = f"""# Example Career Profile

## Markets
| Market | Min base (per year) | Sponsorship | Priority |
|--------|---------------------|-------------|----------|
| Atlantis (home) | ₹35,00,000 | not needed | 1 |
| Singapore | SGD 8000 | required | 2 |
| Elsewhere | TBD — derive from home floor | preferred | 3 |

## Scoring rubric (use when rating job listings 0–10)
| Signal | Weight |
|--------|--------|
| Role title matches target roles | High |
| Primary language(s) in stack | High |
| Location match or remote-friendly | Medium |
| Compensation signals above the minimum for that listing's market | Medium |

{FOLD}
older notes
"""

JD = """# Senior Platform Engineer

Example Corp is hiring a Senior Platform Engineer.

Requirements:
- production Kubernetes experience
- 5+ years of Go
- you will own the on-call
  rotation

Location: hybrid, Pune.
Visa sponsorship is not available for this role.
"""

CREATED = "2026-10-10T09:15:00Z"


def agent_half(ids: dict) -> dict:
    return {
        "updated_at": "2026-10-10T09:20:00Z",
        "requirements": [
            {"id": "r1", "class": "must", "kind": "technology", "term": "Kubernetes",
             "text": "Production Kubernetes experience", "quote": "production Kubernetes experience"},
            {"id": "r2", "class": "must", "kind": "years", "years": 5, "term": "Go",
             "text": "5+ years of Go", "quote": "5+ years of Go"},
            {"id": "r3", "class": "strong", "kind": "domain", "text": "Owned an on-call rotation",
             "quote": "you will own the on-call rotation",
             "links": [{"fact": ids["incident"], "strength": "adjacent", "why": "led incident response"}]},
        ],
        "fit": [
            {"row": "Role title matches target roles", "judgement": "yes", "quote": "Senior Platform Engineer"},
            {"row": "Location match or remote-friendly", "judgement": "partial", "quote": "hybrid, Pune"},
        ],
        "job_facts": {
            "market": "Atlantis (home)",
            "sponsorship": {"needed": "no", "listing": "unknown"},
            "compensation": {
                "range": {"low": 3000000, "high": 4200000, "currency": "INR", "basis": "base"},
                "floor": {"amount": 3500000, "currency": "INR", "basis": "base"},
                "source": "posting", "confidence": "medium", "as_of": "2026-09",
            },
        },
    }


# --- builders ------------------------------------------------------------------------------------

def make_job(root: Path, slug: str = "example-platform") -> dict:
    (root / "profile.md").write_text(PROFILE, encoding="utf-8")
    job = add_job(root, slug, company="Example Corp", title="Senior Platform Engineer")
    (job["path"].parent / "jd.md").write_text(JD, encoding="utf-8")
    job["eval"] = job["path"].parent / "evaluation.md"
    return job


def write_agent(root: Path, job: dict, agent: dict | None, *, evaluate: bool = True, extra: dict | None = None) -> Path:
    """Write a skeleton with `agent` and, when `evaluate`, the computed result and body."""
    fm, _ = split_frontmatter(record.skeleton(job["id"], CREATED).decode("utf-8"))
    fm["agent"] = agent
    fm.update(extra or {})
    path = job["eval"]
    path.write_text(join_frontmatter(fm, record.SKELETON_BODY), encoding="utf-8")
    if evaluate:
        rec = record.load(path, root)
        result = record.compute(rec, record.read_inputs(root, path.parent))
        path.write_bytes(record.to_bytes(rec, result))
    return path


@pytest.fixture
def ws(career):
    """A workspace with a job, its jd.md and a freshly evaluated record."""
    root, ids = career
    job = make_job(root)
    write_agent(root, job, agent_half(ids))
    return root, ids, job


def load(root, job) -> record.Record:
    return record.load(job["eval"], root)


def issues_of(root, job, agent: dict, **kw) -> list:
    path = write_agent(root, job, agent, evaluate=False, **kw)
    inputs = record.read_inputs(root, path.parent)
    return record.validate_agent(record.load(path, root), inputs.jd_text, inputs.career, inputs.rubric,
                                 inputs.markets, inputs.job_id)


def codes(issues) -> list[str]:
    return sorted(i.code for i in issues)


def report(root, job) -> integrity.IntegrityReport:
    return integrity.check(load(root, job), record.read_inputs(root, job["eval"].parent))


def edit(root, job, mutate, *, body=None) -> None:
    """Hand-edit the stored frontmatter (and optionally the body), the way a person would."""
    fm, old_body = split_frontmatter(job["eval"].read_text(encoding="utf-8"))
    mutate(fm)
    job["eval"].write_text(join_frontmatter(fm, old_body if body is None else body), encoding="utf-8")


def stored_result(root, job) -> dict:
    return copy.deepcopy(load(root, job).result)


def rewrite_result(root, job, result: dict) -> None:
    """Replace result: and the body consistently (a careful forger)."""
    rec = load(root, job)
    job["eval"].write_bytes(record.to_bytes(rec, result))


# --- loading (EVL001) ----------------------------------------------------------------------------

def test_fresh_record_round_trips_and_is_clean(ws):
    root, ids, job = ws
    rec = load(root, job)
    assert rec.id.startswith("evl_") and rec.job == job["id"] and rec.rel == "jobs/discovered/example-platform/evaluation.md"
    rep = report(root, job)
    assert (rep.stale, rep.tampered, rep.unsupported, rep.issues) == ((), (), None, ())
    assert rec.body == record.render_body(rec.result)
    assert set(rec.result) == set(record.RESULT_KEYS)
    assert set(rec.result["stamps"]) == {"jd", "memory", "rubric", "rules", "inputs"}
    assert rec.result["basis_sha256"] == canon.sha256_hex(canon.canonical_json(rec.result["basis"]))
    assert rec.result["tier"] == "pursue" and rec.result["combined"] == 0.8


@pytest.mark.parametrize("raw", [
    b"\xff\xfe not utf-8",
    b"no frontmatter at all\n",
    b"---\nid: [unclosed\n---\n",
    b"---\n- a list\n---\n",
])
def test_unreadable_or_invalid_yaml_is_evl001(tmp_path, raw):
    path = tmp_path / "evaluation.md"
    path.write_bytes(raw)
    with pytest.raises(record.RecordError) as exc:
        record.load(path, tmp_path)
    assert exc.value.code == "EVL001"


@pytest.mark.parametrize("change", [
    lambda fm: fm.pop("agent"),
    lambda fm: fm.update(agent=["not", "a", "mapping"]),
    lambda fm: fm.update(result="text"),
    lambda fm: fm.update(type="job"),
    lambda fm: fm.update(schema=2),
    lambda fm: fm.update(id="evl_bad"),
])
def test_missing_or_malformed_namespaces_and_header_are_evl001(ws, change):
    root, ids, job = ws
    edit(root, job, change)
    with pytest.raises(record.RecordError) as exc:
        load(root, job)
    assert exc.value.code == "EVL001"
    found = integrity.validate_workspace(root)          # never raises on a hand-edited file
    assert [i.code for i in found] == ["EVL001"]


def test_missing_file_is_evl001(tmp_path):
    with pytest.raises(record.RecordError) as exc:
        record.load(tmp_path / "evaluation.md", tmp_path)
    assert exc.value.code == "EVL001"


def test_skeleton_has_an_empty_agent_and_no_result(tmp_path):
    data = record.skeleton("job_aaaaaaaaaa", CREATED)
    assert isinstance(data, bytes)
    path = tmp_path / "evaluation.md"
    path.write_bytes(data)
    rec = record.load(path, tmp_path)
    assert rec.agent == {} and rec.result is None and rec.job == "job_aaaaaaaaaa"
    assert rec.fm["created_at"] == CREATED and rec.fm["type"] == "evaluation" and rec.fm["schema"] == 1
    assert record.skeleton("job_aaaaaaaaaa", CREATED, taken={rec.id}) != data


def test_yaml_class_key_maps_to_requirement_cls(ws):
    root, ids, job = ws
    agent, issues = record.parse_agent(load(root, job))
    assert issues == []
    assert [(r.id, r.cls, r.kind) for r in agent.requirements] == [
        ("r1", ReqClass.MUST, Kind.TECHNOLOGY), ("r2", ReqClass.MUST, Kind.YEARS), ("r3", ReqClass.STRONG, Kind.DOMAIN)]


# --- EVL002 --------------------------------------------------------------------------------------

def test_duplicate_requirement_id_is_evl002(career):
    root, ids = career
    job = make_job(root)
    agent = agent_half(ids)
    agent["requirements"][1]["id"] = "r1"
    assert "EVL002" in codes(issues_of(root, job, agent))


@pytest.mark.parametrize("value", [None, "", "  ", 3])
def test_missing_requirement_id_is_evl002(career, value):
    root, ids = career
    job = make_job(root)
    agent = agent_half(ids)
    agent["requirements"][0]["id"] = value
    assert codes(issues_of(root, job, agent)) == ["EVL002"]


def test_record_for_another_job_is_evl002(career):
    root, ids = career
    job = make_job(root)
    found = issues_of(root, job, agent_half(ids), extra={"job": "job_bbbbbbbbbb"})
    assert codes(found) == ["EVL002"] and "job_bbbbbbbbbb" in found[0].message


# --- EVL003 --------------------------------------------------------------------------------------

@pytest.mark.parametrize("path, value", [
    (("requirements", 0, "class"), "required"),
    (("requirements", 0, "kind"), "tool"),
    (("requirements", 0, "class"), "Must"),
    (("requirements", 2, "links", 0, "strength"), "strong"),
    (("fit", 0, "judgement"), "maybe"),
    (("job_facts", "sponsorship", "listing"), "maybe"),
    (("job_facts", "compensation", "range", "basis"), "gross"),
    (("job_facts", "compensation", "floor", "basis"), "ctc"),
    (("job_facts", "compensation", "confidence"), "certain"),
    (("job_facts", "compensation", "floor", "amount"), True),
    (("job_facts", "compensation", "floor", "amount"), 3500000.0),
    (("job_facts", "compensation", "range", "high"), "4200000"),
    (("requirements", 1, "years"), "5"),
    (("requirements", 1, "years"), 2.5),
    (("job_facts", "sponsorship", "needed"), "sometimes"),
    (("job_facts", "market"), 7),
    (("requirements", 0, "term"), ["Kubernetes"]),
])
def test_invalid_values_are_evl003(career, path, value):
    root, ids = career
    job = make_job(root)
    agent = agent_half(ids)
    target = agent
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    assert codes(issues_of(root, job, agent)) == ["EVL003"]


def test_unknown_rubric_row_is_evl003(career):
    root, ids = career
    job = make_job(root)
    agent = agent_half(ids)
    agent["fit"].append({"row": "Free lunches", "judgement": "yes", "quote": "Senior Platform Engineer"})
    found = issues_of(root, job, agent)
    assert codes(found) == ["EVL003"] and "Free lunches" in found[0].message


def test_fit_row_text_with_different_case_and_spacing_is_accepted(career):
    root, ids = career
    job = make_job(root)
    agent = agent_half(ids)
    agent["fit"][0]["row"] = "  role TITLE   matches target roles "
    assert issues_of(root, job, agent) == []


def test_a_fit_row_judged_twice_is_evl003(career):
    root, ids = career
    job = make_job(root)
    agent = agent_half(ids)
    agent["fit"].append(dict(agent["fit"][0], row="ROLE title matches target roles"))
    assert codes(issues_of(root, job, agent)) == ["EVL003"]


def test_unknown_agent_key_is_evl003(career):
    root, ids = career
    job = make_job(root)
    agent = agent_half(ids)
    agent["job_fact"] = agent.pop("job_facts")
    assert "EVL003" in codes(issues_of(root, job, agent))


def test_yaml_booleans_for_yes_and_no_are_read_as_words(career):
    root, ids = career
    job = make_job(root)
    agent = agent_half(ids)
    quoted = write_agent(root, job, agent, evaluate=False).read_text(encoding="utf-8")
    assert "judgement: 'yes'" in quoted and "needed: 'no'" in quoted
    unquoted = quoted.replace("judgement: 'yes'", "judgement: yes").replace("needed: 'no'", "needed: no")
    job["eval"].write_text(unquoted, encoding="utf-8")
    rec = load(root, job)
    assert rec.agent["fit"][0]["judgement"] is True and rec.agent["job_facts"]["sponsorship"]["needed"] is False
    inputs = record.read_inputs(root, job["eval"].parent)
    assert record.validate_agent(rec, inputs.jd_text, inputs.career, inputs.rubric, inputs.markets, inputs.job_id) == []
    parsed, _ = record.parse_agent(rec)
    assert str(parsed.fit[0].judgement) == "yes"
    job["eval"].write_text(quoted, encoding="utf-8")
    assert record.inputs_stamp(load(root, job).agent) == record.inputs_stamp(rec.agent)


# --- EVL004 --------------------------------------------------------------------------------------

def test_quote_not_in_the_posting_is_evl004(career):
    root, ids = career
    job = make_job(root)
    agent = agent_half(ids)
    agent["requirements"][0]["quote"] = "ten years of Kubernetes"
    found = issues_of(root, job, agent)
    assert codes(found) == ["EVL004"] and "r1" in found[0].message


def test_quote_differing_only_by_case_is_evl004(career):
    root, ids = career
    job = make_job(root)
    agent = agent_half(ids)
    agent["requirements"][0]["quote"] = "Production Kubernetes Experience"
    assert codes(issues_of(root, job, agent)) == ["EVL004"]


def test_quote_spanning_a_reflowed_line_break_is_accepted(career):
    root, ids = career
    job = make_job(root)
    agent = agent_half(ids)
    assert "on-call\n  rotation" in JD and agent["requirements"][2]["quote"] == "you will own the on-call rotation"
    agent["requirements"][2]["quote"] = "you   will own the\non-call rotation"
    assert issues_of(root, job, agent) == []


@pytest.mark.parametrize("change", [
    lambda a: a["requirements"][0].pop("quote"),
    lambda a: a["requirements"][0].update(quote="   "),
    lambda a: a["fit"][0].update(quote=None),
    lambda a: a["fit"][1].update(quote="fully remote"),
    lambda a: a["job_facts"]["sponsorship"].update(listing="not_offered", quote="we sponsor everyone"),
])
def test_missing_or_non_verbatim_quotes_are_evl004(career, change):
    root, ids = career
    job = make_job(root)
    agent = agent_half(ids)
    change(agent)
    assert codes(issues_of(root, job, agent)) == ["EVL004"]


def test_null_quote_with_judgement_no_is_accepted(career):
    root, ids = career
    job = make_job(root)
    agent = agent_half(ids)
    agent["fit"].append({"row": "Primary language(s) in stack", "judgement": "no", "quote": None})
    assert issues_of(root, job, agent) == []


def test_missing_jd_is_evl004(career):
    root, ids = career
    job = make_job(root)
    (job["path"].parent / "jd.md").unlink()
    found = issues_of(root, job, agent_half(ids))
    assert codes(found) == ["EVL004"] and "jd.md" in found[0].message


# --- EVL005 --------------------------------------------------------------------------------------

def test_link_to_a_missing_fact_is_evl005(career):
    root, ids = career
    job = make_job(root)
    agent = agent_half(ids)
    agent["requirements"][2]["links"][0]["fact"] = "ach_zzzzzzzzzz"     # e.g. another workspace's fact
    found = issues_of(root, job, agent)
    assert codes(found) == ["EVL005"] and "ach_zzzzzzzzzz" in found[0].message


@pytest.mark.parametrize("how", ["retire", "dispute"])
def test_link_to_an_inactive_fact_is_evl005(career, how):
    root, ids = career
    job = make_job(root)
    getattr(ops, f"{how}_fact")(root, ids["incident"], "no longer true")
    assert codes(issues_of(root, job, agent_half(ids))) == ["EVL005"]


# --- EVL006 --------------------------------------------------------------------------------------

@pytest.mark.parametrize("change", [
    lambda a: a["job_facts"]["sponsorship"].update(listing="not_offered"),
    lambda a: a["job_facts"]["sponsorship"].update(listing="offered", quote="  "),
    lambda a: a["job_facts"]["compensation"].pop("as_of"),
    lambda a: a["job_facts"]["compensation"].pop("source"),
    lambda a: a["job_facts"]["compensation"].update(confidence=None),
    lambda a: a["job_facts"].update(market="Narnia"),
    lambda a: a["job_facts"].pop("market"),
    lambda a: a["job_facts"].update(market="  ", compensation=dict(a["job_facts"]["compensation"])),
])
def test_job_fact_rules_are_evl006(career, change):
    root, ids = career
    job = make_job(root)
    agent = agent_half(ids)
    change(agent)
    assert codes(issues_of(root, job, agent)) == ["EVL006"]


def test_not_offered_with_a_verbatim_quote_is_accepted(career):
    root, ids = career
    job = make_job(root)
    agent = agent_half(ids)
    agent["job_facts"]["sponsorship"].update(listing="not_offered", quote="Visa sponsorship is not available")
    assert issues_of(root, job, agent) == []


def test_floor_given_without_a_market_is_evl006_but_no_market_and_no_values_is_fine(career):
    root, ids = career
    job = make_job(root)
    agent = agent_half(ids)
    agent["job_facts"].pop("market")
    agent["job_facts"]["sponsorship"].pop("needed")
    assert codes(issues_of(root, job, agent)) == ["EVL006"]          # the floor is still given
    agent["job_facts"]["compensation"].pop("floor")
    assert issues_of(root, job, agent) == []


def test_skeleton_agent_has_no_structural_errors(career):
    root, ids = career
    job = make_job(root)
    assert issues_of(root, job, {}) == []
    assert issues_of(root, job, None) == []


# --- freshness (EVL008) --------------------------------------------------------------------------

def stale_of(root, job) -> tuple:
    rep = report(root, job)
    assert rep.tampered == (), rep.issues
    return rep.stale


def test_changed_jd_wording_is_stale_jd(ws):
    root, ids, job = ws
    (job["path"].parent / "jd.md").write_text(JD.replace("hybrid, Pune", "hybrid, Pune or remote"), encoding="utf-8")
    assert stale_of(root, job) == ("jd",)
    issue = [i for i in report(root, job).issues if i.code == "EVL008"][0]
    assert issue.severity == "warning" and "jd" in issue.message


def test_reflowed_jd_stays_fresh(ws):
    root, ids, job = ws
    reflowed = "﻿" + "  " + JD.replace("\n", "\r\n").replace(" is hiring ", " is\n   hiring ") + "\n\n"
    (job["path"].parent / "jd.md").write_bytes(reflowed.encode("utf-8"))
    assert stale_of(root, job) == ()


def test_adding_a_fact_is_stale_memory(ws):
    root, ids, job = ws
    ops.add_fact(root, "skill", {"name": "Rust"}, quote="said so")
    assert stale_of(root, job) == ("memory",)


@pytest.mark.parametrize("how", ["retire", "dispute", "confirm"])
def test_changing_a_fact_status_is_stale_memory(ws, how):
    root, ids, job = ws
    if how == "confirm":
        ops.confirm_fact(root, ids["skill_go"], confirm=lambda _msg: True)
    else:
        getattr(ops, f"{how}_fact")(root, ids["skill_go"], "not true")
    assert stale_of(root, job) == ("memory",)


def test_changing_only_a_fact_timestamp_stays_fresh(ws):
    root, ids, job = ws
    path = store.load_career(root).by_id()[ids["skill_go"]].path
    text = path.read_text(encoding="utf-8")
    fm, body = split_frontmatter(text)
    fm["updated_at"] = "2030-01-01T00:00:00Z"
    path.write_text(join_frontmatter(fm, body), encoding="utf-8")
    assert stale_of(root, job) == ()


def test_changing_a_rubric_weight_is_stale_rubric(ws):
    root, ids, job = ws
    (root / "profile.md").write_text(PROFILE.replace("| Location match or remote-friendly | Medium |",
                                                     "| Location match or remote-friendly | High |"), encoding="utf-8")
    assert stale_of(root, job) == ("rubric",)


def test_changing_the_markets_row_is_stale_rubric_not_tamper(ws):
    root, ids, job = ws
    (root / "profile.md").write_text(PROFILE.replace("₹35,00,000", "₹45,00,000"), encoding="utf-8")
    assert stale_of(root, job) == ("rubric",)


def test_bumping_the_rules_version_is_stale_rules(ws, monkeypatch):
    root, ids, job = ws
    monkeypatch.setattr(rules, "RULES_VERSION", "2")
    assert stale_of(root, job) == ("rules",)


def test_editing_the_agent_half_is_stale_inputs(ws):
    root, ids, job = ws
    edit(root, job, lambda fm: fm["agent"]["requirements"][0].update(text="Kubernetes in production"))
    assert stale_of(root, job) == ("inputs",)


def test_editing_only_agent_updated_at_stays_fresh(ws):
    root, ids, job = ws
    edit(root, job, lambda fm: fm["agent"].update(updated_at="2027-01-01T00:00:00Z"))
    assert stale_of(root, job) == ()


def test_derived_years_crossing_a_boundary_is_stale_memory_not_tamper(career):
    root, ids = career
    job = make_job(root)
    agent = agent_half(ids)
    agent["requirements"].append({"id": "r4", "class": "nice", "kind": "years", "years": 5,
                                  "text": "5 years of experience", "quote": "5+ years"})
    inputs = record.read_inputs(root, job["eval"].parent, now="2026-10-10T00:00:00Z")
    path = write_agent(root, job, agent, evaluate=False)
    rec = load(root, job)
    path.write_bytes(record.to_bytes(rec, record.compute(rec, inputs)))
    later = record.read_inputs(root, path.parent, now="2028-10-10T00:00:00Z")
    rep = integrity.check(load(root, job), later)
    assert rep.stale == ("memory",) and rep.tampered == ()


# --- tamper (EVL007) -----------------------------------------------------------------------------

def tampered(root, job) -> tuple:
    rep = report(root, job)
    assert all(i.severity == "error" for i in rep.issues if i.code == "EVL007")
    return rep.tampered


def test_flipped_tier_is_evl007_scores(ws):
    root, ids, job = ws
    edit(root, job, lambda fm: fm["result"].update(tier="skip"))
    rep = report(root, job)
    assert "scores" in rep.tampered and rep.stale == ()
    assert any(i.code == "EVL007" and "scores" in i.message and "tier" in i.message for i in rep.issues)


def test_changed_score_is_evl007_scores(ws):
    root, ids, job = ws
    edit(root, job, lambda fm: fm["result"].update(coverage=0.9))
    assert "scores" in tampered(root, job)


def test_sub_precision_score_edit_is_evl007(ws):
    root, ids, job = ws
    edit(root, job, lambda fm: fm["result"].update(combined=0.80001))
    assert "scores" in tampered(root, job)


def test_deleted_cap_is_evl007_scores(career):
    root, ids = career
    job = make_job(root)
    agent = agent_half(ids)
    agent["job_facts"]["compensation"]["range"]["high"] = 3000000          # below the verified floor: capped
    write_agent(root, job, agent)
    assert load(root, job).result["caps"][0]["code"] == "compensation"
    edit(root, job, lambda fm: fm["result"].update(caps=[]))
    assert "scores" in tampered(root, job)


def test_edited_action_items_are_evl007(ws):
    root, ids, job = ws
    edit(root, job, lambda fm: fm["result"]["action_items"].pop())
    assert "scores" in tampered(root, job)


def test_edited_body_is_evl007_body(ws):
    root, ids, job = ws
    edit(root, job, lambda fm: None, body=load(root, job).body.replace("pursue", "skip"))
    rep = report(root, job)
    assert rep.tampered == ("body",)
    assert [i.code for i in rep.issues] == ["EVL007"] and "body" in rep.issues[0].message


def test_edited_basis_is_evl007_basis(ws):
    root, ids, job = ws
    edit(root, job, lambda fm: fm["result"]["basis"]["requirements"][0].update(outcome="gap"))
    rep = report(root, job)
    assert "basis" in rep.tampered
    assert any(i.code == "EVL007" and "basis_sha256" in i.message for i in rep.issues)


def test_basis_with_a_float_is_evl007_not_a_crash(ws):
    root, ids, job = ws
    edit(root, job, lambda fm: fm["result"]["basis"]["cap_inputs"]["floor"].update(amount=3500000.0))
    assert "basis" in tampered(root, job)


@pytest.mark.parametrize("change", [
    lambda r: r.pop("basis"),
    lambda r: r.update(basis="text"),
    lambda r: r.pop("stamps"),
    lambda r: r.update(stamps={"jd": "x"}),
    lambda r: r.update(caps="none"),
    lambda r: r.update(requirements=None),
    lambda r: r.update(extra=1),
    lambda r: r.update(coverage="high"),
    lambda r: r.update(combined=True),
])
def test_malformed_results_are_evl007_and_never_crash(ws, change):
    root, ids, job = ws
    edit(root, job, lambda fm: change(fm["result"]))
    assert tampered(root, job)
    assert all(i.code in ("EVL007", "EVL008", "EVL009") for i in integrity.validate_workspace(root))


def test_consistently_forged_result_with_current_stamps_is_caught_by_the_derivation_check(ws):
    root, ids, job = ws
    result = stored_result(root, job)
    basis = result["basis"]
    basis["requirements"][0]["outcome"] = "gap"
    basis["requirements"][0]["facts"], basis["requirements"][0]["status_floor"] = [], None
    display = [dict(r) for r in result["requirements"]]
    display[0].update(outcome="gap", facts=[], status_floor=None)
    forged = record.assemble(basis, result["stamps"], display)
    rewrite_result(root, job, forged)
    rep = report(root, job)
    assert rep.stale == () and rep.tampered == ("basis",)
    assert [i.code for i in rep.issues] == ["EVL007"] and "derived" in rep.issues[0].message


def test_tamper_and_stale_are_both_reported_using_the_stored_basis(ws):
    root, ids, job = ws
    edit(root, job, lambda fm: fm["result"].update(tier="skip"))
    (job["path"].parent / "jd.md").write_text(JD.replace("Example Corp", "Other Corp"), encoding="utf-8")
    ops.add_fact(root, "skill", {"name": "Rust"}, quote="said so")
    rep = report(root, job)
    assert "scores" in rep.tampered and rep.stale == ("jd", "memory")
    assert {"EVL007", "EVL008"} <= {i.code for i in rep.issues}


def test_stale_memory_or_rubric_never_reads_as_tamper(ws):
    root, ids, job = ws
    ops.retire_fact(root, ids["skill_kubernetes"], "gone")
    ops.retire_fact(root, ids["acme"], "gone")              # r1 would now derive differently
    (root / "profile.md").write_text(PROFILE.replace("| Role title matches target roles | High |",
                                                     "| Role title matches target roles | Low |"), encoding="utf-8")
    rep = report(root, job)
    assert rep.tampered == () and rep.stale == ("memory", "rubric")


def test_stamps_rules_differing_from_basis_rules_version_is_evl007(ws):
    root, ids, job = ws
    edit(root, job, lambda fm: fm["result"]["stamps"].update(rules="2"))
    rep = report(root, job)
    assert "basis" in rep.tampered and "rules" in rep.stale
    assert any("rules_version" in i.message for i in rep.issues if i.code == "EVL007")


# --- versions (EVL010) ---------------------------------------------------------------------------

def test_unsupported_basis_version_cannot_be_verified_and_is_stale(ws):
    root, ids, job = ws
    edit(root, job, lambda fm: (fm["result"]["basis"].update(basis_version="99"), fm["result"].update(tier="skip")))
    rep = report(root, job)
    assert rep.tampered == () and rep.unsupported and "99" in rep.unsupported
    assert "basis" in rep.stale
    assert sorted({i.code for i in rep.issues}) == ["EVL008", "EVL010"]
    assert all(i.severity == "warning" for i in rep.issues)


def test_unsupported_rules_version_cannot_be_verified_and_is_stale(ws):
    root, ids, job = ws

    def change(fm):
        fm["result"]["basis"]["rules_version"] = "99"
        fm["result"]["stamps"]["rules"] = "99"
        fm["result"]["tier"] = "skip"                  # a tampered tier cannot be verified either: documented limit

    edit(root, job, change)
    rep = report(root, job)
    assert rep.tampered == () and rep.stale == ("rules",)
    assert sorted({i.code for i in rep.issues}) == ["EVL008", "EVL010"]


def test_older_known_rules_version_recomputes_with_its_own_constants(ws, monkeypatch):
    root, ids, job = ws
    monkeypatch.setitem(rules.RULES, "0", dataclasses.replace(rules.RULES["1"], version="0", tier_pursue=Fraction(9, 10)))
    result = stored_result(root, job)
    result["basis"]["rules_version"] = "0"
    result["stamps"]["rules"] = "0"
    old = record.assemble(result["basis"], result["stamps"], result["requirements"])
    assert old["combined"] == 0.8 and old["tier"] == "stretch"       # v0 cut-off, not v1's pursue
    rewrite_result(root, job, old)
    rep = report(root, job)
    assert rep.tampered == () and rep.stale == ("rules",) and rep.unsupported is None
    edit(root, job, lambda fm: fm["result"].update(tier="pursue"))   # what v1 would say: still tampering under v0
    assert "scores" in report(root, job).tampered


def test_older_known_basis_version_is_verified_but_stale(ws, monkeypatch):
    root, ids, job = ws
    monkeypatch.setitem(rules.BASIS_VERSIONS, "0", dataclasses.replace(rules.BASIS_VERSIONS["1"], version="0"))
    result = stored_result(root, job)
    result["basis"]["basis_version"] = "0"
    rewrite_result(root, job, record.assemble(result["basis"], result["stamps"], result["requirements"]))
    rep = report(root, job)
    assert rep.tampered == () and rep.stale == ("basis",) and rep.unsupported is None


# --- EVL011 --------------------------------------------------------------------------------------

def market_warnings(root, job) -> list:
    return [i for i in report(root, job).issues if i.code == "EVL011"]


def test_verified_floor_and_need_do_not_warn(ws):
    root, ids, job = ws
    assert market_warnings(root, job) == []


def test_unparseable_floor_and_preferred_sponsorship_warn_with_the_cell_text(career):
    root, ids = career
    job = make_job(root)
    agent = agent_half(ids)
    agent["job_facts"]["market"] = "Elsewhere"
    write_agent(root, job, agent)
    found = market_warnings(root, job)
    assert len(found) == 2 and all(i.severity == "warning" for i in found)
    assert any("TBD — derive from home floor" in i.message and "floor" in i.message for i in found)
    assert any('"preferred"' in i.message and "sponsorship" in i.message for i in found)


def test_agent_row_conflict_warns_with_the_cell_text(career):
    root, ids = career
    job = make_job(root)
    agent = agent_half(ids)
    agent["job_facts"]["compensation"]["floor"]["amount"] = 3000000
    write_agent(root, job, agent)
    found = market_warnings(root, job)
    assert len(found) == 1 and "conflict" in found[0].message and "₹35,00,000" in found[0].message


# --- render_body ---------------------------------------------------------------------------------

GOLDEN_RESULT = {
    "tier": "weak", "combined": 0.6667, "coverage": 0.6667, "fit": None, "confidence": "low",
    "caps": [{"code": "sponsorship", "ceiling": "weak", "reason": "sponsorship is needed and not offered"}],
    "action_items": ["r2: cannot be derived from memory", "sponsorship: ask"],
    "requirements": [
        {"id": "r1", "outcome": "covered", "facts": ["exp_aaaaaaaaaa", "skl_aaaaaaaaaa"], "status_floor": "claimed"},
        {"id": "r2", "outcome": "unverifiable", "facts": [], "status_floor": None, "reason": "years of Go | cannot be derived"},
        {"id": "r3", "outcome": "gap", "facts": [], "status_floor": None},
    ],
}

GOLDEN_BODY = """
# Evaluation

<!-- Generated by `careeros evaluate` from `result:`. Do not edit: a changed body is reported as tampering (EVL007). -->

- **Tier:** weak
- **Combined score:** 0.6667
- **Coverage:** 0.6667
- **Fit:** not scored
- **Confidence:** low

## Requirements

| Id | Outcome | Facts | Weakest status | Note |
|---|---|---|---|---|
| r1 | covered | exp_aaaaaaaaaa, skl_aaaaaaaaaa | claimed |  |
| r2 | unverifiable |  |  | years of Go \\| cannot be derived |
| r3 | gap |  |  |  |

## Caps

- sponsorship (at most weak): sponsorship is needed and not offered

## What would change this

- r2: cannot be derived from memory
- sponsorship: ask
"""


def test_render_body_matches_the_golden_text():
    assert record.render_body(GOLDEN_RESULT) == GOLDEN_BODY


def test_render_body_is_deterministic_and_has_no_timestamp(ws):
    root, ids, job = ws
    result = load(root, job).result
    first = record.render_body(result)
    assert first == record.render_body(copy.deepcopy(result))
    assert "2026" not in first and "T09:" not in first


def test_render_body_for_no_requirements_and_no_caps():
    body = record.render_body(dict(GOLDEN_RESULT, requirements=[], caps=[], action_items=[], combined=None,
                                   coverage=None, tier="unrated"))
    assert "No requirements were extracted." in body and "- none" in body and "- nothing" in body
    assert "- **Combined score:** not scored" in body


# --- canonical stamps ----------------------------------------------------------------------------

_SOURCE = {"kind": "user_statement", "quote": "said so"}
_TS = {"created_at": "2026-01-01T00:00:00Z", "updated_at": "2026-02-01T00:00:00Z", "schema": 1, "origin": "manual",
       "source": _SOURCE}

FIXED_FACTS = [
    {"id": "skl_aaaaaaaaaa", "type": "skill", "status": "retired", "name": "Gö", "confirmed_at": None, **_TS},
    {"id": "exp_aaaaaaaaaa", "type": "experience", "status": "confirmed", "employer": "Initech", "title": "Engineer",
     "start": "2024-01", "end": "2025-07", "technologies": ["Go"], "confirmed_at": "2026-01-05T00:00:00Z", **_TS},
    {"id": "ach_aaaaaaaaaa", "type": "achievement", "status": "claimed", "parent": "exp_aaaaaaaaaa",
     "text": "Cut deploy time by 12.5%.", "technologies": [], "confirmed_at": None,
     "metrics": [{"raw": "12.5%", "value": 12.5, "unit": "percent", "plus": False}], **_TS},
]

FIXED_CANONICAL = (
    '{"derived":{"experiences":["exp_aaaaaaaaaa"],"years":1},"facts":['
    '{"id":"ach_aaaaaaaaaa","metrics":[{"plus":false,"raw":"12.5%","unit":"percent","value":"12.5"}],'
    '"parent":"exp_aaaaaaaaaa","status":"claimed","technologies":[],"text":"Cut deploy time by 12.5%.","type":"achievement"},'
    '{"employer":"Initech","end":"2025-07","id":"exp_aaaaaaaaaa","start":"2024-01","status":"confirmed",'
    '"technologies":["Go"],"title":"Engineer","type":"experience"},'
    '{"id":"skl_aaaaaaaaaa","name":"Gö","status":"retired","type":"skill"}],"lexicon":{}}'
).encode("utf-8")


def fixed_career(facts=FIXED_FACTS) -> Career:
    return Career([Fact(dict(fm), "", Path(f"{fm['id']}.md")) for fm in facts])


def test_memory_canonical_form_is_pinned():
    got = canon.canonical_json(record.memory_canonical(fixed_career(), Lexicon({}), "2026-10-10T00:00:00Z"))
    assert got == FIXED_CANONICAL
    stamp = record.memory_stamp(fixed_career(), Lexicon({}), "2026-10-10T00:00:00Z")
    assert stamp == hashlib.sha256(FIXED_CANONICAL).hexdigest()
    assert stamp == "53f808b74040695ba9c2177e1d2fedfcd0c812de9e32c0402234816047415085"


def test_memory_stamp_ignores_fact_order_timestamps_and_int_float_spelling():
    base = record.memory_stamp(fixed_career(), Lexicon({}), "2026-10-10T00:00:00Z")
    shuffled = [dict(f, updated_at="2031-01-01T00:00:00Z", confirmed_at=None) for f in reversed(FIXED_FACTS)]
    assert record.memory_stamp(fixed_career(shuffled), Lexicon({}), "2026-10-10T00:00:00Z") == base
    as_int = copy.deepcopy(FIXED_FACTS)
    as_int[2]["metrics"][0]["value"] = 12.50
    assert record.memory_stamp(fixed_career(as_int), Lexicon({}), "2026-10-10T00:00:00Z") == base
    changed = copy.deepcopy(FIXED_FACTS)
    changed[2]["metrics"][0]["value"] = 13
    assert record.memory_stamp(fixed_career(changed), Lexicon({}), "2026-10-10T00:00:00Z") != base


def test_jd_stamp_is_the_hash_of_normalised_text():
    assert record.jd_stamp("a  b\r\n c") == canon.sha256_hex(b"a b c")


def test_validate_never_writes(ws):
    root, ids, job = ws
    edit(root, job, lambda fm: fm["result"].update(tier="skip"))
    before = {p: p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}
    integrity.validate_workspace(root)
    after = {p: p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}
    assert before == after


# --- fix round 1: deep nesting, listing, symmetric versions, full requirement check, jd-less skeleton ----------

def test_deeply_nested_frontmatter_is_evl001_everywhere(ws):
    root, ids, job = ws
    nest = 1000
    text = "---\nid: evl_aaaaaaaaaa\ntype: evaluation\nagent: " + "[" * nest + "]" * nest + "\n---\nbody\n"
    job["eval"].write_text(text, encoding="utf-8")
    with pytest.raises(record.RecordError) as exc:
        record.load(job["eval"], root)
    assert exc.value.code == "EVL001"
    assert isinstance(record.taken_ids(root, []), set)
    found = integrity.validate_workspace(root)
    assert [i.code for i in found] == ["EVL001"]


@pytest.mark.parametrize("value", [False, "", 0, True, 1, "maybe", []])
def test_invalid_listing_values_are_evl003_not_unknown(career, value):
    root, ids = career
    job = make_job(root)
    agent = agent_half(ids)
    agent["job_facts"]["sponsorship"]["listing"] = value
    assert "EVL003" in [i.code for i in issues_of(root, job, agent)]


def test_absent_or_null_listing_is_unknown(career):
    root, ids = career
    job = make_job(root)
    agent = agent_half(ids)
    agent["job_facts"]["sponsorship"].pop("listing", None)
    assert issues_of(root, job, agent) == []
    agent["job_facts"]["sponsorship"]["listing"] = None
    assert issues_of(root, job, agent) == []


def test_unsupported_rules_version_with_a_current_rules_stamp_is_also_stale_rules(ws):
    root, ids, job = ws
    def bump(fm):
        fm["result"]["basis"]["rules_version"] = "99"
        fm["result"]["stamps"]["rules"] = "99"
    edit(root, job, bump)
    rep = report(root, job)
    assert rep.unsupported and "rules" in rep.stale and rep.tampered == ()
    # the stamp left current while only the basis version is unsupported
    edit(root, job, lambda fm: fm["result"]["stamps"].update(rules=rules.RULES_VERSION))
    assert "rules" in report(root, job).stale


def _forge_requirement(root, job, mutate):
    result = stored_result(root, job)
    display = [dict(r) for r in result["requirements"]]
    mutate(display)
    rewrite_result(root, job, record.assemble(result["basis"], result["stamps"], display))


def _unverifiable_index(root, job):
    return next(i for i, r in enumerate(stored_result(root, job)["requirements"]) if r["outcome"] == "unverifiable")


@pytest.mark.parametrize("mutate", [
    lambda d, i: d[i].update(reason="forged"),
    lambda d, i: d[i].update(extra="x"),
    lambda d, i: d[0].update(reason="years of Go cannot be derived from dated experience") if i != 0 else d[1].update(
        reason="years of Go cannot be derived from dated experience"),
])
def test_forged_requirement_reason_or_key_is_evl007_even_when_stale(ws, mutate):
    root, ids, job = ws
    idx = _unverifiable_index(root, job)
    _forge_requirement(root, job, lambda d: mutate(d, idx))
    rep = report(root, job)
    assert "scores" in rep.tampered and rep.stale == ()
    ops.add_fact(root, "skill", {"name": "Rust"}, quote="said so")
    rep = report(root, job)
    assert "scores" in rep.tampered and "memory" in rep.stale


def test_genuine_requirement_reason_is_clean(ws):
    root, ids, job = ws
    assert any("reason" in r for r in stored_result(root, job)["requirements"])
    assert report(root, job).tampered == ()


def test_skeleton_without_jd_is_clean_but_a_quote_without_jd_is_evl004(career):
    root, ids = career
    job = make_job(root)
    (job["path"].parent / "jd.md").unlink()
    write_agent(root, job, {}, evaluate=False)
    inputs = record.read_inputs(root, job["eval"].parent)
    rec = load(root, job)
    assert record.validate_agent(rec, inputs.jd_text, inputs.career, inputs.rubric, inputs.markets, inputs.job_id) == []
    assert "EVL004" in [i.code for i in issues_of(root, job, agent_half(ids))]


# --- fix round 2: multi-line terms and the documented stale-reason limit --------------------------------------

@pytest.mark.parametrize("term", ["multi\nline", "a\r\nb", "tab\there"])
@pytest.mark.parametrize("years", [5, 10, 100])
def test_fresh_unverifiable_reason_with_odd_terms_is_integrity_clean(career, term, years):
    root, ids = career
    job = make_job(root)
    agent = agent_half(ids)
    agent["requirements"][1].update(term=term, years=years)
    write_agent(root, job, agent)
    assert any("reason" in r for r in stored_result(root, job)["requirements"])
    rep = report(root, job)
    assert (rep.tampered, rep.stale) == ((), ())


def test_stale_record_accepts_a_different_legal_term_but_the_ledger_check_flags_it(ws):
    from careeros.core import ledger
    root, ids, job = ws
    rec = load(root, job)
    ledger.append_events(root, [{"type": "evaluation.completed", "actor": "system", "entity": rec.id,
                                 "action": "evaluated",
                                 "artifacts": [rec.rel, "sha256:" + canon.result_sha256(rec.result)]}])
    ops.add_fact(root, "skill", {"name": "Rust"}, quote="said so")        # now stale: derivation check is skipped
    idx = _unverifiable_index(root, job)
    _forge_requirement(root, job, lambda d: d[idx].update(reason="years of Forged cannot be derived from dated experience"))
    rep = report(root, job)
    assert rep.tampered == () and "memory" in rep.stale
    found = [i for i in integrity.validate_workspace(root) if i.code == "EVL009"]
    assert len(found) == 1 and found[0].message.startswith("mismatched")
