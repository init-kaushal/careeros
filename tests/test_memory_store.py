"""Career memory files and operations: validation rules, status semantics, guards, rollback."""

from __future__ import annotations

from pathlib import Path

import pytest
from helpers import snapshot

from careeros.core import ledger
from careeros.core.memory import ops, store
from careeros.core.memory.ops import MemoryOpError
from careeros.core.validation import validate_workspace
from careeros.core.workspace import join_frontmatter, split_frontmatter

YES = lambda prompt: True  # noqa: E731
NO = lambda prompt: False  # noqa: E731


def codes(root: Path) -> list[str]:
    return sorted({i.code for i in store.load_career(root).issues})


def rewrite(path: Path, **changes: object) -> None:
    fm, body = split_frontmatter(path.read_text(encoding="utf-8"))
    for key, value in changes.items():
        if value is KeyError:
            fm.pop(key, None)
        else:
            fm[key] = value
    path.write_text(join_frontmatter(fm, body), encoding="utf-8")


def events(root: Path, prefix: str = "memory.") -> list[dict]:
    return [e for e in ledger.read_events(root) if e["type"].startswith(prefix)]


# --- a healthy memory ----------------------------------------------------------------------

def test_a_fresh_memory_is_clean_and_every_fact_is_claimed_and_manual(career) -> None:
    root, ids = career
    loaded = store.load_career(root)
    assert loaded.issues == [] and len(loaded.facts) == len(ids)
    assert {f.status for f in loaded.facts} == {"claimed"} and {f.get("origin") for f in loaded.facts} == {"manual"}
    fact = loaded.by_id()[ids["costs"]]
    assert fact.get("source") == {"kind": "user_statement", "quote": "stated by the user in the test"}
    assert fact.get("parent") == ids["acme"] and fact.get("technologies") == ["AWS", "Kafka"]
    assert validate_workspace(root) == []


def test_facts_are_ordinary_markdown_files_a_person_can_read(career) -> None:
    root, ids = career
    text = store.load_career(root).by_id()[ids["costs"]].path.read_text(encoding="utf-8")
    assert text.startswith("---\nid: ach_") and text.rstrip().endswith("# Reduced AWS costs by 35% by moving batch jobs to Kafka-based pipelines.")


# --- validation rules ----------------------------------------------------------------------

def test_unreadable_files_are_reported_not_crashed_on(career) -> None:
    root, ids = career
    path = store.load_career(root).by_id()[ids["costs"]].path
    path.write_text("no frontmatter here\n", encoding="utf-8")
    assert "MEM001" in codes(root)
    path.write_text("---\nid: [unclosed\n---\n", encoding="utf-8")
    assert "MEM001" in codes(root)
    path.write_bytes(b"---\nid: \xff\xfe\n---\n")
    assert "MEM001" in codes(root)


@pytest.mark.parametrize("change,code", [
    ({"parent": KeyError}, "MEM002"),
    ({"text": KeyError}, "MEM002"),
    ({"id": "ach_NOTVALID"}, "MEM002"),
    ({"status": "great"}, "MEM004"),
    ({"origin": "robot"}, "MEM004"),
    ({"source": KeyError}, "MEM002"),
    ({"source": {"kind": "rumour"}}, "MEM004"),
    ({"source": {"kind": "user_statement"}}, "MEM004"),
    ({"source": {"kind": "resume"}}, "MEM004"),
    ({"created_at": "yesterday"}, "MEM004"),
    ({"parent": "exp_0000000000"}, "MEM003"),
    ({"status": "confirmed"}, "MEM005"),
    ({"status": "verified", "confirmed_at": "2026-10-01T00:00:00Z"}, "MEM005"),
])
def test_validation_rules(career, change: dict, code: str) -> None:
    root, ids = career
    rewrite(store.load_career(root).by_id()[ids["costs"]].path, **change)
    assert code in codes(root), codes(root)
    assert any(i.code == code for i in validate_workspace(root))


def test_duplicate_ids_and_misnamed_files_are_errors(career) -> None:
    root, ids = career
    by_id = store.load_career(root).by_id()
    other = by_id[ids["platform"]].path
    rewrite(other, id=ids["costs"])
    assert "MEM002" in codes(root)


def test_experience_dates_must_be_year_month_or_present(career) -> None:
    root, ids = career
    rewrite(store.load_career(root).by_id()[ids["acme"]].path, start="January 2025")
    assert "MEM004" in codes(root)


def test_a_deleted_memory_file_breaks_the_ledger_reference(career) -> None:
    root, ids = career
    store.load_career(root).by_id()[ids["pg"]].path.unlink()
    issues = [i for i in validate_workspace(root) if i.code == "LED003"]
    assert issues and ids["pg"] in issues[0].message and "retire" in issues[0].fix


# --- adding --------------------------------------------------------------------------------

def test_adding_a_fact_needs_the_users_own_words(career) -> None:
    root, ids = career
    with pytest.raises(MemoryOpError, match="--quote"):
        ops.add_fact(root, "skill", {"name": "Rust"}, quote="  ")
    with pytest.raises(MemoryOpError, match="needs"):
        ops.add_fact(root, "experience", {"employer": "X"}, quote="mine")
    with pytest.raises(MemoryOpError, match="parent"):
        ops.add_fact(root, "achievement", {"text": "Did a thing"}, quote="mine")
    with pytest.raises(MemoryOpError, match="not an experience or project"):
        ops.add_fact(root, "achievement", {"parent": ids["pg"], "text": "x"}, quote="mine")
    with pytest.raises(MemoryOpError, match="already exists"):
        ops.add_fact(root, "identity", {"name": "Someone"}, quote="mine")
    with pytest.raises(MemoryOpError, match="YYYY-MM"):
        ops.add_fact(root, "experience", {"employer": "X", "title": "Y", "start": "2020", "end": "present"}, quote="mine")


def test_adding_derives_metrics_and_technologies_and_logs_the_event(career) -> None:
    root, ids = career
    fact = ops.add_fact(root, "achievement", {"parent": ids["acme"], "text": "Cut p99 latency 3x in Rust services."},
                        quote="said so", actor="agent:claude")
    assert fact.get("metrics")[0]["unit"] == "multiplier" and fact.get("technologies") == ["Rust"]
    event = events(root)[-1]
    assert event["type"] == "memory.fact_added" and event["entity"] == fact.id and event["actor"] == "agent:claude"
    assert event["new_state"] == "claimed" and ledger.verify_chain(root) == []


def test_evidence_records_back_facts_and_validate(career) -> None:
    root, ids = career
    (root / "proof.pdf").write_bytes(b"%PDF-1.4 invented")
    evidence = ops.add_fact(root, "evidence", {"kind": "document", "path": "proof.pdf", "supports": [ids["costs"]],
                                               "note": "Cost report from the finance team"})
    assert evidence.get("source") == {"kind": "document", "path": "proof.pdf"}
    with pytest.raises(MemoryOpError, match="supports"):
        ops.add_fact(root, "evidence", {"kind": "link", "url": "https://example.com", "supports": ["ach_zzzzzzzzzz"], "note": "x"})
    assert store.load_career(root).issues == []


# --- status changes ------------------------------------------------------------------------

def test_confirming_needs_a_person_and_records_their_decision(career) -> None:
    root, ids = career
    with pytest.raises(MemoryOpError, match="interactive terminal"):
        ops.confirm_fact(root, ids["costs"])
    assert ops.confirm_fact(root, ids["costs"], confirm=NO) is None
    assert store.load_career(root).by_id()[ids["costs"]].status == "claimed"
    assert events(root)[-1]["type"] == "memory.declined"
    fact = ops.confirm_fact(root, ids["costs"], confirm=YES, actor="user")
    assert fact.status == "confirmed" and fact.get("confirmed_at")
    assert events(root)[-1]["type"] == "memory.fact_confirmed" and events(root)[-1]["prev_state"] == "claimed"
    with pytest.raises(MemoryOpError, match="needs it to be claimed"):
        ops.confirm_fact(root, ids["costs"], confirm=YES)


def test_verifying_needs_evidence_that_lists_the_fact(career) -> None:
    root, ids = career
    ops.confirm_fact(root, ids["costs"], confirm=YES)
    evidence = ops.add_fact(root, "evidence", {"kind": "link", "url": "https://example.com/report", "supports": [ids["k8s"]], "note": "report"})
    with pytest.raises(MemoryOpError, match="does not list"):
        ops.verify_fact(root, ids["costs"], evidence.id, confirm=YES)
    with pytest.raises(MemoryOpError, match="not an active evidence"):
        ops.verify_fact(root, ids["costs"], ids["k8s"], confirm=YES)
    good = ops.add_fact(root, "evidence", {"kind": "link", "url": "https://example.com/r2", "supports": [ids["costs"]], "note": "report 2"})
    with pytest.raises(MemoryOpError, match="interactive terminal"):
        ops.verify_fact(root, ids["costs"], good.id)
    fact = ops.verify_fact(root, ids["costs"], good.id, confirm=YES)
    assert fact.status == "verified" and events(root)[-1]["artifacts"] == [good.id]
    assert store.load_career(root).issues == []
    ops.retire_fact(root, good.id, "link died", confirm=YES)
    assert "MEM005" in codes(root)  # verified, but its evidence is retired


def test_changing_a_confirmed_fact_resets_it_to_claimed_after_confirmation(career) -> None:
    root, ids = career
    ops.confirm_fact(root, ids["costs"], confirm=YES)
    with pytest.raises(MemoryOpError, match="interactive terminal"):
        ops.update_fact(root, ids["costs"], {"text": "Reduced AWS costs by 36%."})
    assert ops.update_fact(root, ids["costs"], {"text": "Reduced AWS costs by 36%."}, confirm=NO) is None
    assert "35%" in store.load_career(root).by_id()[ids["costs"]].get("text")
    updated = ops.update_fact(root, ids["costs"], {"text": "Reduced AWS costs by 36%."}, confirm=YES)
    assert updated.status == "claimed" and updated.get("confirmed_at") is None and updated.get("origin") == "manual"
    assert updated.get("metrics")[0]["value"] == 36.0
    assert events(root)[-1]["type"] == "memory.fact_updated" and events(root)[-1]["prev_state"] == "confirmed"


def test_updating_a_claimed_fact_needs_no_terminal_but_only_known_fields(career) -> None:
    root, ids = career
    assert ops.update_fact(root, ids["platform"], {"text": "Built a developer platform in Go."}).status == "claimed"
    with pytest.raises(MemoryOpError, match="can change"):
        ops.update_fact(root, ids["platform"], {"parent": ids["globex"]})
    with pytest.raises(MemoryOpError, match="no fact"):
        ops.update_fact(root, "ach_zzzzzzzzzz", {"text": "x"})


def test_disputing_and_retiring_need_a_reason_and_keep_the_file(career) -> None:
    root, ids = career
    with pytest.raises(MemoryOpError, match="reason"):
        ops.dispute_fact(root, ids["costs"], " ")
    fact = ops.dispute_fact(root, ids["costs"], "the real number was lower")
    assert fact.status == "disputed" and events(root)[-1]["reason"] == "the real number was lower"
    path = fact.path
    with pytest.raises(MemoryOpError, match="interactive terminal"):
        ops.retire_fact(root, ids["costs"], "superseded")
    ops.retire_fact(root, ids["costs"], "superseded", confirm=YES)
    assert path.exists() and store.load_career(root).by_id()[ids["costs"]].status == "retired"
    with pytest.raises(MemoryOpError, match="retired"):
        ops.update_fact(root, ids["costs"], {"text": "x"})
    assert validate_workspace(root) == []  # a retired fact still resolves its ledger references


def test_disputing_a_confirmed_fact_needs_a_person(career) -> None:
    root, ids = career
    ops.confirm_fact(root, ids["costs"], confirm=YES)
    with pytest.raises(MemoryOpError, match="interactive terminal"):
        ops.dispute_fact(root, ids["costs"], "wrong")
    assert ops.dispute_fact(root, ids["costs"], "wrong", confirm=YES).status == "disputed"


# --- atomicity and the workspace guards ------------------------------------------------------

def test_a_failed_ledger_append_puts_every_file_back(career, monkeypatch: pytest.MonkeyPatch) -> None:
    root, ids = career

    def boom(*args: object, **kwargs: object) -> None:
        raise OSError("ledger disk full")

    monkeypatch.setattr(ops.ledger, "append_events", boom)
    before = snapshot(root)
    for attempt in (
        lambda: ops.add_fact(root, "skill", {"name": "Rust"}, quote="mine"),
        lambda: ops.confirm_fact(root, ids["costs"], confirm=YES),
        lambda: ops.update_fact(root, ids["platform"], {"text": "Changed."}),
        lambda: ops.retire_fact(root, ids["pg"], "gone"),
    ):
        with pytest.raises(OSError, match="ledger disk full"):
            attempt()
        assert snapshot(root) == before
    monkeypatch.undo()
    assert ledger.verify_chain(root) == []


def test_a_workspace_from_a_newer_careeros_is_refused(career) -> None:
    root, ids = career
    from careeros.core.models import WorkspaceMeta
    from careeros.core.workspace import WorkspaceError, save_meta

    save_meta(root, WorkspaceMeta(99, "9.9.9", "2026-10-01T00:00:00Z", "2026-10-01T00:00:00Z", ("claude",)))
    with pytest.raises(WorkspaceError, match="newer"):
        ops.add_fact(root, "skill", {"name": "Rust"}, quote="mine")


def test_operations_refuse_to_build_on_a_broken_memory(career) -> None:
    root, ids = career
    rewrite(store.load_career(root).by_id()[ids["costs"]].path, status="great")
    with pytest.raises(MemoryOpError, match="MEM004"):
        ops.add_fact(root, "skill", {"name": "Rust"}, quote="mine")


def test_memory_event_types_are_reserved_for_the_memory_commands() -> None:
    assert ledger.is_reserved("memory.fact_confirmed") and ledger.is_reserved("memory.imported")
    assert not ledger.is_reserved("draft.checked")
