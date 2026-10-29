"""Outreach records, commands and identity unification (sub-project 4a, Task C), through the real CLI on invented data.

Covers: `person add|link|list|show` and `target add|approve|drop|list|show` (file bytes, event shape, ids, `--json`, exit
codes), one `per_` id per canonical URL across `person add` and `approval propose` in both orders, the identity-conflict
refusal, file-first/event-second, the deduped audit-only `outreach.rejected`, the reserved prefix, the record-editing
table of spec 6.0 (which outputs a hand edit may change and which it never does), the refusal audit, and the `OUT`
validation codes. Time comes from ONE source: `models.utc_now` is patched and the same value is the commands' `now`.

`approve` needs an interactive terminal; the walk patches `_util.is_interactive`, the seam the approvals tests use."""

from __future__ import annotations

import dataclasses
import json
import re
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from helpers import add_job, make_workspace
from typer.testing import CliRunner

from careeros.cli import _util
from careeros.cli.main import app
from careeros.core import ids, ledger, models
from careeros.core.approvals import cap
from careeros.core.approvals import people as apeople
from careeros.core.ledger import LedgerError
from careeros.core.outreach import derive, rules, service
from careeros.core.outreach import person as person_mod
from careeros.core.outreach import target as target_mod
from careeros.core.outreach.model import Evidence, FoundIn, Person, Target

runner = CliRunner()
FMT = "%Y-%m-%dT%H:%M:%SZ"
T0 = "2026-10-06T10:00:00Z"
ALEX = "https://www.linkedin.com/in/alex-example"
ALEX_VARIANTS = [
    "https://www.linkedin.com/in/Alex-Example",
    "https://www.linkedin.com/in/alex-example/",
    "https://in.linkedin.com/in/alex-example",
    "https://www.linkedin.com/in/alex-example?trk=public",
    "http://www.linkedin.com/in/alex-example",
]
SAM = "https://www.linkedin.com/in/sam-example"
KIM = "https://www.linkedin.com/in/kim-example"
PAT = "https://www.linkedin.com/in/pat-example"
LEE = "https://www.linkedin.com/in/lee-example"
RAN = "https://www.linkedin.com/in/ran-example"
EVID = "sponsor register entry (placeholder):2026-10-06"


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


class W:
    """The workspace, ONE clock for the commands' `now` and the ledger's stamps, and the CLI."""

    def __init__(self, root: Path, mp: pytest.MonkeyPatch) -> None:
        self.root, self.now, self.mp = root, T0, mp
        mp.setattr(models, "utc_now", lambda: self.now)
        mp.setattr(_util, "is_interactive", lambda: False)

    def run(self, *args: str, input: str | None = None):
        return runner.invoke(app, [*args, "--workspace", str(self.root)], input=input)

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
        self.mp.setattr(_util, "is_interactive", lambda: True)
        res = self.run("approve", inv["id"], input="a\n")
        self.mp.setattr(_util, "is_interactive", lambda: False)
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


@pytest.fixture
def w(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> W:
    monkeypatch.delenv("CAREEROS_WORKSPACE", raising=False)
    return W(make_workspace(tmp_path / "ws"), monkeypatch)


def rejected(w: W) -> list[dict]:
    return w.events("outreach.rejected")


def read_person(root: Path, per_id: str) -> Person:
    rec, issues = person_mod.parse((root / "people" / f"{per_id}.md").read_text(encoding="utf-8"), f"people/{per_id}.md")
    assert rec is not None, issues
    return rec


def read_target(root: Path, slug: str) -> Target:
    rec, issues = target_mod.parse((root / "targets" / f"{slug}.md").read_text(encoding="utf-8"), f"targets/{slug}.md")
    assert rec is not None, issues
    return rec


def out_codes(w: W, now: str | None = None, *, code: str | None = None) -> list[tuple[str, str, str]]:
    issues = service.validate_workspace(w.root, w.events(), now or w.now)
    return sorted((i.code, i.severity, i.path) for i in issues if code is None or i.code == code)


def sub_text(root: Path, rel: str, old: str, new: str) -> None:
    p = root / rel
    text = p.read_text(encoding="utf-8")
    assert old in text, (old, text)
    p.write_text(text.replace(old, new, 1), encoding="utf-8")


def hand_edit_person(root: Path, per_id: str, **changes) -> None:
    rel = f"people/{per_id}.md"
    text = (root / rel).read_text(encoding="utf-8")
    from careeros.core.workspace import split_frontmatter
    _, body = split_frontmatter(text)
    (root / rel).write_bytes(person_mod.to_bytes(dataclasses.replace(read_person(root, per_id), **changes), body))


def hand_edit_target(root: Path, slug: str, **changes) -> None:
    rel = f"targets/{slug}.md"
    text = (root / rel).read_text(encoding="utf-8")
    from careeros.core.workspace import split_frontmatter
    _, body = split_frontmatter(text)
    (root / rel).write_bytes(target_mod.to_bytes(dataclasses.replace(read_target(root, slug), **changes), body))


# =========================================================================================================================
# person add
# =========================================================================================================================

def test_person_add_writes_the_file_then_the_event(w: W) -> None:
    out = w.person(found_in=(), headline="Recruiter, Platform", market="Australia")
    per = out["id"]
    assert ids.is_valid_id("person", per) and out["path"] == f"people/{per}.md" and out["outcome"] == "added"
    data = (w.root / out["path"]).read_bytes()
    expected = person_mod.to_bytes(Person(per, ALEX, "Alex Example", "Recruiter, Platform", "Example Corp", "example-corp",
                                          "recruiter", "Australia", (), T0[:10]))
    assert data == expected
    (ev,) = w.events("outreach.person_added")
    import hashlib
    assert out["hash"] == hashlib.sha256(data).hexdigest()
    assert ev["entity"] == per and ev["action"] == "add person" and ev["source"] == "cli" and ev["actor"] == "agent:cli"
    assert ev["artifacts"] == [out["path"], f"sha256:{out['hash']}", f"url:{ALEX}"]
    assert [e["type"] for e in w.events()] == ["outreach.person_added"]
    assert apeople.person_urls(w.events()) == ({ALEX: per}, [])


def test_person_add_text_output_and_defaults(w: W) -> None:
    res = w.run("outreach", "person", "add", "--url", ALEX, "--name", "Alex Example", "--company", "Example Corp", "--role", "peer")
    assert res.exit_code == 0 and "added per_" in res.output
    rec = read_person(w.root, ids_of(w)[0])
    assert rec.headline == "" and rec.market is None and rec.found_at == "2026-10-06"


def ids_of(w: W) -> list[str]:
    return [e["entity"] for e in w.events("outreach.person_added")]


@pytest.mark.parametrize("variant", ALEX_VARIANTS)
def test_url_variants_are_one_person_add_then_propose(w: W, variant: str) -> None:
    per = w.person(ALEX)["id"]
    inv = w.propose("linkedin_invite", variant)
    assert inv["target"] == per
    dup = w.refused("outreach", "person", "add", "--url", variant, "--name", "A", "--company", "C", "--role", "peer", code="duplicate")
    assert per in dup["error"]
    assert len(list((w.root / "people").glob("*.md"))) == 1


@pytest.mark.parametrize("variant", ALEX_VARIANTS)
def test_url_variants_are_one_person_propose_then_add(w: W, variant: str) -> None:
    inv = w.propose("linkedin_invite", ALEX)
    out = w.person(variant)
    assert out["id"] == inv["target"] and out["reused_id"] is True
    assert read_person(w.root, out["id"]).url == ALEX            # the canonical form is stored, not the spelling given
    assert apeople.person_urls(w.events()) == ({ALEX: inv["target"]}, [])
    again = w.propose("linkedin_message", variant)
    assert again["target"] == inv["target"]


def test_a_new_url_gets_a_new_id_and_a_reused_one_is_marked(w: W) -> None:
    a, b = w.person(ALEX), w.person(SAM, name="Sam Example")
    assert a["id"] != b["id"] and a["reused_id"] is False and b["reused_id"] is False
    assert w.propose("linkedin_invite", SAM)["target"] == b["id"]


def test_person_add_found_in_job_and_target(w: W) -> None:
    job = add_job(w.root, "beta-job", company="Beta Inc")
    tgt = w.target()
    out = w.person(found_in=("target:example-corp", f"job:{job['id']}", "job:beta-job"))
    rec = read_person(w.root, out["id"])
    assert rec.found_in == (FoundIn("target", tgt["id"]), FoundIn("job", job["id"]))      # a repeated job ref is one entry


@pytest.mark.parametrize("spec", ["job:no-such-job", "target:no-such-target", "nonsense", "job:", "planet:earth", ":x"])
def test_person_add_found_in_must_exist(w: W, spec: str) -> None:
    before = full_state(w.root)
    w.refused("outreach", "person", "add", "--url", ALEX, "--name", "A", "--company", "C", "--role", "peer", "--found-in", spec,
              code="invalid_input")
    assert full_state(w.root) == before                 # no file, no event: the person was not known


def test_an_orphan_target_file_is_not_a_target_for_found_in(w: W) -> None:
    (w.root / "targets").mkdir()
    (w.root / "targets" / "ghost-co.md").write_bytes(target_mod.to_bytes(
        Target(ids.new_id("target"), "ghost-co", "Ghost Co", "Australia", 5, "n/a")))
    w.refused("outreach", "person", "add", "--url", ALEX, "--name", "A", "--company", "C", "--role", "peer",
              "--found-in", "target:ghost-co", code="invalid_input")


@pytest.mark.parametrize("kwargs,why", [
    ({"role": "boss"}, "role"), ({"name": "   "}, "name"), ({"company": "日本語"}, "company_slug"),
    ({"name": "Alex\u202eExample"}, "bidi"), ({"headline": "x" * 600}, "headline"),
])
def test_person_add_invalid_input_writes_nothing(w: W, kwargs: dict, why: str) -> None:
    before = full_state(w.root)
    args = {"name": "Alex Example", "company": "Example Corp", "role": "recruiter", **kwargs}
    argv = ["outreach", "person", "add", "--url", ALEX, "--name", args["name"], "--company", args["company"], "--role", args["role"]]
    if "headline" in args:
        argv += ["--headline", args["headline"]]
    w.refused(*argv, code="invalid_input")
    assert full_state(w.root) == before


def test_person_add_not_a_profile_url(w: W) -> None:
    w.refused("outreach", "person", "add", "--url", "https://example.com/x", "--name", "A", "--company", "C", "--role", "peer",
              code="invalid_input")
    assert w.events() == []


def test_person_add_json_is_one_object_and_usage_exit_codes(w: W) -> None:
    res = w.run("outreach", "person", "add", "--url", ALEX, "--name", "A", "--company", "Example Corp", "--role", "peer", "--json")
    assert res.exit_code == 0 and set(json.loads(res.stdout)) == {"outcome", "id", "path", "hash", "url", "reused_id"}
    missing = w.run("outreach", "person", "add", "--url", SAM)                       # required options missing: usage
    assert missing.exit_code == 2
    nows = runner.invoke(app, ["outreach", "person", "list", "--workspace", str(w.root / "nowhere")])
    assert nows.exit_code == 2


# =========================================================================================================================
# person link, list, show
# =========================================================================================================================

def test_person_link_changes_only_found_in_and_appends_no_event(w: W) -> None:
    tgt = w.target()
    per = w.person(market="Australia")["id"]
    job = add_job(w.root, "beta-job", company="Beta Inc")
    # the user's notes below the frontmatter survive
    p = w.root / "people" / f"{per}.md"
    p.write_text(p.read_text(encoding="utf-8") + "\nMet at a meetup (invented).\n", encoding="utf-8")
    before_rec, before_ledger = read_person(w.root, per), w.ledger_bytes()
    out = w.ok("outreach", "person", "link", per, "--found-in", "target:example-corp", "--found-in", "job:beta-job")
    assert out["outcome"] == "linked" and out["found_in"] == [{"kind": "target", "id": tgt["id"]}, {"kind": "job", "id": job["id"]}]
    after = read_person(w.root, per)
    assert after == dataclasses.replace(before_rec, found_in=(FoundIn("target", tgt["id"]), FoundIn("job", job["id"])))
    assert w.ledger_bytes() == before_ledger and "Met at a meetup (invented)." in p.read_text(encoding="utf-8")
    w.ok("outreach", "person", "link", per, "--found-in", "target:example-corp")           # adding the same entry again: no duplicate
    assert read_person(w.root, per).found_in == after.found_in
    assert w.ledger_bytes() == before_ledger


def test_person_link_refusals(w: W) -> None:
    per = w.person()["id"]
    w.refused("outreach", "person", "link", per, "--found-in", "job:nope", code="invalid_input")
    w.refused("outreach", "person", "link", per, "--found-in", "oops", code="invalid_input")
    w.refused("outreach", "person", "link", ids.new_id("person"), "--found-in", "job:nope", code="not_found")
    w.refused("outreach", "person", "link", "not-an-id", "--found-in", "job:nope", code="invalid_input")
    # an unreadable record is refused and left exactly as it is
    sub_text(w.root, f"people/{per}.md", "role: recruiter", "role: boss")
    bytes_before = (w.root / "people" / f"{per}.md").read_bytes()
    add_job(w.root, "beta-job", company="Beta Inc")
    w.refused("outreach", "person", "link", per, "--found-in", "job:beta-job", code="invalid_input")
    assert (w.root / "people" / f"{per}.md").read_bytes() == bytes_before


def test_person_list_and_show(w: W) -> None:
    a, b = w.person(ALEX)["id"], w.person(SAM, name="Sam Example", role="peer")["id"]
    listing = w.ok("outreach", "person", "list")
    assert [p["id"] for p in listing["people"]] == sorted([a, b]) and all(p["label"] == "discovered" for p in listing["people"])
    shown = w.ok("outreach", "person", "show", a)
    assert shown["record"]["url"] == ALEX and shown["label"] == "discovered" and shown["problems"] == []
    assert w.refused("outreach", "person", "show", ids.new_id("person"), code="not_found")
    assert "discovered" in w.run("outreach", "person", "list").output and a in w.run("outreach", "person", "show", a).output


def test_person_show_reports_state_from_the_ledger(w: W) -> None:
    per, _ = w.invite_done(ALEX)
    w.person(ALEX, found_in=())
    shown = w.ok("outreach", "person", "show", per)
    assert shown["label"] == "invited" and shown["state"]["invited_at"] == T0
    w.outcome(per, "accepted")
    assert w.ok("outreach", "person", "show", per)["label"] == "connected"


def test_a_missing_or_invalid_record_is_reported_not_repaired(w: W) -> None:
    a, b = w.person(ALEX)["id"], w.person(SAM, name="Sam Example")["id"]
    (w.root / "people" / f"{a}.md").unlink()
    sub_text(w.root, f"people/{b}.md", "role: recruiter", "role: boss")
    before = full_state(w.root, lock=True)
    listing = {p["id"]: p for p in w.ok("outreach", "person", "list")["people"]}
    assert listing[a]["record"] is None and "missing" in listing[a]["problems"][0]
    assert listing[b]["record"] is None and "role" in listing[b]["problems"][0]
    w.ok("outreach", "person", "show", a)
    w.ok("outreach", "person", "show", b)
    assert full_state(w.root, lock=True) == before


# =========================================================================================================================
# target add, decisions, list, show
# =========================================================================================================================

def test_target_add_writes_the_file_then_the_event(w: W) -> None:
    out = w.target(evidence=(EVID, "regional board posting:2026-10-05"))
    tgt = out["id"]
    assert tgt.startswith("tgt_") and ids.is_valid_id("target", tgt) and out["path"] == "targets/example-corp.md"
    data = (w.root / out["path"]).read_bytes()
    assert data == target_mod.to_bytes(Target(tgt, "example-corp", "Example Corp", "Australia", 8, "likely",
                                              (Evidence("sponsor register entry (placeholder)", "2026-10-06"),
                                               Evidence("regional board posting", "2026-10-05"))))
    (ev,) = w.events("outreach.target_added")
    import hashlib
    assert ev["entity"] == tgt and ev["action"] == "add target example-corp" and ev["source"] == "cli"
    assert ev["artifacts"] == [out["path"], f"sha256:{hashlib.sha256(data).hexdigest()}"]
    assert out["slug"] == "example-corp" and out["outcome"] == "added"
    view = service.target_show(w.root, "example-corp", w.now)
    assert (view.decision, view.status, view.stale) == ("candidate", "candidate", False)


def test_target_add_home_market_needs_no_evidence_and_text_output(w: W) -> None:
    res = w.run("outreach", "target", "add", "--company", "Home Co", "--slug", "home-co", "--market", "India (home)", "--fit", "9",
                "--sponsorship", "n/a")
    assert res.exit_code == 0 and "added tgt_" in res.output
    assert read_target(w.root, "home-co").evidence == ()


@pytest.mark.parametrize("sponsorship", ["offered", "likely", "unknown", "not_offered"])
def test_target_add_evidence_rule(w: W, sponsorship: str) -> None:
    before = full_state(w.root)
    w.refused("outreach", "target", "add", "--company", "X Co", "--slug", "x-co", "--market", "Australia", "--fit", "5",
              "--sponsorship", sponsorship, code="invalid_input")
    assert full_state(w.root) == before
    w.target("x-co", company="X Co", sponsorship=sponsorship, evidence=("sponsor register:2026-10-05",))


@pytest.mark.parametrize("evidence", ["no-date-here", "source:2026-13-40", "source:", ":2026-10-05", "source:2026-10-05T10:00:00"])
def test_target_add_bad_evidence(w: W, evidence: str) -> None:
    w.refused("outreach", "target", "add", "--company", "X Co", "--slug", "x-co", "--market", "Australia", "--fit", "5",
              "--sponsorship", "likely", "--evidence", evidence, code="invalid_input")
    assert w.events() == [] and not (w.root / "targets").exists()


def test_evidence_source_may_contain_colons(w: W) -> None:
    w.target("x-co", company="X Co", evidence=("register: entry 7 (placeholder):2026-10-05",))
    assert read_target(w.root, "x-co").evidence[0].source == "register: entry 7 (placeholder)"


@pytest.mark.parametrize("slug", ["Example Corp", "example--corp", "-x", "", "x_y", "日本"])
def test_target_add_slug_must_be_in_slugify_form(w: W, slug: str) -> None:
    w.refused("outreach", "target", "add", "--company", "X Co", "--slug", slug, "--market", "Australia", "--fit", "5",
              "--sponsorship", "n/a", code="invalid_input")
    assert w.events() == []


@pytest.mark.parametrize("fit", ["11", "-1"])
def test_target_add_fit_range(w: W, fit: str) -> None:
    res = w.run("outreach", "target", "add", "--company", "X Co", "--slug", "x-co", "--market", "Australia", "--fit", fit,
                "--sponsorship", "n/a", "--json")
    assert res.exit_code == 1 and json.loads(res.stdout)["code"] == "invalid_input"
    assert w.events() == []


def test_duplicate_slug_including_a_dropped_one(w: W) -> None:
    first = w.target()
    again = w.refused("outreach", "target", "add", "--company", "Other", "--slug", "example-corp", "--market", "Australia",
                      "--fit", "3", "--sponsorship", "n/a", code="duplicate")
    assert first["id"] in again["error"]
    w.drop_target("example-corp")
    gone = w.refused("outreach", "target", "add", "--company", "Example Corp", "--slug", "example-corp", "--market", "Australia",
                     "--fit", "3", "--sponsorship", "n/a", code="duplicate")
    assert "dropped" in gone["error"]
    # a dropped target whose file was deleted still blocks its slug (the event is the authority)
    (w.root / "targets" / "example-corp.md").unlink()
    w.refused("outreach", "target", "add", "--company", "Example Corp", "--slug", "example-corp", "--market", "Australia",
              "--fit", "3", "--sponsorship", "n/a", code="duplicate")
    assert len(w.events("outreach.target_added")) == 1


@pytest.mark.parametrize("start,op,outcome", [
    ("candidate", "approve", "approved"), ("candidate", "drop", "dropped"), ("approved", "drop", "dropped"),
    ("approved", "approve", "wrong_state"), ("dropped", "approve", "wrong_state"), ("dropped", "drop", "wrong_state"),
])
def test_target_decision_grid(w: W, start: str, op: str, outcome: str) -> None:
    tgt = w.target()["id"]
    if start in ("approved", "dropped"):
        w.approve_target("example-corp")
    if start == "dropped":
        w.drop_target("example-corp")
    n = len(w.events())
    args = ["outreach", "target", op, "example-corp", "--source", "user", "--reason", "price too low" if op == "drop" else "ok"]
    if outcome == "wrong_state":
        body = w.refused(*args, code="wrong_state")
        assert start in body["error"]
        assert [e["type"] for e in w.events()[n:]] == ["outreach.rejected"]
        return
    res = w.ok(*args)
    assert res["decision"] == outcome and res["id"] == tgt and res["slug"] == "example-corp"
    (ev,) = w.events()[n:]
    assert ev["type"] == f"outreach.target_{outcome}" and ev["entity"] == tgt and ev["source"] == "user"
    assert (ev["prev_state"], ev["new_state"]) == (start, outcome)
    assert ev.get("reason") == ("price too low" if op == "drop" else "ok") or op == "approve"


def test_approve_may_omit_the_reason_and_records_none(w: W) -> None:
    w.target()
    w.ok("outreach", "target", "approve", "example-corp")
    (ev,) = w.events("outreach.target_approved")
    assert ev["source"] == "user" and ev["reason"] is None
    assert service.target_show(w.root, "example-corp", w.now).status == "approved"


def test_drop_needs_a_reason_and_a_valid_source(w: W) -> None:
    w.target()
    n = len(w.events())
    w.refused("outreach", "target", "drop", "example-corp", code="invalid_input")
    w.refused("outreach", "target", "drop", "example-corp", "--reason", "   ", code="invalid_input")
    w.refused("outreach", "target", "approve", "example-corp", "--source", "carrier-pigeon", code="invalid_input")
    # a blank reason is the same refusal as a missing one, so the second is not recorded again
    assert [e["type"] for e in w.events()[n:]] == ["outreach.rejected"] * 2
    assert service.target_show(w.root, "example-corp", w.now).decision == "candidate"


def test_unknown_target_appends_nothing(w: W) -> None:
    w.target()
    before = full_state(w.root)
    w.refused("outreach", "target", "approve", "no-such", code="not_found")
    w.refused("outreach", "target", "drop", "no-such", "--reason", "x", code="not_found")
    w.refused("outreach", "target", "show", "no-such", code="not_found")
    assert full_state(w.root) == before


def test_target_list_and_show(w: W) -> None:
    a, b = w.target("a-co", company="A Co", fit=9), w.target("b-co", company="B Co", fit=4, sponsorship="n/a", evidence=())
    w.approve_target("a-co")
    listing = w.ok("outreach", "target", "list")["targets"]
    assert [(t["slug"], t["status"], t["stale"]) for t in listing] == [("a-co", "approved", False), ("b-co", "candidate", False)]
    shown = w.ok("outreach", "target", "show", "a-co")
    assert shown["id"] == a["id"] and shown["record"]["fit"] == 9 and shown["decision"] == "approved" and shown["problems"] == []
    assert "approved" in w.run("outreach", "target", "list").output and "a-co" in w.run("outreach", "target", "show", "a-co").output
    assert b["slug"] == "b-co"


def test_target_status_follows_linked_people(w: W) -> None:
    w.target()
    w.approve_target("example-corp")
    per = w.person(found_in=("target:example-corp",))["id"]
    assert service.target_show(w.root, "example-corp", w.now).people == (per,)
    per2, _ = w.invite_done(SAM)
    w.person(SAM, name="Sam", found_in=("target:example-corp",))
    assert service.target_show(w.root, "example-corp", w.now).status == "contacted"
    w.outcome(per2, "accepted")
    assert service.target_show(w.root, "example-corp", w.now).status == "warm"


# =========================================================================================================================
# file first, event second
# =========================================================================================================================

def test_a_failing_append_leaves_no_file_and_no_event(w: W, monkeypatch: pytest.MonkeyPatch) -> None:
    w.target("seed-co", company="Seed Co")
    before = {k: v[0] for k, v in full_state(w.root).items()}
    real = ledger.append_events

    def boom(*a, **k):
        raise OSError("disk full (invented)")

    monkeypatch.setattr(ledger, "append_events", boom)
    res = w.run("outreach", "person", "add", "--url", ALEX, "--name", "A", "--company", "C", "--role", "peer", "--json")
    assert res.exit_code == 1
    res = w.run("outreach", "target", "add", "--company", "X Co", "--slug", "x-co", "--market", "Australia", "--fit", "5",
                "--sponsorship", "n/a", "--json")
    assert res.exit_code == 1
    monkeypatch.setattr(ledger, "append_events", real)
    assert not (w.root / "people").exists() and not (w.root / "targets" / "x-co.md").exists()
    assert {k: v[0] for k, v in full_state(w.root).items()} == before          # the ledger and every file are as they were


def test_a_crash_window_leaves_an_orphan_that_is_never_state(w: W) -> None:
    w.target("seed-co", company="Seed Co")
    orphan_id = ids.new_id("person")
    rel = f"people/{orphan_id}.md"
    (w.root / "people").mkdir()
    (w.root / rel).write_bytes(person_mod.to_bytes(Person(orphan_id, ALEX, "Alex", "", "Example Corp", "example-corp", "recruiter",
                                                          None, (), "2026-10-06")))
    assert out_codes(w, code="OUT002") == [("OUT002", "warning", rel)]
    assert ids_of(w) == [] and service.person_list(w.root, w.now) == []
    L = service.load(w.root, w.now)
    assert orphan_id not in L.people_fold.flags
    w.refused("outreach", "person", "show", orphan_id, code="not_found")
    assert apeople.person_urls(w.events()) == ({}, [])
    # the same URL can still be added: it gets its own id, the orphan keeps being reported
    new = w.person(ALEX)
    assert new["id"] != orphan_id
    assert out_codes(w, code="OUT004") == [("OUT004", "error", rel)]          # the orphan's url now belongs to another id


def test_deleting_a_record_file_keeps_identity_and_gives_out003(w: W) -> None:
    per = w.person(ALEX)["id"]
    (w.root / "people" / f"{per}.md").unlink()
    assert out_codes(w, code="OUT003") == [("OUT003", "warning", f"people/{per}.md")]
    assert apeople.person_urls(w.events()) == ({ALEX: per}, [])
    assert w.propose("linkedin_invite", ALEX)["target"] == per
    w.refused("outreach", "person", "add", "--url", ALEX, "--name", "A", "--company", "C", "--role", "peer", code="duplicate")
    assert not (w.root / "people" / f"{per}.md").exists()                      # nothing recreated it


def test_a_person_with_approval_events_but_no_record_is_out003(w: W) -> None:
    w.target("seed-co", company="Seed Co")
    inv = w.propose("linkedin_invite", ALEX)
    assert out_codes(w, code="OUT003") == [("OUT003", "warning", f"people/{inv['target']}.md")]
    w.person(ALEX)
    assert out_codes(w, code="OUT003") == []


# =========================================================================================================================
# identity: conflict, hand edits, unification
# =========================================================================================================================

def person_added_spec(per: str, url: str) -> dict:
    return {"type": "outreach.person_added", "actor": "system", "entity": per, "source": "test",
            **apeople.person_added_fields(f"people/{per}.md", "a" * 64, url)}


def conflict_two_ids_for_one_url(w: W) -> None:
    a, b = ids.new_id("person"), ids.new_id("person")
    w.append(person_added_spec(a, ALEX), person_added_spec(b, ALEX))


def conflict_proposal_vs_added(w: W) -> None:
    inv = w.propose("linkedin_invite", ALEX)
    w.append(person_added_spec(ids.new_id("person"), ALEX))
    assert inv


def conflict_one_id_two_urls(w: W) -> None:
    a = ids.new_id("person")
    w.append(person_added_spec(a, ALEX), person_added_spec(a, SAM))


@pytest.mark.parametrize("make", [conflict_two_ids_for_one_url, conflict_proposal_vs_added, conflict_one_id_two_urls])
def test_an_inconsistent_identity_map_fails_closed(w: W, make) -> None:
    make(w)
    for url in (ALEX, KIM):                       # every URL fails: no winner is ever picked
        before = full_state(w.root)
        w.refused("outreach", "person", "add", "--url", url, "--name", "A", "--company", "C", "--role", "peer",
                  code="identity_conflict")
        assert full_state(w.root) == before       # no file, no event, no rejection (no entity is named)
        f = w.root / "draft.txt"
        f.write_text("hello\n", encoding="utf-8")
        before = full_state(w.root)
        res = w.run("approval", "propose", "--kind", "linkedin_invite", "--person-url", url, "--label", "x",
                    "--content-file", str(f), "--json")
        assert res.exit_code == 1 and json.loads(res.stdout)["code"] == "identity_conflict"
        assert full_state(w.root) == before
    assert not (w.root / "people").exists()
    assert out_codes(w, code="OUT004")                                        # validate reports it as an error
    assert all(s == "error" for _, s, _ in out_codes(w, code="OUT004"))


def test_job_proposals_are_not_blocked_by_an_identity_conflict(w: W) -> None:
    from careeros.core.models import State
    conflict_two_ids_for_one_url(w)
    job = add_job(w.root, "sub-job", status=State.APPROVAL_REQUIRED, bullet="approval-required")
    f = w.root / "draft.txt"
    f.write_text("answers\n", encoding="utf-8")
    res = w.run("approval", "propose", "--kind", "application_submit", "--job", job["id"], "--content-file", str(f), "--json")
    assert res.exit_code == 0, res.output


def test_hand_edited_url_or_id_is_out004_and_changes_no_identity(w: W) -> None:
    per = w.person(ALEX)["id"]
    other = w.person(SAM, name="Sam")["id"]
    ident = apeople.person_urls(w.events())
    hand_edit_person(w.root, per, url=KIM)
    assert out_codes(w, code="OUT004") == [("OUT004", "error", f"people/{per}.md")]
    assert apeople.person_urls(w.events()) == ident and w.propose("linkedin_invite", ALEX)["target"] == per
    hand_edit_person(w.root, per, url=ALEX)
    assert out_codes(w, code="OUT004") == []
    sub_text(w.root, f"people/{per}.md", f"id: {per}", f"id: {other}")                   # an id edit: wrong file name and wrong id
    codes = {c for c, _, p in out_codes(w) if p == f"people/{per}.md"}
    assert {"OUT001", "OUT004"} <= codes
    assert apeople.person_urls(w.events()) == ident


def test_a_record_with_another_persons_url_is_out004(w: W) -> None:
    a, b = w.person(ALEX)["id"], w.person(SAM, name="Sam")["id"]
    hand_edit_person(w.root, b, url=ALEX)
    paths = {p for c, _, p in out_codes(w, code="OUT004")}
    assert f"people/{b}.md" in paths
    hand_edit_person(w.root, b, url=SAM)
    orphan = ids.new_id("person")
    (w.root / "people" / f"{orphan}.md").write_bytes(person_mod.to_bytes(dataclasses.replace(read_person(w.root, a), id=orphan)))
    assert ("OUT004", "error", f"people/{orphan}.md") in out_codes(w)


def test_target_id_edit_is_out004_and_slug_edit_is_out001(w: W) -> None:
    w.target()
    tgt_events = w.events("outreach.target_added")
    hand_edit_target(w.root, "example-corp", id=ids.new_id("target"))
    assert out_codes(w, code="OUT004") == [("OUT004", "error", "targets/example-corp.md")]
    sub_text(w.root, "targets/example-corp.md", "slug: example-corp", "slug: other-co")
    assert ("OUT001", "error", "targets/example-corp.md") in out_codes(w)
    assert w.events("outreach.target_added") == tgt_events
    assert service.target_show(w.root, "example-corp", w.now).decision == "candidate"


# =========================================================================================================================
# outreach.rejected: dedupe and audit-only; reserved prefix; reasons
# =========================================================================================================================

def test_rejections_are_deduped_audit_only_and_never_state(w: W) -> None:
    w.target()
    w.approve_target("example-corp")
    base = service.load(w.root, T0).targets_fold.targets
    for _ in range(3):
        w.refused("outreach", "target", "approve", "example-corp", code="wrong_state")
    assert len(rejected(w)) == 1
    ev = rejected(w)[0]
    assert ev["entity"] == w.events("outreach.target_added")[0]["entity"] and ev["reason"].startswith("wrong_state: ")
    assert ev["action"].startswith("rejected target approve of tgt_") and ev["source"] == "cli"
    assert service.load(w.root, T0).targets_fold.targets == base
    # a different refusal is a new rejection; the same refusal after another event is recorded again
    w.refused("outreach", "target", "drop", "example-corp", code="invalid_input")
    assert len(rejected(w)) == 2
    w.refused("outreach", "target", "approve", "example-corp", code="wrong_state")
    assert len(rejected(w)) == 3
    w.refused("outreach", "target", "approve", "example-corp", code="wrong_state")
    assert len(rejected(w)) == 3
    assert out_codes(w) == []                       # rejections are valid ledger content


def test_rejections_never_change_a_person_fold(w: W) -> None:
    per = w.person()["id"]
    flags = service.load(w.root, T0).people_fold.flags
    for _ in range(2):
        w.refused("outreach", "person", "add", "--url", ALEX, "--name", "A", "--company", "C", "--role", "peer", code="duplicate")
    assert len(rejected(w)) == 1 and rejected(w)[0]["entity"] == per
    loaded = service.load(w.root, T0)
    assert loaded.people_fold.flags == flags and loaded.people_fold.errors == ()


def test_the_outreach_prefix_is_reserved_and_reasons_are_required(w: W) -> None:
    assert ledger.is_reserved("outreach.accepted") and ledger.is_reserved("outreach.anything")
    res = w.run("ledger", "append", "--type", "outreach.x", "--action", "sneaky", "--actor", "agent:claude")
    assert res.exit_code == 2 and "reserved" in res.output
    assert w.events() == []
    for kind in ("outreach.rejected", "outreach.target_dropped"):
        with pytest.raises(LedgerError, match="reason is required"):
            ledger.append_events(w.root, [{"type": kind, "actor": "user", "action": "x", "entity": "tgt_aaaaaaaaaa"}])
        ledger.append_events(w.root, [{"type": kind, "actor": "user", "action": "x", "entity": "tgt_aaaaaaaaaa", "reason": "r"}])


# =========================================================================================================================
# the record-editing table (spec 6.0): what a hand edit may change and what it never does
# =========================================================================================================================

NOWS = ("2026-10-06T11:00:00Z", "2026-10-14T10:00:00Z")      # inside the cap window; the invites are 8 days old


def projections(root: Path, nows=NOWS) -> dict:
    """The outputs that MAY depend on record fields, per `now`."""
    out = {}
    for now in nows:
        L = service.load(root, now)
        infos = {s: derive.TargetInfo(L.targets[s], v) for s, v in L.targets_fold.targets.items() if s in L.targets}
        out[now] = {
            "status": {s: v.status for s, v in L.targets_fold.targets.items()},
            "activity": {s: v.last_activity for s, v in L.targets_fold.targets.items()},
            "next": [n.person_id for n in derive.build_next(L.people, L.people_fold.flags, infos, L.jobs, L.items, 15, now)],
            "warm": [x.person_id for x in derive.build_warm(L.people, L.people_fold.flags, L.jobs, now)],
            "queue": [q.entity_id for q in derive.build_queue(L.people, L.people_fold.flags, infos, L.jobs, now).items],
        }
    return out


def invariants(root: Path, nows=NOWS) -> dict:
    """The outputs that come from the ledger only: identity, sends, the cap, closed people, person flags."""
    out = {}
    for now in nows:
        L = service.load(root, now)
        count = cap.invite_count(L.items, now, exclude=None)
        out[now] = {
            "identity": apeople.person_urls(L.events), "problems": apeople.identity_problems(L.events),
            "items": {i: (s.state.value if s.state else None) for i, s in L.items.items()},
            "cap": (count.in_flight, count.consumed_in_window, count.oldest_leaves),
            "closed": apeople.closed_people(L.events)[0], "flags": dict(L.people_fold.flags),
            "decisions": {s: v.decision for s, v in L.targets_fold.targets.items()},
        }
    return out


@pytest.fixture
def world(w: W):
    """Two approved targets, a saved job at a third company, and persons A and D (recruiters, target route), B (invited, found
    through a target), F (invited and accepted), G (closed). Everything is at T0; the invites are 8 days old at the last now."""
    job = add_job(w.root, "beta-job", company="Beta Inc")
    t1 = w.target("example-corp", company="Example Corp", fit=8, evidence=("sponsor register entry (placeholder):2026-10-06",))
    t2 = w.target("other-co", company="Other Co", fit=5, evidence=("sponsor register entry (placeholder):2026-10-06",))
    w.approve_target("example-corp")
    w.approve_target("other-co")
    p = {}
    p["A"] = w.person(ALEX, name="Alex", found_in=("target:example-corp",))["id"]
    p["D"] = w.person(SAM, name="Sam", company="Other Co", found_in=("target:other-co",))["id"]
    inv_b, _ = w.invite_done(KIM)
    p["B"] = w.person(KIM, name="Kim", role="hiring_manager", found_in=("target:example-corp",))["id"]
    assert inv_b == p["B"]
    inv_f, _ = w.invite_done(PAT)
    p["F"] = w.person(PAT, name="Pat", role="hiring_manager", found_in=())["id"]
    w.outcome(inv_f, "accepted")
    p["G"] = w.person(LEE, name="Lee", found_in=("target:example-corp",))["id"]
    w.outcome(p["G"], "closed")
    return w, p, {"example-corp": t1["id"], "other-co": t2["id"]}, job


def test_the_world_baseline(world) -> None:
    w, p, t, job = world
    base = projections(w.root)
    soon, later = base[NOWS[0]], base[NOWS[1]]
    assert soon["next"] == [p["A"], p["D"]]                       # likely/likely: fit 8 before fit 5; B, F invited; G closed
    assert soon["status"] == {"example-corp": "contacted", "other-co": "approved"}
    assert soon["warm"] == [] and later["queue"] == []             # B is pre-opening: no saved job at its company
    assert out_codes(w) == []
    assert invariants(w.root)[NOWS[0]]["cap"][:2] == (0, 2)


def person_edit(per_key, **changes):
    return lambda root, p, t, job: hand_edit_person(root, p[per_key], **changes)


def target_edit(slug, **changes):
    return lambda root, p, t, job: hand_edit_target(root, slug, **changes)


def company_edit(per_key, company):
    return lambda root, p, t, job: hand_edit_person(root, p[per_key], company=company, company_slug=_slug(company))


def _slug(text):
    from careeros.core.outreach.slug import slugify
    return slugify(text)


def _same(base):
    return base


def _status(**kw):
    return lambda base: {n: {**v, "status": {**v["status"], **kw}} for n, v in base.items()}


def _field(key, value):
    return lambda base: {n: {**v, key: value(n) if callable(value) else value} for n, v in base.items()}


EDITS = [
    # fields that affect no derived output
    ("person.name", person_edit("A", name="Renamed"), _same, set()),
    ("person.headline", person_edit("A", headline="A new headline"), _same, set()),
    ("person.found_at", person_edit("A", found_at="2020-01-01"), _same, set()),
    ("person.market", person_edit("A", market="India (home)"), _same, set()),
    ("target.company", target_edit("example-corp", company="Example Corporation"), _same, set()),
    ("target.market", target_edit("example-corp", market="Germany"), _same, set()),
    # fields that change target status, next, warm or the queue
    ("person.role", person_edit("A", role="peer"), _field("next", ["D"]), set()),
    ("person.found_in", person_edit("A", found_in=()), _field("next", ["D"]), set()),
    # no linked target any more: the target is no longer contacted, and B (empty found_in is not "found only through targets")
    # is no longer a pre-opening contact, so its due invite is queued
    ("person.found_in(B)", person_edit("B", found_in=()),
     lambda b: _field("queue", lambda n: ["B"] if n == NOWS[1] else [])(_status(**{"example-corp": "approved"})(b)), set()),
    ("person.company(A, same target route)", company_edit("A", "Beta Inc"), _same, set()),
    ("person.company(B)", company_edit("B", "Beta Inc"),
     _field("queue", lambda n: ["B"] if n == NOWS[1] else []), set()),
    ("person.company(F)", company_edit("F", "Beta Inc"), _field("warm", ["F"]), set()),
    ("target.fit", target_edit("example-corp", fit=2), _field("next", ["D", "A"]), set()),
    ("target.sponsorship(not_offered)", target_edit("other-co", sponsorship="not_offered"),
     _field("next", ["A"]), set()),
    ("target.sponsorship(n/a ranks first)", target_edit("other-co", sponsorship="n/a"),
     _field("next", ["D", "A"]), set()),
    ("target.evidence", target_edit("example-corp", evidence=(Evidence("sponsor register entry (placeholder)", "2031-01-01"),)),
     lambda b: {n: {**v, "activity": {**v["activity"], "example-corp": "2031-01-01T00:00:00Z"}} for n, v in b.items()}, set()),
    # identity-bearing fields: flagged, and no identity or ledger-derived output changes
    # a record whose url disagrees with its event feeds no state (A is no longer offered), and identity stays as the ledger says
    ("person.url", person_edit("A", url=RAN), _field("next", ["D"]), {"OUT004"}),
]


@pytest.mark.parametrize("name,edit,expect,codes", EDITS, ids=[e[0] for e in EDITS])
def test_the_record_editing_table(world, name, edit, expect, codes) -> None:
    w, p, t, job = world
    base_proj, base_inv = projections(w.root), invariants(w.root)
    ledger_before = w.ledger_bytes()
    edit(w.root, p, t, job)
    proj = projections(w.root)
    letters = {v: k for k, v in p.items()}

    def named(d):         # person ids -> the world's letters, so the expectation reads clearly
        return {n: {**v, **{k: [letters.get(x, x) for x in v[k]] for k in ("next", "warm", "queue")}} for n, v in d.items()}

    assert named(proj) == expect(named(base_proj)), name
    assert invariants(w.root) == base_inv, f"{name} changed an output that must come from the ledger only"
    assert w.ledger_bytes() == ledger_before                                  # a hand edit appends nothing
    found = {c for c, _, _ in out_codes(w, NOWS[1])}
    assert found == codes, (name, found)


def test_unparseable_identity_edits_exclude_the_record_but_never_the_identity(world) -> None:
    w, p, t, job = world
    base_inv = invariants(w.root)
    sub_text(w.root, f"people/{p['A']}.md", f"id: {p['A']}", f"id: {p['D']}")         # id edit: file name and id disagree
    assert invariants(w.root) == base_inv
    codes = {c for c, _, path in out_codes(w, NOWS[1]) if path == f"people/{p['A']}.md"}
    assert codes == {"OUT001", "OUT004"}
    assert projections(w.root)[NOWS[0]]["next"] == [p["D"]]                         # an unreadable record offers nobody
    sub_text(w.root, f"people/{p['A']}.md", f"id: {p['D']}", f"id: {p['A']}")
    sub_text(w.root, "targets/example-corp.md", "slug: example-corp", "slug: renamed-co")
    assert invariants(w.root) == base_inv
    assert ("OUT001", "error", "targets/example-corp.md") in out_codes(w, NOWS[1])


HAND_EDITS = [
    ("person: bad role", lambda w, p: sub_text(w.root, f"people/{p['A']}.md", "role: recruiter", "role: boss")),
    ("person: unknown key", lambda w, p: sub_text(w.root, f"people/{p['A']}.md", "role: recruiter", "role: recruiter\nmood: great")),
    ("person: edited notes", lambda w, p: (w.root / "people" / f"{p['A']}.md").write_text(
        (w.root / "people" / f"{p['A']}.md").read_text(encoding="utf-8") + "\nmore notes\n", encoding="utf-8")),
    ("person: not a record", lambda w, p: (w.root / "people" / f"{p['A']}.md").write_text("no frontmatter at all\n", encoding="utf-8")),
    ("target: bad fit", lambda w, p: sub_text(w.root, "targets/example-corp.md", "fit: 8", "fit: 99")),
    ("target: bad sponsorship", lambda w, p: sub_text(w.root, "targets/other-co.md", "sponsorship: likely", "sponsorship: bogus")),
]


@pytest.mark.parametrize("name,edit", HAND_EDITS, ids=[e[0] for e in HAND_EDITS])
def test_commands_never_repair_or_rewrite_an_edited_record(world, name, edit) -> None:
    w, p, t, job = world
    edit(w, p)
    files = {f.relative_to(w.root).as_posix(): f.read_bytes() for d in ("people", "targets") for f in (w.root / d).glob("*.md")}
    before = full_state(w.root, lock=True)
    for argv in (("validate",), ("outreach", "person", "list"), ("outreach", "person", "show", p["A"]),
                 ("outreach", "person", "show", p["D"]), ("outreach", "target", "list"), ("outreach", "target", "show", "example-corp"),
                 ("outreach", "target", "show", "other-co")):
        res = w.run(*argv)
        assert res.exit_code in (0, 1), (argv, res.output)
        assert full_state(w.root, lock=True) == before, argv
    # state-changing commands on OTHER entities append events but never touch an edited record
    w.person(RAN, name="Ran", found_in=())
    w.target("third-co", company="Third Co", sponsorship="n/a", evidence=())
    w.drop_target("third-co")
    now_files = {f.relative_to(w.root).as_posix(): f.read_bytes() for d in ("people", "targets") for f in (w.root / d).glob("*.md")}
    for rel, data in files.items():
        assert now_files[rel] == data, rel


# =========================================================================================================================
# the refusal audit
# =========================================================================================================================

def derived_snapshot(root: Path, nows=NOWS) -> dict:
    return {"inv": invariants(root, nows), "proj": projections(root, nows)}


def corrupt_chain(w: W) -> None:
    lines = w.ledger_bytes().split(b"\n")
    assert len(lines) > 2
    lines[0] = lines[0].replace(b'"type":', b'"type": ', 1)
    (w.root / "ledger.jsonl").write_bytes(b"\n".join(lines))


def go_back(w: W) -> None:
    w.now = shift(T0, days=-2)


def skew_known_person(w, p):
    go_back(w)
    return ("outreach", "person", "add", "--url", ALEX, "--name", "A", "--company", "C", "--role", "peer")


# (name, how many rejected events are appended, a function (w, p) -> argv, expected code)
REFUSALS = [
    ("person add duplicate", 1, lambda w, p: ("outreach", "person", "add", "--url", ALEX, "--name", "A", "--company", "C", "--role", "peer"), "duplicate"),
    ("person add bad url (unknown entity)", 0, lambda w, p: ("outreach", "person", "add", "--url", "https://x.test/", "--name", "A", "--company", "C", "--role", "peer"), "invalid_input"),
    ("person add bad found_in, new person", 0, lambda w, p: ("outreach", "person", "add", "--url", RAN, "--name", "A", "--company", "C", "--role", "peer", "--found-in", "job:nope"), "invalid_input"),
    ("person add bad role, id known from approvals", 1, lambda w, p: ("outreach", "person", "add", "--url", "https://www.linkedin.com/in/known-from-approvals", "--name", "A", "--company", "C", "--role", "boss"), "invalid_input"),
    ("person add clock_skew, known person", 1, skew_known_person, "clock_skew"),
    ("person add clock_skew, new person", 0, lambda w, p: (go_back(w), "outreach", "person", "add", "--url", RAN, "--name", "A", "--company", "C", "--role", "peer")[1:], "clock_skew"),
    ("person add ledger_corrupt", 0, lambda w, p: (corrupt_chain(w), "outreach", "person", "add", "--url", RAN, "--name", "A", "--company", "C", "--role", "peer")[1:], "ledger_corrupt"),
    ("person add identity_conflict", 0, lambda w, p: (conflict_one_id_two_urls(w), "outreach", "person", "add", "--url", RAN, "--name", "A", "--company", "C", "--role", "peer")[1:], "identity_conflict"),
    ("person link unknown", 0, lambda w, p: ("outreach", "person", "link", ids.new_id("person"), "--found-in", "target:example-corp"), "not_found"),
    ("person link bad ref", 1, lambda w, p: ("outreach", "person", "link", p["A"], "--found-in", "job:nope"), "invalid_input"),
    ("person link clock_skew", 1, lambda w, p: (go_back(w), "outreach", "person", "link", p["A"], "--found-in", "target:other-co")[1:], "clock_skew"),
    ("person link ledger_corrupt", 0, lambda w, p: (corrupt_chain(w), "outreach", "person", "link", p["A"], "--found-in", "target:other-co")[1:], "ledger_corrupt"),
    ("target add duplicate", 1, lambda w, p: ("outreach", "target", "add", "--company", "X", "--slug", "example-corp", "--market", "Australia", "--fit", "5", "--sponsorship", "n/a"), "duplicate"),
    ("target add invalid, new slug", 0, lambda w, p: ("outreach", "target", "add", "--company", "X", "--slug", "new-co", "--market", "Australia", "--fit", "5", "--sponsorship", "likely"), "invalid_input"),
    ("target add clock_skew, existing slug", 1, lambda w, p: (go_back(w), "outreach", "target", "add", "--company", "X", "--slug", "example-corp", "--market", "Australia", "--fit", "5", "--sponsorship", "n/a")[1:], "clock_skew"),
    ("target add clock_skew, new slug", 0, lambda w, p: (go_back(w), "outreach", "target", "add", "--company", "X", "--slug", "new-co", "--market", "Australia", "--fit", "5", "--sponsorship", "n/a")[1:], "clock_skew"),
    ("target add ledger_corrupt", 0, lambda w, p: (corrupt_chain(w), "outreach", "target", "add", "--company", "X", "--slug", "new-co", "--market", "Australia", "--fit", "5", "--sponsorship", "n/a")[1:], "ledger_corrupt"),
    ("target approve wrong_state", 1, lambda w, p: ("outreach", "target", "approve", "example-corp"), "wrong_state"),
    ("target drop without reason", 1, lambda w, p: ("outreach", "target", "drop", "other-co"), "invalid_input"),
    ("target approve bad source", 1, lambda w, p: ("outreach", "target", "approve", "other-co", "--source", "pigeon"), "invalid_input"),
    ("target approve unknown", 0, lambda w, p: ("outreach", "target", "approve", "no-such"), "not_found"),
    ("target drop clock_skew", 1, lambda w, p: (go_back(w), "outreach", "target", "drop", "other-co", "--reason", "x")[1:], "clock_skew"),
    ("target drop ledger_corrupt", 0, lambda w, p: (corrupt_chain(w), "outreach", "target", "drop", "other-co", "--reason", "x")[1:], "ledger_corrupt"),
]


@pytest.mark.parametrize("name,added,argv,code", REFUSALS, ids=[r[0] for r in REFUSALS])
def test_refusal_audit(world, name, added, argv, code) -> None:
    w, p, t, job = world
    w.propose("linkedin_invite", "https://www.linkedin.com/in/known-from-approvals")
    files = {f.relative_to(w.root).as_posix(): f.read_bytes() for d in ("people", "targets") for f in (w.root / d).glob("*.md")}
    args = argv(w, p)                       # may set the clock back, break the chain or append a hand-crafted conflict
    snap_before, ledger_before = derived_snapshot(w.root), w.ledger_bytes()
    w.refused(*args, code=code)
    assert w.ledger_bytes().startswith(ledger_before), "the old ledger bytes must stay a prefix"
    appended = [json.loads(line)["type"] for line in w.ledger_bytes()[len(ledger_before):].splitlines()]
    assert appended == ["outreach.rejected"] * added, (name, appended)
    now_files = {f.relative_to(w.root).as_posix(): f.read_bytes() for d in ("people", "targets") for f in (w.root / d).glob("*.md")}
    assert now_files == files                                       # no record was created, changed or removed
    assert derived_snapshot(w.root) == snap_before                  # the audit event changed no derived output
    again = w.ledger_bytes()
    w.refused(*args, code=code)
    assert w.ledger_bytes() == again                                # an identical refusal is not recorded twice


# =========================================================================================================================
# validation: each OUT code, positive and negative; read-only; unchanged workspaces
# =========================================================================================================================

def test_a_workspace_without_outreach_data_is_unchanged(w: W) -> None:
    assert service.validate_workspace(w.root, [], T0) == []
    add_job(w.root, "beta-job", company="Beta Inc")
    w.propose("linkedin_invite", ALEX)                          # approvals only: no people/, no targets/, no outreach events
    assert service.validate_workspace(w.root, w.events(), T0) == []
    res = w.run("validate", "--json")
    assert not [i for i in json.loads(res.stdout)["issues"] if i["code"].startswith("OUT")]
    assert not (w.root / "people").exists() and not (w.root / "targets").exists()


def test_out001_cases(w: W) -> None:
    per = w.person()["id"]
    w.target()
    assert out_codes(w, code="OUT001") == []
    sub_text(w.root, f"people/{per}.md", "role: recruiter", "role: boss")
    sub_text(w.root, "targets/example-corp.md", "fit: 8", "fit: 99")
    assert out_codes(w, code="OUT001") == [("OUT001", "error", f"people/{per}.md"), ("OUT001", "error", "targets/example-corp.md")]
    (w.root / "people" / "per_zzzzzzzzzz.md").write_bytes(b"\xff\xfe not utf-8")
    (w.root / "people" / "per_yyyyyyyyyy.md").symlink_to(w.root / "profile.md")
    (w.root / "targets" / "dir-co.md").mkdir()
    paths = {p for c, s, p in out_codes(w, code="OUT001")}
    assert {"people/per_zzzzzzzzzz.md", "people/per_yyyyyyyyyy.md", "targets/dir-co.md"} <= paths


def test_out002_cases(w: W) -> None:
    per = w.person()["id"]
    w.target()
    assert out_codes(w, code="OUT002") == []
    rec = read_person(w.root, per)
    orphan = ids.new_id("person")
    (w.root / "people" / f"{orphan}.md").write_bytes(person_mod.to_bytes(dataclasses.replace(rec, id=orphan, url=SAM)))
    (w.root / "targets" / "ghost-co.md").write_bytes(target_mod.to_bytes(
        Target(ids.new_id("target"), "ghost-co", "Ghost", "Australia", 5, "n/a")))
    assert out_codes(w, code="OUT002") == [("OUT002", "warning", f"people/{orphan}.md"), ("OUT002", "warning", "targets/ghost-co.md")]


def test_out003_cases(w: W) -> None:
    per = w.person()["id"]
    tgt = w.target()
    assert out_codes(w, code="OUT003") == []
    (w.root / "targets" / "example-corp.md").unlink()
    (w.root / "people" / f"{per}.md").unlink()
    assert out_codes(w, code="OUT003") == [("OUT003", "warning", f"people/{per}.md"), ("OUT003", "warning", "targets/example-corp.md")]
    assert tgt


def test_out004_cases(w: W) -> None:
    per = w.person()["id"]
    w.target()
    assert out_codes(w, code="OUT004") == []
    hand_edit_person(w.root, per, url=SAM)
    assert out_codes(w, code="OUT004") == [("OUT004", "error", f"people/{per}.md")]
    hand_edit_person(w.root, per, url=ALEX)
    conflict_one_id_two_urls(w)
    assert ("OUT004", "error", "ledger.jsonl") in out_codes(w)


def crafted_second_invite(w: W, per: str, url: str) -> None:
    from careeros.core.approvals import fold as f
    apr = ids.new_id("approval")
    base = {"actor": "system", "entity": apr, "source": "test"}
    w.append({**base, "type": "approval.proposed", "new_state": "pending", **f.proposal_event_fields("linkedin_invite", per, f"approvals/{apr}.md", "b" * 64, url)},
             {**base, "type": "approval.approved", "action": "a", "new_state": "approved"},
             {**base, "type": "approval.started", "action": "s", "new_state": "in_flight"},
             {**base, "type": "approval.consumed", "action": "c", "new_state": "consumed"})


def test_out005_cases(w: W) -> None:
    per, _ = w.invite_done(ALEX)
    w.person(ALEX)
    other = w.person(SAM, name="Sam")["id"]
    assert out_codes(w, code="OUT005") == []
    w.outcome(other, "accepted")                                 # accepted with no invite
    assert len(out_codes(w, code="OUT005")) == 1
    w.outcome(per, "closed")
    w.outcome(per, "replied")                                    # an outcome after closed
    w.append({"type": "outreach.bogus", "actor": "system", "entity": per, "action": "x"})   # unknown type
    crafted_second_invite(w, per, ALEX)                          # a second consumed invite
    msgs = [i.message for i in service.validate_workspace(w.root, w.events(), w.now) if i.code == "OUT005"]
    assert len(msgs) == 4, msgs
    assert any("not legal for a" in m and "accepted" in m for m in msgs) and any("bogus" in m for m in msgs)
    assert any("second consumed linkedin_invite" in m for m in msgs)
    assert all(s == "error" for _, s, _ in out_codes(w, code="OUT005"))


def test_out005_target_sequences(w: W) -> None:
    tgt = w.target()["id"]
    w.append({"type": "outreach.target_approved", "actor": "user", "entity": ids.new_id("target"), "action": "x", "source": "user"})
    w.append({"type": "outreach.target_dropped", "actor": "user", "entity": tgt, "action": "x", "source": "user", "reason": "r"},
             {"type": "outreach.target_approved", "actor": "user", "entity": tgt, "action": "x", "source": "user"})
    assert len(out_codes(w, code="OUT005")) == 2


def test_out006_cases(w: W) -> None:
    job = add_job(w.root, "beta-job", company="Beta Inc")
    tgt = w.target()
    per = w.person(found_in=("target:example-corp", "job:beta-job"))["id"]
    assert out_codes(w, code="OUT006") == []
    hand_edit_person(w.root, per, found_in=(FoundIn("job", ids.new_id("job")), FoundIn("target", ids.new_id("target")),
                                            FoundIn("job", job["id"]), FoundIn("target", tgt["id"])))
    assert out_codes(w, code="OUT006") == [("OUT006", "warning", f"people/{per}.md")] * 2
    # a target whose file was deleted but whose event remains still exists, so its id is a valid reference
    (w.root / "targets" / "example-corp.md").unlink()
    assert len(out_codes(w, code="OUT006")) == 2


def test_out100_cases(w: W) -> None:
    w.target()
    w.target("fresh-co", company="Fresh", sponsorship="n/a", evidence=())
    assert out_codes(w, shift(T0, days=60), code="OUT100") == []                     # exactly 60 days: not stale
    stale = out_codes(w, shift(T0, days=60, seconds=1), code="OUT100")
    assert stale == [("OUT100", "info", "targets/example-corp.md"), ("OUT100", "info", "targets/fresh-co.md")]
    w.drop_target("fresh-co")
    late = out_codes(w, shift(T0, days=90), code="OUT100")
    assert late == [("OUT100", "info", "targets/example-corp.md")]                   # a dropped target is never stale


def test_validate_through_the_cli_and_exit_codes(world) -> None:
    w, p, t, job = world
    res = w.run("validate", "--json")
    assert res.exit_code == 0 and not [i for i in json.loads(res.stdout)["issues"] if i["code"].startswith("OUT")]
    sub_text(w.root, f"people/{p['A']}.md", "role: recruiter", "role: boss")
    res = w.run("validate", "--json")
    body = json.loads(res.stdout)
    assert res.exit_code == 1 and body["errors"] >= 1 and any(i["code"] == "OUT001" for i in body["issues"])
    (w.root / "people" / f"{p['A']}.md").unlink()
    res = w.run("validate", "--json")                                          # warnings only: exit 0
    codes = {i["code"] for i in json.loads(res.stdout)["issues"] if i["code"].startswith("OUT")}
    assert res.exit_code == 0 and codes == {"OUT003"}


def test_a_corrupt_ledger_line_never_crashes_validation(world) -> None:
    w, p, t, job = world
    good = w.events()
    with (w.root / "ledger.jsonl").open("ab") as handle:
        handle.write(b"this is not json\n")
    res = w.run("validate", "--json")
    assert res.exit_code == 1 and json.loads(res.stdout)["errors"] >= 1
    hostile = [1, "x", None, {"type": "outreach.person_added", "entity": 5, "artifacts": "no"},
               {"type": "outreach.target_added", "entity": "tgt_x", "action": 7}, {"type": 3}, {"type": "outreach.closed"}]
    service.validate_workspace(w.root, hostile + good, w.now)           # must not raise
    service.validate_workspace(w.root, hostile, "not a time")


def test_validate_list_and_show_leave_the_workspace_byte_identical(world) -> None:
    w, p, t, job = world
    sub_text(w.root, f"people/{p['G']}.md", "role: recruiter", "role: boss")       # an invalid record changes nothing either
    (w.root / "people" / "per_orphan000.md").write_text("junk\n", encoding="utf-8")
    before = full_state(w.root, lock=True)
    for argv in (("validate",), ("validate", "--json"), ("validate", "--strict"), ("outreach", "person", "list"),
                 ("outreach", "person", "list", "--json"), ("outreach", "person", "show", p["A"]),
                 ("outreach", "person", "show", p["G"], "--json"), ("outreach", "target", "list"),
                 ("outreach", "target", "list", "--json"), ("outreach", "target", "show", "example-corp"),
                 ("outreach", "target", "show", "example-corp", "--json")):
        w.run(*argv)
        assert full_state(w.root, lock=True) == before, argv
    for name in ("people", "targets"):
        assert (w.root / name).is_dir()


def test_every_out_code_in_the_service_is_a_rules_constant() -> None:
    src = Path(service.__file__).read_text(encoding="utf-8")
    used = set(re.findall(r"rules\.(OUT\d+)", src))
    assert used == {"OUT001", "OUT002", "OUT003", "OUT004", "OUT005", "OUT006", "OUT100"}
    assert (rules.OUT001, rules.OUT100) == ("OUT001", "OUT100")


def test_the_service_imports_no_cli(tmp_path: Path) -> None:
    import subprocess
    import sys
    code = ("import sys, careeros.core.outreach.service; "
            "bad=[m for m in sys.modules if m.startswith('careeros.cli') or m=='typer']; print(bad)")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60, check=True).stdout
    assert out.strip() == "[]"
    code = ("import sys, careeros.core.approvals.service; print([m for m in sys.modules if m.startswith('careeros.core.outreach')])")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60, check=True).stdout
    assert out.strip() == "[]"                               # approvals import nothing from the outreach package


# =========================================================================================================================
# fix round 1: orphans never feed state; file-first, one clock read, dedupe and fail-closed rules pinned
# =========================================================================================================================

def test_an_orphan_person_file_never_feeds_target_state(w: W) -> None:
    w.target()
    w.approve_target("example-corp")
    per, _ = w.invite_done(ALEX)                                   # approval-only person: no outreach.person_added event
    tgt = w.events("outreach.target_added")[0]["entity"]
    rel = f"people/{per}.md"
    (w.root / "people").mkdir()
    (w.root / rel).write_bytes(person_mod.to_bytes(Person(per, ALEX, "Alex", "", "Example Corp", "example-corp", "recruiter", None,
                                                          (FoundIn("target", tgt),), "2026-10-06")))
    assert service.target_show(w.root, "example-corp", w.now).status == "approved"            # not "contacted"
    assert service.target_show(w.root, "example-corp", w.now).people == ()
    assert w.ok("outreach", "target", "show", "example-corp")["status"] == "approved"
    assert [t["status"] for t in w.ok("outreach", "target", "list")["targets"]] == ["approved"]
    assert ("OUT002", "warning", rel) in out_codes(w)
    assert per not in service.load(w.root, w.now).people
    (w.root / rel).unlink()                                        # delete the orphan, add again: state follows the real record
    w.person(ALEX, found_in=("target:example-corp",))
    assert service.target_show(w.root, "example-corp", w.now).status == "contacted"


def test_a_record_that_disagrees_with_its_event_feeds_no_state(w: W) -> None:
    w.target()
    w.approve_target("example-corp")
    per, _ = w.invite_done(ALEX)
    w.person(ALEX, found_in=("target:example-corp",))
    assert service.target_show(w.root, "example-corp", w.now).status == "contacted"
    hand_edit_person(w.root, per, url=SAM)
    assert service.target_show(w.root, "example-corp", w.now).status == "approved"
    assert ("OUT004", "error", f"people/{per}.md") in out_codes(w)
    hand_edit_person(w.root, per, url=ALEX)
    assert service.target_show(w.root, "example-corp", w.now).status == "contacted"


def test_a_failing_file_write_leaves_no_event_and_no_file(w: W, monkeypatch: pytest.MonkeyPatch) -> None:
    from careeros.core.memory import store
    w.target("seed-co", company="Seed Co")
    before = {k: v[0] for k, v in full_state(w.root).items()}

    def boom(self, path, data):
        raise OSError("disk full (invented)")

    monkeypatch.setattr(store.Transaction, "write", boom)
    for argv in (("outreach", "person", "add", "--url", ALEX, "--name", "A", "--company", "C", "--role", "peer"),
                 ("outreach", "target", "add", "--company", "X", "--slug", "x-co", "--market", "Australia", "--fit", "5",
                  "--sponsorship", "n/a")):
        assert w.run(*argv, "--json").exit_code == 1
    assert {k: v[0] for k, v in full_state(w.root).items()} == before


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


def test_exactly_one_clock_read_per_operation_taken_under_the_lock(w: W) -> None:
    ok_ops = [
        lambda c: service.person_add(w.root, url=ALEX, name="A", company="Example Corp", role="peer", actor="user", clock=c),
        lambda c: service.target_add(w.root, company="Example Corp", slug="example-corp", market="Australia", fit=5,
                                     sponsorship="n/a", actor="user", clock=c),
        lambda c: service.target_decide(w.root, "example-corp", "approve", actor="user", clock=c),
    ]
    refusing = [
        lambda c: service.person_add(w.root, url=ALEX, name="A", company="Example Corp", role="peer", actor="user", clock=c),   # duplicate
        lambda c: service.target_decide(w.root, "example-corp", "approve", actor="user", clock=c),                               # wrong_state
        lambda c: service.person_link(w.root, ids_of(w)[0], ["job:nope"], actor="user", clock=c),                                # invalid_input
    ]
    for op in [*ok_ops, *refusing]:
        probe = Probe(w.root, shift(T0, hours=1))
        try:
            op(probe)
        except service.Refused:
            pass
        assert probe.calls == 1 and probe.held == [True]
    link = Probe(w.root, shift(T0, hours=2))
    service.person_link(w.root, ids_of(w)[0], ["target:example-corp"], actor="user", clock=link)
    assert link.calls == 1 and link.held == [True]


def test_dedupe_keys_on_the_entitys_latest_event_of_any_type(w: W) -> None:
    w.target()
    w.approve_target("example-corp")
    tgt = w.events("outreach.target_added")[0]["entity"]
    w.refused("outreach", "target", "approve", "example-corp", code="wrong_state")
    w.append({"type": "note.added", "actor": "user", "entity": tgt, "action": "an unrelated note on the same entity"})
    w.refused("outreach", "target", "approve", "example-corp", code="wrong_state")
    assert len(rejected(w)) == 2
    w.refused("outreach", "target", "approve", "example-corp", code="wrong_state")
    assert len(rejected(w)) == 2


def test_a_malformed_person_added_alone_fails_closed(w: W) -> None:
    per = ids.new_id("person")
    w.append({"type": "outreach.person_added", "actor": "system", "entity": per, "source": "test", "action": "add person",
              "artifacts": ["people/x.md"]})
    assert apeople.identity_problems(w.events()) == []                  # no URL conflict: only the reader reports it
    f = w.root / "draft.txt"
    f.write_text("hello\n", encoding="utf-8")
    before = full_state(w.root)
    w.refused("outreach", "person", "add", "--url", ALEX, "--name", "A", "--company", "C", "--role", "peer", code="identity_conflict")
    res = w.run("approval", "propose", "--kind", "linkedin_invite", "--person-url", ALEX, "--label", "x",
                "--content-file", str(f), "--json")
    assert res.exit_code == 1 and json.loads(res.stdout)["code"] == "identity_conflict"
    assert full_state(w.root) == before


def test_target_add_never_overwrites_an_orphan_target_file(w: W) -> None:
    (w.root / "targets").mkdir()
    orphan = target_mod.to_bytes(Target(ids.new_id("target"), "example-corp", "Orphan Co", "Australia", 3, "n/a"))
    (w.root / "targets" / "example-corp.md").write_bytes(orphan)
    w.refused("outreach", "target", "add", "--company", "Example Corp", "--slug", "example-corp", "--market", "Australia",
              "--fit", "5", "--sponsorship", "n/a", code="duplicate")
    assert (w.root / "targets" / "example-corp.md").read_bytes() == orphan and w.events() == []


def test_reused_id_crash_recovery_delete_the_orphan_then_add_again(w: W) -> None:
    inv = w.propose("linkedin_invite", ALEX)
    per = inv["target"]
    rel = f"people/{per}.md"
    (w.root / "people").mkdir()
    (w.root / rel).write_bytes(person_mod.to_bytes(Person(per, ALEX, "Alex", "", "Example Corp", "example-corp", "recruiter",
                                                          None, (), "2026-10-06")))
    body = w.refused("outreach", "person", "add", "--url", ALEX, "--name", "A", "--company", "C", "--role", "peer", code="duplicate")
    assert "delete the orphan file" in body["error"] and "add the person again" in body["error"]
    msgs = [i for i in service.validate_workspace(w.root, w.events(), w.now) if i.code == "OUT002"]
    assert "delete the orphan file" in msgs[0].fix and "add the person again" in msgs[0].fix
    (w.root / rel).unlink()
    out = w.person(ALEX)
    assert out["id"] == per and out["reused_id"] is True and (w.root / rel).is_file()


def test_person_show_prints_a_dash_for_an_empty_state(w: W) -> None:
    per = w.person()["id"]
    res = w.run("outreach", "person", "show", per)
    assert res.exit_code == 0 and "state:    -" in res.output
    w.outcome(per, "closed")
    assert "closed_at=" in w.run("outreach", "person", "show", per).output


@pytest.mark.parametrize("argv,field", [
    (("outreach", "target", "add", "--company", "X", "--slug", "x-co", "--market", "Australia", "--fit", "11", "--sponsorship", "n/a"), "fit"),
    (("outreach", "person", "add", "--url", ALEX, "--name", "A", "--company", "C", "--role", "boss"), "role"),
])
def test_validation_failures_name_the_field_not_a_round_trip(w: W, argv, field) -> None:
    body = w.refused(*argv, code="invalid_input")
    assert field in body["error"] and "write and read back" not in body["error"]


@pytest.mark.parametrize("argv", [
    ("outreach", "person", "add", "--url", ALEX, "--name", "A", "--company", "C", "--role", "peer"),
    ("outreach", "target", "add", "--company", "X", "--slug", "x-co", "--market", "Australia", "--fit", "5", "--sponsorship", "n/a"),
])
def test_an_invalid_actor_is_refused_before_anything_is_written(w: W, argv) -> None:
    before = full_state(w.root, lock=True)
    body = w.refused(*argv, "--actor", "not an actor", code="invalid_input")
    assert "actor" in body["error"] and full_state(w.root, lock=True) == before


def test_a_broken_chain_is_refused_before_the_clock_is_read(w: W) -> None:
    w.target("a-co", company="A Co")
    w.target("b-co", company="B Co")
    corrupt_chain(w)
    calls = []
    with pytest.raises(service.Refused) as exc:
        service.target_add(w.root, company="X", slug="x-co", market="Australia", fit=5, sponsorship="n/a", actor="user",
                           clock=lambda: calls.append(1) or T0)
    assert exc.value.code == "ledger_corrupt" and calls == []
