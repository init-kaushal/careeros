"""One end-to-end walk through the outreach and follow-up state (sub-project 4a) on invented data, through the real CLI:
a target and people (URL variants resolve to one id), `next` ranked and limited to the remaining cap, an invite from
proposal to `done`, the cap at `begin`, the follow-up `queue`, outcomes, `warm`, `person_closed`, an invalid cap setting
failing closed, a hand-edited `found_in`, a byte-identical workspace after every read-only command, event counts,
rejection dedupe, the ledger chain, isolation, and one real-process race for the cap.

Time comes from ONE source: `OutreachW` patches `models.utc_now`, and the walk moves it explicitly. `approve` and
`resolve` need an interactive terminal; the walk plays it by patching `_util.is_interactive`, the same seam the other
approval tests use. The real terminal path is not exercised here. The race is the only part on the real clock."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pytest
from helpers import (ALEX, KIM, SAM, T0, Cmd, OutreachW, add_job, full_state, make_workspace, race_many, shift,
                     snapshot)

from careeros.core import ledger, models
from careeros.core.approvals import rules
from careeros.core.approvals import service as aservice

LEE = "https://www.linkedin.com/in/lee-example"
PAT = "https://www.linkedin.com/in/pat-example"
ALEX_VARIANTS = [
    "https://www.linkedin.com/in/Alex-Example",
    "https://www.linkedin.com/in/alex-example/",
    "https://in.linkedin.com/in/alex-example",
    "https://www.linkedin.com/in/alex-example?trk=public",
]
EXPECTED_COUNTS = {
    "outreach.target_added": 2, "outreach.target_approved": 2, "outreach.person_added": 3, "outreach.accepted": 1,
    "outreach.closed": 1, "outreach.rejected": 1, "approval.proposed": 6, "approval.approved": 5, "approval.started": 2,
    "approval.consumed": 1, "approval.rejected": 4,
}
EXPECTED_REJECTIONS = ["cap_config_invalid", "cap_reached", "cap_reached", "person_closed"]
READ_ONLY = (("validate",), ("validate", "--json"), ("outreach", "queue"), ("outreach", "queue", "--json"),
             ("outreach", "cap"), ("outreach", "cap", "--json"), ("outreach", "next"), ("outreach", "next", "--json"),
             ("outreach", "warm"), ("outreach", "warm", "--json"), ("outreach", "person", "list"),
             ("outreach", "person", "list", "--json"), ("outreach", "target", "list"),
             ("outreach", "target", "list", "--json"))


def set_cap(root: Path, line: str | None) -> None:
    (root / "profile.md").write_text("# Profile\n\n## Outreach\n" + (f"{line}\n" if line else ""), encoding="utf-8")


def approve(w: OutreachW, item: str) -> None:
    from careeros.cli import _util
    w.mp.setattr(_util, "is_interactive", lambda: True)
    res = w.run("approve", item, input="a\n")
    w.mp.setattr(_util, "is_interactive", lambda: False)
    assert res.exit_code == 0, res.output


def approved(w: OutreachW, kind: str, url: str) -> dict:
    item = w.propose(kind, url)
    approve(w, item["id"])
    return item


def exit_and_body(w: OutreachW, *args: str) -> tuple[int, dict]:
    res = w.run(*args, "--json")
    return res.exit_code, json.loads(res.stdout)


def read_only_unchanged(w: OutreachW) -> None:
    """Run every read-only command; the WHOLE workspace (ledger, `.careeros/`, mtimes) must stay as it was."""
    before = full_state(w.root, lock=True)
    for args in READ_ONLY:
        res = w.run(*args)
        assert res.exit_code in (0, 1), (args, res.output)
        assert full_state(w.root, lock=True) == before, args
    first = w.ok("outreach", "person", "list")["people"][0]["id"]
    tgt = w.ok("outreach", "target", "list")["targets"][0]["slug"]
    for args in (("outreach", "person", "show", first), ("outreach", "target", "show", tgt)):
        assert w.run(*args).exit_code == 0
        assert w.run(*args, "--json").exit_code == 0
        assert full_state(w.root, lock=True) == before, args


def test_outreach_end_to_end(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CAREEROS_WORKSPACE", raising=False)
    root = make_workspace(tmp_path / "ws")
    w = OutreachW(root, monkeypatch)                                     # the clock is patched before any event is stamped
    other = add_job(root, "other-job", company="Other Co")               # a different job, never touched
    (other["path"].parent / "jd.md").write_text("# Another invented posting\n", encoding="utf-8")
    (root / "career").mkdir()
    (root / "career" / "facts.md").write_text("# Invented career memory\n", encoding="utf-8")
    guarded = {k: v for k, v in snapshot(root).items() if k.startswith("career/") or "other-job" in k}

    # (a) a target and two people (URL variants resolve to one id); approve the target; next ranks and limits to the cap
    w.target()
    assert w.approve_target("example-corp")["decision"] == "approved"
    sam = w.person(SAM, name="Sam Example", role="recruiter", found_in=["target:example-corp"])["id"]
    alex = w.person(ALEX, name="Alex Example", role="hiring_manager", found_in=["target:example-corp"])["id"]
    for variant in ALEX_VARIANTS:
        assert w.refused("outreach", "person", "add", "--url", variant, "--name", "A", "--company", "C", "--role", "peer",
                         code="duplicate")["error"].startswith(alex)
    assert len(list((root / "people").glob("*.md"))) == 2
    nxt = w.ok("outreach", "next")
    assert [c["person_id"] for c in nxt["contacts"]] == [sam, alex] and nxt["remaining"] == nxt["cap"] == 15
    set_cap(root, "Weekly invite cap: 1")
    nxt = w.ok("outreach", "next")
    assert [c["person_id"] for c in nxt["contacts"]] == [sam] and nxt["remaining"] == 1 and nxt["cap"] == 1

    # (b) propose, approve, begin and done an invite (cap count 1); a second begin at the cap is refused cap_reached
    assert w.invite_done(SAM)[0] == sam
    cap = w.ok("outreach", "cap")
    assert (cap["cap"], cap["count"], cap["in_flight"], cap["consumed_in_window"], cap["remaining"]) == (1, 1, 0, 1, 0)
    second = approved(w, "linkedin_invite", ALEX_VARIANTS[0])                         # a URL variant: still Alex
    assert second["target"] == alex
    w.refused("approval", "begin", second["id"], code="cap_reached")
    w.refused("approval", "begin", second["id"], code="cap_reached")                 # a repeat is not recorded twice
    assert w.ok("outreach", "next")["contacts"] == []

    # (c) 7 days on: the invite leaves the cap window; a target-only person with no saved job is not queued (pre-opening);
    # a saved job at the company queues it; a leftover in_flight invite older than 7 days still counts
    w.now = shift(T0, days=7)
    assert w.ok("outreach", "cap")["count"] == 0
    assert w.ok("outreach", "queue")["items"] == []
    ec = add_job(root, "example-corp-backend", company="Example Corp")
    (ec["path"].parent / "jd.md").write_text("# An invented posting\n", encoding="utf-8")
    ec_guard = {k: v for k, v in snapshot(root).items() if "example-corp-backend" in k}
    (item,) = w.ok("outreach", "queue")["items"]
    assert (item["kind"], item["entity_id"], item["days_overdue"]) == ("invite", sam, 0)
    kim = approved(w, "linkedin_invite", KIM)
    w.ok("approval", "begin", kim["id"])                                             # in flight, never done
    w.now = shift(T0, days=16)
    cap = w.ok("outreach", "cap")
    assert (cap["count"], cap["in_flight"], cap["consumed_in_window"], cap["remaining"]) == (1, 1, 0, 0)
    lee = approved(w, "linkedin_invite", LEE)
    w.refused("approval", "begin", lee["id"], code="cap_reached")

    # (d) record --accepted removes it from the queue; the saved job makes warm list the person
    assert [i["entity_id"] for i in w.ok("outreach", "queue")["items"]] == [sam]
    assert w.ok("outreach", "record", sam, "--accepted")["label"] == "connected"
    assert w.ok("outreach", "queue")["items"] == []
    (warm,) = w.ok("outreach", "warm")["contacts"]
    assert (warm["person_id"], warm["label"], warm["job_ids"]) == (sam, "connected", [ec["id"]])

    # (e) record --closed after an approval: begin and propose both refuse person_closed
    msg = approved(w, "linkedin_message", ALEX)
    assert w.ok("outreach", "record", alex, "--closed", "--reason", "declined")["label"] == "closed"
    w.refused("approval", "begin", msg["id"], code="person_closed")
    w.refused("approval", "begin", msg["id"], code="person_closed")                  # a repeat is not recorded twice
    before = w.ledger_bytes()
    w.refused("approval", "propose", "--kind", "linkedin_message", "--person-url", ALEX, "--label", "Alex Example",
              "--content-file", str(root / "draft.txt"), code="person_closed")
    assert w.ledger_bytes() == before                                                # a refused propose writes nothing
    assert w.ok("outreach", "person", "show", alex)["label"] == "closed"

    # (f) an invalid cap setting fails closed everywhere (cap, next, begin; validate OUT007)
    set_cap(root, "Weekly invite cap: abc")
    for cmd in (("outreach", "cap"), ("outreach", "next")):
        code, body = exit_and_body(w, *cmd)
        assert (code, body["code"]) == (1, "cap_config_invalid"), (cmd, body)
    w.refused("approval", "begin", lee["id"], code="cap_config_invalid")
    code, body = exit_and_body(w, "validate")
    assert any(i["code"] == "OUT007" for i in body["issues"]), body
    set_cap(root, "Weekly invite cap: 5")

    # (g) a hand-edited found_in changes next, but not identity, the cap or the closed state
    second_target = w.target("second-co", company="Second Co")["id"]
    w.approve_target("second-co")
    pat = w.person(PAT, name="Pat Example", company="Second Co", role="recruiter")["id"]
    assert pat not in [c["person_id"] for c in w.ok("outreach", "next")["contacts"]]
    cap_before, closed_before = w.ok("outreach", "cap"), w.ok("outreach", "person", "show", alex)["state"]
    path = root / "people" / f"{pat}.md"
    text = path.read_text(encoding="utf-8")
    assert "found_in: []" in text
    path.write_text(text.replace("found_in: []", f"found_in:\n- kind: target\n  id: {second_target}"), encoding="utf-8")
    assert pat in [c["person_id"] for c in w.ok("outreach", "next")["contacts"]]
    assert w.ok("outreach", "person", "show", pat)["id"] == pat
    assert w.propose("linkedin_message", PAT)["target"] == pat                      # identity still comes from the URL
    assert w.ok("outreach", "cap") == cap_before
    assert w.ok("outreach", "person", "show", alex)["state"] == closed_before
    w.refused("approval", "begin", msg["id"], code="person_closed")
    # the same hand edit on the CLOSED person (alex) and on the person whose invite was consumed (sam)
    for who in (alex, sam):
        path = root / "people" / f"{who}.md"
        text = path.read_text(encoding="utf-8")
        assert "found_in:\n" in text
        path.write_text(text.replace("found_in:\n", f"found_in:\n- kind: target\n  id: {second_target}\n", 1), encoding="utf-8")
    assert w.ok("outreach", "person", "show", alex)["state"] == closed_before
    assert w.ok("outreach", "person", "show", sam)["id"] == sam
    w.refused("approval", "begin", msg["id"], code="person_closed")
    cap_after = w.ok("outreach", "cap")
    keys = ("cap", "count", "in_flight", "consumed_in_window", "remaining")
    assert [cap_after[k] for k in keys] == [cap_before[k] for k in keys] == [5, 1, 1, 0, 4]
    assert alex not in [c["person_id"] for c in w.ok("outreach", "next")["contacts"]]     # a closed person is never offered

    # (h) the read-only commands leave the WHOLE workspace byte-identical (ledger and .careeros/ included)
    read_only_unchanged(w)

    # (i) events per type, deduped rejections, an intact chain
    counts = Counter(e["type"] for e in w.events() if e["type"].split(".")[0] in ("outreach", "approval"))
    assert counts == EXPECTED_COUNTS, counts
    rejects = [(e["entity"], e.get("reason")) for e in w.events(rules.EV_REJECTED)]
    assert len(rejects) == len(set(rejects))                                         # no identical repeat was recorded
    assert sorted(r.split(":")[0] for _, r in rejects) == EXPECTED_REJECTIONS
    assert [e["reason"].split(":")[0] for e in w.events("outreach.rejected")] == ["duplicate"]   # four attempts, one record
    assert not ledger.verify_chain(root)

    # (j) isolation: career/, the other job and every jd.md are untouched
    now_guarded = {k: v for k, v in snapshot(root).items() if k.startswith("career/") or "other-job" in k}
    assert now_guarded == guarded
    assert {k: v for k, v in snapshot(root).items() if "example-corp-backend" in k} == ec_guard
    assert not [e for e in w.events() if e.get("entity") in (other["id"], ec["id"]) and e["type"] != "job.imported"]



def test_a_real_process_race_for_the_cap_has_exactly_one_claim(tmp_path: Path) -> None:
    """(k) Three `begin`s race for the single slot left under a cap of 2: exactly one proceeds, two get `cap_reached`,
    and the ledger holds exactly two started events. Real processes, a barrier, the real clock."""
    root = make_workspace(tmp_path / "ws")
    set_cap(root, "Weekly invite cap: 2")
    urls = [f"https://www.linkedin.com/in/race-{i}" for i in range(4)]

    def real_approved(url: str) -> str:
        got = aservice.propose(root, kind=rules.LINKEDIN_INVITE, person_url=url, job_ref=None, label="Someone, Example Corp",
                               content_text="Hi, an invented note.\n", manifest_paths=[], actor="agent:test")
        aservice.decide(root, got.id, confirm=lambda text: "approve", clock=models.utc_now)
        return got.id

    aservice.begin(root, real_approved(urls[0]), actor="agent:test", clock=models.utc_now)          # one slot left
    items = [real_approved(u) for u in urls[1:]]
    cmds = [Cmd("approval", "begin", i, "--json", "--workspace", str(root)) for i in items]
    (results,) = race_many(tmp_path, [cmds], widen=True)
    assert sorted(d.get("code", "ok") if code else "ok" for code, d in results) == ["cap_reached", "cap_reached", "ok"]
    assert len([e for e in ledger.read_events(root) if e["type"] == rules.EV_STARTED]) == 2
    assert not ledger.verify_chain(root)
