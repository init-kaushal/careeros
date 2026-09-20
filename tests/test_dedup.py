from careeros.core.dedup import (
    canonical_url,
    is_same_posting,
    normalize_location,
    normalize_title,
)
from careeros.core.models import Job

_NOW = "2026-09-20T00:00:00+00:00"


def _job(**over):
    base = dict(
        id="x", source="linkedin", company="Acme", title="Senior SRE",
        url="https://x.test/1", created_at=_NOW, updated_at=_NOW,
    )
    base.update(over)
    return Job(**base)


# --- canonical_url ---

def test_canonical_url_lowercases_scheme_and_host():
    assert canonical_url("HTTPS://Boards.Greenhouse.IO/acme/jobs/1") == \
        "https://boards.greenhouse.io/acme/jobs/1"


def test_canonical_url_strips_fragment_and_trailing_slash():
    assert canonical_url("https://x.test/jobs/1/#apply") == "https://x.test/jobs/1"


def test_canonical_url_strips_each_denylisted_tracking_param():
    for param in ("utm_source=li", "utm_campaign=x", "gh_src=abc",
                  "trk=feed", "refId=zz", "originalSubdomain=uk"):
        assert canonical_url("https://x.test/jobs/1?" + param) == "https://x.test/jobs/1"


def test_canonical_url_preserves_a_non_denylisted_query_param():
    # THE catastrophic case: some boards put the job id in the query string.
    # Stripping all params would collapse every posting into one record.
    assert canonical_url("https://x.test/apply?jobId=1234") == \
        "https://x.test/apply?jobId=1234"


def test_canonical_url_keeps_real_params_while_dropping_tracking_ones():
    out = canonical_url("https://x.test/apply?jobId=1234&utm_source=li")
    assert "jobId=1234" in out
    assert "utm_source" not in out


def test_canonical_url_of_none_is_empty_string():
    assert canonical_url(None) == ""


# --- normalize_title ---

def test_normalize_title_lowercases_and_strips_punctuation():
    assert normalize_title("Senior  Engineer, Platform!") == "senior engineer platform"


def test_normalize_title_expands_each_abbreviation():
    assert normalize_title("Sr SRE") == "senior site reliability engineer"
    assert normalize_title("Jr SWE") == "junior software engineer"
    assert normalize_title("Eng Mgr") == "engineer manager"
    assert normalize_title("Dev") == "developer"


def test_normalize_title_expands_whole_tokens_only():
    # A substring replace would produce "engineerineering" and silently stop
    # matching anything.
    assert normalize_title("Engineering Manager") == "engineering manager"
    assert normalize_title("Sreepathi") == "sreepathi"


# --- normalize_location ---

def test_normalize_location_none_stays_none():
    assert normalize_location(None) is None
    assert normalize_location("   ") is None


def test_normalize_location_lowercases_and_collapses():
    assert normalize_location("  San Francisco,  CA ") == "san francisco ca"


# --- is_same_posting ---

def test_url_tier_matches_even_when_titles_differ_entirely():
    a = _job(title="Senior SRE", url="https://x.test/1?utm_source=li")
    b = _job(title="Completely Different", url="https://x.test/1")
    assert is_same_posting(a, b)


def test_title_variants_match_on_the_fuzzy_tier():
    a = _job(title="Senior SRE", url="https://linkedin.test/1")
    b = _job(title="Senior Site Reliability Engineer", url="https://greenhouse.test/9")
    assert is_same_posting(a, b)


def test_genuinely_different_titles_do_not_match():
    a = _job(title="Senior Engineer", url="https://a.test/1")
    b = _job(title="Staff Engineer", url="https://b.test/2")
    assert not is_same_posting(a, b)


def test_different_companies_never_match_on_the_fuzzy_tier():
    a = _job(company="Acme", url="https://a.test/1")
    b = _job(company="Beta", url="https://b.test/2")
    assert not is_same_posting(a, b)


def test_company_match_is_case_and_whitespace_insensitive():
    a = _job(company=" acme ", url="https://a.test/1")
    b = _job(company="Acme", url="https://b.test/2")
    assert is_same_posting(a, b)


def test_null_location_is_a_wildcard():
    a = _job(location=None, url="https://a.test/1")
    b = _job(location="San Francisco, CA", url="https://b.test/2")
    assert is_same_posting(a, b)
    assert is_same_posting(b, a)


def test_two_different_non_empty_locations_do_not_match():
    # The accepted limitation, pinned so it cannot silently drift into a
    # false positive: "Remote" and "San Francisco, CA" for one job WILL
    # produce two records. That is the recoverable direction.
    a = _job(location="Remote", url="https://a.test/1")
    b = _job(location="San Francisco, CA", url="https://b.test/2")
    assert not is_same_posting(a, b)


def test_identical_non_empty_locations_match():
    a = _job(location="Remote", url="https://a.test/1")
    b = _job(location="remote", url="https://b.test/2")
    assert is_same_posting(a, b)


def test_punctuation_only_titles_do_not_match_each_other():
    # normalize_title("!!!") and normalize_title("???") are both "".
    # Empty normalization is absence of evidence, not identity.
    a = _job(title="!!!", url="https://a.test/1")
    b = _job(title="???", url="https://b.test/2")
    assert not is_same_posting(a, b)


def test_punctuation_only_company_does_not_match():
    a = _job(company="...", title="Senior SRE", url="https://a.test/1")
    b = _job(company="???", title="Senior SRE", url="https://b.test/2")
    assert not is_same_posting(a, b)


def test_url_tier_still_wins_when_the_title_normalizes_to_empty():
    # The guard must not disable the URL tier: same posting, garbage title.
    a = _job(title="!!!", url="https://x.test/1?utm_source=li")
    b = _job(title="!!!", url="https://x.test/1")
    assert is_same_posting(a, b)
