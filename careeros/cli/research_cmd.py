from __future__ import annotations

from datetime import datetime, timezone
import re
import urllib.parse

import typer
from rich import print as rprint

from careeros.browser.driver import fetch_jd_text, launch_browser
from careeros.browser.scrapers.people_search import PeopleSearchScraper
from careeros.config import GlobalConfig
from careeros.core.ids import make_company_id, make_compensation_id, make_person_id
from careeros.core.models import Company, CompensationDataPoint, Job, Person, Preferences
from careeros.runtime.factory import open_local_runtime
from careeros.skills.company_research import extract_company_info
from careeros.skills.compensation_research import extract_compensation_data
from careeros.skills.people_research import classify_person_role
from careeros.storage.filesystem import LocalFilesystemStorage

research_app = typer.Typer(help="Research companies and people for outreach.")


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


@research_app.command()
def company(
    job: str = typer.Option(..., "--job", help="Job ID to research the company for"),
    workspace: str = typer.Option(None, "--workspace", help="Workspace path"),
) -> None:
    try:
        runtime = open_local_runtime(_get_storage(workspace))
    except FileNotFoundError:
        rprint("[red]No workspace configured. Run 'careeros onboard' first.[/red]")
        raise typer.Exit(1)

    try:
        job_obj = Job.load(runtime.storage, job)
    except (FileNotFoundError, ValueError):
        rprint("[red]Job " + job + " not found.[/red]")
        raise typer.Exit(1)

    company_id = make_company_id(job_obj.company)
    company_search_url = "https://www.linkedin.com/search/results/companies/?keywords=" + urllib.parse.quote(job_obj.company)

    try:
        with launch_browser(headless=True) as (_, page):
            page_content = fetch_jd_text(page, company_search_url)
    except ImportError:
        rprint("[red]Playwright is not installed.[/red]")
        raise typer.Exit(1)

    info = extract_company_info(page_content)
    company_obj = Company(
        id=company_id, name=job_obj.company,
        industry=info["industry"], size=info["size"], notes=info["notes"],
        researched_at=_now(),
    )
    company_obj.save(runtime.storage)
    runtime.record_activity(runtime.new_event(
        "company_researched", "research",
        "Researched company: " + job_obj.company,
        entity_type="company", entity_id=company_id,
    ))
    rprint("[green]Researched " + job_obj.company + "[/green]")


@research_app.command()
def people(
    job: str = typer.Option(..., "--job", help="Job ID to research people for"),
    workspace: str = typer.Option(None, "--workspace", help="Workspace path"),
) -> None:
    try:
        runtime = open_local_runtime(_get_storage(workspace))
    except FileNotFoundError:
        rprint("[red]No workspace configured. Run 'careeros onboard' first.[/red]")
        raise typer.Exit(1)

    try:
        job_obj = Job.load(runtime.storage, job)
    except (FileNotFoundError, ValueError):
        rprint("[red]Job " + job + " not found.[/red]")
        raise typer.Exit(1)

    company_id = make_company_id(job_obj.company)
    scraper = PeopleSearchScraper()

    try:
        with launch_browser(headless=True) as (_, page):
            try:
                results = scraper.search(page, job_obj.company, 10)
            except Exception as exc:
                rprint("[yellow]Warning: could not search people: " + str(exc) + "[/yellow]")
                results = []
    except ImportError:
        rprint("[red]Playwright is not installed.[/red]")
        raise typer.Exit(1)

    found = 0
    for r in results:
        role_category = classify_person_role(r["name"], r["title"])
        person_id = make_person_id(r["name"], company_id)
        person_obj = Person(
            id=person_id, company_id=company_id, name=r["name"],
            role_category=role_category, title=r["title"], linkedin_url=r.get("linkedin_url"),
            researched_at=_now(),
        )
        person_obj.save(runtime.storage)
        runtime.record_activity(runtime.new_event(
            "people_researched", "research",
            "Found " + r["name"] + " (" + role_category + ") at " + job_obj.company,
            entity_type="person", entity_id=person_id,
        ))
        found += 1

    rprint("[green]Found " + str(found) + " people[/green]")


def _slugify(s: str) -> str:
    s = s.lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-")


@research_app.command()
def compensation(
    job: str = typer.Option(..., "--job", help="Job ID to research compensation for"),
    workspace: str = typer.Option(None, "--workspace", help="Workspace path"),
) -> None:
    try:
        runtime = open_local_runtime(_get_storage(workspace))
    except FileNotFoundError:
        rprint("[red]No workspace configured. Run 'careeros onboard' first.[/red]")
        raise typer.Exit(1)

    try:
        job_obj = Job.load(runtime.storage, job)
    except (FileNotFoundError, ValueError):
        rprint("[red]Job " + job + " not found.[/red]")
        raise typer.Exit(1)

    prefs = Preferences.load_or_empty(runtime.storage)

    url = (
        "https://www.levels.fyi/companies/" + urllib.parse.quote(_slugify(job_obj.company))
        + "/salaries/" + urllib.parse.quote(_slugify(job_obj.title))
    )

    try:
        with launch_browser(headless=True) as (_, page):
            page_content = fetch_jd_text(page, url)
    except ImportError:
        rprint("[red]Playwright is not installed.[/red]")
        raise typer.Exit(1)

    data = extract_compensation_data(page_content)
    comp_id = make_compensation_id(job_obj.company, job_obj.title)
    point = CompensationDataPoint(
        id=comp_id, job_id=job, role=job_obj.title, seniority=prefs.seniority,
        geo=job_obj.location, company=job_obj.company, source_url=url,
        base_min=data["base_min"], base_max=data["base_max"],
        bonus=data["bonus"], equity=data["equity"], confidence=data["confidence"],
        researched_at=_now(),
    )
    point.save(runtime.storage)
    runtime.record_activity(runtime.new_event(
        "compensation_researched", "research",
        "Researched compensation for " + job_obj.title + " at " + job_obj.company
        + " (confidence: " + data["confidence"] + ")",
        entity_type="compensation", entity_id=comp_id,
    ))
    rprint("[green]Researched compensation for " + job_obj.title + " at " + job_obj.company + "[/green]")
