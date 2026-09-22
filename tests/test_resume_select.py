import hashlib

from careeros.core.models import Evidence, ResumeVariant, VariantSection
from careeros.core.resume_select import select_resume
from careeros.storage.filesystem import LocalFilesystemStorage


def _storage(tmp_path):
    return LocalFilesystemStorage(str(tmp_path))


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write_variant(storage, job_id, pdf=b"%PDF-tailored", pdf_sha256="", master_sha256=""):
    storage.atomic_write(ResumeVariant.pdf_path(job_id), pdf)
    doc = ResumeVariant(
        job_id=job_id, job_company="Acme", job_title="Senior SRE",
        generated_at="2026-09-22T00:00:00+00:00", source_file="resumes/master.md",
        sections=[VariantSection(heading="Skills", entries=[
            Evidence(quote="Python, Go", line=2, source_file="resumes/master.md")
        ])],
        pdf_sha256=pdf_sha256, master_sha256=master_sha256,
    )
    storage.atomic_write(ResumeVariant.json_path(job_id), doc.model_dump_json().encode())


def test_no_resume_at_all_returns_none(tmp_path):
    assert select_resume(_storage(tmp_path), "job1") is None


def test_falls_back_to_the_alphabetically_last_top_level_file(tmp_path):
    storage = _storage(tmp_path)
    storage.atomic_write("resumes/versions/a-resume.pdf", b"a")
    storage.atomic_write("resumes/versions/b-resume.pdf", b"b")
    choice = select_resume(storage, "job1")
    assert choice.tailored is False
    assert choice.storage_path == "resumes/versions/b-resume.pdf"
    assert choice.variant is None
    assert choice.path.endswith("resumes/versions/b-resume.pdf")


def test_exact_per_job_variant_wins_over_the_fallback(tmp_path):
    storage = _storage(tmp_path)
    storage.atomic_write("resumes/versions/zzz-resume.pdf", b"generic")
    _write_variant(storage, "job1")
    choice = select_resume(storage, "job1")
    assert choice.tailored is True
    assert choice.storage_path == ResumeVariant.pdf_path("job1")
    assert choice.variant is not None
    assert choice.variant.entry_count() == 1


def test_one_jobs_variant_does_not_become_another_jobs_resume(tmp_path):
    # storage.list is rglob-based, so without a top-level-only filter the
    # variant directory joins the alphabetical pool and job2 silently gets
    # the resume tailored for job1.
    storage = _storage(tmp_path)
    storage.atomic_write("resumes/versions/a-resume.pdf", b"generic")
    _write_variant(storage, "zzz-job1")
    choice = select_resume(storage, "job2")
    assert choice.tailored is False
    assert choice.storage_path == "resumes/versions/a-resume.pdf"


def test_variant_with_no_readable_sidecar_is_still_the_tailored_file(tmp_path):
    storage = _storage(tmp_path)
    storage.atomic_write(ResumeVariant.pdf_path("job1"), b"%PDF-tailored")
    choice = select_resume(storage, "job1")
    assert choice.tailored is True
    assert choice.variant is None


def test_corrupt_sidecar_does_not_break_selection(tmp_path):
    storage = _storage(tmp_path)
    storage.atomic_write(ResumeVariant.pdf_path("job1"), b"%PDF-tailored")
    storage.atomic_write(ResumeVariant.json_path("job1"), b"{not json")
    choice = select_resume(storage, "job1")
    assert choice.tailored is True
    assert choice.variant is None


def test_docx_is_accepted_in_the_fallback_pool(tmp_path):
    storage = _storage(tmp_path)
    storage.atomic_write("resumes/versions/mine.docx", b"d")
    choice = select_resume(storage, "job1")
    assert choice.storage_path == "resumes/versions/mine.docx"


def test_unrelated_extensions_are_ignored(tmp_path):
    storage = _storage(tmp_path)
    storage.atomic_write("resumes/versions/notes.txt", b"t")
    storage.atomic_write("resumes/versions/mine.pdf", b"p")
    choice = select_resume(storage, "job1")
    assert choice.storage_path == "resumes/versions/mine.pdf"


def test_sidecar_that_does_not_match_its_pdf_is_discarded(tmp_path):
    # Two concurrent `resume variant` runs can interleave their PDF and
    # sidecar writes, leaving a sidecar describing a document nobody holds.
    # The PDF is still the tailored file and still gets uploaded; only its
    # provenance is discarded, so no entry count can be announced.
    storage = _storage(tmp_path)
    _write_variant(storage, "job1", pdf=b"%PDF-run-A", pdf_sha256=_sha(b"%PDF-run-B"))
    choice = select_resume(storage, "job1")
    assert choice.tailored is True
    assert choice.variant is None
    assert choice.stale_master is False
    assert choice.storage_path == ResumeVariant.pdf_path("job1")


def test_variant_generated_from_a_superseded_master_is_flagged(tmp_path):
    # `resume ingest <other-resume>` replaces the master without touching
    # any variant, so the citations now name lines of a different document.
    storage = _storage(tmp_path)
    storage.atomic_write("resumes/master.md", b"SKILLS\nRust, Zig\n")
    _write_variant(storage, "job1", pdf_sha256=_sha(b"%PDF-tailored"),
                   master_sha256=_sha(b"SKILLS\nPython, Go\n"))
    choice = select_resume(storage, "job1")
    assert choice.tailored is True
    assert choice.variant is not None
    assert choice.stale_master is True


def test_a_master_that_has_not_changed_is_not_flagged(tmp_path):
    storage = _storage(tmp_path)
    storage.atomic_write("resumes/master.md", b"SKILLS\nPython, Go\n")
    _write_variant(storage, "job1", pdf_sha256=_sha(b"%PDF-tailored"),
                   master_sha256=_sha(b"SKILLS\nPython, Go\n"))
    choice = select_resume(storage, "job1")
    assert choice.variant is not None
    assert choice.stale_master is False


def test_a_master_that_has_gone_missing_counts_as_superseded(tmp_path):
    storage = _storage(tmp_path)
    _write_variant(storage, "job1", master_sha256=_sha(b"SKILLS\nPython, Go\n"))
    choice = select_resume(storage, "job1")
    assert choice.variant is not None
    assert choice.stale_master is True


def test_variant_written_before_the_hashes_existed_still_selects_normally(tmp_path):
    # Empty hashes mean "unknown", never "bad": a variant generated before
    # this check shipped must keep its provenance display.
    storage = _storage(tmp_path)
    storage.atomic_write("resumes/master.md", b"SKILLS\nPython, Go\n")
    _write_variant(storage, "job1")
    choice = select_resume(storage, "job1")
    assert choice.tailored is True
    assert choice.variant is not None
    assert choice.variant.entry_count() == 1
    assert choice.stale_master is False
