"""Importing resume.md: parse, diff, apply, protect confirmed and manual facts, roll back."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from helpers import make_workspace, snapshot

from careeros.core import ledger
from careeros.core.memory import check, importer, ops, store
from careeros.core.memory.lexicon import load_lexicon
from careeros.core.memory.ops import MemoryOpError

FIXTURE = Path(__file__).parent / "fixtures" / "resume_sample.md"


@pytest.fixture
def ws(tmp_path: Path, clock) -> Path:
    root = make_workspace(tmp_path / "ws")
    shutil.copy(FIXTURE, root / "resume.md")
    return root


def edit_resume(root: Path, old: str, new: str) -> None:
    path = root / "resume.md"
    text = path.read_text(encoding="utf-8")
    assert old in text
    path.write_text(text.replace(old, new), encoding="utf-8")


def facts_of(root: Path, kind: str) -> list:
    return [f for f in store.load_career(root).facts if f.kind == kind]


def parse(text: str):
    return importer.parse_resume(text, load_lexicon())


# --- parsing -------------------------------------------------------------------------------

def test_the_fixture_parses_into_every_kind_and_reports_what_it_skipped() -> None:
    result = parse(FIXTURE.read_text(encoding="utf-8"))
    assert result.errors == []
    kinds = [c.kind for c in result.candidates]
    assert kinds.count("experience") == 2 and kinds.count("education") == 1 and kinds.count("project") == 1
    assert kinds.count("certification") == 1 and kinds.count("skill") == 11
    assert any("preamble" in s for s in result.skipped) and any("Awards" in s for s in result.skipped)


def test_experience_dates_technologies_and_bullets() -> None:
    result = parse(FIXTURE.read_text(encoding="utf-8"))
    acme = next(c for c in result.candidates if c.kind == "experience" and c.fields["employer"] == "Acme Corp")
    assert acme.fields["start"] == "2025-01" and acme.fields["end"] == "present"
    assert acme.fields["technologies"] == ["Go", "Python", "Kubernetes", "AWS", "Kafka", "Terraform"]
    bullets = [c for c in result.candidates if c.kind == "achievement" and c.parent_key == acme.key]
    assert len(bullets) == 3 and not any("Tech:" in b.fields["text"] for b in bullets)
    reduced = next(b for b in bullets if "35%" in b.fields["text"])
    assert reduced.fields["metrics"][0]["unit"] == "percent" and reduced.fields["technologies"] == ["AWS", "Kafka"]


@pytest.mark.parametrize("line,expected", [
    ("Bengaluru - January 2025 - Present", ("2025-01", "present")),
    ("03/2020 - 06/2022", ("2020-03", "2022-06")),
    ("2021 – 2023", ("2021-01", "2023-12")),
    ("Remote | Sept 2019 — Mar 2021", ("2019-09", "2021-03")),
])
def test_date_ranges(line: str, expected: tuple[str, str]) -> None:
    text = f"## Experience\n\n### Acme | Engineer\n{line}\n\n- Did a thing.\n"
    cand = next(c for c in parse(text).candidates if c.kind == "experience")
    assert (cand.fields["start"], cand.fields["end"]) == expected


def test_education_projects_skills_and_certifications() -> None:
    result = parse(FIXTURE.read_text(encoding="utf-8"))
    edu = next(c for c in result.candidates if c.kind == "education")
    assert edu.fields["school"] == "Example Institute of Technology, Jabalpur" and edu.fields["end"] == "2020-06"
    assert edu.fields["degree"] == "B.Tech. in Computer Science" and edu.fields["metrics"][0]["unit"] == "score"
    project = next(c for c in result.candidates if c.kind == "project")
    assert project.fields["technologies"] == ["Go", "Kubernetes"] and "300+" in project.fields["text"]
    skills = {c.fields["name"]: c.fields["category"] for c in result.candidates if c.kind == "skill"}
    assert skills["Lambda"] == "Cloud & Infra" and skills["Go"] == "Languages" and skills["Kafka"] == "Data"
    cert = next(c for c in result.candidates if c.kind == "certification")
    assert cert.fields == {"name": "Certified Kubernetes Administrator", "year": "2023"}


@pytest.mark.parametrize("text,fragment", [
    ("## Experience\n\n### Acme Engineer\nJan 2024 - Present\n- x\n", "expected '### Employer | Title'"),
    ("## Experience\n\n- a bullet with no heading\n", "expected '### Employer | Title'"),
    ("## Experience\n\n### Acme | Engineer\n\n- A bullet but no dates.\n", "no date range"),
    ("## Education\n\n**Some School**\nB.Sc.\n", "needs a date"),
    ("## Education\n\n**Some School** - Jun 2020\n", "no degree line"),
])
def test_structure_the_grammar_cannot_place_is_a_hard_error(text: str, fragment: str) -> None:
    assert any(fragment in e for e in parse(text).errors)


def test_a_hard_error_writes_nothing(ws: Path) -> None:
    (ws / "resume.md").write_text("## Experience\n\n### Acme Engineer\n- no pipe\n", encoding="utf-8")
    before = snapshot(ws)
    with pytest.raises(MemoryOpError, match="expected '### Employer | Title'"):
        importer.apply_import(ws)
    assert snapshot(ws) == before and not (ws / "career").exists()


def test_a_missing_source_is_a_clear_error(tmp_path: Path) -> None:
    with pytest.raises(MemoryOpError, match="does not exist"):
        importer.plan_import(make_workspace(tmp_path / "ws"))


# --- import --------------------------------------------------------------------------------

def test_importing_creates_claimed_facts_with_sources_and_leaves_the_resume_alone(ws: Path) -> None:
    resume = (ws / "resume.md").read_bytes()
    result = importer.apply_import(ws)
    assert result.status == "complete" and (ws / "resume.md").read_bytes() == resume
    career = store.load_career(ws)
    assert career.issues == []
    assert {f.status for f in career.facts} == {"claimed"} and {f.get("origin") for f in career.facts} == {"imported"}
    cost = next(f for f in career.facts if "35%" in str(f.get("text")))
    assert cost.get("source")["kind"] == "resume" and cost.get("source")["path"] == "resume.md"
    assert cost.get("source")["line"] > 0 and len(cost.get("source")["sha256"]) == 64
    assert cost.get("parent") in career.by_id() and career.by_id()[cost.get("parent")].kind == "experience"
    summary = next(f for f in career.facts if f.get("section") == "summary")
    assert summary.get("parent") is None
    sources = store.load_sources(ws)
    assert sources["resume.md"]["sha256"] == store.sha256_hex(resume)


def test_the_import_is_backed_up_manifested_and_recorded_in_the_ledger(ws: Path) -> None:
    result = importer.apply_import(ws)
    manifest = json.loads((result.backup / "manifest.json").read_text())
    assert manifest["status"] == "complete" and manifest["kind"] == "memory-import"
    paths = {f["path"] for f in manifest["files"]}
    assert "career/sources.yaml" in paths and len(paths) > 20
    assert all(f["after_sha256"] for f in manifest["files"] if f["existed"] is False)
    events = ledger.read_events(ws)
    types = [e["type"] for e in events]
    assert types.count("memory.fact_added") == len(store.load_career(ws).facts)
    assert types[-1] == "memory.imported" and "manifest.json" in events[-1]["artifacts"][0]
    assert ledger.verify_chain(ws) == []


def test_importing_twice_is_a_no_op(ws: Path) -> None:
    importer.apply_import(ws)
    before, events = snapshot(ws), ledger.read_events(ws)
    backups = list((ws / ".careeros" / "backups").iterdir())
    assert importer.apply_import(ws).status == "noop"
    assert snapshot(ws) == before and ledger.read_events(ws) == events
    assert list((ws / ".careeros" / "backups").iterdir()) == backups


def test_the_diff_is_deterministic_and_a_dry_run_writes_nothing(ws: Path) -> None:
    before = snapshot(ws)
    one = [(c.result, c.action, c.label) for c in importer.plan_import(ws).changes]
    two = [(c.result, c.action, c.label) for c in importer.plan_import(ws).changes]
    assert one == two and snapshot(ws) == before and not (ws / "career").exists()


def test_a_changed_line_updates_a_claimed_fact_in_place(ws: Path) -> None:
    importer.apply_import(ws)
    fact_id = next(f.id for f in facts_of(ws, "achievement") if "35%" in str(f.get("text")))
    edit_resume(ws, "Reduced AWS costs by 35% by moving batch jobs to Kafka-based pipelines.",
                "Reduced AWS costs by 38% by moving batch jobs to Kafka-based pipelines.")
    plan = importer.plan_import(ws)
    assert [(c.result, c.action) for c in plan.changes if c.result != "unchanged"] == [("changed", "update")]
    importer.apply_import(ws)
    updated = store.load_career(ws).by_id()[fact_id]
    assert "38%" in updated.get("text") and updated.get("metrics")[0]["value"] == 38.0 and updated.status == "claimed"
    assert len(facts_of(ws, "achievement")) == 6  # not removed + added


def test_a_confirmed_fact_is_never_overwritten_and_is_listed_for_review(ws: Path) -> None:
    importer.apply_import(ws)
    fact = next(f for f in facts_of(ws, "achievement") if "35%" in str(f.get("text")))
    ops.confirm_fact(ws, fact.id, confirm=lambda p: True)
    original = store.load_career(ws).by_id()[fact.id].path.read_bytes()
    edit_resume(ws, "Reduced AWS costs by 35%", "Reduced AWS costs by 50%")
    plan = importer.plan_import(ws)
    assert plan.pending_review == 1
    result = importer.apply_import(ws)
    assert result.status == "complete" and fact.path.read_bytes() == original
    assert "35%" in store.load_career(ws).by_id()[fact.id].get("text")
    assert importer.apply_import(ws).status == "noop"  # reviewed state is recorded...
    assert importer.plan_import(ws).pending_review == 1  # ...but the review item stays visible
    assert not check.run_check(ws, "Reduced AWS costs by 50% using Kafka.").ok


def test_a_line_removed_from_the_resume_marks_its_fact_stale_and_keeps_it(ws: Path) -> None:
    importer.apply_import(ws)
    edit_resume(ws, "- Built an internal developer platform in Go used by 120 engineers.\n", "")
    result = importer.apply_import(ws)
    assert result.plan.count("removed") == 1
    stale = [f for f in store.load_career(ws).facts if f.get("stale")]
    assert len(stale) == 1 and "120 engineers" in stale[0].get("text") and stale[0].status == "claimed"
    assert any(i.code == "MEM008" for i in store.load_career(ws).issues)
    assert check.run_check(ws, "Built an internal developer platform in Go used by 120 engineers.").ok
    edit_resume(ws, "- Reduced AWS costs", "- Built an internal developer platform in Go used by 120 engineers.\n- Reduced AWS costs")
    importer.apply_import(ws)
    assert not any(f.get("stale") for f in store.load_career(ws).facts)


def test_manual_facts_and_edited_imported_facts_are_never_touched(ws: Path) -> None:
    importer.apply_import(ws)
    manual = ops.add_fact(ws, "preference", {"text": "no fintech roles"}, quote="I would rather avoid fintech")
    edited = next(f for f in facts_of(ws, "achievement") if "35%" in str(f.get("text")))
    ops.update_fact(ws, edited.id, {"text": "Reduced AWS costs by 35% (my wording)."})
    before = {p: p.read_bytes() for p in (manual.path, store.load_career(ws).by_id()[edited.id].path)}
    edit_resume(ws, "Reduced AWS costs by 35% by moving", "Reduced AWS costs by 41% by moving")
    importer.apply_import(ws)
    assert {p: p.read_bytes() for p in before} == before


def test_a_reworded_line_is_matched_as_a_change_not_a_removal_plus_addition(ws: Path) -> None:
    importer.apply_import(ws)
    edit_resume(ws, "Led a Kubernetes migration for 40+ services, cutting deploy time by 60%.",
                "Led a Kubernetes migration for 40+ services, cutting deployment time by 60%.")
    plan = importer.plan_import(ws)
    changed = [c for c in plan.changes if c.result != "unchanged"]
    assert [(c.result, c.action) for c in changed] == [("changed", "update")]


def test_a_changed_resume_makes_the_memory_stale_until_it_is_imported_again(ws: Path) -> None:
    importer.apply_import(ws)
    assert not any(i.code == "MEM006" for i in store.load_career(ws).issues)
    edit_resume(ws, "Jordan Example", "Jordan Example Jr")
    assert any(i.code == "MEM006" for i in store.load_career(ws).issues)
    result = check.run_check(ws, "Reduced AWS costs by 35% using Kafka.")
    assert result.memory_stale and [f.code for f in result.info] == ["CHK031"]
    importer.apply_import(ws)
    assert not any(i.code == "MEM006" for i in store.load_career(ws).issues)


def test_a_missing_source_file_is_reported_by_validation(ws: Path) -> None:
    importer.apply_import(ws)
    (ws / "resume.md").unlink()
    assert any(i.code == "MEM007" for i in store.load_career(ws).issues)


# --- atomicity -----------------------------------------------------------------------------

def test_a_ledger_failure_during_import_restores_every_file(ws: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    before = snapshot(ws)

    def boom(*args: object, **kwargs: object) -> None:
        raise OSError("ledger disk full")

    monkeypatch.setattr(importer.ledger, "append_events", boom)
    with pytest.raises(OSError, match="ledger disk full"):
        importer.apply_import(ws)
    monkeypatch.undo()
    assert snapshot(ws) == before
    manifests = list((ws / ".careeros" / "backups").glob("*/manifest.json"))
    assert len(manifests) == 1 and json.loads(manifests[0].read_text())["status"] == "rolled_back"
    assert not ledger.read_events(ws) and importer.apply_import(ws).status == "complete"


def test_a_failure_part_way_through_the_writes_restores_changed_files(ws: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    importer.apply_import(ws)
    edit_resume(ws, "Reduced AWS costs by 35%", "Reduced AWS costs by 36%")
    edit_resume(ws, "Cut incident response time from 45 minutes", "Cut incident response time from 50 minutes")
    before = snapshot(ws)
    real, calls = store.atomic_write_bytes, {"n": 0}

    def flaky(path: Path, data: bytes) -> None:
        if "career" in Path(path).parts and "achievements" in Path(path).parts:
            calls["n"] += 1
            if calls["n"] == 2:
                raise OSError("disk full")
        real(path, data)

    monkeypatch.setattr(store, "atomic_write_bytes", flaky)
    with pytest.raises(OSError, match="disk full"):
        importer.apply_import(ws)
    monkeypatch.undo()
    assert snapshot(ws) == before and ledger.verify_chain(ws) == []


def test_a_repeated_line_is_imported_once_and_reported() -> None:
    text = "## Experience\n\n### Acme | Engineer\nJan 2024 - Present\n\n- Built a thing.\n- Built a thing.\n"
    result = parse(text)
    assert [c.kind for c in result.candidates].count("achievement") == 1
    assert any("duplicate" in s for s in result.skipped)


def test_windows_line_endings_in_the_resume_are_handled() -> None:
    text = FIXTURE.read_text(encoding="utf-8").replace("\n", "\r\n")
    assert parse(text).errors == [] and len(parse(text).candidates) == len(parse(FIXTURE.read_text(encoding="utf-8")).candidates)


# --- review fixes --------------------------------------------------------------------------

def test_a_bullet_with_a_year_range_is_not_swallowed_as_the_date_line() -> None:
    text = "## Experience\n\n### Acme | Engineer\n- Migrated the 2019 - 2021 billing stack to Go.\n"
    result = parse(text)
    assert any("no date range" in e for e in result.errors)
    dated = parse("## Experience\n\n### Acme | Engineer\nJan 2024 - Present\n- Migrated the 2019 - 2021 billing stack to Go.\n")
    assert dated.errors == [] and [c.kind for c in dated.candidates].count("achievement") == 1


def test_a_date_range_must_be_the_first_line_under_the_heading() -> None:
    result = parse("## Experience\n\n### Acme | Engineer\nBuilt things.\nJan 2024 - Present\n")
    assert any("no date range" in e for e in result.errors)


def test_a_retired_fact_is_not_resurrected_by_a_reimport(ws: Path) -> None:
    importer.apply_import(ws)
    fact = next(f for f in facts_of(ws, "achievement") if "35%" in str(f.get("text")))
    ops.retire_fact(ws, fact.id, "no longer true", confirm=lambda p: True)
    files = sorted(p.name for p in (ws / "career").rglob("*.md"))
    plan = importer.plan_import(ws)
    assert plan.count("added") == 0 and plan.count("removed") == 0 and not plan.actionable
    assert importer.apply_import(ws).status == "noop"
    assert sorted(p.name for p in (ws / "career").rglob("*.md")) == files


@pytest.mark.parametrize("heading", ["### Acme |", "### | Engineer", "### Acme | "])
def test_an_empty_employer_or_title_is_a_hard_error(heading: str) -> None:
    text = f"## Experience\n\n{heading}\nJan 2024 - Present\n- x\n"
    assert any("expected '### Employer | Title'" in e for e in parse(text).errors)


def test_a_byte_order_mark_does_not_hide_the_first_heading(tmp_path: Path, clock) -> None:
    root = make_workspace(tmp_path / "ws")
    (root / "resume.md").write_bytes("﻿## Summary\n\nBuilds reliable systems.\n".encode("utf-8"))
    plan = importer.plan_import(root)
    assert plan.errors == [] and [c.candidate.kind for c in plan.changes] == ["achievement"]
    assert not any("Summary" in s for s in plan.skipped)


def test_a_mid_file_h1_is_reported_as_skipped(ws: Path) -> None:
    edit_resume(ws, "## Education", "# Appendix\n\n## Education")
    plan = importer.plan_import(ws)
    assert any("heading 'Appendix'" in s and "was not imported" in s for s in plan.skipped)
    assert not any("Resume - Jordan" in s for s in plan.skipped)


def test_a_project_bullet_starting_with_a_plus_is_not_a_project_heading() -> None:
    text = "## Projects\n\n**Tool** - 2024\nA tool.\n+ **Bold start** of a bullet.\n"
    kinds = [c.kind for c in parse(text).candidates]
    assert kinds.count("project") == 1


def test_two_sources_do_not_cross_resolve_parents(ws: Path) -> None:
    importer.apply_import(ws)
    shutil.copy(ws / "resume.md", ws / "other.md")
    edit_resume(ws, "Reduced AWS costs by 35%", "Reduced AWS costs by 35.5%")
    importer.apply_import(ws, "other.md")
    career = store.load_career(ws)
    other = [f for f in career.facts if (f.get("source") or {}).get("path") == "other.md"]
    mine = {f.id for f in career.facts if (f.get("source") or {}).get("path") == "resume.md"}
    assert other and all(f.get("parent") not in mine for f in other if f.kind == "achievement")


def test_two_degrees_at_the_same_school_are_both_imported() -> None:
    text = ("## Education\n\n**Some School** - Jun 2018\nB.Sc. in Maths\n\n"
            "**Some School** - Jun 2020\nM.Sc. in Maths\n")
    result = parse(text)
    assert result.errors == [] and [c.kind for c in result.candidates].count("education") == 2


def test_a_true_duplicate_degree_is_reported_and_dropped() -> None:
    text = ("## Education\n\n**Some School** - Jun 2018\nB.Sc. in Maths\n\n"
            "**Some School** - Jun 2018\nB.Sc. in Maths\n")
    result = parse(text)
    assert [c.kind for c in result.candidates].count("education") == 1
    assert any("duplicate" in s for s in result.skipped)


# --- follow-up: an invalid month is never normalised into a YYYY-MM date -----------------------

@pytest.mark.parametrize("line", ["13/2019 - 06/2022", "03/2020 - 13/2022", "00/2019 - Present", "13/2019 - Present"])
def test_an_invalid_month_in_an_experience_range_is_the_no_date_range_error(line: str) -> None:
    text = f"## Experience\n\n### Acme | Engineer\n{line}\n\n- Did a thing.\n"
    result = parse(text)
    assert any("no date range" in e for e in result.errors)
    assert not any(c.fields.get("start") or c.fields.get("end") for c in result.candidates if c.kind == "experience")


@pytest.mark.parametrize("tail", ["13/2019", "00/2019", "Jun 2018 - 13/2019"])
def test_an_invalid_month_in_an_education_date_is_the_needs_a_date_error(tail: str) -> None:
    result = parse(f"## Education\n\n**Some School** - {tail}\nB.Sc.\n")
    assert any("needs a date" in e for e in result.errors)
    assert not any(c.kind == "education" for c in result.candidates)


@pytest.mark.parametrize("tail,expected", [
    ("12/2019", "2019-12"), ("01/2019", "2019-01"), ("1/2019", "2019-01"), ("Jun 2020", "2020-06"),
    ("June 2020", "2020-06"), ("2020", "2020-12"),
])
def test_valid_education_dates_still_normalise(tail: str, expected: str) -> None:
    edu = next(c for c in parse(f"## Education\n\n**Some School** - {tail}\nB.Sc.\n").candidates if c.kind == "education")
    assert edu.fields["end"] == expected
