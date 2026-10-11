"""Pure scorer (evaluation Task C, spec section 6 and 16.1). Invented data, hand-computed expectations."""

from __future__ import annotations

import ast
import copy
import dataclasses
import json
from fractions import Fraction as F
from pathlib import Path

import pytest

from careeros.core.evaluation import canon, markets, score
from careeros.core.evaluation.derive import Basis
from careeros.core.evaluation.model import Cap, Confidence, JobFacts, Listing, MarketBasis, SourceConfidence, Tier
from careeros.core.evaluation.rubric import FitRow, Rubric
from careeros.core.evaluation.rules import BASIS_VERSIONS, RULES, UnsupportedVersion

RULES1 = RULES["1"]


# --- builders ----------------------------------------------------------------------------------

def rq(rid, cls="must", outcome="covered", floor="verified", facts=("fct_a",)):
    covered = outcome in ("covered", "adjacent")
    return {"id": rid, "class": cls, "outcome": outcome, "facts": list(facts) if covered else [],
            "status_floor": floor if covered else None}


def floor_in(amount=1, currency="INR", basis="base", verification="verified", source_cell="INR 1", row_state="parseable"):
    return {"amount": amount, "currency": currency, "basis": basis, "verification": verification,
            "source_cell": source_cell, "row_state": row_state}


def no_floor(verification="unverified", source_cell=None, row_state=None):
    return floor_in(None, None, None, verification, source_cell,
                    row_state or ("no_market" if source_cell is None else "parseable"))


def caps_in(**over):
    """Neutral cap inputs: need verified `no`, listing unknown, a disclosed range, a verified floor below it: no cap, no item."""
    base = {
        "market": "Atlantis",
        "need": {"value": "no", "verification": "verified", "source_cell": "not needed", "row_state": "parseable"},
        "listing": "unknown",
        "quote": False,
        "range": {"low": 1, "high": 2, "currency": "INR", "basis": "base"},
        "floor": floor_in(),
        "confidence": "high",
        "as_of": "2026-09",
    }
    base.update(over)
    return base


def sponsor(value="yes", verification="verified", listing="not_offered", quote=True):
    return caps_in(need={"value": value, "verification": verification, "source_cell": "required", "row_state": "parseable"},
                   listing=listing, quote=quote)


def comp(high=3_000_000, amount=3_500_000, floor_cur="INR", range_cur="INR", floor_basis="base", range_basis="base",
         verification="verified", confidence="medium", source_cell="INR 3,500,000", row_state="parseable", **over):
    return caps_in(
        range={"low": 2_000_000, "high": high, "currency": range_cur, "basis": range_basis},
        floor=floor_in(amount, floor_cur, floor_basis, verification, source_cell, row_state),
        confidence=confidence, as_of="2026-09", **over)


def merge(*parts):
    out = caps_in()
    for part in parts:
        for key in ("range", "floor", "confidence", "as_of"):
            if part[key] is not None:
                out[key] = part[key]
        if part["listing"] != "unknown" or part["need"]["value"] != "no":
            out["need"], out["listing"], out["quote"] = part["need"], part["listing"], part["quote"]
    return out


def fit_row(label, weight, judgement):
    return {"row": label, "weight": weight, "judgement": judgement}


def basis(reqs, fit=None, rubric_state=None, cap=None, count=None):
    reqs = list(reqs)
    return {
        "basis_version": "1",
        "rules_version": "1",
        "requirement_count": len(reqs) if count is None else count,
        "rubric_state": rubric_state or ("ok" if fit else "missing"),
        "requirements": reqs,
        "fit": list(fit or []),
        "cap_inputs": cap or caps_in(),
    }


def run(*args, **kw):
    return score.score(basis(*args, **kw))


def coverage_only(credits_by_weight):
    """Weights 3,3,2,1,1 (=10): `covered` ids get full credit; the rest are gaps."""
    spec = [("r1", "must"), ("r2", "must"), ("r3", "strong"), ("r4", "nice"), ("r5", "nice")]
    return [rq(i, c, "covered" if i in credits_by_weight else "gap") for i, c in spec]


def texts(result):
    return [score.action_item_text(a) for a in result.action_items]


# a baseline that scores confidence `high`: 3 requirements incl. a must, nothing claimed, rubric ok, no unknowns
def solid():
    return [rq("r1", "must", "covered"), rq("r2", "strong", "covered", "confirmed"), rq("r3", "nice", "covered")]


SOLID_FIT = [fit_row("role", 3, "yes")]


def solid_conf(**kw):
    kw.setdefault("fit", SOLID_FIT)
    return run(kw.pop("reqs", solid()), **kw)


# --- spec worked example -------------------------------------------------------------------------

def worked_example():
    reqs = [rq("r1", "must", "covered", "claimed"), rq("r2", "must", "unverifiable"),
            rq("r3", "strong", "adjacent", "claimed", ("ach_h64tj5evkz",))]
    fit = [fit_row("role title", 3, "yes"), fit_row("location", 2, "partial")]
    return basis(reqs, fit)


def test_worked_example_numbers_tier_and_confidence():
    result = score.score(worked_example())
    assert result.coverage == F(4, 5) and result.fit == F(4, 5) and result.combined == F(4, 5)
    assert result.tier == Tier.PURSUE
    assert result.caps == ()
    # claimed-only share 5/5 > 1/2 costs one step; unverifiable 3/8 = 37.5% of must/strong weight costs one more
    assert result.confidence == Confidence.LOW


def test_worked_example_action_items_in_the_fixed_order():
    result = score.score(worked_example())
    assert texts(result) == [
        "r2: cannot be derived from memory; ask the user, and add the fact with `careeros memory add` if true",
        "r1: supported only by claimed facts (fct_a); ask the user to confirm them",
        "r3: supported only by claimed facts (ach_h64tj5evkz); ask the user to confirm them",
    ]
    assert [a.ref for a in result.action_items] == ["r2", "r1", "r3"]


def test_result_types_are_exact_fractions():
    result = score.score(worked_example())
    for value in (result.coverage, result.fit, result.combined):
        assert type(value) is F
    assert isinstance(result.tier, Tier) and isinstance(result.confidence, Confidence)


# --- tier boundaries: exact values --------------------------------------------------------------

@pytest.mark.parametrize("value,tier", [
    (F(7, 10), Tier.PURSUE), (F(69999, 100000), Tier.STRETCH), (F(1, 2), Tier.STRETCH),
    (F(49999, 100000), Tier.WEAK), (F(3, 10), Tier.WEAK), (F(29999, 100000), Tier.SKIP), (0, Tier.SKIP),
    (F(1), Tier.PURSUE),
])
def test_tier_for_boundaries(value, tier):
    assert score.tier_for(F(value)) == tier
    assert score.tier_for(F(value), RULES1) == tier


@pytest.mark.parametrize("covered,combined,tier", [
    ({"r1", "r2", "r4"}, F(7, 10), Tier.PURSUE),     # 3+3+1
    ({"r1", "r3"}, F(1, 2), Tier.STRETCH),           # 3+2
    ({"r1"}, F(3, 10), Tier.WEAK),                   # 3
])
def test_coverage_only_boundaries(covered, combined, tier):
    result = run(coverage_only(covered))
    assert result.fit is None
    assert result.coverage == combined and result.combined == combined
    assert result.tier == tier


def test_rounding_is_never_used_to_choose_the_tier():
    # 13999 of 20000 nice requirements covered: combined 0.69995 rounds (half up) to 0.7000, but is below 0.70
    reqs = [rq(f"r{i}", "nice", "covered" if i < 13999 else "gap") for i in range(20000)]
    result = run(reqs)
    assert result.combined == F(13999, 20000)
    assert canon.fixed4(result.combined) == "0.7000"
    assert result.tier == Tier.STRETCH


def test_combined_weights_coverage_and_fit_exactly():
    # coverage 1, fit 1/2 -> 0.65 + 0.35/2 = 0.825
    result = run([rq("r1")], [fit_row("a", 3, "partial")])
    assert (result.coverage, result.fit, result.combined) == (F(1), F(1, 2), F(33, 40))


def test_stored_score_is_the_fixed4_float():
    assert score.stored_score(None) is None
    assert score.stored_score(F(2, 3)) == 0.6667
    assert score.stored_score(F(13999, 20000)) == 0.7
    assert score.stored_score(F(0)) == 0.0 and score.stored_score(F(1)) == 1.0


# --- unrated -------------------------------------------------------------------------------------

def test_no_requirements_is_unrated_and_low():
    result = run([])
    assert result.coverage is None and result.combined is None
    assert result.tier == Tier.UNRATED and result.confidence == Confidence.LOW


def test_all_unverifiable_is_unrated_and_low():
    result = run([rq("r1", "must", "unverifiable"), rq("r2", "strong", "unverifiable"), rq("r3", "nice", "unverifiable")],
                 SOLID_FIT)
    assert result.coverage is None and result.combined is None
    assert result.fit == F(1)                      # fit is independent of coverage
    assert result.tier == Tier.UNRATED and result.confidence == Confidence.LOW
    assert len(result.action_items) == 3


# --- weights and credits -----------------------------------------------------------------------

@pytest.mark.parametrize("cls,weight", [("must", 3), ("strong", 2), ("nice", 1)])
def test_class_weights(cls, weight):
    # one covered item of the class against one covered nice item: coverage stays 1; against a gap nice: weight/(weight+1)
    result = run([rq("r1", cls, "covered"), rq("r2", "nice", "gap")])
    assert result.coverage == F(weight, weight + 1)


def test_credits_covered_adjacent_gap():
    result = run([rq("r1", "must", "covered"), rq("r2", "must", "adjacent"), rq("r3", "must", "gap")])
    assert result.coverage == F(3 + F(3, 2), 9)


def test_adjacent_is_half_credit_at_each_weight():
    for cls, weight in (("must", 3), ("strong", 2), ("nice", 1)):
        assert run([rq("r1", cls, "adjacent")]).coverage == F(1, 2)
        assert run([rq("r1", cls, "adjacent"), rq("r2", "nice", "covered")]).coverage == F(F(weight, 2) + 1, weight + 1)


def test_unverifiable_leaves_the_denominator():
    plain = [rq("r1", "must"), rq("r2", "strong", "gap")]
    extra = plain + [rq("r3", "must", "unverifiable")]
    assert run(plain).coverage == run(extra).coverage == F(3, 5)


# --- caps ----------------------------------------------------------------------------------------

def nine_tenths(cap, extra=()):
    reqs = [rq("r1"), rq("r2"), rq("r3", "strong"), rq("r4", "nice"), rq("r5", "nice", "gap"), *extra]
    return run(reqs, cap=cap)


def test_sponsorship_cap_alone():
    result = nine_tenths(sponsor())
    assert result.combined == F(9, 10) and result.tier == Tier.WEAK
    assert result.caps == (Cap("sponsorship", Tier.WEAK,
                               "sponsorship is needed (verified against the Markets row) and the posting does not offer it"),)


def test_no_caps_leaves_the_tier():
    result = nine_tenths(caps_in())
    assert result.tier == Tier.PURSUE and result.caps == ()


def test_compensation_cap_alone():
    result = nine_tenths(comp())
    assert result.tier == Tier.WEAK
    assert result.caps == (Cap("compensation", Tier.WEAK,
                               "the posted range tops out at 3000000 INR (base), below the verified floor of 3500000 INR (base)"),)


def test_compensation_at_the_floor_does_not_cap():
    assert nine_tenths(comp(high=3_500_000)).caps == ()


def many_nice(covered=20):
    return [rq(f"n{i}", "nice") for i in range(covered)]


def test_two_must_gaps_cap_at_stretch():
    result = run([rq("r1", "must", "gap"), rq("r2", "must", "gap"), *many_nice()])
    assert result.combined == F(20, 26) and F(7, 10) <= result.combined
    assert result.tier == Tier.STRETCH
    assert result.caps == (Cap("must_gaps", Tier.STRETCH, "2 must requirements are gaps (r1, r2)"),)


def test_one_must_gap_does_not_cap():
    result = run([rq("r1", "must", "gap"), *many_nice()])
    assert result.caps == () and result.tier == Tier.PURSUE


def test_all_three_caps_together_lowest_wins_and_all_are_listed():
    cap = merge(sponsor(), comp())
    result = run([rq("r1", "must", "gap"), rq("r2", "must", "gap"), *many_nice()], cap=cap)
    assert [c.code for c in result.caps] == ["sponsorship", "compensation", "must_gaps"]
    assert [c.ceiling for c in result.caps] == [Tier.WEAK, Tier.WEAK, Tier.STRETCH]
    assert result.tier == Tier.WEAK


def test_stretch_cap_alone_with_a_weak_cap_is_weak_and_stretch_alone_is_stretch():
    reqs = [rq("r1", "must", "gap"), rq("r2", "must", "gap"), *many_nice()]
    assert run(reqs, cap=sponsor()).tier == Tier.WEAK
    assert run(reqs).tier == Tier.STRETCH


def test_a_cap_never_raises_a_tier():
    result = run([rq("r1", "must", "gap"), rq("r2", "must", "gap"), rq("r3", "nice")])
    assert result.combined == F(1, 7) and result.tier == Tier.SKIP
    assert [c.code for c in result.caps] == ["must_gaps"]         # still listed, tier unchanged
    weak_zone = run([rq("r1", "must", "gap"), rq("r2", "must", "gap"), *many_nice(6)], cap=sponsor())
    assert weak_zone.combined == F(6, 12) and weak_zone.tier == Tier.WEAK   # stretch zone lowered to weak, not raised


# --- established versus unknown ------------------------------------------------------------------

SPONSOR_NO_CAP = {
    "listing unknown": (sponsor(listing="unknown"), "sponsorship: not mentioned in the posting; ask about sponsorship before investing time"),
    "listing offered": (sponsor(listing="offered"), None),
    "needed no": (sponsor(value="no", listing="not_offered"), None),
    "no quote": (sponsor(quote=False), None),
}


@pytest.mark.parametrize("name", list(SPONSOR_NO_CAP))
def test_sponsorship_not_established_never_caps(name):
    cap, item = SPONSOR_NO_CAP[name]
    result = nine_tenths(cap)
    assert result.caps == () and result.tier == Tier.PURSUE
    assert (item in texts(result)) if item else not any(t.startswith("sponsorship:") for t in texts(result))


def test_conflicting_need_never_caps_and_says_why():
    result = nine_tenths(sponsor(verification="conflict"))
    assert result.caps == () and result.tier == Tier.PURSUE
    assert 'sponsorship: resolve the profile/agent mismatch for the need (the Markets row says "required")' in texts(result)
    assert nine_tenths(sponsor(verification="verified")).caps != ()          # the same inputs, verified, cap


def need_unverified(value, cell, row_state="parseable"):
    return caps_in(need={"value": value, "verification": "unverified", "source_cell": cell, "row_state": row_state}, listing="not_offered", quote=True)


def test_unverified_need_is_always_reported_by_cause_and_never_caps():
    cases = [
        (need_unverified(None, "required"),
         "sponsorship: the Markets row says required but the evaluation gave no value; state it so the cap can be checked"),
        (need_unverified("yes", "preferred", "unreadable"), "sponsorship: the Markets row is not machine-readable; clarify it in profile.md"),
        (need_unverified(None, "TBD", "unreadable"), "sponsorship: the Markets row is not machine-readable; clarify it in profile.md"),
        (need_unverified(None, None, "no_market"), "sponsorship: no market is named; name the profile Markets row so the need can be checked"),
    ]
    for cap, text in cases:
        result = nine_tenths(cap)
        assert result.caps == () and result.tier == Tier.PURSUE
        assert text in texts(result)


def test_verified_no_need_produces_no_sponsorship_item():
    assert not any(t.startswith("sponsorship:") for t in texts(nine_tenths(caps_in(listing="not_offered"))))
    assert not any(t.startswith("sponsorship:") for t in texts(nine_tenths(caps_in(listing="unknown"))))


COMP_NO_CAP = {
    "null range": (comp(), {"range": None}, "pay: not disclosed in the posting; ask the recruiter"),
    "currency mismatch": (comp(range_cur="USD"), {}, "pay: range currency USD differs from floor currency INR; compare in one currency before deciding"),
    "basis mismatch": (comp(range_basis="total"), {}, "pay: range basis total differs from floor basis base; ask the recruiter for the base figure"),
    "confidence low": (comp(confidence="low"), {}, "pay: the range source confidence is low; confirm the figure with the recruiter"),
    "confidence unknown": (comp(confidence="unknown"), {}, "pay: the range source confidence is unknown; confirm the figure with the recruiter"),
}


@pytest.mark.parametrize("name", list(COMP_NO_CAP))
def test_compensation_not_established_never_caps(name):
    cap, over, item = COMP_NO_CAP[name]
    cap = {**cap, **over}
    result = nine_tenths(cap)
    assert result.caps == () and result.tier == Tier.PURSUE
    assert item in texts(result)
    assert nine_tenths(comp()).caps != ()                                      # the established version caps


def test_established_versions_cap_where_the_unknown_ones_do_not():
    for cap in (comp(confidence="high"), comp(confidence="medium")):
        assert [c.code for c in nine_tenths(cap).caps] == ["compensation"]


@pytest.mark.parametrize("verification", ["unverified", "conflict"])
def test_unverified_or_conflicting_floor_never_caps(verification):
    # the agent's own numbers would cap (high 3.0M < floor 3.5M) but the floor is not verified
    result = nine_tenths(comp(verification=verification, source_cell="TBD" if verification == "unverified" else "INR 3,500,000",
                                      row_state="unreadable" if verification == "unverified" else "parseable"))
    assert result.caps == () and result.tier == Tier.PURSUE
    assert nine_tenths(comp(verification="verified")).caps != ()
    items = texts(result)
    if verification == "conflict":
        assert 'pay: resolve the profile/agent mismatch for the floor (the Markets row says "INR 3,500,000")' in items
    else:
        assert "pay: the Markets row is not machine-readable; clarify it in profile.md" in items


def test_unverified_floor_is_reported_by_cause():
    cases = [
        (no_floor(source_cell="SGD 8000"),
         "pay: the Markets row says SGD 8000 but the evaluation gave no floor; state it so the cap can be checked"),
        (no_floor(source_cell="TBD", row_state="unreadable"), "pay: the Markets row is not machine-readable; clarify it in profile.md"),
        (no_floor(source_cell=None), "pay: no market is named; name the profile Markets row so the floor can be checked"),
    ]
    for floor, text in cases:
        result = nine_tenths(caps_in(floor=floor))
        assert result.caps == () and text in texts(result)


def test_a_conflict_is_never_resolved_in_the_favourable_direction():
    # the range would NOT cap against the agent's floor, but a conflicted floor must not cap or relax anything
    result = nine_tenths(comp(high=4_000_000, verification="conflict"))
    assert result.caps == () and any("resolve the profile/agent mismatch" in t for t in texts(result))


def test_missing_source_cell_is_reported_without_crashing():
    cap = sponsor(verification="unverified")
    cap["need"]["source_cell"] = None
    cap["need"]["row_state"] = "no_market"
    assert "sponsorship: no market is named; name the profile Markets row so the need can be checked" in texts(nine_tenths(cap))


# --- confidence ----------------------------------------------------------------------------------

def test_solid_baseline_is_high():
    result = solid_conf()
    assert result.confidence == Confidence.HIGH and result.caps == () and result.action_items == ()


def test_claimed_only_share_is_a_step_only_above_one_half():
    half = [rq("r1", "must", "covered", "claimed"), rq("r2", "must", "covered"), rq("r3", "nice", "gap")]
    assert solid_conf(reqs=half).confidence == Confidence.HIGH                         # 3/6 = 1/2, not more
    over = [rq("r1", "must", "covered", "claimed"), rq("r2", "strong", "adjacent", "claimed"), rq("r3", "nice", "covered")]
    assert solid_conf(reqs=over).confidence == Confidence.MEDIUM                       # 5/6


def test_claimed_share_counts_only_covered_and_adjacent_weight():
    reqs = [rq("r1", "must", "covered", "claimed"), rq("r2", "must", "gap"), rq("r3", "nice", "covered", "verified")]
    assert solid_conf(reqs=reqs).confidence == Confidence.MEDIUM                       # 3/4 of supported weight is claimed
    assert solid_conf(reqs=[rq("r1", "must", "gap"), rq("r2", "must", "gap"), rq("r3", "nice", "gap")]).confidence == Confidence.HIGH


def test_unverifiable_share_one_step_above_a_quarter_two_above_a_half():
    base = [rq("r4", "nice", "covered")]
    quarter = [rq("r1", "must", "unverifiable"), rq("r2", "must"), rq("r3", "must"), rq("r5", "must"), *base]
    assert solid_conf(reqs=quarter).confidence == Confidence.HIGH                      # 3/12 = 1/4, not more
    three_eighths = [rq("r1", "must", "unverifiable"), rq("r2", "must"), rq("r3", "strong"), *base]
    assert solid_conf(reqs=three_eighths).confidence == Confidence.MEDIUM              # 3/8 = 37.5%
    half = [rq("r1", "must", "unverifiable"), rq("r2", "must"), *base, rq("r3", "nice")]
    assert solid_conf(reqs=half).confidence == Confidence.MEDIUM                       # 3/6 = 1/2 is one step, not two
    severe = [rq("r1", "must", "unverifiable"), rq("r2", "strong"), *base, rq("r3", "nice")]
    assert solid_conf(reqs=severe).confidence == Confidence.LOW                        # 3/5 > 1/2: two steps


def test_unverifiable_share_is_of_must_and_strong_weight_only():
    reqs = [rq("r1", "nice", "unverifiable"), rq("r2", "nice", "unverifiable"), rq("r3", "must"), rq("r4", "strong")]
    assert solid_conf(reqs=reqs).confidence == Confidence.HIGH


@pytest.mark.parametrize("cap", [
    sponsor(value="yes", listing="unknown", quote=False),
    caps_in(need={"value": None, "verification": "unverified", "source_cell": "TBD", "row_state": "unreadable"}, listing="unknown"),
    caps_in(need={"value": "no", "verification": "unverified", "source_cell": "preferred", "row_state": "unreadable"}, listing="unknown"),
    caps_in(need={"value": "yes", "verification": "conflict", "source_cell": "not needed", "row_state": "parseable"}, listing="unknown"),
    {**comp(), "range": None},
])
def test_decision_relevant_unknown_costs_one_step(cap):
    assert solid_conf(cap=cap).confidence == Confidence.MEDIUM


@pytest.mark.parametrize("cap", [
    sponsor(value="yes", listing="offered"),                                           # listing known
    sponsor(value="yes", listing="not_offered"),                                       # established: a cap, not an unknown
    caps_in(),                                                                         # need verified no
    {**comp(verification="unverified", row_state="unreadable"), "range": None},                                # no verified floor
    {**caps_in(), "range": None, "floor": no_floor()},                                  # no floor at all
])
def test_known_inputs_cost_no_confidence_step(cap):
    assert solid_conf(cap=cap).confidence == Confidence.HIGH


def test_thin_extraction_two_requirements_costs_one_step():
    assert solid_conf(reqs=[rq("r1"), rq("r2", "strong")]).confidence == Confidence.MEDIUM


def test_no_must_requirement_costs_one_step():
    assert solid_conf(reqs=[rq("r1", "strong"), rq("r2", "strong"), rq("r3", "nice")]).confidence == Confidence.MEDIUM
    # fewer than three AND no must is still one step (they are one trigger)
    assert solid_conf(reqs=[rq("r1", "strong"), rq("r2", "nice")]).confidence == Confidence.MEDIUM


def test_requirement_count_uses_all_requirements_including_unverifiable():
    reqs = [rq("r1", "must"), rq("r2", "strong"), rq("r3", "nice", "unverifiable")]
    assert solid_conf(reqs=reqs).confidence == Confidence.HIGH
    assert solid_conf(reqs=reqs[:2]).confidence == Confidence.MEDIUM


@pytest.mark.parametrize("state", ["missing", "invalid"])
def test_rubric_missing_or_invalid_costs_one_step_and_omits_fit(state):
    result = solid_conf(fit=[], rubric_state=state)
    assert result.fit is None and result.confidence == Confidence.MEDIUM
    assert result.combined == result.coverage == F(1)


def test_invalid_rubric_ignores_any_fit_rows_in_the_basis():
    result = solid_conf(fit=SOLID_FIT, rubric_state="invalid")
    assert result.fit is None and result.combined == result.coverage


def test_steps_stack_and_floor_at_low():
    reqs = [rq("r1", "must", "unverifiable"), rq("r2", "strong", "covered", "claimed")]
    result = run(reqs, cap=sponsor(value="yes", listing="unknown", quote=False))
    assert result.confidence == Confidence.LOW


def test_claimed_and_thin_extraction_stack_to_low():
    two = solid_conf(reqs=[rq("r1", "strong", "covered", "claimed"), rq("r2", "strong", "covered", "claimed")])
    assert two.confidence == Confidence.LOW                                            # claimed, then thin extraction


# --- action items ---------------------------------------------------------------------------------

def test_action_items_follow_the_fixed_order_and_each_names_its_ref():
    reqs = [rq("r1", "must", "gap"), rq("r2", "must", "unverifiable"), rq("r3", "strong", "covered", "claimed"),
            rq("r4", "must", "gap"), rq("r5", "nice", "unverifiable")]
    cap = merge(sponsor(value="yes", verification="conflict", listing="unknown", quote=False), comp(verification="unverified", source_cell="TBD", row_state="unreadable"))
    cap["range"] = None
    result = run(reqs, cap=cap)
    got = texts(result)
    assert [a.ref for a in result.action_items] == ["r2", "r5", "r1", "r4", "r3", "sponsorship", "sponsorship", "pay", "pay"]
    assert got[0].startswith("r2: cannot be derived from memory")
    assert got[2] == ("r1: must requirement is not met by memory; ask the user whether they have it, "
                      "and add the fact with `careeros memory add` if true")
    assert got[4] == "r3: supported only by claimed facts (fct_a); ask the user to confirm them"
    assert got[5].startswith("sponsorship: resolve the profile/agent mismatch")
    assert got[6] == "sponsorship: not mentioned in the posting; ask about sponsorship before investing time"
    assert got[7] == "pay: the Markets row is not machine-readable; clarify it in profile.md"
    assert got[8] == "pay: not disclosed in the posting; ask the recruiter"


def test_action_item_text_form():
    from careeros.core.evaluation.model import ActionItem
    assert score.action_item_text(ActionItem("r2", "do it")) == "r2: do it"


def test_a_gap_that_is_not_a_must_has_no_action_item():
    assert run([rq("r1", "strong", "gap"), rq("r2", "nice", "gap"), rq("r3", "must")]).action_items == ()


# --- purity, determinism, no floats ---------------------------------------------------------------

def test_same_basis_twice_and_reordered_requirements_give_equal_results():
    b = worked_example()
    again = copy.deepcopy(b)
    again["requirements"].reverse()
    again["fit"].reverse()
    assert score.score(b) == score.score(b) == score.score(again)


def test_scoring_does_not_mutate_the_basis():
    b = worked_example()
    before = canon.canonical_json(b)
    score.score(b)
    assert canon.canonical_json(b) == before


def test_scoring_a_basis_rebuilt_from_canonical_json_equals_scoring_the_original():
    for b in (worked_example(), basis(coverage_only({"r1", "r3"}), cap=merge(sponsor(), comp()))):
        rebuilt = json.loads(canon.canonical_json(b))
        assert score.score(rebuilt) == score.score(b)


def test_basis_values_are_integers_strings_or_booleans():
    def check(obj):
        if isinstance(obj, dict):
            list(map(check, obj.values()))
        elif isinstance(obj, list):
            list(map(check, obj))
        else:
            assert obj is None or type(obj) in (int, str, bool)
    check(worked_example())
    canon.canonical_json(worked_example())      # a float anywhere would raise


def test_no_float_arithmetic_outside_the_serialiser():
    tree = ast.parse(Path(score.__file__).read_text())
    offenders = []
    for fn in (n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)):
        for call in (n for n in ast.walk(fn) if isinstance(n, ast.Call)):
            if isinstance(call.func, ast.Name) and call.func.id == "float" and fn.name != "stored_score":
                offenders.append(fn.name)
    module_level = [n for n in tree.body if not isinstance(n, (ast.FunctionDef, ast.ClassDef))
                    for c in ast.walk(n) if isinstance(c, ast.Call) and getattr(c.func, "id", "") == "float"]
    assert offenders == [] and module_level == []
    assert "round(" not in Path(score.__file__).read_text()


def test_unknown_versions_raise_exactly_unsupported_version():
    b = worked_example()
    for bad in ({**b, "rules_version": "2"}, {**b, "basis_version": "9"}, {**b, "rules_version": 1},
                {**b, "basis_version": ["1"]}):
        with pytest.raises(UnsupportedVersion) as info:
            score.score(bad)
        assert type(info.value) is UnsupportedVersion


def test_score_resolves_rules_from_the_registry(monkeypatch):
    other = dataclasses.replace(RULES1, version="2", tier_pursue=F(9, 10))
    monkeypatch.setitem(RULES, "2", other)
    b = {**worked_example(), "rules_version": "2"}
    assert score.score(b).tier == Tier.STRETCH                      # 4/5 < 9/10 under the registered version
    assert score.score(worked_example()).tier == Tier.PURSUE


def mutate(path, value):
    b = copy.deepcopy(basis(solid(), SOLID_FIT, cap=merge(sponsor(), comp())))
    target = b
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    return b


def mutate_many(changes):
    b = copy.deepcopy(basis(solid(), SOLID_FIT, cap=merge(sponsor(), comp())))
    for path, value in changes.items():
        target = b
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = value
    return b


MALFORMED = [
    (("requirement_count",), 2), (("requirement_count",), True), (("requirement_count",), "3"),
    (("rubric_state",), "great"), (("rubric_state",), None),
    (("requirements", 0, "class"), "huge"), (("requirements", 0, "outcome"), "great"),
    (("requirements", 0, "status_floor"), "sure"), (("requirements", 0, "id"), 1),
    (("requirements", 0, "facts"), "fct_a"), (("requirements", 0, "facts"), [1]),
    (("fit", 0, "weight"), 4), (("fit", 0, "weight"), True), (("fit", 0, "weight"), "3"), (("fit", 0, "weight"), 1.0),
    (("fit", 0, "judgement"), "maybe"), (("fit", 0, "row"), 5),
    (("cap_inputs", "listing"), "maybe"), (("cap_inputs", "quote"), 1), (("cap_inputs", "quote"), "yes"),
    (("cap_inputs", "confidence"), "great"), (("cap_inputs", "market"), 3), (("cap_inputs", "as_of"), 2026),
    (("cap_inputs", "need", "value"), "maybe"), (("cap_inputs", "need", "value"), True),
    (("cap_inputs", "need", "verification"), "sure"), (("cap_inputs", "need", "source_cell"), 3), (("cap_inputs", "need", "row_state"), "maybe"),
    (("cap_inputs", "floor", "row_state"), None), (("cap_inputs", "floor", "row_state"), "good"),
    (("cap_inputs", "floor", "verification"), "sure"), (("cap_inputs", "floor", "amount"), 3.5),
    (("cap_inputs", "floor", "amount"), True), (("cap_inputs", "floor", "amount"), "3500000"),
    (("cap_inputs", "floor", "currency"), 5), (("cap_inputs", "floor", "basis"), "gross"),
    (("cap_inputs", "range", "high"), 3.5), (("cap_inputs", "range", "high"), None), (("cap_inputs", "range", "high"), True),
    (("cap_inputs", "range", "low"), 1.5), (("cap_inputs", "range", "basis"), "gross"), (("cap_inputs", "range", "currency"), 7),
    (("cap_inputs", "floor"), None), (("cap_inputs", "need"), None), (("cap_inputs",), None), (("fit",), None),
    (("requirements",), None), (("requirements", 0), 5),
]


@pytest.mark.parametrize("path,value", MALFORMED, ids=[f"{'.'.join(map(str, p))}={v!r}" for p, v in MALFORMED])
def test_malformed_basis_is_a_value_error_and_nothing_else(path, value):
    with pytest.raises(ValueError) as info:
        score.score(mutate(path, value))
    assert type(info.value) is ValueError and "malformed basis" in str(info.value)


@pytest.mark.parametrize("key", ["requirement_count", "rubric_state", "requirements", "fit", "cap_inputs"])
def test_missing_top_level_key_is_a_value_error(key):
    b = mutate(("rubric_state",), "ok")
    del b[key]
    with pytest.raises(ValueError) as info:
        score.score(b)
    assert type(info.value) is ValueError


@pytest.mark.parametrize("key", ["basis_version", "rules_version"])
def test_missing_version_key_is_a_value_error(key):
    b = mutate(("rubric_state",), "ok")
    del b[key]
    with pytest.raises(ValueError) as info:
        score.score(b)
    assert type(info.value) is ValueError


def test_extra_keys_anywhere_and_non_mappings_are_value_errors():
    for b in (mutate(("extra",), 1), mutate(("cap_inputs", "extra"), 1), mutate(("cap_inputs", "floor", "extra"), 1),
              mutate(("requirements", 0, "extra"), 1), mutate(("fit", 0, "extra"), 1), [], None, "basis"):
        with pytest.raises(ValueError) as info:
            score.score(b)
        assert type(info.value) is ValueError


def test_valid_enum_values_are_accepted():
    for path, value in ((("rubric_state",), "invalid"), (("cap_inputs", "confidence"), None),
                        (("cap_inputs", "confidence"), "unknown"),
                        (("requirements", 0, "status_floor"), None), (("cap_inputs", "range"), None),
                        (("cap_inputs", "market"), None)):
        score.score(mutate(path, value))
    # a null need value / floor amount is valid once the input is not claimed verified
    score.score(mutate_many({("cap_inputs", "need", "value"): None, ("cap_inputs", "need", "verification"): "unverified"}))
    score.score(mutate_many({("cap_inputs", "floor", "amount"): None, ("cap_inputs", "floor", "verification"): "unverified",
                             ("cap_inputs", "floor", "row_state"): "unreadable"}))


def test_pinned_cap_codes():
    assert (score.CAP_SPONSORSHIP, score.CAP_COMPENSATION, score.CAP_MUST_GAPS) == ("sponsorship", "compensation", "must_gaps")


# --- the basis builder (derive.Basis.complete) ----------------------------------------------------

PROFILE = """# P

## Markets
| Market | Min base (per year) | Sponsorship | Priority |
|---|---|---|---|
| Atlantis (home) | ₹35,00,000 | not needed | 1 |
| Singapore | SGD 8000 | required | 2 |
| Elsewhere | TBD | preferred | 3 |
"""
MARKETS = markets.parse(PROFILE)


def derived_basis(*entries):
    return Basis(tuple(entries), len(entries))


def entry(rid="r1", cls="must", outcome="covered", facts=("fct_a",), floor="verified"):
    return {"id": rid, "class": cls, "outcome": outcome, "facts": facts, "status_floor": floor}


RUBRIC = Rubric((FitRow("Role title matches", 3), FitRow("Location match", 2)), (), "ok")


def judgements(*pairs):
    from careeros.core.evaluation.model import FitJudgement, Judgement
    return tuple(FitJudgement(row, Judgement(j), None) for row, j in pairs)


def test_complete_basis_has_exactly_the_v1_keys_and_no_floats():
    facts = JobFacts(market="Singapore", needed=True, listing=Listing.NOT_OFFERED, sponsorship_quote="no visa",
                     range_low=6000, range_high=7500, range_currency="sgd", range_basis=MarketBasis.BASE,
                     floor_amount=8000, floor_currency="SGD", source="levels.fyi", comp_confidence=None, as_of="2026-09")
    check = markets.verify_market(facts, MARKETS)
    out = derived_basis(entry()).complete(judgements(("Role title matches", "yes")), facts, check, RUBRIC)
    assert set(out) == set(BASIS_VERSIONS["1"].keys)
    canon.canonical_json(out)
    assert out["basis_version"] == "1" and out["rules_version"] == "1"
    assert out["requirement_count"] == 1 and out["rubric_state"] == "ok"
    assert out["requirements"] == [{"id": "r1", "class": "must", "outcome": "covered", "facts": ["fct_a"], "status_floor": "verified"}]
    assert out["fit"] == [{"row": "role title matches", "weight": 3, "judgement": "yes"}]     # a row without a judgement is absent
    assert out["cap_inputs"] == {
        "market": "Singapore",
        "need": {"value": "yes", "verification": "verified", "source_cell": "required", "row_state": "parseable"},
        "listing": "not_offered",
        "quote": True,
        "range": {"low": 6000, "high": 7500, "currency": "SGD", "basis": "base"},
        "floor": {"amount": 8000, "currency": "SGD", "basis": "base", "verification": "verified", "source_cell": "SGD 8000", "row_state": "parseable"},
        "confidence": None,
        "as_of": "2026-09",
    }


def test_complete_basis_scores_and_caps_from_real_market_inputs():
    facts = JobFacts(market="Singapore", needed=True, listing=Listing.NOT_OFFERED, sponsorship_quote="no visa",
                     range_low=6000, range_high=7500, range_currency="SGD", range_basis=MarketBasis.BASE,
                     floor_amount=8000, floor_currency="SGD", comp_confidence=SourceConfidence.HIGH, as_of="2026-09")
    check = markets.verify_market(facts, MARKETS)
    out = derived_basis(entry("r1"), entry("r2", "strong")).complete(
        judgements(("Role title matches", "yes"), ("Location match", "no")), facts, check, RUBRIC)
    result = score.score(json.loads(canon.canonical_json(out)))
    assert result.combined == F(65, 100) + F(35, 100) * F(3, 5)          # 0.86, would be pursue
    assert [c.code for c in result.caps] == ["sponsorship", "compensation"] and result.tier == Tier.WEAK


def test_complete_basis_conflict_and_unverified_states_are_carried():
    facts = JobFacts(market="Atlantis (home)", needed=True, listing=Listing.UNKNOWN, floor_amount=1, floor_currency="INR")
    check = markets.verify_market(facts, MARKETS)
    out = derived_basis(entry()).complete((), facts, check, RUBRIC)["cap_inputs"]
    assert out["need"] == {"value": "yes", "verification": "conflict", "source_cell": "not needed",
                             "row_state": "parseable"}
    assert out["floor"]["verification"] == "conflict" and out["floor"]["source_cell"] == "₹35,00,000"


def test_complete_basis_without_a_market_or_facts_is_neutral():
    check = markets.verify_market(JobFacts(), MARKETS)
    out = derived_basis(entry()).complete((), JobFacts(), check, Rubric((), ("rubric missing",), "missing"))
    assert out["rubric_state"] == "missing" and out["fit"] == []
    assert out["cap_inputs"] == {
        "market": None, "need": {"value": None, "verification": "unverified", "source_cell": None, "row_state": "no_market"},
        "listing": "unknown", "quote": False, "range": None,
        "floor": {"amount": None, "currency": None, "basis": None, "verification": "unverified", "source_cell": None,
                  "row_state": "no_market"},
        "confidence": None, "as_of": None}


def test_complete_basis_unusable_rubrics_drop_fit_rows():
    check = markets.verify_market(JobFacts(), MARKETS)
    given = judgements(("Role title matches", "yes"))
    invalid = derived_basis(entry()).complete(given, JobFacts(), check, Rubric(RUBRIC.rows, ("bad",), "invalid"))
    assert invalid["rubric_state"] == "invalid" and invalid["fit"] == []
    empty = derived_basis(entry()).complete(given, JobFacts(), check, Rubric((), (), "ok"))
    assert empty["rubric_state"] == "missing" and empty["fit"] == []


def test_complete_basis_fit_rows_follow_the_normalised_label_and_are_sorted():
    check = markets.verify_market(JobFacts(), MARKETS)
    out = derived_basis(entry()).complete(judgements(("LOCATION  match", "partial"), ("role TITLE matches", "no")),
                                          JobFacts(), check, RUBRIC)
    assert out["fit"] == [{"row": "location match", "weight": 2, "judgement": "partial"},
                          {"row": "role title matches", "weight": 3, "judgement": "no"}]


def test_complete_basis_requires_both_range_ends_to_be_ints_and_normalises_none_basis():
    check = markets.verify_market(JobFacts(), MARKETS)
    only_low = JobFacts(range_low=5, range_currency="INR")
    assert derived_basis(entry()).complete((), only_low, check, RUBRIC)["cap_inputs"]["range"] is None
    floor_no_basis = JobFacts(market="Atlantis (home)", floor_amount=3_500_000, floor_currency="INR", range_high=3_000_000,
                              range_currency="INR", range_basis=MarketBasis.BASE)
    out = derived_basis(entry()).complete((), floor_no_basis, markets.verify_market(floor_no_basis, MARKETS), RUBRIC)
    assert out["cap_inputs"]["floor"]["basis"] == "base" and out["cap_inputs"]["floor"]["verification"] == "verified"


def test_basis_as_dict_is_unchanged_by_the_extension():
    assert set(derived_basis(entry()).as_dict()) == {"basis_version", "rules_version", "requirement_count", "requirements"}


# --- omitted inputs leave a trace (spec 16.1) ----------------------------------------------------------

SG_PROFILE = """# P

## Markets
| Market | Min base (per year) | Sponsorship | Priority |
|---|---|---|---|
| Singapore | SGD 8000 | required | 1 |
"""
SG = markets.parse(SG_PROFILE)


def singapore(**over):
    given = dict(market="Singapore", listing=Listing.NOT_OFFERED, sponsorship_quote="no visa support",
                 range_low=6000, range_high=7500, range_currency="SGD", range_basis=MarketBasis.BASE,
                 comp_confidence=SourceConfidence.HIGH, as_of="2026-09", needed=True, floor_amount=8000,
                 floor_currency="SGD")
    given.update(over)
    facts = JobFacts(**given)
    reqs = (entry("r1"), entry("r2", "strong"), entry("r3", "nice"))
    out = Basis(reqs, 3).complete(judgements(("Role title matches", "yes")), facts, markets.verify_market(facts, SG),
                                  Rubric((FitRow("Role title matches", 3),), (), "ok"))
    return out, score.score(json.loads(canon.canonical_json(out)))


def test_singapore_agent_states_both_values_both_caps_apply():
    out, result = singapore()
    assert [c.code for c in result.caps] == ["sponsorship", "compensation"] and result.tier == Tier.WEAK
    assert out["cap_inputs"]["need"] == {"value": "yes", "verification": "verified", "source_cell": "required",
                                              "row_state": "parseable"}
    assert out["cap_inputs"]["floor"]["source_cell"] == "SGD 8000"


def test_singapore_agent_omits_both_values_no_cap_but_both_items_and_cells_stored():
    out, result = singapore(needed=None, floor_amount=None, floor_currency=None)
    assert result.caps == () and result.tier == Tier.PURSUE and result.confidence == Confidence.HIGH
    assert out["cap_inputs"]["need"] == {"value": None, "verification": "unverified", "source_cell": "required",
                                              "row_state": "parseable"}
    assert out["cap_inputs"]["floor"] == {"amount": None, "currency": None, "basis": None,
                                          "verification": "unverified", "source_cell": "SGD 8000",
                                          "row_state": "parseable"}
    assert texts(result) == [
        "sponsorship: the Markets row says required but the evaluation gave no value; state it so the cap can be checked",
        "pay: the Markets row says SGD 8000 but the evaluation gave no floor; state it so the cap can be checked",
    ]


def test_singapore_favourable_wrong_values_are_conflicts_not_caps_or_relaxations():
    out, result = singapore(needed=False, floor_amount=7000)
    assert result.caps == () and result.tier == Tier.PURSUE
    assert texts(result) == [
        'sponsorship: resolve the profile/agent mismatch for the need (the Markets row says "required")',
        'pay: resolve the profile/agent mismatch for the floor (the Markets row says "SGD 8000")',
    ]
    assert out["cap_inputs"]["need"]["verification"] == out["cap_inputs"]["floor"]["verification"] == "conflict"


# --- natural order, sorted facts, pinned text constants ----------------------------------------------------

def test_requirement_ids_sort_naturally_in_items_and_cap_reasons():
    reqs = [rq("r10", "must", "gap"), rq("r2", "must", "gap"), rq("r1", "must", "unverifiable"), *many_nice()]
    result = run(reqs)
    assert [a.ref for a in result.action_items] == ["r1", "r2", "r10"]
    assert result.caps[0].reason == "2 must requirements are gaps (r2, r10)"


def test_listed_facts_are_sorted_in_claimed_items():
    a = run([{**rq("r1", "must", "covered", "claimed", ("z_fact", "a_fact")), "facts": ["z_fact", "a_fact"]}])
    b = run([{**rq("r1", "must", "covered", "claimed", ("a_fact", "z_fact")), "facts": ["a_fact", "z_fact"]}])
    assert texts(a) == texts(b) and "(a_fact, z_fact)" in texts(a)[0]


def test_every_pinned_text_is_a_named_constant():
    names = [n for n in dir(score) if n.startswith(("REASON_", "ITEM_"))]
    assert len(names) == 20
    doc = score.__doc__
    assert all(f"`{n}`" in doc for n in names)
    assert "requires a new rules version" in doc


# --- the scorer never re-parses a cell ----------------------------------------------------------------------

def test_score_is_unaffected_by_the_parsers_and_does_not_import_markets(monkeypatch):
    out, before = singapore(needed=None, floor_amount=None, floor_currency=None)
    out2, before2 = singapore(needed=True, floor_amount=8000)

    def boom(*_a, **_k):
        raise AssertionError("the scorer must not parse a Markets cell")

    monkeypatch.setattr(markets, "parse_floor", boom)
    monkeypatch.setattr(markets, "parse_need", boom)
    assert score.score(out) == before and score.score(out2) == before2

    tree = ast.parse(Path(score.__file__).read_text())
    imported = [n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)] + [
        a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names]
    assert not any("markets" in (m or "") for m in imported)
    assert not hasattr(score, "markets") and not hasattr(score, "parse_floor") and not hasattr(score, "parse_need")


def test_row_state_is_recorded_at_build_time():
    facts = JobFacts(market="Elsewhere")
    profile = SG_PROFILE + "| Elsewhere | TBD | preferred | 2 |\n"
    mk = markets.parse(profile)
    unreadable = Basis((entry(),), 1).complete((), facts, markets.verify_market(facts, mk), Rubric((), (), "missing"))
    assert unreadable["cap_inputs"]["need"]["row_state"] == unreadable["cap_inputs"]["floor"]["row_state"] == "unreadable"
    assert unreadable["cap_inputs"]["need"]["source_cell"] == "preferred"
    none = Basis((entry(),), 1).complete((), JobFacts(), markets.verify_market(JobFacts(), mk), Rubric((), (), "missing"))
    assert none["cap_inputs"]["need"]["row_state"] == none["cap_inputs"]["floor"]["row_state"] == "no_market"
    out, _ = singapore(needed=None, floor_amount=None, floor_currency=None)
    assert out["cap_inputs"]["need"]["row_state"] == out["cap_inputs"]["floor"]["row_state"] == "parseable"


# --- state combinations (fix round 3) ---------------------------------------------------------------------------

NEED, FLOOR = ("cap_inputs", "need"), ("cap_inputs", "floor")
BAD_COMBINATIONS = {
    "verified need without a value": {NEED + ("value",): None},
    "verified floor without an amount": {FLOOR + ("amount",): None},
    "verified floor without a currency": {FLOOR + ("currency",): None},
    "verified floor without a basis": {FLOOR + ("basis",): None},
    "verified need with an unreadable row": {NEED + ("row_state",): "unreadable"},
    "verified need with no market": {NEED + ("row_state",): "no_market", NEED + ("source_cell",): None},
    "verified floor with an unreadable row": {FLOOR + ("row_state",): "unreadable"},
    "verified floor with no market": {FLOOR + ("row_state",): "no_market", FLOOR + ("source_cell",): None},
    "conflicting need with an unreadable row": {NEED + ("verification",): "conflict", NEED + ("row_state",): "unreadable"},
    "conflicting floor with no market": {FLOOR + ("verification",): "conflict", FLOOR + ("row_state",): "no_market",
                                         FLOOR + ("source_cell",): None},
    "conflicting need without a value": {NEED + ("verification",): "conflict", NEED + ("value",): None},
    "conflicting floor without an amount": {FLOOR + ("verification",): "conflict", FLOOR + ("amount",): None},
    "conflicting floor without a currency": {FLOOR + ("verification",): "conflict", FLOOR + ("currency",): None},
    "no_market need with a source cell": {NEED + ("verification",): "unverified", NEED + ("row_state",): "no_market"},
    "no_market floor with a source cell": {FLOOR + ("verification",): "unverified", FLOOR + ("row_state",): "no_market"},
    "unverified need, readable row, complete value": {NEED + ("verification",): "unverified"},
    "unverified floor, readable row, complete value": {FLOOR + ("verification",): "unverified"},
}


@pytest.mark.parametrize("name", list(BAD_COMBINATIONS))
def test_impossible_state_combinations_are_value_errors(name):
    with pytest.raises(ValueError) as info:
        score.score(mutate_many(BAD_COMBINATIONS[name]))
    assert type(info.value) is ValueError and "malformed basis" in str(info.value)


def test_possible_state_combinations_are_accepted():
    for changes in ({NEED + ("verification",): "conflict", NEED + ("value",): "no"},
                    {FLOOR + ("verification",): "conflict"},
                    {NEED + ("verification",): "unverified", NEED + ("value",): None},
                    {NEED + ("verification",): "unverified", NEED + ("row_state",): "no_market", NEED + ("source_cell",): None,
                     NEED + ("value",): "yes"},
                    {FLOOR + ("verification",): "unverified", FLOOR + ("amount",): None, FLOOR + ("currency",): None},
                    {FLOOR + ("verification",): "unverified", FLOOR + ("amount",): None}):
        score.score(mutate_many(changes))


# --- unusable table and incomplete floor ---------------------------------------------------------------------------

UNUSABLE = SG_PROFILE + "\n## Markets\n| Market | Min base | Sponsorship |\n|---|---|---|\n| Other | $1 | required |\n"


def singapore_in(table, **over):
    given = dict(market="Singapore", listing=Listing.NOT_OFFERED, sponsorship_quote="no visa support",
                 range_low=6000, range_high=7500, range_currency="SGD", range_basis=MarketBasis.BASE,
                 comp_confidence=SourceConfidence.HIGH, as_of="2026-09", needed=True, floor_amount=8000, floor_currency="SGD")
    given.update(over)
    facts = JobFacts(**given)
    out = Basis((entry("r1"), entry("r2", "strong"), entry("r3", "nice")), 3).complete(
        judgements(("Role title matches", "yes")), facts, markets.verify_market(facts, table),
        Rubric((FitRow("Role title matches", 3),), (), "ok"))
    return out, score.score(json.loads(canon.canonical_json(out)))


@pytest.mark.parametrize("over", [dict(needed=None, floor_amount=None, floor_currency=None), {}])
def test_unusable_table_is_unreadable_never_caps_and_says_so(over):
    out, result = singapore_in(markets.parse(UNUSABLE), **over)
    assert markets.parse(UNUSABLE).unusable
    for field in ("need", "floor"):
        assert out["cap_inputs"][field]["row_state"] == "unreadable"
        assert out["cap_inputs"][field]["verification"] == "unverified"
    assert result.caps == () and result.tier == Tier.PURSUE
    assert texts(result) == ["sponsorship: the Markets row is not machine-readable; clarify it in profile.md",
                             "pay: the Markets row is not machine-readable; clarify it in profile.md"]


def test_incomplete_floor_is_the_agents_problem_not_the_profiles():
    expected = ("pay: the evaluation's floor is incomplete (amount and currency are both required); "
                "state both so the cap can be checked")
    for over in (dict(floor_currency=None), dict(floor_amount=None)):
        out, result = singapore_in(SG, **over)
        floor = out["cap_inputs"]["floor"]
        assert floor["verification"] == "unverified" and floor["row_state"] == "parseable"
        assert expected in texts(result)
        assert "pay: the Markets row is not machine-readable; clarify it in profile.md" not in texts(result)
        assert [c.code for c in result.caps] == ["sponsorship"]          # the incomplete floor never feeds the cap
    assert floor["currency"] == "SGD" and floor["amount"] is None        # the partial value is stored, not dropped


def test_module_docstring_describes_the_current_signature():
    assert "`score(basis)`" in score.__doc__ and "score(basis, rules)" not in score.__doc__
