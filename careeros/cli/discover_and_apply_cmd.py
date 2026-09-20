from __future__ import annotations

from datetime import datetime, timezone

import typer
from rich import print as rprint

from careeros.browser.boards import BOARDS
from careeros.browser.driver import BrowserProfileBusy, fetch_jd_text, launch_browser
from careeros.browser.session import check_board_sessions
from careeros.cli.apply_cmd import FILLERS
from careeros.config import GlobalConfig
from careeros.core.job_id import make_job_id
from careeros.core.models import AutomationPolicy, Goals, Job, PolicyConfig, Profile, Skills
from careeros.core.policy_engine import PolicyEngine
from careeros.runtime.base import ActionProposal
from careeros.runtime.factory import open_automation_runtime
from careeros.skills.browse_query import job_query_from_profile
from careeros.skills.cover_letter import generate_cover_letter
from careeros.skills.job_score import score_job
from careeros.storage.filesystem import LocalFilesystemStorage

discover_and_apply_app = typer.Typer(help="Unattended discover + auto-apply for scheduled runs.")

_RESUME_EXTENSIONS = (".pdf", ".docx")

SCRAPERS: dict = {name: board.scraper for name, board in BOARDS.items()}


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
    try:
        sessions = check_board_sessions(boards)
    except BrowserProfileBusy as exc:
        rprint("[red]" + str(exc) + "[/red]")
        raise typer.Exit(1)
    unauthorized = [b for b in boards if not sessions.get(b)]
    for b in unauthorized:
        runtime.record_activity(runtime.new_event(
            "session_unauthorized", "discover-and-apply",
            "No authorized browser session for " + b + " — board skipped",
            status="failed", entity_type="board", entity_id=b,
        ))
    boards = [b for b in boards if sessions.get(b)]
    if not boards:
        rprint(
            "[red]No authorized board sessions. Run: careeros browser login --board <name>[/red]"
        )
        raise typer.Exit(1)
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
        except BrowserProfileBusy as exc:
            rprint("[red]" + str(exc) + "[/red]")
            raise typer.Exit(1)

    # Minimal dedup: match on (company, title) case-insensitively against jobs already
    # in the workspace. This is a stopgap ahead of Phase 10's canonical-URL fingerprint
    # engine — it prevents the most damaging case (re-submitting a real application to
    # the same employer on every cron run) without claiming to solve cross-board dedup.
    existing_jobs = Job.list_all(runtime.storage)
    existing_by_key = {(j.company.strip().lower(), j.title.strip().lower()): j for j in existing_jobs}

    saved_count = 0
    duplicate_count = 0
    for p in discovered:
        key = (p["company"].strip().lower(), p["title"].strip().lower())
        existing = existing_by_key.get(key)
        if existing is not None:
            p["job_id"] = existing.id
            p["already_applied"] = existing.applied_at is not None
            duplicate_count += 1
            continue

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
        p["already_applied"] = False
        existing_by_key[key] = job

    eligible = sorted(
        [p for p in discovered if p["score"] >= policy.auto_apply_min_score and not p.get("already_applied")],
        key=lambda p: p["score"], reverse=True,
    )

    resume_entries = sorted([
        p for p in runtime.storage.list("resumes/versions/")
        if p.endswith(_RESUME_EXTENSIONS)
    ])
    resume_path = runtime.storage.resolve(resume_entries[-1]) if resume_entries else None

    policy_engine = PolicyEngine(PolicyConfig.load(runtime.storage))
    applied_count = 0
    skipped_count = 0
    blocked_count = 0
    for p in eligible:
        if applied_count >= policy.max_auto_applies_per_run:
            break
        if resume_path is None:
            rprint("[yellow]No resume found — skipping auto-apply for " + p["company"] + ".[/yellow]")
            skipped_count += 1
            continue

        job_id = p["job_id"]
        job = Job.load(runtime.storage, job_id)
        policy_result = policy_engine.check_job(job)
        if policy_result.blocked:
            runtime.record_activity(runtime.new_event(
                "policy_blocked", "discover-and-apply",
                "Blocked by policy (" + policy_result.rule + "): " + p["company"] + " — " + p["title"],
                status="failed", entity_type="job", entity_id=job_id,
            ))
            blocked_count += 1
            continue

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
                runtime.record_activity(runtime.new_event(
                    "apply_incomplete", "discover-and-apply",
                    "Form fill incomplete for " + p["company"] + " — " + p["title"],
                    status="failed", entity_type="job", entity_id=job_id,
                ))
                skipped_count += 1
        except BrowserProfileBusy as exc:
            # A locked profile is a whole-run condition, not a per-job failure:
            # continuing would re-run a paid cover-letter generation for every
            # remaining job only to fail identically at launch. Match the
            # discovery loop and stop.
            rprint("[red]" + str(exc) + "[/red]")
            raise typer.Exit(1)
        except Exception as exc:
            runtime.record_activity(runtime.new_event(
                "apply_error", "discover-and-apply",
                "Error while applying to " + p["company"] + " — " + p["title"] + ": " + type(exc).__name__,
                status="failed", entity_type="job", entity_id=job_id,
            ))
            skipped_count += 1
            continue

    rprint(
        "Discovered: " + str(saved_count) + ", Duplicates: " + str(duplicate_count)
        + ", Blocked: " + str(blocked_count)
        + ", Auto-applied: " + str(applied_count) + ", Skipped: " + str(skipped_count)
        + ", Unauthorized boards: " + (", ".join(unauthorized) if unauthorized else "none")
    )
