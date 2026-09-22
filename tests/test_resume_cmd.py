import hashlib
import json
from datetime import datetime, timezone
from unittest.mock import patch

from playwright.sync_api import Error as PlaywrightError
from typer.testing import CliRunner

from careeros.cli.main import app
from careeros.core.models import Evidence, Job, Profile, ResumeVariant, Skill, Skills
from careeros.core.models import Evidence as _Ev
from careeros.core.models import VariantSection
from careeros.core.resume_select import select_resume
from careeros.render.resume_pdf import RendererUnavailable
from careeros.skills.resume_ingest import IngestResult
from careeros.skills.resume_variant import VariantResult
from careeros.storage.filesystem import LocalFilesystemStorage
from careeros.workspace.manager import init_workspace

runner = CliRunner()


def _workspace(tmp_path):
    storage = LocalFilesystemStorage(str(tmp_path))
    init_workspace(storage)
    storage.atomic_write("resumes/master.md", b"SKILLS\nPython, Go\n")
    # `resume variant` refuses to render a resume with no name on it, so a
    # workspace an onboarded user would actually have carries a profile.
    Profile(name="Alice Johnson", title="Senior SRE",
            location="San Francisco, CA", email="alice@example.com").save(storage)
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


def test_zero_verified_ingest_with_a_path_leaves_previous_master_intact(tmp_path):
    # I1: the new resume must not overwrite the stored master until ingestion
    # is known to have produced something to save. Otherwise skills retained
    # from a prior run cite a file that no longer contains their quote.
    ws, storage = _workspace(tmp_path)
    original_master = storage.read("resumes/master.md").decode()
    new_resume = tmp_path / "updated.md"
    new_resume.write_text("SKILLS\nRust, Zig\n")
    with patch("careeros.cli.resume_cmd.ingest_resume",
               return_value=_result(names=(), dropped=("Rust", "Zig"))):
        result = runner.invoke(app, ["resume", "ingest", str(new_resume), "--workspace", ws])
    assert result.exit_code == 1
    assert storage.read("resumes/master.md").decode() == original_master
    assert "left unchanged" in result.output


def test_successful_ingest_with_a_path_does_replace_master(tmp_path):
    ws, storage = _workspace(tmp_path)
    new_resume = tmp_path / "updated.md"
    new_resume.write_text("SKILLS\nRust, Zig\n")
    with patch("careeros.cli.resume_cmd.ingest_resume", return_value=_result(names=("Rust",))):
        result = runner.invoke(app, ["resume", "ingest", str(new_resume), "--workspace", ws])
    assert result.exit_code == 0
    assert storage.read("resumes/master.md").decode() == "SKILLS\nRust, Zig\n"


def test_llm_error_is_reported_distinctly_and_exits_non_zero(tmp_path):
    from careeros.core.models import Skills
    from careeros.skills.resume_ingest import IngestResult

    ws, _ = _workspace(tmp_path)
    err_result = IngestResult(skills=Skills(), dropped=(), error="invalid api key")
    with patch("careeros.cli.resume_cmd.ingest_resume", return_value=err_result):
        result = runner.invoke(app, ["resume", "ingest", "--workspace", ws])
    assert result.exit_code == 1
    assert "invalid api key" in result.output


def test_llm_error_does_not_overwrite_existing_skills(tmp_path):
    from careeros.core.models import Skills
    from careeros.skills.resume_ingest import IngestResult

    ws, storage = _workspace(tmp_path)
    with patch("careeros.cli.resume_cmd.ingest_resume", return_value=_result(names=("Python",))):
        runner.invoke(app, ["resume", "ingest", "--workspace", ws])
    before = storage.read("profile/skills.json").decode()

    err_result = IngestResult(skills=Skills(), dropped=(), error="network timeout")
    with patch("careeros.cli.resume_cmd.ingest_resume", return_value=err_result):
        result = runner.invoke(app, ["resume", "ingest", "--workspace", ws])

    assert result.exit_code == 1
    assert storage.read("profile/skills.json").decode() == before


def test_missing_path_records_a_failed_activity_event(tmp_path):
    ws, _ = _workspace(tmp_path)
    result = runner.invoke(app, ["resume", "ingest", str(tmp_path / "nope.md"), "--workspace", ws])
    assert result.exit_code == 1
    logs = sorted((tmp_path / "activity").glob("*.jsonl"))
    events = [json.loads(l) for l in logs[-1].read_text().strip().split("\n") if l]
    ingested = [e for e in events if e["event_type"] == "resume_ingested"]
    assert len(ingested) == 1
    assert ingested[-1]["status"] == "failed"


def test_missing_default_resume_records_a_failed_activity_event(tmp_path):
    storage = LocalFilesystemStorage(str(tmp_path))
    init_workspace(storage)
    result = runner.invoke(app, ["resume", "ingest", "--workspace", str(tmp_path)])
    assert result.exit_code == 1
    logs = sorted((tmp_path / "activity").glob("*.jsonl"))
    events = [json.loads(l) for l in logs[-1].read_text().strip().split("\n") if l]
    ingested = [e for e in events if e["event_type"] == "resume_ingested"]
    assert len(ingested) == 1
    assert ingested[-1]["status"] == "failed"


def test_unreadable_path_records_a_failed_activity_event(tmp_path):
    ws, _ = _workspace(tmp_path)
    bad = tmp_path / "bad.md"
    bad.write_bytes(b"\xff\xfe\x00not valid utf-8")
    result = runner.invoke(app, ["resume", "ingest", str(bad), "--workspace", ws])
    assert result.exit_code == 1
    logs = sorted((tmp_path / "activity").glob("*.jsonl"))
    events = [json.loads(l) for l in logs[-1].read_text().strip().split("\n") if l]
    ingested = [e for e in events if e["event_type"] == "resume_ingested"]
    assert len(ingested) == 1
    assert ingested[-1]["status"] == "failed"


def _job(storage, job_id="acme-sre-abc1"):
    now = datetime.now(timezone.utc).isoformat()
    Job(
        id=job_id, source="browse", url="https://example.com/j",
        company="Acme", title="Senior SRE",
        description="We need Kubernetes and Go.",
        stage="saved", created_at=now, updated_at=now,
    ).save(storage)
    return job_id


def _variant_result(quotes=("Python, Go",), dropped=()):
    return VariantResult(
        sections=(VariantSection(
            heading="Skills",
            entries=[_Ev(quote=q, line=2, source_file="resumes/master.md") for q in quotes],
        ),),
        dropped=tuple(dropped),
    )


def _patches(result, pdf=b"%PDF-1.4 x"):
    return (
        patch("careeros.cli.resume_cmd.select_variant_content", return_value=result),
        patch("careeros.cli.resume_cmd.render_pdf", return_value=pdf),
    )


def test_variant_writes_the_pdf_and_the_sidecar(tmp_path):
    ws, storage = _workspace(tmp_path)
    job_id = _job(storage)
    sel, rnd = _patches(_variant_result())
    with sel, rnd:
        result = runner.invoke(app, ["resume", "variant", "--job", job_id, "--workspace", ws])
    assert result.exit_code == 0
    assert storage.exists(ResumeVariant.pdf_path(job_id))
    assert storage.read(ResumeVariant.pdf_path(job_id)) == b"%PDF-1.4 x"
    sidecar = ResumeVariant.model_validate_json(
        storage.read(ResumeVariant.json_path(job_id)).decode()
    )
    assert sidecar.job_id == job_id
    assert sidecar.job_company == "Acme"
    assert sidecar.job_title == "Senior SRE"
    assert sidecar.source_file == "resumes/master.md"
    assert sidecar.sections[0].entries[0].quote == "Python, Go"
    assert sidecar.sections[0].entries[0].line == 2


def test_variant_passes_the_job_description_to_the_selector(tmp_path):
    ws, storage = _workspace(tmp_path)
    job_id = _job(storage)
    with patch("careeros.cli.resume_cmd.select_variant_content",
               return_value=_variant_result()) as sel, \
         patch("careeros.cli.resume_cmd.render_pdf", return_value=b"%PDF"):
        runner.invoke(app, ["resume", "variant", "--job", job_id, "--workspace", ws])
    assert "Kubernetes" in sel.call_args.args[0]
    assert sel.call_args.args[1] == "SKILLS\nPython, Go\n"
    assert sel.call_args.args[3] == "resumes/master.md"


def test_variant_names_every_dropped_span(tmp_path):
    ws, storage = _workspace(tmp_path)
    job_id = _job(storage)
    sel, rnd = _patches(_variant_result(dropped=("invented span",)))
    with sel, rnd:
        result = runner.invoke(app, ["resume", "variant", "--job", job_id, "--workspace", ws])
    assert result.exit_code == 0
    assert "invented span" in result.output
    sidecar = ResumeVariant.model_validate_json(
        storage.read(ResumeVariant.json_path(job_id)).decode()
    )
    assert sidecar.dropped == ["invented span"]


def test_variant_refuses_to_write_when_nothing_verified(tmp_path):
    ws, storage = _workspace(tmp_path)
    job_id = _job(storage)
    sel, rnd = _patches(VariantResult(sections=(), dropped=("a", "b")))
    with sel, rnd:
        result = runner.invoke(app, ["resume", "variant", "--job", job_id, "--workspace", ws])
    assert result.exit_code == 1
    assert not storage.exists(ResumeVariant.pdf_path(job_id))
    assert not storage.exists(ResumeVariant.json_path(job_id))


def test_variant_reports_an_llm_failure_distinctly(tmp_path):
    ws, storage = _workspace(tmp_path)
    job_id = _job(storage)
    sel, rnd = _patches(VariantResult(sections=(), dropped=(), error="invalid api key"))
    with sel, rnd:
        result = runner.invoke(app, ["resume", "variant", "--job", job_id, "--workspace", ws])
    assert result.exit_code == 1
    assert "invalid api key" in result.output
    assert not storage.exists(ResumeVariant.pdf_path(job_id))


def test_variant_reports_missing_chromium_actionably(tmp_path):
    ws, storage = _workspace(tmp_path)
    job_id = _job(storage)
    with patch("careeros.cli.resume_cmd.select_variant_content",
               return_value=_variant_result()), \
         patch("careeros.cli.resume_cmd.render_pdf",
               side_effect=RendererUnavailable("Chromium is not installed for "
                                               "Playwright. Run: playwright install chromium")):
        result = runner.invoke(app, ["resume", "variant", "--job", job_id, "--workspace", ws])
    assert result.exit_code == 1
    assert "playwright install chromium" in result.output
    assert not storage.exists(ResumeVariant.pdf_path(job_id))


def test_variant_reports_a_playwright_error_distinctly(tmp_path):
    ws, storage = _workspace(tmp_path)
    job_id = _job(storage)
    with patch("careeros.cli.resume_cmd.select_variant_content",
               return_value=_variant_result()), \
         patch("careeros.cli.resume_cmd.render_pdf",
               side_effect=PlaywrightError(
                   "Target page, context or browser has been closed")):
        result = runner.invoke(app, ["resume", "variant", "--job", job_id, "--workspace", ws])
    assert result.exit_code == 1
    assert "Target page, context or browser has been closed" in result.output
    assert not storage.exists(ResumeVariant.pdf_path(job_id))
    assert not storage.exists(ResumeVariant.json_path(job_id))


def test_variant_regeneration_overwrites_only_on_success(tmp_path):
    ws, storage = _workspace(tmp_path)
    job_id = _job(storage)
    sel, rnd = _patches(_variant_result(), pdf=b"%PDF-first")
    with sel, rnd:
        runner.invoke(app, ["resume", "variant", "--job", job_id, "--workspace", ws])
    assert storage.read(ResumeVariant.pdf_path(job_id)) == b"%PDF-first"

    sel, rnd = _patches(VariantResult(sections=(), dropped=(), error="network down"))
    with sel, rnd:
        result = runner.invoke(app, ["resume", "variant", "--job", job_id, "--workspace", ws])
    assert result.exit_code == 1
    assert storage.read(ResumeVariant.pdf_path(job_id)) == b"%PDF-first"

    sel, rnd = _patches(_variant_result(quotes=("Python, Go",)), pdf=b"%PDF-second")
    with sel, rnd:
        runner.invoke(app, ["resume", "variant", "--job", job_id, "--workspace", ws])
    assert storage.read(ResumeVariant.pdf_path(job_id)) == b"%PDF-second"


def test_variant_fails_clearly_for_an_unknown_job(tmp_path):
    ws, _storage = _workspace(tmp_path)
    result = runner.invoke(app, ["resume", "variant", "--job", "nope-xyz", "--workspace", ws])
    assert result.exit_code == 1
    assert "nope-xyz" in result.output


def test_variant_fails_clearly_when_the_master_is_missing(tmp_path):
    storage = LocalFilesystemStorage(str(tmp_path))
    init_workspace(storage)
    job_id = _job(storage)
    result = runner.invoke(app, ["resume", "variant", "--job", job_id,
                                 "--workspace", str(tmp_path)])
    assert result.exit_code == 1
    assert "resume ingest" in result.output


def test_variant_records_an_activity_event_on_success_and_failure(tmp_path):
    ws, storage = _workspace(tmp_path)
    job_id = _job(storage)
    sel, rnd = _patches(_variant_result())
    with sel, rnd:
        runner.invoke(app, ["resume", "variant", "--job", job_id, "--workspace", ws])
    sel, rnd = _patches(VariantResult(sections=(), dropped=()))
    with sel, rnd:
        runner.invoke(app, ["resume", "variant", "--job", job_id, "--workspace", ws])

    events = [
        json.loads(line)
        for p in storage.list("activity/")
        for line in storage.read(p).decode().splitlines() if line.strip()
    ]
    variant_events = [e for e in events if e["action"] == "variant"]
    assert [e["status"] for e in variant_events] == ["success", "failed"]


def test_variant_records_a_failed_event_for_an_unknown_job(tmp_path):
    ws, storage = _workspace(tmp_path)
    result = runner.invoke(app, ["resume", "variant", "--job", "nope-xyz", "--workspace", ws])
    assert result.exit_code == 1
    events = [
        json.loads(line)
        for p in storage.list("activity/")
        for line in storage.read(p).decode().splitlines() if line.strip()
    ]
    variant_events = [e for e in events if e["action"] == "variant"]
    assert len(variant_events) == 1
    assert variant_events[0]["status"] == "failed"


def test_variant_pdf_write_failure_leaves_prior_pdf_untouched(tmp_path):
    ws, storage = _workspace(tmp_path)
    job_id = _job(storage)
    sel, rnd = _patches(_variant_result(), pdf=b"%PDF-first")
    with sel, rnd:
        runner.invoke(app, ["resume", "variant", "--job", job_id, "--workspace", ws])
    pdf_path = ResumeVariant.pdf_path(job_id)
    assert storage.read(pdf_path) == b"%PDF-first"

    real_write = LocalFilesystemStorage.atomic_write

    def _boom(self, path, data):
        if path == pdf_path:
            raise OSError("disk full")
        return real_write(self, path, data)

    sel, rnd = _patches(_variant_result(quotes=("Python, Go",)), pdf=b"%PDF-second")
    with sel, rnd, patch.object(
        LocalFilesystemStorage, "atomic_write", autospec=True, side_effect=_boom
    ):
        result = runner.invoke(app, ["resume", "variant", "--job", job_id, "--workspace", ws])

    assert result.exit_code == 1
    assert "disk full" in result.output
    assert storage.read(pdf_path) == b"%PDF-first"

    events = [
        json.loads(line)
        for p in storage.list("activity/")
        for line in storage.read(p).decode().splitlines() if line.strip()
    ]
    variant_events = [e for e in events if e["action"] == "variant"]
    assert variant_events[-1]["status"] == "failed"


def test_variant_sidecar_write_failure_removes_the_stale_sidecar(tmp_path):
    ws, storage = _workspace(tmp_path)
    job_id = _job(storage)
    sel, rnd = _patches(_variant_result(), pdf=b"%PDF-first")
    with sel, rnd:
        runner.invoke(app, ["resume", "variant", "--job", job_id, "--workspace", ws])
    pdf_path = ResumeVariant.pdf_path(job_id)
    json_path = ResumeVariant.json_path(job_id)
    assert storage.exists(json_path)

    real_write = LocalFilesystemStorage.atomic_write

    def _boom(self, path, data):
        if path == json_path:
            raise OSError("disk full")
        return real_write(self, path, data)

    sel, rnd = _patches(_variant_result(quotes=("Python, Go",)), pdf=b"%PDF-second")
    with sel, rnd, patch.object(
        LocalFilesystemStorage, "atomic_write", autospec=True, side_effect=_boom
    ):
        result = runner.invoke(app, ["resume", "variant", "--job", job_id, "--workspace", ws])

    assert result.exit_code == 1
    assert "disk full" in result.output
    # The PDF already holds the new variant; the stale sidecar describing
    # the previous one must not survive to misrepresent it.
    assert storage.read(pdf_path) == b"%PDF-second"
    assert not storage.exists(json_path)

    events = [
        json.loads(line)
        for p in storage.list("activity/")
        for line in storage.read(p).decode().splitlines() if line.strip()
    ]
    variant_events = [e for e in events if e["action"] == "variant"]
    assert variant_events[-1]["status"] == "failed"


def test_pdf_is_written_before_its_evidence_sidecar(tmp_path):
    ws, storage = _workspace(tmp_path)
    job_id = _job(storage)
    written: list[str] = []
    real_write = storage.atomic_write

    def _spy(path, data):
        written.append(path)
        real_write(path, data)

    sel, rnd = _patches(_variant_result())
    with sel, rnd, patch.object(
        LocalFilesystemStorage, "atomic_write", autospec=True,
        side_effect=lambda self, path, data: _spy(path, data),
    ):
        result = runner.invoke(app, ["resume", "variant", "--job", job_id, "--workspace", ws])
    assert result.exit_code == 0
    variant_writes = [p for p in written if p.startswith("resumes/versions/")]
    assert variant_writes == [
        ResumeVariant.pdf_path(job_id),
        ResumeVariant.json_path(job_id),
    ], variant_writes


def test_a_freshly_generated_variant_validates_clean_on_both_hashes(tmp_path):
    ws, storage = _workspace(tmp_path)
    job_id = _job(storage)
    sel, rnd = _patches(_variant_result(), pdf=b"%PDF-1.4 fresh")
    with sel, rnd:
        result = runner.invoke(app, ["resume", "variant", "--job", job_id, "--workspace", ws])
    assert result.exit_code == 0

    sidecar = ResumeVariant.model_validate_json(
        storage.read(ResumeVariant.json_path(job_id)).decode()
    )
    assert sidecar.pdf_sha256 == hashlib.sha256(b"%PDF-1.4 fresh").hexdigest()
    assert sidecar.master_sha256 == hashlib.sha256(b"SKILLS\nPython, Go\n").hexdigest()

    choice = select_resume(storage, job_id)
    assert choice.tailored is True
    assert choice.variant is not None
    assert choice.stale_master is False


def test_a_variant_is_flagged_stale_after_the_master_is_re_ingested(tmp_path):
    # The whole point of master_sha256: `resume ingest` replaces the master
    # and leaves every variant's citations pointing at lines of a document
    # that no longer exists.
    ws, storage = _workspace(tmp_path)
    job_id = _job(storage)
    sel, rnd = _patches(_variant_result())
    with sel, rnd:
        runner.invoke(app, ["resume", "variant", "--job", job_id, "--workspace", ws])
    assert select_resume(storage, job_id).stale_master is False

    new_resume = tmp_path / "replacement.md"
    new_resume.write_text("SKILLS\nRust, Zig\n")
    with patch("careeros.cli.resume_cmd.ingest_resume", return_value=_result(names=("Rust",))):
        result = runner.invoke(app, ["resume", "ingest", str(new_resume), "--workspace", ws])
    assert result.exit_code == 0

    choice = select_resume(storage, job_id)
    assert choice.tailored is True
    assert choice.variant is not None
    assert choice.stale_master is True


def test_a_concurrent_regeneration_of_the_pdf_invalidates_the_sidecar(tmp_path):
    # Run A commits its PDF, run B completes fully, then run A commits its
    # sidecar: the stored sidecar describes entries absent from the stored
    # PDF. Reproduced here by replacing the PDF under a written sidecar.
    ws, storage = _workspace(tmp_path)
    job_id = _job(storage)
    sel, rnd = _patches(_variant_result())
    with sel, rnd:
        runner.invoke(app, ["resume", "variant", "--job", job_id, "--workspace", ws])

    storage.atomic_write(ResumeVariant.pdf_path(job_id), b"%PDF-from-another-run")
    choice = select_resume(storage, job_id)
    assert choice.tailored is True
    assert choice.variant is None


def test_the_sidecar_records_the_contact_header_that_was_rendered(tmp_path):
    # The header is the one part of the document not backed by an Evidence
    # line, so variant.json has to account for it or the sidecar does not
    # describe every rendered line.
    ws, storage = _workspace(tmp_path)
    job_id = _job(storage)
    sel, rnd = _patches(_variant_result())
    with sel, rnd:
        result = runner.invoke(app, ["resume", "variant", "--job", job_id, "--workspace", ws])
    assert result.exit_code == 0
    sidecar = ResumeVariant.model_validate_json(
        storage.read(ResumeVariant.json_path(job_id)).decode()
    )
    assert sidecar.header == {
        "name": "Alice Johnson",
        "title": "Senior SRE",
        "location": "San Francisco, CA",
        "email": "alice@example.com",
    }


def test_a_profile_field_absent_from_the_master_is_not_an_error(tmp_path):
    # A profile is authoritative for the user's own contact details, and may
    # have been hand-edited. Header fields are recorded, never verified
    # against the master.
    ws, storage = _workspace(tmp_path)
    Profile(name="Alice Johnson", title="Chief Vibes Officer").save(storage)
    job_id = _job(storage)
    sel, rnd = _patches(_variant_result())
    with sel, rnd:
        result = runner.invoke(app, ["resume", "variant", "--job", job_id, "--workspace", ws])
    assert result.exit_code == 0
    sidecar = ResumeVariant.model_validate_json(
        storage.read(ResumeVariant.json_path(job_id)).decode()
    )
    assert sidecar.header["title"] == "Chief Vibes Officer"


def test_the_sidecar_header_omits_fields_the_document_does_not_show(tmp_path):
    ws, storage = _workspace(tmp_path)
    Profile(name="Alice Johnson", title="", location=None, email=None).save(storage)
    job_id = _job(storage)
    sel, rnd = _patches(_variant_result())
    with sel, rnd:
        result = runner.invoke(app, ["resume", "variant", "--job", job_id, "--workspace", ws])
    assert result.exit_code == 0
    sidecar = ResumeVariant.model_validate_json(
        storage.read(ResumeVariant.json_path(job_id)).decode()
    )
    assert sidecar.header == {"name": "Alice Johnson"}


def test_variant_refuses_to_render_a_resume_with_no_name_on_it(tmp_path):
    # `onboard` saves Profile() whenever extract_basic_profile raises, so
    # this state is reachable. Without the guard the renderer emits an empty
    # <h1>, the command exits 0, and `apply` uploads an anonymous resume.
    storage = LocalFilesystemStorage(str(tmp_path))
    init_workspace(storage)
    storage.atomic_write("resumes/master.md", b"SKILLS\nPython, Go\n")
    ws = str(tmp_path)
    job_id = _job(storage)
    sel, rnd = _patches(_variant_result())
    with sel, rnd:
        result = runner.invoke(app, ["resume", "variant", "--job", job_id, "--workspace", ws])

    assert result.exit_code == 1
    assert not storage.exists(ResumeVariant.pdf_path(job_id))
    assert not storage.exists(ResumeVariant.json_path(job_id))
    assert "profile/profile.json" in result.output
    assert "careeros onboard" in " ".join(result.output.split())

    events = [
        json.loads(line)
        for p in storage.list("activity/")
        for line in storage.read(p).decode().splitlines() if line.strip()
    ]
    variant_events = [e for e in events if e["action"] == "variant"]
    assert len(variant_events) == 1
    assert variant_events[0]["status"] == "failed"


def test_a_blank_name_leaves_an_existing_variant_untouched(tmp_path):
    ws, storage = _workspace(tmp_path)
    job_id = _job(storage)
    sel, rnd = _patches(_variant_result(), pdf=b"%PDF-first")
    with sel, rnd:
        runner.invoke(app, ["resume", "variant", "--job", job_id, "--workspace", ws])
    assert storage.read(ResumeVariant.pdf_path(job_id)) == b"%PDF-first"

    Profile(name="   ").save(storage)
    sel, rnd = _patches(_variant_result(), pdf=b"%PDF-second")
    with sel, rnd:
        result = runner.invoke(app, ["resume", "variant", "--job", job_id, "--workspace", ws])
    assert result.exit_code == 1
    assert storage.read(ResumeVariant.pdf_path(job_id)) == b"%PDF-first"
