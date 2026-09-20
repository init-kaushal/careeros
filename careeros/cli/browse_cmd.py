from __future__ import annotations

from datetime import datetime, timezone

import typer
from rich import print as rprint
from rich.console import Console
from rich.prompt import Prompt
from rich.table import Table

from careeros.browser.boards import BOARDS
from careeros.browser.driver import BrowserProfileBusy, fetch_jd_text, launch_browser
from careeros.browser.scrapers.generic import GenericScraper
from careeros.cli.preflight import require_board_session
from careeros.config import GlobalConfig
from careeros.core.job_store import JobStore
from careeros.core.models import Goals, Profile, Skills
from careeros.runtime.factory import open_local_runtime
from careeros.skills.browse_query import job_query_from_profile
from careeros.skills.job_score import score_job
from careeros.sources.base import job_from_posting, posting_from_scrape
from careeros.storage.filesystem import LocalFilesystemStorage

browse_app = typer.Typer(name="browse", help="Search job boards using your browser session.")
console = Console()

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


@browse_app.command()
def browse_cmd(
    board: str = typer.Option(..., "--board", help="linkedin | indeed | wellfound | url"),
    url: str = typer.Option(None, "--url", help="Target URL (required when --board url)"),
    limit: int = typer.Option(20, "--limit", help="Max listings to fetch"),
    min_score: int = typer.Option(0, "--min-score", help="Minimum score to display"),
    headless: bool = typer.Option(False, "--headless/--no-headless", help="Run browser headlessly"),
    workspace: str = typer.Option(None, "--workspace", help="Workspace path"),
) -> None:
    valid_boards = set(BOARDS) | {"url"}
    if board not in valid_boards:
        rprint(f"[red]Invalid --board '{board}'. Valid: {' '.join(sorted(valid_boards))}[/red]")
        raise typer.Exit(1)

    if board == "url" and not url:
        rprint("[red]Provide --url when using --board url.[/red]")
        raise typer.Exit(1)

    if board != "url":
        require_board_session(board)

    try:
        runtime = open_local_runtime(_get_storage(workspace))
    except FileNotFoundError:
        rprint("[red]No workspace configured. Run 'careeros onboard' first.[/red]")
        raise typer.Exit(1)

    try:
        profile = Profile.load(runtime.storage)
    except FileNotFoundError:
        rprint("[red]No profile found. Run 'careeros onboard' first.[/red]")
        raise typer.Exit(1)

    skills = Skills.load_or_empty(runtime.storage)
    goals = Goals.load_or_empty(runtime.storage)
    query = job_query_from_profile(profile, goals) if board != "url" else (url or "")
    scraper = GenericScraper() if board == "url" else SCRAPERS[board]

    try:
        with launch_browser(headless=headless) as (_, page):
            try:
                postings = scraper.search(page, query, limit)
            except Exception as exc:
                rprint(f"[yellow]Warning: could not search {board}: {exc}[/yellow]")
                postings = []

            rprint(f"Scoring {len(postings)} listings...")
            scored = []
            for posting in postings:
                jd_text = fetch_jd_text(page, posting["url"])
                result = score_job(jd_text, profile, skills)
                scored.append({
                    **posting,
                    "score": result["score"],
                    # score_job returns the raw parsed model JSON and coerces only
                    # "score"; a model that omits "reasoning" must not abort the run.
                    "reasoning": result.get("reasoning", ""),
                })
    except ImportError:
        rprint("[red]Playwright is not installed.[/red]")
        rprint("Run: [bold]pip install playwright && playwright install chrome[/bold]")
        raise typer.Exit(1)
    except BrowserProfileBusy as exc:
        rprint("[red]" + str(exc) + "[/red]")
        raise typer.Exit(1)

    filtered = [p for p in scored if p["score"] >= min_score]
    filtered.sort(key=lambda p: p["score"], reverse=True)

    if not filtered:
        rprint(f"No jobs found matching min-score {min_score}.")
        return

    table = Table(show_header=True)
    table.add_column("#", style="bold")
    table.add_column("Score")
    table.add_column("Company")
    table.add_column("Title")
    table.add_column("Location")
    table.add_column("URL")
    for i, p in enumerate(filtered, 1):
        table.add_row(str(i), str(p["score"]), p["company"], p["title"], p.get("location") or "—", p["url"])
    console.print(table)

    picks_str = Prompt.ask("Pick jobs to save (e.g. 1 3 5, or q to quit)")
    if picks_str.strip().lower() == "q":
        return

    saved = 0
    duplicates = 0
    seen_indices: set[int] = set()
    now = _now()
    store = JobStore(runtime.storage)
    for part in picks_str.split():
        if not part.isdigit():
            continue
        idx = int(part) - 1
        if not (0 <= idx < len(filtered)) or idx in seen_indices:
            continue
        seen_indices.add(idx)
        p = filtered[idx]
        try:
            posting = posting_from_scrape(p)
        except ValueError as exc:
            rprint("[yellow]Warning: skipping listing, " + str(exc) + "[/yellow]")
            continue
        outcome = store.save_new(job_from_posting(posting, now))
        runtime.record_activity(runtime.new_event(
            "job_added" if outcome.created else "job_merged", "browse",
            "Job saved from " + p["source_board"] + ": " + p["company"] + " — " + p["title"],
            entity_type="job", entity_id=outcome.job.id,
        ))
        if outcome.created:
            saved += 1
        else:
            duplicates += 1

    rprint("[green]Saved " + str(saved) + " job(s), Duplicates: " + str(duplicates) + "[/green]")
