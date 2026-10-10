"""Evaluation primitives: canonical JSON, text normalisation, rule/basis registries, model enums."""

from __future__ import annotations

import dataclasses
import decimal
import hashlib
import unicodedata
from fractions import Fraction

import pytest

from careeros.core.evaluation import canon, model, rules
from careeros.core.evaluation.canon import (
    canonical_json,
    exact,
    fixed4,
    normalize_text,
    parse_exact,
    result_canonical,
    result_sha256,
    sha256_hex,
)
from careeros.core.evaluation.rules import (
    BASIS_VERSIONS,
    RULES,
    RULES_VERSION,
    BasisSchema,
    UnsupportedVersion,
    basis_schema_for,
    rules_for,
)
from careeros.core.ids import is_valid_id

F = Fraction


# --- canonical_json ---------------------------------------------------------------------------

def test_canonical_json_ignores_insertion_and_nested_order():
    a = {"b": 1, "a": {"y": [1, 2], "x": None}}
    b = {"a": {"x": None, "y": [1, 2]}, "b": 1}
    assert canonical_json(a) == canonical_json(b) == b'{"a":{"x":null,"y":[1,2]},"b":1}'
    assert sha256_hex(canonical_json(a)) == sha256_hex(canonical_json(b))


def test_lists_keep_order():
    assert canonical_json([2, 1]) != canonical_json([1, 2])


def test_none_and_empty_containers_round_trip():
    assert canonical_json(None) == b"null"
    assert canonical_json([]) == b"[]"
    assert canonical_json({}) == b"{}"
    assert canonical_json({"k": None, "l": [], "m": {}}) == b'{"k":null,"l":[],"m":{}}'


def test_non_ascii_is_not_escaped():
    assert canonical_json({"k": "é→日"}) == '{"k":"é→日"}'.encode("utf-8")


def test_tuples_are_lists_and_bools_are_bools():
    assert canonical_json({"t": (1, 2), "b": True}) == b'{"b":true,"t":[1,2]}'


@pytest.mark.parametrize("bad", [0.8, 1.0, float("nan"), float("inf"), float("-inf")])
def test_floats_and_non_finite_are_forbidden(bad):
    with pytest.raises(TypeError):
        canonical_json({"x": bad})
    with pytest.raises(TypeError):
        canonical_json([[bad]])


@pytest.mark.parametrize("bad", [F(1, 2), {1, 2}, b"x", object(), {1: "a"}])
def test_other_types_and_non_string_keys_are_forbidden(bad):
    with pytest.raises(TypeError):
        canonical_json({"x": bad} if not isinstance(bad, dict) else bad)


# --- normalize_text ---------------------------------------------------------------------------

def _h(s):
    return sha256_hex(normalize_text(s).encode("utf-8"))


def test_line_endings_hash_equal():
    assert _h("a\r\nb\rc\nd") == _h("a\nb\nc\nd") == _h("a b c d")


def test_whitespace_runs_collapse():
    assert normalize_text("a  b\n\n c") == normalize_text("a b c") == "a b c"


def test_leading_and_trailing_whitespace_ignored():
    assert normalize_text("  \n a b \t\n") == "a b"


def test_nfc_and_nfd_hash_equal():
    composed, decomposed = "café", unicodedata.normalize("NFD", "café")
    assert composed != decomposed
    assert _h(composed) == _h(decomposed)


def test_case_is_kept():
    assert _h("Kubernetes") != _h("kubernetes")


# --- rules and basis registries ---------------------------------------------------------------

def test_rules_for_current_version():
    assert RULES_VERSION == "1"
    r = rules_for("1")
    assert r is RULES["1"]
    assert r.version == "1"
    assert dataclasses.is_dataclass(r)
    with pytest.raises(dataclasses.FrozenInstanceError):
        r.coverage_share = F(1)  # type: ignore[misc]


def test_rules_v1_constants_match_the_spec():
    r = rules_for("1")
    table = {
        "coverage_share": F(65, 100),
        "fit_share": F(35, 100),
        "tier_pursue": F(7, 10),
        "tier_stretch": F(1, 2),
        "tier_weak": F(3, 10),
        "claimed_share": F(1, 2),
        "unverifiable_share": F(1, 4),
        "unverifiable_share_severe": F(1, 2),
        "min_requirements": F(3),
    }
    for name, expected in table.items():
        assert getattr(r, name) == expected, name
    assert dict(r.class_weights) == {"must": 3, "strong": 2, "nice": 1}
    assert dict(r.credits) == {"covered": 1, "adjacent": F(1, 2), "gap": 0}
    assert dict(r.fit_weights) == {"high": 3, "medium": 2, "low": 1}
    assert dict(r.fit_credits) == {"yes": 1, "partial": F(1, 2), "no": 0}
    assert r.coverage_share + r.fit_share == 1


def test_every_rule_constant_is_a_fraction():
    r = rules_for("1")
    for f in dataclasses.fields(r):
        value = getattr(r, f.name)
        if f.name == "version":
            assert isinstance(value, str)
        elif isinstance(value, Fraction):
            continue
        else:
            assert all(isinstance(v, Fraction) for v in value.values()), f.name
            assert all(isinstance(k, str) for k in value), f.name


def test_rule_mappings_are_read_only():
    with pytest.raises(TypeError):
        rules_for("1").class_weights["must"] = F(9)  # type: ignore[index]


@pytest.mark.parametrize("bad", ["99", "", "0", 1, None])
def test_unknown_rules_version_is_unsupported(bad):
    with pytest.raises(UnsupportedVersion):
        rules_for(bad)  # type: ignore[arg-type]


@pytest.mark.parametrize("bad", ["99", "", 1, None])
def test_unknown_basis_version_is_unsupported(bad):
    with pytest.raises(UnsupportedVersion):
        basis_schema_for(bad)  # type: ignore[arg-type]


def test_unsupported_version_is_a_lookup_error_not_a_value_error():
    assert issubclass(UnsupportedVersion, LookupError)
    assert not issubclass(UnsupportedVersion, ValueError)
    assert UnsupportedVersion("x").args == ("x",)


def test_basis_schema_current_version():
    s = basis_schema_for("1")
    assert s is BASIS_VERSIONS["1"]
    assert s.version == "1"
    assert {"requirements", "fit", "cap_inputs", "requirement_count", "rubric_state"} <= set(s.keys)
    assert "caps" not in s.keys


def test_rules_and_basis_versions_are_independent(monkeypatch):
    v2 = BasisSchema(version="2", keys=("requirements",))
    monkeypatch.setitem(BASIS_VERSIONS, "2", v2)
    assert basis_schema_for("2") is v2
    assert rules_for("1").version == "1"
    with pytest.raises(UnsupportedVersion):
        rules_for("2")


# --- result_canonical / result_sha256 -----------------------------------------------------------

INVENTED = {
    "stamps": {"jd": "aa", "memory": "bb", "rubric": "cc", "rules": "1", "inputs": "dd"},
    "requirements": [{"id": "r1", "outcome": "covered", "facts": ["skl_a1b2c3d4e5"], "status_floor": "claimed"}],
    "coverage": 0.8,
    "fit": 0.8,
    "combined": 0.8,
    "caps": [],
    "tier": "pursue",
    "confidence": "low",
    "action_items": ["r2: ask"],
}
INVENTED_CANONICAL = (
    '{"action_items":["r2: ask"],"caps":[],"combined":"0.8000","confidence":"low","coverage":"0.8000",'
    '"fit":"0.8000","requirements":[{"facts":["skl_a1b2c3d4e5"],"id":"r1","outcome":"covered",'
    '"status_floor":"claimed"}],"stamps":{"inputs":"dd","jd":"aa","memory":"bb","rubric":"cc","rules":"1"},'
    '"tier":"pursue"}'
)
INVENTED_SHA256 = "e906f9d537dc5cb2a5f16a15f60ad33347f1f2fe9f35794133c09f77031b1657"


def test_result_canonical_fixed_point_scores():
    out = result_canonical(INVENTED)
    assert out["coverage"] == out["fit"] == out["combined"] == "0.8000"
    assert INVENTED["coverage"] == 0.8  # input untouched


@pytest.mark.parametrize(
    "value,text",
    [(0.8, "0.8000"), (1, "1.0000"), (0, "0.0000"), (F(13, 20), "0.6500"), (F(1, 3), "0.3333"),
     (F(2, 3), "0.6667"), (0.12345, "0.1235"), (F(1, 20000), "0.0001"), ("0.8000", "0.8000")],
)
def test_score_rounding(value, text):
    assert result_canonical({"coverage": value})["coverage"] == text


def test_score_keys_always_emitted_and_missing_equals_null():
    for name in ("coverage", "fit", "combined"):
        others = {k: None for k in ("coverage", "fit", "combined") if k != name}
        a = result_canonical({**others, name: None})
        b = result_canonical(others)
        assert a[name] is None and b[name] is None
        assert result_sha256({**others, name: None}) == result_sha256(others)
    assert result_canonical({}) == {"coverage": None, "fit": None, "combined": None}
    assert result_sha256({"fit": None}) == result_sha256({}) == result_sha256({"coverage": None})


def test_scores_outside_unit_interval_are_rejected():
    for bad in (-0.1, 1.0001, F(-1, 3), F(3, 2), 2, "1.5000"):
        with pytest.raises(ValueError):
            fixed4(bad)


@pytest.mark.parametrize(
    "value,text",
    [(F(1, 32), "0.0313"), (F(1, 3), "0.3333"), (F(2, 3), "0.6667"), (F(1, 20000), "0.0001"),
     (0, "0.0000"), (-0.0, "0.0000"), (1, "1.0000"), (0.8, "0.8000"), (1.0, "1.0000"), ("0.1235", "0.1235"),
     (F(1, 40000), "0.0000"), (0.00005, "0.0001")],
)
def test_fixed4_integer_half_up(value, text):
    assert fixed4(value) == text
    assert float(fixed4(value)) == float(text)


def test_fixed4_rejects_bad_types_and_non_finite():
    for bad in (True, None, [1], "0.8", "0.80000"):
        with pytest.raises((TypeError, ValueError)):
            fixed4(bad)
    for bad in (float("nan"), float("inf")):
        with pytest.raises(TypeError):
            fixed4(bad)


def test_fixed4_ignores_decimal_context():
    ctx = decimal.getcontext()
    old = ctx.prec
    try:
        ctx.prec = 3
        assert fixed4(F(1, 32)) == "0.0313"
        assert fixed4(0.12345) == "0.1235"
        assert result_canonical(INVENTED)["coverage"] == "0.8000"
        assert result_sha256(INVENTED) == INVENTED_SHA256
    finally:
        ctx.prec = old


@pytest.mark.parametrize(
    "value,enc",
    [(F(3), 3), (3, 3), (F(1, 2), "1/2"), (F(2, 4), "1/2"), (F(4, 2), 2), (F(0), 0), (F(-1, 2), "-1/2"),
     (F(1, -2), "-1/2"), (F(13, 20), "13/20")],
)
def test_exact_encoding_round_trips(value, enc):
    got = exact(value)
    assert got == enc and type(got) is type(enc)
    assert parse_exact(enc) == F(value)


def test_exact_rejects_bool_float_and_others():
    for bad in (True, 0.5, "1/2", None):
        with pytest.raises(TypeError):
            exact(bad)


@pytest.mark.parametrize("bad", ["0.5", "2/2", "1/0", " 1/2", "1/2 ", "2/4", "0/5", "+1/2", "01/2", "1/-2",
                                 "", "1", "a/b", True, 0.5, None, [1]])
def test_parse_exact_rejects_non_canonical(bad):
    with pytest.raises((TypeError, ValueError)):
        parse_exact(bad)


def test_normalize_strips_a_leading_bom_only():
    assert _h("\ufeffabc") == _h("abc")
    assert _h("\ufeffcaf\u00e9") == _h(unicodedata.normalize("NFD", "caf\u00e9"))
    assert _h("a\u200bc") != _h("ac")


def test_other_floats_are_rejected():
    with pytest.raises(TypeError):
        result_canonical({"coverage": 0.5, "extra": 0.25})
    with pytest.raises(TypeError):
        result_canonical({"requirements": [{"weight": 1.5}]})
    with pytest.raises(TypeError):
        result_canonical({"coverage": float("nan")})
    with pytest.raises(TypeError):
        result_canonical({"coverage": True})
    with pytest.raises(ValueError):
        result_canonical({"coverage": "high"})


def test_result_canonical_is_idempotent():
    once = result_canonical(INVENTED)
    assert result_canonical(once) == once


def test_result_sha256_hard_coded():
    assert canonical_json(result_canonical(INVENTED)).decode() == INVENTED_CANONICAL
    assert result_sha256(INVENTED) == INVENTED_SHA256
    assert result_sha256(INVENTED) == hashlib.sha256(INVENTED_CANONICAL.encode()).hexdigest()


def test_result_sha256_order_independent_and_sensitive():
    shuffled = dict(reversed(list(INVENTED.items())))
    assert result_sha256(shuffled) == result_sha256(INVENTED)
    assert result_sha256({**INVENTED, "tier": "stretch"}) != result_sha256(INVENTED)


def test_sha256_hex_is_lowercase_64():
    h = sha256_hex(b"")
    assert h == hashlib.sha256(b"").hexdigest() and len(h) == 64


# --- model enums --------------------------------------------------------------------------------

def test_enum_values_equal_spec_strings():
    assert [e.value for e in model.Outcome] == ["covered", "adjacent", "gap", "unverifiable"]
    assert [e.value for e in model.Tier] == ["pursue", "stretch", "weak", "skip", "unrated"]
    assert [e.value for e in model.Confidence] == ["high", "medium", "low"]
    assert [e.value for e in model.ReqClass] == ["must", "strong", "nice"]
    assert [e.value for e in model.Kind] == [
        "technology", "title", "certification", "education", "years", "domain", "other"]
    assert [e.value for e in model.Strength] == ["direct", "adjacent"]
    assert [e.value for e in model.Judgement] == ["yes", "partial", "no"]
    assert [e.value for e in model.Listing] == ["offered", "not_offered", "unknown"]
    assert [e.value for e in model.Verification] == ["verified", "unverified", "conflict"]
    assert [e.value for e in model.MarketBasis] == ["base", "total"]
    assert [e.value for e in model.SourceConfidence] == ["high", "medium", "low", "unknown"]


def test_job_facts_typed_fields():
    jf = model.JobFacts(range_basis=model.MarketBasis.BASE, floor_basis=model.MarketBasis.TOTAL,
                        comp_confidence=model.SourceConfidence.UNKNOWN,
                        floor_verification=model.Verification.CONFLICT,
                        need_verification=model.Verification.UNVERIFIED)
    assert jf.range_basis == "base" and jf.floor_verification == "conflict"


def test_enums_compare_equal_to_their_strings():
    assert model.Outcome.COVERED == "covered"
    assert str(model.Tier.PURSUE.value) == "pursue"
    assert model.Outcome("gap") is model.Outcome.GAP
    with pytest.raises(ValueError):
        model.Outcome("Covered")


def test_model_dataclasses_are_frozen_and_construct():
    req = model.Requirement(id="r1", cls=model.ReqClass.MUST, kind=model.Kind.TECHNOLOGY, text="t", quote="q",
                            term="Go", links=(model.Link(fact="skl_a1b2c3d4e5", strength=model.Strength.DIRECT,
                                                         why="w"),))
    assert req.years is None
    fit = model.FitJudgement(row="Role", judgement=model.Judgement.YES, quote=None)
    facts = model.JobFacts()
    cap = model.Cap(code="sponsorship", ceiling=model.Tier.WEAK, reason="r")
    item = model.ActionItem(ref="r2", message="ask")
    stamps = model.Stamps(jd="a", memory="b", rubric="c", rules="1", inputs="d")
    for obj in (req, fit, facts, cap, item, stamps, req.links[0]):
        assert dataclasses.is_dataclass(obj)
        field = dataclasses.fields(obj)[0].name
        with pytest.raises(dataclasses.FrozenInstanceError):
            setattr(obj, field, "x")
    assert [f.name for f in dataclasses.fields(stamps)] == ["jd", "memory", "rubric", "rules", "inputs"]


def test_evaluation_id_shape_is_the_shared_one():
    assert is_valid_id("evaluation", "evl_9k2m4q7xta")
    assert not is_valid_id("evaluation", "job_3f8c2m1xqa")


def test_modules_import_without_a_workspace():
    assert canon.canonical_json and rules.RULES
