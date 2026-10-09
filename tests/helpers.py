"""Builders for test workspaces."""

from __future__ import annotations

from pathlib import Path

from careeros.core import ids, ledger, versions
from careeros.core.models import State, WorkspaceMeta
from careeros.core.workspace import join_frontmatter, save_meta

TS = "2026-10-01T00:00:00Z"


def make_workspace(root: Path, framework_version: str | None = None) -> Path:
    """A schema-1 workspace with a profile, an activity log, an empty pipeline and no jobs."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "profile.md").write_text("# Profile\n", encoding="utf-8")
    (root / "jobs" / "discovered").mkdir(parents=True, exist_ok=True)
    (root / "activity.md").write_text("# Activity Log\n", encoding="utf-8")
    (root / "jobs" / "pipeline.md").write_text("# Job Pipeline\n\n## Active\n\n", encoding="utf-8")
    save_meta(
        root,
        WorkspaceMeta(
            versions.SCHEMA_VERSION, framework_version or versions.installed_version(), TS, TS, ("claude",)
        ),
    )
    return root


def add_job(
    root: Path,
    slug: str,
    *,
    status: State = State.DISCOVERED,
    company: str = "Acme",
    title: str = "Backend Engineer",
    url: str | None = None,
    bullet: str = "discovered",
    baseline: bool = True,
    pipeline: bool = True,
) -> dict:
    url = url or f"https://example.com/jobs/{slug}"
    job_id = ids.new_id("job")
    frontmatter = {
        "id": job_id, "type": "job", "schema": 1, "status": status.value,
        "company": company, "title": title, "url": url, "created_at": TS, "updated_at": TS,
    }
    body = f"# {title} at {company}\n\n- **URL:** {url}\n- **Status:** {bullet}\n\n## Notes\nhello\n"
    path = root / "jobs" / "discovered" / slug / "job.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(join_frontmatter(frontmatter, body), encoding="utf-8")
    if pipeline:
        with (root / "jobs" / "pipeline.md").open("a", encoding="utf-8") as handle:
            handle.write(f"- [ ] **{company}** — {title} · Remote · Score 8 · 2026-10-01 · {url}\n")
    if baseline:
        ledger.append_event(
            root, type="job.imported", actor="system", entity=job_id, new_state=status.value,
            action=f"imported {company}", source="migration",
        )
    return {"id": job_id, "path": path, "url": url, "slug": slug}
