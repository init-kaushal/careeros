"""Final-review fixes: date forms, a broken lexicon, evidence that backs a verified fact, stale notes, CLI edges."""

from __future__ import annotations

from pathlib import Path

import pytest
from helpers import make_workspace, snapshot
from typer.testing import CliRunner

from careeros.cli.main import app
from careeros.core.memory import check, importer, ops, store
from careeros.core.memory.lexicon import LexiconError, load_lexicon
from careeros.core.memory.ops import MemoryOpError
from careeros.core.validation import validate_workspace
from careeros.core.workspace import split_frontmatter, join_frontmatter

YES = lambda prompt: True  # noqa: E731
runner = CliRunner()
FIXTURE = Path(__file__).parent / "fixtures" / "resume_sample.md"


def run(root: Path, *args: str, input: str | None = None):
    return runner.invoke(app, [*args, "--workspace", str(root)], input=input)


def rewrite(path: Path, **changes: object) -> None:
    fm, body = split_frontmatter(path.read_text(encoding="utf-8"))
    fm.update(changes)
    path.write_text(join_frontmatter(fm, body), encoding="utf-8")


# --- 1. dates ------------------------------------------------------------------------------

BAD_ADDS = [
    ("education", {"school": "Fake University", "degree": "BSc", "end": "June 2020"}, "end"),
    ("education", {"school": "Fake University", "degree": "BSc", "end": "2020"}, "end"),
    ("experience", {"employer": "Initech", "title": "Engineer", "start": "present", "end": "present"}, "start"),
    ("experience", {"employer": "Initech", "title": "Engineer", "start": "2020-01", "end": "later"}, "end"),
    ("certification", {"name": "Foo Cert", "year": "circa 2020"}, "year"),
    ("certification", {"name": "Foo Cert", "year": "20"}, "year"),
]


@pytest.mark.parametrize("kind,fields,key", BAD_ADDS)
def test_add_refuses_dates_the_check_cannot_read(career, kind, fields, key) -> None:
    root, _ = career
    before = snapshot(root)
    with pytest.raises(MemoryOpError, match=key):
        ops.add_fact(root, kind, fields, quote="q")
    assert snapshot(root) == before


@pytest.mark.parametrize("fact,changes", [
    ("acme", {"start": "present"}), ("edu", {"end": "June 2020"}), ("cka", {"year": "circa 2020"}),
])
def test_update_refuses_the_same_dates(career, fact, changes) -> None:
    root, ids = career
    before = snapshot(root)
    with pytest.raises(MemoryOpError):
        ops.update_fact(root, ids[fact], changes, confirm=YES)
    assert snapshot(root) == before


def test_update_accepts_good_dates(career) -> None:
    root, ids = career
    ops.update_fact(root, ids["edu"], {"end": "2021-05"}, confirm=YES)
    ops.update_fact(root, ids["cka"], {"year": "2024"}, confirm=YES)


@pytest.mark.parametrize("fact,key,value", [
    ("acme", "start", "present"), ("edu", "end", "June 2020"), ("cka", "year", "circa 2020"),
])
def test_hand_edited_dates_are_mem004_and_check_never_crashes(career, fact, key, value) -> None:
    root, ids = career
    path = store.load_career(root).by_id()[ids[fact]].path
    rewrite(path, **{key: value})
    issues = [i for i in store.load_career(root).errors if i.code == "MEM004"]
    assert issues and key in issues[0].message
    assert any(i.code == "MEM004" for i in validate_workspace(root))


def test_record_skips_a_date_it_cannot_parse(career) -> None:
    root, ids = career
    path = store.load_career(root).by_id()[ids["edu"]].path
    rewrite(path, end="June 2020")
    memory = check.Memory(store.load_career(root), store.lexicon_for(root, store.load_career(root)), "2026-10-10T00:00:00Z")
    record = next(r for r in memory.records if r.id == ids["edu"])
    assert record.points == frozenset()


# --- 2. lexicon ----------------------------------------------------------------------------

BAD_LEXICONS = ["technologies: [unclosed\n", "technologies: Go\n", "certifications: [CKA]\n", "stoplist: 5\n",
                "technologies:\n  - aliases: [x]\n", "role_nouns: [1, 2]\n", "certifications:\n  - name: 5\n"]


@pytest.mark.parametrize("text", BAD_LEXICONS)
def test_bad_extra_lexicon_raises_lexicon_error(tmp_path: Path, text: str) -> None:
    (tmp_path / "career").mkdir()
    (tmp_path / "career" / "lexicon.yaml").write_text(text, encoding="utf-8")
    with pytest.raises(LexiconError, match="lexicon.yaml"):
        load_lexicon(tmp_path)


@pytest.mark.parametrize("text", BAD_LEXICONS)
def test_bad_lexicon_is_mem001_and_check_exits_2(career, text: str) -> None:
    root, _ = career
    (root / "career" / "lexicon.yaml").write_text(text, encoding="utf-8")
    errors = store.load_career(root).errors
    assert [(i.code, i.path) for i in errors] == [("MEM001", "career/lexicon.yaml")]
    assert any(i.code == "MEM001" and i.path == "career/lexicon.yaml" for i in validate_workspace(root))
    result = run(root, "check", "-", input="I worked at Acme Corp.")
    assert result.exit_code == 2 and "Traceback" not in result.output
    with pytest.raises(MemoryOpError):
        ops.add_fact(root, "preference", {"text": "x"}, quote="q")


def test_check_command_turns_a_workspace_error_into_exit_2(career, monkeypatch) -> None:
    from careeros.core.workspace import WorkspaceError

    root, _ = career

    def boom(*a, **k):
        raise WorkspaceError("kaboom")

    monkeypatch.setattr("careeros.core.memory.check.run_check", boom)
    result = run(root, "check", "-", input="I worked at Acme Corp.")
    assert result.exit_code == 2 and "error: kaboom" in result.output


def test_a_good_extra_lexicon_still_loads(career) -> None:
    root, _ = career
    (root / "career" / "lexicon.yaml").write_text("technologies:\n  - name: Zig\nstoplist: [foo]\n", encoding="utf-8")
    assert not store.load_career(root).errors
    assert load_lexicon(root).canonical_tech("zig") == "Zig"


# --- 3. evidence that is the only backing of a verified fact -------------------------------

def _verified(root, ids, extra_evidence: bool = False):
    ops.confirm_fact(root, ids["costs"], confirm=YES)
    ev = ops.add_fact(root, "evidence", {"kind": "link", "url": "https://x", "note": "invoice", "supports": [ids["costs"]]})
    ops.verify_fact(root, ids["costs"], ev.id, confirm=YES)
    ev2 = None
    if extra_evidence:
        ev2 = ops.add_fact(root, "evidence", {"kind": "link", "url": "https://y", "note": "report", "supports": [ids["costs"]]})
    return ev, ev2


@pytest.mark.parametrize("action", ["retire", "dispute"])
def test_only_evidence_of_a_verified_fact_cannot_be_retired_or_disputed(career, action) -> None:
    root, ids = career
    ev, _ = _verified(root, ids)
    before = snapshot(root)
    fn = ops.retire_fact if action == "retire" else ops.dispute_fact
    with pytest.raises(MemoryOpError) as exc:
        fn(root, ev.id, "wrong", confirm=YES)
    assert str(exc.value) == (f"{ev.id} is the only evidence for verified fact {ids['costs']}; "
                              "dispute or re-verify that fact first")
    assert snapshot(root) == before
    assert not store.load_career(root).errors


@pytest.mark.parametrize("action", ["retire", "dispute"])
def test_evidence_can_go_when_a_second_active_record_supports_the_fact(career, action) -> None:
    root, ids = career
    ev, _ = _verified(root, ids, extra_evidence=True)
    (ops.retire_fact if action == "retire" else ops.dispute_fact)(root, ev.id, "wrong", confirm=YES)
    assert not store.load_career(root).errors


def test_evidence_for_unverified_facts_can_be_retired(career) -> None:
    root, ids = career
    ev = ops.add_fact(root, "evidence", {"kind": "link", "url": "https://x", "note": "n", "supports": [ids["k8s"]]})
    ops.retire_fact(root, ev.id, "wrong")
    assert not store.load_career(root).errors


# --- 4. stale supporting facts -------------------------------------------------------------

def test_check_notes_a_stale_supporting_fact(tmp_path: Path, clock) -> None:
    ws = make_workspace(tmp_path / "ws")
    (ws / "resume.md").write_text(FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")
    importer.apply_import(ws)
    line = "- Built an internal developer platform in Go used by 120 engineers.\n"
    resume = ws / "resume.md"
    resume.write_text(resume.read_text(encoding="utf-8").replace(line, ""), encoding="utf-8")
    importer.apply_import(ws)
    stale = next(f for f in store.load_career(ws).facts if f.get("stale"))
    result = check.run_check(ws, "Built an internal developer platform in Go used by 120 engineers.")
    assert result.ok
    notes = [f for f in result.info if f.code == "CHK031" and stale.id in f.reason]
    assert len(notes) == 1 and "stale" in notes[0].reason
    assert not [f for f in check.run_check(ws, "Reduced AWS costs by 35% using Kafka.").info if "stale fact" in f.reason]


# --- 5. memory status with a corrupt sources.yaml ------------------------------------------

def test_memory_status_survives_a_corrupt_sources_file(career) -> None:
    root, _ = career
    (root / "career" / "sources.yaml").write_text("sources: [unclosed\n", encoding="utf-8")
    result = run(root, "memory", "status", "--json")
    assert result.exit_code == 0 and "Traceback" not in result.output
    assert '"pending_review": 0' in result.output and "MEM001" in result.output


# --- 6. summary achievements ---------------------------------------------------------------

@pytest.mark.parametrize("parent", [None, ""])
def test_a_summary_achievement_needs_no_parent(career, parent) -> None:
    root, _ = career
    fields = {"section": "summary", "text": "Shipped a thing."}
    if parent is not None:
        fields["parent"] = parent
    fact = ops.add_fact(root, "achievement", fields, quote="q")
    assert fact.get("parent") is None
    assert not store.load_career(root).errors


def test_a_summary_achievement_can_be_added_from_the_cli(career) -> None:
    root, _ = career
    result = run(root, "memory", "add", "--kind", "achievement", "--set", "section=summary",
                 "--text", "Shipped a thing.", "--quote", "I shipped a thing")
    assert result.exit_code == 0, result.output
    assert not store.load_career(root).errors
