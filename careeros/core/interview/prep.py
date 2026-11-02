"""The prep status of a job's interview prep file (spec 6.4). Read-only.

`none` when the job has no readable `interview-prep.md`; `checked` when the file's current text hashes, exactly as
`careeros check` hashes a draft, to the `sha256:` of a `draft.checked` event whose entity is the job and whose action
records a pass; otherwise `drafted`. The file is opened without following a symlink and without blocking (a FIFO never
hangs), must be a regular file of at most 1 MiB and valid UTF-8; anything else is `none`. Never raises, never writes."""

from __future__ import annotations

import os
import stat
from pathlib import Path

from careeros.core import draft_check
from careeros.core.interview import rules

PREP_FILE = "interview-prep.md"
MAX_PREP_BYTES = 1 << 20


def _read(path: Path) -> bytes | None:
    try:
        if stat.S_ISLNK(os.lstat(path).st_mode):
            return None
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    except OSError:
        return None
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return None
        chunks, size = [], 0
        while size <= MAX_PREP_BYTES:
            chunk = os.read(fd, MAX_PREP_BYTES + 1 - size)
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
        return None if size > MAX_PREP_BYTES else b"".join(chunks)
    except OSError:
        return None
    finally:
        os.close(fd)


def prep_status(root: Path, job_dir: Path, job_id: str, events: list) -> str:
    """`none`, `drafted` or `checked` for the job saved in `job_dir` (absolute, or relative to `root`)."""
    data = _read(Path(root) / job_dir / PREP_FILE)
    if data is None:
        return rules.PREP_NONE
    try:
        text = draft_check.decode_draft(data)
    except UnicodeDecodeError:
        return rules.PREP_NONE
    digest = f"sha256:{draft_check.draft_sha256(text)}"
    for event in events:
        if (isinstance(event, dict) and event.get("type") == draft_check.EVENT_TYPE and event.get("entity") == job_id
                and isinstance(event.get("action"), str) and event["action"].startswith(draft_check.PASSED)
                and isinstance(event.get("artifacts"), list) and digest in event["artifacts"]):
            return rules.PREP_CHECKED
    return rules.PREP_DRAFTED
