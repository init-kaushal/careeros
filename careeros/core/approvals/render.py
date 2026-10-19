"""Terminal-safe display of untrusted text (spec section 6). Pure.

Control characters, ANSI escape sequences, line separators, Unicode bidi controls, invisible or zero-width characters
and every Default_Ignorable_Code_Point (variation selectors included) become visible `\\xNN` / `\\uNNNN` escapes and a
literal backslash becomes `\\\\`, so the rendering is injective: a typed escape cannot be confused with a rendered one.
Newlines are kept so a message shows as written.

In `summary` every content line carries the gutter `| `, and the block ends with a `+-- end of content ...` line.
Content cannot forge that line (or any header line): each of its own lines is gutter-prefixed, so a fake delimiter,
header or footer stays visibly inside the block. Distinct contents render distinctly (one gutter line per content line).
"""

from __future__ import annotations

import re

from careeros.core.approvals.model import Item

_BIDI = frozenset([*range(0x202A, 0x202F), *range(0x2066, 0x206A), 0x200E, 0x200F, 0x061C, 0x2028, 0x2029])
# invisible or zero-width characters that could hide or fake text
_INVISIBLE = frozenset([0xAD, 0x34F, 0x180E, *range(0x200B, 0x200E), *range(0x2060, 0x2065), 0xFEFF, 0x3164,
                        0x115F, 0x1160, 0x17B4, 0x17B5, 0xFFA0, *range(0xE0000, 0xE0080)])
# Default_Ignorable_Code_Point (inclusive ranges) from Unicode 16.0 DerivedCoreProperties.txt
# (https://www.unicode.org/Public/16.0.0/ucd/DerivedCoreProperties.txt): variation selectors, joiners, tag and other
# characters that render as nothing, so two strings that look identical could differ.
_DEFAULT_IGNORABLE_RANGES = (
    (0x00AD, 0x00AD), (0x034F, 0x034F), (0x061C, 0x061C), (0x115F, 0x1160), (0x17B4, 0x17B5), (0x180B, 0x180F),
    (0x200B, 0x200F), (0x202A, 0x202E), (0x2060, 0x206F), (0x3164, 0x3164), (0xFE00, 0xFE0F), (0xFEFF, 0xFEFF),
    (0xFFA0, 0xFFA0), (0xFFF0, 0xFFF8), (0x1BCA0, 0x1BCA3), (0x1D173, 0x1D17A), (0xE0000, 0xE0FFF),
)
_DEFAULT_IGNORABLE = frozenset(cp for lo, hi in _DEFAULT_IGNORABLE_RANGES for cp in range(lo, hi + 1))


def default_ignorable(char: str) -> bool:
    """True for a code point with the Unicode property Default_Ignorable_Code_Point."""
    return ord(char) in _DEFAULT_IGNORABLE


def needs_escape(char: str) -> bool:
    """True for every character except a plain printable one (a backslash and a newline are handled separately)."""
    code = ord(char)
    return (code < 0x20 or 0x7F <= code <= 0x9F or code in _BIDI or code in _INVISIBLE or code in _DEFAULT_IGNORABLE
            or 0xD800 <= code <= 0xDFFF)


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


GUTTER = "| "


def summary(item: Item, action_hash: str) -> str:
    """What the user sees before deciding: kind, target, content length, the hash prefix and the exact content."""
    lines = [
        f"item:    {item.id}",
        f"kind:    {_header(item.kind)}",
        f"target:  {_header(item.target.label)} ({_header(item.target.id)}, {_header(item.target.channel)})",
        *([f"url:     {_header(item.target.url)}"] if item.target.url is not None else []),
        f"length:  {len(item.content)} characters",
        f"hash:    {hash_prefix(action_hash)}",
    ]
    if item.manifest:
        lines.append("manifest:")
        lines += [f"  {_header(m.path)}  {m.sha256[:12]}" for m in item.manifest]
    lines.append("content:")
    lines += [GUTTER + line for line in render_content(item.content).split("\n")]
    lines.append(f"+-- end of content ({len(item.content)} characters, hash {hash_prefix(action_hash)})")
    return "\n".join(lines)
