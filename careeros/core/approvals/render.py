"""Terminal-safe display of untrusted text (spec section 6). Pure.

Control characters, ANSI escape sequences, line separators and Unicode bidi controls become visible `\\xNN` / `\\uNNNN`
escapes and a literal backslash becomes `\\\\`, so the rendering is injective: a typed escape cannot be confused with a
rendered one. Newlines are kept so a message shows as written.
"""

from __future__ import annotations

import re

from careeros.core.approvals.model import Item

_BIDI = frozenset([*range(0x202A, 0x202F), *range(0x2066, 0x206A), 0x200E, 0x200F, 0x061C, 0x2028, 0x2029])


def _escape(char: str) -> str:
    code = ord(char)
    if char == "\\":
        return "\\\\"
    if char == "\n":
        return char
    if code < 0x20 or 0x7F <= code <= 0x9F:
        return f"\\x{code:02x}"
    if code in _BIDI or 0xD800 <= code <= 0xDFFF:
        return f"\\u{code:04x}"
    return char


def render_content(text: str) -> str:
    return "".join(_escape(c) for c in text)


def hash_prefix(action_hash: str) -> str:
    """The first 12 hex characters of an action hash (a `sha256:` prefix is accepted)."""
    value = action_hash.removeprefix("sha256:")
    if not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ValueError("not a SHA-256 hex digest")
    return value[:12]


def summary(item: Item, action_hash: str) -> str:
    """What the user sees before deciding: kind, target, content length, the hash prefix and the exact content."""
    lines = [
        f"item:    {item.id}",
        f"kind:    {render_content(item.kind)}",
        f"target:  {render_content(item.target.label)} ({render_content(item.target.id)}, {render_content(item.target.channel)})",
        f"length:  {len(item.content)} characters",
        f"hash:    {hash_prefix(action_hash)}",
    ]
    if item.manifest:
        lines.append("manifest:")
        lines += [f"  {render_content(m.path)}  {m.sha256[:12]}" for m in item.manifest]
    lines += ["content:", "--------", render_content(item.content), "--------"]
    return "\n".join(lines)
