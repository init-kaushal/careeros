"""One end-to-end walk through the extended approvals on invented data, through the real CLI: an invite from proposal to
`done`, one invite per person (URL variants included), expiry at exactly 24 hours and no revival by a backward clock, a
submission with a manifest (changed file, supersession, in flight, the APPLIED gate, `resolve`), a voided item, escaped
drafts, a byte-identical workspace after the read-only commands, event counts, rejection dedupe, ledger chain, isolation,
the non-interactive refusals through real processes, and one real-process race.

`approve` and `resolve` need an interactive terminal; the walk plays it by patching `_util.is_interactive`, the same seam
the other approval tests use. The real terminal path is not exercised here."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from helpers import add_job, make_workspace, snapshot
from test_approvals_claims import Cmd, W, race_many, real_workspace
from typer.testing import CliRunner

from careeros.cli import _util
from careeros.cli.main import app
from careeros.core import ledger, models
from careeros.core.approvals import rules
from careeros.core.models import State

runner = CliRunner()

FMT = "%Y-%m-%dT%H:%M:%SZ"
T0 = "2026-10-06T10:00:00Z"
ALEX = "https://www.linkedin.com/in/alex-example"
ALEX_VARIANTS = [
    "https://www.linkedin.com/in/Alex-Example",
    "https://www.linkedin.com/in/alex-example/",
    "https://in.linkedin.com/in/alex-example",
    "https://www.linkedin.com/in/alex-example?trk=public",
]
SAM = "https://www.linkedin.com/in/sam-example"
KIM = "https://www.linkedin.com/in/kim-example"
RAW_BAD = ("\x1b", "\u202e", "\u200b")


def shift(ts: str, **delta) -> str:
    return (datetime.strptime(ts, FMT) + timedelta(**delta)).strftime(FMT)


class Walk:
    """The workspace, ONE clock for both the commands' `now` and the ledger's stamps, and a trace of every command."""

    def __init__(self, root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self.root, self.now, self.trace = root, T0, []
        self.mp = monkeypatch
        monkeypatch.setattr(models, "utc_now", lambda: self.now)

    def tty(self, on: bool) -> None:
        self.mp.setattr(_util, "is_interactive", lambda: on)

    def run(self, *args: str, input: str | None = None, expect: int | None = None):
        res = runner.invoke(app, [*args, "--workspace", str(self.root)], input=input)
        code = None
        if res.stdout.strip().startswith("{"):
            code = json.loads(res.stdout).get("code")
        found = re.findall(r"\[([a-z_0-9]+)\]\s*$", res.output.strip(), re.M)
        code = code or (found[-1] if found and res.exit_code else None)
        self.trace.append((" ".join(args[:3]), res.exit_code, code))
        if expect is not None:
            assert res.exit_code == expect, res.output
        return res, code

    def propose(self, kind: str, *, url: str | None = None, job: dict | None = None, label: str, content: str,
                manifest: tuple[str, ...] = (), expect: int = 0):
        f = self.root / "draft.txt"
        f.write_text(content, encoding="utf-8")
        args = ["approval", "propose", "--kind", kind, "--label", label, "--content-file", str(f), "--json"]
        args += ["--person-url", url] if url else ["--job", job["id"]]
        for m in manifest:
            args += ["--manifest", m]
        res, code = self.run(*args, expect=expect)
        return json.loads(res.stdout), code

    def decide(self, apr: str, answer: str = "a"):
        self.tty(True)
        res, _ = self.run("approve", apr, input=f"{answer}\n")
        self.tty(False)
        return res

    def state_of(self, apr: str) -> str:
        res, _ = self.run("approval", "show", apr, "--json", expect=0)
        return json.loads(res.stdout)["state"]

    def events(self, type_: str | None = None) -> list[dict]:
        evs = ledger.read_events(self.root)
        return [e for e in evs if type_ is None or e["type"] == type_]


def full_state(root: Path) -> dict[str, tuple[bytes | None, int | None]]:
    """EVERY path (files and directories) with bytes and mtime: the ledger and `.careeros/` included."""
    out = {}
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root).as_posix()
        out[rel] = (p.read_bytes(), p.stat().st_mtime_ns) if p.is_file() else (None, None)
    return out


def read_only_unchanged(w: Walk, *cmds: tuple[str, ...]) -> None:
    before = full_state(w.root)
    for cmd in cmds:
        res, _ = w.run(*cmd)
        assert res.exit_code == 0, (cmd, res.output)
        assert full_state(w.root) == before, f"{cmd} changed the workspace"


def make_submit_job(root: Path, slug: str) -> dict:
    job = add_job(root, slug, status=State.APPROVAL_REQUIRED, bullet="approval-required")
    folder = job["path"].parent
    (folder / "resume.pdf").write_bytes(b"%PDF resume " + slug.encode())
    (folder / "cover.pdf").write_bytes(b"%PDF cover " + slug.encode())
    (folder / "jd.md").write_text("# Invented posting\n", encoding="utf-8")
    job["resume"] = f"jobs/discovered/{slug}/resume.pdf"
    job["cover"] = f"jobs/discovered/{slug}/cover.pdf"
    return job


def test_extended_approvals_end_to_end(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CAREEROS_WORKSPACE", raising=False)
    root = make_workspace(tmp_path / "ws")
    w = Walk(root, monkeypatch)                                          # the clock is patched before any event is stamped
    w.tty(False)
    job = make_submit_job(root, "example-platform")
    other = add_job(root, "other-job")                                   # a different job, never touched
    (other["path"].parent / "jd.md").write_text("# Another invented posting\n", encoding="utf-8")
    (root / "career").mkdir()
    (root / "career" / "facts.md").write_text("# Invented career memory\n", encoding="utf-8")
    guarded = {k: v for k, v in snapshot(root).items() if k.startswith("career/") or "other-job" in k}
    guarded["example-platform/jd.md"] = (job["path"].parent / "jd.md").read_bytes()

    # (a) an invite: propose, approve (interactive seam), begin, done; one invite per person, URL variants included
    inv, _ = w.propose("linkedin_invite", url=ALEX, label="Alex Example, Example Corp", content="Hi Alex, small world.\n")
    apr, person = inv["id"], inv["target"]
    assert person.startswith("per_") and w.state_of(apr) == "pending"
    res = w.decide(apr)
    assert res.exit_code == 0 and "Approved" in res.output and w.state_of(apr) == "approved"
    res, _ = w.run("approval", "begin", apr, "--json", expect=0)
    assert json.loads(res.stdout)["state"] == "in_flight"
    w.run("approval", "done", apr, "--note", "confirmation seen", "--json", expect=0)
    assert w.state_of(apr) == "consumed"
    for variant in [ALEX, *ALEX_VARIANTS]:
        refusal, code = w.propose("linkedin_invite", url=variant, label="Alex Example, Example Corp",
                                  content="Hi again.\n", expect=1)
        assert code == "invite_conflict" and refusal["code"] == "invite_conflict"
    msg1, _ = w.propose("linkedin_message", url=ALEX_VARIANTS[2], label="Alex Example, Example Corp", content="Thanks!\n")
    assert msg1["target"] == person                                      # one per_ id for every spelling

    # (b) expiry at exactly 24 h, no revival by a backward clock
    sam, _ = w.propose("linkedin_invite", url=SAM, label="Sam Example, Example Corp", content="Hi Sam.\n")
    w.decide(sam["id"])                                                  # approved_at = T0
    w.now = shift(T0, hours=24)
    _, code = w.run("approval", "begin", sam["id"], "--json", expect=1)
    assert code == "expired"
    rejected = len(w.events(rules.EV_REJECTED))
    _, code = w.run("approval", "begin", sam["id"], "--json", expect=1)  # the identical refusal is not recorded twice
    assert code == "expired" and len(w.events(rules.EV_REJECTED)) == rejected
    assert w.state_of(sam["id"]) == "expired"
    w.now = shift(T0, hours=1)
    _, code = w.run("approval", "begin", sam["id"], "--json", expect=1)
    assert code == "clock_skew" and w.state_of(sam["id"]) == "approved"  # derived at the earlier time; nothing revived
    _, code = w.run("approval", "propose", "--kind", "linkedin_invite", "--person-url", KIM, "--label", "Kim Example",
                    "--content-file", str(root / "draft.txt"), expect=1)  # state changes refuse the skewed clock too
    assert code == "clock_skew"
    w.now = shift(T0, hours=25)
    assert w.state_of(sam["id"]) == "expired"

    # (c) a submission with a manifest
    manifest = (job["resume"], job["cover"])
    s1, _ = w.propose("application_submit", job=job, label="Example job", content="Answers: 5 years of Go.\n", manifest=manifest)
    w.decide(s1["id"])
    assert w.state_of(s1["id"]) == "approved" and w.events("application.approved")      # the job-level approval is recorded too
    (root / job["resume"]).write_bytes(b"%PDF resume, edited")
    _, code = w.run("approval", "begin", s1["id"], "--json", expect=1)
    assert code == "manifest_changed"
    s2, _ = w.propose("application_submit", job=job, label="Example job", content="Answers: 5 years of Go.\n", manifest=manifest)
    assert s2["superseded"] == [s1["id"]] and w.state_of(s1["id"]) == "superseded"
    w.decide(s2["id"])
    w.run("approval", "begin", s2["id"], "--json", expect=0)
    assert w.state_of(s2["id"]) == "in_flight"                           # the agent is interrupted here: no `done`
    _, code = w.propose("application_submit", job=job, label="Example job", content="Again.\n", manifest=manifest, expect=1)
    assert code == "in_flight_conflict"
    read_only_unchanged(w, ("validate",), ("approval", "list"), ("approval", "list", "--json"), ("approval", "show", s2["id"]))
    _, code = w.run("transition", job["id"], "--to", "APPLIED", expect=1)
    assert code == "submission_unresolved"
    w.tty(True)
    res, _ = w.run("approval", "resolve", s2["id"], "--sent", "--json", expect=0)
    w.tty(False)
    assert json.loads(res.stdout)["outcome"] == "consumed" and w.state_of(s2["id"]) == "consumed"
    w.run("transition", job["id"], "--to", "APPLIED", expect=0)
    assert w.state_of(s2["id"]) == "consumed"

    # (d) `resolve --not-sent` with a changed manifest voids the item
    third = make_submit_job(root, "third-platform")
    s3, _ = w.propose("application_submit", job=third, label="Third job", content="Answers.\n",
                      manifest=(third["resume"], third["cover"]))
    w.decide(s3["id"])
    w.run("approval", "begin", s3["id"], "--json", expect=0)
    (root / third["resume"]).write_bytes(b"%PDF resume, changed after approval")
    w.tty(True)
    res, _ = w.run("approval", "resolve", s3["id"], "--not-sent", "--json", expect=0)
    w.tty(False)
    assert json.loads(res.stdout)["outcome"] == "voided" and w.state_of(s3["id"]) == "voided"
    _, code = w.run("transition", third["id"], "--to", "APPLIED", expect=1)
    assert code == "submission_not_consumed"

    # (e) a draft with terminal escapes, bidi and zero-width characters is shown escaped
    evil = "Hi \x1b[31mred\x1b[0m \u202e rtl \u200b zero\n"
    msg2, _ = w.propose("linkedin_message", url=KIM, label="Kim Example", content=evil)
    shown, _ = w.run("approval", "show", msg2["id"], expect=0)
    assert not any(c in shown.output for c in RAW_BAD) and "\\x1b" in shown.output
    assert "\\u202e" in shown.output and "\\u200b" in shown.output
    w.tty(True)
    queue, _ = w.run("approve", input="d\nd\n", expect=0)                  # the queue: msg1 and msg2, both declined
    w.tty(False)
    assert not any(c in queue.output for c in RAW_BAD) and "\\u202e" in queue.output
    assert "0 approved, 2 declined, 0 skipped" in queue.output
    assert w.state_of(msg1["id"]) == w.state_of(msg2["id"]) == "declined"

    # (f) the read-only commands leave the WHOLE workspace byte-identical (ledger and .careeros/ included)
    files = sorted(full_state(root))
    read_only_unchanged(w, ("validate",), ("validate", "--json"), ("approval", "list"), ("approval", "list", "--json"),
                        ("approval", "show", s3["id"]), ("approval", "show", msg2["id"], "--json"))
    assert sorted(full_state(root)) == files

    # (g) events per type, deduped rejections, an intact chain
    counts = Counter(e["type"] for e in w.events() if e["type"].startswith("approval."))
    assert counts == {
        "approval.proposed": 7, "approval.approved": 5, "approval.declined": 2, "approval.superseded": 1,
        "approval.started": 3, "approval.consumed": 2, "approval.voided": 1, "approval.rejected": 3,
    }, counts
    rejects = [(e["entity"], e.get("reason")) for e in w.events(rules.EV_REJECTED)]
    assert len(rejects) == len(set(rejects))                              # no identical repeat was recorded
    assert sorted(re.match(r"[a-z_]+", r or "").group() for _, r in rejects) == ["clock_skew", "expired", "manifest_changed"]
    assert not ledger.verify_chain(root)

    # (h) isolation: career/, the other job and every jd.md are untouched
    now_guarded = {k: v for k, v in snapshot(root).items() if k.startswith("career/") or "other-job" in k}
    now_guarded["example-platform/jd.md"] = (job["path"].parent / "jd.md").read_bytes()
    assert now_guarded == guarded
    assert not [e for e in w.events() if e.get("entity") == other["id"] and e["type"] != "job.imported"]


def run_real(root: Path, *args: str, stdin=subprocess.DEVNULL) -> subprocess.CompletedProcess:
    code = "from careeros.cli.main import app; app()"
    return subprocess.run([sys.executable, "-c", code, *args, *W(root)], stdin=stdin, capture_output=True, text=True,
                          timeout=60)


def test_approve_and_resolve_refuse_without_a_terminal_in_a_real_process(tmp_path: Path) -> None:
    root, _ = real_workspace(tmp_path / "ws")
    from test_approvals_claims import P, real_decide
    got = P(root)
    for stdin in (subprocess.DEVNULL, subprocess.PIPE):
        if stdin == subprocess.PIPE:
            proc = subprocess.run([sys.executable, "-c", "from careeros.cli.main import app; app()", "approve", got.id,
                                   *W(root)], input="a\n", capture_output=True, text=True, timeout=60)
        else:
            proc = run_real(root, "approve", got.id)
        assert proc.returncode == 2, proc.stderr
        assert "not_interactive" in proc.stderr or "interactive terminal" in proc.stderr
    assert json.loads(run_real(root, "approval", "show", got.id, "--json").stdout)["state"] == "pending"
    real_decide(root, got.id)
    run_real(root, "approval", "begin", got.id, "--json")
    proc = run_real(root, "approval", "resolve", got.id, "--sent", "--json")
    assert proc.returncode == 2, proc.stdout
    assert json.loads(run_real(root, "approval", "show", got.id, "--json").stdout)["state"] == "in_flight"
    assert not [e for e in ledger.read_events(root) if e["type"] == rules.EV_APPROVED and e["entity"] == got.id and e["source"] != "tty"]
    assert not ledger.verify_chain(root)


def test_a_real_process_race_has_exactly_one_start(tmp_path: Path) -> None:
    root, _ = real_workspace(tmp_path / "ws")
    from test_approvals_claims import P, real_decide
    got = P(root)
    real_decide(root, got.id)
    (results,) = race_many(tmp_path, [[Cmd("approval", "begin", got.id, "--json", *W(root))] * 2], widen=True)
    assert sorted(code for code, _ in results) == [0, 1]
    assert [d.get("code") for code, d in results if code] == ["wrong_state"]
    assert len([e for e in ledger.read_events(root) if e["type"] == rules.EV_STARTED]) == 1
    assert not ledger.verify_chain(root)
