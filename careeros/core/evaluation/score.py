"""The pure scorer (spec section 6 and 16.1): coverage, fit, combined score, tier, caps, confidence, action items.

`score(basis, rules)` is a pure function of a stored basis (`result.basis`, layout `BasisSchema` v1) and the `Rules` of
the version that basis declares: no memory, profile, clock or filesystem, so history can be recomputed from a stored
record. All arithmetic is on exact `Fraction`s and every comparison against a threshold uses the exact value; rounding
exists only in `stored_score`, which no decision reads. A basis holds integers, strings, booleans and nulls only.

Basis v1 keys read here (Task B's `Basis.complete` writes them):

* `requirements`: `[{id, class, outcome, facts, status_floor}]`; `requirement_count` (all of them, including
  unverifiable ones); `rubric_state` (`ok | missing | invalid`);
* `fit`: `[{row, weight, judgement}]` (integer weights; `row` is the normalised rubric label, audit only);
* `cap_inputs`: `{market, need: {value, verification, source}, listing, quote, range: {low, high, currency, basis} | null,
  floor: {amount, currency, basis, verification, source} | null, confidence, as_of}`.

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
  basis mismatch, low source confidence). A need verified as `no` produces no sponsorship item.
"""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction

from careeros.core.evaluation.canon import fixed4
from careeros.core.evaluation.model import ActionItem, Cap, Confidence, Tier
from careeros.core.evaluation.rules import RULES, RULES_VERSION, Rules, basis_schema_for

CAP_SPONSORSHIP = "sponsorship"
CAP_COMPENSATION = "compensation"
CAP_MUST_GAPS = "must_gaps"

_ORDER = (Tier.SKIP, Tier.WEAK, Tier.STRETCH, Tier.PURSUE)
_CONFIDENCE = (Confidence.LOW, Confidence.MEDIUM, Confidence.HIGH)
_OUTCOMES = ("covered", "adjacent", "gap", "unverifiable")
_ESTABLISHED_CONFIDENCE = ("high", "medium")


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


def _requirements(basis: dict, rules: Rules) -> list[dict]:
    out = []
    for r in sorted(basis["requirements"], key=lambda r: r["id"]):
        if r["class"] not in rules.class_weights:
            raise ValueError(f"requirement {r['id']!r}: unknown class {r['class']!r}")
        if r["outcome"] not in _OUTCOMES:
            raise ValueError(f"requirement {r['id']!r}: unknown outcome {r['outcome']!r}")
        out.append(r)
    return out


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
        weight, judgement = row["weight"], row["judgement"]
        if type(weight) is not int or weight <= 0:
            raise ValueError(f"fit row {row.get('row')!r}: weight {weight!r} is not a positive integer")
        if judgement not in rules.fit_credits:
            raise ValueError(f"fit row {row.get('row')!r}: unknown judgement {judgement!r}")
        total += weight
        got += rules.fit_credits[judgement] * weight
    return got / total


def _compensation_cap(cap_inputs: dict) -> Cap | None:
    rng, floor = cap_inputs["range"], cap_inputs["floor"]
    if rng is None or floor is None or floor["verification"] != "verified":
        return None
    high, amount = rng["high"], floor["amount"]
    if type(high) is not int or type(amount) is not int or high >= amount:
        return None
    if rng["currency"] is None or rng["currency"] != floor["currency"]:
        return None
    if rng["basis"] is None or rng["basis"] != floor["basis"]:
        return None
    if cap_inputs["confidence"] not in _ESTABLISHED_CONFIDENCE:
        return None
    return Cap(CAP_COMPENSATION, Tier.WEAK,
               f"the posted range tops out at {high} {rng['currency']} ({rng['basis']}), "
               f"below the verified floor of {amount} {floor['currency']} ({floor['basis']})")


def _sponsorship_cap(cap_inputs: dict) -> Cap | None:
    need = cap_inputs["need"]
    if (need["verification"] == "verified" and need["value"] == "yes"
            and cap_inputs["listing"] == "not_offered" and cap_inputs["quote"] is True):
        return Cap(CAP_SPONSORSHIP, Tier.WEAK,
                   "sponsorship is needed (verified against the Markets row) and the posting does not offer it")
    return None


def _caps(reqs: list[dict], cap_inputs: dict) -> tuple[Cap, ...]:
    caps = [_sponsorship_cap(cap_inputs), _compensation_cap(cap_inputs)]
    gaps = [r["id"] for r in reqs if r["class"] == "must" and r["outcome"] == "gap"]
    if len(gaps) >= 2:
        caps.append(Cap(CAP_MUST_GAPS, Tier.STRETCH, f"{len(gaps)} must requirements are gaps ({', '.join(gaps)})"))
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
    if (cap_inputs["listing"] == "unknown" and not need_is_no) or (
            range_null and floor is not None and floor["verification"] == "verified"):
        steps += 1
    if basis["requirement_count"] < rules.min_requirements or not any(r["class"] == "must" for r in reqs):
        steps += 1
    if fit is None:
        steps += 1
    return _CONFIDENCE[max(0, len(_CONFIDENCE) - 1 - steps)]


def _quoted(source: str | None) -> str:
    return f'cell "{source}"' if source is not None else "no market named"


def _action_items(reqs: list[dict], cap_inputs: dict, caps: tuple[Cap, ...]) -> tuple[ActionItem, ...]:
    items: list[ActionItem] = []
    for r in reqs:
        if r["outcome"] == "unverifiable":
            items.append(ActionItem(r["id"], "cannot be derived from memory; ask the user, "
                                             "and add the fact with `careeros memory add` if true"))
    for r in reqs:
        if r["class"] == "must" and r["outcome"] == "gap":
            items.append(ActionItem(r["id"], "must requirement is not met by memory; ask the user whether they have it, "
                                             "and add the fact with `careeros memory add` if true"))
    for r in reqs:
        if r["outcome"] in ("covered", "adjacent") and r["status_floor"] == "claimed":
            items.append(ActionItem(r["id"], f"supported only by claimed facts ({', '.join(r['facts'])}); "
                                             "ask the user to confirm them"))
    need = cap_inputs["need"]
    if need["verification"] == "conflict":
        items.append(ActionItem("sponsorship", "resolve the profile/agent mismatch for the need "
                                               f"(the Markets row says \"{need['source']}\")"))
    elif need["verification"] == "unverified" and need["value"] is not None:
        items.append(ActionItem("sponsorship", f"the need is not verified by the profile Markets row ({_quoted(need['source'])}); "
                                               "state it as \"required\" or \"not needed\" there"))
    if cap_inputs["listing"] == "unknown" and not (need["verification"] == "verified" and need["value"] == "no"):
        items.append(ActionItem("sponsorship", "not mentioned in the posting; ask about sponsorship before investing time"))
    rng, floor = cap_inputs["range"], cap_inputs["floor"]
    if floor is not None and floor["verification"] == "conflict":
        items.append(ActionItem("pay", "resolve the profile/agent mismatch for the floor "
                                       f"(the Markets row says \"{floor['source']}\")"))
    elif floor is not None and floor["verification"] == "unverified":
        items.append(ActionItem("pay", f"the floor is not verified by the profile Markets row ({_quoted(floor['source'])}); "
                                       "state a single base amount with a currency there"))
    if rng is None:
        items.append(ActionItem("pay", "not disclosed in the posting; ask the recruiter"))
    elif floor is not None and floor["verification"] == "verified" and not any(c.code == CAP_COMPENSATION for c in caps):
        if rng["currency"] != floor["currency"]:
            items.append(ActionItem("pay", f"range currency {rng['currency'] or 'unstated'} differs from floor currency "
                                           f"{floor['currency']}; compare in one currency before deciding"))
        if rng["basis"] != floor["basis"]:
            items.append(ActionItem("pay", f"range basis {rng['basis'] or 'unstated'} differs from floor basis "
                                           f"{floor['basis']}; ask the recruiter for the base figure"))
        if cap_inputs["confidence"] not in _ESTABLISHED_CONFIDENCE:
            items.append(ActionItem("pay", f"the range source confidence is {cap_inputs['confidence'] or 'unstated'}; "
                                           "confirm the figure with the recruiter"))
    return tuple(items)


def score(basis: dict, rules: Rules) -> ScoreResult:
    """Score a stored basis under `rules`. `ValueError` for a malformed basis or a rules/basis version mismatch,
    `UnsupportedVersion` for an unknown basis version."""
    schema = basis_schema_for(basis.get("basis_version"))
    missing = [k for k in schema.keys if k not in basis]
    if missing:
        raise ValueError(f"basis lacks {', '.join(missing)}")
    if basis["rules_version"] != rules.version:
        raise ValueError(f"basis declares rules version {basis['rules_version']!r}, scored with {rules.version!r}")
    reqs = _requirements(basis, rules)
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
