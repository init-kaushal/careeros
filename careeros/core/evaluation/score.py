"""The pure scorer (spec section 6 and 16.1): coverage, fit, combined score, tier, caps, confidence, action items.

`score(basis, rules)` is a pure function of a stored basis (`result.basis`, layout `BasisSchema` v1) and the `Rules` of
the version that basis declares: no memory, profile, clock or filesystem, so history can be recomputed from a stored
record. All arithmetic is on exact `Fraction`s and every comparison against a threshold uses the exact value; rounding
exists only in `stored_score`, which no decision reads. A basis holds integers, strings, booleans and nulls only.

Basis v1 keys read here (Task B's `Basis.complete` writes them):

* `requirements`: `[{id, class, outcome, facts, status_floor}]`; `requirement_count` (all of them, including
  unverifiable ones); `rubric_state` (`ok | missing | invalid`);
* `fit`: `[{row, weight, judgement}]` (integer weights; `row` is the normalised rubric label, audit only);
* `cap_inputs`: `{market, need: {value, verification, source_cell}, listing, quote, range: {low, high, currency, basis} | null,
  floor: {amount, currency, basis, verification, source_cell}, confidence, as_of}`. `need` and `floor` are always present:
  an input the agent omitted has `value`/`amount` null and verification `unverified`, with the Markets cell kept in
  `source_cell` (null only when no market was named).

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
`ITEM_NEED_NO_MARKET`, `ITEM_LISTING_UNKNOWN`, `ITEM_FLOOR_CONFLICT`, `ITEM_FLOOR_NO_VALUE`, `ITEM_FLOOR_UNREADABLE`,
`ITEM_FLOOR_NO_MARKET`, `ITEM_RANGE_UNDISCLOSED`, `ITEM_CURRENCY_MISMATCH`, `ITEM_BASIS_MISMATCH`, `ITEM_LOW_CONFIDENCE`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from fractions import Fraction

from careeros.core.evaluation.canon import fixed4
from careeros.core.evaluation.markets import parse_floor, parse_need
from careeros.core.evaluation.model import ActionItem, Cap, Confidence, Tier
from careeros.core.evaluation.rules import RULES, RULES_VERSION, Rules, basis_schema_for, rules_for

CAP_SPONSORSHIP = "sponsorship"
CAP_COMPENSATION = "compensation"
CAP_MUST_GAPS = "must_gaps"

REASON_SPONSORSHIP = "sponsorship is needed (verified against the Markets row) and the posting does not offer it"
REASON_COMPENSATION = ("the posted range tops out at {high} {currency} ({basis}), "
                       "below the verified floor of {amount} {currency} ({basis})")
REASON_MUST_GAPS = "{n} must requirements are gaps ({ids})"

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
ITEM_FLOOR_UNREADABLE = "the Markets row is not machine-readable; clarify it in profile.md"
ITEM_FLOOR_NO_MARKET = "no market is named; name the profile Markets row so the floor can be checked"
ITEM_RANGE_UNDISCLOSED = "not disclosed in the posting; ask the recruiter"
ITEM_CURRENCY_MISMATCH = "range currency {range} differs from floor currency {floor}; compare in one currency before deciding"
ITEM_BASIS_MISMATCH = "range basis {range} differs from floor basis {floor}; ask the recruiter for the base figure"
ITEM_LOW_CONFIDENCE = "the range source confidence is {confidence}; confirm the figure with the recruiter"

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
    _keys(cap, ("market", "need", "listing", "quote", "range", "floor", "confidence", "as_of"), "cap_inputs")
    _str_or_none(cap["market"], "market")
    _str_or_none(cap["as_of"], "as_of")
    _one_of(cap["listing"], _LISTINGS, "listing")
    if type(cap["quote"]) is not bool:
        _bad(f"quote {cap['quote']!r} is not a boolean")
    _one_of(cap["confidence"], _CONFIDENCES, "compensation confidence")
    _keys(cap["need"], ("value", "verification", "source_cell"), "need")
    _one_of(cap["need"]["value"], ("yes", "no", None), "need value")
    _one_of(cap["need"]["verification"], _VERIFICATION, "need verification")
    _str_or_none(cap["need"]["source_cell"], "need source_cell")
    _keys(cap["floor"], ("amount", "currency", "basis", "verification", "source_cell"), "floor")
    _int_or_none(cap["floor"]["amount"], "floor amount")
    _str_or_none(cap["floor"]["currency"], "floor currency")
    _one_of(cap["floor"]["basis"], _BASES, "floor basis")
    _one_of(cap["floor"]["verification"], _VERIFICATION, "floor verification")
    _str_or_none(cap["floor"]["source_cell"], "floor source_cell")
    if cap["range"] is not None:
        _keys(cap["range"], ("low", "high", "currency", "basis"), "range")
        _int_or_none(cap["range"]["low"], "range low")
        if type(cap["range"]["high"]) is not int:
            _bad(f"range high {cap['range']['high']!r} is not an integer")
        _str_or_none(cap["range"]["currency"], "range currency")
        _one_of(cap["range"]["basis"], _BASES, "range basis")


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


def _sponsorship_cap(cap_inputs: dict) -> Cap | None:
    need = cap_inputs["need"]
    if (need["verification"] == "verified" and need["value"] == "yes"
            and cap_inputs["listing"] == "not_offered" and cap_inputs["quote"] is True):
        return Cap(CAP_SPONSORSHIP, Tier.WEAK, REASON_SPONSORSHIP)
    return None


def _caps(reqs: list[dict], cap_inputs: dict) -> tuple[Cap, ...]:
    caps = [_sponsorship_cap(cap_inputs), _compensation_cap(cap_inputs)]
    gaps = [r["id"] for r in reqs if r["class"] == "must" and r["outcome"] == "gap"]
    if len(gaps) >= 2:
        caps.append(Cap(CAP_MUST_GAPS, Tier.STRETCH, REASON_MUST_GAPS.format(n=len(gaps), ids=", ".join(gaps))))
    return tuple(c for c in caps if c is not None)


def _share(part: Fraction, whole: Fraction) -> Fraction:
    return part / whole if whole else Fraction(0)


def _confidence(basis: dict, reqs: list[dict], rules: Rules, coverage: Fraction | None, fit: Fraction | None) -> Confidence:
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
    range_null = cap_inputs["range"] is None
    if (cap_inputs["listing"] == "unknown" and not need_is_no) or (range_null and floor["verification"] == "verified"):
        steps += 1
    if basis["requirement_count"] < rules.min_requirements or not any(r["class"] == "must" for r in reqs):
        steps += 1
    if fit is None:
        steps += 1
    return _CONFIDENCE[max(0, len(_CONFIDENCE) - 1 - steps)]


# --- action items -------------------------------------------------------------------------------

def _unverified_input(ref: str, field: dict, value, parseable, no_value: str, unreadable: str, no_market: str) -> ActionItem:
    cell = field["source_cell"]
    if cell is None:
        return ActionItem(ref, no_market)
    if value is None and parseable(cell) is not None:
        return ActionItem(ref, no_value.format(cell=cell))
    return ActionItem(ref, unreadable)


def _action_items(reqs: list[dict], cap_inputs: dict, caps: tuple[Cap, ...]) -> tuple[ActionItem, ...]:
    items: list[ActionItem] = []
    items += [ActionItem(r["id"], ITEM_UNVERIFIABLE) for r in reqs if r["outcome"] == "unverifiable"]
    items += [ActionItem(r["id"], ITEM_MUST_GAP) for r in reqs if r["class"] == "must" and r["outcome"] == "gap"]
    items += [ActionItem(r["id"], ITEM_CLAIMED.format(facts=", ".join(sorted(r["facts"]))))
              for r in reqs if r["outcome"] in ("covered", "adjacent") and r["status_floor"] == "claimed"]
    need, floor, rng = cap_inputs["need"], cap_inputs["floor"], cap_inputs["range"]
    if need["verification"] == "conflict":
        items.append(ActionItem("sponsorship", ITEM_NEED_CONFLICT.format(cell=need["source_cell"])))
    elif need["verification"] == "unverified":
        items.append(_unverified_input("sponsorship", need, need["value"], parse_need, ITEM_NEED_NO_VALUE,
                                       ITEM_NEED_UNREADABLE, ITEM_NEED_NO_MARKET))
    if cap_inputs["listing"] == "unknown" and not (need["verification"] == "verified" and need["value"] == "no"):
        items.append(ActionItem("sponsorship", ITEM_LISTING_UNKNOWN))
    if floor["verification"] == "conflict":
        items.append(ActionItem("pay", ITEM_FLOOR_CONFLICT.format(cell=floor["source_cell"])))
    elif floor["verification"] == "unverified":
        items.append(_unverified_input("pay", floor, floor["amount"], parse_floor, ITEM_FLOOR_NO_VALUE,
                                       ITEM_FLOOR_UNREADABLE, ITEM_FLOOR_NO_MARKET))
    if rng is None:
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
    _validate(basis, schema.keys, rules)
    reqs = _requirements(basis)
    coverage, fit = _coverage(reqs, rules), _fit(basis, rules)
    caps = _caps(reqs, basis["cap_inputs"])
    if coverage is None:
        combined, tier = None, Tier.UNRATED
    else:
        combined = coverage if fit is None else rules.coverage_share * coverage + rules.fit_share * fit
        tier = _lowest(tier_for(combined, rules), [c.ceiling for c in caps])
    return ScoreResult(coverage, fit, combined, tier, caps,
                       _confidence(basis, reqs, rules, coverage, fit),
                       _action_items(reqs, basis["cap_inputs"], caps))
