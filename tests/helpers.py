"""Builders for test workspaces."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from careeros.core import ids, ledger, models, versions
from careeros.core.models import State, WorkspaceMeta
from careeros.core.workspace import join_frontmatter, save_meta

TS = "2026-10-01T00:00:00Z"


def make_workspace(root: Path, framework_version: str | None = None) -> Path:
    """A schema-1 workspace with a profile, an activity log, an empty pipeline and no jobs."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "profile.md").write_text("# Profile\n", encoding="utf-8")
    (root / "jobs" / "discovered").mkdir(parents=True, exist_ok=True)
    (root / "activity.md").write_text("# Activity Log\n", encoding="utf-8")
    (root / "jobs" / "pipeline.md").write_text("# Job Pipeline\n\n## Active\n\n", encoding="utf-8")
    save_meta(
        root,
        WorkspaceMeta(
            versions.SCHEMA_VERSION, framework_version or versions.installed_version(), TS, TS, ("claude",)
        ),
    )
    return root


def add_job(
    root: Path,
    slug: str,
    *,
    status: State = State.DISCOVERED,
    company: str = "Acme",
    title: str = "Backend Engineer",
    url: str | None = None,
    bullet: str = "discovered",
    baseline: bool = True,
    pipeline: bool = True,
) -> dict:
    url = url or f"https://example.com/jobs/{slug}"
    job_id = ids.new_id("job")
    frontmatter = {
        "id": job_id, "type": "job", "schema": 1, "status": status.value,
        "company": company, "title": title, "url": url, "created_at": TS, "updated_at": TS,
    }
    body = f"# {title} at {company}\n\n- **URL:** {url}\n- **Status:** {bullet}\n\n## Notes\nhello\n"
    path = root / "jobs" / "discovered" / slug / "job.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(join_frontmatter(frontmatter, body), encoding="utf-8")
    if pipeline:
        with (root / "jobs" / "pipeline.md").open("a", encoding="utf-8") as handle:
            handle.write(f"- [ ] **{company}** — {title} · Remote · Score 8 · 2026-10-01 · {url}\n")
    if baseline:
        ledger.append_event(
            root, type="job.imported", actor="system", entity=job_id, new_state=status.value,
            action=f"imported {company}", source="migration",
        )
    return {"id": job_id, "path": path, "url": url, "slug": slug}


# --- legacy (schema 0) workspaces ---------------------------------------------------

LEGACY_JOBS = [
    # slug, title, company, legacy status, pipeline icon the legacy skills would show
    ("acme-backend-engineer", "Backend Engineer", "Acme", "discovered", " "),
    ("globex-senior-engineer-at-scale", "Senior Engineer at Scale", "Globex", "discovered", " "),
    ("initech-platform-engineer", "Platform Engineer", "Initech", "applied", "~"),
    ("hooli-staff-engineer", "Staff Engineer", "Hooli", "interview", "?"),
    ("umbrella-sde-3", "SDE-3", "Umbrella", "offer", "✓"),
    ("wayne-principal-engineer", "Principal Engineer", "Wayne", "accepted", "✓"),
    ("stark-engineer", "Engineer", "Stark", "declined", "x"),
    ("oscorp-engineer", "Engineer", "Oscorp", "closed", "x"),
]


def legacy_job_text(title: str, company: str, url: str, status: str, discovered: str = "2026-10-01") -> str:
    return (
        f"# {title} at {company}\n\n"
        f"- **Board:** LinkedIn\n- **Location:** Bengaluru\n- **Market:** home\n"
        f"- **URL:** {url}\n- **Score:** 8\n- **Discovered:** {discovered}\n- **Status:** {status}\n\n"
        f"## Notes\nSnippet text — with unicode ✓\n"
    )


def make_legacy_workspace(root: Path) -> None:
    """A schema-0 workspace shaped like the real one: no metadata, no frontmatter, no ledger."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "profile.md").write_text("# Example Career Profile\n\n## Markets\n| Market | Min base |\n", encoding="utf-8")
    (root / "boards.md").write_text("# Job Boards\n\n### linkedin\n- **Market:** home\n", encoding="utf-8")
    (root / "resume.md").write_text("# Resume\n\n## Education\n", encoding="utf-8")
    (root / "activity.md").write_text(
        "# Activity Log\n\n<!-- append-only; newest entries at top -->\n2026-09-29 careeros update: variants\n",
        encoding="utf-8",
    )
    (root / "resume-variants").mkdir()
    (root / "resume-variants" / "Resume.pdf").write_bytes(b"%PDF-1.4\n%fixture \x00\xff\n")
    (root / "markets").mkdir()
    (root / "markets" / "singapore.md").write_text("# Singapore — Market Notes\n", encoding="utf-8")
    pipeline = ["# Job Pipeline", "", "## Active", ""]
    for slug, title, company, status, icon in LEGACY_JOBS:
        url = f"https://example.com/jobs/{slug}"
        job_dir = root / "jobs" / "discovered" / slug
        job_dir.mkdir(parents=True)
        (job_dir / "job.md").write_text(legacy_job_text(title, company, url, status), encoding="utf-8")
        pipeline.append(f"- [{icon}] **{company}** — {title} · Bengaluru · Score 8 · 2026-10-01 · {url}")
    (root / "jobs" / "discovered" / "acme-backend-engineer" / "people.md").write_text("# Hiring contacts\n", encoding="utf-8")
    (root / "jobs" / "pipeline.md").write_text("\n".join(pipeline) + "\n", encoding="utf-8")


def snapshot(root: Path) -> dict[str, bytes]:
    """Every file in the workspace except CareerOS's own bookkeeping, keyed by relative path."""
    skip = {".careeros", "ledger.jsonl"}
    return {
        p.relative_to(root).as_posix(): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file() and p.relative_to(root).parts[0] not in skip
    }


def make_claude_workspace(root: Path, framework: str = "0.1.0") -> Path:
    """A scaffolded Claude workspace with metadata at an older framework version and a ledger."""
    from careeros.workspace.scaffold import scaffold

    scaffold(root, runtime="claude")
    (root / "profile.md").write_text("# my profile\n", encoding="utf-8")
    save_meta(root, WorkspaceMeta(versions.SCHEMA_VERSION, framework, TS, TS, ("claude",)))
    ledger.append_event(root, type="workspace.created", actor="system", action="created")
    return root

# --- career memory ---------------------------------------------------------------------

def make_career(root: Path) -> dict[str, str]:
    """A small, invented career memory (every fact `claimed`). Returns the ids by short name."""
    from careeros.core.memory import ops

    quote = "stated by the user in the test"

    def add(kind: str, **fields: object) -> str:
        return ops.add_fact(root, kind, fields, quote=quote).id

    made: dict[str, str] = {}
    made["identity"] = add("identity", name="Jordan Example", headlines=["Backend Engineer", "Platform Engineer"])
    made["acme"] = add("experience", employer="Acme Corp", title="Senior Software Engineer", start="2025-01", end="present",
                       technologies=["Go", "Python", "Kubernetes", "AWS", "Kafka", "Terraform"])
    made["globex"] = add("experience", employer="Globex Systems Pvt Ltd", title="Software Engineer 2", start="2022-03",
                         end="2024-12", technologies=["PostgreSQL", "Prometheus", "Grafana"])
    made["k8s"] = add("achievement", parent=made["acme"], text="Led a Kubernetes migration for 40+ services, cutting deploy time by 60%.")
    made["costs"] = add("achievement", parent=made["acme"], text="Reduced AWS costs by 35% by moving batch jobs to Kafka-based pipelines.")
    made["platform"] = add("achievement", parent=made["acme"], text="Built an internal developer platform in Go used by 120 engineers.")
    made["pg"] = add("achievement", parent=made["globex"], text="Designed a multi-region PostgreSQL setup with 99.95% availability.")
    made["incident"] = add("achievement", parent=made["globex"],
                           text="Cut incident response time from 45 minutes to 12 minutes with Prometheus and Grafana alerts.")
    for name in ("Go", "Python", "SQL", "Terraform", "Kubernetes"):
        made[f"skill_{name.lower()}"] = add("skill", name=name)
    made["edu"] = add("education", school="Example Institute of Technology, Jabalpur", degree="B.Tech. in Computer Science",
                      end="2020-06", text="CGPA 8.1/10")
    made["cka"] = add("certification", name="Certified Kubernetes Administrator", year="2023")
    made["kubewatch"] = add("project", name="kubewatch", text="Open-source tool that streams Kubernetes events, used by 300+ teams.")
    return made


# --- write-scope checks -----------------------------------------------------------------

def state(root: Path) -> dict[str, bytes]:
    """`snapshot(root)` plus the ledger bytes under `ledger.jsonl` (empty when there is no ledger)."""
    out = snapshot(root)
    path = root / "ledger.jsonl"
    out["ledger.jsonl"] = path.read_bytes() if path.exists() else b""
    return out


def assert_only_ledger_appended(before: dict[str, bytes], after: dict[str, bytes], expected_types: list[str]) -> None:
    """Every file except `ledger.jsonl` is byte-identical, the old ledger bytes are an exact prefix of the new, and the
    appended events have exactly `expected_types`, in order. Both arguments come from `state(root)`."""
    import json

    assert {k: v for k, v in before.items() if k != "ledger.jsonl"} == {k: v for k, v in after.items() if k != "ledger.jsonl"}
    old, new = before["ledger.jsonl"], after["ledger.jsonl"]
    assert new.startswith(old), "the old ledger bytes are not a prefix of the new ledger"
    appended = [json.loads(line)["type"] for line in new[len(old):].splitlines()]
    assert appended == list(expected_types)


# --- shared by the outreach and approvals command tests (moved here from test_outreach_records / test_approvals_claims) ---

FMT = "%Y-%m-%dT%H:%M:%SZ"
T0 = "2026-10-06T10:00:00Z"
ALEX = "https://www.linkedin.com/in/alex-example"
SAM = "https://www.linkedin.com/in/sam-example"
KIM = "https://www.linkedin.com/in/kim-example"
EVID = "sponsor register entry (placeholder):2026-10-06"


def _cli_runner():
    from typer.testing import CliRunner
    return CliRunner()


def _cli_app():
    from careeros.cli.main import app
    return app


def _cli_util():
    from careeros.cli import _util
    return _util


def shift(ts: str, **delta) -> str:
    return (datetime.strptime(ts, FMT) + timedelta(**delta)).strftime(FMT)


def full_state(root: Path, lock: bool = False) -> dict[str, tuple[bytes | None, int | None]]:
    """EVERY path (files and directories) with bytes and mtime: the ledger and `.careeros/` included. A state-changing
    command takes the lock, which creates the empty `.careeros/lock` file, so refusal checks leave it out; the read-only
    checks pass `lock=True` and prove a read never creates or touches it."""
    out = {}
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root).as_posix()
        if rel == ".careeros/lock" and not lock:
            continue
        out[rel] = (p.read_bytes(), p.stat().st_mtime_ns) if p.is_file() else (None, None)
    return out


class OutreachW:
    """The workspace, ONE clock for the commands' `now` and the ledger's stamps, and the CLI."""

    def __init__(self, root: Path, mp: pytest.MonkeyPatch) -> None:
        self.root, self.now, self.mp = root, T0, mp
        mp.setattr(models, "utc_now", lambda: self.now)
        mp.setattr(_cli_util(), "is_interactive", lambda: False)

    def run(self, *args: str, input: str | None = None):
        return _cli_runner().invoke(_cli_app(), [*args, "--workspace", str(self.root)], input=input)

    def ok(self, *args: str):
        res = self.run(*args, "--json")
        assert res.exit_code == 0, res.output
        return json.loads(res.stdout)

    def refused(self, *args: str, code: str, exit_code: int = 1) -> dict:
        res = self.run(*args, "--json")
        assert res.exit_code == exit_code, res.output
        body = json.loads(res.stdout)
        assert body["code"] == code, body
        return body

    def events(self, type_: str | None = None) -> list[dict]:
        evs = ledger.read_events(self.root)
        return [e for e in evs if type_ is None or e["type"] == type_]

    def ledger_bytes(self) -> bytes:
        p = self.root / "ledger.jsonl"
        return p.read_bytes() if p.exists() else b""

    # --- commands
    def person(self, url=ALEX, *, name="Alex Example", company="Example Corp", role="recruiter", found_in=(), market=None,
               headline=None) -> dict:
        args = ["outreach", "person", "add", "--url", url, "--name", name, "--company", company, "--role", role]
        for f in found_in:
            args += ["--found-in", f]
        if market:
            args += ["--market", market]
        if headline:
            args += ["--headline", headline]
        return self.ok(*args)

    def target(self, slug="example-corp", *, company="Example Corp", market="Australia", fit=8, sponsorship="likely",
               evidence=(EVID,)) -> dict:
        args = ["outreach", "target", "add", "--company", company, "--slug", slug, "--market", market, "--fit", str(fit),
                "--sponsorship", sponsorship]
        for e in evidence:
            args += ["--evidence", e]
        return self.ok(*args)

    def approve_target(self, slug: str) -> dict:
        return self.ok("outreach", "target", "approve", slug, "--source", "user")

    def drop_target(self, slug: str, reason="not a fit") -> dict:
        return self.ok("outreach", "target", "drop", slug, "--source", "user", "--reason", reason)

    def propose(self, kind: str, url: str, *, expect: int = 0):
        f = self.root / "draft.txt"
        f.write_text("Hi there, a short invented note.\n", encoding="utf-8")
        res = self.run("approval", "propose", "--kind", kind, "--person-url", url, "--label", "Someone, Example Corp",
                       "--content-file", str(f), "--json")
        assert res.exit_code == expect, res.output
        return json.loads(res.stdout)

    def invite_done(self, url: str) -> tuple[str, str]:
        """propose, approve (interactive seam), begin, done: a consumed invite. Returns (person id, item id)."""
        inv = self.propose("linkedin_invite", url)
        self.mp.setattr(_cli_util(), "is_interactive", lambda: True)
        res = self.run("approve", inv["id"], input="a\n")
        self.mp.setattr(_cli_util(), "is_interactive", lambda: False)
        assert res.exit_code == 0, res.output
        self.ok("approval", "begin", inv["id"])
        self.ok("approval", "done", inv["id"])
        return inv["target"], inv["id"]

    def append(self, *specs: dict) -> None:
        ledger.append_events(self.root, list(specs))

    def outcome(self, per: str, kind: str, **extra) -> None:
        spec = {"type": f"outreach.{kind}", "actor": "user", "entity": per, "source": "user", "action": f"{kind} {per}"}
        if kind == "closed":
            spec["reason"] = "declined"
        self.append({**spec, **extra})


def rejected(w: OutreachW) -> list[dict]:
    return w.events("outreach.rejected")


class Probe:
    """A clock that returns a different value on every call, counts its calls and records whether the workspace lock was
    held by this thread at that moment (probed from another thread, which must time out on the lock)."""

    def __init__(self, root: Path, start: str = "2026-10-06T10:00:00Z") -> None:
        self.root, self.calls, self.held, self.t = root, 0, [], start

    def __call__(self) -> str:
        import threading
        from careeros.core.workspace import LockTimeout, WorkspaceLock
        result = []

        def try_lock():
            try:
                with WorkspaceLock(self.root, timeout=0.15):
                    result.append(False)
            except LockTimeout:
                result.append(True)
        t = threading.Thread(target=try_lock)
        t.start()
        t.join(5)
        self.held.append(result == [True])
        self.calls += 1
        self.t = shift(self.t, seconds=1)
        return self.t



CHILD = r"""
import os, sys, time
from careeros.cli import _util
from careeros.cli.main import app
if os.environ.get("CAREEROS_TEST_TTY"):
    _util.is_interactive = lambda: True        # the test plays the user's terminal
if os.environ.get("CAREEROS_TEST_WIDEN"):
    # widen the window between reading the ledger and appending, inside the command's critical section
    from careeros.core.approvals import fold
    real = fold.fold_events
    def slow(*a, **k):
        out = real(*a, **k)
        time.sleep(0.3)
        return out
    fold.fold_events = slow
ready, go = sys.argv[1], sys.argv[2]
open(ready, "w").close()
while not os.path.exists(go):
    time.sleep(0.001)
time.sleep(float(os.environ.get("CAREEROS_TEST_DELAY") or 0))
sys.argv = ["careeros", *sys.argv[3:]]
app()
"""


def Cmd(*args, tty=False, delay=0.0, input=""):
    return {"args": list(args), "tty": tty, "delay": delay, "input": input}


def race_many(tmp_path: Path, rounds: list[list[dict]], widen: bool) -> list[list[tuple[int, dict]]]:
    """Start one OS process per command of every round at once (each round on its own workspace), wait until every
    child is ready, release them together, and return (exit code, JSON output or raw text) per command per round."""
    script = tmp_path / "child.py"
    script.write_text(CHILD)
    stamp = time.monotonic_ns()
    go = tmp_path / f"go-{stamp}"
    procs = []
    for r, round_ in enumerate(rounds):
        for c, cmd in enumerate(round_):
            env = {**os.environ, "PYTHONPATH": os.pathsep.join(sys.path)}
            for key in ("CAREEROS_TEST_WIDEN", "CAREEROS_TEST_TTY", "CAREEROS_TEST_DELAY"):
                env.pop(key, None)
            if widen:
                env["CAREEROS_TEST_WIDEN"] = "1"
            if cmd["tty"]:
                env["CAREEROS_TEST_TTY"] = "1"
            if cmd["delay"]:
                env["CAREEROS_TEST_DELAY"] = str(cmd["delay"])
            stdin = tmp_path / f"in-{stamp}-{r}-{c}"
            stdin.write_text(cmd["input"])
            ready = tmp_path / f"ready-{stamp}-{r}-{c}"
            with stdin.open("rb") as handle:
                proc = subprocess.Popen([sys.executable, str(script), str(ready), str(go), *cmd["args"]], stdin=handle,
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
            procs.append((r, ready, proc))
    deadline = time.monotonic() + 120
    while not all(ready.exists() for _, ready, _ in procs):
        assert time.monotonic() < deadline, "children did not start"
        time.sleep(0.01)
    go.write_text("go")
    out: list[list[tuple[int, dict]]] = [[] for _ in rounds]
    for r, _, proc in procs:
        stdout, stderr = proc.communicate(timeout=120)
        try:
            data = json.loads(stdout)
        except ValueError:
            data = {"raw": stdout.decode() + stderr.decode()}
        out[r].append((proc.returncode, data))
    return out
