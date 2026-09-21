import json
import pytest
from pathlib import Path
from typer.testing import CliRunner
from unittest.mock import MagicMock, patch
from careeros.cli.main import app
from careeros.core.models import Profile, Skills
from careeros.storage.filesystem import LocalFilesystemStorage
from careeros.workspace.manager import open_workspace
from datetime import datetime, timezone


def _today():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


@pytest.fixture
def resume_file(tmp_path):
    f = tmp_path / "resume.md"
    f.write_text("Alice Johnson\nSenior SRE\n8 years")
    return f


@pytest.fixture
def mock_extraction():
    from careeros.skills.resume_ingest import IngestResult

    profile = Profile(name="Alice Johnson", title="Senior SRE", years_of_experience=8)
    skills = Skills(skills=[])
    ingested = IngestResult(skills=skills, dropped=())
    with patch("careeros.cli.onboard.extract_basic_profile", return_value=profile), \
         patch("careeros.cli.onboard.ingest_resume", return_value=ingested):
        yield


def _run_onboard(runner, tmp_path, resume_file, ws_name="workspace", sources="greenhouse"):
    ws_path = str(tmp_path / ws_name)
    # Input sequence: workspace path, resume path, confirm profile (y),
    # roles (blank), remote (any), comp (blank), locations (blank),
    # sources, goals (n)
    user_input = f"{ws_path}\n{resume_file}\ny\n\nany\n\n\n{sources}\nn\n"
    return runner.invoke(app, ["onboard"], input=user_input), ws_path


def test_onboard_creates_manifest(tmp_path, resume_file, mock_extraction, monkeypatch):
    monkeypatch.setattr("careeros.config.CONFIG_PATH", tmp_path / "config.json")
    runner = CliRunner()
    result, ws_path = _run_onboard(runner, tmp_path, resume_file)
    assert result.exit_code == 0, result.output
    assert (Path(ws_path) / "manifest.json").exists()


def test_onboard_writes_profile(tmp_path, resume_file, mock_extraction, monkeypatch):
    monkeypatch.setattr("careeros.config.CONFIG_PATH", tmp_path / "config.json")
    runner = CliRunner()
    _, ws_path = _run_onboard(runner, tmp_path, resume_file)
    storage = LocalFilesystemStorage(ws_path)
    profile = Profile.load(storage)
    assert profile.name == "Alice Johnson"


def test_onboard_writes_global_config(tmp_path, resume_file, mock_extraction, monkeypatch):
    config_path = tmp_path / "config.json"
    monkeypatch.setattr("careeros.config.CONFIG_PATH", config_path)
    runner = CliRunner()
    _, ws_path = _run_onboard(runner, tmp_path, resume_file)
    assert config_path.exists()
    data = json.loads(config_path.read_text())
    assert data["workspace_path"] == ws_path


def test_onboard_logs_activity_events(tmp_path, resume_file, mock_extraction, monkeypatch):
    monkeypatch.setattr("careeros.config.CONFIG_PATH", tmp_path / "config.json")
    runner = CliRunner()
    _, ws_path = _run_onboard(runner, tmp_path, resume_file)
    storage = LocalFilesystemStorage(ws_path)
    log = storage.read(f"activity/{_today()}.jsonl").decode()
    event_types = [json.loads(l)["event_type"] for l in log.strip().split("\n") if l]
    assert "workspace_created" in event_types
    assert "resume_imported" in event_types
    assert "onboard_complete" in event_types


def test_onboard_missing_resume_exits(tmp_path, monkeypatch):
    monkeypatch.setattr("careeros.config.CONFIG_PATH", tmp_path / "config.json")
    runner = CliRunner()
    ws_path = str(tmp_path / "ws")
    result = runner.invoke(app, ["onboard"], input=f"{ws_path}\n/nonexistent/resume.md\n")
    assert result.exit_code != 0


def test_onboard_oversized_resume_exits(tmp_path, monkeypatch):
    import careeros.cli.onboard as onboard_module
    monkeypatch.setattr(onboard_module, "_MAX_RESUME_BYTES", 10)
    monkeypatch.setattr("careeros.config.CONFIG_PATH", tmp_path / "config.json")
    big_resume = tmp_path / "big_resume.md"
    big_resume.write_text("x" * 1000)
    runner = CliRunner()
    ws_path = str(tmp_path / "ws")
    result = runner.invoke(app, ["onboard"], input=f"{ws_path}\n{big_resume}\n")
    assert result.exit_code != 0
    assert "too large" in result.output.lower()


def test_onboard_non_utf8_resume_does_not_crash(tmp_path, mock_extraction, monkeypatch):
    monkeypatch.setattr("careeros.config.CONFIG_PATH", tmp_path / "config.json")
    binary_resume = tmp_path / "resume.bin"
    binary_resume.write_bytes(b"\xff\xfe\x00Alice Johnson\x00\xff")
    runner = CliRunner()
    ws_path = str(tmp_path / "ws")
    result, ws_path = _run_onboard(runner, tmp_path, binary_resume)
    assert result.exit_code == 0, result.output
    storage = LocalFilesystemStorage(ws_path)
    profile = Profile.load(storage)
    assert profile.name == "Alice Johnson"


def test_onboard_writes_board_entries(tmp_path, resume_file, mock_extraction, monkeypatch):
    monkeypatch.setattr("careeros.config.CONFIG_PATH", tmp_path / "config.json")
    runner = CliRunner()
    _, ws_path = _run_onboard(runner, tmp_path, resume_file,
                              sources="greenhouse:stripe:Stripe")
    storage = LocalFilesystemStorage(ws_path)
    raw = json.loads(storage.read("config/sources.json").decode())
    assert raw["sources"] == [
        {"source": "greenhouse", "board": "stripe", "company": "Stripe",
         "mode": "SEARCH_ONLY"}
    ]


def test_onboard_defaults_company_to_the_board_slug(tmp_path, resume_file,
                                                    mock_extraction, monkeypatch):
    monkeypatch.setattr("careeros.config.CONFIG_PATH", tmp_path / "config.json")
    runner = CliRunner()
    _, ws_path = _run_onboard(runner, tmp_path, resume_file, sources="lever:acme")
    storage = LocalFilesystemStorage(ws_path)
    raw = json.loads(storage.read("config/sources.json").decode())
    assert raw["sources"] == [
        {"source": "lever", "board": "acme", "company": "acme", "mode": "SEARCH_ONLY"}
    ]


def test_onboard_skips_an_entry_with_no_board(tmp_path, resume_file,
                                              mock_extraction, monkeypatch):
    # The legacy answer shape. It must produce no entry rather than an inert one.
    monkeypatch.setattr("careeros.config.CONFIG_PATH", tmp_path / "config.json")
    runner = CliRunner()
    _, ws_path = _run_onboard(runner, tmp_path, resume_file, sources="greenhouse")
    storage = LocalFilesystemStorage(ws_path)
    raw = json.loads(storage.read("config/sources.json").decode())
    assert raw["sources"] == []


def test_onboard_no_longer_offers_naukri(tmp_path, resume_file,
                                         mock_extraction, monkeypatch):
    monkeypatch.setattr("careeros.config.CONFIG_PATH", tmp_path / "config.json")
    runner = CliRunner()
    result, _ = _run_onboard(runner, tmp_path, resume_file,
                             sources="greenhouse:stripe:Stripe")
    assert "naukri" not in result.output


def test_onboard_uses_evidence_backed_ingestion_for_skills(tmp_path, resume_file, monkeypatch):
    monkeypatch.setattr("careeros.config.CONFIG_PATH", tmp_path / "config.json")
    from careeros.core.models import Evidence, Profile, Skill, Skills
    from careeros.skills.resume_ingest import IngestResult

    profile = Profile(name="Alice Johnson", title="Senior SRE", years_of_experience=8)
    ingested = IngestResult(
        skills=Skills(skills=[Skill(
            name="Kubernetes",
            source="resumes/master.md",
            evidence=Evidence(quote="Kubernetes", line=1, source_file="resumes/master.md"),
        )]),
        dropped=("Rust",),
    )
    runner = CliRunner()
    with patch("careeros.cli.onboard.extract_basic_profile", return_value=profile), \
         patch("careeros.cli.onboard.ingest_resume", return_value=ingested):
        result, ws_path = _run_onboard(runner, tmp_path, resume_file)

    assert result.exit_code == 0, result.output
    storage = LocalFilesystemStorage(ws_path)
    raw = json.loads(storage.read("profile/skills.json").decode())
    assert raw["skills"][0]["evidence"]["quote"] == "Kubernetes"


def test_onboard_completes_when_nothing_verifies(tmp_path, resume_file, monkeypatch):
    # A sparse profile is the honest outcome of the guarantee; it must not
    # be a fatal one.
    monkeypatch.setattr("careeros.config.CONFIG_PATH", tmp_path / "config.json")
    from careeros.core.models import Profile, Skills
    from careeros.skills.resume_ingest import IngestResult

    profile = Profile(name="Alice Johnson", title="Senior SRE")
    empty = IngestResult(skills=Skills(), dropped=("Rust", "Go"))
    runner = CliRunner()
    with patch("careeros.cli.onboard.extract_basic_profile", return_value=profile), \
         patch("careeros.cli.onboard.ingest_resume", return_value=empty):
        result, ws_path = _run_onboard(runner, tmp_path, resume_file)

    assert result.exit_code == 0, result.output
    storage = LocalFilesystemStorage(ws_path)
    assert json.loads(storage.read("profile/skills.json").decode())["skills"] == []


def test_onboard_warns_loudly_when_skill_extraction_errors(tmp_path, resume_file, monkeypatch):
    # I2: an LLM/API failure is not the same outcome as "nothing verified"
    # and onboard must say so clearly, with the retry command, rather than
    # silently reporting zero skills.
    monkeypatch.setattr("careeros.config.CONFIG_PATH", tmp_path / "config.json")
    from careeros.core.models import Profile, Skills
    from careeros.skills.resume_ingest import IngestResult

    profile = Profile(name="Alice Johnson", title="Senior SRE")
    errored = IngestResult(skills=Skills(), dropped=(), error="invalid api key")
    runner = CliRunner()
    with patch("careeros.cli.onboard.extract_basic_profile", return_value=profile), \
         patch("careeros.cli.onboard.ingest_resume", return_value=errored):
        result, ws_path = _run_onboard(runner, tmp_path, resume_file)

    assert result.exit_code == 0, result.output
    assert "invalid api key" in result.output
    assert "careeros resume ingest" in result.output
    storage = LocalFilesystemStorage(ws_path)
    assert json.loads(storage.read("profile/skills.json").decode())["skills"] == []


def test_onboard_survives_a_malformed_ingest_response_end_to_end(tmp_path, resume_file, monkeypatch):
    # C1: ingest_resume must be total. {"skills": null} is a plausible
    # model response when told to omit unsupportable skills, and `onboard`
    # deliberately has no try/except around ingest_resume — it relies on the
    # documented never-raises contract. This exercises that contract for
    # real, through the actual parsing path, not a mocked IngestResult.
    monkeypatch.setattr("careeros.config.CONFIG_PATH", tmp_path / "config.json")
    from careeros.config import GlobalConfig

    profile = Profile(name="Alice Johnson", title="Senior SRE", years_of_experience=8)
    resp = MagicMock()
    resp.choices = [MagicMock()]
    resp.choices[0].message.content = json.dumps({"skills": None})

    runner = CliRunner()
    with patch("careeros.cli.onboard.extract_basic_profile", return_value=profile), \
         patch("litellm.completion", return_value=resp):
        result, ws_path = _run_onboard(runner, tmp_path, resume_file)

    assert result.exit_code == 0, result.output
    storage = LocalFilesystemStorage(ws_path)
    assert (Path(ws_path) / "manifest.json").exists()
    assert Profile.load(storage).name == "Alice Johnson"
    assert json.loads(storage.read("profile/skills.json").decode())["skills"] == []
    assert GlobalConfig.load().workspace_path == ws_path
    # Re-usable: a completed workspace must open cleanly, not just exist.
    open_workspace(storage)
