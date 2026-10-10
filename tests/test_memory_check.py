"""The evidence check: what passes, what fails, and why. All data is invented."""

from __future__ import annotations

from pathlib import Path

import pytest
from helpers import make_workspace

from careeros.core.memory import check, ops


def codes(result) -> list[str]:
    return [f.code for f in result.review_required]


def run(root: Path, draft: str, **kwargs):
    return check.run_check(root, draft, **kwargs)


# --- supported claims -----------------------------------------------------------------------

@pytest.mark.parametrize("draft", [
    "Reduced AWS costs by 35% using Kafka-based pipelines at Acme Corp.",
    "Cut AWS spend 35% by moving batch workloads to Kafka pipelines.",
    "Led a Kubernetes migration for 40+ services.",
    "Led a Kubernetes migration for 40 services, cutting deploy time by 60 percent.",
    "Designed a multi-region Postgres setup with 99.95% availability.",
    "Built an internal developer platform in golang used by 120 engineers.",
    "Cut incident response time from 45 minutes to 12 minutes.",
    "I have 4 years of experience.",
    "From 2022 to 2024 I worked at Globex Systems.",
    "I graduated from Example Institute of Technology, Jabalpur in 2020.",
    "I hold the Certified Kubernetes Administrator certification.",
    "Experience with Go, Python and k8s.",
    "I work as a Backend Engineer.",
    "Dear Hiring Manager,\nBest regards,\nJordan Example",
])
def test_faithful_drafts_pass(career, draft: str) -> None:
    root, _ = career
    result = run(root, draft)
    assert result.ok, [(f.code, f.atom, f.reason) for f in result.review_required]


def test_a_pass_reports_that_support_is_only_claimed_and_lists_what_was_not_evaluated(career) -> None:
    root, _ = career
    result = run(root, "Reduced AWS costs by 35% using Kafka.\nI enjoy mentoring junior colleagues.")
    assert result.ok
    assert result.claimed_support > 0 and all(s["claimed_only"] for s in result.supported)
    assert result.not_evaluated == ["I enjoy mentoring junior colleagues."]
    assert "whether prose is true" in result.to_dict()["not_evaluated"]["categories"]


# --- unsupported claims ----------------------------------------------------------------------

@pytest.mark.parametrize("draft,code", [
    ("Reduced AWS costs by 50% using Kafka.", "CHK001"),
    ("Served 2M users.", "CHK001"),
    ("Led a Kubernetes migration for 50+ services.", "CHK001"),
    ("Cut AWS spend by 35 users.", "CHK001"),
    ("I joined the team in 2019.", "CHK002"),
    ("I built it with Rust.", "CHK003"),
    ("We used Flink and Kafka.", "CHK003"),
    ("I worked at FakeCorp for a while.", "CHK004"),
    ("My experience at FakeCorp taught me patience.", "CHK004"),
    ("I joined FakeCorp after Acme Corp.", "CHK004"),
    ("Staff Engineer at FakeCorp, I led the team.", "CHK004"),
    ("I graduated from Fake University.", "CHK005"),
    ("I studied computer science at Imaginary College.", "CHK005"),
    ("As Director of Engineering, I led platform teams.", "CHK006"),
    ("I am a Staff Engineer.", "CHK006"),
    ("I was Head of Platform for two teams.", "CHK006"),
    ("I am the CTO of a startup.", "CHK006"),
    ("I am an AWS Certified Solutions Architect.", "CHK007"),
    ("I am certified in Magic Beans.", "CHK007"),
    ("I have 10+ years of experience.", "CHK008"),
])
def test_unsupported_claims_fail_with_the_right_code(career, draft: str, code: str) -> None:
    root, _ = career
    result = run(root, draft)
    assert not result.ok
    assert code in codes(result), [(f.code, f.atom) for f in result.review_required]


def test_a_technology_duration_is_never_derivable(career) -> None:
    root, _ = career
    result = run(root, "I have 3 years of Go experience.")
    assert codes(result) == ["CHK008"]


def test_a_year_range_longer_than_the_dated_experience_fails(career) -> None:
    root, _ = career
    assert codes(run(root, "I have 5 years of experience.")) == ["CHK008"]


# --- compound claims -------------------------------------------------------------------------

@pytest.mark.parametrize("draft", [
    "I reduced costs by 60% using Kafka.",                 # 60% and Kafka each exist, in different facts
    "I cut costs by 35% with Terraform.",                  # Terraform is only a skill; 35% lives in another fact
    "At Globex Systems I cut deploy time by 60%.",         # employer from one experience, metric from another
    "Experience with Go and 35% cost savings.",            # a number in the line removes the list exemption
    "As a Staff Engineer I cut costs by 35%.",
])
def test_claims_that_are_only_supported_separately_need_review(career, draft: str) -> None:
    root, _ = career
    result = run(root, draft)
    assert "CHK010" in codes(result) or "CHK006" in codes(result), codes(result)
    assert not result.ok


def test_keywords_scattered_across_the_memory_cannot_carry_a_made_up_sentence(career) -> None:
    root, _ = career
    result = run(root, "I led Kubernetes and Kafka migrations that cut costs by 60% and 35% at Globex Systems.")
    assert not result.ok


def test_a_relationship_inside_one_fact_passes_including_inherited_employer(career) -> None:
    root, _ = career
    assert run(root, "At Acme Corp I reduced AWS costs by 35%.").ok
    assert run(root, "At Globex Systems I designed a PostgreSQL setup with 99.95% availability.").ok


def test_a_known_employer_without_any_cue_still_takes_part_in_the_relationship_check(career) -> None:
    root, _ = career
    assert not run(root, "My time at Globex Systems taught me to cut deploy time by 60%.").ok


def test_technology_enumerations_are_exempt_among_themselves_but_not_with_other_claims(career) -> None:
    root, _ = career
    assert run(root, "Technologies: Go, Python, PostgreSQL and Grafana.").ok
    assert run(root, "## Skills\n\nGo, Kafka, PostgreSQL").ok
    assert not run(root, "Built a service in Go and PostgreSQL for 500 users.").ok


# --- detection without cues ------------------------------------------------------------------

@pytest.mark.parametrize("draft,code", [
    ("My experience at FakeCorp taught me a lot.", "CHK004"),
    ("As Director of Engineering, I led platform teams.", "CHK006"),
    ("I graduated from Fake University.", "CHK005"),
    ("Zorbix Labs shaped how I work.", "CHK009"),
    ("I really admired working with Zorbix Labs.", "CHK009"),
    ("We shipped it together with Quantum Widgets.", "CHK009"),
    ("Senior Staff Engineer", "CHK006"),
    ("FakeCorp | Principal Engineer", "CHK004"),
])
def test_names_are_caught_without_the_usual_phrasing(career, draft: str, code: str) -> None:
    root, _ = career
    assert code in codes(run(root, draft)), codes(run(root, draft))


def test_a_pipe_header_in_a_resume_draft_is_checked_as_employer_and_title(career) -> None:
    root, _ = career
    assert run(root, "### Acme Corp | Senior Software Engineer\nBengaluru - January 2025 - Present").ok
    assert codes(run(root, "### Acme Corp | Principal Engineer")) == ["CHK006"]


def test_ordinary_capitalisation_is_not_mistaken_for_a_company(career) -> None:
    root, _ = career
    assert run(root, "Thanks for your time on Monday. I live in Bengaluru, India and speak English.").ok
    assert run(root, "Dear Priya Sharma,\nI admire your work.").ok


# --- --allow and --against ------------------------------------------------------------------

def test_an_allowed_term_alone_in_a_sentence_is_an_unverified_mention(career) -> None:
    root, _ = career
    result = run(root, "Your team runs Flink at scale, which excites me.", allow=["Flink"])
    assert result.ok and result.allowed_mentions == ["Flink"] and not result.supported


def test_an_allowed_technology_cannot_back_an_unsupported_achievement(career) -> None:
    root, _ = career
    without = run(root, "I used Kafka to reduce costs by 35% with Flink.", allow=["Flink"])
    assert codes(without) == ["CHK010"]
    result = run(root, "I used Flink to reduce costs by 35%.", allow=["Flink"])
    assert codes(result) == ["CHK010"] and "Flink" in result.review_required[0].atom
    assert not result.supported


def test_allowing_a_number_does_not_make_it_supported(career) -> None:
    root, _ = career
    result = run(root, "Reduced AWS costs by 50%.", allow=["50%"])
    assert codes(result) == ["CHK010"]


def test_allowing_a_name_only_silences_it_when_nothing_else_is_claimed(career) -> None:
    root, _ = career
    assert run(root, "I spoke with Zorbix Labs.", allow=["Zorbix Labs"]).ok
    assert not run(root, "I worked at FakeCorp and cut costs by 35%.", allow=["FakeCorp"]).ok


def test_the_company_applied_to_may_be_named_but_working_there_still_needs_a_record(career) -> None:
    root, _ = career
    against = ("Initech", "Backend Engineer")
    assert run(root, "I am excited about the Backend Engineer role at Initech.", against=against).ok
    assert run(root, "I would love to join Initech.", against=against).ok
    assert codes(run(root, "I worked at Initech last year.", against=against)) == ["CHK004"]


# --- status semantics ------------------------------------------------------------------------

def test_require_confirmed_fails_claims_backed_only_by_claimed_facts(career) -> None:
    root, ids = career
    draft = "Reduced AWS costs by 35% using Kafka."
    assert run(root, draft).ok
    assert "CHK020" in codes(run(root, draft, require_confirmed=True))
    ops.confirm_fact(root, ids["costs"], confirm=lambda p: True)
    ops.confirm_fact(root, ids["acme"], confirm=lambda p: True)
    result = run(root, draft, require_confirmed=True)
    assert result.ok and not any(s["claimed_only"] for s in result.supported if s["claim"] in ("35%", "Kafka"))


def test_disputed_and_retired_facts_support_nothing(career) -> None:
    root, ids = career
    draft = "Reduced AWS costs by 35% using Kafka."
    ops.dispute_fact(root, ids["costs"], "this number was wrong")
    assert "CHK001" in codes(run(root, draft))
    ops.retire_fact(root, ids["platform"], "no longer used")
    assert not run(root, "Built an internal developer platform in Go used by 120 engineers.").ok


def test_an_empty_memory_fails_closed(tmp_path: Path, clock) -> None:
    root = make_workspace(tmp_path / "ws")
    assert codes(run(root, "Anything at all.")) == ["CHK030"]
    ops.add_fact(root, "preference", {"text": "no fintech roles"}, quote="I prefer no fintech")
    assert codes(run(root, "Anything at all.")) == ["CHK030"]


# --- normalisation --------------------------------------------------------------------------

@pytest.mark.parametrize("draft", [
    "Cut costs by 35 percent using Kafka and AWS.",
    "Reduced AWS costs by 35% using Kafka.",
    "Served 300+ teams.",
    "Served 300 teams.",
    "Used Python 3.11 and Go 1.22 daily.",
    "See https://example.com/2031/45 or call +91 80032 93018.",
    "1. Reduced AWS costs by 35% using Kafka.",
])
def test_numbers_and_versions_normalise(career, draft: str) -> None:
    root, _ = career
    assert run(root, draft).ok, [(f.code, f.atom) for f in run(root, draft).review_required]


@pytest.mark.parametrize("draft", ["Served 3000 teams.", "Served 301+ teams.", "Cut costs by 35 users."])
def test_numbers_match_by_equality_and_the_plus_rule_is_directional(career, draft: str) -> None:
    root, _ = career
    assert "CHK001" in codes(run(root, draft))


def test_json_shape_has_the_three_sections(career) -> None:
    root, _ = career
    data = run(root, "Reduced AWS costs by 50%.\nHello there friend.").to_dict()
    assert set(data) >= {"ok", "supported", "review_required", "not_evaluated", "allowed_mentions", "supported_by_claimed"}
    assert data["ok"] is False and data["not_evaluated"]["sentences"] == ["Hello there friend."]


# --- names that merely begin like a known one -------------------------------------------------

@pytest.mark.parametrize("draft,codes_expected", [
    ("I worked at Acme Systems for a year.", {"CHK004"}),
    ("I worked at Acme Corp Labs.", {"CHK004", "CHK009"}),
    ("I studied at Example Institute of Technology Bangalore.", {"CHK005", "CHK009"}),
])
def test_a_longer_name_that_starts_like_a_known_one_is_not_the_known_one(career, draft: str, codes_expected: set) -> None:
    root, _ = career
    assert set(codes(run(root, draft))) & codes_expected, codes(run(root, draft))


# --- markdown, line endings and unusual text ----------------------------------------------

def test_markdown_is_stripped_before_checking(career) -> None:
    root, _ = career
    draft = (
        "# Resume\n\n## Experience\n\n### Acme Corp | Senior Software Engineer\nBengaluru - January 2025 - Present\n\n"
        "- **Reduced** AWS costs by 35% using [Kafka](https://example.com/kafka)-based pipelines.\n"
        "```\nnot a claim: 99% of Rust\n```\n<!-- Rust 77% hidden -->\n---\n"
    )
    assert run(root, draft).ok


def test_windows_line_endings_are_handled(career) -> None:
    root, _ = career
    assert run(root, "Reduced AWS costs by 35% using Kafka.\r\nBuilt it in Go for 120 engineers.\r\n").ok
    assert not run(root, "Reduced AWS costs by 35% using Kafka.\r\nServed 9 users.\r\n").ok


def test_non_ascii_text_and_names_round_trip(tmp_path: Path, clock) -> None:
    root = make_workspace(tmp_path / "ws")
    exp = ops.add_fact(root, "experience", {"employer": "Café Systèmes", "title": "Ingénieur", "start": "2021-01", "end": "2022-12"}, quote="à moi")
    ops.add_fact(root, "achievement", {"parent": exp.id, "text": "Réduit les coûts de ₹5 crore avec Kafka."}, quote="à moi")
    assert run(root, "J'ai réduit les coûts de ₹5 crore avec Kafka chez Café Systèmes.").ok
    assert not run(root, "J'ai réduit les coûts de ₹6 crore avec Kafka.").ok


def test_a_very_long_draft_is_checked_in_reasonable_time(career) -> None:
    root, _ = career
    import time

    start = time.monotonic()
    result = run(root, "\n".join(["Reduced AWS costs by 35% using Kafka-based pipelines."] * 400))
    assert result.ok and time.monotonic() - start < 20


# --- review fix round 1 -------------------------------------------------------------------------

def test_a_disputed_parent_experience_lends_nothing_to_its_achievements(career) -> None:
    root, ids = career
    ops.dispute_fact(root, ids["globex"], "never worked there", confirm=lambda p: True)
    assert "CHK004" in codes(run(root, "I worked at Globex Systems."))
    assert "CHK002" in codes(run(root, "In 2022 I designed a PostgreSQL setup with 99.95% availability."))


@pytest.mark.parametrize("draft", [
    "Built internal tools with Kafka and PostgreSQL.",
    "Led tech work moving Kafka onto PostgreSQL.",
    "Ran Kafka on PostgreSQL, including on-call.",
])
def test_loose_list_words_do_not_exempt_technology_pairs(career, draft: str) -> None:
    root, _ = career
    assert "CHK010" in codes(run(root, draft))


def test_an_allowed_name_cannot_stand_in_for_a_fact(career) -> None:
    root, _ = career
    assert "CHK010" in codes(run(root, "Reduced AWS costs by 35% at Initech.", allow=["Initech"]))
    assert run(root, "I spoke with Zorbix Labs.", allow=["Zorbix Labs"]).ok


def test_against_never_excuses_a_self_description_of_the_target_title(career) -> None:
    root, _ = career
    against = ("Initech", "Staff Engineer")
    assert "CHK006" in codes(run(root, "I am a Staff Engineer.", against=against))
    assert "CHK006" in codes(run(root, "I was hired as a Staff Engineer.", against=against))
    assert run(root, "I am excited about the Staff Engineer role.", against=against).ok


@pytest.mark.parametrize("draft,ok", [
    ("I spent 4 years at Acme Corp.", False),
    ("My 4 years at Globex Systems taught me PostgreSQL.", False),
    ("I spent 1 year at Acme Corp.", True),
    ("My 2 years at Globex Systems taught me a lot.", True),
    ("I have 4 years of experience.", True),
])
def test_a_duration_beside_an_employer_is_compared_with_that_employer(career, draft: str, ok: bool) -> None:
    root, _ = career
    result = run(root, draft)
    assert result.ok is ok, [(f.code, f.atom, f.reason) for f in result.review_required]
    if not ok:
        assert "CHK008" in codes(result)


def test_a_known_title_with_a_level_is_not_split_by_the_role_cue(career) -> None:
    root, _ = career
    assert run(root, "I was a Software Engineer 2 at Globex Systems.").ok
    assert "CHK006" in codes(run(root, "I am a Staff Engineer."))
    assert not run(root, "I was a Software Engineer 3 at Globex Systems.").ok


def test_soft_wrapped_paragraph_lines_are_joined_but_bullets_stay_separate(career) -> None:
    root, _ = career
    assert "CHK010" in codes(run(root, "Reduced AWS costs by 35%\nwith PostgreSQL on Grafana."))
    assert run(root, "Reduced AWS costs by 35%\nusing Kafka-based pipelines.").ok
    assert run(root, "- Reduced AWS costs by 35% using Kafka.\n- Built it in Go for 120 engineers.").ok
    assert "CHK010" not in codes(run(root, "- Reduced costs by 35%\n- with PostgreSQL on Grafana"))


# --- review fix round 2 -------------------------------------------------------------------------

@pytest.mark.parametrize("draft", [
    "## Skills\n\n**Languages:** Go, Python\n**Infra:** Kubernetes, AWS",
    "Acme Corp | Senior Software Engineer\n2025 - Present\nReduced AWS costs by 35% using Kafka.",
    "Led a Kubernetes migration for 40+ services\nDesigned a multi-region PostgreSQL setup with 99.95% availability",
    "Dear Hiring Manager,\nI admire the work.",
])
def test_conservative_joining_keeps_faithful_layouts_passing(career, draft: str) -> None:
    root, _ = career
    assert run(root, draft).ok, [(f.code, f.atom) for f in run(root, draft).review_required]


@pytest.mark.parametrize("draft", [
    "Dear Hiring Manager,\nI partnered with Zorbix Labs on Kafka.",
    "Hello,\nI earned my MS at Stanford University in 2020.",
    "Thanks for your time\nZorbix Labs referred me.",
    "Tech: Go, Python\nand Kafka with PostgreSQL",
])
def test_conservative_joining_does_not_hide_fabrications(career, draft: str) -> None:
    root, _ = career
    assert not run(root, draft).ok


def test_against_join_as_the_target_title_is_not_a_claim(career) -> None:
    root, _ = career
    against = ("Initech", "Staff Engineer")
    assert run(root, "I would love to join Initech as a Staff Engineer.", against=against).ok
    assert "CHK006" in codes(run(root, "As the Staff Engineer, I led teams.", against=against))


def test_each_duration_is_compared_with_the_nearest_employer(career) -> None:
    root, _ = career
    assert "CHK008" not in codes(run(root, "I spent 2 years at Globex Systems and 1 year at Acme Corp."))
    assert "CHK008" in codes(run(root, "I spent 4 years at Acme Corp."))
