"""Data shapes for approvals. Frozen dataclasses and enums only."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class State(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    IN_FLIGHT = "in_flight"
    CONSUMED = "consumed"
    DECLINED = "declined"
    EXPIRED = "expired"
    SUPERSEDED = "superseded"
    VOIDED = "voided"


@dataclass(frozen=True)
class Target:
    id: str
    channel: str
    label: str
    url: str | None = None      # canonical LinkedIn profile URL for invites and messages; None for submissions


@dataclass(frozen=True)
class ManifestEntry:
    path: str
    sha256: str


@dataclass(frozen=True)
class Item:
    id: str
    kind: str
    target: Target
    created_at: str
    content: str
    manifest: tuple[ManifestEntry, ...] = ()


@dataclass(frozen=True)
class SequenceError:
    """An event that does not fit the item's history (or an unknown `approval.*` type): the source of `APR005`."""
    seq: object
    entity: str | None
    message: str


@dataclass(frozen=True)
class ItemState:
    """The folded state of one item. `state` is None when no `approval.proposed` event exists for it."""
    state: State | None
    approved_at: str | None = None      # ts of the latest approval.approved event; kept after release, claim and consumption
    started: bool = False               # the item has been claimed at least once
    started_at: str | None = None
    proposed_at: str | None = None
    last_ts: str | None = None          # ts of the latest event applied to the item
    kind: str | None = None
    target_id: str | None = None
    action_hash: str | None = None      # from the proposal event's artifacts, without the `sha256:` prefix
    rel: str | None = None
    void_reason: str | None = None
    errors: tuple[str, ...] = ()
