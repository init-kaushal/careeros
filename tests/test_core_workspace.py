import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from careeros.core import versions
from careeros.core import workspace as ws
from careeros.core.models import State, WorkspaceMeta

TS = "2026-10-06T09:15:00Z"


def _meta(schema: int = 1, framework: str = "0.3.0") -> WorkspaceMeta:
    return WorkspaceMeta(schema, framework, TS, TS, ("claude",))


def _write_job(root: Path, slug: str, job_id: str = "job_aaaaaaaaaa", status: str = "DISCOVERED") -> Path:
    fm = {
        "id": job_id, "type": "job", "schema": 1, "status": status,
        "company": "Acme", "title": "Backend Engineer", "url": f"https://x.test/{slug}",
        "created_at": TS, "updated_at": TS,
    }
    path = root / "jobs" / "discovered" / slug / "job.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(ws.join_frontmatter(fm, "# Backend Engineer at Acme\n"), encoding="utf-8")
    return path


# --- atomic writes -----------------------------------------------------------

def test_atomic_write_creates_and_overwrites_without_leaving_temp_files(tmp_path: Path) -> None:
    target = tmp_path / "sub" / "a.txt"
    ws.atomic_write_bytes(target, b"one")
    assert target.read_bytes() == b"one"
    ws.atomic_write_bytes(target, b"two")
    assert target.read_bytes() == b"two"
    assert [p.name for p in target.parent.iterdir()] == ["a.txt"]


def test_atomic_write_preserves_existing_mode(tmp_path: Path) -> None:
    target = tmp_path / "a.txt"
    target.write_text("x")
    target.chmod(0o640)
    ws.atomic_write_bytes(target, b"y")
    assert target.stat().st_mode & 0o777 == 0o640


def test_failed_atomic_write_leaves_target_unchanged_and_no_temp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "a.txt"
    target.write_bytes(b"original")

    def boom(src: object, dst: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        ws.atomic_write_bytes(target, b"new")
    monkeypatch.undo()
    assert target.read_bytes() == b"original"
    assert [p.name for p in tmp_path.iterdir()] == ["a.txt"]


# --- frontmatter --------------------------------------------------------------

def test_frontmatter_round_trip_keeps_body_byte_identical() -> None:
    body = "# Title at Co\n\n- **URL:** https://x.test/a?b=1\n---\nnot a fence in the body — ✓\n\ttabbed\n"
    data = {"id": "job_0000000000", "title": "Senior: Engineer (Go) — Platform", "created_at": TS, "archived": False}
    text = ws.join_frontmatter(data, body)
    parsed, rest = ws.split_frontmatter(text)
    assert text.startswith("---\n")
    assert rest == body
    assert parsed == data


def test_split_without_frontmatter_returns_none_and_full_text() -> None:
    assert ws.split_frontmatter("# Just a heading\n") == (None, "# Just a heading\n")


def test_split_invalid_yaml_and_non_mapping_raise() -> None:
    with pytest.raises(ws.WorkspaceError):
        ws.split_frontmatter("---\nkey: [unclosed\n---\nbody\n")
    with pytest.raises(ws.WorkspaceError, match="mapping"):
        ws.split_frontmatter("---\n- a\n- b\n---\nbody\n")


# --- metadata and the write guard ------------------------------------------------

def test_meta_round_trip_and_missing(tmp_path: Path) -> None:
    assert ws.load_meta(tmp_path) is None
    ws.save_meta(tmp_path, _meta())
    assert ws.load_meta(tmp_path) == _meta()
    assert (tmp_path / ".careeros" / "workspace.yaml").is_file()


def test_load_meta_rejects_bad_content(tmp_path: Path) -> None:
    path = tmp_path / ".careeros" / "workspace.yaml"
    path.parent.mkdir()
    path.write_text("schema_version: 1\n")
    with pytest.raises(ws.WorkspaceError, match="missing key"):
        ws.load_meta(tmp_path)
    path.write_text(f"schema_version: one\nframework_version: 0.3.0\ncreated_at: '{TS}'\nupdated_at: '{TS}'\n")
    with pytest.raises(ws.WorkspaceError, match="schema_version"):
        ws.load_meta(tmp_path)
    path.write_text(f"schema_version: 1\nframework_version: abc\ncreated_at: '{TS}'\nupdated_at: '{TS}'\n")
    with pytest.raises(ws.WorkspaceError, match="MAJOR.MINOR.PATCH"):
        ws.load_meta(tmp_path)


def test_ensure_writable_rules(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(versions, "installed_version", lambda: "0.3.0")
    assert ws.ensure_writable(tmp_path) is None  # legacy workspace: no metadata, nothing to refuse
    ws.save_meta(tmp_path, _meta(framework="0.3.0"))
    assert ws.ensure_writable(tmp_path) is not None
    ws.save_meta(tmp_path, _meta(framework="0.2.0"))
    assert ws.ensure_writable(tmp_path) is not None  # older workspace: upgrade available, still writable
    ws.save_meta(tmp_path, _meta(framework="0.4.0"))
    with pytest.raises(ws.WorkspaceError, match="newer than the installed"):
        ws.ensure_writable(tmp_path)
    ws.save_meta(tmp_path, _meta(schema=2, framework="0.3.0"))
    with pytest.raises(ws.WorkspaceError, match="schema 2 is newer"):
        ws.ensure_writable(tmp_path)


# --- discovery -----------------------------------------------------------------

def test_find_workspace_explicit_parent_walk_and_failure(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    (root / "jobs").mkdir(parents=True)
    (root / "profile.md").write_text("# p\n")
    nested = root / "jobs" / "deep"
    nested.mkdir()
    assert ws.find_workspace(explicit=root) == root.resolve()
    assert ws.find_workspace(start=nested) == root.resolve()
    with pytest.raises(ws.WorkspaceError, match="not a CareerOS workspace"):
        ws.find_workspace(explicit=tmp_path / "elsewhere")
    with pytest.raises(ws.WorkspaceError, match="no CareerOS workspace found"):
        ws.find_workspace(start=tmp_path / "nothing-here")


def test_find_workspace_recognises_meta_file_alone(tmp_path: Path) -> None:
    ws.save_meta(tmp_path, _meta())
    assert ws.is_workspace_root(tmp_path)


# --- jobs ------------------------------------------------------------------------

def test_read_job_and_resolve_by_id_slug_and_path(tmp_path: Path) -> None:
    path = _write_job(tmp_path, "acme-backend")
    job = ws.read_job(path)
    assert job.id == "job_aaaaaaaaaa" and job.status is State.DISCOVERED and job.slug == "acme-backend"
    assert job.body == "# Backend Engineer at Acme\n"
    for ref in ("job_aaaaaaaaaa", "acme-backend", "jobs/discovered/acme-backend", str(path.parent)):
        assert ws.resolve_job(tmp_path, ref).path == path
    with pytest.raises(ws.WorkspaceError, match="no job matches"):
        ws.resolve_job(tmp_path, "nope")


def test_read_job_reports_missing_keys_bad_status_and_no_frontmatter(tmp_path: Path) -> None:
    legacy = tmp_path / "jobs" / "discovered" / "legacy" / "job.md"
    legacy.parent.mkdir(parents=True)
    legacy.write_text("# T at C\n")
    with pytest.raises(ws.WorkspaceError, match="no frontmatter"):
        ws.read_job(legacy)
    partial = tmp_path / "jobs" / "discovered" / "partial" / "job.md"
    partial.parent.mkdir(parents=True)
    partial.write_text(ws.join_frontmatter({"id": "job_aaaaaaaaaa"}, "x\n"))
    with pytest.raises(ws.WorkspaceError, match="missing"):
        ws.read_job(partial)
    bad = _write_job(tmp_path, "bad", status="NONSENSE")
    with pytest.raises(ws.WorkspaceError, match="invalid status"):
        ws.read_job(bad)


# --- lock ------------------------------------------------------------------------

def test_lock_is_reentrant_within_a_thread(tmp_path: Path) -> None:
    with ws.WorkspaceLock(tmp_path, timeout=0.5):
        with ws.WorkspaceLock(tmp_path, timeout=0.5):
            pass
    with ws.WorkspaceLock(tmp_path, timeout=0.5):
        pass
    assert (tmp_path / ".careeros" / "lock").exists()


_HOLDER = """
import sys, time
from pathlib import Path
from careeros.core.workspace import WorkspaceLock
with WorkspaceLock(Path(sys.argv[1])):
    print("locked", flush=True)
    time.sleep(float(sys.argv[2]))
"""


def test_lock_excludes_another_process_and_times_out_with_clear_message(tmp_path: Path) -> None:
    proc = subprocess.Popen(
        [sys.executable, "-c", _HOLDER, str(tmp_path), "2"], stdout=subprocess.PIPE, text=True
    )
    try:
        assert proc.stdout.readline().strip() == "locked"
        with pytest.raises(ws.LockTimeout, match="another careeros command is running"):
            with ws.WorkspaceLock(tmp_path, timeout=0.3):
                pass
    finally:
        proc.wait(timeout=10)
    with ws.WorkspaceLock(tmp_path, timeout=1):
        pass


def test_lock_serialises_threads_so_no_update_is_lost(tmp_path: Path) -> None:
    counter = tmp_path / "counter.txt"
    counter.write_text("0")

    def worker() -> None:
        for _ in range(20):
            with ws.WorkspaceLock(tmp_path, timeout=10):
                counter.write_text(str(int(counter.read_text()) + 1))

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert counter.read_text() == "80"
