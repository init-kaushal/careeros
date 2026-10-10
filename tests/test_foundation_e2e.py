"""One job from a legacy workspace to an offer, through the CLI, with an audit at every step."""

import json
from pathlib import Path

import pytest
from helpers import make_legacy_workspace
from typer.testing import CliRunner

from careeros.cli import _util
from careeros.cli.main import app
from careeros.core import ledger
from careeros.core import workspace as ws
from careeros.core.models import State

runner = CliRunner()


def run(root: Path, *args: str, input: str | None = None):
    return runner.invoke(app, [*args, "--workspace", str(root)], input=input)


def test_legacy_workspace_to_offer_with_audit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "ws"
    make_legacy_workspace(root)

    # 1. migrate, then the workspace validates with nothing to report
    assert run(root, "migrate", "--yes").exit_code == 0
    assert run(root, "validate", "--strict").exit_code == 0

    job_path = root / "jobs" / "discovered" / "acme-backend-engineer" / "job.md"
    job_id = ws.read_job(job_path).id

    # 2. walk the pre-application stages
    for state in ("EVALUATED", "SHORTLISTED", "RESEARCHED", "PREPARING", "READY_TO_APPLY", "APPROVAL_REQUIRED"):
        result = run(root, "transition", job_id, "--to", state, "--actor", "agent:claude")
        assert result.exit_code == 0, result.output

    # 3. an agent cannot jump to APPLIED, and the refusal is on the record
    refused = run(root, "transition", job_id, "--to", "APPLIED", "--actor", "agent:claude")
    assert refused.exit_code == 1 and "careeros approve" in refused.output
    assert ledger.read_events(root)[-1]["type"] == "job.transition_rejected"

    # 4. without a terminal the approval command refuses; with one, the user's decision is recorded
    assert run(root, "approve", job_id).exit_code == 2
    monkeypatch.setattr(_util, "is_interactive", lambda: True)
    assert run(root, "approve", job_id, input="y\n").exit_code == 0
    monkeypatch.undo()
    assert run(root, "transition", job_id, "--to", "APPLIED", "--actor", "agent:claude").exit_code == 0

    # 5. the interview stages and the offer; a retry changes nothing
    for state in ("RECRUITER_REPLIED", "SCREEN", "TECHNICAL", "HM", "FINAL", "OFFER"):
        assert run(root, "transition", job_id, "--to", state, "--actor", "agent:claude").exit_code == 0
    events_before_retry = len(ledger.read_events(root))
    assert "nothing to do" in run(root, "transition", job_id, "--to", "OFFER").output
    assert len(ledger.read_events(root)) == events_before_retry

    # 6. file, pipeline and ledger all agree, and the chain verifies
    assert ws.read_job(job_path).status is State.OFFER
    assert "- **Status:** offer" in job_path.read_text()
    assert "- [✓] **Acme**" in (root / "jobs" / "pipeline.md").read_text()
    assert run(root, "validate", "--strict").exit_code == 0
    assert run(root, "ledger", "verify").exit_code == 0
    status = json.loads(run(root, "status", "--json").stdout)
    assert status["jobs"]["OFFER"] == 2  # the walked job and the legacy job that was already at OFFER

    # 7. editing history is detected
    path = root / "ledger.jsonl"
    lines = path.read_text().splitlines()
    lines[3] = lines[3].replace("job.imported", "job.imported ")
    path.write_text("\n".join(lines) + "\n")
    assert run(root, "ledger", "verify").exit_code == 1
