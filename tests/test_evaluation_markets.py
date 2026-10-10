"""Rubric and `## Markets` parsing and verification (evaluation Task B). Invented profile text only."""

from __future__ import annotations

import pytest

from careeros.core.evaluation import markets, rubric
from careeros.core.evaluation.model import JobFacts, MarketBasis, Verification

FOLD = "## ── HISTORY (on demand; do not read past this line) ──"

TEMPLATE = f"""# Example Career Profile

## Markets
*(where you'd work and the terms for each.)*

| Market | Min base (per year) | Sponsorship | Priority |
|--------|---------------------|-------------|----------|
| Atlantis (home) | ₹35,00,000 | not needed | 1 |
| Singapore | SGD 8000 | required | 2 |
| Elsewhere | TBD — derive from home floor | preferred | 3 |

## Scoring rubric (use when rating job listings 0–10)
| Signal | Weight |
|--------|--------|
| Role title matches target roles | High |
| Primary language(s) in stack | High |
| Distributed systems / platform / infra work | High |
| Location match or remote-friendly | Medium |
| Seniority level matches target | High |
| Company type matches preference | Medium |
| Compensation signals above the minimum for that listing's market | Medium |
| Listing is in a market from `## Markets` and its sponsorship need is met (offered, or not needed) | Medium |
| Sponsorship is required but the listing says none, or demands existing right to work | Negative — deprioritize |

{FOLD}
older notes
"""


def _rubric(*rows: str, heading: str = "## Scoring rubric") -> str:
    body = "\n".join(rows)
    return f"# P\n\n{heading}\n| Signal | Weight |\n|---|---|\n{body}\n\n{FOLD}\n"


# --- rubric ------------------------------------------------------------------------------------

def test_template_rubric_has_six_fit_rows_in_order():
    r = rubric.parse(TEMPLATE)
    assert r.state == "ok" and r.usable and r.problems == ()
    assert [row.weight for row in r.rows] == [3, 3, 3, 2, 3, 2]
    assert r.rows[0].label == "Role title matches target roles"
    labels = " ".join(row.label.casefold() for row in r.rows)
    assert "compensation" not in labels and "sponsorship" not in labels


def test_compensation_sponsorship_and_negative_rows_are_excluded_case_insensitively():
    text = _rubric("| Fun work | High |", "| COMPENSATION is great | Medium |", "| visa Sponsorship offered | Low |",
                   "| Long commute | Negative — avoid |")
    r = rubric.parse(text)
    assert [row.label for row in r.rows] == ["Fun work"] and r.usable


def test_weight_words_map_high_medium_low_case_insensitively():
    r = rubric.parse(_rubric("| A | high |", "| B | MEDIUM |", "| C | Low |"))
    assert [row.weight for row in r.rows] == [3, 2, 1]


def test_duplicate_label_makes_the_rubric_invalid():
    r = rubric.parse(_rubric("| Same | High |", "| same  | Low |", "| Other | Medium |"))
    assert r.state == "invalid" and not r.usable
    assert [row.label for row in r.rows] == ["Same", "Other"]
    assert any("duplicate" in p for p in r.problems)


def test_unknown_weight_word_drops_the_row_and_invalidates():
    r = rubric.parse(_rubric("| A | High |", "| B | Critical |", "| C | 3 |"))
    assert r.state == "invalid" and not r.usable
    assert [row.label for row in r.rows] == ["A"]
    assert len(r.problems) == 2


def test_empty_label_row_is_dropped_and_recorded():
    r = rubric.parse(_rubric("| A | High |", "|  | Medium |"))
    assert [row.label for row in r.rows] == ["A"]
    assert any("empty label" in p for p in r.problems) and r.state == "invalid"


def test_short_row_is_dropped_without_crashing():
    r = rubric.parse(_rubric("| A | High |", "| only one cell", "|"))
    assert [row.label for row in r.rows] == ["A"] and r.state == "invalid"


@pytest.mark.parametrize("text", ["", "# P\n", "# P\n\n## Scoring rubric\nno table here\n",
                                  "## Scoring rubric\n| Signal | Weight |\n"])
def test_no_rubric_degrades(text):
    r = rubric.parse(text)
    assert r.rows == () and r.problems == ("rubric missing",) and r.state == "missing" and not r.usable


def test_rubric_below_the_fold_is_ignored():
    text = f"# P\n\n{FOLD}\n\n## Scoring rubric\n| Signal | Weight |\n|---|---|\n| A | High |\n"
    assert rubric.parse(text).state == "missing"


def test_rubric_table_must_be_under_its_own_heading():
    text = ("# P\n\n## Other\n| Signal | Weight |\n|---|---|\n| A | High |\n\n"
            "## Scoring rubric\nnothing\n")
    assert rubric.parse(text).state == "missing"


def test_rubric_with_only_excluded_rows_is_ok_but_empty():
    r = rubric.parse(_rubric("| Compensation | High |"))
    assert r.rows == () and r.problems == () and r.state == "ok" and not r.usable


def test_row_identity_is_the_normalised_label():
    r = rubric.parse(_rubric("|   Stack   OVERLAP  | High |"))
    assert r.rows[0].key == "stack overlap" == rubric.norm_label("Stack Overlap".replace(" ", " "))
    assert rubric.norm_label("Café") == rubric.norm_label("Café")
    assert rubric.norm_label("A\tB\n C") == "a b c"


def test_rubric_parse_never_raises_on_garbage():
    for text in ["|||", "## Scoring rubric\n|\n|\n|", "## Scoring rubric\n|a|b|\n|-|-|\n|\\|x|High|"]:
        rubric.parse(text)


# --- markets table -----------------------------------------------------------------------------

def test_template_markets_table_parses():
    m = markets.parse(TEMPLATE)
    assert m.problems == ()
    home = m.rows["atlantis (home)"]
    assert (home.label, home.floor_cell, home.sponsorship_cell) == ("Atlantis (home)", "₹35,00,000", "not needed")
    assert home.floor == markets.Floor(3500000, "INR") and home.need == "no"
    sg = m.rows["singapore"]
    assert sg.floor == markets.Floor(8000, "SGD") and sg.need == "yes"
    other = m.rows["elsewhere"]
    assert other.floor is None and other.need is None and other.floor_cell == "TBD — derive from home floor"


def test_markets_missing_or_below_fold_degrades():
    assert markets.parse("# P\n").rows == {} and markets.parse("# P\n").problems == ("markets missing",)
    below = f"{FOLD}\n## Markets\n| Market | Min base | Sponsorship |\n|--|--|--|\n| A | $1 | required |\n"
    assert markets.parse(below).rows == {}


def test_markets_columns_are_found_by_header_not_position():
    text = ("## Markets\n| Priority | Sponsorship | Market | Min base (per year) |\n|-|-|-|-|\n"
            "| 1 | required | Wonderland | EUR 90000 |\n")
    row = markets.parse(text).rows["wonderland"]
    assert row.need == "yes" and row.floor == markets.Floor(90000, "EUR")


def test_markets_without_a_floor_or_sponsorship_column_gives_unknowns_and_a_problem():
    text = "## Markets\n| Market | Notes |\n|-|-|\n| A | hi |\n"
    m = markets.parse(text)
    assert m.rows["a"].floor is None and m.rows["a"].need is None and m.problems


def test_duplicate_market_labels_are_ambiguous_and_unusable():
    text = ("## Markets\n| Market | Min base | Sponsorship |\n|-|-|-|\n| X | $1 | required |\n| x  | $2 | not needed |\n"
            "| Y | $3 | required |\n")
    m = markets.parse(text)
    assert set(m.rows) == {"y"} and "x" in m.ambiguous and m.problems
    with pytest.raises(markets.MarketError) as err:
        markets.verify_market(JobFacts(market="X"), m)
    assert err.value.code == "EVL006" and "ambiguous" in str(err.value)


def test_markets_table_stops_at_next_heading():
    text = ("## Markets\n| Market | Min base | Sponsorship |\n|-|-|-|\n| A | $1 | required |\n\n"
            "## Other\n| B | $2 | required |\n")
    assert set(markets.parse(text).rows) == {"a"}


# --- floor cell --------------------------------------------------------------------------------

@pytest.mark.parametrize("cell, amount, currency", [
    ("3500000 INR", 3500000, "INR"),
    ("₹35,00,000", 3500000, "INR"),
    ("₹35 lakh", 3500000, "INR"),
    ("SGD 8000", 8000, "SGD"),
    ("$150k", 150000, "USD"),
    ("€1.2M", 1200000, "EUR"),
    ("₹1 crore", 10000000, "INR"),
    ("USD 2000", 2000, "USD"),
    ("  £90,000 per year  ", 90000, "GBP"),
    ("35 lakh INR", 3500000, "INR"),
])
def test_parseable_floor_cells_yield_one_floor(cell, amount, currency):
    assert markets.parse_floor(cell) == markets.Floor(amount, currency)


@pytest.mark.parametrize("cell", [
    "TBD — derive from home floor", "30-35 LPA", "INR 30L or USD 40k", "35,00,000", "", "   ",
    "₹30 lakh – ₹35 lakh", "$100k and €90k", "$ 150k USD", "S$8000", "SGD 8000 per month", "USD 5000/mo",
    "150k+ USD", "10% INR", "TBD", "ZZZ 8000", "approximately fifty thousand USD", "0 USD", "INR 30L",
])
def test_ambiguous_floor_cells_are_unparseable(cell):
    assert markets.parse_floor(cell) is None


# --- sponsorship cell --------------------------------------------------------------------------

@pytest.mark.parametrize("cell, need", [
    ("required", "yes"), ("Required", "yes"), ("  REQUIRED ", "yes"), ("not needed", "no"), ("Not  Needed", "no"),
    ("preferred", None), ("optional", None), ("", None), ("required if remote", None), ("not required", None),
    ("not needed (citizen)", None),
])
def test_need_is_exact(cell, need):
    assert markets.parse_need(cell) == need


# --- verify_market -----------------------------------------------------------------------------

M = markets.parse(TEMPLATE)


def test_market_naming_no_row_is_evl006():
    with pytest.raises(markets.MarketError) as err:
        markets.verify_market(JobFacts(market="Narnia"), M)
    assert err.value.code == "EVL006"


def test_a_different_row_is_never_consulted():
    check = markets.verify_market(JobFacts(market="elsewhere", needed=True, floor_amount=8000, floor_currency="SGD"), M)
    assert check.row.label == "Elsewhere"
    assert check.floor.state is Verification.UNVERIFIED and check.need.state is Verification.UNVERIFIED


def test_label_comparison_ignores_case_and_spacing():
    check = markets.verify_market(JobFacts(market="  SINGAPORE "), M)
    assert check.row.label == "Singapore"
    assert markets.verify_market(JobFacts(market="atlantis   (HOME)"), M).row.label == "Atlantis (home)"


def test_floor_equal_to_the_cell_is_verified_with_verbatim_source():
    check = markets.verify_market(JobFacts(market="Singapore", floor_amount=8000, floor_currency="SGD",
                                           floor_basis=MarketBasis.BASE), M)
    assert check.floor.state is Verification.VERIFIED and check.floor.source == "SGD 8000"


@pytest.mark.parametrize("amount, currency, basis", [
    (9000, "SGD", None), (8000, "USD", None), (8000, "SGD", MarketBasis.TOTAL),
])
def test_floor_differing_from_the_cell_is_a_conflict(amount, currency, basis):
    check = markets.verify_market(JobFacts(market="Singapore", floor_amount=amount, floor_currency=currency,
                                           floor_basis=basis), M)
    assert check.floor.state is Verification.CONFLICT and check.floor.source == "SGD 8000"
    assert check.floor.value is None


def test_case_and_padding_of_the_currency_code_is_not_a_difference():
    check = markets.verify_market(JobFacts(market="Singapore", floor_amount=8000, floor_currency=" sgd "), M)
    assert check.floor.state is Verification.VERIFIED and check.floor.value == markets.Floor(8000, "SGD")


def test_unparseable_cell_is_unverified_even_if_agent_gives_a_floor():
    check = markets.verify_market(JobFacts(market="Elsewhere", floor_amount=50000, floor_currency="USD"), M)
    assert check.floor.state is Verification.UNVERIFIED and check.floor.source == "TBD — derive from home floor"


def test_parseable_cell_with_no_agent_floor_is_unverified_not_verified():
    assert markets.verify_market(JobFacts(market="Singapore"), M).floor.state is Verification.UNVERIFIED


def test_need_agreement_conflict_and_unknown():
    assert markets.verify_market(JobFacts(market="Singapore", needed=True), M).need.state is Verification.VERIFIED
    assert markets.verify_market(JobFacts(market="Singapore", needed="yes"), M).need.state is Verification.VERIFIED
    conflict = markets.verify_market(JobFacts(market="Atlantis (home)", needed=True), M)
    assert conflict.need.state is Verification.CONFLICT and conflict.need.source == "not needed"
    assert markets.verify_market(JobFacts(market="Atlantis (home)", needed="no"), M).need.state is Verification.VERIFIED
    # preferred in the row: the need is unknown whatever the agent says
    pref = markets.verify_market(JobFacts(market="Elsewhere", needed=False), M)
    assert pref.need.state is Verification.UNVERIFIED and pref.need.source == "preferred"
    # the row establishes a need but the agent gave none
    assert markets.verify_market(JobFacts(market="Singapore"), M).need.state is Verification.UNVERIFIED


def test_no_market_named_gives_no_row_and_unverified():
    check = markets.verify_market(JobFacts(needed=True, floor_amount=1, floor_currency="USD"), M)
    assert check.row is None
    assert check.floor.state is Verification.UNVERIFIED and check.need.state is Verification.UNVERIFIED
    assert check.floor.source is None and check.need.source is None


def test_conflict_never_selects_the_favourable_value():
    # the row says sponsorship is not needed; an agent claiming it is needed does not turn it into a cap input
    check = markets.verify_market(JobFacts(market="Atlantis (home)", needed=True), M)
    assert check.need.state is Verification.CONFLICT and check.need.value is None
    ok = markets.verify_market(JobFacts(market="Singapore", needed=True), M)
    assert ok.need.value == "yes"
