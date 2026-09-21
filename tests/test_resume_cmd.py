import json
from unittest.mock import patch

from typer.testing import CliRunner

from careeros.cli.main import app
from careeros.core.models import Evidence, Skill, Skills
from careeros.skills.resume_ingest import IngestResult
from careeros.storage.filesystem import LocalFilesystemStorage
from careeros.workspace.manager import init_workspace

runner = CliRunner()


def _workspace(tmp_path):
    storage = LocalFilesystemStorage(str(tmp_path))
    init_workspace(storage)
    storage.atomic_write("resumes/master.md", b"SKILLS\nPython, Go\n")
    return str(tmp_path), storage


def _result(names=("Python",), dropped=()):
    skills = [
        Skill(
            name=n,
            source="resumes/master.md",
            evidence=Evidence(quote="Python, Go", line=2, source_file="resumes/master.md"),
        )
        for n in names
    ]
    return IngestResult(skills=Skills(skills=skills), dropped=tuple(dropped))


def test_ingest_with_no_path_reads_master(tmp_path):
    ws, storage = _workspace(tmp_path)
    with patch("careeros.cli.resume_cmd.ingest_resume", return_value=_result()) as ing:
        result = runner.invoke(app, ["resume", "ingest", "--workspace", ws])
    assert result.exit_code == 0
    assert ing.call_args.args[0] == "SKILLS\nPython, Go\n"
    assert ing.call_args.args[1] == "resumes/master.md"


def test_ingest_writes_skills_with_evidence(tmp_path):
    ws, storage = _workspace(tmp_path)
    with patch("careeros.cli.resume_cmd.ingest_resume", return_value=_result()):
        runner.invoke(app, ["resume", "ingest", "--workspace", ws])
    raw = json.loads(storage.read("profile/skills.json").decode())
    assert raw["skills"][0]["evidence"]["quote"] == "Python, Go"
    assert raw["skills"][0]["evidence"]["line"] == 2


def test_ingest_with_a_path_replaces_master_then_ingests(tmp_path):
    ws, storage = _workspace(tmp_path)
    new_resume = tmp_path / "updated.md"
    new_resume.write_text("SKILLS\nRust, Zig\n")
    with patch("careeros.cli.resume_cmd.ingest_resume", return_value=_result()) as ing:
        result = runner.invoke(app, ["resume", "ingest", str(new_resume), "--workspace", ws])
    assert result.exit_code == 0
    assert storage.read("resumes/master.md").decode() == "SKILLS\nRust, Zig\n"
    assert ing.call_args.args[0] == "SKILLS\nRust, Zig\n"


def test_ingest_logs_verified_and_dropped_counts(tmp_path):
    ws, _ = _workspace(tmp_path)
    with patch("careeros.cli.resume_cmd.ingest_resume",
               return_value=_result(names=("Python",), dropped=("Rust",))):
        runner.invoke(app, ["resume", "ingest", "--workspace", ws])
    logs = sorted((tmp_path / "activity").glob("*.jsonl"))
    events = [json.loads(l) for l in logs[-1].read_text().strip().split("\n") if l]
    ingested = [e for e in events if e["event_type"] == "resume_ingested"]
    assert len(ingested) == 1
    assert "1" in ingested[0]["summary"]


def test_ingest_names_the_dropped_skills_in_output(tmp_path):
    ws, _ = _workspace(tmp_path)
    with patch("careeros.cli.resume_cmd.ingest_resume",
               return_value=_result(names=("Python",), dropped=("Rust", "Haskell"))):
        result = runner.invoke(app, ["resume", "ingest", "--workspace", ws])
    assert "Rust" in result.output
    assert "Haskell" in result.output


def test_zero_verified_skills_exits_non_zero(tmp_path):
    ws, _ = _workspace(tmp_path)
    with patch("careeros.cli.resume_cmd.ingest_resume",
               return_value=_result(names=(), dropped=("Rust",))):
        result = runner.invoke(app, ["resume", "ingest", "--workspace", ws])
    assert result.exit_code == 1
    assert "Rust" in result.output


def test_missing_path_exits_one_naming_it(tmp_path):
    ws, _ = _workspace(tmp_path)
    result = runner.invoke(app, ["resume", "ingest", str(tmp_path / "nope.md"), "--workspace", ws])
    assert result.exit_code == 1
    assert "nope.md" in result.output


def test_requires_a_workspace(tmp_path, monkeypatch):
    monkeypatch.setattr("careeros.config.CONFIG_PATH", tmp_path / "missing.json")
    result = runner.invoke(app, ["resume", "ingest"])
    assert result.exit_code == 1
    assert "No workspace configured" in result.output


def test_failed_ingestion_does_not_destroy_existing_skills(tmp_path):
    # ingest_resume never raises; it returns an empty result on any LLM or
    # parse failure. A transient blip must not wipe a populated skills.json.
    ws, storage = _workspace(tmp_path)
    with patch("careeros.cli.resume_cmd.ingest_resume", return_value=_result(names=("Python",))):
        runner.invoke(app, ["resume", "ingest", "--workspace", ws])
    before = storage.read("profile/skills.json").decode()

    with patch("careeros.cli.resume_cmd.ingest_resume",
               return_value=_result(names=(), dropped=())):
        result = runner.invoke(app, ["resume", "ingest", "--workspace", ws])

    assert result.exit_code == 1
    assert storage.read("profile/skills.json").decode() == before
    assert "left unchanged" in result.output


def test_failed_ingestion_logs_status_failed(tmp_path):
    ws, _ = _workspace(tmp_path)
    with patch("careeros.cli.resume_cmd.ingest_resume",
               return_value=_result(names=(), dropped=("Rust",))):
        runner.invoke(app, ["resume", "ingest", "--workspace", ws])
    logs = sorted((tmp_path / "activity").glob("*.jsonl"))
    events = [json.loads(l) for l in logs[-1].read_text().strip().split("\n") if l]
    ingested = [e for e in events if e["event_type"] == "resume_ingested"]
    assert ingested[-1]["status"] == "failed"


def test_successful_ingestion_logs_status_success(tmp_path):
    ws, _ = _workspace(tmp_path)
    with patch("careeros.cli.resume_cmd.ingest_resume", return_value=_result(names=("Python",))):
        runner.invoke(app, ["resume", "ingest", "--workspace", ws])
    logs = sorted((tmp_path / "activity").glob("*.jsonl"))
    events = [json.loads(l) for l in logs[-1].read_text().strip().split("\n") if l]
    ingested = [e for e in events if e["event_type"] == "resume_ingested"]
    assert ingested[-1]["status"] == "success"
