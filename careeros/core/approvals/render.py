"""Terminal-safe display of untrusted text (spec section 6). Pure.

Control characters, ANSI escape sequences, line separators, Unicode bidi controls and invisible or zero-width characters become visible `\\xNN` / `\\uNNNN`
escapes and a literal backslash becomes `\\\\`, so the rendering is injective: a typed escape cannot be confused with a
rendered one. Newlines are kept so a message shows as written.
"""

from __future__ import annotations

import re

from careeros.core.approvals.model import Item

_BIDI = frozenset([*range(0x202A, 0x202F), *range(0x2066, 0x206A), 0x200E, 0x200F, 0x061C, 0x2028, 0x2029])
# invisible or zero-width characters that could hide or fake text
_INVISIBLE = frozenset([0xAD, 0x34F, 0x180E, *range(0x200B, 0x200E), *range(0x2060, 0x2065), 0xFEFF, 0x3164,
                        *range(0xE0000, 0xE0080)])


def needs_escape(char: str) -> bool:
    """True for every character except a plain printable one (a backslash and a newline are handled separately)."""
    code = ord(char)
    return code < 0x20 or 0x7F <= code <= 0x9F or code in _BIDI or code in _INVISIBLE or 0xD800 <= code <= 0xDFFF


def _escape(char: str) -> str:
    code = ord(char)
    if char == "\\":
        return "\\\\"
    if char == "\n":
        return char
    if code < 0x20 or 0x7F <= code <= 0x9F:
        return f"\\x{code:02x}"
    if needs_escape(char):
        return f"\\u{code:04x}" if code <= 0xFFFF else f"\\U{code:08x}"
    return char


def render_content(text: str) -> str:
    return "".join(_escape(c) for c in text)


def hash_prefix(action_hash: str) -> str:
    """The first 12 hex characters of an action hash (a `sha256:` prefix is accepted)."""
    value = action_hash.removeprefix("sha256:")
    if not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ValueError("not a SHA-256 hex digest")
    return value[:12]


def _header(text: str) -> str:
    """Header fields stay on one line: a newline in them is shown as `\\x0a`, never as a real line break."""
    return render_content(text).replace("\n", "\\x0a")


def summary(item: Item, action_hash: str) -> str:
    """What the user sees before deciding: kind, target, content length, the hash prefix and the exact content."""
    lines = [
        f"item:    {item.id}",
        f"kind:    {_header(item.kind)}",
        f"target:  {_header(item.target.label)} ({_header(item.target.id)}, {_header(item.target.channel)})",
        f"length:  {len(item.content)} characters",
        f"hash:    {hash_prefix(action_hash)}",
    ]
    if item.manifest:
        lines.append("manifest:")
        lines += [f"  {_header(m.path)}  {m.sha256[:12]}" for m in item.manifest]
    lines += ["content:", "--------", render_content(item.content), "--------"]
    return "\n".join(lines)
