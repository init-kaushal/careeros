"""Helpers shared by every flow in this package.

Extracted when a third flow arrived. With two flows the duplication was
drift; with three a fourth author would have invented a fifth name for the
same digest, and the digests are load-bearing — an approval binds what it
transmits by comparing one.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone

from careeros.storage.interface import StorageProvider


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def digest_text(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def digest_stored(storage: StorageProvider, path: str) -> str:
    """sha256 of stored bytes, for an artifact handed onward by path."""
    return hashlib.sha256(storage.read(path)).hexdigest()
