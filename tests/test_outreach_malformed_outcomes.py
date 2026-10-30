"""A malformed outcome event (an unsupported or missing `source`, an invalid closed `reason`) is reported as OUT005 and
changes no derived state, end to end through the CLI. The approvals-side `person_closed` gate deliberately counts ANY
`outreach.closed` event, so a malformed close never changes the displayed state but always blocks sending.
Invented data only; the malformed events are appended by hand the way a hand edit or an old writer would."""

from __future__ import annotations

import json

import pytest
from helpers import ALEX, SAM, T0, OutreachW as W, make_workspace, shift

from careeros.cli import _util
from careeros.core.approvals import people


@pytest.fixture
def w(tmp_path, monkeypatch) -> W:
    monkeypatch.delenv("CAREEROS_WORKSPACE", raising=False)
    return W(make_workspace(tmp_path / "ws"), monkeypatch)


def out005(w: W) -> list[dict]:
    body = json.loads(w.run("validate", "--json").stdout)
    return [i for i in body["issues"] if i["code"] == "OUT005"]


def approve(w: W, item: str) -> None:
    w.mp.setattr(_util, "is_interactive", lambda: True)
    assert w.run("approve", item, input="a\n").exit_code == 0
    w.mp.setattr(_util, "is_interactive", lambda: False)


def malformed(kind: str, per: str, **bad) -> dict:
    spec = {"type": f"outreach.{kind}", "actor": "user", "entity": per, "source": "user", "action": f"{kind} {per}"}
    if kind == "closed":
        spec["reason"] = "declined"
    return {**spec, **bad}


CASES = [
    ("accepted", {"source": "carrier-pigeon"}), ("accepted", {"source": None}),
    ("replied", {"source": "carrier-pigeon"}), ("replied", {"source": None}),
    ("closed", {"source": "carrier-pigeon"}), ("closed", {"source": None}),
    ("closed", {"reason": "because"}),     # a missing reason cannot be appended at all (the ledger needs one); the fold tests cover it
]


@pytest.mark.parametrize("kind,bad", CASES, ids=[f"{k}-{'-'.join(map(str, b.values()))}" for k, b in CASES])
def test_a_malformed_outcome_changes_nothing_but_validate(w: W, kind: str, bad: dict) -> None:
    w.person(ALEX)
    per, _ = w.invite_done(ALEX)
    w.now = shift(T0, days=8)
    queue, person, nxt = w.ok("outreach", "queue"), w.ok("outreach", "person", "show", per), w.ok("outreach", "next")
    assert [i["entity_id"] for i in queue["items"]] == [per] and out005(w) == []
    w.append(malformed(kind, per, **bad))
    assert w.ok("outreach", "queue") == queue                       # still due
    assert w.ok("outreach", "person", "show", per) == person        # same label and flags
    assert w.ok("outreach", "next") == nxt
    (issue,) = out005(w)
    assert issue["severity"] == "error" and f"outreach.{kind}:" in issue["message"]
    # `outreach record` fails closed on ANY closed event (the same gate as sending), so after a malformed close it refuses
    # with wrong_state; after a malformed accepted or replied it records normally and the person is not marked twice
    expected = {"accepted": "connected", "replied": "replied", "closed": "closed"}[kind]
    if kind == "closed":
        w.refused("outreach", "record", per, "--accepted", code="wrong_state")
        w.outcome(per, "closed")                                        # a later valid event applies (hand-appended)
    else:
        assert w.ok("outreach", "record", per, f"--{kind}")["label"] == expected
    assert w.ok("outreach", "person", "show", per)["label"] == expected
    assert w.ok("outreach", "queue")["items"] == []
    assert len(out005(w)) == 1


@pytest.mark.parametrize("bad", [{"reason": "because"}, {"source": "carrier-pigeon"}, {"source": None}])
@pytest.mark.parametrize("kind", ["linkedin_invite", "linkedin_message"])
def test_a_malformed_close_never_changes_state_but_always_blocks_sending(w: W, kind: str, bad: dict) -> None:
    per = w.person(SAM)["id"]
    item = w.propose(kind, SAM)
    approve(w, item["id"])
    before = w.ok("outreach", "person", "show", per)
    w.append(malformed("closed", per, **bad))
    assert people.closed_people(w.events())[0] == {per}              # the approvals gate counts ANY closed event
    assert w.ok("outreach", "person", "show", per) == before          # the displayed state does not
    assert w.ok("outreach", "person", "show", per)["label"] != "closed"
    w.refused("approval", "begin", item["id"], code="person_closed")
    f = w.root / "draft.txt"
    w.refused("approval", "propose", "--kind", kind, "--person-url", SAM, "--label", "Sam Example",
              "--content-file", str(f), code="person_closed")
    assert len(out005(w)) == 1
