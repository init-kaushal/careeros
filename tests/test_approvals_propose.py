"""`careeros approval propose|list|show`, the APR section of `validate` and the reserved `approval.` prefix. Invented data."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from helpers import add_job, make_workspace, state
from typer.testing import CliRunner

from careeros.cli.main import app
from careeros.core import ids, ledger
from careeros.core.approvals import fold, item as item_mod, rules, service
from careeros.core.approvals.model import Item, ManifestEntry, Target
from careeros.core.validation import validate_workspace as validate_all
from careeros.core.workspace import WorkspaceError

runner = CliRunner()

URL = "https://www.linkedin.com/in/alex-example"
NOW = "2026-10-06T10:00:00Z"        # later than every event the `clock` fixture stamps in these tests


@pytest.fixture
def ws(tmp_path: Path, clock):
    root = make_workspace(tmp_path / "ws")
    job = add_job(root, "example-platform")
    folder = job["path"].parent
    (folder / "resume.pdf").write_bytes(b"%PDF resume")
    (folder / "cover.pdf").write_bytes(b"%PDF cover")
    job["resume"], job["cover"] = "jobs/discovered/example-platform/resume.pdf", "jobs/discovered/example-platform/cover.pdf"
    return root, job


def P(root, kind=rules.LINKEDIN_INVITE, *, url=URL, job=None, label="Alex Example, Example Corp", content="Hi Alex, small world.\n",
      manifest=(), actor="agent:test"):
    if kind == rules.APPLICATION_SUBMIT:
        url = None
    return service.propose(root, kind=kind, person_url=url, job_ref=job, label=label, content_text=content,
                           manifest_paths=list(manifest), actor=actor)


def S(root, job, **kw):
    kw.setdefault("manifest", [job["resume"], job["cover"]])
    kw.setdefault("content", "Answers: 5 years of Go.\n")
    return P(root, rules.APPLICATION_SUBMIT, job=job["id"], label="Example job", **kw)


def ev(root, type_, entity, reason=None, new_state=None):
    ledger.append_event(root, type=type_, actor="user", entity=entity, action=type_, source="tty", reason=reason, new_state=new_state)


def approve(root, apr):
    ev(root, rules.EV_APPROVED, apr)


def start(root, apr):
    ev(root, rules.EV_STARTED, apr)


def consume(root, apr):
    ev(root, rules.EV_CONSUMED, apr)


def approval_events(root):
    return [e for e in ledger.read_events(root) if e["type"].startswith("approval.")]


def codes(root, now=NOW, prefix="APR"):
    return sorted(i.code for i in validate_all(root, now) if i.code.startswith(prefix))


def issues(root, code, now=NOW):
    return [i for i in validate_all(root, now) if i.code == code]


def everything(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()} | {
        "<dirs>": "\n".join(p.relative_to(root).as_posix() for p in sorted(root.rglob("*")) if p.is_dir()).encode()}


def cli(root, *args):
    return runner.invoke(app, ["approval", *args, "--workspace", str(root)])


# --- propose: file and event shape -------------------------------------------------------------------------------------

def test_invite_file_bytes_and_event_shape(ws):
    root, _ = ws
    got = P(root)
    assert got.rel == f"approvals/{got.id}.md" and ids.is_valid_id("approval", got.id) and got.superseded == ()
    parsed, problems = item_mod.parse_bytes((root / got.rel).read_bytes(), got.rel)
    assert not problems and parsed.kind == rules.LINKEDIN_INVITE
    assert parsed.target.url == URL and parsed.target.channel == "linkedin" and ids.is_valid_id("person", parsed.target.id)
    assert parsed.content == "Hi Alex, small world.\n" and parsed.manifest == ()
    assert (root / got.rel).read_bytes() == item_mod.to_bytes(parsed)
    assert got.hash == item_mod.action_hash(parsed)
    (event,) = approval_events(root)
    assert event["type"] == "approval.proposed" and event["entity"] == got.id and event["actor"] == "agent:test"
    assert event["artifacts"] == [got.rel, f"sha256:{got.hash}"]
    assert event["action"] == fold.proposal_event_fields(rules.LINKEDIN_INVITE, parsed.target.id, got.rel, got.hash)["action"]


def test_message_and_submit_kinds(ws):
    root, job = ws
    message = P(root, rules.LINKEDIN_MESSAGE, content="Thanks for connecting.")
    submit = S(root, job)
    parsed_m, _ = item_mod.parse_bytes((root / message.rel).read_bytes(), message.rel)
    parsed_s, _ = item_mod.parse_bytes((root / submit.rel).read_bytes(), submit.rel)
    assert parsed_m.kind == rules.LINKEDIN_MESSAGE and parsed_m.target.url == URL
    assert parsed_s.kind == rules.APPLICATION_SUBMIT and parsed_s.target.id == job["id"] and parsed_s.target.url is None
    assert [m.path for m in parsed_s.manifest] == sorted([job["resume"], job["cover"]])
    assert all(len(m.sha256) == 64 for m in parsed_s.manifest)
    assert [e["entity"] for e in approval_events(root)] == [message.id, submit.id]


def test_crlf_content_is_normalised_and_blank_lines_kept(ws):
    root, _ = ws
    got = P(root, content="\r\nline\r\n\r\n")
    parsed, _ = item_mod.parse_bytes((root / got.rel).read_bytes(), got.rel)
    assert parsed.content == "\nline\n\n"


def test_job_reference_by_slug(ws):
    root, job = ws
    got = P(root, rules.APPLICATION_SUBMIT, job="example-platform", content="x", manifest=[job["resume"]])
    parsed, _ = item_mod.parse_bytes((root / got.rel).read_bytes(), got.rel)
    assert parsed.target.id == job["id"]


# --- supersession ---------------------------------------------------------------------------------------------------------

def test_second_proposal_supersedes_pending_and_unused_approved_in_one_batch(ws):
    root, _ = ws
    first = P(root)
    approve(root, first.id)
    second = P(root, content="Different text")
    assert second.superseded == (first.id,)
    events = approval_events(root)[-2:]
    assert [e["type"] for e in events] == ["approval.proposed", "approval.superseded"]
    assert events[1]["entity"] == first.id and events[1]["reason"]
    assert events[1]["seq"] == events[0]["seq"] + 1
    third = P(root, content="Third")
    assert third.superseded == (second.id,)       # the earlier one is already superseded: only the live one is touched
    folded = fold.fold_all(ledger.read_events(root), NOW)
    assert [folded[i].state.value for i in (first.id, second.id, third.id)] == ["superseded", "superseded", "pending"]


def test_different_target_or_kind_is_not_superseded(ws):
    root, _ = ws
    invite = P(root)
    other_person = P(root, url="https://www.linkedin.com/in/someone-else")
    message = P(root, rules.LINKEDIN_MESSAGE)
    assert other_person.superseded == () and message.superseded == ()
    states = fold.fold_all(ledger.read_events(root), NOW)
    assert all(states[i.id].state.value == "pending" for i in (invite, other_person, message))


def test_consumed_item_is_never_superseded(ws):
    root, job = ws
    message = P(root, rules.LINKEDIN_MESSAGE)
    approve(root, message.id), start(root, message.id), consume(root, message.id)
    again = P(root, rules.LINKEDIN_MESSAGE, content="Follow-up")
    assert again.superseded == ()
    assert fold.fold_item(ledger.read_events(root), message.id, NOW).state.value == "consumed"


def test_proposal_while_in_flight_is_refused_and_changes_nothing(ws):
    root, job = ws
    for make, kind in ((lambda: P(root), rules.LINKEDIN_INVITE), (lambda: S(root, job), rules.APPLICATION_SUBMIT)):
        first = make()
        approve(root, first.id), start(root, first.id)
        before = everything(root)
        with pytest.raises(service.Refused) as exc:
            make() if kind == rules.APPLICATION_SUBMIT else P(root, content="newer")
        assert exc.value.code == rules.IN_FLIGHT_CONFLICT
        assert everything(root) == before
        assert fold.fold_item(ledger.read_events(root), first.id, NOW).state.value == "in_flight"


def test_in_flight_of_another_kind_or_person_does_not_block(ws):
    root, _ = ws
    invite = P(root)
    approve(root, invite.id), start(root, invite.id)
    P(root, rules.LINKEDIN_MESSAGE)
    P(root, url="https://www.linkedin.com/in/someone-else")


# --- refusals ---------------------------------------------------------------------------------------------------------------

def test_invite_conflict_and_already_submitted(ws):
    root, job = ws
    invite = P(root)
    approve(root, invite.id), start(root, invite.id), consume(root, invite.id)
    submit = S(root, job)
    approve(root, submit.id), start(root, submit.id), consume(root, submit.id)
    before = everything(root)
    with pytest.raises(service.Refused) as exc:
        P(root, content="again")
    assert exc.value.code == rules.INVITE_CONFLICT
    with pytest.raises(service.Refused) as exc:
        S(root, job)
    assert exc.value.code == rules.ALREADY_SUBMITTED
    assert everything(root) == before
    P(root, rules.LINKEDIN_MESSAGE)             # a message to the same person is fine


def test_invalid_inputs_are_refused_without_side_effects(ws):
    root, job = ws
    before = everything(root)
    bad = [
        dict(job="no-such-job", kind=rules.APPLICATION_SUBMIT),
        dict(url="https://example.com/in/alex"),
        dict(url="not a url"),
        dict(url="https://www.linkedin.com/company/acme"),
        dict(content=""),
        dict(content="  \n\n"),
        dict(label="a\nb"),
        dict(label="x️"),
        dict(label=""),
    ]
    for kw in bad:
        kind = kw.pop("kind", rules.LINKEDIN_INVITE)
        with pytest.raises(service.Refused):
            P(root, kind, **kw)
    assert everything(root) == before


@pytest.mark.parametrize("paths", [
    ["/etc/hosts"], ["../outside.pdf"], ["jobs/discovered/example-platform/missing.pdf"], ["jobs/discovered/example-platform"],
    ["jobs/discovered/example-platform/resume.pdf", "jobs/discovered/example-platform/./resume.pdf"], ["bad\npath"],
])
def test_bad_manifest_is_refused(ws, paths):
    root, job = ws
    before = everything(root)
    with pytest.raises(service.Refused) as exc:
        P(root, rules.APPLICATION_SUBMIT, job=job["id"], manifest=paths, content="a")
    assert exc.value.code == rules.MANIFEST_INVALID
    assert everything(root) == before


def test_symlink_escaping_the_workspace_is_refused(ws, tmp_path):
    root, job = ws
    outside = tmp_path / "secret.txt"
    outside.write_text("x")
    (root / "link.pdf").symlink_to(outside)
    with pytest.raises(service.Refused):
        P(root, rules.APPLICATION_SUBMIT, job=job["id"], manifest=["link.pdf"], content="a")


def test_argument_combinations_are_usage_errors(ws):
    root, job = ws
    with pytest.raises(WorkspaceError) as exc:
        service.propose(root, kind=rules.LINKEDIN_INVITE, person_url=None, job_ref=job["id"], label="x", content_text="a",
                        manifest_paths=[], actor="agent:test")
    assert not isinstance(exc.value, service.Refused)
    with pytest.raises(WorkspaceError):
        service.propose(root, kind=rules.LINKEDIN_INVITE, person_url=URL, job_ref=None, label="x", content_text="a",
                        manifest_paths=["resume.pdf"], actor="agent:test")
    with pytest.raises(WorkspaceError):
        service.propose(root, kind="bogus", person_url=URL, job_ref=None, label="x", content_text="a", manifest_paths=[],
                        actor="agent:test")
    with pytest.raises(WorkspaceError):
        service.propose(root, kind=rules.APPLICATION_SUBMIT, person_url=URL, job_ref=job["id"], label="x", content_text="a",
                        manifest_paths=[], actor="agent:test")


# --- person identity ---------------------------------------------------------------------------------------------------------

VARIANTS = [
    "http://linkedin.com/in/alex-example", "https://linkedin.com/in/alex-example", "https://www.linkedin.com/in/alex-example",
    "https://in.linkedin.com/in/alex-example/", "https://www.linkedin.com/in/Alex-Example", "https://www.linkedin.com/in/ALEX-EXAMPLE/",
    "https://www.linkedin.com/in/alex-example?utm_source=x", "https://www.linkedin.com/in/alex-example#top",
]


def person_of(root, got):
    parsed, _ = item_mod.parse_bytes((root / got.rel).read_bytes(), got.rel)
    return parsed.target.id, parsed.target.url


def test_url_variants_map_to_one_person_id_across_kinds(ws):
    root, _ = ws
    seen = set()
    for n, url in enumerate(VARIANTS):
        kind = rules.LINKEDIN_MESSAGE if n % 2 else rules.LINKEDIN_INVITE
        seen.add(person_of(root, P(root, kind, url=url, content=f"text {n}")))
    assert len(seen) == 1 and seen.pop()[1] == URL
    assert person_of(root, P(root, url="https://www.linkedin.com/in/alex-other"))[0] not in {person_of(root, P(root))[0]}


def test_person_id_stable_across_supersession(ws):
    root, _ = ws
    first = P(root)
    pid = person_of(root, first)[0]
    for n in range(3):
        assert person_of(root, P(root, url=VARIANTS[n], content=f"v{n}"))[0] == pid


def test_variant_cannot_bypass_invite_or_in_flight_rules(ws):
    root, _ = ws
    first = P(root)
    approve(root, first.id), start(root, first.id)
    with pytest.raises(service.Refused) as exc:
        P(root, url="http://LINKEDIN.com/in/Alex-Example/?x=1", content="sneaky")
    assert exc.value.code == rules.IN_FLIGHT_CONFLICT
    consume(root, first.id)
    with pytest.raises(service.Refused) as exc:
        P(root, url="https://in.linkedin.com/in/alex-example/", content="sneaky")
    assert exc.value.code == rules.INVITE_CONFLICT


def test_same_url_under_two_person_ids_is_apr006(ws):
    root, _ = ws
    got = P(root)
    parsed, _ = item_mod.parse_bytes((root / got.rel).read_bytes(), got.rel)
    rogue = Item(ids.new_id("approval"), rules.LINKEDIN_MESSAGE, Target(ids.new_id("person"), "linkedin", "x", URL), "2026-10-06T09:00:00Z", "hi")
    (root / "approvals" / f"{rogue.id}.md").write_bytes(item_mod.to_bytes(rogue))
    assert "APR006" in codes(root)
    assert any("more than one person id" in i.message for i in issues(root, "APR006"))


def test_one_person_id_with_two_urls_is_apr006(ws):
    root, _ = ws
    got = P(root)
    pid = person_of(root, got)[0]
    rogue = Item(ids.new_id("approval"), rules.LINKEDIN_MESSAGE, Target(pid, "linkedin", "x", "https://www.linkedin.com/in/other"),
                 "2026-10-06T09:00:00Z", "hi")
    (root / "approvals" / f"{rogue.id}.md").write_bytes(item_mod.to_bytes(rogue))
    assert any("more than one profile URL" in i.message for i in issues(root, "APR006"))


# --- transaction ------------------------------------------------------------------------------------------------------------------

def test_failing_append_leaves_no_file_and_no_event(ws, monkeypatch):
    root, _ = ws
    before = everything(root)

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(ledger, "append_events", boom)
    with pytest.raises(OSError):
        P(root)
    monkeypatch.undo()
    assert not (root / "approvals").exists() or not list((root / "approvals").iterdir())
    assert {k: v for k, v in everything(root).items() if k != "<dirs>"} == {k: v for k, v in before.items() if k != "<dirs>"}
    assert P(root).id


def test_failing_append_keeps_an_earlier_item_intact(ws, monkeypatch):
    root, _ = ws
    first = P(root)
    before = everything(root)
    monkeypatch.setattr(ledger, "append_events", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    with pytest.raises(RuntimeError):
        P(root, content="second")
    monkeypatch.undo()
    assert everything(root) == before
    assert fold.fold_item(ledger.read_events(root), first.id, NOW).state.value == "pending"


def test_crash_between_file_and_event_leaves_an_orphan(ws, monkeypatch):
    root, _ = ws
    monkeypatch.setattr("careeros.core.memory.store.Transaction.rollback", lambda self: None)     # a kill runs no handler
    monkeypatch.setattr(ledger, "append_events", lambda *a, **k: (_ for _ in ()).throw(KeyboardInterrupt()))
    with pytest.raises(KeyboardInterrupt):
        P(root)
    monkeypatch.undo()
    (orphan,) = (root / "approvals").glob("*.md")
    assert [i.severity for i in issues(root, "APR002")] == ["warning"] and orphan.name in issues(root, "APR002")[0].path
    assert service.list_items(root, now=NOW) == []
    with pytest.raises(service.Refused):
        service.show(root, orphan.stem, now=NOW)
    assert P(root).id                                    # the next proposal is unaffected
    assert [v.id for v in service.list_items(root, now=NOW)] != []


# --- list and show ---------------------------------------------------------------------------------------------------------------

def test_list_and_show_fold_state_and_never_write(ws):
    root, job = ws
    a, b = P(root), P(root, rules.LINKEDIN_MESSAGE)
    approve(root, b.id)
    s = S(root, job)
    views = {v.id: v for v in service.list_items(root, now=NOW)}
    assert {v.id: v.state for v in views.values()} == {a.id: "pending", b.id: "approved", s.id: "pending"}
    assert [v.id for v in service.list_items(root, status="approved", now=NOW)] == [b.id]
    assert service.list_items(root, status="consumed", now=NOW) == []
    shown = service.show(root, b.id, now=NOW)
    assert shown.state == "approved" and shown.hash == b.hash and shown.item.content.startswith("Hi Alex")
    assert not shown.problems and shown.approved_at
    with pytest.raises(service.Refused) as exc:
        service.show(root, "apr_zzzzzzzzzz", now=NOW)
    assert exc.value.code == rules.NOT_FOUND


def test_expired_state_is_computed(ws):
    root, _ = ws
    got = P(root)
    approve(root, got.id)
    assert service.show(root, got.id, now="2026-10-06T10:00:00Z").state == "approved"
    assert service.show(root, got.id, now="2026-10-08T00:00:00Z").state == "expired"


def test_list_flags_unusable_items(ws):
    root, _ = ws
    missing, edited = P(root), P(root, url="https://www.linkedin.com/in/b")
    (root / missing.rel).unlink()
    (root / edited.rel).write_bytes((root / edited.rel).read_bytes() + b"more")
    views = {v.id: v for v in service.list_items(root, now=NOW)}
    assert list(views[missing.id].problems) == [rules.CONTENT_MISSING]
    assert list(views[edited.id].problems) == [rules.HASH_MISMATCH]
    assert all(not v.usable for v in views.values())


def test_backward_clock_is_reported_as_skew(ws, monkeypatch):
    root, _ = ws
    got = P(root)
    approve(root, got.id)
    early = "2026-10-05T00:00:00Z"
    assert service.show(root, got.id, now=early).clock_skew and service.list_items(root, now=early)[0].clock_skew
    assert rules.CLOCK_SKEW in service.show(root, got.id, now=early).problems
    monkeypatch.setattr("careeros.core.models.utc_now", lambda: early)
    out = json.loads(cli(root, "show", got.id, "--json").stdout)
    assert out["clock_skew"] is True and rules.CLOCK_SKEW in out["problems"]
    assert json.loads(cli(root, "list", "--json").stdout)["items"][0]["clock_skew"] is True
    assert not service.show(root, got.id, now=NOW).clock_skew


# --- CLI ----------------------------------------------------------------------------------------------------------------------------

def test_cli_propose_list_show_json(ws, tmp_path):
    root, job = ws
    content = tmp_path / "msg.txt"
    content.write_text("Hello <b>Alex</b>\x1b[31m\n", encoding="utf-8")
    result = cli(root, "propose", "--kind", "linkedin_invite", "--person-url", URL, "--label", "Alex", "--content-file", str(content), "--json")
    assert result.exit_code == 0, result.output
    out = json.loads(result.stdout)
    assert out["outcome"] == "proposed" and out["id"].startswith("apr_") and out["path"] == f"approvals/{out['id']}.md"
    assert out["state"] == "pending" and out["superseded"] == [] and len(out["hash"]) == 64
    listed = json.loads(cli(root, "list", "--json").stdout)
    assert [i["id"] for i in listed["items"]] == [out["id"]] and listed["items"][0]["state"] == "pending"
    shown = json.loads(cli(root, "show", out["id"], "--json").stdout)
    assert shown["content"] == "Hello <b>Alex</b>\x1b[31m\n" and shown["hash"] == out["hash"] and shown["state"] == "pending"
    text = cli(root, "show", out["id"])
    assert text.exit_code == 0 and out["hash"][:12] in text.stdout and "pending" in text.stdout and "\\x1b" in text.stdout
    assert "\x1b" not in text.stdout
    assert out["id"] in cli(root, "list").stdout
    assert json.loads(cli(root, "list", "--status", "approved", "--json").stdout)["items"] == []


def test_cli_submit_with_manifest_and_supersession(ws, tmp_path):
    root, job = ws
    content = tmp_path / "answers.txt"
    content.write_text("answers", encoding="utf-8")
    args = ["propose", "--kind", "application_submit", "--job", job["id"], "--content-file", str(content),
            "--manifest", job["resume"], "--manifest", job["cover"], "--json"]
    first = json.loads(cli(root, *args).stdout)
    second = json.loads(cli(root, *args).stdout)
    assert second["superseded"] == [first["id"]]


def test_cli_exit_codes(ws, tmp_path):
    root, job = ws
    content = tmp_path / "c.txt"
    content.write_text("hello", encoding="utf-8")
    base = ["propose", "--kind", "linkedin_invite", "--content-file", str(content), "--label", "A"]
    assert cli(root, *base, "--person-url", "https://example.com/in/x").exit_code == 1          # refused
    r = cli(root, *base, "--person-url", "https://example.com/in/x", "--json")
    assert r.exit_code == 1 and "error" in json.loads(r.stdout)                                   # json purity on failure
    assert cli(root, *base).exit_code == 2                                                         # no target: usage
    assert cli(root, *base, "--person-url", URL, "--job", job["id"]).exit_code == 2
    assert cli(root, "propose", "--kind", "nope", "--person-url", URL, "--content-file", str(content)).exit_code == 2
    assert cli(root, *base[:-4], "--person-url", URL, "--content-file", str(tmp_path / "missing.txt")).exit_code == 2
    assert cli(root, "show", "apr_zzzzzzzzzz").exit_code == 1
    assert cli(root, "list", "--status", "bogus").exit_code == 2
    assert runner.invoke(app, ["approval", "list", "--workspace", str(tmp_path / "nowhere")]).exit_code == 2
    empty = tmp_path / "e.txt"
    empty.write_text("", encoding="utf-8")
    assert cli(root, *base[:-4], "--content-file", str(empty), "--person-url", URL).exit_code == 1


def test_cli_ledger_failure_is_exit_1_and_clean(ws, tmp_path, monkeypatch):
    root, _ = ws
    content = tmp_path / "c.txt"
    content.write_text("hello", encoding="utf-8")
    monkeypatch.setattr(ledger, "append_events", lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")))
    r = cli(root, "propose", "--kind", "linkedin_invite", "--person-url", URL, "--label", "A", "--content-file", str(content), "--json")
    assert r.exit_code == 1 and "disk full" in json.loads(r.stdout)["error"]
    assert not list((root / "approvals").glob("*.md")) if (root / "approvals").exists() else True


# --- validate -------------------------------------------------------------------------------------------------------------------------

def test_workspace_without_approvals_is_unchanged(ws):
    root, _ = ws
    assert codes(root) == []
    assert not (root / "approvals").exists()


def test_clean_workspace_has_no_apr_issues(ws):
    root, job = ws
    P(root), P(root, rules.LINKEDIN_MESSAGE), S(root, job)
    assert codes(root) == []


def test_apr001_unreadable_item(ws):
    root, _ = ws
    got = P(root)
    assert "APR001" not in codes(root)
    (root / got.rel).write_text("---\nschema: 1\nbogus: [\n---\nx", encoding="utf-8")
    assert "APR001" in codes(root)


def test_apr002_orphan_file(ws):
    root, _ = ws
    got = P(root)
    assert "APR002" not in codes(root)
    twin = Item(ids.new_id("approval"), rules.LINKEDIN_INVITE, Target(ids.new_id("person"), "linkedin", "x", "https://www.linkedin.com/in/zed"),
                "2026-10-06T09:00:00Z", "hi")
    (root / "approvals" / f"{twin.id}.md").write_bytes(item_mod.to_bytes(twin))
    (found,) = issues(root, "APR002")
    assert found.severity == "warning" and twin.id in found.path and got.id not in found.path


def test_apr003_missing_file(ws):
    root, job = ws
    pending, consumed = P(root), P(root, rules.LINKEDIN_MESSAGE)
    approve(root, consumed.id), start(root, consumed.id), consume(root, consumed.id)
    assert "APR003" not in codes(root)
    (root / pending.rel).unlink()
    (root / consumed.rel).unlink()
    (found,) = issues(root, "APR003")
    assert found.severity == "error" and pending.id in found.path + found.message and consumed.id not in found.path + found.message


def test_apr004_edited_file(ws):
    root, _ = ws
    got = P(root)
    assert "APR004" not in codes(root)
    (root / got.rel).write_bytes((root / got.rel).read_bytes().replace(b"small world", b"big world"))
    (found,) = issues(root, "APR004")
    assert found.severity == "error"
    assert list(service.show(root, got.id, now=NOW).problems) == [rules.HASH_MISMATCH]


def test_apr005_sequences_and_unknown_types(ws):
    root, _ = ws
    first, second = P(root), P(root, rules.LINKEDIN_MESSAGE)
    assert "APR005" not in codes(root)
    consume(root, first.id)                                              # consumed without started
    ev(root, "approval.bogus", second.id)                                # unknown type
    ev(root, rules.EV_APPROVED, None)                                    # names no item
    ev(root, rules.EV_APPROVED, "apr_zzzzzzzzzz")                        # before any proposal
    found = issues(root, "APR005")
    assert len(found) >= 4 and all(i.severity == "error" for i in found)


def test_apr005_audit_only_rejections_are_ignored(ws):
    root, _ = ws
    got = P(root)
    ev(root, rules.EV_REJECTED, got.id, reason="expired")
    ev(root, rules.EV_REJECTED, None, reason="wrong_state")
    assert "APR005" not in codes(root)


def test_apr005_unparseable_proposal_event(ws):
    root, _ = ws
    got = P(root)
    ledger.append_event(root, type="approval.proposed", actor="agent:x", entity=got.id, action="something else", artifacts=[got.rel])
    assert "APR005" in codes(root)


def test_apr005_event_kind_target_cross_check(ws):
    root, job = ws
    # the file says an invite to a person; the proposal event claims a submission for the job (a hand-edited action)
    real = Item(ids.new_id("approval"), rules.LINKEDIN_INVITE, Target(ids.new_id("person"), "linkedin", "x", URL), "2026-10-06T09:00:00Z", "hi")
    rel = f"approvals/{real.id}.md"
    (root / "approvals").mkdir()
    (root / rel).write_bytes(item_mod.to_bytes(real))
    ledger.append_event(root, type="approval.proposed", actor="agent:x", entity=real.id, source="cli",
                        **fold.proposal_event_fields(rules.APPLICATION_SUBMIT, job["id"], rel, item_mod.action_hash(real)))
    assert "APR005" in codes(root)
    folded = fold.fold_item(ledger.read_events(root), real.id, NOW)
    assert (folded.kind, folded.target_id) == (rules.APPLICATION_SUBMIT, job["id"])      # what the event claims, now flagged


def test_apr006_job_not_found(ws):
    root, job = ws
    got = S(root, job)
    assert "APR006" not in codes(root)
    (job["path"]).unlink()
    assert any(i.code == "APR006" for i in validate_all(root, NOW))
    assert got.id


def test_apr007_scope(ws):
    root, job = ws
    pending = S(root, job)         # one submit item per job (a newer proposal would supersede): four states on four jobs
    root2_jobs = [add_job(root, f"j{n}") for n in range(3)]
    items = {"pending": pending}
    for state_name, j in zip(("approved", "in_flight", "consumed"), root2_jobs):
        (j["path"].parent / "doc.pdf").write_bytes(b"d")
        items[state_name] = P(root, rules.APPLICATION_SUBMIT, job=j["id"], manifest=[f"jobs/discovered/{j['slug']}/doc.pdf"], content="a")
        j["doc"] = j["path"].parent / "doc.pdf"
    approve(root, items["approved"].id)
    approve(root, items["in_flight"].id), start(root, items["in_flight"].id)
    approve(root, items["consumed"].id), start(root, items["consumed"].id), consume(root, items["consumed"].id)
    assert "APR007" not in codes(root)
    for j in root2_jobs:
        j["doc"].write_bytes(b"changed")
    (job["path"].parent / "resume.pdf").write_bytes(b"changed")           # the pending item's file too
    found = issues(root, "APR007")
    assert sorted(i.path for i in found) == sorted(f"approvals/{items[k].id}.md" for k in ("approved", "in_flight"))
    assert all(i.severity == "warning" for i in found)
    root2_jobs[0]["doc"].unlink()
    assert len(issues(root, "APR007")) == 2                               # missing is reported like changed


def test_apr008_in_flight(ws):
    root, _ = ws
    got = P(root)
    approve(root, got.id)
    assert "APR008" not in codes(root)
    start(root, got.id)
    (found,) = issues(root, "APR008")
    assert found.severity == "warning" and "resolve" in found.fix
    consume(root, got.id)
    assert "APR008" not in codes(root)


def test_apr100_stale_pending(ws):
    root, _ = ws
    got = P(root)
    assert "APR100" not in codes(root, now="2026-10-12T00:00:00Z")
    (found,) = issues(root, "APR100", now="2026-10-14T00:00:00Z")
    assert found.severity == "info"
    approve(root, got.id)
    assert "APR100" not in codes(root, now="2026-10-30T00:00:00Z")


def test_corrupt_ledger_line_never_crashes_validation(ws):
    root, _ = ws
    P(root)
    with (root / "ledger.jsonl").open("ab") as handle:
        handle.write(b"{not json\n[]\n42\n")
    assert isinstance(validate_all(root, NOW), list)


# --- read-only proof ---------------------------------------------------------------------------------------------------------------

def test_validate_list_and_show_change_nothing(ws):
    root, job = ws
    a, b, c = P(root), P(root, rules.LINKEDIN_MESSAGE), S(root, job)
    approve(root, b.id), start(root, b.id)
    (root / a.rel).write_bytes(b"corrupt")
    (root / c.rel).unlink()
    consume(root, b.id)
    stray = root / "approvals" / "apr_strayfile1.md"
    stray.write_text("junk")
    before = everything(root)
    before_state = state(root)
    validate_all(root, NOW)
    service.list_items(root, now=NOW), service.show(root, b.id, now=NOW)
    for args in (("list",), ("list", "--json"), ("show", b.id), ("show", b.id, "--json"), ("show", a.id)):
        cli(root, *args)
    runner.invoke(app, ["validate", "--workspace", str(root)])
    assert everything(root) == before and state(root) == before_state


# --- reserved prefix ------------------------------------------------------------------------------------------------------------------

def test_approval_prefix_is_reserved(ws):
    root, _ = ws
    assert ledger.is_reserved("approval.x") and ledger.is_reserved("approval.proposed")
    r = runner.invoke(app, ["ledger", "append", "--type", "approval.x", "--action", "forge", "--workspace", str(root)])
    assert r.exit_code == 2 and "reserved" in r.output
    assert approval_events(root) == []


def test_rejected_needs_a_reason(ws):
    root, _ = ws
    with pytest.raises(ledger.LedgerError):
        ledger.append_event(root, type="approval.rejected", actor="agent:x", action="refused")
    ledger.append_event(root, type="approval.rejected", actor="agent:x", action="refused", reason="expired")
