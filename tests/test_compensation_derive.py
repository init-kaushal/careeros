"""Pure compensation derivation (Task B): order of operations, FX window, reason precedence, effective kind, union/conflict."""

from __future__ import annotations

import itertools
import subprocess
import sys
from datetime import date, timedelta
from fractions import Fraction

import pytest

from careeros.core.compensation.derive import FloorInfo, Note, derive_compensation, to_basis
from careeros.core.compensation.model import CompRecord, Conversion, Observation
from careeros.core.evaluation import canon
from careeros.core.evaluation.model import MarketBasis

D0 = date(2026, 6, 1)
FLOOR = FloorInfo(3_000_000, "INR", "base", True)


def obs(id="a", kind="posted", low=30, high=40, currency="INR", basis=MarketBasis.BASE, period="year", conf="high",
        as_of=D0, source="jd"):
    return Observation(id, kind, low, high, currency, basis, period, source, None, as_of, conf)


def conv(rate="83.20", frm="USD", to="INR", as_of=D0):
    return Conversion(frm, to, Fraction(rate), "rbi", as_of)


def rec(*observations, conversions=()):
    return CompRecord("India (home)", tuple(observations), tuple(conversions), "")


def run(*observations, conversions=(), floor=FLOOR):
    return derive_compensation(rec(*observations, conversions=conversions), floor)


def only(d):
    return d.observations[0]


# --- per observation ----------------------------------------------------------------------------------

def test_month_is_annualised_before_conversion_and_originals_kept():
    d = run(obs(low=250_000, high=300_000, period="month"))
    r = only(d)
    assert r.comparable and r.reason is None
    assert (r.annualised.low, r.annualised.high) == (3_000_000, 3_600_000)
    assert (r.converted.low, r.converted.high) == (3_000_000, 3_600_000)
    o = r.original
    assert (o.low, o.high, o.currency, o.period, o.source) == (250_000, 300_000, "INR", "month", "jd")


def test_usd_converts_at_exact_rate_after_annualising():
    d = run(obs(low=10_000, high=12_500, currency="USD", period="month"), conversions=[conv("83.20")])
    r = only(d)
    assert (r.annualised.low, r.annualised.high) == (120_000, 150_000)
    assert r.converted.low == Fraction(120_000) * Fraction("83.20") == 9_984_000
    assert r.converted.high == 12_480_000
    assert (r.original.currency, r.original.period, r.original.low) == ("USD", "month", 10_000)
    assert (d.low, d.high) == (9_984_000, 12_480_000)


def test_exact_fractional_rate_is_not_rounded():
    d = run(obs(low=1, high=None, currency="USD"), conversions=[conv("1/3")])
    assert only(d).converted.low == Fraction(1, 3) and only(d).converted.high is None


@pytest.mark.parametrize("delta,ok", [(0, True), (30, True), (31, False), (-1, False)])
def test_fx_window(delta, ok):
    o = obs(currency="USD", as_of=D0)
    d = run(o, conversions=[conv(as_of=D0 - timedelta(days=delta))])
    r = only(d)
    assert r.comparable is ok
    assert r.reason == (None if ok else "FX_RATE_OUTSIDE_WINDOW")


def test_custom_window():
    o = obs(currency="USD")
    c = conv(as_of=D0 - timedelta(days=5))
    assert only(derive_compensation(rec(o, conversions=[c]), FLOOR, fx_window_days=4)).reason == "FX_RATE_OUTSIDE_WINDOW"
    assert only(derive_compensation(rec(o, conversions=[c]), FLOOR, fx_window_days=5)).comparable


def test_missing_pair_no_inverse_no_chain():
    inverse = conv(frm="INR", to="USD", rate="0.012")
    assert only(run(obs(currency="USD"), conversions=[inverse])).reason == "FX_CONVERSION_MISSING"
    chain = [conv(frm="USD", to="EUR", rate="0.9"), conv(frm="EUR", to="INR", rate="90")]
    assert only(run(obs(currency="USD"), conversions=chain)).reason == "FX_CONVERSION_MISSING"
    assert only(run(obs(currency="USD"))).reason == "FX_CONVERSION_MISSING"


def test_basis_mismatch():
    assert only(run(obs(basis=MarketBasis.TOTAL))).reason == "BASIS_MISMATCH"
    assert only(run(obs(basis=MarketBasis.TOTAL), floor=FloorInfo(1, "INR", "total", True))).comparable


def test_floor_unverified_marks_every_observation():
    for floor in (FloorInfo(None, None, None, False), FloorInfo(3_000_000, "INR", "base", False)):
        d = run(obs("a"), obs("b", kind="estimate"), floor=floor)
        assert [r.reason for r in d.observations] == ["FLOOR_UNVERIFIED"] * 2
        assert all(not r.comparable and r.converted is None for r in d.observations)
        assert d.status == "none" and d.low is None and d.high is None


def test_reason_precedence_with_all_four_applicable():
    # unverified floor, total vs base, no USD->INR conversion... then peel off one reason at a time
    o = obs(currency="USD", basis=MarketBasis.TOTAL, as_of=D0)
    late = conv(as_of=D0 + timedelta(days=1))
    unverified = FloorInfo(3_000_000, "INR", "base", False)
    assert only(run(o, conversions=[late], floor=unverified)).reason == "FLOOR_UNVERIFIED"
    assert only(run(o, conversions=[late])).reason == "BASIS_MISMATCH"
    o2 = obs(currency="USD", as_of=D0)
    assert only(run(o2, conversions=[late])).reason == "FX_RATE_OUTSIDE_WINDOW"
    assert only(run(obs(currency="USD", basis=MarketBasis.TOTAL))).reason == "BASIS_MISMATCH"
    # missing beats outside-window cannot co-occur (window needs a conversion); missing beats nothing below it
    assert only(run(o2)).reason == "FX_CONVERSION_MISSING"


# --- effective kind -----------------------------------------------------------------------------------

def test_posted_wins_over_estimate():
    d = run(obs("p", "posted", 30, 40), obs("e", "estimate", 50, 60))
    assert (d.status, d.kind, d.low, d.high) == ("effective", "posted", 30, 40)
    assert d.kind_complete and d.kind_confident
    assert not any(n.code == "estimate_only" for n in d.notes)


def test_recruiter_over_estimate_and_posted_over_recruiter():
    assert run(obs("r", "recruiter"), obs("e", "estimate", 1, 2)).kind == "recruiter"
    assert run(obs("r", "recruiter", 1, 2), obs("p", "posted")).kind == "posted"


def test_incomparable_posted_does_not_fall_back():
    d = run(obs("p", "posted", basis=MarketBasis.TOTAL), obs("e", "estimate", 50, 60))
    assert d.status == "none" and d.kind == "posted" and d.low is None and d.high is None
    assert not d.kind_complete
    assert Note("incomparable", "p", "BASIS_MISMATCH") in d.notes


def test_estimate_only_is_effective_with_note():
    d = run(obs("e", "estimate", 50, 60))
    assert (d.status, d.kind, d.low, d.high) == ("effective", "estimate", 50, 60)
    assert Note("estimate_only", None, "estimate") in d.notes


def test_empty_is_none():
    d = derive_compensation(CompRecord(None, (), (), ""), FloorInfo(None, None, None, False))
    assert (d.status, d.kind, d.low, d.high, d.observations, d.notes) == ("none", None, None, None, (), ())
    assert not d.kind_complete and not d.kind_confident


# --- union and conflict -------------------------------------------------------------------------------

def span(*pairs):
    return run(*[obs(f"o{i}", low=lo, high=hi) for i, (lo, hi) in enumerate(pairs)])


@pytest.mark.parametrize("pairs,status,low,high", [
    (((30, 40), (35, 45)), "effective", 30, 45),
    (((30, 40), (40, 45)), "effective", 30, 45),            # touching
    (((30, 40), (41, 45)), "conflict", None, None),
    (((30, 40), (35, 45), (42, 50)), "conflict", None, None),   # neighbours meet, ends do not
    (((30, None), (40, 45)), "effective", 30, None),
    (((None, None),), "effective", None, None),
    (((None, None), (40, 45)), "effective", None, None),
    (((30, 40),), "effective", 30, 40),
    (((30, 40), (30, 40)), "effective", 30, 40),
    (((None, 20), (30, None)), "conflict", None, None),
    (((None, 30), (30, None)), "effective", None, None),
    (((0, 0), (0, 5)), "effective", 0, 5),
])
def test_union_and_conflict(pairs, status, low, high):
    d = span(*pairs)
    assert (d.status, d.low, d.high) == (status, low, high)
    if status == "conflict":
        assert d.kind == "posted" and any(n.code == "conflict" for n in d.notes)
    else:
        assert not any(n.code == "conflict" for n in d.notes)


def test_conflict_only_among_comparable_of_the_effective_kind():
    d = run(obs("a", low=30, high=40), obs("b", low=50, high=60, basis=MarketBasis.TOTAL), obs("c", "estimate", 1, 2))
    assert (d.status, d.low, d.high) == ("effective", 30, 40)
    assert not d.kind_complete


def test_kind_complete_and_confident_flags():
    d = run(obs("a"), obs("b", basis=MarketBasis.TOTAL))
    assert d.status == "effective" and not d.kind_complete and d.kind_confident
    d = run(obs("a", conf="low"), obs("b", conf="medium"))
    assert d.kind_complete and not d.kind_confident
    d = run(obs("a", "posted"), obs("b", "estimate", conf="low", basis=MarketBasis.TOTAL))
    assert d.kind_complete and d.kind_confident        # only the effective kind counts


# --- to_basis -----------------------------------------------------------------------------------------

def test_to_basis_shape_exact_strings_and_canonical_json():
    d = run(obs("a", currency="USD", low=1, high=None, as_of=D0), obs("b", "estimate", 5, 9),
            conversions=[conv("1/3")])
    b = to_basis(d)
    assert set(b) == {"status", "kind", "low", "high", "kind_complete", "kind_confident", "observations", "notes"}
    assert (b["status"], b["kind"], b["low"], b["high"]) == ("effective", "posted", "1/3", None)
    assert b["observations"] == [{"id": "a", "kind": "posted", "comparable": True, "reason": None},
                                 {"id": "b", "kind": "estimate", "comparable": True, "reason": None}]
    blob = canon.canonical_json(b)
    assert b"." not in blob.replace(b"1/3", b"")
    assert canon.parse_exact(b["low"]) == Fraction(1, 3)


def test_to_basis_deterministic_across_observation_order():
    obs_ = [obs("a", low=30, high=40), obs("b", low=35, high=45, basis=MarketBasis.TOTAL), obs("c", "estimate", 1, 2)]
    blobs = {canon.canonical_json(to_basis(run(*p))) for p in itertools.permutations(obs_)}
    assert len(blobs) == 1


def test_to_basis_whole_numbers_are_ints_and_notes_listed():
    b = to_basis(run(obs("e", "estimate", 50, 60), obs("x", "estimate", 1, 2, basis=MarketBasis.TOTAL)))
    assert b["low"] == 50 and b["high"] == 60 and isinstance(b["low"], int)
    assert {"code": "estimate_only", "ref": None, "detail": "estimate"} in b["notes"]
    assert {"code": "incomparable", "ref": "x", "detail": "BASIS_MISMATCH"} in b["notes"]
    canon.canonical_json(b)


# --- acceptance: grid against an independent brute-force reference -------------------------------------

INTERVALS = [(2, 4), (4, 6), (3, None), (None, 3), (None, None), (7, 8), (5, 5)]


def _ref(items):
    """items: (kind, interval, comparable, conf). Brute force over integer points -1..10 (None = unbounded)."""
    if not items:
        return ("none", None, None, None, False, False)
    top = next(k for k in ("posted", "recruiter", "estimate") if any(i[0] == k for i in items))
    same = [i for i in items if i[0] == top]
    comp = [i for i in same if i[2]]
    complete = len(comp) == len(same)
    confident = all(i[3] != "low" for i in same)
    if not comp:
        return ("none", top, None, None, complete, confident)

    def has(iv, x):
        lo, hi = iv
        return (lo is None or lo <= x) and (hi is None or x <= hi)

    pts = range(-1, 11)
    if not any(all(has(i[1], x) for i in comp) for x in pts):
        return ("conflict", top, None, None, complete, confident)
    lows = [i[1][0] for i in comp]
    highs = [i[1][1] for i in comp]
    low = None if None in lows else min(lows)
    high = None if None in highs else max(highs)
    return ("effective", top, low, high, complete, confident)


def test_grid_agrees_with_reference():
    kinds = ("posted", "recruiter", "estimate")
    single = list(itertools.product(kinds, INTERVALS, (True, False), ("high", "low")))
    checked = 0
    for n in (0, 1, 2):
        for combo in itertools.combinations(single, n):
            observations = [obs(f"o{i}", k, iv[0], iv[1], basis=MarketBasis.BASE if c else MarketBasis.TOTAL, conf=cf)
                            for i, (k, iv, c, cf) in enumerate(combo)]
            d = run(*observations)
            got = (d.status, d.kind, d.low, d.high, d.kind_complete, d.kind_confident)
            exp = _ref(list(combo))
            if not combo:
                exp = ("none", None, None, None, False, False)
            assert got == exp, (combo, got, exp)
            checked += 1
    assert checked > 1000


def test_three_way_grid_same_kind_intervals():
    for combo in itertools.combinations_with_replacement(INTERVALS, 3):
        d = run(*[obs(f"o{i}", "posted", lo, hi) for i, (lo, hi) in enumerate(combo)])
        exp = _ref([("posted", iv, True, "high") for iv in combo])
        assert (d.status, d.low, d.high) == exp[:1] + exp[2:4], combo


# --- purity --------------------------------------------------------------------------------------------

def test_importing_derive_pulls_in_no_evaluation_record_or_validation():
    code = ("import sys, careeros.core.compensation.derive;"
            "bad=[m for m in ('careeros.core.evaluation.record','careeros.core.evaluation.integrity',"
            "'careeros.core.evaluation.service','careeros.core.validation') if m in sys.modules];"
            "print(bad); sys.exit(1 if bad else 0)")
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr
