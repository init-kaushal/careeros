"""The v2 scorer (basis version 2): compensation cap, confidence and action items from the stored `compensation` object.

Invented data. The parity grid compares the v1 scorer on a legacy `cap_inputs` with the v2 scorer on the equivalent object
derived by `careeros.core.compensation.derive` for a single `posted`/`recruiter` figure.
"""

from __future__ import annotations

import copy
import itertools
from datetime import date
from fractions import Fraction as F

import pytest
from test_evaluation_score import SOLID_FIT, comp as comp_v1, floor_in, no_floor, rq, solid

from careeros.core.compensation.derive import FloorInfo, derive_compensation, to_basis
from careeros.core.compensation.model import CompRecord, Conversion, Observation
from careeros.core.evaluation import canon, score
from careeros.core.evaluation.model import MarketBasis, Tier
from careeros.core.evaluation.rules import BASIS_VERSIONS, RULES, RulesV1, RulesV2, UnsupportedVersion, rules_for

D0 = date(2026, 6, 1)
FLOOR_AMOUNT = 3_500_000


def obs(id="a", kind="posted", low=2_000_000, high=3_000_000, currency="INR", basis=MarketBasis.BASE, period="year",
        conf="medium", as_of=D0):
    return Observation(id, kind, low, high, currency, basis, period, "jd", None, as_of, conf)


def derived(*observations, floor=FloorInfo(FLOOR_AMOUNT, "INR", "base", True), conversions=()):
    return to_basis(derive_compensation(CompRecord("Atlantis", tuple(observations), tuple(conversions), ""), floor))


def caps2(compensation, floor=None, **over):
    base = {
        "market": "Atlantis",
        "need": {"value": "no", "verification": "verified", "source_cell": "not needed", "row_state": "parseable"},
        "listing": "unknown",
        "quote": False,
        "floor": floor if floor is not None else floor_in(FLOOR_AMOUNT, source_cell="INR 3,500,000"),
        "compensation": compensation,
    }
    base.update(over)
    return base


def basis2(cap, reqs=None, fit=SOLID_FIT):
    reqs = list(reqs or solid())
    return {"basis_version": "2", "rules_version": "2", "requirement_count": len(reqs), "rubric_state": "ok",
            "requirements": reqs, "fit": list(fit), "cap_inputs": cap}


def run2(compensation, floor=None, **over):
    return score.score(basis2(caps2(compensation, floor, **over)))


def texts(result):
    return [score.action_item_text(a) for a in result.action_items]


def codes(result):
    return [c.code for c in result.caps]


# --- registries -------------------------------------------------------------------------------------

def test_rules_v2_carries_every_v1_constant_plus_the_fx_window() -> None:
    v1, v2 = rules_for("1"), rules_for("2")
    assert isinstance(v2, RulesV2) and v2.version == "2" and v2.fx_window_days == 30
    for name in ("coverage_share", "fit_share", "tier_pursue", "tier_stretch", "tier_weak", "class_weights", "credits",
                 "fit_weights", "fit_credits", "claimed_share", "unverifiable_share", "unverifiable_share_severe",
                 "min_requirements"):
        assert getattr(v2, name) == getattr(v1, name)
    assert type(v1) is RulesV1 and not hasattr(v1, "fx_window_days")
    assert BASIS_VERSIONS["2"].version == "2" and BASIS_VERSIONS["2"].keys == BASIS_VERSIONS["1"].keys


def test_new_evaluations_still_default_to_version_1_until_the_wiring() -> None:
    from careeros.core.evaluation import rules
    assert (rules.RULES_VERSION, rules.BASIS_VERSION) == ("1", "1") and set(RULES) == {"1", "2"}


# --- parity grid -----------------------------------------------------------------------------------

def _v1(high, amount, cur, basis, conf, verified):
    if verified:
        cap = comp_v1(high=high, amount=amount, range_cur=cur, range_basis=basis, confidence=conf)
    else:
        cap = comp_v1(high=high, amount=None, floor_cur=None, floor_basis=None, range_cur=cur, range_basis=basis,
                      confidence=conf, verification="unverified", source_cell="TBD", row_state="unreadable")
    b = {"basis_version": "1", "rules_version": "1", "requirement_count": 3, "rubric_state": "ok",
         "requirements": solid(), "fit": SOLID_FIT, "cap_inputs": cap}
    return score.score(b)


def _v2(high, amount, cur, basis, conf, verified):
    o = obs(high=high, currency=cur, basis=MarketBasis(basis), conf=conf)
    if verified:
        return run2(derived(o, floor=FloorInfo(amount, "INR", "base", True)), floor_in(amount, source_cell="INR 3,500,000"))
    return run2(derived(o, floor=FloorInfo(None, None, None, False)), no_floor("unverified", "TBD", "unreadable"))


GRID = list(itertools.product(
    (FLOOR_AMOUNT - 1, FLOOR_AMOUNT, FLOOR_AMOUNT + 1), ("INR", "USD"), ("base", "total"),
    ("high", "medium", "low"), (True, False)))


def test_parity_grid_has_no_mismatch() -> None:
    mismatches = []
    for high, cur, basis, conf, verified in GRID:
        a, b = _v1(high, FLOOR_AMOUNT, cur, basis, conf, verified), _v2(high, FLOOR_AMOUNT, cur, basis, conf, verified)
        if (a.caps, a.tier) != (b.caps, b.tier):
            mismatches.append((high, cur, basis, conf, verified, a.caps, b.caps))
    assert not mismatches
    capped = [g for g in GRID if _v2(g[0], FLOOR_AMOUNT, *g[1:]).caps]
    assert capped and len(capped) < len(GRID)                       # the grid exercises both outcomes


def test_cap_reason_equals_the_v1_text_for_a_posted_figure() -> None:
    cap = run2(derived(obs(high=3_000_000))).caps[0]
    assert cap.code == "compensation" and cap.ceiling is Tier.WEAK
    assert cap.reason == score.REASON_COMPENSATION.format(high=3_000_000, amount=FLOOR_AMOUNT, currency="INR", basis="base")
    assert cap.reason == score.REASON_COMPENSATION_V2.format(kind="posted", high=3_000_000, amount=FLOOR_AMOUNT,
                                                             currency="INR", basis="base")


def test_cap_reason_names_the_kind_and_renders_converted_highs_as_money() -> None:
    usd = obs(low=30_000, high=40_000, currency="USD", kind="recruiter")
    rate = Conversion("USD", "INR", F("83.333"), "rbi", D0)
    result = run2(derived(usd, conversions=[rate]))
    assert codes(result) == ["compensation"]
    # 40000 * 83.333 = 3333320 exactly, so a whole amount renders plain
    assert result.caps[0].reason == ("the recruiter range tops out at 3333320 INR (base), "
                                     "below the verified floor of 3500000 INR (base)")
    odd = obs(low=1, high=40_000, currency="USD")
    result = run2(derived(odd, conversions=[Conversion("USD", "INR", F(1, 3), "rbi", D0)]))
    assert "tops out at 13333.33 INR" in result.caps[0].reason       # 40000/3, half-up to two decimals
    rounding = obs(low=1, high=3, currency="USD")
    result = run2(derived(rounding, conversions=[Conversion("USD", "INR", F(1, 3) + F(1, 200), "rbi", D0)]))
    assert "tops out at 1.02 INR" in result.caps[0].reason


# --- spec 5.3 cases ----------------------------------------------------------------------------------

def test_equality_does_not_cap() -> None:
    assert codes(run2(derived(obs(high=FLOOR_AMOUNT)))) == []
    assert codes(run2(derived(obs(high=FLOOR_AMOUNT - 1)))) == ["compensation"]


def test_null_high_never_caps() -> None:
    assert codes(run2(derived(obs(low=2_000_000, high=None)))) == []


def test_estimate_only_never_caps_and_adds_the_item() -> None:
    result = run2(derived(obs(kind="estimate", low=500_000, high=1_000_000)))
    assert codes(result) == []
    assert score.ITEM_PAY_ESTIMATE_ONLY.format(kind="estimate") in [a.message for a in result.action_items]
    assert ("pay", score.ITEM_PAY_ESTIMATE_ONLY.format(kind="estimate")) in [(a.ref, a.message) for a in result.action_items]


def test_any_low_confidence_observation_prevents_the_cap_and_is_reported() -> None:
    result = run2(derived(obs("a", high=3_000_000, conf="high"), obs("b", high=3_100_000, conf="low")))
    assert codes(result) == []
    assert "pay: " + score.ITEM_PAY_LOW_CONFIDENCE.format(kind="posted") in texts(result)


def test_conflict_prevents_the_cap_and_adds_the_conflict_item() -> None:
    result = run2(derived(obs("a", low=1, high=2), obs("b", low=3, high=4)))
    assert codes(result) == []
    assert "pay: " + score.ITEM_PAY_CONFLICT.format(kind="posted") in texts(result)


def test_unverified_floor_prevents_the_cap() -> None:
    result = run2(derived(obs(high=1), floor=FloorInfo(FLOOR_AMOUNT, "INR", "base", False)),
                  floor_in(FLOOR_AMOUNT, verification="unverified", source_cell="INR 3,500,000", row_state="unreadable"))
    assert codes(result) == []
    assert any("FLOOR_UNVERIFIED" in t for t in texts(result))


def test_an_incomparable_observation_in_the_effective_kind_prevents_the_cap() -> None:
    result = run2(derived(obs("a", high=3_000_000), obs("b", high=3_100_000, currency="USD")))
    assert codes(result) == []
    assert "pay: " + score.ITEM_PAY_INCOMPARABLE.format(ref="b", reason="FX_CONVERSION_MISSING") in texts(result)


def test_status_none_with_no_observations_says_undisclosed() -> None:
    result = run2(derived())
    assert "pay: " + score.ITEM_RANGE_UNDISCLOSED in texts(result)
    assert score.ITEM_RANGE_UNDISCLOSED == "not disclosed in the posting; ask the recruiter"


def test_status_none_with_a_verified_floor_lowers_confidence_exactly_like_range_null() -> None:
    effective = run2(derived(obs(high=FLOOR_AMOUNT + 1)))
    none = run2(derived())
    assert (effective.confidence.value, none.confidence.value) == ("high", "medium")
    v1_null = score.score({"basis_version": "1", "rules_version": "1", "requirement_count": 3, "rubric_state": "ok",
                           "requirements": solid(), "fit": SOLID_FIT,
                           "cap_inputs": {**comp_v1(), "range": None}})
    assert v1_null.confidence == none.confidence
    # an unverified floor does not trigger the unknown step
    unverified = run2(derived(floor=FloorInfo(None, None, None, False)), no_floor("unverified", "TBD", "unreadable"))
    assert unverified.confidence.value == "high"


def test_conflict_with_a_verified_floor_also_counts_as_unknown() -> None:
    assert run2(derived(obs("a", low=1, high=2), obs("b", low=3, high=4))).confidence.value == "medium"


def test_effective_low_confidence_estimate_gets_only_the_estimate_item() -> None:
    result = run2(derived(obs(kind="estimate", low=1, high=2, conf="low")))
    pay = [a.message for a in result.action_items if a.ref == "pay"]
    assert pay == ["pay is estimated only (estimate); ask the recruiter for a range"]


def test_only_incomparable_observations_do_not_say_undisclosed() -> None:
    result = run2(derived(obs("a", currency="USD"), obs("b", currency="EUR")))
    assert result.confidence.value == "medium"
    pay = [a.message for a in result.action_items if a.ref == "pay"]
    assert pay == [
        "pay observation a cannot be compared with the floor (FX_CONVERSION_MISSING); resolve it before relying on pay",
        "pay observation b cannot be compared with the floor (FX_CONVERSION_MISSING); resolve it before relying on pay",
    ]
    assert [a.message for a in run2(derived()).action_items if a.ref == "pay"] == [
        "not disclosed in the posting; ask the recruiter"]


def test_the_pay_texts_are_pinned_literally() -> None:
    assert score.ITEM_PAY_ESTIMATE_ONLY == "pay is estimated only ({kind}); ask the recruiter for a range"
    assert score.ITEM_PAY_CONFLICT == "resolve conflicting {kind} pay figures"
    assert score.ITEM_PAY_INCOMPARABLE == ("pay observation {ref} cannot be compared with the floor ({reason}); "
                                           "resolve it before relying on pay")
    assert score.ITEM_PAY_LOW_CONFIDENCE == "a {kind} pay figure has low source confidence; confirm it with the recruiter"
    assert score.REASON_COMPENSATION_V2 == ("the {kind} range tops out at {high} {currency} ({basis}), "
                                            "below the verified floor of {amount} {currency} ({basis})")
    assert texts(run2(derived(obs("a", low=1, high=2), obs("b", low=3, high=4)))) [-1] == (
        "pay: resolve conflicting posted pay figures")
    assert "pay: a posted pay figure has low source confidence; confirm it with the recruiter" in texts(
        run2(derived(obs(high=FLOOR_AMOUNT + 5, conf="low"))))
    assert run2(derived(obs(kind="recruiter", high=3_000_000))).caps[0].reason == (
        "the recruiter range tops out at 3000000 INR (base), below the verified floor of 3500000 INR (base)")


def test_floor_items_and_pay_items_come_after_the_sponsorship_items_in_order() -> None:
    result = run2(derived(), no_floor("unverified", None, "no_market"), listing="unknown",
                  need={"value": None, "verification": "unverified", "source_cell": None, "row_state": "no_market"})
    refs = [a.ref for a in result.action_items]
    assert refs.index("sponsorship") < refs.index("pay")
    assert texts(result)[-2:] == ["pay: " + score.ITEM_FLOOR_NO_MARKET, "pay: " + score.ITEM_RANGE_UNDISCLOSED]


# --- stored numbers ------------------------------------------------------------------------------------

def test_stored_high_and_low_round_trip_exactly_and_hold_no_floats() -> None:
    rate = Conversion("USD", "INR", F(1, 3), "rbi", D0)
    comp = derived(obs(low=1, high=40_000, currency="USD"), conversions=[rate])
    assert comp["high"] == "40000/3" and comp["low"] == "1/3"
    assert canon.parse_exact(comp["high"]) == F(40_000, 3)
    canon.canonical_json(basis2(caps2(comp)))                              # no float anywhere
    assert derived(obs(high=3_000_000))["high"] == 3_000_000               # whole numbers are ints


# --- validation ------------------------------------------------------------------------------------------

def bad(mutate):
    cap = caps2(derived(obs(), obs("b", kind="recruiter", currency="USD")))
    mutate(cap)
    return basis2(cap)


MUTATIONS = {
    "comp key missing": lambda c: c["compensation"].pop("notes"),
    "comp key extra": lambda c: c["compensation"].update(extra=1),
    "legacy range key": lambda c: c.update(range=None),
    "legacy confidence key": lambda c: c.update(confidence="high"),
    "status": lambda c: c["compensation"].update(status="maybe"),
    "kind": lambda c: c["compensation"].update(kind="rumour"),
    "kind none with effective status": lambda c: c["compensation"].update(kind=None),
    "high as decimal string": lambda c: c["compensation"].update(high="3000000"),
    "high as float": lambda c: c["compensation"].update(high=3000000.0),
    "high as bool": lambda c: c["compensation"].update(high=True),
    "high unreduced": lambda c: c["compensation"].update(high="4/2"),
    "low float": lambda c: c["compensation"].update(low=1.5),
    "kind_complete type": lambda c: c["compensation"].update(kind_complete=1),
    "kind_confident type": lambda c: c["compensation"].update(kind_confident="yes"),
    "observations not list": lambda c: c["compensation"].update(observations={}),
    "observation key": lambda c: c["compensation"]["observations"][0].pop("reason"),
    "observation kind": lambda c: c["compensation"]["observations"][0].update(kind="x"),
    "observation reason": lambda c: c["compensation"]["observations"][1].update(reason="WHY"),
    "observation comparable type": lambda c: c["compensation"]["observations"][0].update(comparable=0),
    "observation comparable with reason": lambda c: c["compensation"]["observations"][0].update(reason="FLOOR_UNVERIFIED"),
    "observation id": lambda c: c["compensation"]["observations"][0].update(id=1),
    "note code": lambda c: c["compensation"]["notes"][0].update(code="other"),
    "note ref": lambda c: c["compensation"]["notes"][0].update(ref=3),
    "note detail": lambda c: c["compensation"]["notes"][0].update(detail=None),
    "note key": lambda c: c["compensation"]["notes"][0].pop("detail"),
    "floor conflict": lambda c: c["floor"].update(verification="conflict"),
    "floor float amount": lambda c: c["floor"].update(amount=3.5e6),
    "floor basis unknown": lambda c: c["floor"].update(basis="gross"),
    "verified floor without basis": lambda c: c["floor"].update(basis=None),
    "need value": lambda c: c["need"].update(value="maybe"),
    "listing": lambda c: c.update(listing="sometimes"),
    "quote": lambda c: c.update(quote=1),
    "market": lambda c: c.update(market=3),
    "compensation missing": lambda c: c.pop("compensation"),
    "compensation not a mapping": lambda c: c.update(compensation=[]),
}


@pytest.mark.parametrize("name", sorted(MUTATIONS))
def test_malformed_v2_cap_inputs_are_a_value_error(name) -> None:
    with pytest.raises(ValueError, match="malformed basis"):
        score.score(bad(MUTATIONS[name]))


def test_the_unmutated_grid_basis_is_valid() -> None:
    assert score.score(bad(lambda c: None)).tier is not None


def test_unknown_versions_are_unsupported_before_any_layout_check() -> None:
    b = bad(lambda c: None)
    b["basis_version"] = "3"
    with pytest.raises(UnsupportedVersion):
        score.score(b)
    b["basis_version"], b["rules_version"] = "2", "9"
    b["cap_inputs"] = {"junk": 1}
    with pytest.raises(UnsupportedVersion):
        score.score(b)


def test_a_v1_layout_under_basis_2_and_a_v2_layout_under_basis_1_are_malformed() -> None:
    v2 = basis2(caps2(derived(obs())))
    v2["basis_version"] = "1"
    with pytest.raises(ValueError, match="malformed basis"):
        score.score(v2)
    v1 = {**basis2(caps2(derived(obs()))), "cap_inputs": comp_v1()}
    with pytest.raises(ValueError, match="malformed basis"):
        score.score(v1)


def test_mismatched_basis_and_rules_versions_are_malformed_in_both_directions() -> None:
    for basis_version, rules_version in (("2", "1"), ("1", "2")):
        b = basis2(caps2(derived(obs())))
        b["basis_version"], b["rules_version"] = basis_version, rules_version
        with pytest.raises(ValueError, match="malformed basis"):
            score.score(b)
    b = basis2(caps2(derived(obs())))
    b["rules_version"] = "9"                                                # unknown still wins, and is not ValueError
    with pytest.raises(UnsupportedVersion):
        score.score(b)
    b["rules_version"], b["basis_version"] = "1", "9"
    with pytest.raises(UnsupportedVersion):
        score.score(b)


def test_the_input_is_not_mutated() -> None:
    b = basis2(caps2(derived(obs())))
    before = copy.deepcopy(b)
    assert codes(score.score(b)) == ["compensation"] and b == before


def test_sponsorship_cap_still_works_under_v2() -> None:
    need = {"value": "yes", "verification": "verified", "source_cell": "required", "row_state": "parseable"}
    result = run2(derived(obs(high=FLOOR_AMOUNT + 1)), need=need, listing="not_offered", quote=True)
    assert codes(result) == ["sponsorship"]


# --- cross-field validation ---------------------------------------------------------------------------------

def two_incomparable():
    return derived(obs("a", high=3_000_000), obs("b", high=3_100_000, currency="USD"))


def ok(cap):
    assert score.score(basis2(cap)).tier is not None


def malformed(cap):
    with pytest.raises(ValueError, match="malformed basis"):
        score.score(basis2(cap))


def with_comp(comp, **changes):
    comp = copy.deepcopy(comp)
    comp.update(changes)
    return caps2(comp)


def test_cross_field_rules_each_have_a_positive_and_a_negative_case() -> None:
    eff = derived(obs("a", low=1, high=2))
    none_inc = derived(obs("a", currency="USD"))
    conflict = derived(obs("a", low=1, high=2), obs("b", low=3, high=4))
    est = derived(obs("a", kind="estimate", low=1, high=2))
    inc = two_incomparable()
    for good in (eff, none_inc, conflict, est, inc, derived()):
        ok(caps2(good))
    # effective needs a kind and an observation of it
    malformed(with_comp(eff, kind=None))
    malformed(with_comp(eff, observations=[]))
    malformed(with_comp(eff, kind="recruiter"))
    # none / conflict carry no range
    malformed(with_comp(none_inc, low=1))
    malformed(with_comp(none_inc, high=5))
    malformed(with_comp(conflict, high=5))
    # bounds
    ok(with_comp(eff, low=0, high=0))
    malformed(with_comp(eff, low=-1))
    malformed(with_comp(eff, high=-1))
    malformed(with_comp(eff, low=3, high=2))
    ok(with_comp(eff, low=2, high=2))
    ok(with_comp(eff, low=None, high=2))
    # unique ids
    dup = copy.deepcopy(inc)
    dup["observations"][1]["id"] = "a"
    malformed(caps2(dup))
    # incomparable notes
    for mutate in (lambda c: c["notes"][0].update(ref="zzz"), lambda c: c["notes"][0].update(ref="a"),
                   lambda c: c["notes"][0].update(detail="BASIS_MISMATCH"), lambda c: c["notes"].clear(),
                   lambda c: c["notes"].append(dict(c["notes"][0]))):
        c = copy.deepcopy(inc)
        mutate(c)
        malformed(caps2(c))
    # a comparable observation marked incomparable without a note / a note for a comparable one
    c = copy.deepcopy(eff)
    c["observations"][0].update(comparable=False, reason="BASIS_MISMATCH")
    malformed(caps2(c))
    # conflict / estimate_only notes
    for note in (dict(code="conflict", ref="a", detail="posted"), dict(code="conflict", ref=None, detail="recruiter")):
        c = copy.deepcopy(conflict)
        c["notes"] = [note]
        malformed(caps2(c))
    malformed(with_comp(conflict, notes=[]))
    malformed(with_comp(conflict, notes=conflict["notes"] * 2))
    malformed(with_comp(eff, notes=[dict(code="conflict", ref=None, detail="posted")]))
    malformed(with_comp(eff, notes=[dict(code="estimate_only", ref=None, detail="posted")]))
    malformed(with_comp(est, notes=[]))
    ok(with_comp(est, notes=est["notes"]))
    # kind_complete
    malformed(with_comp(eff, kind_complete=False))
    malformed(with_comp(inc, kind_complete=True))
    malformed(with_comp(derived(), kind_complete=True))
    ok(with_comp(inc, kind_complete=False))


def test_floor_cross_field_rules() -> None:
    comp = derived(obs())
    ok(caps2(comp, floor_in(FLOOR_AMOUNT, row_state="parseable")))
    malformed(caps2(comp, floor_in(FLOOR_AMOUNT, row_state="unreadable")))                   # verified needs a parseable row
    malformed(caps2(comp, floor_in(None, None, "base", "verified", "x", "parseable")))      # verified needs amount and currency
    malformed(caps2(comp, floor_in(FLOOR_AMOUNT, basis=None)))                               # ... and a basis
    ok(caps2(comp, floor_in(FLOOR_AMOUNT, verification="unverified", row_state="unreadable")))
    ok(caps2(comp, no_floor("unverified", None, "no_market")))
    ok(caps2(comp, floor_in(None, None, None, "unverified", "INR", "parseable")))            # incomplete value: allowed
    malformed(caps2(comp, floor_in(FLOOR_AMOUNT, verification="unverified", row_state="parseable")))


def test_v1_still_words_the_unreachable_v2_floor_items() -> None:
    assert score.ITEM_FLOOR_NO_VALUE and score.ITEM_FLOOR_INCOMPLETE


def _random_record(rng):
    kinds = ("posted", "recruiter", "estimate")
    obs_list = []
    for i in range(rng.randint(0, 4)):
        low = rng.choice((None, rng.randint(0, 40)))
        high = rng.choice((None, rng.randint(low or 0, 60)))
        obs_list.append(Observation(
            f"o{i}", rng.choice(kinds), low, high, rng.choice(("INR", "USD")), MarketBasis(rng.choice(("base", "total"))),
            rng.choice(("year", "month")), "jd", None, date(2026, 6, rng.randint(1, 28)), rng.choice(("high", "medium", "low"))))
    conv = ()
    if rng.random() < 0.6:
        conv = (Conversion("USD", "INR", F(rng.randint(1, 100), rng.randint(1, 7)), "rbi", date(2026, 6, rng.randint(1, 28))),)
    return obs_list, conv


def test_every_to_basis_output_over_random_records_passes_the_validator() -> None:
    import random
    rng = random.Random(20261014)
    statuses = set()
    for _ in range(1500):
        obs_list, conv = _random_record(rng)
        verified = rng.random() < 0.8
        info = FloorInfo(30, "INR", "base", True) if verified else FloorInfo(None, None, None, False)
        comp = derived(*obs_list, floor=info, conversions=conv)
        statuses.add(comp["status"])
        floor = floor_in(30, source_cell="INR 30") if verified else no_floor("unverified", "TBD", "unreadable")
        score.score(basis2(caps2(comp, floor)))
    assert statuses == {"effective", "none", "conflict"}
