"""Requirement outcome derivation against Career Memory (evaluation Task B, spec section 5). Invented data."""

from __future__ import annotations

import pytest
from helpers import make_workspace

from careeros.core.evaluation import derive
from careeros.core.evaluation.canon import canonical_json
from careeros.core.evaluation.model import Kind, Link, Outcome, ReqClass, Requirement, Strength
from careeros.core.memory import ops, store

NOW = "2026-10-10T00:00:00Z"
QUOTE = "stated in the test"


def req(rid="r1", kind="technology", term=None, years=None, links=(), cls="must") -> Requirement:
    return Requirement(rid, ReqClass(cls), Kind(kind), f"needs {term or years}", "quote", term, years, tuple(links))


def link(fact: str, strength="direct") -> Link:
    return Link(fact, Strength(strength), "because")


def run(root, *requirements, mutate=None):
    career = store.load_career(root)
    if mutate:
        mutate(career)
    return derive.derive(tuple(requirements), career, store.lexicon_for(root, career), NOW)


def one(root, requirement, **kw):
    return run(root, requirement, **kw).items[0]


def add(root, kind, **fields):
    return ops.add_fact(root, kind, fields, quote=QUOTE).id


# --- technology ----------------------------------------------------------------------------------

def test_technology_covered_by_an_active_skill(career):
    root, ids = career
    skill = add(root, "skill", name="Rust")
    d = one(root, req(term="Rust"))
    assert (d.outcome, d.facts, d.status_floor, d.reason) == (Outcome.COVERED, (skill,), "claimed", None)


def test_technology_matches_through_the_lexicon_alias_and_case(career):
    root, ids = career
    d = one(root, req(term="golang"))
    assert d.outcome is Outcome.COVERED and ids["skill_go"] in d.facts
    assert one(root, req(term="kubernetes")).outcome is Outcome.COVERED


def test_technology_covered_by_experience_technologies(career):
    root, ids = career
    d = one(root, req(term="Terraform"))
    assert d.outcome is Outcome.COVERED and ids["acme"] in d.facts and list(d.facts) == sorted(d.facts)
    assert one(root, req(term="Grafana")).facts and ids["globex"] in one(root, req(term="Grafana")).facts


def test_technology_covered_by_achievement_technologies(career):
    root, ids = career
    ach = add(root, "achievement", parent=ids["acme"], text="Wrote a Scala service for ingest.")
    d = one(root, req(term="Scala"))
    assert d.outcome is Outcome.COVERED and d.facts == (ach,)


def test_technology_covered_by_project_technologies(career):
    root, ids = career
    proj = add(root, "project", name="toolbox", text="A small Elixir tool for logs.")
    assert one(root, req(term="Elixir")).facts == (proj,)


def test_term_only_in_unrelated_achievement_text_is_not_covered(career):
    root, ids = career
    add(root, "achievement", parent=ids["acme"], text="Introduced Zephyrflow scheduling for the team.")
    assert one(root, req(term="Zephyrflow")).outcome is Outcome.GAP

    def blank(c):  # a lexicon technology named in the text but absent from `technologies`
        for fact in c.facts:
            if fact.kind == "achievement" and "Elixir" in str(fact.get("text")):
                fact.fm["technologies"] = []

    add(root, "achievement", parent=ids["acme"], text="Prototyped a chat service in Elixir.")
    assert one(root, req(term="Elixir"), mutate=blank).outcome is Outcome.GAP


def test_technology_not_found_in_title_or_other_fields_is_not_covered(career):
    root, ids = career
    # "Senior" is in a title, "Example" in a school name, "Open-source" in a project text
    for term in ("Senior", "Jabalpur", "Open-source", "Acme"):
        assert one(root, req(term=term)).outcome is Outcome.GAP


def test_retired_skill_does_not_cover(career):
    root, ids = career
    skill = add(root, "skill", name="Rust")
    ops.retire_fact(root, skill, "no longer used")
    d = one(root, req(term="Rust"))
    assert d.outcome is Outcome.GAP and d.facts == () and d.status_floor is None


def test_disputed_achievement_does_not_cover(career):
    root, ids = career
    ach = add(root, "achievement", parent=ids["acme"], text="Wrote a Scala service for ingest.")
    ops.dispute_fact(root, ach, "not accurate")
    assert one(root, req(term="Scala")).outcome is Outcome.GAP


def test_status_floor_follows_claimed_and_confirmed_support(career):
    root, ids = career
    skill = add(root, "skill", name="Rust")
    assert one(root, req(term="Rust")).status_floor == "claimed"
    ops.confirm_fact(root, skill, confirm=lambda _msg: True)
    d = one(root, req(term="Rust"))
    assert d.status_floor == "confirmed" and d.facts == (skill,)


def test_status_floor_is_the_weakest_of_the_listed_facts(career):
    root, ids = career
    strong = add(root, "skill", name="Rust")
    ops.confirm_fact(root, strong, confirm=lambda _msg: True)
    weak = add(root, "achievement", parent=ids["acme"], text="Wrote a Rust prototype.")
    d = one(root, req(term="Rust"))
    assert set(d.facts) == {strong, weak} and d.status_floor == "claimed"


def test_facts_are_capped_at_five_and_sorted(career):
    root, ids = career
    made = [add(root, "achievement", parent=ids["acme"], text=f"Wrote Rust module number {n}.") for n in range(7)]
    d = one(root, req(term="Rust"))
    assert d.outcome is Outcome.COVERED and len(d.facts) == 5
    assert list(d.facts) == sorted(d.facts) == sorted(made)[:5]


def test_technology_without_a_term_is_a_gap(career):
    root, ids = career
    assert one(root, req(term=None)).outcome is Outcome.GAP
    assert one(root, req(term="  ")).outcome is Outcome.GAP


# --- years ---------------------------------------------------------------------------------------

def test_years_within_derived_experience_are_covered(career):
    root, ids = career
    d = one(root, req(kind="years", years=3))
    assert d.outcome is Outcome.COVERED and set(d.facts) == {ids["acme"], ids["globex"]}
    assert d.status_floor == "claimed"
    assert one(root, req(kind="years", years=4)).outcome is Outcome.COVERED   # 54 months = 4 whole years


def test_years_above_derived_experience_are_a_gap(career):
    root, ids = career
    for years in (5, 10):
        d = one(root, req(kind="years", years=years))
        assert d.outcome is Outcome.GAP and d.facts == () and d.status_floor is None


def test_years_with_a_term_is_unverifiable_with_a_reason(career):
    root, ids = career
    d = one(root, req(kind="years", years=5, term="Go"))
    assert d.outcome is Outcome.UNVERIFIABLE and d.reason and "Go" in d.reason
    assert d.facts == () and d.status_floor is None
    assert one(root, req(kind="years", years=1, term="Go")).outcome is Outcome.UNVERIFIABLE


def test_years_without_a_usable_number_is_unverifiable(career):
    root, ids = career
    for years in (None, 0, -2):
        d = one(root, req(kind="years", years=years))
        assert d.outcome is Outcome.UNVERIFIABLE and d.reason


def test_years_use_the_union_of_overlapping_experiences(tmp_path):
    root = make_workspace(tmp_path / "ws")
    a = add(root, "experience", employer="Aco", title="Engineer", start="2020-01", end="2022-12")
    b = add(root, "experience", employer="Bco", title="Engineer", start="2021-01", end="2024-01")
    # the union is 2020-01..2024-01 = 48 months; a plain sum would be 71
    assert one(root, req(kind="years", years=4)).outcome is Outcome.COVERED
    d = one(root, req(kind="years", years=5))
    assert d.outcome is Outcome.GAP
    assert one(root, req(kind="years", years=4)).facts == tuple(sorted((a, b)))


def test_years_ignore_retired_experience(career):
    root, ids = career
    ops.retire_fact(root, ids["globex"], "test")
    assert one(root, req(kind="years", years=2)).outcome is Outcome.GAP        # 21 months
    d = one(root, req(kind="years", years=1))
    assert d.outcome is Outcome.COVERED and d.facts == (ids["acme"],)


def test_years_with_no_experience_is_a_gap(tmp_path):
    root = make_workspace(tmp_path / "ws")
    add(root, "skill", name="Go")
    assert one(root, req(kind="years", years=1)).outcome is Outcome.GAP


# --- title ---------------------------------------------------------------------------------------

def test_title_covered_by_experience_title_normalised(career):
    root, ids = career
    for term in ("Senior Software Engineer", "senior  software engineer", "SENIOR SOFTWARE ENGINEER."):
        d = one(root, req(kind="title", term=term))
        assert d.outcome is Outcome.COVERED and d.facts == (ids["acme"],)
    assert one(root, req(kind="title", term="Software Engineer 2")).facts == (ids["globex"],)


def test_title_covered_by_a_title_variant(tmp_path):
    root = make_workspace(tmp_path / "ws")
    exp = add(root, "experience", employer="Cco", title="Staff Engineer, Platform", start="2020-01", end="2021-01")
    assert one(root, req(kind="title", term="Staff Engineer")).facts == (exp,)
    assert one(root, req(kind="title", term="Staff Engineer, Platform")).facts == (exp,)


def test_title_covered_by_identity_headline(career):
    root, ids = career
    d = one(root, req(kind="title", term="Platform Engineer"))
    assert d.outcome is Outcome.COVERED and d.facts == (ids["identity"],)


def test_title_not_covered_by_substring_other_role_or_retired(career):
    root, ids = career
    for term in ("Software Engineer", "Principal Engineer", "Senior", "Engineer"):
        assert one(root, req(kind="title", term=term)).outcome is Outcome.GAP
    ops.retire_fact(root, ids["globex"], "test")
    assert one(root, req(kind="title", term="Software Engineer 2")).outcome is Outcome.GAP


# --- certification -------------------------------------------------------------------------------

def test_certification_covered_by_name_and_lexicon_alias(career):
    root, ids = career
    for term in ("Certified Kubernetes Administrator", "certified kubernetes administrator", "CKA"):
        d = one(root, req(kind="certification", term=term))
        assert d.outcome is Outcome.COVERED and d.facts == (ids["cka"],)


def test_certification_not_covered(career):
    root, ids = career
    for term in ("CKAD", "AWS Certified Developer", "Kubernetes"):
        assert one(root, req(kind="certification", term=term)).outcome is Outcome.GAP
    ops.retire_fact(root, ids["cka"], "lapsed")
    assert one(root, req(kind="certification", term="CKA")).outcome is Outcome.GAP


# --- education -----------------------------------------------------------------------------------

def test_education_covered_when_every_word_is_present(career):
    root, ids = career
    for term in ("Computer Science", "B.Tech. Computer Science", "computer science b tech"):
        d = one(root, req(kind="education", term=term))
        assert d.outcome is Outcome.COVERED and d.facts == (ids["edu"],)


def test_education_uses_degree_and_field(tmp_path):
    root = make_workspace(tmp_path / "ws")
    edu = add(root, "education", school="Example University", degree="BSc", field="Physics")
    assert one(root, req(kind="education", term="BSc Physics")).facts == (edu,)


def test_education_with_partial_words_is_a_gap(career):
    root, ids = career
    for term in ("Computer Science and Mathematics", "Master of Computer Science", "Example Institute", ""):
        assert one(root, req(kind="education", term=term)).outcome is Outcome.GAP
    ops.retire_fact(root, ids["edu"], "test")
    assert one(root, req(kind="education", term="Computer Science")).outcome is Outcome.GAP


# --- domain / other ------------------------------------------------------------------------------

def test_domain_and_other_are_never_covered_by_code(career):
    root, ids = career
    for kind in ("domain", "other"):
        d = one(root, req(kind=kind, term="Kubernetes"))
        assert d.outcome is Outcome.GAP and d.facts == ()


# --- links ---------------------------------------------------------------------------------------

def test_direct_link_on_a_domain_requirement_is_adjacent_not_covered(career):
    root, ids = career
    d = one(root, req(kind="domain", term="platform", links=[link(ids["platform"], "direct")]))
    assert d.outcome is Outcome.ADJACENT and d.facts == (ids["platform"],) and d.status_floor == "claimed"
    assert d.link_strength == "direct"


def test_direct_link_on_a_technology_the_memory_lacks_is_adjacent(career):
    root, ids = career
    d = one(root, req(term="Haskell", links=[link(ids["skill_go"], "direct")]))
    assert d.outcome is Outcome.ADJACENT and d.outcome is not Outcome.COVERED


def test_link_strength_does_not_change_the_outcome(career):
    root, ids = career
    out = {s: one(root, req(kind="other", links=[link(ids["k8s"], s)])) for s in ("direct", "adjacent")}
    assert out["direct"].outcome is out["adjacent"].outcome is Outcome.ADJACENT
    assert (out["direct"].link_strength, out["adjacent"].link_strength) == ("direct", "adjacent")
    assert out["direct"].facts == out["adjacent"].facts


@pytest.mark.parametrize("kind, term", [
    ("title", "Principal Engineer"), ("certification", "CKAD"), ("education", "Physics"),
])
def test_link_upgrades_gap_to_adjacent_for_every_kind(career, kind, term):
    root, ids = career
    assert one(root, req(kind=kind, term=term)).outcome is Outcome.GAP
    assert one(root, req(kind=kind, term=term, links=[link(ids["acme"])])).outcome is Outcome.ADJACENT


def test_link_on_years_gap_upgrades_but_years_with_term_stays_unverifiable(career):
    root, ids = career
    assert one(root, req(kind="years", years=10, links=[link(ids["acme"])])).outcome is Outcome.ADJACENT
    assert one(root, req(kind="years", years=2, term="Go", links=[link(ids["acme"])])).outcome is Outcome.UNVERIFIABLE


def test_link_on_a_covered_requirement_keeps_the_deterministic_facts(career):
    root, ids = career
    plain = one(root, req(term="Go"))
    linked = one(root, req(term="Go", links=[link(ids["k8s"], "adjacent")]))
    assert linked.outcome is Outcome.COVERED and linked.facts == plain.facts


def test_link_to_a_retired_fact_is_evl005_with_the_requirement_id(career):
    root, ids = career
    ops.retire_fact(root, ids["k8s"], "test")
    with pytest.raises(derive.DeriveError) as err:
        run(root, req("r1", term="Go"), req("r2", kind="domain", links=[link(ids["k8s"])]))
    assert err.value.code == "EVL005" and err.value.requirement_id == "r2"


def test_link_to_a_disputed_fact_is_evl005(career):
    root, ids = career
    ops.dispute_fact(root, ids["k8s"], "wrong")
    with pytest.raises(derive.DeriveError) as err:
        run(root, req(kind="domain", links=[link(ids["k8s"])]))
    assert err.value.code == "EVL005"


@pytest.mark.parametrize("fact", ["fct_doesnotexist", "evl_01HZZZZZZZZZZZZZZZZZZZZZZZ", "", "../../etc/passwd"])
def test_link_to_a_nonexistent_or_foreign_id_is_evl005(career, fact):
    root, ids = career
    with pytest.raises(derive.DeriveError) as err:
        run(root, req(term="Go", links=[link(fact)]))      # even a requirement that is covered
    assert err.value.code == "EVL005" and err.value.requirement_id == "r1"


# --- basis and determinism -----------------------------------------------------------------------

def test_basis_is_exact_and_independent_of_requirement_order(career):
    root, ids = career
    a, b, c = req("r1", term="Go"), req("r2", kind="years", years=10, cls="strong"), req("r3", kind="domain", cls="nice")
    first, second = run(root, a, b, c), run(root, c, a, b)
    assert canonical_json(first.basis.as_dict()) == canonical_json(second.basis.as_dict())
    data = first.basis.as_dict()
    assert data["basis_version"] == "2" and data["rules_version"] == "2" and data["requirement_count"] == 3
    assert [r["id"] for r in data["requirements"]] == ["r1", "r2", "r3"]
    r1 = data["requirements"][0]
    assert set(r1) == {"id", "class", "outcome", "facts", "status_floor"}
    assert r1["class"] == "must" and r1["outcome"] == "covered" and isinstance(r1["facts"], list)
    assert data["requirements"][1]["outcome"] == "gap" and data["requirements"][1]["status_floor"] is None


def test_derive_is_pure(career):
    root, ids = career
    before = {p: p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}
    run(root, req(term="Go"), req("r2", kind="title", term="Platform Engineer"))
    assert before == {p: p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


# --- fix round 1 -------------------------------------------------------------------------------

def test_technology_matching_does_not_collapse_punctuation(career):
    root, ids = career
    csharp = add(root, "skill", name="C#")
    dotnet = add(root, "skill", name=".NET")
    assert one(root, req(term="C#")).outcome is Outcome.COVERED and csharp in one(root, req(term="C#")).facts
    assert one(root, req(term=".NET")).outcome is Outcome.COVERED and dotnet in one(root, req(term=".NET")).facts
    for term in ("C++", "C", "NET", "net"):
        assert one(root, req(term=term)).outcome is Outcome.GAP, term


def test_technology_aliases_still_match(career):
    root, ids = career
    for term in ("k8s", "golang", "Postgres", "kubernetes"):
        assert one(root, req(term=term)).outcome is Outcome.COVERED, term


def test_achievement_under_inactive_parent_is_inactive(career):
    root, ids = career
    ach = add(root, "achievement", parent=ids["globex"], text="Wrote a Scala service for ingest.")
    assert one(root, req(term="Scala")).outcome is Outcome.COVERED
    ops.retire_fact(root, ids["globex"], "test")
    assert one(root, req(term="Scala")).outcome is Outcome.GAP
    with pytest.raises(derive.DeriveError) as err:
        run(root, req(kind="domain", links=[link(ach)]))
    assert err.value.code == "EVL005"


def test_achievement_under_disputed_experience_is_inactive(career):
    root, ids = career
    add(root, "achievement", parent=ids["acme"], text="Wrote a Scala service for ingest.")
    ops.dispute_fact(root, ids["acme"], "wrong")
    assert one(root, req(term="Scala")).outcome is Outcome.GAP


def test_summary_achievement_without_parent_stays_active(tmp_path):
    root = make_workspace(tmp_path / "ws")
    summary = ops.add_fact(root, "achievement", {"parent": None, "section": "summary", "text": "Wrote Scala tools."},
                           quote=QUOTE).id
    assert one(root, req(term="Scala")).facts == (summary,)


def test_years_facts_list_only_experiences_that_contributed(tmp_path):
    root = make_workspace(tmp_path / "ws")
    a = add(root, "experience", employer="Aco", title="Engineer", start="2020-01", end="2023-01")
    add(root, "experience", employer="Bco", title="Engineer", start="2024-05", end="2024-05")   # zero months
    d = one(root, req(kind="years", years=3))
    assert d.outcome is Outcome.COVERED and d.facts == (a,)
