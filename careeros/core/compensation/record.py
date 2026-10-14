"""The compensation record `jobs/discovered/<slug>/compensation.md`: strict parsing, validation, canonical form and
stamps (spec sections 4 and 8). Read-only and pure except `load`, which reads one path; nothing here writes a file, reads
the clock or touches a workspace. `skeleton` returns bytes for the caller (`careeros comp --init`) to write.

Reading (`load` / `from_text`): the frontmatter is read by a strict YAML loader that, unlike `workspace.split_frontmatter`,
refuses duplicate mapping keys, merge keys and aliases, so a hand-edited file cannot say two things. Anything that cannot
be read (bad bytes, no frontmatter, invalid or duplicate-key YAML, tabs, a non-mapping root, nesting too deep) raises
`RecordError` with code `CMP001`; no other exception escapes. `Loaded.fm` is the frontmatter exactly as YAML read it.

Codes (every problem is collected, none stops the scan):

* `CMP001` structure: unknown or missing top-level key, unsupported `schema`, a container of the wrong shape, unknown key
  inside an observation or conversion.
* `CMP002` wrong type, enum, date, empty id/currency/market, `market: null` with observations.
* `CMP003` range invariants: a missing bound key, a non-integer or boolean amount, `low < 0`, `high < low`.
* `CMP004` a `posted` quote that is missing, absent from `jd.md` (after `canon.normalize_text`) or `jd.md` itself absent.
* `CMP005` a duplicate observation id or a second conversion for the same `(from, to)` pair, even with an equal rate.
* `CMP006` a missing or blank `source`, a missing `as_of`, a missing or non-decimal-string `rate` (an unquoted YAML float
  is the usual cause; the fix text says to quote it).

`parse` returns `(record, [])` or `(None, issues)`; `validate` is `parse` plus the `CMP004` quote search (done only when
the record is otherwise valid). `CMP007` (Markets table) and `CMP100` (age) need the profile and today's date and belong
to `compensation.service`.

Currencies are stripped and upper-cased on parse (the same normalisation `evaluation.markets.norm_currency` applies), so
`usd` and `USD` are one currency. Dates may be YAML dates or `YYYY-MM-DD` strings; a datetime with a time is `CMP002`.

Stamp (`stamp`): the SHA-256 of the canonical form of the parsed record. An absent file, the `--init` skeleton and any
record with `observations: []` share one value (market and conversions are ignored when there are no observations, so
creating a skeleton never stales an evaluation). A file that does not parse hashes its whitespace-normalised raw text
under an `invalid:` prefix, so editing it still changes the stamp.
"""

from __future__ import annotations

import re
from datetime import date
from fractions import Fraction
from pathlib import Path

import yaml

from careeros.core.compensation import rules
from careeros.core.compensation.model import CompRecord, Conversion, Observation
from careeros.core.evaluation.canon import canonical_json, normalize_text, sha256_hex
from careeros.core.evaluation.model import MarketBasis
from careeros.core.models import Issue
from careeros.core.workspace import _FENCE_LINE, WorkspaceError, join_frontmatter

_TOP_KEYS = ("schema", "market", "observations", "conversions")
_REQUIRED_TOP = ("schema", "market", "observations")
_OBS_KEYS = ("id", "kind", "low", "high", "currency", "basis", "period", "source", "quote", "as_of", "confidence")
_CONV_KEYS = ("from", "to", "rate", "source", "as_of")
_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
_RATE = re.compile(r"[0-9]{1,30}(\.[0-9]{1,30})?")
_BASES = tuple(b.value for b in MarketBasis)

_FIX_FILE = "fix compensation.md (see `careeros comp --help` and docs/compensation.md), then run `careeros comp <job>`"
_FIX_RATE = 'write the rate as a quoted positive decimal string, for example rate: "83.20" (an unquoted 83.20 is a float)'
_FIX_DATE = "write the date as ISO YYYY-MM-DD, for example 2026-10-12 (no time)"
_SKELETON_BODY = "\nFree notes (ignored by code).\n"


class RecordError(WorkspaceError):
    """The file cannot be used at all: unreadable, undecodable or invalid frontmatter (CMP001)."""

    def __init__(self, message: str, code: str = "CMP001") -> None:
        super().__init__(message)
        self.code = code


class Loaded:
    """A read compensation file. `raw` is the exact bytes, `text` their decoding, `fm` the frontmatter as YAML read it,
    `body` the text after the closing fence and `rel` the workspace-relative path used in issues."""

    __slots__ = ("rel", "raw", "text", "fm", "body")

    def __init__(self, rel: str, raw: bytes, text: str, fm: dict, body: str) -> None:
        self.rel, self.raw, self.text, self.fm, self.body = rel, raw, text, fm, body


class _Strict(yaml.SafeLoader):
    """SafeLoader that rejects aliases, merge keys and duplicate mapping keys."""

    def compose_node(self, parent, index):
        if self.check_event(yaml.AliasEvent):
            raise yaml.constructor.ConstructorError(None, None, "YAML aliases are not allowed", self.peek_event().start_mark)
        return super().compose_node(parent, index)

    def construct_mapping(self, node, deep=False):
        seen = set()
        for key_node, _ in node.value:
            if key_node.tag == "tag:yaml.org,2002:merge":
                raise yaml.constructor.ConstructorError(None, None, "YAML merge keys are not allowed", key_node.start_mark)
            try:
                key = self.construct_object(key_node, deep=True)
                hash(key)
            except TypeError:
                raise yaml.constructor.ConstructorError(None, None, "an unhashable mapping key", key_node.start_mark) from None
            if key in seen:
                raise yaml.constructor.ConstructorError(None, None, f"duplicate key {key!r}", key_node.start_mark)
            seen.add(key)
        return super().construct_mapping(node, deep)


def _rel(path: Path, root: Path | None) -> str:
    if root is not None:
        try:
            return path.resolve().relative_to(Path(root).resolve()).as_posix()
        except ValueError:
            pass
    return path.as_posix()


def from_text(text: str, rel: str = rules.COMPENSATION_FILE, raw: bytes | None = None) -> Loaded:
    """Parse the frontmatter of `text`. `RecordError` (CMP001) when there is none or it is unusable; nothing else is raised."""
    raw = text.encode("utf-8", "surrogatepass") if raw is None else raw
    if not text.startswith("---\n"):
        raise RecordError(f"{rel} has no --- frontmatter block")
    match = _FENCE_LINE.search(text, 4)
    if not match:
        raise RecordError(f"{rel} has no closing --- line for its frontmatter")
    block, rest = text[4:match.start()], match.end()
    if text[rest:rest + 1] == "\n":
        rest += 1
    try:
        fm = yaml.load(block, Loader=_Strict) if block.strip() else {}   # noqa: S506 - a SafeLoader subclass
    except yaml.YAMLError as exc:
        raise RecordError(f"{rel}: frontmatter is not valid YAML: {exc}") from None
    except (RecursionError, MemoryError):
        raise RecordError(f"{rel}: the frontmatter is nested too deeply to read") from None
    except (ValueError, TypeError, OverflowError) as exc:
        raise RecordError(f"{rel}: frontmatter cannot be read: {exc}") from None
    if fm is None:
        fm = {}
    if not isinstance(fm, dict):
        raise RecordError(f"{rel}: frontmatter must be a mapping of keys to values")
    return Loaded(rel, raw, text, fm, text[rest:])


def load(path: Path, root: Path) -> Loaded:
    """Read `path`; `root` makes `rel` workspace-relative. `RecordError` (CMP001) when unreadable or unusable."""
    path = Path(path)
    rel = _rel(path, root)
    try:
        raw = path.read_bytes()
        text = raw.decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise RecordError(f"{rel} cannot be read: {exc}") from None
    return from_text(text, rel, raw)


def error_issue(err: RecordError, rel: str) -> Issue:
    """The finding for a file `load` could not read."""
    return Issue("error", err.code, rel, str(err), _FIX_FILE)


# --- parsing -----------------------------------------------------------------------------------------

class _Scan:
    def __init__(self, rel: str) -> None:
        self.rel = rel
        self.issues: list[Issue] = []

    def add(self, code: str, where: str, message: str, fix: str = _FIX_FILE) -> None:
        self.issues.append(Issue("error", code, self.rel, f"{where}: {message}" if where else message, fix))


def _text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _date(value: object) -> date | None:
    if type(value) is date:
        return value
    if isinstance(value, str) and _DATE.fullmatch(value):
        try:
            return date.fromisoformat(value)
        except ValueError:
            return None
    return None


def _amount(scan: _Scan, where: str, item: dict, key: str) -> tuple[bool, int | None]:
    if key not in item:
        scan.add("CMP003", where, f"`{key}` is missing; write `{key}: null` for an undisclosed side",
                 f"add `{key}:` with a whole number, or `null` when the posting does not state it")
        return False, None
    value = item[key]
    if value is None:
        return True, None
    if type(value) is not int:
        scan.add("CMP003", where, f"`{key}` is {value!r}, not a whole number or null",
                 f"write `{key}` as a whole integer in whole currency units (ten lakh is 1000000), or `null`")
        return False, None
    return True, value


def _enum(scan: _Scan, where: str, item: dict, key: str, allowed: tuple) -> str | None:
    value = item.get(key)
    if isinstance(value, str) and value in allowed:
        return value
    scan.add("CMP002", where, f"`{key}` is {value!r}, not one of {', '.join(allowed)}" if key in item
             else f"`{key}` is missing", f"set `{key}` to one of {', '.join(allowed)}")
    return None


def _evidence(scan: _Scan, where: str, item: dict, key: str, what: str) -> str | None:
    """A required non-blank text: missing, null or blank is CMP006; a non-string is CMP002."""
    value = item.get(key)
    if isinstance(value, str) and value.strip():
        return value
    if value is None or isinstance(value, str):
        scan.add("CMP006", where, f"`{key}` is missing or blank", f"give `{key}` {what}")
    else:
        scan.add("CMP002", where, f"`{key}` is {value!r}, not text", f"write `{key}` as a text string")
    return None


def _as_of(scan: _Scan, where: str, item: dict) -> date | None:
    if item.get("as_of") is None:
        scan.add("CMP006", where, "`as_of` is missing", "give `as_of` the date the figure or rate was seen, as YYYY-MM-DD")
        return None
    parsed = _date(item["as_of"])
    if parsed is None:
        scan.add("CMP002", where, f"`as_of` is {item['as_of']!r}, not an ISO date", _FIX_DATE)
    return parsed


def _unknown(scan: _Scan, where: str, item: dict, allowed: tuple) -> bool:
    extra = sorted((k for k in item if k not in allowed), key=repr)
    if extra:
        scan.add("CMP001", where, f"unknown key(s) {', '.join(repr(k) for k in extra)}",
                 f"remove them; the allowed keys are {', '.join(allowed)}")
    return not extra


def _observation(scan: _Scan, index: int, item: object) -> Observation | None:
    where = f"observations[{index}]"
    if not isinstance(item, dict):
        scan.add("CMP001", where, "must be a mapping of fields")
        return None
    ident = item.get("id")
    if isinstance(ident, str) and ident.strip():
        where += f" (id {ident!r})"
    ok = _unknown(scan, where, item, _OBS_KEYS)
    if not (isinstance(ident, str) and ident.strip()):
        scan.add("CMP002", where, f"`id` is {ident!r}; it must be a non-empty string", "give each observation a unique non-empty `id`")
        ok = False
    kind = _enum(scan, where, item, "kind", rules.KINDS)
    basis = _enum(scan, where, item, "basis", _BASES)
    period = _enum(scan, where, item, "period", rules.PERIODS)
    confidence = _enum(scan, where, item, "confidence", rules.CONFIDENCES)
    currency = _text(item.get("currency"))
    if currency is None:
        scan.add("CMP002", where, f"`currency` is {item.get('currency')!r}; it must be a currency code such as INR", "set `currency`")
    low_ok, low = _amount(scan, where, item, "low")
    high_ok, high = _amount(scan, where, item, "high")
    if low_ok and low is not None and low < 0:
        scan.add("CMP003", where, f"`low` is {low}, below zero", "amounts are zero or more; use `null` for an undisclosed side")
        low_ok = False
    if high_ok and high is not None and high < 0:
        scan.add("CMP003", where, f"`high` is {high}, below zero", "amounts are zero or more; use `null` for an undisclosed side")
        high_ok = False
    if low_ok and high_ok and low is not None and high is not None and high < low:
        scan.add("CMP003", where, f"`high` ({high}) is below `low` ({low})", "swap them, or correct the figure")
        high_ok = False
    source = _evidence(scan, where, item, "source", "where the figure came from, such as `posting text` or `recruiter call`")
    as_of = _as_of(scan, where, item)
    quote = item.get("quote")
    if quote is not None and not isinstance(quote, str):
        scan.add("CMP002", where, f"`quote` is {quote!r}, not text", "write `quote` as a text string or `null`")
        ok = False
    elif kind == "posted" and not (isinstance(quote, str) and quote.strip()):
        scan.add("CMP004", where, "a `posted` observation needs a `quote` copied verbatim from jd.md",
                 "copy the line that states the pay from jd.md into `quote`, or use kind `recruiter` or `estimate`")
        ok = False
    complete = all(v is not None for v in (kind, basis, period, confidence, currency, source, as_of)) and low_ok and high_ok
    if not (ok and complete):
        return None
    return Observation(ident, kind, low, high, currency.upper(), MarketBasis(basis), period, source, quote, as_of, confidence)


def _currency(scan: _Scan, where: str, item: dict, key: str) -> str | None:
    value = item.get(key)
    text = _text(value)
    if text is None:
        scan.add("CMP002", where, f"`{key}` is {value!r}; it must be a currency code such as USD" if key in item
                 else f"`{key}` is missing", f"set `{key}` to a currency code")
        return None
    return text.upper()


def _conversion(scan: _Scan, index: int, item: object) -> Conversion | None:
    where = f"conversions[{index}]"
    if not isinstance(item, dict):
        scan.add("CMP001", where, "must be a mapping of fields")
        return None
    ok = _unknown(scan, where, item, _CONV_KEYS)
    src = _currency(scan, where, item, "from")
    dst = _currency(scan, where, item, "to")
    rate = None
    value = item.get("rate")
    if value is None and "rate" not in item:
        scan.add("CMP006", where, "`rate` is missing", _FIX_RATE)
    elif isinstance(value, str) and _RATE.fullmatch(value) and Fraction(value) > 0:
        rate = Fraction(value)
    else:
        scan.add("CMP006", where, f"`rate` is {value!r}, not a positive decimal string", _FIX_RATE)
    source = _evidence(scan, where, item, "source", "where the rate came from")
    as_of = _as_of(scan, where, item)
    if not (ok and None not in (src, dst, rate, source, as_of)):
        return None
    return Conversion(src, dst, rate, source, as_of)


def parse(loaded: Loaded) -> tuple[CompRecord | None, list[Issue]]:
    """The record and `[]`, or `(None, issues)` with every problem found (all errors)."""
    scan = _Scan(loaded.rel)
    fm = loaded.fm
    _unknown(scan, "", fm, _TOP_KEYS)
    for key in _REQUIRED_TOP:
        if key not in fm:
            scan.add("CMP001", "", f"`{key}` is missing", f"add `{key}:` (`careeros comp <job> --init` writes a skeleton)")
    if "schema" in fm and (type(fm["schema"]) is not int or fm["schema"] != rules.SCHEMA):
        scan.add("CMP001", "", f"`schema` is {fm['schema']!r}; only schema {rules.SCHEMA} is supported", f"set `schema: {rules.SCHEMA}`")
    raw_obs, raw_conv = fm.get("observations", []), fm.get("conversions", [])
    if "observations" in fm and not isinstance(raw_obs, list):
        scan.add("CMP001", "", "`observations` must be a list")
        raw_obs = []
    if "conversions" in fm and not isinstance(raw_conv, list):
        scan.add("CMP001", "", "`conversions` must be a list")
        raw_conv = []
    market = fm.get("market")
    if "market" in fm and market is not None and not (isinstance(market, str) and market.strip()):
        scan.add("CMP002", "", f"`market` is {market!r}; it must be a `## Markets` row label or null",
                 "write the exact row label from profile.md, or `null`")
    elif market is None and "market" in fm and raw_obs:
        scan.add("CMP002", "", "`market` is null but there are observations",
                 "set `market` to the exact `## Markets` row label the pay should be compared with")

    observations = [_observation(scan, i, item) for i, item in enumerate(raw_obs)]
    seen: dict[str, int] = {}
    for i, item in enumerate(raw_obs):
        ident = item.get("id") if isinstance(item, dict) else None
        if isinstance(ident, str) and ident.strip():
            if ident in seen:
                scan.add("CMP005", f"observations[{i}] (id {ident!r})", f"duplicate id (first used by observations[{seen[ident]}])",
                         "give each observation a unique `id`")
                observations[i] = None
            else:
                seen[ident] = i
    conversions = [_conversion(scan, i, item) for i, item in enumerate(raw_conv)]
    pairs: dict[tuple[str, str], int] = {}
    for i, conv in enumerate(conversions):
        if conv is None:
            continue
        pair = (conv.from_currency, conv.to_currency)
        if pair in pairs:
            scan.add("CMP005", f"conversions[{i}]", f"a second conversion for {pair[0]} to {pair[1]} (first is conversions[{pairs[pair]}])",
                     "keep one conversion per (from, to) pair, even when the rates agree")
            conversions[i] = None
        else:
            pairs[pair] = i
    if scan.issues or None in observations or None in conversions:
        return None, scan.issues
    return CompRecord(market, tuple(observations), tuple(conversions), loaded.body), []


def validate(loaded: Loaded, jd_text: str | None) -> list[Issue]:
    """`parse` findings plus `CMP004` for every `posted` quote not found in `jd_text` after `canon.normalize_text`
    (`jd_text=None` means jd.md is absent). The quote search runs only once the record is otherwise valid."""
    rec, issues = parse(loaded)
    if rec is None:
        return issues
    haystack = normalize_text(jd_text) if jd_text is not None else None
    for i, o in enumerate(rec.observations):
        if o.kind != "posted":
            continue
        where = f"observations[{i}] (id {o.id!r})"
        if haystack is None:
            issues.append(Issue("error", "CMP004", loaded.rel, f"{where}: jd.md is missing, so the quote cannot be checked",
                                "save the posting as jd.md in the job folder, or use kind `recruiter` or `estimate`"))
        elif normalize_text(o.quote) not in haystack:
            issues.append(Issue("error", "CMP004", loaded.rel, f"{where}: the quote is not found in jd.md",
                                "copy the pay line from jd.md exactly (whitespace and line breaks may differ)"))
    return issues


# --- canonical form, stamp, skeleton --------------------------------------------------------------------

def _rate(rate: Fraction) -> str:
    return str(rate.numerator) if rate.denominator == 1 else f"{rate.numerator}/{rate.denominator}"


def canonical(rec: CompRecord) -> dict:
    """The hashed form: observations sorted by id, conversions by `(from, to)`, dates as ISO strings, amounts as ints,
    rates as exact strings (`"83.20"` is `"416/5"`, `"90"` stays `"90"`); `market` omitted when there are no observations;
    `notes` never included."""
    out: dict = {
        "schema": rules.SCHEMA,
        "observations": [
            {"id": o.id, "kind": o.kind, "low": o.low, "high": o.high, "currency": o.currency, "basis": o.basis.value,
             "period": o.period, "source": o.source, "quote": o.quote, "as_of": o.as_of.isoformat(),
             "confidence": o.confidence}
            for o in sorted(rec.observations, key=lambda o: o.id)],
        "conversions": [
            {"from": c.from_currency, "to": c.to_currency, "rate": _rate(c.rate), "source": c.source,
             "as_of": c.as_of.isoformat()}
            for c in sorted(rec.conversions, key=lambda c: (c.from_currency, c.to_currency))],
    }
    if rec.observations:
        out["market"] = rec.market
    return out


EMPTY_STAMP = sha256_hex(canonical_json({"schema": rules.SCHEMA, "observations": [], "conversions": []}))


def stamp(rec: CompRecord | None, raw_text: str | None) -> str:
    """The `compensation` freshness stamp. `rec` parsed: the hash of its canonical form (`EMPTY_STAMP` when it has no
    observations). `rec` None and `raw_text` None (no file): `EMPTY_STAMP`. `rec` None and `raw_text` given (the file
    does not parse): the hash of `invalid:` plus the normalised text."""
    if rec is not None:
        return sha256_hex(canonical_json(canonical(rec))) if rec.observations else EMPTY_STAMP
    if raw_text is None:
        return EMPTY_STAMP
    return sha256_hex(("invalid:" + normalize_text(raw_text)).encode("utf-8", "surrogatepass"))


def skeleton(now: str) -> bytes:
    """The `--init` file: `market: null`, empty lists and a one-line body; `now` (an ISO timestamp from the caller)
    is recorded only in the body, never in a key, so the file is the empty record."""
    fm = {"schema": rules.SCHEMA, "market": None, "observations": [], "conversions": []}
    return join_frontmatter(fm, f"Free notes (ignored by code). Created {now}.\n").encode("utf-8")
