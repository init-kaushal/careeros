import hashlib
import json
from pathlib import Path

import pytest
from helpers import LEGACY_JOBS, legacy_job_text, make_legacy_workspace, snapshot

from careeros.core import ids, ledger, versions
from careeros.core import migration as mig
from careeros.core import workspace as ws
from careeros.core.models import State, WorkspaceMeta
from careeros.core.validation import validate_workspace
from careeros.workspace.scaffold import scaffold

TS = "2026-10-01T00:00:00Z"


@pytest.fixture
def legacy(tmp_path: Path, clock) -> Path:
    root = tmp_path / "ws"
    make_legacy_workspace(root)
    return root


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _add_legacy_job(root: Path, slug: str, text: str) -> Path:
    path = root / "jobs" / "discovered" / slug / "job.md"
    path.parent.mkdir(parents=True)
    path.write_text(text, encoding="utf-8")
    return path


# --- planning --------------------------------------------------------------------------

def test_plan_covers_every_job_and_maps_legacy_statuses(legacy: Path) -> None:
    plan = mig.plan_migration(legacy)
    assert plan.errors == [] and plan.source_schema == 0 and plan.target_schema == 1
    assert plan.meta_to_write is not None and plan.meta_to_write.schema_version == 1
    assert plan.meta_to_write.framework_version == versions.installed_version()
    assert len(plan.job_changes) == len(LEGACY_JOBS) == 8
    mapped = {c.legacy_status: c.status for c in plan.job_changes}
    assert mapped == {
        "discovered": State.DISCOVERED, "applied": State.APPLIED, "interview": State.SCREEN, "offer": State.OFFER,
        "accepted": State.ACCEPTED, "declined": State.WITHDRAWN, "closed": State.WITHDRAWN,
    }
    assert any("interview" in note and "SCREEN" in note for note in plan.notes)
    assert any("midnight UTC" in note for note in plan.notes)
    assert len({c.job_id for c in plan.job_changes}) == 8 and all(ids.is_valid_id("job", c.job_id) for c in plan.job_changes)


def test_title_containing_at_splits_on_the_last_at(legacy: Path) -> None:
    plan = mig.plan_migration(legacy)
    change = next(c for c in plan.job_changes if "globex" in c.rel)
    fm, _ = ws.split_frontmatter(change.new_text)
    assert (fm["title"], fm["company"]) == ("Senior Engineer at Scale", "Globex")


def test_planning_writes_nothing(legacy: Path) -> None:
    before = snapshot(legacy)
    mig.plan_migration(legacy)
    assert snapshot(legacy) == before
    assert not (legacy / ".careeros").exists() and not (legacy / "ledger.jsonl").exists()


@pytest.mark.parametrize(
    "text,fragment",
    [
        ("# No separator here\n- **URL:** https://x.test\n- **Discovered:** 2026-10-01\n- **Status:** discovered\n", "Title at Company"),
        ("# T at C\n- **Discovered:** 2026-10-01\n- **Status:** discovered\n", "URL"),
        ("# T at C\n- **URL:** https://x.test\n- **Status:** discovered\n", "Discovered"),
        ("# T at C\n- **URL:** https://x.test\n- **Discovered:** soon\n- **Status:** discovered\n", "YYYY-MM-DD"),
        ("# T at C\n- **URL:** https://x.test\n- **Discovered:** 2026-10-01\n- **Status:** wibble\n", "unknown Status"),
        ("- **URL:** https://x.test\n", "heading"),
    ],
)
def test_each_unparseable_job_is_reported_by_file(legacy: Path, text: str, fragment: str) -> None:
    _add_legacy_job(legacy, "broken-job", text)
    plan = mig.plan_migration(legacy)
    assert any("jobs/discovered/broken-job/job.md" in e and fragment in e for e in plan.errors), plan.errors


# --- running ---------------------------------------------------------------------------

def test_migrate_prepends_frontmatter_and_changes_nothing_else(legacy: Path) -> None:
    before = snapshot(legacy)
    result = mig.run_migration(legacy, mig.plan_migration(legacy))
    assert result.status == "complete" and result.jobs == 8
    after = snapshot(legacy)
    assert set(after) == set(before)
    for rel, original in before.items():
        if rel.endswith("/job.md"):
            fm, body = ws.split_frontmatter(after[rel].decode("utf-8"))
            assert body.encode("utf-8") == original  # the old file is the new body, byte for byte
            assert ids.is_valid_id("job", fm["id"]) and fm["type"] == "job" and fm["schema"] == 1
            assert fm["created_at"] == "2026-10-01T00:00:00Z" and fm["created_at_precision"] == "date"
            assert fm["updated_at"].startswith("2026-10-06T09:")
        else:
            assert after[rel] == original, rel


def test_migrate_writes_metadata_and_a_valid_ledger(legacy: Path) -> None:
    mig.run_migration(legacy, mig.plan_migration(legacy))
    meta = ws.load_meta(legacy)
    assert meta is not None and meta.schema_version == 1 and meta.framework_version == versions.installed_version()
    events = ledger.read_events(legacy)
    assert [e["type"] for e in events].count("job.imported") == 8
    assert events[0]["type"] == "workspace.migrated"
    imported = {e["entity"]: e["new_state"] for e in events if e["type"] == "job.imported"}
    assert sorted(imported.values()).count("WITHDRAWN") == 2
    assert ledger.verify_chain(legacy) == []


def test_migrated_workspace_validates_with_no_errors_or_warnings(legacy: Path) -> None:
    mig.run_migration(legacy, mig.plan_migration(legacy))
    assert validate_workspace(legacy) == []


def test_backup_holds_originals_and_manifest_records_hashes(legacy: Path) -> None:
    before = snapshot(legacy)
    result = mig.run_migration(legacy, mig.plan_migration(legacy))
    manifest = json.loads((result.backup / "manifest.json").read_text())
    assert manifest["status"] == "complete" and manifest["kind"] == "migrate"
    assert (manifest["source_schema"], manifest["target_schema"]) == (0, 1)
    assert manifest["tool_version"] == versions.installed_version()
    by_path = {f["path"]: f for f in manifest["files"]}
    for rel, original in before.items():
        if rel.endswith("/job.md"):
            record = by_path[rel]
            assert record["existed"] is True
            assert record["before_sha256"] == _sha(original)
            assert (result.backup / rel).read_bytes() == original
            assert record["after_sha256"] == _sha((legacy / rel).read_bytes())
    assert by_path[".careeros/workspace.yaml"]["existed"] is False
    assert by_path[".careeros/workspace.yaml"]["after_sha256"] == _sha((legacy / ".careeros" / "workspace.yaml").read_bytes())
    assert by_path["ledger.jsonl"]["existed"] is False


def test_migration_is_idempotent(legacy: Path) -> None:
    mig.run_migration(legacy, mig.plan_migration(legacy))
    after_first = snapshot(legacy)
    ledger_bytes = (legacy / "ledger.jsonl").read_bytes()
    plan = mig.plan_migration(legacy)
    assert plan.empty and not plan.errors
    assert mig.run_migration(legacy, plan).status == "noop"
    assert snapshot(legacy) == after_first and (legacy / "ledger.jsonl").read_bytes() == ledger_bytes
    assert len(list((legacy / ".careeros" / "backups").iterdir())) == 1


def test_a_second_run_adopts_only_new_legacy_jobs(legacy: Path) -> None:
    mig.run_migration(legacy, mig.plan_migration(legacy))
    ids_before = {p.parent.name: ws.read_job(p).id for p in ws.job_files(legacy)}
    _add_legacy_job(legacy, "newco-engineer", legacy_job_text("Engineer", "NewCo", "https://example.com/jobs/newco", "discovered"))
    plan = mig.plan_migration(legacy)
    assert plan.meta_to_write is None and [c.rel for c in plan.job_changes] == ["jobs/discovered/newco-engineer/job.md"]
    result = mig.run_migration(legacy, plan)
    assert result.status == "complete" and (result.backup / "ledger.jsonl").exists()
    assert {p.parent.name: ws.read_job(p).id for p in ws.job_files(legacy) if p.parent.name in ids_before} == ids_before
    assert ledger.verify_chain(legacy) == []
    assert [e["type"] for e in ledger.read_events(legacy)][-2:] == ["workspace.migrated", "job.imported"]


def test_unknown_status_aborts_with_nothing_written(legacy: Path) -> None:
    _add_legacy_job(legacy, "weird", legacy_job_text("Engineer", "Weird", "https://example.com/weird", "wibble"))
    before = snapshot(legacy)
    plan = mig.plan_migration(legacy)
    assert plan.errors
    with pytest.raises(mig.MigrationError):
        mig.run_migration(legacy, plan)
    assert snapshot(legacy) == before
    assert not (legacy / ".careeros").exists() and not (legacy / "ledger.jsonl").exists()


def test_a_file_changed_after_planning_aborts_before_anything_is_written(legacy: Path) -> None:
    plan = mig.plan_migration(legacy)
    victim = legacy / "jobs" / "discovered" / "acme-backend-engineer" / "job.md"
    victim.write_text(victim.read_text() + "edited meanwhile\n")
    before = snapshot(legacy)
    with pytest.raises(mig.MigrationError, match="changed since the plan"):
        mig.run_migration(legacy, plan)
    assert snapshot(legacy) == before
    assert not (legacy / ".careeros" / "backups").exists()


def test_backup_verification_failure_aborts_before_modifying_the_workspace(
    legacy: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan = mig.plan_migration(legacy)
    real = mig.atomic_write_bytes

    def corrupting(path: Path, data: bytes) -> None:
        if "backups" in Path(path).parts and Path(path).name == "job.md":
            data = data + b"corrupt"
        real(path, data)

    monkeypatch.setattr(mig, "atomic_write_bytes", corrupting)
    before = snapshot(legacy)
    with pytest.raises(mig.MigrationError, match="backup verification failed"):
        mig.run_migration(legacy, plan)
    monkeypatch.undo()
    assert snapshot(legacy) == before
    assert not (legacy / ".careeros" / "workspace.yaml").exists()


def test_failure_while_applying_rolls_everything_back(legacy: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    before = snapshot(legacy)

    def boom(*args: object, **kwargs: object) -> None:
        raise OSError("ledger disk full")

    monkeypatch.setattr(mig.ledger, "append_events", boom)
    with pytest.raises(OSError, match="ledger disk full"):
        mig.run_migration(legacy, mig.plan_migration(legacy))
    monkeypatch.undo()
    assert snapshot(legacy) == before
    assert not (legacy / ".careeros" / "workspace.yaml").exists()
    assert not (legacy / "ledger.jsonl").exists()
    manifests = list((legacy / ".careeros" / "backups").glob("*/manifest.json"))
    assert len(manifests) == 1 and json.loads(manifests[0].read_text())["status"] == "rolled_back"
    assert mig.find_incomplete_operations(legacy) == []


def test_find_incomplete_operations_reports_pending_manifests(legacy: Path) -> None:
    backup = legacy / ".careeros" / "backups" / "20261006T090000Z-migrate"
    backup.mkdir(parents=True)
    (backup / "manifest.json").write_text(json.dumps({"status": "pending", "files": []}))
    assert mig.find_incomplete_operations(legacy) == [backup]


def test_migrate_refuses_a_workspace_from_the_future(legacy: Path) -> None:
    ws.save_meta(legacy, WorkspaceMeta(2, versions.installed_version(), TS, TS, ()))
    with pytest.raises(ws.WorkspaceError, match="newer than this CareerOS supports"):
        mig.plan_migration(legacy)


# --- upgrade ---------------------------------------------------------------------------

def _claude_workspace(tmp_path: Path, framework: str = "0.1.0") -> Path:
    root = tmp_path / "ws"
    scaffold(root, runtime="claude")
    (root / "profile.md").write_text("# my profile\n", encoding="utf-8")
    ws.save_meta(root, WorkspaceMeta(1, framework, TS, TS, ("claude",)))
    ledger.append_event(root, type="workspace.created", actor="system", action="created")
    return root


def test_upgrade_plan_reports_new_changed_and_unchanged(tmp_path: Path, clock) -> None:
    root = _claude_workspace(tmp_path)
    (root / ".claude" / "skills" / "browse" / "SKILL.md").write_text("customised")
    (root / ".claude" / "skills" / "track" / "SKILL.md").unlink()
    plan = mig.plan_upgrade(root)
    status = {i.rel: i.status for i in plan.items}
    assert status[".claude/skills/browse/SKILL.md"] == "changed"
    assert status[".claude/skills/track/SKILL.md"] == "new"
    assert status["CLAUDE.md"] == "unchanged"
    assert plan.framework_stale and not plan.noop and not plan.schema_behind


def test_upgrade_backs_up_refreshes_and_keeps_user_data(tmp_path: Path, clock) -> None:
    root = _claude_workspace(tmp_path)
    skill = root / ".claude" / "skills" / "browse" / "SKILL.md"
    skill.write_text("customised")
    user_files = {rel: data for rel, data in snapshot(root).items() if not rel.startswith(".claude/") and rel != "CLAUDE.md"}
    result = mig.run_upgrade(root, mig.plan_upgrade(root))
    assert result.status == "complete"
    assert skill.read_text() != "customised"
    assert (result.backup / ".claude" / "skills" / "browse" / "SKILL.md").read_text() == "customised"
    manifest = json.loads((result.backup / "manifest.json").read_text())
    assert manifest["kind"] == "upgrade" and manifest["status"] == "complete"
    record = next(f for f in manifest["files"] if f["path"] == ".claude/skills/browse/SKILL.md")
    assert record["before_sha256"] == _sha(b"customised") and record["after_sha256"] == _sha(skill.read_bytes())
    assert ws.load_meta(root).framework_version == versions.installed_version()
    assert ledger.read_events(root)[-1]["type"] == "workspace.upgraded"
    assert ledger.verify_chain(root) == []
    for rel, data in user_files.items():
        assert (root / rel).read_bytes() == data, rel


def test_upgrade_is_a_no_op_when_everything_is_current(tmp_path: Path, clock) -> None:
    root = _claude_workspace(tmp_path, framework=versions.installed_version())
    plan = mig.plan_upgrade(root)
    assert plan.noop
    assert mig.run_upgrade(root, plan).status == "noop"


def test_upgrade_on_a_legacy_workspace_refreshes_files_but_creates_no_metadata(tmp_path: Path, clock) -> None:
    root = tmp_path / "ws"
    scaffold(root, runtime="claude")
    (root / ".claude" / "skills" / "browse" / "SKILL.md").write_text("old")
    plan = mig.plan_upgrade(root)
    assert plan.schema_behind
    mig.run_upgrade(root, plan)
    assert (root / ".claude" / "skills" / "browse" / "SKILL.md").read_text() != "old"
    assert not (root / ".careeros" / "workspace.yaml").exists() and not (root / "ledger.jsonl").exists()


def test_upgrade_refuses_a_workspace_from_the_future(tmp_path: Path, clock) -> None:
    root = _claude_workspace(tmp_path, framework="9.0.0")
    with pytest.raises(ws.WorkspaceError, match="newer than the installed"):
        mig.run_upgrade(root, mig.plan_upgrade(root))


def test_upgrade_failure_rolls_back_files_and_metadata(tmp_path: Path, clock, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _claude_workspace(tmp_path)
    skill = root / ".claude" / "skills" / "browse" / "SKILL.md"
    skill.write_text("customised")
    meta_before = (root / ".careeros" / "workspace.yaml").read_bytes()
    before = snapshot(root)

    def boom(*args: object, **kwargs: object) -> None:
        raise OSError("ledger disk full")

    monkeypatch.setattr(mig.ledger, "append_event", boom)
    with pytest.raises(OSError):
        mig.run_upgrade(root, mig.plan_upgrade(root))
    monkeypatch.undo()
    assert snapshot(root) == before
    assert (root / ".careeros" / "workspace.yaml").read_bytes() == meta_before
    assert mig.find_incomplete_operations(root) == []
