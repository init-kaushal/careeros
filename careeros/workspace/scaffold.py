"""Scaffold a CareerOS Cowork workspace from bundled templates."""

import shutil
from pathlib import Path

_RUNTIME_CONFIG: dict[str, dict] = {
    "claude": {
        "templates_dir": Path(__file__).parent.parent / "templates" / "workspace",
        "sentinel": "CLAUDE.md",
        "framework_prefixes": (".claude/skills/",),
    },
    "gpt": {
        "templates_dir": Path(__file__).parent.parent / "templates" / "gpt-workspace",
        "sentinel": "AGENTS.md",
        "framework_prefixes": (".gpt/skills/",),
    },
}


def is_scaffolded(path: Path, runtime: str = "claude") -> bool:
    cfg = _RUNTIME_CONFIG[runtime]
    return (path / cfg["sentinel"]).exists()


def scaffold(path: Path, *, refresh: bool = False, runtime: str = "claude") -> list[str]:
    """
    Copy framework templates to `path`. Returns list of relative file paths written.

    First run (refresh=False): copies all templates. Raises FileExistsError if
    the sentinel file already exists.

    Refresh run (refresh=True): re-copies only framework-owned files (skill files
    and the entry file, whose routing table lists the skills) so the user picks up
    updates from newer CareerOS versions without touching their profile, pipeline,
    or activity log. A stale entry file is saved as `<name>.bak` before it is replaced.
    """
    if runtime not in _RUNTIME_CONFIG:
        raise ValueError(f"Unknown runtime '{runtime}'. Choose: {', '.join(_RUNTIME_CONFIG)}")

    cfg = _RUNTIME_CONFIG[runtime]
    templates_dir: Path = cfg["templates_dir"]
    sentinel: str = cfg["sentinel"]
    framework_prefixes: tuple = cfg["framework_prefixes"]

    if not refresh and (path / sentinel).exists():
        raise FileExistsError(
            f"Workspace already exists at {path}.\n"
            "Use --refresh to update skill files from the latest CareerOS version."
        )

    path.mkdir(parents=True, exist_ok=True)
    written: list[str] = []

    for src in templates_dir.rglob("*"):
        if src.is_dir():
            continue

        rel = src.relative_to(templates_dir)
        rel_str = rel.as_posix()
        dst = path / rel

        if refresh:
            is_framework = rel_str == sentinel or any(
                rel_str.startswith(p) for p in framework_prefixes
            )
            if not is_framework:
                continue
            if rel_str == sentinel and dst.exists():
                if dst.read_bytes() == src.read_bytes():
                    continue
                backup = dst.with_name(dst.name + ".bak")
                shutil.copy2(dst, backup)
                written.append(f"{rel_str}.bak")

        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        written.append(rel_str)

    return written
