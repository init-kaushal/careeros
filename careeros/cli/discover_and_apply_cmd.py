from __future__ import annotations

from datetime import datetime, timezone

import typer
from rich import print as rprint

from careeros.browser.boards import BOARDS
from careeros.browser.driver import BrowserProfileBusy, fetch_jd_text, launch_browser
from careeros.browser.session import check_board_sessions
from careeros.config_sources import build_source, load_board_entries
from careeros.core.job_store import JobStore
from careeros.core.models import AutomationPolicy, Goals, Job, Profile, Skills
from careeros.operations.apply import _JD_CAP, ACTION, execute_apply, propose_apply
from careeros.operations.approvals import has_executed_approval, resolve_approval
from careeros.operations.errors import (
    BrowserUnavailable, DraftFailed, EntityNotFound, FillIncomplete,
    NoFillerAvailable, OperationError, PolicyBlocked, ResumeNotFound,
)
from careeros.runtime.automation import AutomationRuntime
from careeros.runtime.base import ActionProposal
from careeros.runtime.factory import (
    WorkspaceNotConfigured, open_automation_runtime, resolve_storage,
)
from careeros.skills.browse_query import job_query_from_profile
from careeros.skills.job_score import score_job
from careeros.sources.ats import ATSFetchError
from careeros.sources.base import job_from_posting, posting_from_scrape

discover_and_apply_app = typer.Typer(help="Unattended discover + auto-apply for scheduled runs.")

SCRAPERS: dict = {name: board.scraper for name, board in BOARDS.items()}


def _open_runtime(workspace_path: str | None) -> AutomationRuntime:
    try:
        return open_automation_runtime(resolve_storage(workspace_path))
    except (WorkspaceNotConfigured, FileNotFoundError):
        rprint("[red]No workspace configured. Run 'careeros onboard' first.[/red]")
        raise typer.Exit(1)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@discover_and_apply_app.command()
def discover_and_apply_cmd(
    board: str = typer.Option(
        None, "--board",
        help="Single browser board to run; API sources in config/sources.json are always polled.",
    ),
    workspace: str = typer.Option(None, "--workspace", help="Workspace path"),
) -> None:
    runtime = _open_runtime(workspace)

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

    if board is not None and board not in BOARDS:
        rprint("[red]Unknown board '" + board + "'. Valid: " + ", ".join(BOARDS) + "[/red]")
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
    query = job_query_from_profile(profile, goals)

    discovered: list[dict] = []
    now = _now()
    for b in boards:
        scraper = SCRAPERS[b]
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

    entries = load_board_entries(runtime.storage)
    unavailable: list[str] = []
    for entry in entries:
        label = entry.source + ":" + entry.board
        try:
            postings = build_source(entry).fetch(entry.board)
        except ATSFetchError as exc:
            unavailable.append(label)
            runtime.record_activity(runtime.new_event(
                "source_unavailable", "discover-and-apply",
                "Could not fetch " + label + ": " + str(exc),
                status="failed", entity_type="source", entity_id=label,
            ))
            continue
        for posting in postings:
            jd_text = posting.description or ""
            result = score_job(jd_text, profile, skills)
            discovered.append({
                "source_board": posting.source,
                "title": posting.title,
                "company": posting.company,
                "location": posting.location,
                "url": posting.url,
                "source_id": posting.source_id,
                "score": result["score"],
                "jd_text": jd_text,
            })

    if not boards and (not entries or len(unavailable) == len(entries)):
        # Joined with an explicit \n, not a space: rich's 80-column wrap would
        # otherwise be free to break "careeros browser login" mid-phrase,
        # which would fail an unchanged Phase 9c assertion on that exact
        # substring. Do not "clean this up" back to a single sentence.
        rprint(
            "[red]No authorized board sessions and no reachable API sources.\n"
            "Run: careeros browser login --board <name>, or check config/sources.json[/red]"
        )
        raise typer.Exit(1)

    store = JobStore(runtime.storage)
    saved_count = 0
    duplicate_count = 0
    for p in discovered:
        try:
            posting = posting_from_scrape(p)
        except ValueError as exc:
            runtime.record_activity(runtime.new_event(
                "posting_unusable", "discover-and-apply",
                "Skipped an unusable posting from " + str(p.get("source_board", "?"))
                + ": " + str(exc),
                status="failed", entity_type="job",
            ))
            continue
        job = job_from_posting(posting, now)
        job = job.model_copy(update={"description": p["jd_text"]})
        outcome = store.save_new(job)
        p["job_id"] = outcome.job.id
        p["already_applied"] = outcome.job.applied_at is not None or outcome.job.stage == "applied"
        if outcome.created:
            runtime.record_activity(runtime.new_event(
                "job_added", "discover-and-apply",
                "Job saved from " + p["source_board"] + ": " + p["company"] + " — " + p["title"],
                entity_type="job", entity_id=outcome.job.id,
            ))
            saved_count += 1
        else:
            runtime.record_activity(runtime.new_event(
                "job_merged", "discover-and-apply",
                "Job saved from " + p["source_board"] + ": " + p["company"] + " — " + p["title"],
                entity_type="job", entity_id=outcome.job.id,
            ))
            duplicate_count += 1

    # Two sources can surface the same posting in one run; both discovery
    # dicts then carry the same job_id after save_new's merge. Dedupe here,
    # keeping the highest-scoring entry, so neither the max_auto_applies_per_run
    # cap nor the reported counts are inflated by a duplicate that would never
    # actually be applied to twice (see the job.applied_at re-read below).
    best_by_job_id: dict[str, dict] = {}
    for p in discovered:
        if not p.get("job_id") or p["score"] < policy.auto_apply_min_score or p.get("already_applied"):
            continue
        job_id = p["job_id"]
        current = best_by_job_id.get(job_id)
        if current is None or p["score"] > current["score"]:
            best_by_job_id[job_id] = p
    eligible = sorted(best_by_job_id.values(), key=lambda p: p["score"], reverse=True)

    applied_count = 0
    skipped_count = 0
    blocked_count = 0
    teardown_failed_count = 0
    for p in eligible:
        if applied_count >= policy.max_auto_applies_per_run:
            break

        job_id = p["job_id"]
        job = Job.load(runtime.storage, job_id)
        if job.applied_at is not None or job.stage == "applied":
            # Re-read persisted state rather than trusting discovery-time
            # already_applied: this also covers an out-of-band apply that
            # happened mid-run (e.g. via a concurrent `careeros apply`), and
            # legacy records where stage="applied" but applied_at was never set.
            skipped_count += 1
            continue

        if has_executed_approval(runtime.storage, ACTION, job_id):
            # There is an executed apply_to_job approval for this job, but
            # applied_at was never recorded — the approval was consumed and
            # a browser was launched, yet the outcome is unknown. That can
            # be a BaseException during teardown after a successful submit,
            # or a failure inside _mark_applied's save that followed one —
            # but it can equally be an interrupt anywhere after the approval
            # was consumed and before a submission happened at all, e.g. a
            # Ctrl-C or SIGTERM during browser launch or mid-fill, with
            # nothing sent. The durable record cannot tell these apart, and
            # this is not "never attempted": re-attempting here could
            # resubmit an application that already went out, so this job is
            # skipped rather than re-proposed either way. Logged as a
            # distinct event, not folded into the neighbouring applied_at
            # skip above, because silently excluding a job forever with no
            # durable trace would leave no way for a human to notice and
            # investigate.
            runtime.record_activity(runtime.new_event(
                "apply_outcome_unknown", "discover-and-apply",
                "Skipped " + p["company"] + " — " + p["title"]
                + ": an executed apply_to_job approval exists with no "
                + "recorded applied_at — outcome unknown, not re-attempting.",
                status="failed", entity_type="job", entity_id=job_id,
            ))
            skipped_count += 1
            continue

        summary = (
            "Auto-apply (score " + str(p["score"]) + " >= threshold "
            + str(policy.auto_apply_min_score) + ") to " + p["company"] + " — " + p["title"]
        )

        try:
            proposal = propose_apply(
                runtime, job_id, action_label="discover-and-apply", summary=summary,
                # This run's freshly-fetched JD text, not job.description:
                # JobStore._merge only fills description when the existing
                # record's value is absent, so a rediscovered job (cap
                # reached, draft failed, no filler, or score risen above
                # threshold on a prior run) would otherwise draft from a
                # stale JD. _JD_CAP is imported from apply.py so the two
                # can never drift apart.
                jd_text=p["jd_text"][:_JD_CAP],
            )
        except PolicyBlocked:
            # Already logged (policy_blocked) inside propose_apply.
            blocked_count += 1
            continue
        except DraftFailed:
            runtime.record_activity(runtime.new_event(
                "cover_letter_failed", "discover-and-apply",
                "Cover letter generation failed for " + p["company"] + " — " + p["title"],
                status="failed", entity_type="job", entity_id=job_id,
            ))
            skipped_count += 1
            continue
        except ResumeNotFound:
            rprint("[yellow]No resume found — skipping auto-apply for " + p["company"] + ".[/yellow]")
            skipped_count += 1
            continue
        except NoFillerAvailable:
            runtime.record_activity(runtime.new_event(
                "no_filler_available", "discover-and-apply",
                "No filler available for " + p["company"] + " — " + p["title"],
                status="failed", entity_type="job", entity_id=job_id,
            ))
            skipped_count += 1
            continue
        except EntityNotFound as exc:
            # Job not found, or no URL on file: not distinguished by the
            # unattended run before this refactor either, since neither
            # case could occur for a job this loop just saved itself.
            runtime.record_activity(runtime.new_event(
                "apply_error", "discover-and-apply",
                "Error while applying to " + p["company"] + " — " + p["title"]
                + ": " + type(exc).__name__,
                status="failed", entity_type="job", entity_id=job_id,
            ))
            skipped_count += 1
            continue
        except BrowserUnavailable as exc:
            if exc.profile_busy:
                # A locked profile is a whole-run condition, not a per-job
                # one: continuing would re-run a paid cover-letter generation
                # for every remaining job only to fail identically at
                # launch. Match the discovery loop and stop.
                rprint("[red]" + str(exc) + "[/red]")
                raise typer.Exit(1)
            runtime.record_activity(runtime.new_event(
                "apply_error", "discover-and-apply",
                "Error while applying to " + p["company"] + " — " + p["title"]
                + ": " + type(exc).__name__,
                status="failed", entity_type="job", entity_id=job_id,
            ))
            skipped_count += 1
            continue
        except OperationError as exc:
            runtime.record_activity(runtime.new_event(
                "apply_error", "discover-and-apply",
                "Error while applying to " + p["company"] + " — " + p["title"]
                + ": " + type(exc).__name__,
                status="failed", entity_type="job", entity_id=job_id,
            ))
            skipped_count += 1
            continue
        except Exception as exc:
            runtime.record_activity(runtime.new_event(
                "apply_error", "discover-and-apply",
                "Error while applying to " + p["company"] + " — " + p["title"]
                + ": " + type(exc).__name__,
                status="failed", entity_type="job", entity_id=job_id,
            ))
            skipped_count += 1
            continue

        # Announced only once this job is actually going to be applied to:
        # propose_apply already refused a job blocked by policy or lacking a
        # resume/filler/cover letter before this point.
        job_label = p["company"] + " / " + p["title"]
        resume_choice = proposal.resume
        try:
            if resume_choice.tailored:
                if resume_choice.variant is None:
                    # Unattended, so this cannot offer to regenerate and must
                    # not print an entry count it does not have: the sidecar
                    # is missing, corrupt, or describes a different document.
                    rprint("Resume: tailored for " + job_label
                           + ", evidence record unavailable — run 'careeros resume variant "
                           + "--job " + job_id + "' to regenerate it.")
                else:
                    rprint("Resume: tailored for " + job_label + " — "
                           + str(resume_choice.variant.entry_count())
                           + " evidence-backed entries.")
                    if resume_choice.stale_master:
                        rprint("Resume: generated from a superseded master resume, so its "
                               + "cited lines no longer match "
                               + resume_choice.variant.source_file
                               + " — run 'careeros resume variant --job " + job_id
                               + "' to regenerate it.")
            else:
                rprint("Resume: " + resume_choice.storage_path
                       + " — NOT tailored to " + job_label + ".")

            result = runtime.request_approval(ActionProposal(
                action=ACTION,
                summary=proposal.summary,
                entity_type="job", entity_id=job_id,
            ))
            resolve_approval(runtime, proposal.approval_id, result, action_label="discover-and-apply")
        except OperationError as exc:
            # request_approval/resolve_approval used to sit inside the same
            # guarded try pre-refactor. A concurrent decision on this
            # approval (ApprovalNotGranted) or a storage failure here must
            # sink this one job, not the whole scheduled run — same property
            # the bare except Exception below exists to preserve.
            runtime.record_activity(runtime.new_event(
                "apply_error", "discover-and-apply",
                "Error while applying to " + p["company"] + " — " + p["title"]
                + ": " + type(exc).__name__,
                status="failed", entity_type="job", entity_id=job_id,
            ))
            skipped_count += 1
            continue
        except Exception as exc:
            runtime.record_activity(runtime.new_event(
                "apply_error", "discover-and-apply",
                "Error while applying to " + p["company"] + " — " + p["title"]
                + ": " + type(exc).__name__,
                status="failed", entity_type="job", entity_id=job_id,
            ))
            skipped_count += 1
            continue

        if not result.approved:
            skipped_count += 1
            continue

        try:
            apply_outcome = execute_apply(
                runtime, proposal.approval_id, headless=True, action_label="discover-and-apply"
            )
        except FillIncomplete:
            # execute_apply already logged apply_incomplete.
            skipped_count += 1
            continue
        except BrowserUnavailable as exc:
            if exc.profile_busy:
                rprint("[red]" + str(exc) + "[/red]")
                raise typer.Exit(1)
            # execute_apply already logged apply_failed.
            skipped_count += 1
            continue
        except OperationError as exc:
            # ApprovalNotGranted, MalformedApproval, WrongApprovalAction, and
            # ArtifactChanged are all raised before mark_executed, with no
            # record_activity of their own — unlike FillIncomplete and
            # BrowserUnavailable above, nothing has logged this refusal yet.
            # Without this, a refused submission (e.g. the approved cover
            # letter, resume, or profile bytes changed since approval — the
            # exact tamper-or-corruption signal the digest binding exists to
            # catch) would leave no durable trace, only a console Skipped
            # count nobody reads the next morning.
            runtime.record_activity(runtime.new_event(
                "apply_error", "discover-and-apply",
                "Error while applying to " + p["company"] + " — " + p["title"]
                + ": " + type(exc).__name__,
                status="failed", entity_type="job", entity_id=job_id,
            ))
            skipped_count += 1
            continue
        except Exception as exc:
            runtime.record_activity(runtime.new_event(
                "apply_error", "discover-and-apply",
                "Error while applying to " + p["company"] + " — " + p["title"]
                + ": " + type(exc).__name__,
                status="failed", entity_type="job", entity_id=job_id,
            ))
            skipped_count += 1
            continue

        applied_count += 1
        if apply_outcome.teardown_failed:
            teardown_failed_count += 1
            rprint(
                "[yellow]Warning: browser teardown failed after the submission "
                "to " + p["company"] + " — " + p["title"] + " went through. "
                "See the activity log (apply_teardown_failed) for details.[/yellow]"
            )

    rprint(
        "Discovered: " + str(saved_count) + ", Duplicates: " + str(duplicate_count)
        + ", Blocked: " + str(blocked_count)
        + ", Auto-applied: " + str(applied_count) + ", Skipped: " + str(skipped_count)
        + ", Teardown warnings: " + str(teardown_failed_count)
        + ", Unauthorized boards: " + (", ".join(unauthorized) if unauthorized else "none")
        + ", Unavailable sources: " + (", ".join(unavailable) if unavailable else "none")
    )
