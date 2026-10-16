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
    target: {id: per_abcdefghjk, channel: linkedin, label: "Alex Example, Example Corp"}
    created_at: 2026-10-15T09:00:00Z
    ---
    Hi Alex, I would like to connect.
    """)


def mk(**over) -> Item:
    base = dict(id=APR, kind="linkedin_invite", target=Target(PER, "linkedin", "Alex Example, Example Corp"),
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
    assert parsed is None and [i.code for i in issues] == ["APR001"]


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
    assert parsed is None and [i.code for i in issues] == ["APR001"]


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


@pytest.mark.parametrize("label", ['Alex "Q": #x', "multi\nline", "e\x1b[31m", "  padded  ", "null", "123", "2026-10-15", "- dash", "é\u202e"])
def test_round_trip_survives_awkward_labels(label):
    it = mk(target=Target(PER, "linkedin", label))
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
    assert reason(ws, "a\x00b") == "a\x00b: path contains a control character"


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


def test_render_is_injective_on_random_strings():
    rng = random.Random(3)
    alphabet = ["\\", "x", "u", "1", "b", "\x1b", "\n", "\t", "\u202e", "\x00", "é", "0", "2", "f", "[", "\\x1b", "\\\\", "\\u202e"]
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
        "submission_not_consumed", "not_interactive"}
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
    return ev(seq, "approval.proposed", item_id, at, action=f"propose {kind} for {target}",
              artifacts=[f"approvals/{item_id}.md", "sha256:" + "c" * 64])


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
