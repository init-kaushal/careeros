"""Workspace discovery, metadata, frontmatter, atomic writes, the workspace lock, job lookup."""

from __future__ import annotations

import os
import re
import tempfile
import threading
import time
from datetime import date, datetime, timezone
from pathlib import Path

import yaml

from careeros.core import versions
from careeros.core.models import Job, State, WorkspaceMeta

META_REL = Path(".careeros") / "workspace.yaml"
LOCK_REL = Path(".careeros") / "lock"
RECOVERY_REL = Path(".careeros") / "recovery"
BACKUPS_REL = Path(".careeros") / "backups"
LEDGER_REL = Path("ledger.jsonl")

JOB_KEYS = ("id", "type", "status", "company", "title", "created_at", "updated_at")


class WorkspaceError(Exception):
    """A problem with the workspace that the user can act on."""


class LockTimeout(WorkspaceError):
    """The workspace lock could not be acquired in time."""


# --- atomic writes ---------------------------------------------------------------

def _fsync_dir(directory: Path) -> None:
    try:
        fd = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def atomic_write_bytes(path: Path, data: bytes) -> None:
    """Write via a temp file in the same directory, fsync, then atomic rename."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        mode = path.stat().st_mode & 0o777
    else:
        mask = os.umask(0)
        os.umask(mask)
        mode = 0o666 & ~mask
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise
    _fsync_dir(path.parent)


# --- workspace lock --------------------------------------------------------------

try:
    import fcntl

    def _try_lock(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _unlock(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_UN)

    _CONTENDED: tuple[type[BaseException], ...] = (BlockingIOError,)
except ImportError:  # Windows
    import msvcrt

    def _try_lock(fd: int) -> None:
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)

    def _unlock(fd: int) -> None:
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)

    _CONTENDED = (OSError,)

_local = threading.local()


class WorkspaceLock:
    """Exclusive across processes and threads; re-entrant within a thread."""

    def __init__(self, root: Path, timeout: float = 10.0) -> None:
        self.root = Path(root)
        self.timeout = timeout
        self._path = self.root / LOCK_REL

    def _held(self) -> dict[str, list[int]]:
        if not hasattr(_local, "held"):
            _local.held = {}
        return _local.held

    def __enter__(self) -> "WorkspaceLock":
        key = str(self._path.resolve())
        held = self._held()
        if key in held:
            held[key][1] += 1
            return self
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self._path, os.O_CREAT | os.O_RDWR, 0o600)
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                _try_lock(fd)
                break
            except _CONTENDED:
                if time.monotonic() >= deadline:
                    os.close(fd)
                    raise LockTimeout(
                        "another careeros command is running in this workspace; try again in a moment"
                    ) from None
                time.sleep(0.05)
        held[key] = [fd, 1]
        return self

    def __exit__(self, *exc: object) -> bool:
        key = str(self._path.resolve())
        held = self._held()
        entry = held[key]
        entry[1] -= 1
        if entry[1] == 0:
            try:
                _unlock(entry[0])
            finally:
                os.close(entry[0])
                del held[key]
        return False


# --- frontmatter -----------------------------------------------------------------

_FENCE_LINE = re.compile(r"^---[ \t]*$", re.M)


def split_frontmatter(text: str) -> tuple[dict | None, str]:
    """Return (frontmatter dict, body). The body is exactly the text after the closing fence."""
    if not text.startswith("---\n"):
        return None, text
    match = _FENCE_LINE.search(text, 4)
    if not match:
        return None, text
    block = text[4:match.start()]
    rest = match.end()
    if text[rest:rest + 1] == "\n":
        rest += 1
    try:
        data = yaml.safe_load(block) if block.strip() else {}
    except yaml.YAMLError as exc:
        raise WorkspaceError(f"frontmatter is not valid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise WorkspaceError("frontmatter must be a mapping of keys to values")
    return data, text[rest:]


def join_frontmatter(data: dict, body: str) -> str:
    dumped = yaml.safe_dump(
        data, sort_keys=False, allow_unicode=True, default_flow_style=False, width=10_000
    )
    return f"---\n{dumped}---\n{body}"


def normalize_value(value: object) -> object:
    """YAML turns unquoted timestamps into datetime/date; bring them back to canonical strings."""
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            value = value.astimezone(timezone.utc).replace(tzinfo=None)
        return value.strftime("%Y-%m-%dT%H:%M:%SZ")
    if isinstance(value, date):
        return value.isoformat()
    return value


# --- discovery, metadata, guard --------------------------------------------------

def is_workspace_root(path: Path) -> bool:
    path = Path(path)
    return (path / META_REL).is_file() or ((path / "profile.md").is_file() and (path / "jobs").is_dir())


def find_workspace(explicit: Path | str | None = None, start: Path | None = None) -> Path:
    if explicit is not None:
        root = Path(explicit).expanduser().resolve()
        if not is_workspace_root(root):
            raise WorkspaceError(
                f"{root} is not a CareerOS workspace (no .careeros/workspace.yaml, and no profile.md with jobs/)"
            )
        return root
    current = (start or Path.cwd()).resolve()
    for candidate in (current, *current.parents):
        if is_workspace_root(candidate):
            return candidate
    raise WorkspaceError("no CareerOS workspace found here or in any parent directory; use --workspace PATH")


def load_meta(root: Path) -> WorkspaceMeta | None:
    path = Path(root) / META_REL
    if not path.exists():
        return None
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise WorkspaceError(f"{META_REL}: not valid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise WorkspaceError(f"{META_REL}: expected a mapping of keys to values")
    for key in ("schema_version", "framework_version", "created_at", "updated_at"):
        if key not in data:
            raise WorkspaceError(f"{META_REL}: missing key {key!r}")
    schema = data["schema_version"]
    if not isinstance(schema, int) or isinstance(schema, bool):
        raise WorkspaceError(f"{META_REL}: schema_version must be an integer")
    framework = str(data["framework_version"])
    try:
        versions.parse_version(framework)
    except ValueError as exc:
        raise WorkspaceError(f"{META_REL}: {exc}") from exc
    return WorkspaceMeta(
        schema_version=schema,
        framework_version=framework,
        created_at=str(normalize_value(data["created_at"])),
        updated_at=str(normalize_value(data["updated_at"])),
        runtimes=tuple(str(r) for r in (data.get("runtimes") or ())),
    )


def save_meta(root: Path, meta: WorkspaceMeta) -> None:
    data = {
        "schema_version": meta.schema_version,
        "framework_version": meta.framework_version,
        "created_at": meta.created_at,
        "updated_at": meta.updated_at,
        "runtimes": list(meta.runtimes),
    }
    text = yaml.safe_dump(data, sort_keys=False, allow_unicode=True, default_flow_style=False)
    atomic_write_bytes(Path(root) / META_REL, text.encode("utf-8"))


def ensure_writable(root: Path) -> WorkspaceMeta | None:
    """Refuse to change a workspace that a newer CareerOS has touched. Legacy workspaces return None."""
    meta = load_meta(root)
    if meta is None:
        return None
    if meta.schema_version > versions.SCHEMA_VERSION:
        raise WorkspaceError(
            f"workspace schema {meta.schema_version} is newer than this CareerOS supports "
            f"({versions.SCHEMA_VERSION}); upgrade CareerOS before changing it"
        )
    installed = versions.installed_version()
    if versions.compare_versions(installed, meta.framework_version) < 0:
        raise WorkspaceError(
            f"workspace was last updated by CareerOS {meta.framework_version}, which is newer than "
            f"the installed {installed}; upgrade CareerOS before changing it"
        )
    return meta


# --- jobs ------------------------------------------------------------------------

def job_files(root: Path) -> list[Path]:
    base = Path(root) / "jobs" / "discovered"
    return sorted(base.glob("*/job.md")) if base.is_dir() else []


def read_job(path: Path) -> Job:
    path = Path(path)
    fm, body = split_frontmatter(path.read_text(encoding="utf-8"))
    if fm is None:
        raise WorkspaceError(f"{path}: no frontmatter; run `careeros migrate`")
    fm = {k: normalize_value(v) for k, v in fm.items()}
    missing = [k for k in JOB_KEYS if k not in fm]
    if missing:
        raise WorkspaceError(f"{path}: frontmatter is missing {', '.join(missing)}")
    try:
        status = State(fm["status"])
    except ValueError:
        raise WorkspaceError(f"{path}: invalid status {fm['status']!r}") from None
    return Job(
        id=str(fm["id"]),
        status=status,
        company=str(fm["company"]),
        title=str(fm["title"]),
        url=fm.get("url"),
        created_at=str(fm["created_at"]),
        updated_at=str(fm["updated_at"]),
        path=path,
        archived=bool(fm.get("archived", False)),
        archived_at=fm.get("archived_at"),
        frontmatter=fm,
        body=body,
    )


def resolve_job(root: Path, ref: str) -> Job:
    """Find a job by ID, by directory slug, or by path."""
    root = Path(root)
    matches: list[Path] = []
    for path in job_files(root):
        if ref == path.parent.name:
            matches.append(path)
            continue
        try:
            if read_job(path).id == ref:
                matches.append(path)
        except WorkspaceError:
            continue  # un-migrated or broken files cannot be matched by ID
    if not matches:
        candidate = Path(ref)
        if not candidate.is_absolute():
            candidate = root / candidate
        if candidate.is_dir() and (candidate / "job.md").is_file():
            matches.append(candidate / "job.md")
        elif candidate.is_file() and candidate.name == "job.md":
            matches.append(candidate)
    unique = list(dict.fromkeys(matches))
    if not unique:
        raise WorkspaceError(f"no job matches {ref!r}")
    if len(unique) > 1:
        raise WorkspaceError(f"{ref!r} matches more than one job; use the job ID")
    return read_job(unique[0])
