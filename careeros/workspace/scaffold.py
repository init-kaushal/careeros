"""Scaffold a CareerOS Cowork workspace from bundled templates."""

import shutil
from pathlib import Path

_TEMPLATES_DIR = Path(__file__).parent.parent / "templates" / "workspace"
_SENTINEL = "CLAUDE.md"

# Files owned by the framework — always refreshed on --refresh runs.
# User data files (profile.md, boards.md, jobs/, activity.md) are never touched.
_FRAMEWORK_PREFIXES = (".claude/skills/",)


def is_scaffolded(path: Path) -> bool:
    return (path / _SENTINEL).exists()


def scaffold(path: Path, *, refresh: bool = False) -> list[str]:
    """
    Copy framework templates to `path`. Returns list of relative file paths written.

    First run (refresh=False): copies all templates. Raises FileExistsError if
    CLAUDE.md already exists.

    Refresh run (refresh=True): re-copies only framework-owned files (skill files)
    so the user picks up updates from newer CareerOS versions without touching
    their profile, pipeline, or activity log.
    """
    if not refresh and is_scaffolded(path):
        raise FileExistsError(
            f"Workspace already exists at {path}.\n"
            "Use --refresh to update skill files from the latest CareerOS version."
        )

    path.mkdir(parents=True, exist_ok=True)
    written: list[str] = []

    for src in _TEMPLATES_DIR.rglob("*"):
        if src.is_dir():
            continue

        rel = src.relative_to(_TEMPLATES_DIR)
        rel_str = rel.as_posix()
        dst = path / rel

        if refresh:
            is_framework = any(rel_str.startswith(p) for p in _FRAMEWORK_PREFIXES)
            if not is_framework:
                continue

        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        written.append(rel_str)

    return written
