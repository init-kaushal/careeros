from careeros.core.models import Evidence, Skill
from careeros.skills.resume_evidence import MAX_QUOTE_CHARS, verify_quote

_RESUME = """Alice Johnson
Senior Site Reliability Engineer

EXPERIENCE
Site Reliability Engineer — MegaCorp (2018–present)
  - Built distributed monitoring handling 1M events/sec
  - Wrote C++ tooling and a .NET migration shim

SKILLS
Python, Go, Kubernetes, Terraform
"""


def test_exact_quote_returns_its_one_indexed_line():
    assert verify_quote("Python, Go, Kubernetes, Terraform", _RESUME) == 10


def test_quote_on_first_line_returns_one_not_zero():
    assert verify_quote("Alice Johnson", _RESUME) == 1


def test_whitespace_differences_still_match():
    assert verify_quote("Python,   Go,  Kubernetes, Terraform", _RESUME) == 10


def test_case_differences_still_match():
    assert verify_quote("PYTHON, GO, KUBERNETES, TERRAFORM", _RESUME) == 10


def test_absent_quote_returns_none():
    assert verify_quote("Rust, Haskell", _RESUME) is None


def test_quote_with_regex_metacharacters_matches_literally():
    # Resumes are full of these. Compiled as a pattern they raise or mismatch.
    assert verify_quote("C++ tooling", _RESUME) == 7
    assert verify_quote(".NET migration shim", _RESUME) == 7
    assert verify_quote("(2018–present)", _RESUME) == 5


def test_regex_pattern_does_not_match_as_a_pattern():
    # ".*" must be treated as literal text, not "anything".
    assert verify_quote(".*", _RESUME) is None


def test_overlong_quote_is_rejected_even_though_it_occurs():
    # An unbounded quote verifies trivially: hand back the whole resume and
    # every skill "checks out". Evidence that cites everything cites nothing.
    assert len(_RESUME) > MAX_QUOTE_CHARS
    assert verify_quote(_RESUME, _RESUME) is None


def test_quote_exactly_at_the_limit_is_accepted():
    quote = _RESUME[:MAX_QUOTE_CHARS]
    assert verify_quote(quote, _RESUME) == 1


def test_repeated_quote_returns_the_first_line():
    source = "Kubernetes\nsomething else\nKubernetes\n"
    assert verify_quote("Kubernetes", source) == 1


def test_quote_spanning_a_line_break_matches_and_returns_the_starting_line():
    # A wrapped bullet is one span to the model and two lines to us. The
    # per-line pass cannot see it, so the whole-document fallback must.
    assert verify_quote("Alice Johnson Senior Site Reliability Engineer", _RESUME) == 1


def test_quote_spanning_a_line_break_deeper_in_the_document():
    assert verify_quote("MegaCorp (2018–present) - Built distributed", _RESUME) == 5


def test_words_not_adjacent_in_the_source_do_not_match():
    # The fallback normalizes the whole document, so it must still require
    # contiguity — otherwise any bag of words in the resume "verifies".
    assert verify_quote("Alice Kubernetes", _RESUME) is None


def test_empty_quote_returns_none():
    assert verify_quote("", _RESUME) is None
    assert verify_quote("   ", _RESUME) is None


def test_empty_source_returns_none():
    assert verify_quote("Python", "") is None


def test_evidence_round_trips_through_json():
    skill = Skill(
        name="Python",
        evidence=Evidence(quote="Python, Go", line=10, source_file="resumes/master.md"),
    )
    loaded = Skill.model_validate_json(skill.model_dump_json())
    assert loaded.evidence is not None
    assert loaded.evidence.quote == "Python, Go"
    assert loaded.evidence.line == 10
    assert loaded.evidence.source_file == "resumes/master.md"


def test_skill_without_evidence_still_loads():
    # A pre-Phase-11a skills.json has no "evidence" key at all. The spec's
    # "no migration needed" claim depends on this.
    import json
    legacy = {"name": "Python", "level": "expert", "source": "resume"}
    assert "evidence" not in legacy
    skill = Skill.model_validate_json(json.dumps(legacy))
    assert skill.evidence is None


def test_short_quote_does_not_match_inside_a_longer_word():
    # "Go" occurs in "Golang" as characters, but is not evidence of Go.
    assert verify_quote("Go", "Experienced with Golang and distributed systems") is None
    assert verify_quote("Java", "Skilled in JavaScript frameworks") is None


def test_short_quote_matches_when_it_is_its_own_word():
    assert verify_quote("Go", "Python, Go, Kubernetes") == 1


def test_boundary_check_allows_adjacent_punctuation():
    # A quote butting against a comma or parenthesis is still boundary-aligned.
    assert verify_quote("Kubernetes", "Skills: Python, Kubernetes, Terraform") == 1
    assert verify_quote("MegaCorp", "Engineer — MegaCorp (2018–present)") == 1


def test_span_starting_mid_word_is_rejected():
    # Previously this mis-attributed the line; now it does not verify at all.
    source = "alphabet betaword\ngammaword deltaword\n"
    assert verify_quote("aword gammaword", source) is None


def test_bare_c_is_rejected_inside_cpp():
    # "C" is a real skill name a model will propose; it must not verify off
    # the tail end of "C++".
    assert verify_quote("C", "Wrote C++ tooling") is None


def test_bare_r_is_rejected_inside_r_and_d():
    assert verify_quote("R", "Led R&D efforts") is None


def test_go_is_rejected_inside_go_to_market():
    assert verify_quote("Go", "Owned go-to-market strategy") is None


def test_go_is_rejected_inside_a_snake_case_identifier():
    # Identifiers land in resumes verbatim, and "_" is the same fabrication
    # class as "-": a bare skill name matching inside a larger token.
    assert verify_quote("Go", "Built go_to_market_dashboard") is None


def test_bare_c_is_rejected_inside_a_snake_case_identifier():
    assert verify_quote("C", "Maintained C_API bindings") is None


def test_go_is_rejected_across_unicode_dashes():
    # A resume exported from a word processor carries en-dashes, not hyphens.
    assert verify_quote("Go", "Owned go\u2013to\u2013market strategy") is None


def test_cpp_matches_when_it_is_the_whole_quote():
    assert verify_quote("C++ tooling", "Wrote C++ tooling") == 1


def test_csharp_matches_when_it_is_the_whole_quote():
    assert verify_quote("C#", "Skilled in C# development") == 1


def test_r_and_d_matches_when_it_is_the_whole_quote():
    assert verify_quote("R&D", "Led R&D efforts") == 1


def test_quote_with_no_alphanumeric_content_is_rejected():
    # A lone "-" (or similar) matches almost anywhere in real resume text
    # and evidences nothing.
    assert verify_quote("-", "2018-2020: worked at MegaCorp") is None
    assert verify_quote("--", "line one -- line two") is None
