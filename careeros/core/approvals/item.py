"""The approval item file `approvals/<apr_id>.md`: strict parsing, the canonical bytes and the action hash (spec section 4).

Pure: parsing takes text, hashing takes an `Item`; nothing here reads a file, the clock or the workspace. Content is the
body exactly as written: line endings are normalised to LF and nothing else, so leading and trailing blank lines are
significant. `APR001` covers an unreadable file, invalid frontmatter, an unknown or duplicate key, an alias, a bad kind
or target shape, and a manifest that does not match the kind.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from urllib.parse import unquote

from careeros.core import ids
from careeros.core.approvals import manifest, render, rules
from careeros.core.approvals.model import Item, ManifestEntry, Target
from careeros.core.compensation import record
from careeros.core.evaluation.canon import canonical_json, sha256_hex
from careeros.core.models import Issue, is_utc_timestamp
from careeros.core.workspace import join_frontmatter, normalize_value

_KEYS = ("schema", "id", "kind", "target", "created_at", "manifest")
_TARGET_KEYS = ("id", "channel", "label")
_PERSON_KINDS = (rules.LINKEDIN_INVITE, rules.LINKEDIN_MESSAGE)
_ENTRY_KEYS = ("path", "sha256")
_SHA = re.compile(r"[0-9a-f]{64}")
_FIX = "an approval item is immutable: restore the file from version control, or propose a new item"


def normalize_content(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def header_text_problem(value: object, name: str) -> str | None:
    """Why `value` cannot be a target label or channel (None when it can): they appear in the header the user approves
    from, so control characters (newline and tab included), C1 controls, NEL, line separators, bidi controls and
    invisible characters are refused. Task B's propose validation reuses this."""
    if not isinstance(value, str):
        return f"target {name} must be a string"
    for char in value:
        if char != "\\" and render.needs_escape(char):
            return f"target {name} contains a control, bidi or invisible character (U+{ord(char):04X})"
    return None


_URL = re.compile(r"(?i)(https?)://([^/?#]*)(/[^?#]*)?(\?[^#]*)?(#.*)?")
_HOST = re.compile(r"(?:www\.|[a-z]{2}\.)?linkedin\.com")
_SLUG_PATH = re.compile(r"/in/([^/]+)/?")
_MAX_URL = 2048
_MAX_SLUG = 100


def canonical_profile_url(raw: object) -> str | None:
    """`https://www.linkedin.com/in/<slug>` for a LinkedIn profile URL, or None. Accepts http or https (forced to https),
    the hosts `linkedin.com`, `www.linkedin.com` and `<cc>.linkedin.com` (two letters; mapped to `www`), a path of exactly
    `/in/<slug>` with an optional trailing slash, and drops query and fragment. The slug is percent-decoded (strict
    UTF-8), normalised with NFKC and then lower-cased (`lower`, never `casefold`: the sharp s stays; a word-final capital
    sigma becomes the final sigma, so it equals the lower-case spelling but a medial sigma is a different slug); it may
    contain letters, digits and combining marks of any script plus `-` and `_` (slugs of non-English members are real;
    format characters such as joiners are not allowed), at most 100 characters counted after normalisation. Everything else is refused: other hosts and look-alikes,
    userinfo, ports, IP literals, other paths, an empty slug, anything with controls, spaces, `/`, `.` or `%` left after
    decoding, non-text input and very long input. Idempotent on its own output. The scheme is matched case-insensitively and lower-cased on purpose."""
    if not isinstance(raw, str) or len(raw) > _MAX_URL:
        return None
    match = _URL.fullmatch(raw)
    if not match or not match[2].isascii() or not _HOST.fullmatch(match[2].lower()):
        return None
    path = _SLUG_PATH.fullmatch(match[3] or "")
    if not path:
        return None
    try:
        slug = unicodedata.normalize("NFKC", unquote(path[1], errors="strict")).lower()
    except UnicodeDecodeError:
        return None
    if not slug or len(slug) > _MAX_SLUG or slug.lower() != slug or unicodedata.normalize("NFKC", slug) != slug:
        return None
    if not all(unicodedata.category(c)[0] in "LNM" or c in "-_" for c in slug) or render.render_content(slug) != slug:
        return None
    return f"https://www.linkedin.com/in/{slug}"


def person_ids_by_url(items: Iterable[Item]) -> dict[str, str]:
    """`{canonical profile URL: person id}` over the invite and message items given (first id seen wins)."""
    out: dict[str, str] = {}
    for it in items:
        if it.target.url is not None:
            out.setdefault(it.target.url, it.target.id)
    return out


def person_identity_problems(items: Iterable[Item]) -> list[str]:
    """Inconsistent person identities: one URL under two ids, or one id under two URLs."""
    by_url: dict[str, list[str]] = {}
    by_id: dict[str, list[str]] = {}
    for it in items:
        if it.target.url is None:
            continue
        if it.target.id not in by_url.setdefault(it.target.url, []):
            by_url[it.target.url].append(it.target.id)
        if it.target.url not in by_id.setdefault(it.target.id, []):
            by_id[it.target.id].append(it.target.url)
    problems = [f"profile URL {render.render_content(u)} is used under more than one person id: {', '.join(v)}"
                for u, v in sorted(by_url.items()) if len(v) > 1]
    problems += [f"person id {i} is used for more than one profile URL: {', '.join(render.render_content(u) for u in v)}"
                 for i, v in sorted(by_id.items()) if len(v) > 1]
    return problems


def _unencodable(value: object) -> bool:
    if isinstance(value, str):
        try:
            value.encode("utf-8")
        except UnicodeEncodeError:
            return True
        return False
    if isinstance(value, dict):
        return any(_unencodable(k) or _unencodable(v) for k, v in value.items())
    if isinstance(value, (list, tuple)):
        return any(_unencodable(v) for v in value)
    return False


def _target(raw: object, kind: str | None, problems: list[str]) -> Target | None:
    if not isinstance(raw, dict):
        problems.append("target must be a mapping with id, channel and label")
        return None
    person = kind in _PERSON_KINDS
    allowed = _TARGET_KEYS + (("url",) if person else ())
    extra, missing = set(raw) - set(allowed), [k for k in allowed if k not in raw]
    if extra:
        problems.append(f"unknown key in target: {', '.join(sorted(map(str, extra)))}")
    if missing:
        problems.append(f"target is missing: {', '.join(missing)}")
    if extra or missing:
        return None
    ok = True
    if kind in rules.TARGET_ID_KIND and not ids.is_valid_id(rules.TARGET_ID_KIND[kind], raw["id"]):
        problems.append(f"target id {raw['id']!r} is not a {rules.TARGET_ID_KIND[kind]} id (a {kind} item targets a "
                        f"{rules.TARGET_ID_KIND[kind]})")
        ok = False
    elif kind not in rules.TARGET_ID_KIND and not isinstance(raw["id"], str):
        ok = False
    if not isinstance(raw["channel"], str) or not raw["channel"].strip():
        problems.append("target channel must be a non-empty string")
        ok = False
    for name in ("channel", "label"):
        problem = header_text_problem(raw[name], name)
        if problem:
            problems.append(problem)
            ok = False
    url = None
    if person:
        url = raw["url"]
        if not isinstance(url, str) or canonical_profile_url(url) != url:
            problems.append(f"target url {url!r} must be a canonical LinkedIn profile URL "
                            f"(https://www.linkedin.com/in/<slug>; try {canonical_profile_url(url)!r})")
            ok = False
    return Target(raw["id"], raw["channel"], raw["label"], url) if ok else None


def _manifest(raw: object, kind: str | None, present: bool, problems: list[str]) -> tuple[ManifestEntry, ...] | None:
    if kind != rules.APPLICATION_SUBMIT:
        if present:
            problems.append(f"a {kind} item must not carry a manifest")
        return ()
    if not present:
        problems.append("an application_submit item needs a manifest (a list, possibly empty)")
        return None
    if not isinstance(raw, list):
        problems.append("manifest must be a list of {path, sha256} entries")
        return None
    entries, seen, ok = [], set(), True
    for n, entry in enumerate(raw):
        if not isinstance(entry, dict) or set(entry) != set(_ENTRY_KEYS):
            problems.append(f"manifest entry {n} must be a mapping with exactly path and sha256")
            ok = False
            continue
        path, digest = entry["path"], entry["sha256"]
        try:
            if manifest.normalize_rel(path) != path:
                problems.append(f"manifest entry {n}: path {path!r} is not in normalised form ({manifest.normalize_rel(path)!r})")
                ok = False
        except manifest.ManifestError as exc:
            problems.append(f"manifest entry {n}: {exc}")
            ok = False
            continue
        if not isinstance(digest, str) or not _SHA.fullmatch(digest):
            problems.append(f"manifest entry {n}: sha256 must be 64 lower-case hex characters (quote it in YAML)")
            ok = False
        if path in seen:
            problems.append(f"manifest entry {n}: path {path!r} is listed more than once")
            ok = False
        seen.add(path)
        if ok:
            entries.append(ManifestEntry(path, digest))
    return tuple(entries) if ok else None


def _validate(fm: dict, body: str, rel: str) -> tuple[Item | None, list[str]]:
    problems: list[str] = []
    extra = [str(k) for k in fm if k not in _KEYS]
    if extra:
        problems.append(f"unknown key(s): {', '.join(sorted(extra))}")
    if _unencodable(fm) or _unencodable(body):
        problems.append("the file contains text that cannot be encoded as UTF-8")
    if type(fm.get("schema")) is not int or fm.get("schema") != rules.SCHEMA:
        problems.append(f"schema must be {rules.SCHEMA}")
    item_id = fm.get("id")
    if not ids.is_valid_id("approval", item_id):
        problems.append(f"id {item_id!r} is not an apr_ id")
    elif rel.endswith(".md") and rel.rsplit("/", 1)[-1] != f"{item_id}.md":
        problems.append(f"id {item_id!r} does not match the file name")
    kind = fm.get("kind")
    if kind not in rules.KINDS:
        problems.append(f"kind {kind!r} must be one of {', '.join(rules.KINDS)}")
        kind = None
    created = normalize_value(fm.get("created_at"))
    if not is_utc_timestamp(created):
        problems.append("created_at must be a UTC timestamp like 2026-10-15T09:00:00Z")
    target = _target(fm.get("target"), kind, problems)
    entries = _manifest(fm.get("manifest"), kind, "manifest" in fm, problems)
    if problems or kind is None or target is None or entries is None:
        return None, problems
    return Item(item_id, kind, target, created, body, entries), problems


def parse(text: str, rel: str) -> tuple[Item | None, list[Issue]]:
    """`(item, [])` or `(None, [APR001 issues])`. Never raises."""
    try:
        fm, body = record.strict_frontmatter(text, rel)
        parsed, problems = _validate(fm, body, rel)
    except record.RecordError as exc:
        parsed, problems = None, [str(exc)]
    except Exception as exc:  # noqa: BLE001 - hostile input must never escape as an exception
        parsed, problems = None, [f"{rel} cannot be read: {type(exc).__name__}"]
    if parsed is not None:
        return parsed, []
    return None, [Issue("error", rules.APR001, rel, problem, _FIX) for problem in problems or [f"{rel} is not a valid approval item"]]


def parse_bytes(raw: bytes, rel: str) -> tuple[Item | None, list[Issue]]:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return None, [Issue("error", rules.APR001, rel, f"{rel} is not valid UTF-8", _FIX)]
    return parse(text, rel)


def _target_doc(item: Item) -> dict:
    doc = {"id": item.target.id, "channel": item.target.channel, "label": item.target.label}
    if item.target.url is not None:
        doc["url"] = item.target.url
    return doc


def to_bytes(item: Item) -> bytes:
    """The exact bytes of the item file: frontmatter, closing fence, then the content (LF line endings)."""
    fm: dict = {
        "schema": rules.SCHEMA, "id": item.id, "kind": item.kind,
        "target": _target_doc(item),
        "created_at": item.created_at,
    }
    if item.kind == rules.APPLICATION_SUBMIT:
        fm["manifest"] = [{"path": m.path, "sha256": m.sha256} for m in item.manifest]
    return join_frontmatter(fm, normalize_content(item.content)).encode("utf-8")


def action_hash(item: Item) -> str:
    """sha256 of the canonical JSON of schema, kind, target (id, channel, label and, for invites and messages, the canonical profile url), content and the sorted manifest.
    The item id and creation time are not part of it: it binds the action, the target and the exact content."""
    doc = {
        "schema": rules.SCHEMA, "kind": item.kind,
        "target": _target_doc(item),
        "content": normalize_content(item.content),
        "manifest": sorted([m.path, m.sha256] for m in item.manifest),
    }
    return sha256_hex(canonical_json(doc))
