"""Stale versus tampered versus cannot-verify, for a stored evaluation record (spec 8, 9, 16.2, 16.3; plan
clarification 7). Read-only.

`check(record, inputs)` runs, in this order:

0. **Version support.** A `basis_version` or `basis.rules_version` this CareerOS does not know is `EVL010` (warning,
   "cannot be verified"): steps 2 and 4 are skipped and it is never `EVL007`. A tampered result under such a version is
   therefore not detected (documented limit).
1. **Structure.** `record.validate_agent` (`EVL001`-`EVL006`); any error stops the check here.
2. **Self-consistency (tamper, stored data only).** The result has exactly its keys and five string stamps;
   `sha256(canonical(basis)) == basis_sha256`; `basis.rules_version == stamps.rules`; `score(basis)` (with the rules of
   the version the basis declares) reproduces the stored scores, tier, caps, confidence and action items, and
   `result.requirements` agrees with the basis; `render_body(result)` equals the stored body. Every comparison is on
   exact types or canonical JSON, never Python `==` on YAML values (`True == 1`). Each failing part is one `EVL007`
   naming it (`result`, `basis`, `scores`, `body`).
3. **Freshness.** Each stamp against today's value (`rules` against the current `RULES_VERSION`) -> `EVL008` (warning)
   listing them. **Decided here:** a stored `basis_version` other than the current one adds `basis` to the stale list
   (spec 16.3 has `evaluate` replace such a record), so an unsupported basis version is also reported stale.
4. **Derivation (only when the `memory`, `rubric` and `inputs` stamps are current, the versions are supported, the
   stored basis is well-formed and uses the current basis layout, and the memory has no errors).** The basis derived
   today from the agent half and memory, with the stored `rules_version`, must equal the stored basis, and the stored
   `result.requirements` (with reasons) must equal today's; otherwise `EVL007` ("basis": the stored basis was altered).

`EVL011` (warning) lists every `unverified` or `conflict` floor or need in a well-formed stored basis, with the source
cell. Staleness and tampering are reported separately and can both be present.

`validate_workspace(root)` runs this and `ledgercheck.verify` (EVL009) for every `jobs/discovered/*/evaluation.md`; it
never writes and never raises on a hand-edited file.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from careeros.core.evaluation import ledgercheck, record, rules, score
from careeros.core.evaluation.canon import canonical_json, sha256_hex
from careeros.core.evaluation.markets import MarketError
from careeros.core.evaluation.record import RESULT_KEYS, STAMP_KEYS, Inputs, Record, RecordError
from careeros.core.evaluation.rules import UnsupportedVersion
from careeros.core.models import Issue
from careeros.core.workspace import WorkspaceError

_FIX_TAMPER = "do not edit result: or the body; run `careeros evaluate <job>` to recompute (it logs the replacement)"
_FIX_STALE = "run `careeros evaluate <job>` to refresh the evaluation"
_FIX_VERSION = "run `careeros evaluate <job>`: it recomputes with this CareerOS's versions"
_FIX_MARKET = "make the profile `## Markets` row state the floor as one amount with a currency and the sponsorship as " \
              "`required` or `not needed`, and give the same values in `job_facts`; then run `careeros evaluate <job>`"


@dataclass(frozen=True)
class IntegrityReport:
    """`stale`: stamp names (and `basis`) that differ from today; `tampered`: parts of the result that disagree with
    their own stored inputs; `unsupported`: why the record cannot be verified (EVL010), else None; `issues`: every
    EVL001-EVL008, EVL010 and EVL011 issue, structural ones first."""

    stale: tuple[str, ...] = ()
    tampered: tuple[str, ...] = ()
    unsupported: str | None = None
    issues: tuple[Issue, ...] = ()


def _same(a: object, b: object) -> bool:
    """Equal in canonical form (so `True` and `1`, or `1` and `1.0`, differ); False when either cannot be hashed."""
    try:
        return canonical_json(a) == canonical_json(b)
    except (TypeError, ValueError):
        return False


def _unsupported(result: dict | None) -> str | None:
    basis = result.get("basis") if isinstance(result, dict) else None
    if not isinstance(basis, dict):
        return None
    try:
        rules.basis_schema_for(basis.get("basis_version"))
    except UnsupportedVersion:
        return f"cannot be verified: basis_version {basis.get('basis_version')!r} is not supported by this CareerOS"
    try:
        rules.rules_for(basis.get("rules_version"))
    except UnsupportedVersion:
        return f"cannot be verified: rules_version {basis.get('rules_version')!r} is not supported by this CareerOS"
    return None


def _score_field(stored: object, want: object) -> bool:
    if want is None:
        return stored is None
    return type(stored) is float and stored == want


def _self_consistency(rec: Record, result: dict) -> tuple[list[tuple[str, str]], bool]:
    """(failing parts with details, whether the stored basis scored)."""
    found: list[tuple[str, str]] = []
    keys = set(result)
    if keys != set(RESULT_KEYS):
        missing, extra = sorted(set(RESULT_KEYS) - keys), sorted(str(k) for k in keys - set(RESULT_KEYS))
        found.append(("result", f"result: keys differ (missing: {', '.join(missing) or 'none'}; "
                                f"unexpected: {', '.join(extra) or 'none'})"))
    stamps = result.get("stamps")
    if not (isinstance(stamps, dict) and set(stamps) == set(STAMP_KEYS) and all(type(v) is str for v in stamps.values())):
        found.append(("result", "result.stamps is not the five stamp strings"))
        stamps = None
    basis = result.get("basis")
    try:
        digest = sha256_hex(canonical_json(basis))
    except (TypeError, ValueError):
        digest = None
    if digest is None or type(result.get("basis_sha256")) is not str or digest != result["basis_sha256"]:
        found.append(("basis", "the stored basis does not match result.basis_sha256"))
    if stamps is not None and isinstance(basis, dict):
        if not (type(basis.get("rules_version")) is str and basis["rules_version"] == stamps["rules"]):
            found.append(("basis", f"basis.rules_version {basis.get('rules_version')!r} differs from stamps.rules "
                                   f"{stamps['rules']!r}"))
    scored = None
    try:
        scored = score.score(basis)
    except UnsupportedVersion:        # checked in step 0; kept so a version problem can never read as tamper
        pass
    except ValueError as exc:
        found.append(("basis", f"the stored basis cannot be scored ({exc})"))
    if scored is not None:
        bad = [name for name in ("coverage", "fit", "combined")
               if not _score_field(result.get(name), score.stored_score(getattr(scored, name)))]
        if not (type(result.get("tier")) is str and result["tier"] == str(scored.tier)):
            bad.append("tier")
        if not (type(result.get("confidence")) is str and result["confidence"] == str(scored.confidence)):
            bad.append("confidence")
        caps = [{"code": c.code, "ceiling": str(c.ceiling), "reason": c.reason} for c in scored.caps]
        if not _same(result.get("caps"), caps):
            bad.append("caps")
        if not _same(result.get("action_items"), [score.action_item_text(i) for i in scored.action_items]):
            bad.append("action_items")
        shown = result.get("requirements")
        expected = sorted(basis["requirements"], key=lambda r: score._natural(r["id"]))
        if not (isinstance(shown, list) and len(shown) == len(expected) and all(
                isinstance(s, dict) and _same({k: s.get(k) for k in ("id", "outcome", "facts", "status_floor")},
                                              {k: e[k] for k in ("id", "outcome", "facts", "status_floor")})
                for s, e in zip(shown, expected))):
            bad.append("requirements")
        if bad:
            found.append(("scores", f"stored {', '.join(bad)} differ from the recomputation of the stored basis"))
    try:
        body_ok = record.render_body(result) == rec.body
    except (KeyError, TypeError, ValueError, AttributeError):
        body_ok = False
    if not body_ok:
        found.append(("body", "the body differs from the one rendered from result:"))
    return found, scored is not None


def _freshness(rec: Record, result: dict, inputs: Inputs) -> list[str]:
    stored = result.get("stamps") if isinstance(result.get("stamps"), dict) else {}
    current = record.stamps(inputs.jd_text, inputs.career, inputs.rubric, rules.RULES_VERSION, rec.fm.get("agent"),
                            lexicon=inputs.lexicon, markets=inputs.markets, now=inputs.now)
    stale = [name for name in STAMP_KEYS
             if not (type(stored.get(name)) is str and stored[name] == getattr(current, name))]
    basis = result.get("basis")
    if not (isinstance(basis, dict) and type(basis.get("basis_version")) is str
            and basis["basis_version"] == rules.BASIS_VERSION):
        stale.append("basis")
    return stale


def _derivation(rec: Record, result: dict, inputs: Inputs) -> str | None:
    """Why the stored basis is not the one today's inputs derive, or None (also None when it cannot be derived)."""
    agent, issues = record.parse_agent(rec)
    if issues:
        return None
    try:
        basis, derived = record.derive_basis(agent, inputs)
    except (WorkspaceError, MarketError):
        return None
    stored = result["basis"]
    basis["rules_version"] = stored["rules_version"]
    if not _same(basis, stored):
        return "the stored basis differs from the one derived from the current agent half and memory"
    if not _same(record.display_requirements(derived), result.get("requirements")):
        return "result.requirements differ from the ones derived from the current agent half and memory"
    return None


def market_issues(rec: Record) -> list[Issue]:
    """EVL011: every unverified or conflicting floor or need in the stored (well-formed) basis, with its source cell."""
    cap = rec.result["basis"]["cap_inputs"]
    out = []
    for name, label in (("need", "sponsorship need"), ("floor", "floor")):
        field = cap[name]
        state = field["verification"]
        if state not in ("unverified", "conflict"):
            continue
        if cap["market"] is None:
            where = "no Markets row: job_facts.market is not named"
        else:
            where = f'the Markets row {cap["market"]!r} (cell "{field["source_cell"]}")'
        out.append(Issue("warning", "EVL011", rec.rel, f"{label} is {state} against {where}", _FIX_MARKET))
    return out


def check(rec: Record, inputs: Inputs) -> IntegrityReport:
    """Stale vs tampered vs cannot-verify for one loaded record, in the order of the module docstring. Read-only."""
    result = rec.result
    unsupported = _unsupported(result)
    version_issues = [Issue("warning", "EVL010", rec.rel, unsupported, _FIX_VERSION)] if unsupported else []
    structure = record.validate_agent(rec, inputs.jd_text, inputs.career, inputs.rubric, inputs.markets, inputs.job_id)
    if structure or result is None:
        return IntegrityReport((), (), unsupported, tuple(structure + version_issues))
    found: list[tuple[str, str]] = []
    basis_ok = False
    if not unsupported:
        found, basis_ok = _self_consistency(rec, result)
    stale = _freshness(rec, result, inputs)
    if (basis_ok and not {"memory", "rubric", "inputs", "basis"} & set(stale) and not inputs.career.errors):
        detail = _derivation(rec, result, inputs)
        if detail:
            found.append(("basis", detail))
    issues = [Issue("error", "EVL007", rec.rel, f"tampered ({part}): {detail}", _FIX_TAMPER) for part, detail in found]
    if stale:
        message = f"stale: {', '.join(stale)} changed since this evaluation"
        if "rubric" in stale and inputs.rubric.problems:
            message += f" (rubric: {'; '.join(inputs.rubric.problems)})"
        issues.append(Issue("warning", "EVL008", rec.rel, message, _FIX_STALE))
    issues += version_issues
    if basis_ok:
        issues += market_issues(rec)
    tampered = tuple(dict.fromkeys(part for part, _ in found))
    return IntegrityReport(tuple(stale), tampered, unsupported, tuple(issues))


def _lenient_events(root: Path) -> list[dict]:
    path = Path(root) / "ledger.jsonl"
    events: list[dict] = []
    try:
        data = path.read_bytes()
    except OSError:
        return events
    for raw in data.split(b"\n"):
        if not raw.strip():
            continue
        try:
            event = json.loads(raw)
        except ValueError:
            continue
        if isinstance(event, dict):
            events.append(event)
    return events


def validate_workspace(root: Path, events: list[dict] | None = None, now: str | None = None) -> list[Issue]:
    """EVL001-EVL011 for every evaluation file in the workspace. Read-only; never raises on a hand-edited file."""
    root = Path(root)
    issues: list[Issue] = []
    records: list[Record] = []
    for path in sorted((root / "jobs" / "discovered").glob(f"*/{record.EVALUATION_FILE}")):
        try:
            records.append(record.load(path, root))
        except RecordError as exc:
            issues.append(Issue("error", exc.code, path.relative_to(root).as_posix(), str(exc),
                                "fix the frontmatter, or delete the file and run `careeros evaluate <job> --init`"))
    if not records:
        return issues
    events = _lenient_events(root) if events is None else events
    base = record.workspace_inputs(root, now)
    for rec in records:
        try:
            issues += check(rec, record.for_job(base, rec.path.parent)).issues
            issues += ledgercheck.verify(rec, records, events)
        except Exception as exc:       # a last guard: validate must report, never crash, on any hand-edited file
            issues.append(Issue("error", "EVL001", rec.rel, f"could not be checked: {exc!r}",
                                "fix the file, or run `careeros evaluate <job>`"))
    return issues
