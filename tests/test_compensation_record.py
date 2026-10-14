"""Compensation record: parsing, validation, canonical form and stamps (Task A). Pure; invented data only."""

from __future__ import annotations

import copy
import datetime
import subprocess
import sys
import textwrap
from fractions import Fraction

import pytest

from careeros.core.compensation import record, rules
from careeros.core.compensation.model import CompRecord, Conversion, Observation
from careeros.core.evaluation.model import MarketBasis
from careeros.core.workspace import join_frontmatter

JD = "Senior Engineer. Pay range is INR 30,00,000 to 45,00,000\nper year, paid monthly.\n"

SPEC_EXAMPLE = textwrap.dedent("""\
    ---
    schema: 1
    market: "India (home)"
    observations:
      - id: c1
        kind: posted
        low: 3000000
        high: 4500000
        currency: INR
        basis: base
        period: year
        source: "posting text"
        quote: "Pay range is INR 30,00,000 to 45,00,000"
        as_of: 2026-10-12
        confidence: high
    conversions:
      - {from: USD, to: INR, rate: "83.20", source: "bank page", as_of: 2026-10-10}
    ---
    Free notes (ignored by code).
    """)


def obs(**over):
    base = {"id": "c1", "kind": "recruiter", "low": 100, "high": 200, "currency": "USD", "basis": "base",
            "period": "year", "source": "call", "quote": None, "as_of": "2026-10-12", "confidence": "high"}
    base.update(over)
    return base


def conv(**over):
    base = {"from": "USD", "to": "INR", "rate": "83.20", "source": "bank", "as_of": "2026-10-10"}
    base.update(over)
    return base


def doc(observations=None, conversions=None, **top):
    fm = {"schema": 1, "market": "India (home)", "observations": [obs()] if observations is None else observations,
          "conversions": [] if conversions is None else conversions}
    fm.update(top)
    return join_frontmatter(fm, "notes\n")


def parse(text, jd=None):
    loaded = record.from_text(text)
    return record.parse(loaded)


def codes(text, jd=None):
    loaded = record.from_text(text)
    return sorted({i.code for i in record.validate(loaded, jd)})


def drop(d, *keys):
    d = dict(d)
    for k in keys:
        d.pop(k)
    return d


# --- the spec example and the empty record ---------------------------------------------------------

def test_spec_example_parses():
    loaded = record.from_text(SPEC_EXAMPLE)
    rec, issues = record.parse(loaded)
    assert issues == []
    assert rec.market == "India (home)"
    o = rec.observations[0]
    assert (o.id, o.kind, o.low, o.high, o.currency, o.basis, o.period) == (
        "c1", "posted", 3000000, 4500000, "INR", MarketBasis.BASE, "year")
    assert o.as_of == datetime.date(2026, 10, 12) and o.confidence == "high"
    c = rec.conversions[0]
    assert (c.from_currency, c.to_currency, c.rate, c.as_of) == ("USD", "INR", Fraction("83.20"), datetime.date(2026, 10, 10))
    assert rec.notes == "Free notes (ignored by code).\n"
    assert record.validate(loaded, JD) == []


def test_empty_observations_parse():
    rec, issues = parse(doc(observations=[]))
    assert issues == [] and rec.observations == () and rec.conversions == ()


def test_conversions_key_may_be_absent():
    fm_text = "---\nschema: 1\nmarket: null\nobservations: []\n---\n"
    rec, issues = parse(fm_text)
    assert issues == [] and rec.conversions == ()


def test_dates_as_strings_or_yaml_dates_agree():
    a, _ = parse(doc(observations=[obs(as_of="2026-10-12")]))
    b, _ = parse(doc(observations=[obs(as_of=datetime.date(2026, 10, 12))]))
    assert a == b and record.canonical(a) == record.canonical(b)


# --- stamps ---------------------------------------------------------------------------------------

def test_absent_skeleton_and_empty_records_share_one_stamp():
    absent = record.stamp(None, None)
    skel = record.from_text(record.skeleton("2026-10-14T00:00:00Z").decode())
    skel_rec, issues = record.parse(skel)
    assert issues == []
    other, _ = parse(doc(observations=[], conversions=[conv()], market="Somewhere"))
    assert record.stamp(skel_rec, None) == absent == record.stamp(other, None)


def test_changing_any_observation_field_changes_the_stamp():
    base, _ = parse(doc(observations=[obs(kind="posted", quote="q")]))
    ref = record.stamp(base, None)
    changes = {"id": "c2", "kind": "estimate", "low": 101, "high": 201, "currency": "EUR", "basis": "total",
               "period": "month", "source": "other", "quote": "q2", "as_of": "2026-10-13", "confidence": "low"}
    for key, value in changes.items():
        changed, issues = parse(doc(observations=[obs(**{"kind": "posted", "quote": "q", key: value})]))
        assert issues == [], (key, issues)
        assert record.stamp(changed, None) != ref, key


def test_stamp_covers_market_and_conversions_when_observations_exist():
    a, _ = parse(doc())
    b, _ = parse(doc(market="Other"))
    c, _ = parse(doc(conversions=[conv()]))
    assert len({record.stamp(a, None), record.stamp(b, None), record.stamp(c, None)}) == 3


def test_key_order_does_not_change_the_stamp():
    a, _ = parse(doc(observations=[obs(id="a"), obs(id="b", low=1)]))
    b, _ = parse(doc(observations=[obs(id="b", low=1), obs(id="a")]))
    text = "---\nobservations:\n" + "\n".join(
        f"  - {{id: {i}, kind: recruiter, low: 100, high: 200, currency: USD, basis: base, period: year, source: call,"
        f" quote: null, as_of: 2026-10-12, confidence: high}}" for i in ("a",)) + \
        "\nmarket: India (home)\nschema: 1\n---\n"
    assert record.stamp(a, None) == record.stamp(b, None)
    c, issues = parse(text)
    d, _ = parse(doc(observations=[obs(id="a")]))
    assert issues == [] and record.stamp(c, None) == record.stamp(d, None)


def test_unparseable_file_hashes_its_normalised_text():
    s1 = record.stamp(None, "garbage  text\n")
    assert s1 == record.stamp(None, "garbage text")           # whitespace-normalised
    assert s1 != record.stamp(None, "garbage texts")
    assert s1 != record.stamp(None, None)


def test_rate_spelling_does_not_change_the_stamp():
    a, _ = parse(doc(conversions=[conv(rate="83.20")]))
    b, _ = parse(doc(conversions=[conv(rate="83.2")]))
    assert record.stamp(a, None) == record.stamp(b, None)


# --- canonical form -------------------------------------------------------------------------------

def test_canonical_form():
    rec, issues = parse(doc(observations=[obs(id="b", as_of=datetime.date(2026, 10, 12)), obs(id="a")],
                            conversions=[conv(**{"from": "USD", "to": "INR"}), conv(**{"from": "EUR", "to": "INR", "rate": "90"}),
                                         conv(**{"from": "INR", "to": "USD", "rate": "0.5"})]))
    assert issues == []
    c = record.canonical(rec)
    assert [o["id"] for o in c["observations"]] == ["a", "b"]
    assert [(x["from"], x["to"]) for x in c["conversions"]] == [("EUR", "INR"), ("INR", "USD"), ("USD", "INR")]
    assert c["market"] == "India (home)"
    assert c["observations"][0]["as_of"] == "2026-10-12"
    assert type(c["observations"][0]["low"]) is int
    assert [x["rate"] for x in c["conversions"]] == ["90", "1/2", "416/5"]


def test_canonical_omits_market_without_observations():
    rec, _ = parse(doc(observations=[]))
    assert "market" not in record.canonical(rec)


def test_canonical_keeps_null_bounds():
    rec, _ = parse(doc(observations=[obs(low=None)]))
    assert record.canonical(rec)["observations"][0]["low"] is None


# --- range grid -----------------------------------------------------------------------------------

@pytest.mark.parametrize("low,high", [(0, 0), (0, 5), (None, 5), (5, None), (None, None), (5, 5)])
def test_valid_ranges(low, high):
    rec, issues = parse(doc(observations=[obs(low=low, high=high)]))
    assert issues == []
    assert (rec.observations[0].low, rec.observations[0].high) == (low, high)


@pytest.mark.parametrize("low,high", [
    (-1, 5), (5, 4), (None, -1), (True, 5), (5, False), (1.5, 5), (5, 6.0), ("100", 200), (100, "200"), ([1], 5),
])
def test_invalid_ranges(low, high):
    assert codes(doc(observations=[obs(low=low, high=high)])) == ["CMP003"]


def test_missing_bound_is_cmp003_not_zero():
    assert codes(doc(observations=[drop(obs(), "low")])) == ["CMP003"]
    assert codes(doc(observations=[drop(obs(), "high")])) == ["CMP003"]


# --- evidence by kind -----------------------------------------------------------------------------

def test_posted_quote_must_be_in_jd():
    ok = obs(kind="posted", quote="Pay range is INR 30,00,000 to 45,00,000")
    assert codes(doc(observations=[ok]), JD) == []
    assert codes(doc(observations=[dict(ok, quote="Pay range is INR 99")]), JD) == ["CMP004"]
    assert codes(doc(observations=[ok]), None) == ["CMP004"]


@pytest.mark.parametrize("quote", [None, "", "   "])
def test_posted_without_quote(quote):
    assert codes(doc(observations=[obs(kind="posted", quote=quote)]), JD) == ["CMP004"]
    assert codes(doc(observations=[drop(obs(kind="posted"), "quote")]), JD) == ["CMP004"]


def test_quote_reflowed_and_nfc_still_found():
    reflowed = obs(kind="posted", quote="to 45,00,000   per\nyear")
    assert codes(doc(observations=[reflowed]), JD) == []
    jd = "Café role pays 10 per year"
    nfd = obs(kind="posted", quote="Café role pays")
    assert codes(doc(observations=[nfd]), jd) == []
    assert codes(doc(observations=[obs(kind="posted", quote="café role")]), jd) == ["CMP004"]   # case kept


def test_quote_not_required_for_other_kinds():
    for kind in ("recruiter", "estimate"):
        assert codes(doc(observations=[obs(kind=kind)]), None) == []
        assert codes(doc(observations=[drop(obs(kind=kind), "quote")]), None) == []


def test_non_string_quote_is_cmp002():
    assert codes(doc(observations=[obs(quote=5)])) == ["CMP002"]


@pytest.mark.parametrize("key", ["source", "as_of"])
def test_missing_source_or_as_of_is_cmp006(key):
    assert codes(doc(observations=[drop(obs(), key)])) == ["CMP006"]
    assert codes(doc(observations=[obs(**{key: None})])) == ["CMP006"]
    assert codes(doc(conversions=[drop(conv(), key)])) == ["CMP006"]


def test_blank_source_is_cmp006():
    assert codes(doc(observations=[obs(source="  ")])) == ["CMP006"]


@pytest.mark.parametrize("field,value", [
    ("confidence", "certain"), ("confidence", None), ("kind", "rumour"), ("basis", "gross"), ("period", "week"),
    ("currency", ""), ("currency", 5), ("source", 5), ("kind", True),
])
def test_enum_and_text_errors_are_cmp002(field, value):
    expected = "CMP006" if field == "source" and value == "" else "CMP002"
    assert codes(doc(observations=[obs(**{field: value})])) == [expected]


def test_missing_enum_fields_are_cmp002():
    for key in ("id", "kind", "currency", "basis", "period", "confidence"):
        assert codes(doc(observations=[drop(obs(), key)])) == ["CMP002"], key


@pytest.mark.parametrize("value", [
    "2026-13-01", "12/10/2026", "2026-10-12T10:00:00", "20261012", "2026-1-2", 20261012, 1.5, True,
    datetime.datetime(2026, 10, 12, 10, 0, 0),
])
def test_bad_dates_are_cmp002(value):
    assert codes(doc(observations=[obs(as_of=value)])) == ["CMP002"]
    assert codes(doc(conversions=[conv(as_of=value)])) == ["CMP002"]


def test_datetime_in_yaml_text_is_cmp002():
    text = doc().replace("as_of: '2026-10-12'", "as_of: 2026-10-12 10:00:00")
    assert text != doc()
    assert codes(text) == ["CMP002"]


# --- conversions ----------------------------------------------------------------------------------

def test_duplicate_pair_even_with_equal_rates():
    assert codes(doc(conversions=[conv(), conv()])) == ["CMP005"]
    assert codes(doc(conversions=[conv(), conv(rate="90")])) == ["CMP005"]


def test_inverse_pair_is_legal():
    rec, issues = parse(doc(conversions=[conv(), conv(**{"from": "INR", "to": "USD", "rate": "0.012"})]))
    assert issues == [] and len(rec.conversions) == 2


def test_unquoted_float_rate_says_to_quote():
    text = doc(conversions=[conv(rate="83.20")]).replace("rate: '83.20'", "rate: 83.20")
    assert "rate: 83.20" in text
    issues = record.validate(record.from_text(text), None)
    assert [i.code for i in issues] == ["CMP006"]
    assert "quote" in issues[0].fix.lower()
    assert codes(doc(conversions=[conv(rate=5)])) == ["CMP006"]


@pytest.mark.parametrize("rate", ["0", "0.0", "-1", "-83.2", "abc", "", " ", "1e3", "1/2", "83,2", ".5", "5.", "NaN", "inf",
                                  "8" * 100, None, True, [1]])
def test_bad_rates(rate):
    assert codes(doc(conversions=[conv(rate=rate)])) == ["CMP006"]


def test_good_rates():
    for r, expected in [("83.20", Fraction("83.2")), ("1", Fraction(1)), ("0.0001", Fraction(1, 10000)), ("007.5", Fraction("7.5"))]:
        rec, issues = parse(doc(conversions=[conv(rate=r)]))
        assert issues == [] and rec.conversions[0].rate == expected


def test_conversion_currency_codes_and_text():
    assert codes(doc(conversions=[conv(source="")])) == ["CMP006"]
    assert codes(doc(conversions=[conv(**{"from": ""})])) == ["CMP002"]
    assert codes(doc(conversions=[conv(to=3)])) == ["CMP002"]
    assert codes(doc(conversions=[drop(conv(), "from")])) == ["CMP002"]


def test_currency_is_normalised():
    rec, _ = parse(doc(observations=[obs(currency=" usd ")], conversions=[conv(**{"from": "usd", "to": " inr"})]))
    assert rec.observations[0].currency == "USD"
    assert (rec.conversions[0].from_currency, rec.conversions[0].to_currency) == ("USD", "INR")


# --- ids ------------------------------------------------------------------------------------------

def test_duplicate_ids():
    assert codes(doc(observations=[obs(), obs()])) == ["CMP005"]


def test_empty_ids():
    assert codes(doc(observations=[obs(id="")])) == ["CMP002"]
    assert codes(doc(observations=[obs(id="  ")])) == ["CMP002"]


@pytest.mark.parametrize("ident", ["x", "1", "Zażółć gęślą", "a b", "c1/α", "-", "null"])
def test_any_non_empty_string_is_an_id(ident):
    assert codes(doc(observations=[obs(id=ident)])) == []


def test_non_string_id_is_cmp002():
    assert codes(doc(observations=[obs(id=5)])) == ["CMP002"]


# --- market ---------------------------------------------------------------------------------------

def test_market_null_only_without_observations():
    assert codes(doc(observations=[], market=None)) == []
    assert codes(doc(market=None)) == ["CMP002"]
    assert codes(doc(market="")) == ["CMP002"]
    assert codes(doc(market=5)) == ["CMP002"]


# --- structure and YAML hazards ---------------------------------------------------------------------

def test_unknown_keys_anywhere():
    assert codes(doc(extra=1)) == ["CMP001"]
    assert codes(doc(observations=[obs(extra=1)])) == ["CMP001"]
    assert codes(doc(conversions=[conv(extra=1)])) == ["CMP001"]


def test_missing_top_level_keys():
    assert codes("---\nschema: 1\nobservations: []\n---\n") == ["CMP001"]
    assert codes("---\nschema: 1\nmarket: null\n---\n") == ["CMP001"]


@pytest.mark.parametrize("schema", [2, 0, "1", None, True, 1.0])
def test_unsupported_schema(schema):
    assert codes(doc(schema=schema)) == ["CMP001"]


def test_missing_schema():
    assert codes("---\nmarket: null\nobservations: []\n---\n") == ["CMP001"]


@pytest.mark.parametrize("observations,conversions", [
    ({"a": 1}, []), (5, []), ("x", []), ([], {"a": 1}), ([], 5), (["str"], []), ([], [5]), ([None], []), ([[1]], []),
])
def test_wrong_container_types_are_cmp001(observations, conversions):
    text = join_frontmatter({"schema": 1, "market": "m", "observations": observations, "conversions": conversions}, "")
    assert codes(text) == ["CMP001"]


def test_duplicate_yaml_keys_are_cmp001():
    text = "---\nschema: 1\nmarket: null\nmarket: other\nobservations: []\n---\n"
    with pytest.raises(record.RecordError) as err:
        record.from_text(text)
    assert err.value.code == "CMP001"
    nested = ("---\nschema: 1\nmarket: x\nobservations:\n  - {id: a, id: b, kind: recruiter}\n---\n")
    with pytest.raises(record.RecordError) as err:
        record.from_text(nested)
    assert err.value.code == "CMP001"


def test_tabs_are_cmp001():
    with pytest.raises(record.RecordError) as err:
        record.from_text("---\nschema: 1\nobservations:\n\t- x\n---\n")
    assert err.value.code == "CMP001"


@pytest.mark.parametrize("text", ["", "no frontmatter\n", "---\nschema: 1\n", "---\n- a\n- b\n---\n", "---\njust text\n---\n",
                                  "---\n---\n", "﻿---\nschema: 1\n---\n", "---\n&a [*a]\n---\n", "---\nk: *x\n---\n"])
def test_structural_garbage_never_crashes(text):
    try:
        rec = record.from_text(text)
    except record.RecordError as err:
        assert err.code == "CMP001"
        return
    issues = record.validate(rec, None)
    assert issues and all(i.code == "CMP001" for i in issues)


def test_empty_frontmatter_block_is_cmp001():
    rec = record.from_text("---\n---\n")
    assert {i.code for i in record.validate(rec, None)} == {"CMP001"}


def test_huge_and_deep_inputs_never_crash():
    big = "---\nschema: 1\nmarket: null\nobservations: []\nconversions: []\n" + "# " + "x" * 1_000_000 + "\n---\n"
    rec, issues = record.parse(record.from_text(big))
    assert issues == [] and rec is not None
    deep = "---\n" + "a: " + "[" * 5000 + "]" * 5000 + "\n---\n"
    with pytest.raises(record.RecordError) as err:
        record.from_text(deep)
    assert err.value.code == "CMP001"
    deep2 = "---\nschema: 1\nmarket: null\nobservations: " + "[" * 100_000 + "\n---\n"
    with pytest.raises(record.RecordError):
        record.from_text(deep2)
    many = "---\nschema: 1\nmarket: null\nobservations:\n" + "  - {}\n" * 20000 + "---\n"
    assert {i.code for i in record.validate(record.from_text(many), None)} <= {"CMP001", "CMP002", "CMP003", "CMP006", "CMP005"}
    huge_int = "---\nschema: 1\nmarket: x\nobservations:\n  - {id: a, low: " + "9" * 6000 + "}\n---\n"
    try:
        r = record.from_text(huge_int)
    except record.RecordError as err:
        assert err.code == "CMP001"
    else:
        assert record.validate(r, None)


def test_nul_and_invalid_utf8(tmp_path):
    p = tmp_path / "compensation.md"
    p.write_bytes(b"---\nschema: 1\n\xff\xfe\n---\n")
    with pytest.raises(record.RecordError) as err:
        record.load(p, tmp_path)
    assert err.value.code == "CMP001"
    p.write_bytes(b"---\nschema: 1\nmarket: \x00\n---\n")
    with pytest.raises(record.RecordError):
        record.load(p, tmp_path)


def test_load_reads_a_file_and_reports_rel(tmp_path):
    job = tmp_path / "jobs" / "discovered" / "x"
    job.mkdir(parents=True)
    p = job / "compensation.md"
    p.write_bytes(SPEC_EXAMPLE.encode())
    loaded = record.load(p, tmp_path)
    assert loaded.rel == "jobs/discovered/x/compensation.md"
    assert loaded.raw == SPEC_EXAMPLE.encode()
    rec, issues = record.parse(loaded)
    assert issues == [] and rec is not None
    assert all(i.path == loaded.rel for i in record.validate(loaded, "nothing"))
    with pytest.raises(record.RecordError) as err:
        record.load(job / "missing.md", tmp_path)
    assert err.value.code == "CMP001"
    with pytest.raises(record.RecordError) as err:
        record.load(job, tmp_path)                     # a directory
    assert err.value.code == "CMP001"


def test_issue_shape_and_collection():
    text = doc(observations=[obs(low=-1, kind="bad", id="a"), obs(id="a", source=None)],
               conversions=[conv(rate="x"), conv()])
    issues = record.validate(record.from_text(text), None)
    assert {i.code for i in issues} == {"CMP002", "CMP003", "CMP005", "CMP006"}
    for i in issues:
        assert i.severity == "error" and i.path and i.message and i.fix
    rec, again = record.parse(record.from_text(text))
    assert rec is None and again == [i for i in issues]


def test_validate_with_parse_errors_does_not_check_quotes():
    assert codes(doc(observations=[obs(kind="posted", quote="zzz", low=-1)]), JD) == ["CMP003"]


# --- model, purity and imports ------------------------------------------------------------------------

def test_models_are_frozen():
    rec, _ = parse(doc(conversions=[conv()]))
    assert isinstance(rec, CompRecord) and isinstance(rec.observations[0], Observation)
    assert isinstance(rec.conversions[0], Conversion)
    for obj in (rec, rec.observations[0], rec.conversions[0]):
        with pytest.raises(Exception):
            obj.notes = "x"                                                 # noqa
    assert isinstance(rec.observations, tuple) and isinstance(rec.conversions, tuple)


def test_parse_is_pure_and_repeatable():
    loaded = record.from_text(SPEC_EXAMPLE)
    fm = copy.deepcopy(loaded.fm)
    first = record.parse(loaded)
    assert record.parse(loaded) == first and loaded.fm == fm


def test_rules_constants():
    assert (rules.COMPENSATION_FILE, rules.SCHEMA, rules.KINDS, rules.FX_WINDOW_DAYS, rules.OBSERVATION_STALE_DAYS) == (
        "compensation.md", 1, ("posted", "recruiter", "estimate"), 30, 90)


def test_skeleton_parses_to_the_empty_record():
    raw = record.skeleton("2026-10-14T09:00:00Z")
    text = raw.decode()
    assert "market: null" in text and "observations: []" in text and "conversions: []" in text
    assert text.rstrip("\n").splitlines()[-1].strip() and len(text.rstrip("\n").splitlines()[-1].splitlines()) == 1
    rec, issues = record.parse(record.from_text(text))
    assert issues == [] and rec.market is None and rec.observations == ()
    assert record.skeleton("2026-10-14T09:00:00Z") == raw


def test_importing_the_package_pulls_in_no_evaluation_record_or_validation():
    code = ("import sys, careeros.core.compensation.record, careeros.core.compensation.rules, careeros.core.compensation.model;"
            "bad=[m for m in ('careeros.core.evaluation.record','careeros.core.evaluation.integrity',"
            "'careeros.core.evaluation.service','careeros.core.validation') if m in sys.modules];"
            "print(bad); sys.exit(1 if bad else 0)")
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr


# --- fix round 1 -----------------------------------------------------------------------------------

def _doc_with(old, new):
    text = doc()
    assert old in text, old
    return text.replace(old, new)


@pytest.mark.parametrize("old,field", [
    ("source: call", "source"), ("id: c1", "id"), ("currency: USD", "currency"), ("market: India (home)", "market"),
])
def test_lone_surrogate_in_observation_text_is_cmp002(old, field):
    text = _doc_with(old, f'{old.split(":")[0]}: "\\ud800"')
    issues = record.validate(record.from_text(text), JD)
    assert {i.code for i in issues} == {"CMP002"}
    assert any(field in i.message for i in issues)


def test_lone_surrogate_in_quote_and_conversion_fields():
    assert codes(doc(observations=[obs(kind="posted", quote="a\ud800b")]), JD) == ["CMP002"]
    for key in ("source", "from", "to"):
        assert codes(doc(conversions=[conv(**{key: "x\udfff"})])) == ["CMP002"]


def test_stamp_and_canonical_never_raise():
    assert record.stamp(None, "raw \ud800 text")
    assert record.stamp(None, "raw \ud800 text") != record.stamp(None, "raw \udc00 text")
    bad = CompRecord("m\ud800", (Observation("a\ud800", "recruiter", 1, 2, "USD", MarketBasis.BASE, "year", "s", None,
                                             datetime.date(2026, 1, 1), "high"),), (), "")
    assert record.stamp(bad, None)
    assert record.canonical(bad)


def test_crlf_file_parses_like_lf():
    lf = SPEC_EXAMPLE
    crlf = lf.replace("\n", "\r\n")
    cr = lf.replace("\n", "\r")
    a = record.from_text(lf)
    for variant in (crlf, cr):
        b = record.from_text(variant, raw=variant.encode())
        assert b.raw == variant.encode()
        ra, ia = record.parse(a)
        rb, ib = record.parse(b)
        assert ib == [] and record.canonical(ra) == record.canonical(rb)
        assert record.stamp(ra, None) == record.stamp(rb, None)
    bad = "---\nschema: 1\nmarket: x\nobservations: 5\n---\nnotes\n"
    assert record.stamp(None, bad) == record.stamp(None, bad.replace("\n", "\r\n"))


def test_crlf_file_via_load(tmp_path):
    p = tmp_path / "compensation.md"
    p.write_bytes(SPEC_EXAMPLE.replace("\n", "\r\n").encode())
    loaded = record.load(p, tmp_path)
    assert loaded.raw == SPEC_EXAMPLE.replace("\n", "\r\n").encode()
    rec, issues = record.parse(loaded)
    assert issues == [] and rec is not None


@pytest.mark.parametrize("value", ["2026-02-30", "0000-01-01", "2026-13-45", "2026-02-30 10:00:00"])
@pytest.mark.parametrize("quoted", [False, True])
def test_impossible_dates_are_cmp002_not_cmp001(value, quoted):
    shown = f'"{value}"' if quoted else value
    obs_text = _doc_with("as_of: '2026-10-12'", f"as_of: {shown}")
    assert codes(obs_text) == ["CMP002"]
    conv_text = doc(conversions=[conv()]).replace("as_of: '2026-10-10'", f"as_of: {shown}")
    assert "as_of: " + shown in conv_text
    assert codes(conv_text) == ["CMP002"]


@pytest.mark.parametrize("spelling", ["010", "0500", "1:30", "0x10", "1_000", "0b11", "0o7", "00", "-010"])
def test_odd_integer_spellings_are_cmp003(spelling):
    for key in ("low", "high"):
        text = join_frontmatter({"schema": 1, "market": "m", "observations": [obs(low=None, high=None)],
                                 "conversions": []}, "").replace(f"{key}: null", f"{key}: {spelling}")
        issues = record.validate(record.from_text(text), None)
        assert {i.code for i in issues} == {"CMP003"}, spelling
        assert "plain" in issues[0].fix


@pytest.mark.parametrize("spelling,value", [("0", 0), ("7", 7), ("+7", 7), ("3000000", 3000000)])
def test_plain_integer_spellings_are_accepted(spelling, value):
    text = join_frontmatter({"schema": 1, "market": "m", "observations": [obs(low=None, high=None)],
                             "conversions": []}, "").replace("low: null", f"low: {spelling}")
    rec, issues = record.parse(record.from_text(text))
    assert issues == [] and rec.observations[0].low == value


def test_currency_uses_the_evaluation_normalisation_and_accepts_odd_codes():
    from careeros.core.evaluation.markets import norm_currency
    for raw in (" usd ", "Rs.", "уsd", "US D", "₹", "1234"):
        rec, issues = parse(doc(observations=[obs(currency=raw)]))
        assert issues == [] and rec.observations[0].currency == norm_currency(raw)
    cyr, _ = parse(doc(observations=[obs(currency="уsd")]))
    lat, _ = parse(doc(observations=[obs(currency="usd")]))
    assert cyr.observations[0].currency != lat.observations[0].currency       # not validated against ISO-4217


def test_invalid_stamp_is_pinned():
    import hashlib
    from careeros.core.evaluation.canon import normalize_text
    text = "---\nschema: 1\nobservations: 5\n---\nbody  here\n"
    norm = normalize_text(text)
    assert record.stamp(None, text) == hashlib.sha256(("invalid:" + norm).encode()).hexdigest()
    assert record.stamp(None, text) != hashlib.sha256(norm.encode()).hexdigest()


def test_unparseable_file_through_stamp():
    text = "---\nschema: 1\nschema: 2\n---\n"
    with pytest.raises(record.RecordError):
        record.from_text(text)
    assert record.stamp(None, text) != record.stamp(None, None)
    assert record.stamp(None, text) != record.stamp(None, text + "x")


def test_valid_alias_is_rejected():
    text = "---\nschema: 1\nmarket: null\nobservations: []\nconversions: []\nx: &a 1\ny: *a\n---\n"
    with pytest.raises(record.RecordError) as err:
        record.from_text(text)
    assert err.value.code == "CMP001"
