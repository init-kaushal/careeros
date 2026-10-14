"""The pure scorer (spec section 6 and 16.1): coverage, fit, combined score, tier, caps, confidence, action items.

`score(basis)` is a pure function of a stored basis (`result.basis`, layout `BasisSchema` v1) and the `Rules` of
the version that basis declares (it resolves them itself): no memory, profile, clock or filesystem, so history can be recomputed from a stored
record. All arithmetic is on exact `Fraction`s and every comparison against a threshold uses the exact value; rounding
exists only in `stored_score`, which no decision reads. A basis holds integers, strings, booleans and nulls only.

Basis v1 keys read here (Task B's `Basis.complete` writes them):

* `requirements`: `[{id, class, outcome, facts, status_floor}]`; `requirement_count` (all of them, including
  unverifiable ones); `rubric_state` (`ok | missing | invalid`);
* `fit`: `[{row, weight, judgement}]` (integer weights; `row` is the normalised rubric label, audit only);
* `cap_inputs`: `{market, need: {value, verification, source_cell, row_state}, listing, quote, range: {low, high, currency, basis} | null,
  floor: {amount, currency, basis, verification, source_cell, row_state}, confidence, as_of}`. `need` and `floor` are always present:
  an input the agent omitted has `value`/`amount` null and verification `unverified`, with the Markets cell kept in
  `source_cell` (null only when no market was named).

Basis v2 differs only in `cap_inputs`: `{market, need, listing, quote, floor: {amount, currency, basis, verification,
source_cell, row_state}, compensation}` where `compensation` is `careeros.core.compensation.derive.to_basis` (`status`,
`kind`, `low`, `high` stored as an int or a reduced `"p/q"` string or null, `kind_complete`, `kind_confident`,
`observations`, `notes`). The floor comes from the Markets row alone (`verified` or `unverified`). The v2 cap is
`compensation.derive.cap_applies` (the one cap decision), the unknown-pay confidence step applies when the floor is
verified and `compensation.status != "effective"`, and pay items come from the notes. The v1 paths are unchanged.

An effective estimate with low confidence gets only the estimate-only item (no low-confidence item: that one is for
`posted` and `recruiter`). The released pairs are (basis, rules) = (1, 1) and (2, 2); a mixed pair is malformed.

Errors: `score` takes only the basis and resolves `basis_schema_for(basis_version)` then `rules_for(rules_version)`
itself. Callers catch `UnsupportedVersion` first (cannot verify, EVL010), then `ValueError` (malformed, EVL007). An
unknown version is always `UnsupportedVersion`, never `ValueError`; every other defect (missing or extra keys, wrong
types, values outside their enum, `requirement_count` differing from the requirement list) is `ValueError("malformed
basis: ...")`. The basis holds no fraction strings today (weights and amounts are ints); every future one must pass
`canon.parse_exact`.

Decisions this module pins (changing any of them requires a new rules version):

* Fit is `None` when the rubric is not `ok` or no fit row is present; the combined score then equals coverage and the
  confidence loses one step. Fit is independent of coverage, so an `unrated` result may still carry a fit.
* `unrated` (coverage undefined): combined is `None`, tier `unrated`, confidence `low`; caps are still listed.
* Caps: `sponsorship` (ceiling `weak`), `compensation` (ceiling `weak`), `must_gaps` (ceiling `stretch`), listed in that
  order. They use verified inputs only. The lowest ceiling wins and a cap never raises a tier.
* Decision-relevant unknown (confidence): the listing is `unknown` and the need is not established as `no` (a need that
  is `yes`, unknown, unverified or in conflict counts as not established), or the range is null while a verified floor
  exists.
* Action items (`ActionItem(ref, message)`, stored as `"<ref>: <message>"`) follow spec 6.6 in this order: unverifiable
  requirements, `must` gaps, claimed-only support (each by requirement id), then sponsorship (need conflict, need
  unverified, listing unknown), then pay (floor conflict, floor unverified, range not disclosed, currency mismatch,
  basis mismatch, low source confidence). A need verified as `no` produces no sponsorship item. An `unverified` need or
  floor ALWAYS produces an item, worded by cause: the agent gave no value while the row is machine-readable, the row is
  not machine-readable, or no market was named. Requirement ids sort naturally (`r2` before `r10`); listed facts are sorted.

Stored text constants: every cap reason and action-item template is a named module constant. Changing any of them, or any
decision above, requires a new rules version: `REASON_SPONSORSHIP`, `REASON_COMPENSATION`, `REASON_MUST_GAPS`,
`ITEM_UNVERIFIABLE`, `ITEM_MUST_GAP`, `ITEM_CLAIMED`, `ITEM_NEED_CONFLICT`, `ITEM_NEED_NO_VALUE`, `ITEM_NEED_UNREADABLE`,
`ITEM_NEED_NO_MARKET`, `ITEM_LISTING_UNKNOWN`, `ITEM_FLOOR_CONFLICT`, `ITEM_FLOOR_NO_VALUE`, `ITEM_FLOOR_INCOMPLETE`, `ITEM_FLOOR_UNREADABLE`,
`ITEM_FLOOR_NO_MARKET`, `ITEM_RANGE_UNDISCLOSED`, `ITEM_CURRENCY_MISMATCH`, `ITEM_BASIS_MISMATCH`, `ITEM_LOW_CONFIDENCE`;
version 2 adds `REASON_COMPENSATION_V2`, `ITEM_PAY_ESTIMATE_ONLY`, `ITEM_PAY_CONFLICT`, `ITEM_PAY_INCOMPARABLE`,
`ITEM_PAY_LOW_CONFIDENCE`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from fractions import Fraction

from careeros.core.compensation.derive import cap_applies
from careeros.core.evaluation.canon import fixed4, parse_exact
from careeros.core.evaluation.model import ActionItem, Cap, Confidence, Tier
from careeros.core.evaluation.rules import RULES, RULES_VERSION, Rules, basis_schema_for, rules_for

CAP_SPONSORSHIP = "sponsorship"
CAP_COMPENSATION = "compensation"
CAP_MUST_GAPS = "must_gaps"

REASON_SPONSORSHIP = "sponsorship is needed (verified against the Markets row) and the posting does not offer it"
REASON_COMPENSATION = ("the posted range tops out at {high} {currency} ({basis}), "
                       "below the verified floor of {amount} {currency} ({basis})")
REASON_MUST_GAPS = "{n} must requirements are gaps ({ids})"
REASON_COMPENSATION_V2 = ("the {kind} range tops out at {high} {currency} ({basis}), "
                          "below the verified floor of {amount} {currency} ({basis})")

ITEM_UNVERIFIABLE = "cannot be derived from memory; ask the user, and add the fact with `careeros memory add` if true"
ITEM_MUST_GAP = ("must requirement is not met by memory; ask the user whether they have it, "
                 "and add the fact with `careeros memory add` if true")
ITEM_CLAIMED = "supported only by claimed facts ({facts}); ask the user to confirm them"
ITEM_NEED_CONFLICT = 'resolve the profile/agent mismatch for the need (the Markets row says "{cell}")'
ITEM_NEED_NO_VALUE = "the Markets row says {cell} but the evaluation gave no value; state it so the cap can be checked"
ITEM_NEED_UNREADABLE = "the Markets row is not machine-readable; clarify it in profile.md"
ITEM_NEED_NO_MARKET = "no market is named; name the profile Markets row so the need can be checked"
ITEM_LISTING_UNKNOWN = "not mentioned in the posting; ask about sponsorship before investing time"
ITEM_FLOOR_CONFLICT = 'resolve the profile/agent mismatch for the floor (the Markets row says "{cell}")'
ITEM_FLOOR_NO_VALUE = "the Markets row says {cell} but the evaluation gave no floor; state it so the cap can be checked"
ITEM_FLOOR_INCOMPLETE = ("the evaluation's floor is incomplete (amount and currency are both required); "
                         "state both so the cap can be checked")
ITEM_FLOOR_UNREADABLE = "the Markets row is not machine-readable; clarify it in profile.md"
ITEM_FLOOR_NO_MARKET = "no market is named; name the profile Markets row so the floor can be checked"
ITEM_RANGE_UNDISCLOSED = "not disclosed in the posting; ask the recruiter"
ITEM_CURRENCY_MISMATCH = "range currency {range} differs from floor currency {floor}; compare in one currency before deciding"
ITEM_BASIS_MISMATCH = "range basis {range} differs from floor basis {floor}; ask the recruiter for the base figure"
ITEM_LOW_CONFIDENCE = "the range source confidence is {confidence}; confirm the figure with the recruiter"
ITEM_PAY_ESTIMATE_ONLY = "pay is estimated only ({kind}); ask the recruiter for a range"
ITEM_PAY_CONFLICT = "resolve conflicting {kind} pay figures"
ITEM_PAY_INCOMPARABLE = "pay observation {ref} cannot be compared with the floor ({reason}); resolve it before relying on pay"
ITEM_PAY_LOW_CONFIDENCE = "a {kind} pay figure has low source confidence; confirm it with the recruiter"

_ORDER = (Tier.SKIP, Tier.WEAK, Tier.STRETCH, Tier.PURSUE)
_CONFIDENCE = (Confidence.LOW, Confidence.MEDIUM, Confidence.HIGH)
_OUTCOMES = ("covered", "adjacent", "gap", "unverifiable")
_ESTABLISHED_CONFIDENCE = ("high", "medium")
_VERIFICATION = ("verified", "unverified", "conflict")
_STATUS = ("claimed", "confirmed", "verified", None)
_RUBRIC_STATES = ("ok", "missing", "invalid")
_LISTINGS = ("offered", "not_offered", "unknown")
_CONFIDENCES = ("high", "medium", "low", "unknown", None)
_BASES = ("base", "total", None)
_ROW_STATES = ("parseable", "unreadable", "no_market")
_VERIFICATION_V2 = ("verified", "unverified")
_COMP_STATUS = ("effective", "none", "conflict")
_COMP_KINDS = ("posted", "recruiter", "estimate")
_COMP_REASONS = ("FLOOR_UNVERIFIED", "BASIS_MISMATCH", "FX_CONVERSION_MISSING", "FX_RATE_OUTSIDE_WINDOW")
_PAIRED = ("1", "2")        # released basis/rules versions, which are only ever used as the same number on both
_NOTE_CODES = ("estimate_only", "conflict", "incomparable")
_NATURAL = re.compile(r"([A-Za-z_]*)(\d+)")


@dataclass(frozen=True)
class ScoreResult:
    coverage: Fraction | None
    fit: Fraction | None
    combined: Fraction | None
    tier: Tier
    caps: tuple[Cap, ...]
    confidence: Confidence
    action_items: tuple[ActionItem, ...]


def action_item_text(item: ActionItem) -> str:
    """The stored string form of an action item."""
    return f"{item.ref}: {item.message}"


def stored_score(value: Fraction | None) -> float | None:
    """The value stored in YAML for a score: `float(fixed4(x))`, or None when undefined. The only float in the scorer."""
    return None if value is None else float(fixed4(value))


def tier_for(combined: Fraction, rules: Rules | None = None) -> Tier:
    """The tier of an exact combined score (never of a rounded one)."""
    rules = rules or RULES[RULES_VERSION]
    if combined >= rules.tier_pursue:
        return Tier.PURSUE
    if combined >= rules.tier_stretch:
        return Tier.STRETCH
    if combined >= rules.tier_weak:
        return Tier.WEAK
    return Tier.SKIP


def _lowest(tier: Tier, ceilings: list[Tier]) -> Tier:
    return min([tier, *ceilings], key=_ORDER.index) if tier in _ORDER else tier


def _natural(ident: str) -> tuple:
    m = _NATURAL.fullmatch(ident)
    return (m[1], int(m[2]), ident) if m else (ident, -1, ident)


# --- validation ---------------------------------------------------------------------------------

def _bad(message: str):
    raise ValueError(f"malformed basis: {message}")


def _one_of(value, allowed, name: str) -> None:
    if not any(type(value) is type(a) and value == a for a in allowed):
        _bad(f"{name} {value!r} is not one of {allowed}")


def _int_or_none(value, name: str) -> None:
    if value is not None and type(value) is not int:
        _bad(f"{name} {value!r} is not an integer or null")


def _str_or_none(value, name: str) -> None:
    if value is not None and type(value) is not str:
        _bad(f"{name} {value!r} is not a string or null")


def _keys(obj, keys: tuple[str, ...], name: str) -> None:
    if not isinstance(obj, dict) or set(obj) != set(keys):
        _bad(f"{name} must be a mapping with exactly the keys {', '.join(keys)}")


def _validate(basis: dict, keys: tuple[str, ...], rules: Rules) -> None:
    _keys(basis, keys, "basis")
    count = basis["requirement_count"]
    if type(count) is not int or not isinstance(basis["requirements"], list) or count != len(basis["requirements"]):
        _bad("requirement_count does not equal the number of requirements")
    _one_of(basis["rubric_state"], _RUBRIC_STATES, "rubric_state")
    for r in basis["requirements"]:
        _keys(r, ("id", "class", "outcome", "facts", "status_floor"), "requirement")
        if type(r["id"]) is not str:
            _bad(f"requirement id {r['id']!r} is not a string")
        _one_of(r["class"], tuple(rules.class_weights), f"requirement {r['id']} class")
        _one_of(r["outcome"], _OUTCOMES, f"requirement {r['id']} outcome")
        _one_of(r["status_floor"], _STATUS, f"requirement {r['id']} status_floor")
        if not isinstance(r["facts"], list) or any(type(f) is not str for f in r["facts"]):
            _bad(f"requirement {r['id']} facts must be a list of strings")
    weights = {int(w) for w in rules.fit_weights.values()}
    if not isinstance(basis["fit"], list):
        _bad("fit must be a list")
    for row in basis["fit"]:
        _keys(row, ("row", "weight", "judgement"), "fit row")
        if type(row["row"]) is not str:
            _bad(f"fit row label {row['row']!r} is not a string")
        if type(row["weight"]) is not int or row["weight"] not in weights:
            _bad(f"fit weight {row['weight']!r} is not one of {sorted(weights)}")
        _one_of(row["judgement"], tuple(rules.fit_credits), "fit judgement")
    cap = basis["cap_inputs"]
    if basis["basis_version"] == "2":
        _validate_cap_v2(cap)
        return
    _keys(cap, ("market", "need", "listing", "quote", "range", "floor", "confidence", "as_of"), "cap_inputs")
    _str_or_none(cap["market"], "market")
    _str_or_none(cap["as_of"], "as_of")
    _one_of(cap["listing"], _LISTINGS, "listing")
    if type(cap["quote"]) is not bool:
        _bad(f"quote {cap['quote']!r} is not a boolean")
    _one_of(cap["confidence"], _CONFIDENCES, "compensation confidence")
    _keys(cap["need"], ("value", "verification", "source_cell", "row_state"), "need")
    _one_of(cap["need"]["value"], ("yes", "no", None), "need value")
    _one_of(cap["need"]["verification"], _VERIFICATION, "need verification")
    _str_or_none(cap["need"]["source_cell"], "need source_cell")
    _one_of(cap["need"]["row_state"], _ROW_STATES, "need row_state")
    _keys(cap["floor"], ("amount", "currency", "basis", "verification", "source_cell", "row_state"), "floor")
    _int_or_none(cap["floor"]["amount"], "floor amount")
    _str_or_none(cap["floor"]["currency"], "floor currency")
    _one_of(cap["floor"]["basis"], _BASES, "floor basis")
    _one_of(cap["floor"]["verification"], _VERIFICATION, "floor verification")
    _str_or_none(cap["floor"]["source_cell"], "floor source_cell")
    _one_of(cap["floor"]["row_state"], _ROW_STATES, "floor row_state")
    floor = cap["floor"]
    _state("need", cap["need"], cap["need"]["value"] is not None, cap["need"]["value"] is None)
    _state("floor", floor, floor["amount"] is not None and floor["currency"] is not None,
           floor["amount"] is None and floor["currency"] is None)
    if floor["verification"] == "verified" and floor["basis"] is None:
        _bad("floor is verified but has no basis")
    if cap["range"] is not None:
        _keys(cap["range"], ("low", "high", "currency", "basis"), "range")
        _int_or_none(cap["range"]["low"], "range low")
        if type(cap["range"]["high"]) is not int:
            _bad(f"range high {cap['range']['high']!r} is not an integer")
        _str_or_none(cap["range"]["currency"], "range currency")
        _one_of(cap["range"]["basis"], _BASES, "range basis")


def _exact_or_none(value, name: str) -> None:
    if value is None:
        return
    try:
        parse_exact(value)
    except (TypeError, ValueError):
        _bad(f"{name} {value!r} is not an exact quantity (an integer or a reduced p/q string) or null")


def _validate_cap_v2(cap: dict) -> None:
    _keys(cap, ("market", "need", "listing", "quote", "floor", "compensation"), "cap_inputs")
    _str_or_none(cap["market"], "market")
    _one_of(cap["listing"], _LISTINGS, "listing")
    if type(cap["quote"]) is not bool:
        _bad(f"quote {cap['quote']!r} is not a boolean")
    _keys(cap["need"], ("value", "verification", "source_cell", "row_state"), "need")
    _one_of(cap["need"]["value"], ("yes", "no", None), "need value")
    _one_of(cap["need"]["verification"], _VERIFICATION, "need verification")
    _str_or_none(cap["need"]["source_cell"], "need source_cell")
    _one_of(cap["need"]["row_state"], _ROW_STATES, "need row_state")
    floor = cap["floor"]
    _keys(floor, ("amount", "currency", "basis", "verification", "source_cell", "row_state"), "floor")
    _int_or_none(floor["amount"], "floor amount")
    _str_or_none(floor["currency"], "floor currency")
    _one_of(floor["basis"], _BASES, "floor basis")
    _one_of(floor["verification"], _VERIFICATION_V2, "floor verification")
    _str_or_none(floor["source_cell"], "floor source_cell")
    _one_of(floor["row_state"], _ROW_STATES, "floor row_state")
    _state("need", cap["need"], cap["need"]["value"] is not None, cap["need"]["value"] is None)
    _state("floor", floor, floor["amount"] is not None and floor["currency"] is not None,
           floor["amount"] is None and floor["currency"] is None)
    if floor["verification"] == "verified" and floor["basis"] is None:
        _bad("floor is verified but has no basis")
    if (floor["verification"] == "unverified" and floor["row_state"] == "parseable"
            and floor["amount"] is not None and floor["currency"] is not None):
        _bad("floor is unverified although its Markets row is readable and complete")
    comp = cap["compensation"]
    _keys(comp, ("status", "kind", "low", "high", "kind_complete", "kind_confident", "observations", "notes"),
          "compensation")
    _one_of(comp["status"], _COMP_STATUS, "compensation status")
    _one_of(comp["kind"], (*_COMP_KINDS, None), "compensation kind")
    _exact_or_none(comp["low"], "compensation low")
    _exact_or_none(comp["high"], "compensation high")
    for flag in ("kind_complete", "kind_confident"):
        if type(comp[flag]) is not bool:
            _bad(f"compensation {flag} {comp[flag]!r} is not a boolean")
    if not isinstance(comp["observations"], list):
        _bad("compensation observations must be a list")
    for o in comp["observations"]:
        _keys(o, ("id", "kind", "comparable", "reason"), "compensation observation")
        if type(o["id"]) is not str:
            _bad(f"compensation observation id {o['id']!r} is not a string")
        _one_of(o["kind"], _COMP_KINDS, "compensation observation kind")
        if type(o["comparable"]) is not bool:
            _bad(f"compensation observation {o['id']} comparable {o['comparable']!r} is not a boolean")
        _one_of(o["reason"], (*_COMP_REASONS, None), f"compensation observation {o['id']} reason")
        if o["comparable"] != (o["reason"] is None):
            _bad(f"compensation observation {o['id']} is comparable exactly when it has no reason")
    if not isinstance(comp["notes"], list):
        _bad("compensation notes must be a list")
    for n in comp["notes"]:
        _keys(n, ("code", "ref", "detail"), "compensation note")
        _one_of(n["code"], _NOTE_CODES, "compensation note code")
        _str_or_none(n["ref"], "compensation note ref")
        if type(n["detail"]) is not str:
            _bad(f"compensation note detail {n['detail']!r} is not a string")
    _validate_compensation_fields(comp)


def _validate_compensation_fields(comp: dict) -> None:
    """Cross-field rules: shapes `compensation.derive.to_basis` can never produce are malformed."""
    status, kind, obs = comp["status"], comp["kind"], comp["observations"]
    ids = [o["id"] for o in obs]
    if len(set(ids)) != len(ids):
        _bad("compensation observation ids are not unique")
    same = [o for o in obs if o["kind"] == kind]
    if status == "effective" and (kind is None or not same):
        _bad("compensation is effective without a kind or an observation of that kind")
    low = None if comp["low"] is None else parse_exact(comp["low"])
    high = None if comp["high"] is None else parse_exact(comp["high"])
    if status != "effective" and (low is not None or high is not None):
        _bad(f"compensation is {status} but carries a range")
    if (low is not None and low < 0) or (high is not None and high < 0):
        _bad("compensation bounds are negative")
    if low is not None and high is not None and low > high:
        _bad("compensation low exceeds high")
    if comp["kind_complete"] != (kind is not None and bool(same) and all(o["comparable"] for o in same)):
        _bad("compensation kind_complete disagrees with the observations of the kind")
    incomparable = {o["id"]: o["reason"] for o in obs if not o["comparable"]}
    seen: list[str] = []
    for n in comp["notes"]:
        if n["code"] == "incomparable":
            if n["ref"] not in incomparable or n["detail"] != incomparable[n["ref"]]:
                _bad(f"incomparable note {n['ref']!r} does not match an incomparable observation and its reason")
            seen.append(n["ref"])
        elif n["ref"] is not None or kind is None or n["detail"] != kind:
            _bad(f"{n['code']} note must have a null ref and the kind as detail")
    if sorted(seen) != sorted(incomparable):
        _bad("every incomparable observation needs exactly one incomparable note")
    codes = [n["code"] for n in comp["notes"]]
    if codes.count("estimate_only") != (1 if status == "effective" and kind == "estimate" else 0):
        _bad("an estimate_only note exists exactly when an estimate is the effective kind")
    if codes.count("conflict") != (1 if status == "conflict" else 0):
        _bad("a conflict note exists exactly when the status is conflict")


def _state(name: str, field: dict, complete: bool, empty: bool) -> None:
    """State combinations the builder can never produce: `complete` = the agent's value is fully given, `empty` = absent."""
    state, row = field["verification"], field["row_state"]
    if row == "no_market" and field["source_cell"] is not None:
        _bad(f"{name} has no market but a source cell")
    if state in ("verified", "conflict") and row != "parseable":
        _bad(f"{name} is {state} but its Markets row is {row}")
    if state in ("verified", "conflict") and not complete:
        _bad(f"{name} is {state} without a complete value")
    if state == "unverified" and row == "parseable" and complete:
        _bad(f"{name} is unverified with a readable row and a complete value")


def _requirements(basis: dict) -> list[dict]:
    return sorted(basis["requirements"], key=lambda r: _natural(r["id"]))


# --- arithmetic ---------------------------------------------------------------------------------

def _coverage(reqs: list[dict], rules: Rules) -> Fraction | None:
    total = got = Fraction(0)
    for r in reqs:
        if r["outcome"] == "unverifiable":
            continue
        weight = rules.class_weights[r["class"]]
        total += weight
        got += rules.credits[r["outcome"]] * weight
    return got / total if total else None


def _fit(basis: dict, rules: Rules) -> Fraction | None:
    rows = basis["fit"]
    if basis["rubric_state"] != "ok" or not rows:
        return None
    total = got = Fraction(0)
    for row in rows:
        total += row["weight"]
        got += rules.fit_credits[row["judgement"]] * row["weight"]
    return got / total


def _compensation_cap(cap_inputs: dict) -> Cap | None:
    rng, floor = cap_inputs["range"], cap_inputs["floor"]
    if rng is None or floor["verification"] != "verified":
        return None
    high, amount = rng["high"], floor["amount"]
    if amount is None or high >= amount:
        return None
    if rng["currency"] is None or rng["currency"] != floor["currency"]:
        return None
    if rng["basis"] is None or rng["basis"] != floor["basis"]:
        return None
    if cap_inputs["confidence"] not in _ESTABLISHED_CONFIDENCE:
        return None
    return Cap(CAP_COMPENSATION, Tier.WEAK,
               REASON_COMPENSATION.format(high=high, amount=amount, currency=rng["currency"], basis=rng["basis"]))


def _money(value: Fraction) -> str:
    """A whole amount as a plain integer, any other as a two-decimal half-up decimal (integer arithmetic only)."""
    if value.denominator == 1:
        return str(value.numerator)
    q, r = divmod(value.numerator * 100, value.denominator)
    if 2 * r >= value.denominator:
        q += 1
    return f"{q // 100}.{q % 100:02d}"


def _compensation_cap_v2(cap_inputs: dict) -> Cap | None:
    floor, comp = cap_inputs["floor"], cap_inputs["compensation"]
    amount = None if floor["amount"] is None else Fraction(floor["amount"])
    if not cap_applies(comp, amount, floor["verification"] == "verified"):
        return None
    return Cap(CAP_COMPENSATION, Tier.WEAK, REASON_COMPENSATION_V2.format(
        kind=comp["kind"], high=_money(parse_exact(comp["high"])), amount=_money(amount),
        currency=floor["currency"], basis=floor["basis"]))


def _sponsorship_cap(cap_inputs: dict) -> Cap | None:
    need = cap_inputs["need"]
    if (need["verification"] == "verified" and need["value"] == "yes"
            and cap_inputs["listing"] == "not_offered" and cap_inputs["quote"] is True):
        return Cap(CAP_SPONSORSHIP, Tier.WEAK, REASON_SPONSORSHIP)
    return None


def _caps(reqs: list[dict], cap_inputs: dict, v2: bool = False) -> tuple[Cap, ...]:
    caps = [_sponsorship_cap(cap_inputs), (_compensation_cap_v2 if v2 else _compensation_cap)(cap_inputs)]
    gaps = [r["id"] for r in reqs if r["class"] == "must" and r["outcome"] == "gap"]
    if len(gaps) >= 2:
        caps.append(Cap(CAP_MUST_GAPS, Tier.STRETCH, REASON_MUST_GAPS.format(n=len(gaps), ids=", ".join(gaps))))
    return tuple(c for c in caps if c is not None)


def _share(part: Fraction, whole: Fraction) -> Fraction:
    return part / whole if whole else Fraction(0)


def _confidence(basis: dict, reqs: list[dict], rules: Rules, coverage: Fraction | None, fit: Fraction | None,
                v2: bool = False) -> Confidence:
    if coverage is None:
        return Confidence.LOW
    cap_inputs = basis["cap_inputs"]
    weights = rules.class_weights
    steps = 0
    supported = [r for r in reqs if r["outcome"] in ("covered", "adjacent")]
    claimed = sum((weights[r["class"]] for r in supported if r["status_floor"] == "claimed"), Fraction(0))
    if _share(claimed, sum((weights[r["class"]] for r in supported), Fraction(0))) > rules.claimed_share:
        steps += 1
    weighty = [r for r in reqs if r["class"] in ("must", "strong")]
    unverifiable = _share(sum((weights[r["class"]] for r in weighty if r["outcome"] == "unverifiable"), Fraction(0)),
                          sum((weights[r["class"]] for r in weighty), Fraction(0)))
    if unverifiable > rules.unverifiable_share_severe:
        steps += 2
    elif unverifiable > rules.unverifiable_share:
        steps += 1
    need, floor = cap_inputs["need"], cap_inputs["floor"]
    need_is_no = need["verification"] == "verified" and need["value"] == "no"
    if v2:
        pay_unknown = cap_inputs["compensation"]["status"] != "effective"
    else:
        pay_unknown = cap_inputs["range"] is None
    if (cap_inputs["listing"] == "unknown" and not need_is_no) or (pay_unknown and floor["verification"] == "verified"):
        steps += 1
    if basis["requirement_count"] < rules.min_requirements or not any(r["class"] == "must" for r in reqs):
        steps += 1
    if fit is None:
        steps += 1
    return _CONFIDENCE[max(0, len(_CONFIDENCE) - 1 - steps)]


# --- action items -------------------------------------------------------------------------------

def _unverified_input(ref: str, field: dict, no_value: str, unreadable: str, no_market: str,
                      incomplete: str | None = None) -> ActionItem:
    """The cause-specific item for an unverified input, from the stored `row_state` and `value` only."""
    if field["row_state"] == "no_market":
        return ActionItem(ref, no_market)
    if field["row_state"] == "unreadable":
        return ActionItem(ref, unreadable)
    if incomplete:
        return ActionItem(ref, incomplete)
    return ActionItem(ref, no_value.format(cell=field["source_cell"]))     # a readable row, an empty value


def _pay_items_v2(comp: dict) -> list[ActionItem]:
    items: list[ActionItem] = []
    if comp["status"] == "none" and not comp["observations"]:
        items.append(ActionItem("pay", ITEM_RANGE_UNDISCLOSED))
    for n in comp["notes"]:
        if n["code"] == "estimate_only":
            items.append(ActionItem("pay", ITEM_PAY_ESTIMATE_ONLY.format(kind=n["detail"])))
        elif n["code"] == "conflict":
            items.append(ActionItem("pay", ITEM_PAY_CONFLICT.format(kind=n["detail"])))
        else:
            items.append(ActionItem("pay", ITEM_PAY_INCOMPARABLE.format(ref=n["ref"], reason=n["detail"])))
    if comp["status"] == "effective" and comp["kind"] != "estimate" and not comp["kind_confident"]:
        items.append(ActionItem("pay", ITEM_PAY_LOW_CONFIDENCE.format(kind=comp["kind"])))
    return items


def _action_items(reqs: list[dict], cap_inputs: dict, caps: tuple[Cap, ...], v2: bool = False) -> tuple[ActionItem, ...]:
    items: list[ActionItem] = []
    items += [ActionItem(r["id"], ITEM_UNVERIFIABLE) for r in reqs if r["outcome"] == "unverifiable"]
    items += [ActionItem(r["id"], ITEM_MUST_GAP) for r in reqs if r["class"] == "must" and r["outcome"] == "gap"]
    items += [ActionItem(r["id"], ITEM_CLAIMED.format(facts=", ".join(sorted(r["facts"]))))
              for r in reqs if r["outcome"] in ("covered", "adjacent") and r["status_floor"] == "claimed"]
    need, floor, rng = cap_inputs["need"], cap_inputs["floor"], None if v2 else cap_inputs["range"]
    if need["verification"] == "conflict":
        items.append(ActionItem("sponsorship", ITEM_NEED_CONFLICT.format(cell=need["source_cell"])))
    elif need["verification"] == "unverified":
        items.append(_unverified_input("sponsorship", need, ITEM_NEED_NO_VALUE,
                                       ITEM_NEED_UNREADABLE, ITEM_NEED_NO_MARKET))
    if cap_inputs["listing"] == "unknown" and not (need["verification"] == "verified" and need["value"] == "no"):
        items.append(ActionItem("sponsorship", ITEM_LISTING_UNKNOWN))
    if floor["verification"] == "conflict":
        items.append(ActionItem("pay", ITEM_FLOOR_CONFLICT.format(cell=floor["source_cell"])))
    elif floor["verification"] == "unverified":
        items.append(_unverified_input("pay", floor, ITEM_FLOOR_NO_VALUE, ITEM_FLOOR_UNREADABLE, ITEM_FLOOR_NO_MARKET,
                                       ITEM_FLOOR_INCOMPLETE if (floor["amount"] is None) != (floor["currency"] is None) else None))
    if v2:
        items += _pay_items_v2(cap_inputs["compensation"])
    elif rng is None:
        items.append(ActionItem("pay", ITEM_RANGE_UNDISCLOSED))
    elif floor["verification"] == "verified" and not any(c.code == CAP_COMPENSATION for c in caps):
        if rng["currency"] != floor["currency"]:
            items.append(ActionItem("pay", ITEM_CURRENCY_MISMATCH.format(
                range=rng["currency"] or "unstated", floor=floor["currency"] or "unstated")))
        if rng["basis"] != floor["basis"]:
            items.append(ActionItem("pay", ITEM_BASIS_MISMATCH.format(
                range=rng["basis"] or "unstated", floor=floor["basis"] or "unstated")))
        if cap_inputs["confidence"] not in _ESTABLISHED_CONFIDENCE:
            items.append(ActionItem("pay", ITEM_LOW_CONFIDENCE.format(confidence=cap_inputs["confidence"] or "unstated")))
    return tuple(items)


def score(basis: dict) -> ScoreResult:
    """Score a stored basis with the rules version it declares.

    `UnsupportedVersion` for an unknown `basis_version` or `rules_version`; `ValueError("malformed basis: ...")` for
    every other defect. Callers catch `UnsupportedVersion` first (cannot verify, EVL010), then `ValueError` (EVL007).
    """
    try:
        return _score(basis)
    except (KeyError, TypeError, AttributeError, IndexError) as exc:
        raise ValueError(f"malformed basis: {exc!r}") from exc


def _score(basis: dict) -> ScoreResult:
    if not isinstance(basis, dict) or "basis_version" not in basis or "rules_version" not in basis:
        _bad("a mapping with basis_version and rules_version is required")
    schema = basis_schema_for(basis["basis_version"])
    rules = rules_for(basis["rules_version"])
    if basis["basis_version"] in _PAIRED and basis["rules_version"] in _PAIRED and basis["basis_version"] != basis["rules_version"]:
        _bad(f"basis_version {basis['basis_version']!r} and rules_version {basis['rules_version']!r} move together")
    _validate(basis, schema.keys, rules)
    reqs = _requirements(basis)
    coverage, fit = _coverage(reqs, rules), _fit(basis, rules)
    v2 = basis["basis_version"] == "2"
    caps = _caps(reqs, basis["cap_inputs"], v2)
    if coverage is None:
        combined, tier = None, Tier.UNRATED
    else:
        combined = coverage if fit is None else rules.coverage_share * coverage + rules.fit_share * fit
        tier = _lowest(tier_for(combined, rules), [c.ceiling for c in caps])
    return ScoreResult(coverage, fit, combined, tier, caps,
                       _confidence(basis, reqs, rules, coverage, fit, v2),
                       _action_items(reqs, basis["cap_inputs"], caps, v2))
