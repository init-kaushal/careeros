"""One realistic pass through the whole feature: import, confirm, check, retire, audit."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from helpers import make_workspace
from typer.testing import CliRunner

from careeros.cli import _util
from careeros.cli.main import app
from careeros.core import ledger
from careeros.core.memory import store

runner = CliRunner()
FIXTURE = Path(__file__).parent / "fixtures" / "resume_sample.md"


def run(root: Path, *args: str, input: str | None = None):
    return runner.invoke(app, [*args, "--workspace", str(root)], input=input)


def test_the_whole_flow(tmp_path: Path, clock, monkeypatch: pytest.MonkeyPatch) -> None:
    root = make_workspace(tmp_path / "ws")
    shutil.copy(FIXTURE, root / "resume.md")
    resume_before = (root / "resume.md").read_bytes()

    # 1. import (dry run, then apply) and a second import changes nothing
    assert run(root, "memory", "import").exit_code == 0 and not (root / "career").exists()
    assert run(root, "memory", "import", "--apply", "--yes").exit_code == 0
    assert (root / "resume.md").read_bytes() == resume_before
    assert "Nothing to import" in run(root, "memory", "import", "--apply", "--yes").output

    # 2. a faithful draft passes, with its support reported as claimed
    faithful = "At Acme Corp I reduced AWS costs by 35% using Kafka-based pipelines."
    passed = run(root, "check", "-", "--record", input=faithful)
    assert passed.exit_code == 0 and "claimed rather than confirmed" in passed.output

    # 3. the user confirms the two facts it rests on; --require-confirmed now passes
    assert run(root, "check", "-", "--require-confirmed", input=faithful).exit_code == 1
    monkeypatch.setattr(_util, "is_interactive", lambda: True)
    career = store.load_career(root)
    cost = next(f for f in career.facts if "35%" in str(f.get("text")))
    acme = career.by_id()[cost.get("parent")]
    for fact in (cost, acme):
        assert run(root, "memory", "confirm", fact.id, input="y\n").exit_code == 0
    monkeypatch.setattr(_util, "is_interactive", lambda: False)
    assert run(root, "check", "-", input=faithful).exit_code == 0

    # 4. a tampered draft fails with the expected findings and a non-zero exit
    tampered = json.loads(run(root, "check", "-", "--json", input=(
        "Reduced AWS costs by 50% using Kafka. I worked at FakeCorp. I reduced costs by 60% with Kafka. I hold an MBA from Fake University."
    )).stdout)
    assert tampered["ok"] is False
    found = {f["code"] for f in tampered["review_required"]}
    assert {"CHK001", "CHK004", "CHK010", "CHK005"} <= found

    # 5. retiring the fact it relied on breaks the draft that used it
    monkeypatch.setattr(_util, "is_interactive", lambda: True)
    assert run(root, "memory", "retire", cost.id, "--reason", "I am not sure of this number", input="y\n").exit_code == 0
    monkeypatch.setattr(_util, "is_interactive", lambda: False)
    assert run(root, "check", "-", input=faithful).exit_code == 1

    # 6. the audit trail and the workspace are intact
    types = [e["type"] for e in ledger.read_events(root)]
    assert types.count("memory.imported") == 1 and "memory.fact_confirmed" in types and "memory.fact_retired" in types
    assert types.count("draft.checked") == 1
    assert ledger.verify_chain(root) == []
    assert run(root, "validate").exit_code == 0
