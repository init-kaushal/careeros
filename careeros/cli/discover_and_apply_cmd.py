from __future__ import annotations

from datetime import datetime, timezone

import typer
from rich import print as rprint

from careeros.browser.driver import fetch_jd_text, launch_browser
from careeros.browser.scrapers.indeed import IndeedScraper
from careeros.browser.scrapers.linkedin import LinkedInScraper
from careeros.browser.scrapers.wellfound import WellfoundScraper
from careeros.cli.apply_cmd import FILLERS
from careeros.config import GlobalConfig
from careeros.core.job_id import make_job_id
from careeros.core.models import AutomationPolicy, Goals, Job, Profile, Skills
from careeros.runtime.base import ActionProposal
from careeros.runtime.factory import open_automation_runtime
from careeros.skills.browse_query import job_query_from_profile
from careeros.skills.cover_letter import generate_cover_letter
from careeros.skills.job_score import score_job
from careeros.storage.filesystem import LocalFilesystemStorage

discover_and_apply_app = typer.Typer(help="Unattended discover + auto-apply for scheduled runs.")

_RESUME_EXTENSIONS = (".pdf", ".docx")

SCRAPERS: dict = {
    "linkedin": LinkedInScraper(),
    "indeed": IndeedScraper(),
    "wellfound": WellfoundScraper(),
}


def _get_storage(workspace_path: str | None) -> LocalFilesystemStorage:
    if workspace_path:
        return LocalFilesystemStorage(workspace_path)
    config = GlobalConfig.load()
    if not config.workspace_path:
        rprint("[red]No workspace configured. Run 'careeros onboard' first.[/red]")
        raise typer.Exit(1)
    return LocalFilesystemStorage(config.workspace_path)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@discover_and_apply_app.command()
def discover_and_apply_cmd(
    board: str = typer.Option(None, "--board", help="Single board to run (overrides policy's board list)"),
    workspace: str = typer.Option(None, "--workspace", help="Workspace path"),
) -> None:
    try:
        runtime = open_automation_runtime(_get_storage(workspace))
    except FileNotFoundError:
        rprint("[red]No workspace configured. Run 'careeros onboard' first.[/red]")
        raise typer.Exit(1)

    try:
        policy = AutomationPolicy.load(runtime.storage)
    except FileNotFoundError:
        rprint("[red]config/automation_policy.json not found — create one before running discover-and-apply.[/red]")
        raise typer.Exit(1)

    try:
        profile = Profile.load(runtime.storage)
    except FileNotFoundError:
        rprint("[red]No profile found. Run 'careeros onboard' first.[/red]")
        raise typer.Exit(1)

    skills = Skills.load_or_empty(runtime.storage)
    goals = Goals.load_or_empty(runtime.storage)
    boards = [board] if board else policy.boards
    query = job_query_from_profile(profile, goals)

    discovered: list[dict] = []
    now = _now()
    for b in boards:
        scraper = SCRAPERS.get(b)
        if scraper is None:
            rprint("[yellow]Unknown board '" + b + "', skipping.[/yellow]")
            continue
        try:
            with launch_browser(headless=True) as (_, page):
                try:
                    postings = scraper.search(page, query, 20)
                except Exception as exc:
                    rprint("[yellow]Warning: could not search " + b + ": " + str(exc) + "[/yellow]")
                    postings = []

                for posting in postings:
                    jd_text = fetch_jd_text(page, posting["url"])
                    result = score_job(jd_text, profile, skills)
                    discovered.append({**posting, "score": result["score"], "jd_text": jd_text})
        except ImportError:
            rprint("[red]Playwright is not installed.[/red]")
            raise typer.Exit(1)

    saved_count = 0
    for p in discovered:
        job_id = make_job_id(p["company"], p["title"])
        job = Job(
            id=job_id,
            source=p["source_board"],
            url=p["url"],
            company=p["company"],
            title=p["title"],
            location=p.get("location"),
            description=p["jd_text"],
            stage="saved",
            created_at=now,
            updated_at=now,
        )
        job.save(runtime.storage)
        runtime.record_activity(runtime.new_event(
            "job_added", "discover-and-apply",
            "Job saved from " + p["source_board"] + ": " + p["company"] + " — " + p["title"],
            entity_type="job", entity_id=job_id,
        ))
        saved_count += 1
        p["job_id"] = job_id

    eligible = sorted(
        [p for p in discovered if p["score"] >= policy.auto_apply_min_score],
        key=lambda p: p["score"], reverse=True,
    )

    resume_entries = sorted([
        p for p in runtime.storage.list("resumes/versions/")
        if p.endswith(_RESUME_EXTENSIONS)
    ])
    resume_path = runtime.storage.resolve(resume_entries[-1]) if resume_entries else None

    applied_count = 0
    skipped_count = 0
    for p in eligible:
        if applied_count >= policy.max_auto_applies_per_run:
            break
        if resume_path is None:
            rprint("[yellow]No resume found — skipping auto-apply for " + p["company"] + ".[/yellow]")
            skipped_count += 1
            continue

        job_id = p["job_id"]
        cover_letter = generate_cover_letter(p["jd_text"], profile, skills, goals)
        if not cover_letter:
            runtime.record_activity(runtime.new_event(
                "cover_letter_failed", "discover-and-apply",
                "Cover letter generation failed for " + p["company"] + " — " + p["title"],
                status="failed", entity_type="job", entity_id=job_id,
            ))
            skipped_count += 1
            continue

        filler = next((f for f in FILLERS if f.can_handle(p["url"])), None)
        if filler is None:
            runtime.record_activity(runtime.new_event(
                "no_filler_available", "discover-and-apply",
                "No filler available for " + p["company"] + " — " + p["title"],
                status="failed", entity_type="job", entity_id=job_id,
            ))
            skipped_count += 1
            continue

        cl_storage_path = "applications/" + job_id + "/cover_letter.txt"
        try:
            runtime.storage.atomic_write(cl_storage_path, cover_letter.encode())
            cover_letter_path = runtime.storage.resolve(cl_storage_path)

            approval = runtime.request_approval(ActionProposal(
                action="apply_to_job",
                summary="Auto-apply (score " + str(p["score"]) + " >= threshold "
                + str(policy.auto_apply_min_score) + ") to " + p["company"] + " — " + p["title"],
                entity_type="job", entity_id=job_id,
            ))
            if not approval.approved:
                skipped_count += 1
                continue

            job = Job.load(runtime.storage, job_id)
            with launch_browser(headless=True) as (_, page):
                success = filler.fill(page, job, profile, cover_letter, cover_letter_path, resume_path)

            if success:
                applied_now = _now()
                job = job.model_copy(update={"stage": "applied", "applied_at": applied_now, "updated_at": applied_now})
                job.save(runtime.storage)
                runtime.record_activity(runtime.new_event(
                    "job_applied", "discover-and-apply",
                    "Auto-applied (score " + str(p["score"]) + " >= threshold "
                    + str(policy.auto_apply_min_score) + ") to " + p["company"] + " — " + p["title"],
                    entity_type="job", entity_id=job_id,
                ))
                applied_count += 1
            else:
                skipped_count += 1
        except Exception:
            skipped_count += 1
            continue

    rprint(
        "Discovered: " + str(saved_count) + ", Auto-applied: " + str(applied_count)
        + ", Skipped: " + str(skipped_count)
    )
