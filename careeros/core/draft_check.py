"""The `draft.checked` event wording and draft hashing, shared by `careeros check` (the producer) and the interview prep
reader (spec 6.4). A test pins the two together: changing the wording here changes both."""

from __future__ import annotations

import hashlib

EVENT_TYPE = "draft.checked"
PASSED = "checked draft: passed"        # the action of a passing check starts with this
FAILED = "checked draft: failed"


def decode_draft(data: bytes) -> str:
    """The text `careeros check` reads from a draft file (`Path.read_text(encoding="utf-8")`): strict UTF-8 (a BOM is
    kept as text) with universal newlines (`\\r\\n` and `\\r` become `\\n`). Raises UnicodeDecodeError."""
    return data.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")


def draft_sha256(text: str) -> str:
    """The hex digest `careeros check --record` stores as `sha256:<hex>`."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
