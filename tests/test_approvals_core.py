"""Approvals core (Task A): item format and hash, manifest rules, rendering, state fold and transition table.

Pure; invented data only. The reference models here (a table-driven simulator for the fold, a decoder for the rendering,
an expected-state grid for the submission rule) are written independently of the production code.
"""

from __future__ import annotations

import ast
import dataclasses
import hashlib
import itertools
import json
import random
import re
import subprocess
import sys
import textwrap
import unicodedata
from urllib.parse import quote
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from careeros.core import ids, models
from careeros.core.approvals import fold, item, manifest, render, rules
from careeros.core.approvals.model import Item, ItemState, ManifestEntry, State, Target
from careeros.core.compensation import record

PER = "per_abcdefghjk"
JOB = "job_abcdefghjk"
APR = "apr_abcdefghjk"
SHA_A = "a" * 64
SHA_B = "b" * 64
URL = "https://www.linkedin.com/in/alex-example"

SPEC_EXAMPLE = textwrap.dedent("""\
    ---
    schema: 1
    id: apr_abcdefghjk
    kind: application_submit
    target: {id: job_abcdefghjk, channel: linkedin, label: "Alex Example, Example Corp"}
    created_at: 2026-10-15T09:00:00Z
    manifest:
      - {path: jobs/discovered/example/resume.pdf, sha256: "%s"}
      - {path: jobs/discovered/example/cover-letter.pdf, sha256: "%s"}
    ---
    The exact message text, or, for application_submit, the exact answers text.
    """ % (SHA_A, SHA_B))

INVITE = textwrap.dedent("""\
    ---
    schema: 1
    id: apr_abcdefghjk
    kind: linkedin_invite
    target: {id: per_abcdefghjk, channel: linkedin, label: "Alex Example, Example Corp", url: "https://www.linkedin.com/in/alex-example"}
    created_at: 2026-10-15T09:00:00Z
    ---
    Hi Alex, I would like to connect.
    """)


def mk(**over) -> Item:
    base = dict(id=APR, kind="linkedin_invite", target=Target(PER, "linkedin", "Alex Example, Example Corp", URL),
                created_at="2026-10-15T09:00:00Z", content="Hi Alex.\n", manifest=())
    base.update(over)
    return Item(**base)


def mk_submit(**over) -> Item:
    base = dict(kind="application_submit", target=Target(JOB, "linkedin", "Example Corp"),
                content="Answers.\n", manifest=(ManifestEntry("a/resume.pdf", SHA_A), ManifestEntry("b.pdf", SHA_B)))
    base.update(over)
    return mk(**base)


# --- the strict frontmatter wrapper (compensation.record) --------------------------------------------------------

def test_strict_frontmatter_returns_mapping_and_exact_body():
    fm, body = record.strict_frontmatter("---\na: 1\n---\n\nbody\n\n")
    assert fm == {"a": 1} and body == "\nbody\n\n"


@pytest.mark.parametrize("text", [
    "no frontmatter", "---\na: 1\n", "---\na: 1\na: 2\n---\n", "---\na: &x 1\nb: *x\n---\n",
    "---\n<<: {a: 1}\n---\n", "---\n- a\n---\n", "---\na: [\n---\n"])
def test_strict_frontmatter_refuses_with_record_error(text):
    with pytest.raises(record.RecordError):
        record.strict_frontmatter(text)


def test_strict_frontmatter_reads_crlf_as_lf():
    assert record.strict_frontmatter("---\r\na: 1\r\n---\r\nx\r\ny\r\n") == ({"a": 1}, "x\ny\n")


# --- item parsing ------------------------------------------------------------------------------------------------

def test_spec_example_parses():
    parsed, issues = item.parse(SPEC_EXAMPLE, f"approvals/{APR}.md")
    assert issues == []
    assert parsed == Item(
        APR, "application_submit", Target(JOB, "linkedin", "Alex Example, Example Corp"), "2026-10-15T09:00:00Z",
        "The exact message text, or, for application_submit, the exact answers text.\n",
        (ManifestEntry("jobs/discovered/example/resume.pdf", SHA_A),
         ManifestEntry("jobs/discovered/example/cover-letter.pdf", SHA_B)))


def test_every_kind_shape_parses():
    inv, issues = item.parse(INVITE, "approvals/apr_abcdefghjk.md")
    assert issues == [] and inv.kind == "linkedin_invite" and inv.manifest == ()
    msg, issues = item.parse(INVITE.replace("linkedin_invite", "linkedin_message"), "approvals/apr_abcdefghjk.md")
    assert issues == [] and msg.kind == "linkedin_message"
    assert set(rules.KINDS) == {"linkedin_invite", "linkedin_message", "application_submit"}


def _swap(text: str, old: str, new: str) -> str:
    assert old in text
    return text.replace(old, new, 1)


BAD = {
    "unknown top key": lambda: _swap(INVITE, "kind:", "extra: 1\nkind:"),
    "duplicate key": lambda: _swap(INVITE, "kind:", "kind: linkedin_message\nkind:"),
    "alias": lambda: _swap(INVITE, "schema: 1", "schema: &s 1\nextra: *s"),
    "merge key": lambda: _swap(INVITE, "schema: 1", "<<: {schema: 1}"),
    "bad kind": lambda: _swap(INVITE, "linkedin_invite", "email"),
    "invite to a job": lambda: _swap(INVITE, "per_abcdefghjk", "job_abcdefghjk"),
    "submit to a person": lambda: _swap(SPEC_EXAMPLE, "id: job_abcdefghjk", "id: per_abcdefghjk"),
    "malformed target id": lambda: _swap(INVITE, "per_abcdefghjk", "per_ABC"),
    "submit without manifest": lambda: SPEC_EXAMPLE.split("manifest:")[0] + "---\nx\n",
    "manifest on a message": lambda: _swap(INVITE, "---\nHi", f"manifest:\n  - {{path: a.pdf, sha256: \"{SHA_A}\"}}\n---\nHi"),
    "unknown key in target": lambda: _swap(INVITE, "channel: linkedin", "channel: linkedin, url: x"),
    "target not a mapping": lambda: re.sub(r"target: \{.*\}", "target: per_abcdefghjk", INVITE),
    "missing channel": lambda: _swap(INVITE, "channel: linkedin, ", ""),
    "empty channel": lambda: _swap(INVITE, "channel: linkedin", 'channel: ""'),
    "numeric label": lambda: _swap(INVITE, '"Alex Example, Example Corp"', "12"),
    "unknown key in manifest entry": lambda: _swap(SPEC_EXAMPLE, "sha256:", "size: 1, sha256:"),
    "short sha": lambda: _swap(SPEC_EXAMPLE, SHA_A, "abc"),
    "upper-case sha": lambda: _swap(SPEC_EXAMPLE, SHA_A, "A" * 64),
    "numeric sha": lambda: _swap(SPEC_EXAMPLE, f'"{SHA_A}"', "1" * 64),
    "absolute manifest path": lambda: _swap(SPEC_EXAMPLE, "jobs/discovered/example/resume.pdf", "/etc/passwd"),
    "dotdot manifest path": lambda: _swap(SPEC_EXAMPLE, "jobs/discovered/example/resume.pdf", "../x.pdf"),
    "unnormalised manifest path": lambda: _swap(SPEC_EXAMPLE, "jobs/discovered/example/resume.pdf", "jobs/./discovered/example/resume.pdf"),
    "duplicate manifest path": lambda: _swap(SPEC_EXAMPLE, "cover-letter.pdf", "resume.pdf"),
    "manifest not a list": lambda: re.sub(r"manifest:\n(  - .*\n)+", "manifest: x\n", SPEC_EXAMPLE),
    "schema 2": lambda: _swap(INVITE, "schema: 1", "schema: 2"),
    "schema missing": lambda: _swap(INVITE, "schema: 1\n", ""),
    "bad id": lambda: _swap(INVITE, "apr_abcdefghjk", "apr_x"),
    "wrong id prefix": lambda: _swap(INVITE, "apr_abcdefghjk", "job_abcdefghjk"),
    "bad created_at": lambda: _swap(INVITE, "2026-10-15T09:00:00Z", "yesterday"),
    "date-only created_at": lambda: _swap(INVITE, "2026-10-15T09:00:00Z", "2026-10-15"),
    "no frontmatter": lambda: "just text\n",
    "unclosed frontmatter": lambda: INVITE.replace("---\nHi", "Hi"),
    "invalid yaml": lambda: _swap(INVITE, "schema: 1", "schema: ["),
    "list root": lambda: "---\n- a\n---\nx\n",
}


@pytest.mark.parametrize("name", sorted(BAD))
def test_bad_items_are_apr001_and_never_raise(name):
    parsed, issues = item.parse(BAD[name](), f"approvals/{APR}.md")
    assert parsed is None
    assert issues and {i.code for i in issues} == {"APR001"}
    assert all(i.severity == "error" and i.path == f"approvals/{APR}.md" and i.message and i.fix for i in issues)


def test_issue_is_the_shared_issue_type():
    _, issues = item.parse("x", "approvals/a.md")
    assert isinstance(issues[0], models.Issue)


def test_file_name_must_match_the_item_id():
    parsed, issues = item.parse(INVITE, "approvals/apr_zzzzzzzzzz.md")
    assert parsed is None and {i.code for i in issues} == {"APR001"}


def test_parse_bytes_refuses_undecodable_input():
    parsed, issues = item.parse_bytes(b"---\n\xff\xfe\n---\n", f"approvals/{APR}.md")
    assert parsed is None and [i.code for i in issues] == ["APR001"]
    parsed, issues = item.parse_bytes(INVITE.encode(), f"approvals/{APR}.md")
    assert issues == [] and parsed.id == APR


def test_hostile_input_never_raises():
    rng = random.Random(7)
    corpus = [bytes(rng.randrange(256) for _ in range(rng.randrange(0, 200))) for _ in range(300)]
    corpus += [SPEC_EXAMPLE.encode()[:n] for n in range(len(SPEC_EXAMPLE))]
    corpus += [INVITE.encode()[:n] for n in range(len(INVITE))]
    corpus += [b"---\n" + b"[" * 5000 + b"\n---\n", b"---\n" + b"{a: " * 3000 + b"\n---\n", b"---\nid: \"\\ud800\"\n---\n", b""]
    for raw in corpus:
        parsed, issues = item.parse_bytes(raw, f"approvals/{APR}.md")
        assert (parsed is None) == bool(issues)
        parsed, issues = item.parse(raw.decode("utf-8", "replace"), "x.md")
        assert (parsed is None) == bool(issues)


def test_lone_surrogate_in_a_field_is_apr001():
    text = _swap(INVITE, '"Alex Example, Example Corp"', '"bad \\ud800 label"')
    parsed, issues = item.parse(text, f"approvals/{APR}.md")
    assert parsed is None and {i.code for i in issues} == {"APR001"}


# --- hash and round trip -----------------------------------------------------------------------------------------

def test_action_hash_matches_an_independent_computation():
    it = mk_submit()
    doc = {"schema": 1, "kind": "application_submit", "target": {"id": JOB, "channel": "linkedin", "label": "Example Corp"},
           "content": "Answers.\n", "manifest": [["a/resume.pdf", SHA_A], ["b.pdf", SHA_B]]}
    raw = json.dumps(doc, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    assert item.action_hash(it) == hashlib.sha256(raw).hexdigest()
    assert re.fullmatch("[0-9a-f]{64}", item.action_hash(it))


def test_action_hash_covers_every_field():
    base = mk_submit()
    h = item.action_hash(base)
    variants = [
        dataclasses.replace(base, kind="linkedin_message", manifest=()),
        dataclasses.replace(base, target=Target("job_zzzzzzzzzz", "linkedin", "Example Corp")),
        dataclasses.replace(base, target=Target(JOB, "other", "Example Corp")),
        dataclasses.replace(base, target=Target(JOB, "linkedin", "Example Corp ")),
        dataclasses.replace(base, content="Answers!\n"),
        dataclasses.replace(base, manifest=(ManifestEntry("a/resume.pdf", SHA_B), ManifestEntry("b.pdf", SHA_B))),
        dataclasses.replace(base, manifest=(ManifestEntry("a/other.pdf", SHA_A), ManifestEntry("b.pdf", SHA_B))),
        dataclasses.replace(base, manifest=base.manifest[:1]),
    ]
    hashes = {item.action_hash(v) for v in variants}
    assert h not in hashes and len(hashes) == len(variants)


def test_id_and_created_at_are_not_part_of_the_hash():
    base = mk()
    assert item.action_hash(base) == item.action_hash(dataclasses.replace(base, id="apr_zzzzzzzzzz", created_at="2027-01-01T00:00:00Z"))


@pytest.mark.parametrize("change", ["Hi Alex. \n", "Hi  Alex.\n", "Hi Alex.", "\nHi Alex.\n", "Hi Alex.\n\n", "Hi\tAlex.\n", " Hi Alex.\n"])
def test_whitespace_changes_the_hash(change):
    assert item.action_hash(mk(content=change)) != item.action_hash(mk())


def test_crlf_and_lf_hash_equal_and_content_is_normalised_on_parse():
    assert item.action_hash(mk(content="a\r\nb\r\n")) == item.action_hash(mk(content="a\nb\n"))
    text = INVITE.replace("\n", "\r\n")
    parsed, issues = item.parse(text, f"approvals/{APR}.md")
    assert issues == [] and parsed.content == "Hi Alex, I would like to connect.\n"


def test_manifest_order_does_not_matter():
    a = mk_submit()
    b = dataclasses.replace(a, manifest=tuple(reversed(a.manifest)))
    assert item.action_hash(a) == item.action_hash(b)


def test_hash_is_stable_across_a_round_trip():
    it = mk_submit()
    parsed, issues = item.parse_bytes(item.to_bytes(it), f"approvals/{APR}.md")
    assert issues == [] and item.action_hash(parsed) == item.action_hash(it)


CONTENTS = ["", "x", "\n", "\n\n", "\n\nhi\n\n", "  lead\n", "trail  \n\n\n", "---\nnot a fence\n---\n", "---", "tab\there\n",
            "unicode \u00e9\u4e2d \U0001F600\n", "back\\slash \\x1b\n", "esc \x1b[31m red \x1b]0;t\x07\n", "nul \x00 end", "a\u202eb\n"]


@pytest.mark.parametrize("content", CONTENTS)
@pytest.mark.parametrize("make", [mk, mk_submit])
def test_round_trip_is_exact(content, make):
    it = make(content=content)
    parsed, issues = item.parse_bytes(item.to_bytes(it), f"approvals/{APR}.md")
    assert issues == [] and parsed == it


@pytest.mark.parametrize("label", ['Alex "Q": #x', "  padded  ", "null", "123", "2026-10-15", "- dash"])
def test_round_trip_survives_awkward_labels(label):
    it = mk(target=Target(PER, "linkedin", label, URL))
    parsed, issues = item.parse_bytes(item.to_bytes(it), f"approvals/{APR}.md")
    assert issues == [] and parsed == it


def test_round_trip_random_contents():
    rng = random.Random(11)
    alphabet = ["a", "b", " ", "\n", "\r\n", "\t", "-", "---", "\\", "\u00e9", "\x1b", "\u202e", "#", ":"]
    for _ in range(200):
        content = "".join(rng.choice(alphabet) for _ in range(rng.randrange(0, 30)))
        it = mk(content=content)
        parsed, issues = item.parse_bytes(item.to_bytes(it), f"approvals/{APR}.md")
        assert issues == []
        assert parsed.content == content.replace("\r\n", "\n")
        assert item.to_bytes(parsed) == item.to_bytes(it)


def test_to_bytes_is_deterministic_and_lf_only():
    raw = item.to_bytes(mk(content="a\r\nb\n"))
    assert raw == item.to_bytes(mk(content="a\nb\n")) and b"\r" not in raw and raw.startswith(b"---\nschema: 1\n")


# --- manifest ----------------------------------------------------------------------------------------------------

@pytest.fixture
def ws(tmp_path):
    root = tmp_path / "ws"
    (root / "jobs" / "x").mkdir(parents=True)
    (root / "jobs" / "x" / "resume.pdf").write_bytes(b"resume v1")
    (root / "notes.txt").write_bytes(b"notes")
    return root


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def reason(root, path):
    with pytest.raises(manifest.ManifestError) as exc:
        manifest.resolve(root, [path])
    assert exc.value.code == rules.MANIFEST_INVALID
    return str(exc.value)


def test_resolve_hashes_regular_files_sorted_and_normalised(ws):
    got = manifest.resolve(ws, ["notes.txt", "./jobs//x/resume.pdf"])
    assert [(f.path, f.sha256) for f in got] == [("jobs/x/resume.pdf", sha(b"resume v1")), ("notes.txt", sha(b"notes"))]
    assert manifest.hash_files(ws, ["notes.txt"]) == (ManifestEntry("notes.txt", sha(b"notes")),)


def test_manifest_rejections_have_exact_per_path_messages(ws, tmp_path):
    (tmp_path / "outside.txt").write_bytes(b"secret")
    (ws / "link-out").symlink_to(tmp_path / "outside.txt")
    (ws / "dir-link").symlink_to(tmp_path)
    (ws / "dangling").symlink_to(ws / "nowhere")
    assert reason(ws, str(tmp_path / "outside.txt")) == f"{tmp_path / 'outside.txt'}: path must be relative to the workspace"
    assert reason(ws, "../outside.txt") == "../outside.txt: '..' segments are not allowed"
    assert reason(ws, "jobs/../notes.txt") == "jobs/../notes.txt: '..' segments are not allowed"
    assert reason(ws, "") == ": path is empty"
    assert reason(ws, ".") == ".: path is empty"
    assert reason(ws, "missing.txt") == "missing.txt: file does not exist"
    assert reason(ws, "dangling") == "dangling: file does not exist"
    assert reason(ws, "jobs") == "jobs: not a regular file"
    assert reason(ws, "link-out") == "link-out: resolves outside the workspace"
    assert reason(ws, "dir-link/outside.txt") == "dir-link/outside.txt: resolves outside the workspace"
    assert reason(ws, "a\x00b") == "a\\x00b: path contains a control character"


def test_manifest_rejects_fifo_and_duplicates(ws):
    import os
    os.mkfifo(ws / "pipe")
    assert reason(ws, "pipe") == "pipe: not a regular file"
    with pytest.raises(manifest.ManifestError) as exc:
        manifest.resolve(ws, ["notes.txt", "./notes.txt"])
    assert exc.value.code == rules.MANIFEST_INVALID and "listed more than once" in str(exc.value)


def test_symlink_to_a_file_inside_the_workspace_is_hashed_by_target(ws):
    (ws / "link.pdf").symlink_to(ws / "jobs" / "x" / "resume.pdf")
    (f,) = manifest.resolve(ws, ["link.pdf"])
    assert f.path == "link.pdf" and f.sha256 == sha(b"resume v1")


def test_retargeting_a_link_before_begin_is_manifest_changed(ws):
    link = ws / "link.pdf"
    link.symlink_to(ws / "jobs" / "x" / "resume.pdf")
    entries = manifest.hash_files(ws, ["link.pdf"])
    manifest.verify(ws, entries)
    link.unlink()
    link.symlink_to(ws / "notes.txt")
    with pytest.raises(manifest.ManifestError) as exc:
        manifest.verify(ws, entries)
    assert exc.value.code == rules.MANIFEST_CHANGED and str(exc.value) == "link.pdf: content changed since it was proposed"


def test_verify_detects_changed_missing_and_escaping_files(ws, tmp_path):
    entries = manifest.hash_files(ws, ["jobs/x/resume.pdf", "notes.txt"])
    manifest.verify(ws, entries)
    (ws / "jobs" / "x" / "resume.pdf").write_bytes(b"resume v2")
    with pytest.raises(manifest.ManifestError) as exc:
        manifest.verify(ws, entries)
    assert exc.value.code == rules.MANIFEST_CHANGED
    (ws / "jobs" / "x" / "resume.pdf").write_bytes(b"resume v1")
    (ws / "notes.txt").unlink()
    with pytest.raises(manifest.ManifestError) as exc:
        manifest.verify(ws, entries)
    assert exc.value.code == rules.MANIFEST_INVALID and str(exc.value) == "notes.txt: file does not exist"
    (tmp_path / "o.txt").write_bytes(b"notes")
    (ws / "notes.txt").symlink_to(tmp_path / "o.txt")
    with pytest.raises(manifest.ManifestError) as exc:
        manifest.verify(ws, entries)
    assert exc.value.code == rules.MANIFEST_INVALID


def test_manifest_writes_nothing(ws):
    before = sorted((p.relative_to(ws).as_posix(), p.read_bytes() if p.is_file() else None) for p in ws.rglob("*"))
    manifest.resolve(ws, ["notes.txt"])
    assert before == sorted((p.relative_to(ws).as_posix(), p.read_bytes() if p.is_file() else None) for p in ws.rglob("*"))


# --- rendering ---------------------------------------------------------------------------------------------------

def decode(rendered: str) -> str:
    """Independent inverse of the rendering: `\\\\`, `\\xNN` and `\\uNNNN` back to characters."""
    out, i = [], 0
    while i < len(rendered):
        c = rendered[i]
        if c != "\\":
            out.append(c)
            i += 1
        elif rendered[i + 1] == "\\":
            out.append("\\")
            i += 2
        elif rendered[i + 1] == "x":
            out.append(chr(int(rendered[i + 2:i + 4], 16)))
            i += 4
        elif rendered[i + 1] == "u":
            out.append(chr(int(rendered[i + 2:i + 6], 16)))
            i += 6
        elif rendered[i + 1] == "U":
            out.append(chr(int(rendered[i + 2:i + 10], 16)))
            i += 10
        else:
            raise AssertionError(f"undecodable escape at {i}: {rendered!r}")
    return "".join(out)


@pytest.mark.parametrize("text,expected", [
    ("plain text", "plain text"),
    ("a\x1b[31mred\x1b[0m", "a\\x1b[31mred\\x1b[0m"),
    ("\x1b]0;title\x07", "\\x1b]0;title\\x07"),
    ("a\x00b", "a\\x00b"),
    ("a\tb", "a\\x09b"),
    ("a\rb", "a\\x0db"),
    ("line1\nline2\n", "line1\nline2\n"),
    ("a\x7fb\x9bc", "a\\x7fb\\x9bc"),
    ("back\\slash", "back\\\\slash"),
    ("\\x1b", "\\\\x1b"),
    ("a\u202eb", "a\\u202eb"),
    ("\u2066x\u2069", "\\u2066x\\u2069"),
    ("caf\u00e9 \u4e2d \U0001F600", "caf\u00e9 \u4e2d \U0001F600"),
])
def test_render_content_cases(text, expected):
    assert render.render_content(text) == expected


def test_bidi_overrides_and_controls_are_all_escaped():
    for cp in [*range(0x202A, 0x202F), *range(0x2066, 0x206A), *range(0, 0x20), *range(0x7F, 0xA0)]:
        out = render.render_content(chr(cp))
        if cp == 0x0A:
            assert out == "\n"
        else:
            assert out.startswith("\\") and out.isascii() and decode(out) == chr(cp), hex(cp)


INVISIBLE = [0x115F, 0x1160, 0x17B4, 0x17B5, 0xFFA0, 0xAD, 0x34F, 0x180E, 0x200B, 0x200C, 0x200D, 0x2060, 0x2061, 0x2064, 0xFEFF, 0x3164, 0xE0000, 0xE0041, 0xE007F]


def test_invisible_characters_are_escaped():
    for cp in INVISIBLE:
        out = render.render_content(chr(cp))
        assert out.isascii() and out.startswith("\\") and decode(out) == chr(cp), hex(cp)
    assert render.render_content("caf\u00e9 \u4e2d") == "caf\u00e9 \u4e2d"


def test_render_is_injective_on_random_strings():
    rng = random.Random(3)
    alphabet = ["\\", "x", "u", "1", "b", "\x1b", "\n", "\t", "\u202e", "\x00", "é", "0", "2", "f", "[", "\\x1b", "\\\\", "\\u202e", "\u200b", "\U000e0041", "\ufeff"]
    seen: dict[str, str] = {}
    for _ in range(3000):
        s = "".join(rng.choice(alphabet) for _ in range(rng.randrange(0, 8)))
        r = render.render_content(s)
        assert decode(r) == s
        assert seen.setdefault(r, s) == s
    assert "\x1b" not in "".join(seen)


def test_hash_prefix_is_the_first_twelve_characters_of_the_real_hash():
    h = item.action_hash(mk())
    assert render.hash_prefix(h) == h[:12] and len(render.hash_prefix(h)) == 12
    assert render.hash_prefix("sha256:" + h) == h[:12]
    with pytest.raises(ValueError):
        render.hash_prefix("xyz")


def test_summary_shows_length_hash_prefix_and_escaped_text():
    it = mk_submit(content="hi \x1b[31m\u202e \\ end\n", target=Target(JOB, "linkedin", "Evil\x1b[2J Corp"))
    h = item.action_hash(it)
    text = render.summary(it, h)
    assert h[:12] in text and h not in text
    assert f"{len(it.content)} characters" in text
    assert "\x1b" not in text and "\u202e" not in text
    assert "hi \\x1b[31m\\u202e \\\\ end" in text and "Evil\\x1b[2J Corp" in text
    assert "a/resume.pdf" in text and "application_submit" in text and JOB in text


# --- the transition table ----------------------------------------------------------------------------------------

OPS = ["approve", "decline", "supersede", "begin", "done", "release", "void"]
STATES = ["pending", "approved", "in_flight", "consumed", "declined", "expired", "superseded", "voided"]

# spec section 5, written out by hand: (state, operation) -> new state; everything else is refused
SPEC_LEGAL = {
    ("pending", "approve"): "approved", ("pending", "decline"): "declined", ("pending", "supersede"): "superseded",
    ("approved", "supersede"): "superseded", ("approved", "begin"): "in_flight",
    ("in_flight", "done"): "consumed", ("in_flight", "release"): "approved", ("in_flight", "void"): "voided",
}
SPEC_REFUSAL_CODE = {("in_flight", "supersede"): "in_flight_conflict", ("expired", "begin"): "expired"}


def test_constants():
    assert rules.APPROVAL_TTL_HOURS == 24 and rules.PENDING_STALE_DAYS == 7
    assert [s.value for s in State] == STATES or {s.value for s in State} == set(STATES)
    assert set(rules.REFUSAL_CODES) == {
        "not_found", "wrong_state", "expired", "clock_skew", "content_missing", "hash_mismatch", "manifest_invalid",
        "manifest_changed", "invite_conflict", "already_submitted", "in_flight_conflict", "submission_unresolved",
        "submission_not_consumed", "not_interactive", "ledger_corrupt", "job_not_awaiting_approval", "identity_conflict",
        "person_closed", "cap_reached", "cap_config_invalid"}
    assert rules.APPLICATION_SUBMIT == "application_submit" and rules.TTL_SECONDS == 24 * 3600
    assert len(set(rules.OPERATIONS)) == len(OPS) and set(rules.OPERATIONS) == set(OPS)


@pytest.mark.parametrize("state", STATES)
@pytest.mark.parametrize("op", OPS)
def test_apply_transition_matches_the_spec_table(state, op):
    got = fold.apply_transition(State(state), op)
    if (state, op) in SPEC_LEGAL:
        assert got is State(SPEC_LEGAL[(state, op)])
    else:
        assert isinstance(got, fold.Refusal)
        assert got.code == SPEC_REFUSAL_CODE.get((state, op), "wrong_state")
        assert got.state is State(state)


@pytest.mark.parametrize("state", ["consumed", "declined", "expired", "superseded", "voided"])
def test_terminal_states_refuse_all_reuse(state):
    for op in OPS:
        assert isinstance(fold.apply_transition(State(state), op), fold.Refusal)


def test_unknown_operation_is_a_programming_error():
    with pytest.raises(ValueError):
        fold.apply_transition(State.PENDING, "explode")


def test_rules_table_agrees_with_the_spec_table():
    assert {(s, o): n for (s, o), n in rules.TRANSITIONS.items()} == SPEC_LEGAL


# --- the fold ----------------------------------------------------------------------------------------------------

T0 = datetime(2026, 10, 15, 9, 0, 0)


def ts(delta: timedelta | int = 0) -> str:
    d = timedelta(seconds=delta) if isinstance(delta, int) else delta
    return (T0 + d).strftime("%Y-%m-%dT%H:%M:%SZ")


def ev(seq, etype, entity, at, *, action=None, artifacts=None, reason=None):
    return {"id": f"evt_{seq:010d}", "seq": seq, "ts": at, "type": etype, "actor": "agent:cli", "entity": entity,
            "prev_state": None, "new_state": None, "action": action or etype, "reason": reason, "approval": "not_required",
            "artifacts": artifacts or [], "source": None, "prev": "0" * 64}


def proposed(seq, item_id, at, kind="application_submit", target=JOB):
    arts = [f"approvals/{item_id}.md", "sha256:" + "c" * 64] + ([f"url:{URL}"] if kind != "application_submit" else [])
    return ev(seq, "approval.proposed", item_id, at, action=f"propose {kind} for {target}", artifacts=arts)


EV_FOR_OP = {"approve": "approval.approved", "decline": "approval.declined", "supersede": "approval.superseded",
             "begin": "approval.started", "done": "approval.consumed", "release": "approval.released", "void": "approval.voided"}
EVENT_OF = {v: k for k, v in EV_FOR_OP.items()}


def make_events(plans, *, start_seq=1):
    """plans: [(item_id, kind, target, [(event_type, delta_seconds)...])] -> a ledger event list in the given order."""
    out, seq = [], start_seq
    for item_id, kind, target, steps in plans:
        out.append(proposed(seq, item_id, ts(0), kind, target))
        seq += 1
        for etype, delta in steps:
            out.append(ev(seq, etype, item_id, ts(delta), reason="why" if etype == "approval.voided" else None))
            seq += 1
    return out


def ref_fold(events, item_id, now):
    """Table-driven reference simulator (independent of production code): returns (state, approved_at, started)."""
    legal = {"pending": {"approval.approved": "approved", "approval.declined": "declined", "approval.superseded": "superseded"},
             "approved": {"approval.started": "in_flight", "approval.superseded": "superseded"},
             "in_flight": {"approval.consumed": "consumed", "approval.released": "approved", "approval.voided": "voided"}}
    state, approved_at, started, errors = None, None, False, 0
    for e in sorted((e for e in events if e["entity"] == item_id), key=lambda e: e["seq"]):
        t = e["type"]
        if t == "approval.rejected":
            continue
        if t == "approval.proposed":
            if state is None:
                state = "pending"
            else:
                errors += 1
            continue
        nxt = legal.get(state, {}).get(t)
        if nxt is None:
            errors += 1
            continue
        if t == "approval.approved":
            approved_at = e["ts"]
        if t == "approval.started":
            started = True
        state = nxt
    if state == "approved":
        a = datetime.strptime(approved_at, "%Y-%m-%dT%H:%M:%SZ")
        if datetime.strptime(now, "%Y-%m-%dT%H:%M:%SZ") >= a + timedelta(hours=24):
            state = "expired"
    return state, approved_at, started, errors


def gen_legal(rng, item_id, *, max_len=8):
    """A random legal event path for one item, drawn from the spec graph: [(event_type, delta_seconds)]."""
    graph = {"pending": ["approval.approved", "approval.declined", "approval.superseded"],
             "approved": ["approval.started", "approval.superseded"],
             "in_flight": ["approval.consumed", "approval.released", "approval.voided"]}
    nxt = {"approval.approved": "approved", "approval.declined": "declined", "approval.superseded": "superseded",
           "approval.started": "in_flight", "approval.consumed": "consumed", "approval.released": "approved",
           "approval.voided": "voided"}
    state, steps, t = "pending", [], 0
    for _ in range(rng.randrange(0, max_len)):
        options = graph.get(state)
        if not options:
            break
        etype = rng.choice(options)
        t += rng.choice([1, 60, 3600, 20 * 3600, 30 * 3600])
        steps.append((etype, t))
        state = nxt[etype]
    return steps


def merge_plans(rng, plans):
    """Interleave several items' event paths in time order with fresh sequence numbers."""
    flat = []
    for item_id, kind, target, steps in plans:
        flat.append((0, rng.random(), proposed(0, item_id, ts(0), kind, target)))
        for etype, delta in steps:
            flat.append((delta, rng.random(), ev(0, etype, item_id, ts(delta), reason="why" if etype == "approval.voided" else None)))
    flat.sort(key=lambda x: (x[0], x[1]))
    events = [e for _, _, e in flat]
    # a proposal always precedes its item's later events (time-sorted with delta 0 first)
    for i, e in enumerate(events, 1):
        e["seq"] = i
    return events


def test_fold_unknown_item_has_no_state():
    st = fold.fold_item([], APR, ts(0))
    assert st.state is None and st.errors == ()


def test_fold_basic_path_and_fields():
    events = make_events([(APR, "linkedin_invite", PER, [("approval.approved", 60), ("approval.started", 120)])])
    st = fold.fold_item(events, APR, ts(200))
    assert st.state is State.IN_FLIGHT and st.approved_at == ts(60) and st.started and st.started_at == ts(120)
    assert (st.kind, st.target_id, st.action_hash, st.rel) == ("linkedin_invite", PER, "c" * 64, f"approvals/{APR}.md")
    assert st.proposed_at == ts(0) and st.errors == ()
    assert st == fold.fold_item(list(reversed(events)), APR, ts(200))      # sorted by seq, not list order


def test_released_keeps_the_original_approved_at():
    events = make_events([(APR, "linkedin_invite", PER, [("approval.approved", 60), ("approval.started", 120), ("approval.released", 180)])])
    st = fold.fold_item(events, APR, ts(200))
    assert st.state is State.APPROVED and st.approved_at == ts(60) and st.started
    # a released item whose approval has aged out is expired, computed from the ORIGINAL approved_at
    assert fold.fold_item(events, APR, ts(60 + 24 * 3600)).state is State.EXPIRED
    assert fold.fold_item(events, APR, ts(60 + 24 * 3600 - 1)).state is State.APPROVED
    # and it can be claimed again
    again = events + [ev(5, "approval.started", APR, ts(300))]
    assert fold.fold_item(again, APR, ts(400)).state is State.IN_FLIGHT


def test_superseded_and_voided_semantics():
    sup = make_events([(APR, "linkedin_invite", PER, [("approval.superseded", 10)])])
    assert fold.fold_item(sup, APR, ts(20)).state is State.SUPERSEDED
    void = make_events([(APR, "linkedin_invite", PER, [("approval.approved", 10), ("approval.started", 20), ("approval.voided", 30)])])
    st = fold.fold_item(void, APR, ts(40))
    assert st.state is State.VOIDED and st.void_reason == "why"
    # superseding an in-flight item is an illegal sequence, never applied
    bad = make_events([(APR, "linkedin_invite", PER, [("approval.approved", 10), ("approval.started", 20), ("approval.superseded", 30)])])
    st = fold.fold_item(bad, APR, ts(40))
    assert st.state is State.IN_FLIGHT and len(st.errors) == 1


@pytest.mark.parametrize("steps", [
    [("approval.consumed", 5)],                                                    # consumed without started
    [("approval.started", 5)],                                                     # started without approval
    [("approval.approved", 5), ("approval.approved", 6)],                          # double approval
    [("approval.declined", 5), ("approval.approved", 6)],                          # declined never reopens
    [("approval.approved", 5), ("approval.started", 6), ("approval.consumed", 7), ("approval.started", 8)],   # reuse
    [("approval.released", 5)],
])
def test_illegal_sequences_are_reported_and_skipped(steps):
    st = fold.fold_item(make_events([(APR, "linkedin_invite", PER, steps)]), APR, ts(30))
    assert st.errors and st.state is not None


def test_event_before_proposal_and_double_proposal_are_errors():
    events = [ev(1, "approval.approved", APR, ts(1)), proposed(2, APR, ts(2)), proposed(3, APR, ts(3))]
    st = fold.fold_item(events, APR, ts(10))
    assert st.state is State.PENDING and len(st.errors) == 2


def test_proposal_without_a_readable_kind_and_target_is_an_error():
    e = proposed(1, APR, ts(0))
    e["action"] = "something else"
    st = fold.fold_item([e], APR, ts(1))
    assert st.errors and st.kind is None and st.target_id is None


def test_fold_agrees_with_the_reference_on_random_legal_sequences():
    rng = random.Random(21)
    for _ in range(400):
        ids_ = [f"apr_{'abcdefghjk'[:9]}{c}" for c in rng.sample("0123456789", rng.randrange(1, 4))]
        plans = [(i, rng.choice(rules.KINDS), JOB if rng.random() < 0.5 else PER, gen_legal(rng, i)) for i in ids_]
        events = merge_plans(rng, plans)
        now = ts(rng.choice([0, 100, 3600, 25 * 3600, 60 * 3600]))
        everything = fold.fold_all(events, now)
        for i in ids_:
            state, approved_at, started, errors = ref_fold(events, i, now)
            st = fold.fold_item(events, i, now)
            assert (st.state.value, st.approved_at, st.started, len(st.errors)) == (state, approved_at, started, errors)
            assert everything[i] == st


# --- the clock ---------------------------------------------------------------------------------------------------

def test_expiry_boundary():
    a = "2026-10-15T09:00:00Z"
    assert not fold.is_expired(a, "2026-10-16T08:59:59Z")
    assert fold.is_expired(a, "2026-10-16T09:00:00Z")
    assert fold.is_expired(a, "2026-10-16T09:00:01Z")
    assert not fold.is_expired(a, a)
    assert fold.is_expired("garbage", a) and fold.is_expired(None, a)      # an unreadable approval never counts as valid


def test_clock_skew():
    a = "2026-10-15T09:00:00Z"
    assert fold.clock_skewed(a, "2026-10-15T08:59:59Z")
    assert not fold.clock_skewed(a, a) and not fold.clock_skewed(a, "2026-10-17T00:00:00Z")
    assert fold.clock_skewed(a, "2026-10-15T12:00:00Z", last_ts="2026-10-15T12:00:01Z")
    assert not fold.clock_skewed(a, "2026-10-15T12:00:00Z", last_ts="2026-10-15T12:00:00Z")
    assert not fold.clock_skewed(None, "2026-10-15T12:00:00Z")


def test_fold_state_at_the_boundary():
    events = make_events([(APR, "linkedin_invite", PER, [("approval.approved", 100)])])
    assert fold.fold_item(events, APR, ts(100 + 24 * 3600 - 1)).state is State.APPROVED
    assert fold.fold_item(events, APR, ts(100 + 24 * 3600)).state is State.EXPIRED


def test_backward_clock_after_approval_never_makes_an_expired_item_valid():
    events = make_events([(APR, "linkedin_invite", PER, [("approval.approved", 0), ("approval.started", 60),
                                                         ("approval.released", 25 * 3600)])])
    back = ts(10 * 3600)                                   # the clock jumped back before the recorded release
    st = fold.fold_item(events, APR, back)
    assert st.last_ts == ts(25 * 3600)
    assert fold.clock_skewed(st.approved_at, back, st.last_ts)
    assert fold.fold_item(events, APR, ts(25 * 3600)).state is State.EXPIRED
    before_approval = fold.fold_item(events, APR, ts(-5))
    assert fold.clock_skewed(before_approval.approved_at, ts(-5), before_approval.last_ts)


def test_nothing_reads_the_clock_or_the_filesystem(monkeypatch):
    def boom():
        raise AssertionError("clock read")
    monkeypatch.setattr(models, "utc_now", boom)
    events = make_events([(APR, "linkedin_invite", PER, [("approval.approved", 1)])])
    fold.fold_item(events, APR, ts(5))
    fold.live_items(events, "linkedin_invite", PER, ts(5))
    fold.submission_status(events, JOB, ts(5))
    base = Path(item.__file__).parent
    for name in ("fold", "item", "render", "rules", "model"):
        tree = ast.parse((base / f"{name}.py").read_text())
        imported = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        imported |= {(n.module or "").split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
        assert not imported & {"os", "pathlib", "time", "shutil", "tempfile", "subprocess"}, name
        src = (base / f"{name}.py").read_text()
        assert "utc_now" not in src and "datetime.now" not in src and "open(" not in src, name


# --- submission status and the APPLIED rule ----------------------------------------------------------------------

NOW = ts(100 * 3600)
SUBMIT_STEPS = {                 # label -> event path that leaves the item in that effective state at NOW
    "pending": [],
    "approved": [("approval.approved", 99 * 3600)],
    "in_flight": [("approval.approved", 99 * 3600), ("approval.started", 99 * 3600 + 1)],
    "declined": [("approval.declined", 60)],
    "expired": [("approval.approved", 60)],
    "voided": [("approval.approved", 60), ("approval.started", 61), ("approval.voided", 62)],
    "superseded": [("approval.superseded", 60)],
    "consumed": [("approval.approved", 60), ("approval.started", 61), ("approval.consumed", 62)],
}
UNRESOLVED = {"pending", "approved", "in_flight"}
LABELS = list(SUBMIT_STEPS)


def submit_events(labels, job=JOB):
    plans = [(f"apr_aaaaaaaaa{'0123456789'[n]}", "application_submit", job, SUBMIT_STEPS[label]) for n, label in enumerate(labels)]
    return make_events(plans), [p[0] for p in plans]


def expected_refusal(labels):
    if any(label in UNRESOLVED for label in labels):
        return "submission_unresolved"
    if "consumed" not in labels:
        return "submission_not_consumed"
    return None


@pytest.mark.parametrize("size", [1, 2, 3])
def test_submission_grid_matches_the_rule(size):
    for labels in itertools.product(LABELS, repeat=size):
        events, item_ids = submit_events(labels)
        status = fold.submission_status(events, JOB, NOW)
        assert status.consumed == tuple(i for i, label in zip(item_ids, labels) if label == "consumed"), labels
        assert status.unresolved == tuple((i, State(label)) for i, label in zip(item_ids, labels) if label in UNRESOLVED), labels
        assert fold.submission_refusal(status) == expected_refusal(labels), labels


def test_submission_named_cases():
    def refusal(*labels):
        events, _ = submit_events(labels)
        return fold.submission_refusal(fold.submission_status(events, JOB, NOW))
    assert refusal("in_flight", "declined") == "submission_unresolved"           # old in-flight, newer declined
    assert refusal("consumed", "pending") == "submission_unresolved"             # old consumed, newer unresolved
    assert refusal("consumed", "approved") == "submission_unresolved"
    assert refusal("consumed", "in_flight") == "submission_unresolved"
    assert refusal("declined", "consumed") is None
    assert refusal("superseded", "consumed") is None and refusal("consumed", "superseded") is None
    assert refusal("declined", "expired", "voided", "superseded") == "submission_not_consumed"
    assert refusal("pending", "declined") == "submission_unresolved"             # unresolved takes precedence
    assert refusal("expired") == "submission_not_consumed"


def test_a_job_without_submit_items_is_unaffected():
    events = make_events([(APR, "linkedin_invite", PER, [("approval.approved", 1), ("approval.started", 2)])])
    status = fold.submission_status(events, JOB, NOW)
    assert status == fold.SubmissionStatus(consumed=(), unresolved=(), total=0)
    assert fold.submission_refusal(status) is None
    assert fold.submission_status([], JOB, NOW).total == 0


def test_submission_status_is_per_job_and_per_kind():
    events, _ = submit_events(["consumed"], job=JOB)
    other = make_events([("apr_bbbbbbbbb0", "application_submit", "job_zzzzzzzzzz", SUBMIT_STEPS["pending"]),
                         ("apr_bbbbbbbbb1", "linkedin_message", PER, SUBMIT_STEPS["pending"])], start_seq=100)
    status = fold.submission_status(events + other, JOB, NOW)
    assert status.unresolved == () and len(status.consumed) == 1 and status.total == 1
    assert fold.submission_refusal(status) is None


def test_expired_approval_is_resolved_unexpired_is_not():
    events = make_events([(APR, "application_submit", JOB, [("approval.approved", 0)])])
    assert fold.submission_status(events, JOB, ts(24 * 3600 - 1)).unresolved == ((APR, State.APPROVED),)
    assert fold.submission_status(events, JOB, ts(24 * 3600)).unresolved == ()


def test_live_items():
    events = make_events([
        ("apr_aaaaaaaaa0", "linkedin_invite", PER, SUBMIT_STEPS["pending"]),
        ("apr_aaaaaaaaa1", "linkedin_invite", PER, SUBMIT_STEPS["approved"]),
        ("apr_aaaaaaaaa2", "linkedin_invite", PER, SUBMIT_STEPS["in_flight"]),
        ("apr_aaaaaaaaa3", "linkedin_invite", PER, SUBMIT_STEPS["consumed"]),
        ("apr_aaaaaaaaa4", "linkedin_invite", PER, SUBMIT_STEPS["declined"]),
        ("apr_aaaaaaaaa5", "linkedin_invite", PER, SUBMIT_STEPS["expired"]),
        ("apr_aaaaaaaaa6", "linkedin_invite", PER, SUBMIT_STEPS["superseded"]),
        ("apr_aaaaaaaaa7", "linkedin_message", PER, SUBMIT_STEPS["pending"]),
        ("apr_aaaaaaaaa8", "linkedin_invite", "per_zzzzzzzzzz", SUBMIT_STEPS["pending"]),
    ])
    got = fold.live_items(events, "linkedin_invite", PER, NOW)
    assert [(i, st.state) for i, st in got] == [("apr_aaaaaaaaa0", State.PENDING), ("apr_aaaaaaaaa1", State.APPROVED),
                                                ("apr_aaaaaaaaa2", State.IN_FLIGHT)]
    assert fold.live_items(events, "linkedin_message", PER, NOW)[0][0] == "apr_aaaaaaaaa7"
    assert fold.live_items([], "linkedin_invite", PER, NOW) == ()


# --- audit-only events -------------------------------------------------------------------------------------------

def test_filter_events_drops_audit_only_and_reports_unknown_types():
    events = [proposed(1, APR, ts(0)), ev(2, "approval.rejected", APR, ts(1), reason="wrong_state"),
              ev(3, "job.transition_rejected", JOB, ts(2), reason="x"), ev(4, "approval.bogus", APR, ts(3)),
              ev(5, "note.added", None, ts(4)), ev(6, "approval.approved", APR, ts(5))]
    kept, errors = fold.filter_events(list(reversed(events)))
    assert [e["seq"] for e in kept] == [1, 6]
    assert len(errors) == 1 and errors[0].seq == 4 and errors[0].entity == APR and "approval.bogus" in errors[0].message
    st = fold.fold_item(events, APR, ts(10))
    assert st.state is State.APPROVED and len(st.errors) == 1       # the unknown type is an illegal sequence for its item


def test_unknown_approval_type_on_a_foreign_entity_is_reported_globally():
    _, errors = fold.filter_events([ev(1, "approval.bogus", "job_abcdefghjk", ts(0))])
    assert len(errors) == 1


def test_rejections_change_nothing():
    rng = random.Random(5)
    for _ in range(250):
        item_ids = [f"apr_aaaaaaaaa{c}" for c in rng.sample("0123456789", rng.randrange(1, 4))]
        plans = [(i, rng.choice(["linkedin_invite", "application_submit"]), JOB, gen_legal(rng, i)) for i in item_ids]
        for p in plans:
            if p[1] == "linkedin_invite":
                plans[plans.index(p)] = (p[0], p[1], PER, p[3])
        events = merge_plans(rng, plans)
        noisy = list(events)
        for _ in range(rng.randrange(1, 8)):
            noisy.insert(rng.randrange(0, len(noisy) + 1),
                         ev(0, rng.choice(["approval.rejected", "job.transition_rejected"]), rng.choice(item_ids + [JOB]),
                            ts(rng.choice([0, 5, 3600, 90000])), reason=rng.choice(["wrong_state", "expired"])))
        for i, e in enumerate(noisy, 1):
            e["seq"] = i
        now = ts(rng.choice([10, 3600, 25 * 3600, 70 * 3600]))
        assert fold.fold_all(noisy, now) == fold.fold_all(events, now)
        assert fold.filter_events(noisy)[1] == fold.filter_events(events)[1]
        for kind, target in (("linkedin_invite", PER), ("application_submit", JOB)):
            assert [(i, s.state) for i, s in fold.live_items(noisy, kind, target, now)] == \
                   [(i, s.state) for i, s in fold.live_items(events, kind, target, now)]
        assert fold.submission_status(noisy, JOB, now) == fold.submission_status(events, JOB, now)
        for i in item_ids:
            assert fold.fold_item(noisy, i, now).approved_at == fold.fold_item(events, i, now).approved_at


# --- import order ------------------------------------------------------------------------------------------------

def test_importing_the_package_does_not_pull_in_heavier_modules():
    code = ("import sys, importlib;"
            "[importlib.import_module('careeros.core.approvals.' + m) for m in "
            "('rules','model','item','manifest','render','fold')];"
            "import careeros.core.approvals;"
            "bad=[m for m in sys.modules if m in ('careeros.core.validation','careeros.core.state_machine',"
            "'careeros.core.evaluation.record','careeros.core.evaluation.integrity','careeros.core.evaluation.service',"
            "'careeros.core.compensation.service','careeros.cli','careeros.cli.main','careeros.core.ledger') "
            "or m.startswith('careeros.cli.')];"
            "print(bad); sys.exit(1 if bad else 0)")
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_the_approval_id_prefix():
    assert ids.PREFIXES["approval"] == "apr" and ids.is_valid_id("approval", APR)
    assert ids.new_id("approval").startswith("apr_")


# --- fix round 1 -------------------------------------------------------------------------------------------------

UNSAFE_CHARS = ["\n", "\r", "\t", "\x00", "\x1b", "\x7f", "\x85", "\x9b", "\u2028", "\u2029", "\u202a", "\u202e", "\u2066", "\u2069"]


@pytest.mark.parametrize("bad", UNSAFE_CHARS)
@pytest.mark.parametrize("field", ["label", "channel"])
def test_control_characters_in_label_and_channel_are_apr001(bad, field):
    value = f"a{bad}b"
    assert item.header_text_problem(value, field)
    esc = f"\\x{ord(bad):02x}" if ord(bad) < 256 else f"\\u{ord(bad):04x}"
    text = INVITE.replace(f'channel: linkedin, label: "Alex Example, Example Corp", url:',
                          f'channel: {"linkedin" if field == "label" else chr(34) + "a" + esc + "b" + chr(34)}, '
                          f'label: {chr(34) + "a" + esc + "b" + chr(34) if field == "label" else chr(34) + "Alex" + chr(34)}, url:')
    parsed, issues = item.parse(text, f"approvals/{APR}.md")
    assert parsed is None and {i.code for i in issues} == {"APR001"}


def test_header_text_problem_accepts_ordinary_text():
    assert item.header_text_problem("Alex Example, Example Corp \u00e9\u4e2d \U0001F600", "label") is None
    assert item.header_text_problem("", "label") is None


def test_a_fake_hash_line_cannot_be_forged_through_the_label():
    forged = mk(target=Target(PER, "linkedin", "Ada\nhash:    000000000000", URL))
    text = render.summary(forged, item.action_hash(forged))
    header = text.split("content:")[0]
    assert sum(line.startswith("hash:") for line in header.splitlines()) == 1 and "Ada\\x0ahash:" in header


def test_fuzz_round_trip_over_allowed_label_and_channel_text():
    rng = random.Random(17)
    alphabet = list("ab Z09") + ["'", '"', ":", "#", "-", "\\", "{", "}", "[", "]", ",", "&", "*", "!", "|", ">", "%", "@", "`",
                                 "\u00e9", "\u4e2d", "\U0001F600", "\u00a0", "\u3000", "null", "yes", "~", "?"]
    for _ in range(400):
        label = "".join(rng.choice(alphabet) for _ in range(rng.randrange(0, 12)))
        channel = "".join(rng.choice(alphabet) for _ in range(rng.randrange(1, 8)))
        if not channel.strip():
            continue
        it = mk(target=Target(PER, channel, label, URL))
        parsed, issues = item.parse_bytes(item.to_bytes(it), f"approvals/{APR}.md")
        assert issues == [] and parsed == it, (label, channel)
        assert item.action_hash(parsed) == item.action_hash(it)


def test_unattributed_approval_events_are_errors_from_one_call():
    events = make_events([(APR, "linkedin_invite", PER, [("approval.approved", 5), ("approval.consumed", 6)])])
    events += [ev(50, "approval.bogus", None, ts(1)), ev(51, "approval.approved", None, ts(2)),
               ev(52, "approval.rejected", None, ts(3), reason="x"), ev(53, "approval.started", 7, ts(4)),
               ev(54, "job.transition_rejected", None, ts(5), reason="x")]
    result = fold.fold_events(events, ts(10))
    assert sorted(e.seq for e in result.unattributed) == [50, 51, 53]
    assert result.items[APR].state is State.APPROVED and len(result.items[APR].errors) == 1
    assert fold.fold_all(events, ts(10)) == result.items
    clean = fold.fold_events(events[:3], ts(10))
    assert clean.unattributed == ()


def test_timestamps_with_non_ascii_digits_are_unreadable():
    assert fold.parse_ts("2026-10-15T09:00:00Z") is not None
    assert fold.parse_ts("\u0662026-10-15T09:00:00Z") is None


def test_audit_only_types_are_exactly_the_documented_ones():
    assert set(rules.AUDIT_ONLY_TYPES) == {"approval.rejected", "job.transition_rejected"}
    assert not hasattr(fold, "RefusalCode")


def test_manifest_messages_render_hostile_paths(ws):
    assert reason(ws, "a\u202eb\\c.txt") == "a\\u202eb\\\\c.txt: file does not exist"
    assert "\x1b" not in reason(ws, "\x1b[2Jx")


# --- fix round 2: persons are identified by canonical profile URL --------------------------------------------------

CANON = "https://www.linkedin.com/in/alex-example"
ACCEPTED = [
    "https://www.linkedin.com/in/alex-example", "https://www.linkedin.com/in/alex-example/", "http://www.linkedin.com/in/alex-example",
    "https://linkedin.com/in/alex-example", "HTTPS://LinkedIn.com/in/Alex-Example", "https://uk.linkedin.com/in/alex-example",
    "https://IN.linkedin.com/in/ALEX-EXAMPLE/", "https://www.linkedin.com/in/alex-example?trk=abc&x=1", "https://www.linkedin.com/in/alex-example#top",
    "https://www.linkedin.com/in/alex-example/?utm=1#x", "https://www.linkedin.com/in/Alex%2DExample", "https://www.linkedin.com/in/%41lex-example",
]
REJECTED = [
    "https://linkedin.com.evil.example/in/alex-example", "https://evil-linkedin.com/in/alex-example", "https://notlinkedin.com/in/alex",
    "https://www.linkedin.com.evil.example/in/a", "https://user@www.linkedin.com/in/alex", "https://user:pw@www.linkedin.com/in/alex",
    "https://www.linkedin.com:443/in/alex", "https://www.linkedin.com:80/in/alex", "https://127.0.0.1/in/alex", "https://[::1]/in/alex",
    "https://www.linkedin.com./in/alex", "https://abc.linkedin.com/in/alex", "https://a.b.linkedin.com/in/alex",
    "https://www.l\u0131nkedin.com/in/alex", "https://www.linked\u0456n.com/in/alex", "https://www.linkedin.com\\@evil.example/in/alex",
    "ftp://www.linkedin.com/in/alex", "www.linkedin.com/in/alex", "//www.linkedin.com/in/alex", "/in/alex", "", "   ",
    "https://www.linkedin.com/company/example", "https://www.linkedin.com/pub/alex/1/2/3", "https://www.linkedin.com/in/",
    "https://www.linkedin.com/in", "https://www.linkedin.com/in//", "https://www.linkedin.com/in/alex/details", "https://www.linkedin.com/in/alex//",
    "https://www.linkedin.com/IN/alex", "https://www.linkedin.com/in/%2e%2e", "https://www.linkedin.com/in/..", "https://www.linkedin.com/in/.",
    "https://www.linkedin.com/in/a%2Fb", "https://www.linkedin.com/in/a%2fb", "https://www.linkedin.com/in/a%252e", "https://www.linkedin.com/in/a%00b",
    "https://www.linkedin.com/in/a%0ab", "https://www.linkedin.com/in/a%20b", "https://www.linkedin.com/in/a b", "https://www.linkedin.com/in/a\nb",
    "https://www.linkedin.com/in/a\x00b", "https://www.linkedin.com/in/a%ff", "https://www.linkedin.com/in/a%e2%80%ae", "https://www.linkedin.com/in/a.b",
    "https://www.linkedin.com/in/a%5Cb", "https://www.linkedin.com/in/" + "a" * 101,
    "https://www.linkedin.com/in/alex" + "/" * 5000, "https://www.linkedin.com/in/" + "a" * 5000, None, 5, b"https://www.linkedin.com/in/alex", ["x"],
]


@pytest.mark.parametrize("raw", ACCEPTED)
def test_canonical_profile_url_accepts_variants(raw):
    assert item.canonical_profile_url(raw) == CANON


@pytest.mark.parametrize("raw", REJECTED)
def test_canonical_profile_url_rejects(raw):
    assert item.canonical_profile_url(raw) is None


def test_canonical_profile_url_non_ascii_slug_and_idempotence():
    got = item.canonical_profile_url("https://www.linkedin.com/in/Jos%C3%A9-N%C3%BA%C3%B1ez")
    assert got == "https://www.linkedin.com/in/jos\u00e9-n\u00fa\u00f1ez"
    assert item.canonical_profile_url(got) == got
    for raw in ACCEPTED + ["https://www.linkedin.com/in/\u00df", "https://www.linkedin.com/in/\u4e2d\u6587"]:
        c = item.canonical_profile_url(raw)
        assert c is None or item.canonical_profile_url(c) == c


def test_canonical_profile_url_never_raises_on_random_input():
    rng = random.Random(9)
    alphabet = list("ab/:%.@#?\\ -_01") + ["https://", "linkedin.com", "www.", "/in/", "%2f", "%zz", "\u00e9", "\x00", "\u202e"]
    for _ in range(3000):
        raw = "".join(rng.choice(alphabet) for _ in range(rng.randrange(0, 14)))
        c = item.canonical_profile_url(raw)
        assert c is None or (c.startswith("https://www.linkedin.com/in/") and item.canonical_profile_url(c) == c)


def person_item(url=URL, kind="linkedin_invite", pid=PER, **over):
    return mk(kind=kind, target=Target(pid, "linkedin", "Alex", url), **over)


def test_person_kinds_require_a_canonical_url_and_submits_forbid_one():
    parsed, issues = item.parse(INVITE, f"approvals/{APR}.md")
    assert issues == [] and parsed.target.url == URL
    for bad in ('url: "https://www.linkedin.com/in/alex-example"', 'url: "https://linkedin.com/in/Alex-Example/"',
                'url: "https://www.linkedin.com/company/x"', "url: 5", 'url: ""', "url: null"):
        text = _swap(INVITE, 'url: "https://www.linkedin.com/in/alex-example"', bad)
        parsed, issues = item.parse(text, f"approvals/{APR}.md")
        assert (parsed is None) == (bad != 'url: "https://www.linkedin.com/in/alex-example"'), bad
        assert {i.code for i in issues} <= {"APR001"}
    missing = _swap(INVITE, ', url: "https://www.linkedin.com/in/alex-example"', "")
    parsed, issues = item.parse(missing, f"approvals/{APR}.md")
    assert parsed is None and {i.code for i in issues} == {"APR001"}
    msg = INVITE.replace("linkedin_invite", "linkedin_message")
    assert item.parse(msg, f"approvals/{APR}.md")[0].target.url == URL
    assert item.parse(_swap(msg, ', url: "https://www.linkedin.com/in/alex-example"', ""), f"approvals/{APR}.md")[0] is None
    with_url = _swap(SPEC_EXAMPLE, 'label: "Alex Example, Example Corp"', f'label: "x", url: "{URL}"')
    parsed, issues = item.parse(with_url, f"approvals/{APR}.md")
    assert parsed is None and {i.code for i in issues} == {"APR001"}
    assert item.parse(SPEC_EXAMPLE, f"approvals/{APR}.md")[0].target.url is None


def test_url_is_covered_by_the_hash_and_the_round_trip():
    a = person_item()
    b = person_item(url="https://www.linkedin.com/in/someone-else")
    assert item.action_hash(a) != item.action_hash(b)
    doc = {"schema": 1, "kind": "linkedin_invite", "target": {"id": PER, "channel": "linkedin", "label": "Alex", "url": URL},
           "content": "Hi Alex.\n", "manifest": []}
    raw = json.dumps(doc, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    assert item.action_hash(a) == hashlib.sha256(raw).hexdigest()
    for it in (a, b, mk_submit()):
        parsed, issues = item.parse_bytes(item.to_bytes(it), f"approvals/{APR}.md")
        assert issues == [] and parsed == it
    assert b"url:" not in item.to_bytes(mk_submit())
    unicode_url = "https://www.linkedin.com/in/jos\u00e9"
    parsed, issues = item.parse_bytes(item.to_bytes(person_item(url=unicode_url)), f"approvals/{APR}.md")
    assert issues == [] and parsed.target.url == unicode_url


def test_summary_shows_the_url_without_new_header_spoofing():
    it = person_item()
    text = render.summary(it, item.action_hash(it))
    assert f"url:     {URL}" in text
    header = text.split("content:")[0]
    assert sum(line.startswith("hash:") for line in header.splitlines()) == 1
    forged = dataclasses.replace(it, target=Target(PER, "linkedin", "Alex", "https://www.linkedin.com/in/a\nhash:    0"))
    header = render.summary(forged, item.action_hash(forged)).split("content:")[0]
    assert sum(line.startswith("hash:") for line in header.splitlines()) == 1


def test_person_identity_helpers():
    a = person_item()
    a2 = person_item(kind="linkedin_message", pid=PER)
    b = person_item(url="https://www.linkedin.com/in/other", pid="per_zzzzzzzzzz")
    assert item.person_ids_by_url([a, a2, b, mk_submit()]) == {URL: PER, "https://www.linkedin.com/in/other": "per_zzzzzzzzzz"}
    assert item.person_identity_problems([a, a2, b, mk_submit()]) == []
    assert item.person_identity_problems([]) == []
    two_ids = person_item(pid="per_yyyyyyyyyy")
    problems = item.person_identity_problems([a, two_ids])
    assert len(problems) == 1 and URL in problems[0] and PER in problems[0] and "per_yyyyyyyyyy" in problems[0]
    two_urls = person_item(url="https://www.linkedin.com/in/other")
    problems = item.person_identity_problems([a, two_urls])
    assert len(problems) == 1 and PER in problems[0]
    assert len(item.person_identity_problems([a, two_ids, two_urls])) >= 2


# --- fix round 3: NFKC identity ----------------------------------------------------------------------------------------

P = "https://www.linkedin.com/in/"


def slug_of(raw_slug: str, *, encode=False) -> str | None:
    got = item.canonical_profile_url(P + (quote(raw_slug, safe="") if encode else raw_slug))
    return None if got is None else got[len(P):]


@pytest.mark.parametrize("a,b", [
    ("\ud55c\uae00", "\u1112\u1161\u11ab\u1100\u1173\u11af"),          # Hangul precomposed vs conjoining jamo
    ("\uff41bc", "abc"), ("\uff21\uff22\uff23", "abc"),                      # fullwidth
    ("\ufb01sh", "fish"), ("a\u00b2", "a2"), ("\u2460\u2461", "12"), ("a\u2460", "a1"),
    ("caf\u00e9", "cafe\u0301"), ("CAF\u00c9", "cafe\u0301"),                # accented NFC vs NFD, case
    ("\u0130", "i\u0307"),
])
def test_equivalent_slugs_have_one_canonical_url(a, b):
    assert slug_of(a) is not None and slug_of(a) == slug_of(b)
    assert slug_of(a, encode=True) == slug_of(a) == slug_of(b, encode=True)
    assert slug_of(slug_of(a)) == slug_of(a)
    assert unicodedata.normalize("NFKC", slug_of(a)) == slug_of(a)


def test_combining_marks_scripts_and_case_choices():
    for slug in ("\u0939\u093f\u0928\u094d\u0926\u0940", "\u0e2a\u0e27\u0e31\u0e2a\u0e14\u0e35", "e\u0301", "\u4e2d\u6587-x_y"):
        got = slug_of(slug)
        assert got is not None and slug_of(got) == got and unicodedata.normalize("NFKC", got) == got, slug
    assert slug_of("\u00df") == "\u00df"                                       # no casefold: sharp s stays
    assert slug_of("\u0391\u03a3") == slug_of("\u03b1\u03c2") == "\u03b1\u03c2"   # word-final sigma lower-cases to final sigma
    assert slug_of("\u03b1\u03c3") != slug_of("\u03b1\u03c2")                 # a medial sigma is a different slug
    for bad in ("a\u200db", "a\u00adb", "a\u00a0b", "a!b", "a$b", "a\ue000b", "a\u2028b", "a\u202eb", "a\x00b", "a\u0301\u034f", "a\u3164"):
        assert slug_of(bad) is None and slug_of(bad, encode=True) is None, repr(bad)
    assert slug_of("\u2460" * 101) is None and slug_of("1" * 100) is not None        # length counted after normalisation
    assert slug_of("\ufb03" * 40) is None                                          # 40 ligatures normalise to 120 characters


def test_uppercase_scheme_is_accepted_and_lowercased_on_purpose():
    assert item.canonical_profile_url("HTTPS://WWW.LINKEDIN.COM/in/Alex-Example") == CANON
    assert item.canonical_profile_url("HtTp://linkedin.com/in/alex-example") == CANON


def test_stored_non_nfkc_or_non_lowercase_slug_is_apr001():
    for bad in ("Cafe\u0301", "cafe\u0301", "ABC", "\ufb01sh"):
        text = _swap(INVITE, "alex-example", bad)
        parsed, issues = item.parse(text, f"approvals/{APR}.md")
        assert (parsed is None) == (unicodedata.normalize("NFKC", bad).lower() != bad), bad
    parsed, issues = item.parse(_swap(INVITE, "alex-example", "caf\u00e9"), f"approvals/{APR}.md")
    assert issues == [] and parsed.target.url == P + "caf\u00e9"


def test_nfkc_equivalent_inputs_give_equal_outputs_differential():
    rng = random.Random(23)
    pool = list("abAB09-_") + ["\uff41", "\uff22", "\ufb01", "\u00b2", "\u2460", "e\u0301", "\u00e9", "\u1112\u1161\u11ab", "\ud55c",
                              "\u0939\u093f", "\u00df", "\u03a3", "\u03c3", "\u03c2", "\u0130", "\u4e2d", "\u200d", "\u00ad", "!", " "]
    for _ in range(3000):
        x = "".join(rng.choice(pool) for _ in range(rng.randrange(1, 7)))
        base = slug_of(x)
        assert slug_of(unicodedata.normalize("NFKC", x)) == base, repr(x)
        assert slug_of(unicodedata.normalize("NFKD", x)) == base, repr(x)
        assert slug_of(x, encode=True) == base, repr(x)
        if base is not None:
            assert slug_of(base) == base and unicodedata.normalize("NFKC", base) == base


def test_person_ids_by_url_first_id_wins():
    first = person_item(pid="per_aaaaaaaaaa")
    second = person_item(pid="per_bbbbbbbbbb")
    assert item.person_ids_by_url([first, second]) == {URL: "per_aaaaaaaaaa"}
    assert item.person_ids_by_url([second, first]) == {URL: "per_bbbbbbbbbb"}


# --- fix round 4: default-ignorable code points, mark-only slugs, normalisation order ----------------------------------

# Default_Ignorable_Code_Point, Unicode 16.0 DerivedCoreProperties.txt (independent copy of the list in render.py).
DEFAULT_IGNORABLE_RANGES = [
    (0x00AD, 0x00AD), (0x034F, 0x034F), (0x061C, 0x061C), (0x115F, 0x1160), (0x17B4, 0x17B5), (0x180B, 0x180F),
    (0x200B, 0x200F), (0x202A, 0x202E), (0x2060, 0x206F), (0x3164, 0x3164), (0xFE00, 0xFE0F), (0xFEFF, 0xFEFF),
    (0xFFA0, 0xFFA0), (0xFFF0, 0xFFF8), (0x1BCA0, 0x1BCA3), (0x1D173, 0x1D17A), (0xE0000, 0xE0FFF),
]
DEFAULT_IGNORABLE = frozenset(cp for lo, hi in DEFAULT_IGNORABLE_RANGES for cp in range(lo, hi + 1))


def test_every_default_ignorable_code_point_is_escaped_and_refused_in_labels():
    for cp in sorted(DEFAULT_IGNORABLE):
        out = render.render_content(chr(cp))
        assert out.isascii() and out.startswith("\\") and decode(out) == chr(cp), hex(cp)
        assert item.header_text_problem(f"a{chr(cp)}", "label") is not None, hex(cp)


@pytest.mark.parametrize("cp", [0xFE00, 0xFE0F, 0x180B, 0x180D, 0x180F, 0xE0100, 0xE01EF, 0x1D173, 0x1BCA0, 0x2065,
                                0xFFF0])
def test_variation_selectors_and_other_default_ignorables_are_refused_in_slugs(cp):
    assert slug_of("a" + chr(cp)) is None and slug_of("a" + chr(cp), encode=True) is None
    assert slug_of("a") == "a"


def test_full_code_point_probe_no_accepted_slug_contains_a_default_ignorable():
    for cp in range(0x110000):
        if 0xD800 <= cp <= 0xDFFF:
            continue
        for raw in ("a" + chr(cp), chr(cp)):
            got = item.canonical_profile_url(P + raw)
            if got is not None:
                slug = got[len(P):]
                assert not DEFAULT_IGNORABLE.intersection(map(ord, slug)), hex(cp)
                assert any(unicodedata.category(c)[0] in "LN" for c in slug), hex(cp)


def test_mark_only_slugs_are_refused_but_real_scripts_with_marks_pass():
    for bad in ("́", "́̂", "ि", "ั", "्ि"):
        assert slug_of(bad) is None and slug_of(bad, encode=True) is None, repr(bad)
    for good in ("हिन्दी", "สวัสดี", "é", "josé",
                 "नमस्ते", "ภาษาไทย", "a-b_c"):
        got = slug_of(good)
        assert got is not None and slug_of(got) == got, repr(good)


@pytest.mark.parametrize("raw,expected", [
    ("ᴬ", "a"),                 # MODIFIER LETTER CAPITAL A: lower() first leaves it, NFKC then gives "A"
    ("Ϲ", "σ"),            # GREEK CAPITAL LUNATE SIGMA: NFKC -> capital sigma -> sigma; lower() first -> final sigma
    ("xᴬ", "xa"),
])
def test_nfkc_is_applied_before_lower(raw, expected):
    assert slug_of(raw) == expected and slug_of(raw, encode=True) == expected


# --- the proposal event carries the profile url (the ledger is authoritative for identity) ------------------------------

def _fold_prop(event):
    return fold.fold_events([event], ts(1)).items[APR]


def test_proposal_event_fields_carry_the_url_only_for_person_kinds():
    got = fold.proposal_event_fields("linkedin_invite", PER, "approvals/x.md", "c" * 64, URL)
    assert got["artifacts"] == ["approvals/x.md", "sha256:" + "c" * 64, f"url:{URL}"]
    assert len(fold.proposal_event_fields("application_submit", JOB, "approvals/x.md", "c" * 64)["artifacts"]) == 2
    st = _fold_prop(ev(1, "approval.proposed", APR, ts(0), action=f"propose linkedin_invite for {PER}", artifacts=got["artifacts"]))
    assert st.url == URL and not st.errors


def test_proposal_event_url_problems_are_errors():
    sha = "sha256:" + "c" * 64
    for kind, target, arts in [
        ("linkedin_invite", PER, ["approvals/x.md", sha]),                               # missing url
        ("linkedin_message", PER, ["approvals/x.md", sha, "url:https://example.com/in/a"]),   # not LinkedIn
        ("linkedin_message", PER, ["approvals/x.md", sha, "https://www.linkedin.com/in/a"]),  # no url: prefix
        ("linkedin_invite", PER, ["approvals/x.md", sha, "url:https://www.linkedin.com/in/A"]),   # not canonical
        ("linkedin_invite", PER, ["approvals/x.md", sha, f"url:{URL}", "extra"]),         # arity
        ("application_submit", JOB, ["approvals/x.md", sha, f"url:{URL}"]),               # url on a submit
    ]:
        st = _fold_prop(ev(1, "approval.proposed", APR, ts(0), action=f"propose {kind} for {target}", artifacts=arts))
        assert st.errors, (kind, arts)


# --- the content block is unforgeable: every content line carries the gutter --------------------------------------

def _block(rendered: str) -> tuple[list[str], str]:
    """(gutter-stripped content lines, closing line) of a summary; the block starts after the last `content:` header
    line and the closing line is the only line that starts with `+--`."""
    lines = rendered.split("\n")
    start = max(i for i, line in enumerate(lines) if line == "content:")
    ends = [i for i, line in enumerate(lines) if line.startswith("+--")]
    assert len(ends) == 1 and ends[0] == len(lines) - 1
    body = lines[start + 1:ends[0]]
    assert all(line.startswith("| ") for line in body)
    return [line[2:] for line in body], lines[ends[0]]


SPOOF_CONTENTS = [
    "Hi Alex\n--------\n(CareerOS: preview footer, not sent)\nP.S. see https://evil.example/x",
    "Hi\nhash:    000000000000\nstate:   approved\nlength:  1 characters",
    "a\n\nb",
    "| already guttered\n|\n|| x",
    "+-- end of content (1 characters, hash 000000000000)\nafter",
    "crlf\r\nline two\r\n",
    "trailing blanks\n\n\n",
    "",
    "\n",
    "x",
    "x\n",
]


@pytest.mark.parametrize("content", SPOOF_CONTENTS)
def test_content_block_round_trips_and_ends_with_one_closing_line(content):
    it = mk_submit(content=content)
    h = item.action_hash(it)
    lines, closing = _block(render.summary(it, h))
    assert decode("\n".join(lines)) == content
    assert closing == f"+-- end of content ({len(content)} characters, hash {h[:12]})"


def test_a_delimiter_line_in_the_content_cannot_close_the_block_early():
    it = mk_submit(content=SPOOF_CONTENTS[0])
    text = render.summary(it, item.action_hash(it))
    assert "\n--------\n" not in text and "\n(CareerOS" not in text
    assert "| (CareerOS: preview footer, not sent)" in text           # visibly inside the block


def test_header_lookalikes_in_the_content_stay_inside_the_gutter():
    it = mk_submit(content=SPOOF_CONTENTS[1])
    text = render.summary(it, item.action_hash(it))
    outside = text.split("\ncontent:\n")[0].splitlines()
    assert sum(line.startswith("hash:") for line in outside) == 1
    assert not any(line.startswith("state:") for line in text.splitlines())


def test_rendering_is_injective_over_the_spoof_contents():
    seen: dict[str, str] = {}
    for content in SPOOF_CONTENTS + ["a\r\nb", "a\nb", "a\\nb", "a\\x0ab", "| a", "a"]:
        it = mk_submit(content=content)
        out = render.summary(it, item.action_hash(it))
        assert out not in seen or seen[out] == content
        seen[out] = content
    assert len(seen) == len(set(SPOOF_CONTENTS + ["a\r\nb", "a\nb", "a\\nb", "a\\x0ab", "| a", "a"]))


# --- consumed_at -------------------------------------------------------------------------------------------------

def test_consumed_at_is_the_consumed_event_ts_and_none_for_every_other_state():
    for label, steps in SUBMIT_STEPS.items():
        events, (item_id,) = submit_events([label])
        st = fold.fold_item(events, item_id, NOW)
        if label == "consumed":
            assert st.consumed_at == ts(62) and st.consumed_at != st.started_at and st.consumed_at == st.last_ts
        else:
            assert st.consumed_at is None, label
    assert fold.fold_item([], APR, NOW).consumed_at is None


def test_consumed_at_ignores_later_audit_events_and_the_started_ts():
    events = make_events([(APR, "linkedin_invite", PER, [("approval.approved", 5), ("approval.started", 6), ("approval.consumed", 90)])])
    events.append(ev(99, "approval.rejected", APR, ts(500), reason="late"))
    st = fold.fold_item(events, APR, NOW)
    assert (st.started_at, st.consumed_at, st.last_ts) == (ts(6), ts(90), ts(90))
