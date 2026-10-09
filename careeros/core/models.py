"""Core data shapes: states, approvals, workspace metadata, jobs, validation issues."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path


class State(str, Enum):
    DISCOVERED = "DISCOVERED"
    EVALUATED = "EVALUATED"
    SHORTLISTED = "SHORTLISTED"
    RESEARCHED = "RESEARCHED"
    PREPARING = "PREPARING"
    READY_TO_APPLY = "READY_TO_APPLY"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    APPLIED = "APPLIED"
    RECRUITER_REPLIED = "RECRUITER_REPLIED"
    SCREEN = "SCREEN"
    TECHNICAL = "TECHNICAL"
    HM = "HM"
    FINAL = "FINAL"
    OFFER = "OFFER"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    WITHDRAWN = "WITHDRAWN"


class Approval(str, Enum):
    NOT_REQUIRED = "not_required"
    REQUIRED = "required"
    APPROVED = "approved"
    DENIED = "denied"


_TS_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
_TS_SHAPE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


def utc_now() -> str:
    """The only source of timestamps. Call as `models.utc_now()` so tests can replace it."""
    return datetime.now(timezone.utc).strftime(_TS_FORMAT)


def is_utc_timestamp(value: object) -> bool:
    if not isinstance(value, str) or not _TS_SHAPE.match(value):
        return False
    try:
        datetime.strptime(value, _TS_FORMAT)
    except ValueError:
        return False
    return True


def date_to_utc(date_str: str) -> str:
    """Legacy date-only value to midnight UTC. Raises ValueError for anything but YYYY-MM-DD."""
    datetime.strptime(date_str, "%Y-%m-%d")
    return f"{date_str}T00:00:00Z"


@dataclass(frozen=True)
class WorkspaceMeta:
    schema_version: int
    framework_version: str
    created_at: str
    updated_at: str
    runtimes: tuple[str, ...] = ()


@dataclass(frozen=True)
class Issue:
    severity: str  # "error" | "warning"
    code: str
    path: str
    message: str
    fix: str

    def to_dict(self) -> dict[str, str]:
        return {
            "severity": self.severity,
            "code": self.code,
            "path": self.path,
            "message": self.message,
            "fix": self.fix,
        }


@dataclass
class Job:
    id: str
    status: State
    company: str
    title: str
    url: str | None
    created_at: str
    updated_at: str
    path: Path
    archived: bool = False
    archived_at: str | None = None
    frontmatter: dict = field(default_factory=dict)
    body: str = ""

    @property
    def slug(self) -> str:
        return self.path.parent.name
