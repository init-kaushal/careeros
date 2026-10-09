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


# --- legacy (schema 0) workspaces ---------------------------------------------------

LEGACY_JOBS = [
    # slug, title, company, legacy status, pipeline icon the legacy skills would show
    ("acme-backend-engineer", "Backend Engineer", "Acme", "discovered", " "),
    ("globex-senior-engineer-at-scale", "Senior Engineer at Scale", "Globex", "discovered", " "),
    ("initech-platform-engineer", "Platform Engineer", "Initech", "applied", "~"),
    ("hooli-staff-engineer", "Staff Engineer", "Hooli", "interview", "?"),
    ("umbrella-sde-3", "SDE-3", "Umbrella", "offer", "✓"),
    ("wayne-principal-engineer", "Principal Engineer", "Wayne", "accepted", "✓"),
    ("stark-engineer", "Engineer", "Stark", "declined", "x"),
    ("oscorp-engineer", "Engineer", "Oscorp", "closed", "x"),
]


def legacy_job_text(title: str, company: str, url: str, status: str, discovered: str = "2026-10-01") -> str:
    return (
        f"# {title} at {company}\n\n"
        f"- **Board:** LinkedIn\n- **Location:** Bengaluru\n- **Market:** home\n"
        f"- **URL:** {url}\n- **Score:** 8\n- **Discovered:** {discovered}\n- **Status:** {status}\n\n"
        f"## Notes\nSnippet text — with unicode ✓\n"
    )


def make_legacy_workspace(root: Path) -> None:
    """A schema-0 workspace shaped like the real one: no metadata, no frontmatter, no ledger."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "profile.md").write_text("# Kaushal's Career Profile\n\n## Markets\n| Market | Min base |\n", encoding="utf-8")
    (root / "boards.md").write_text("# Job Boards\n\n### linkedin\n- **Market:** home\n", encoding="utf-8")
    (root / "resume.md").write_text("# Resume\n\n## Education\n", encoding="utf-8")
    (root / "activity.md").write_text(
        "# Activity Log\n\n<!-- append-only; newest entries at top -->\n2026-09-29 careeros update: variants\n",
        encoding="utf-8",
    )
    (root / "resume-variants").mkdir()
    (root / "resume-variants" / "Resume.pdf").write_bytes(b"%PDF-1.4\n%fixture \x00\xff\n")
    (root / "markets").mkdir()
    (root / "markets" / "singapore.md").write_text("# Singapore — Market Notes\n", encoding="utf-8")
    pipeline = ["# Job Pipeline", "", "## Active", ""]
    for slug, title, company, status, icon in LEGACY_JOBS:
        url = f"https://example.com/jobs/{slug}"
        job_dir = root / "jobs" / "discovered" / slug
        job_dir.mkdir(parents=True)
        (job_dir / "job.md").write_text(legacy_job_text(title, company, url, status), encoding="utf-8")
        pipeline.append(f"- [{icon}] **{company}** — {title} · Bengaluru · Score 8 · 2026-10-01 · {url}")
    (root / "jobs" / "discovered" / "acme-backend-engineer" / "people.md").write_text("# Hiring contacts\n", encoding="utf-8")
    (root / "jobs" / "pipeline.md").write_text("\n".join(pipeline) + "\n", encoding="utf-8")


def snapshot(root: Path) -> dict[str, bytes]:
    """Every file in the workspace except CareerOS's own bookkeeping, keyed by relative path."""
    skip = {".careeros", "ledger.jsonl"}
    return {
        p.relative_to(root).as_posix(): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file() and p.relative_to(root).parts[0] not in skip
    }


def make_claude_workspace(root: Path, framework: str = "0.1.0") -> Path:
    """A scaffolded Claude workspace with metadata at an older framework version and a ledger."""
    from careeros.workspace.scaffold import scaffold

    scaffold(root, runtime="claude")
    (root / "profile.md").write_text("# my profile\n", encoding="utf-8")
    save_meta(root, WorkspaceMeta(versions.SCHEMA_VERSION, framework, TS, TS, ("claude",)))
    ledger.append_event(root, type="workspace.created", actor="system", action="created")
    return root
