"""Outreach core (Task A): slugify, person and target records, the shared person-event module, the person fold, the target
fold. Pure; invented data only. The reference models here (the outcome simulator, the status chain, the stale rule) are
written independently of the production code and share no table with it.
"""

from __future__ import annotations

import itertools
import random
import subprocess
import sys
import textwrap
from datetime import datetime, timedelta

import pytest

from careeros.core import ids
from careeros.core.approvals import people
from careeros.core.outreach import derive
from careeros.core.outreach import fold as ofold
from careeros.core.outreach import person, rules, slug, target
from careeros.core.outreach.model import Evidence, FoundIn, JobInfo, Person, PersonFlags, Target
from careeros.core.workspace import join_frontmatter

PER = "per_abcdefghjk"
PER2 = "per_bcdefghjkm"
TGT = "tgt_abcdefghjk"
TGT2 = "tgt_bcdefghjkm"
JOB = "job_abcdefghjk"
URL = "https://www.linkedin.com/in/alex-example"
URL2 = "https://www.linkedin.com/in/sam-example"
SHA = "c" * 64
T0 = datetime(2026, 1, 1, 0, 0, 0)
FMT = "%Y-%m-%dT%H:%M:%SZ"


def ts(delta: int | timedelta = 0) -> str:
    d = timedelta(seconds=delta) if isinstance(delta, int) else delta
    return (T0 + d).strftime(FMT)


NOW = ts(timedelta(days=400))


# --- slugify ---------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("Example Corp", "example-corp"),
    ("  Example   Corp  ", "example-corp"),
    ("Example, Corp.", "example-corp"),
    ("Acme (UK) Ltd", "acme-uk-ltd"),
    ("A&B  --  C", "a-b-c"),
    ("under_score", "under-score"),
    ("Example123 Corp", "example123-corp"),
    ("Ｅｘａｍｐｌｅ Corp", "example-corp"),      # fullwidth letters fold under NFKC
    ("Café Corp", "cafe-corp"),
    ("Cafe\u0301 Corp", "cafe-corp"),
    ("Café Inc", "cafe-inc"),
    ("Zürich Labs", "zurich-labs"), ("Škoda", "skoda"), ("Nestlé", "nestle"), ("Straße", "strasse"),
    ("Ørsted", "orsted"), ("Łódź", "lodz"), ("McDonald's", "mcdonalds"), ("L\u2019Oréal", "loreal"),
    ("L\u2018Oréal", "loreal"), ("l\u02bcoreal", "loreal"), ("Ünilever", "unilever"), ("Æon œuvre", "aeon-oeuvre"),
    ("Đorđe ð þ", "dorde-d-th"), ("Müller", "muller"), ("Mueller", "mueller"),    # documented limit: these still differ
    ("K\u0131van\u00e7", "kivanc"), ("\u0126al Far", "hal-far"), ("\u014a Co", "n-co"), ("\u0166est", "test"),
    ("\u01e2on \u01fcon", "aeon-aeon"), ("\u01feland", "oland"), ("\u0191oo", "foo"), ("\u0192oo", "foo"),
    ("---x---", "x"),
    ("x", "x"),
    ("9", "9"),
    ("① Circle", "1-circle"),                                           # NFKC turns the circled digit into 1
])
def test_slugify_grid(text, expected):
    assert slug.slugify(text) == expected


@pytest.mark.parametrize("text", ["", "   ", "---", "!!!", "北京公司", "会社", "\U0001F600", "\ud800", "\x00"])
def test_slugify_without_an_ascii_letter_or_digit_is_none(text):
    assert slug.slugify(text) is None


@pytest.mark.parametrize("value", [None, 5, b"abc", ["a"], 1.5])
def test_slugify_of_a_non_string_is_none(value):
    assert slug.slugify(value) is None


def test_slugify_output_is_well_formed_or_none_and_idempotent_over_random_strings():
    import re as _re
    rng = random.Random(99)
    alphabet = list("aZ9 -'") + ["\u00e9", "\u00df", "\u00f8", "\u0142", "\u00e6", "\u0153", "\u0111", "\u00f0", "\u00fe", "\u0131", "\u0127", "\u014b", "\u0167", "\u01e3", "\u01fd", "\u01ff", "\u0192", "\u2019",
                                "\u02bc", "\u0301", "\u4e2d", "\U0001F600", "\ufb01", "\u00b2", "\ud800", "\u0130"]
    for _ in range(5000):
        text = "".join(rng.choice(alphabet) for _ in range(rng.randrange(0, 12)))
        got = slug.slugify(text)
        assert got is None or _re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", got), (text, got)
        assert got is None or slug.slugify(got) == got
        assert slug.slugify(text) == got


def test_slugify_is_deterministic_idempotent_and_well_formed():
    rng = random.Random(3)
    alphabet = list("abZ09 -_.,&()") + ["é", "中", "\U0001F600", " ", "Ａ", "İ", "ß", "①", "\ud800"]
    for _ in range(3000):
        text = "".join(rng.choice(alphabet) for _ in range(rng.randrange(0, 14)))
        got = slug.slugify(text)
        assert got == slug.slugify(text)
        if got is not None:
            assert slug.slugify(got) == got
            assert all(part and part.isascii() and part.isalnum() and part == part.lower() for part in got.split("-")), got


# --- person record -----------------------------------------------------------------------------------------------------

PEOPLE_REL = f"people/{PER}.md"
PERSON_SPEC = textwrap.dedent(f"""\
    ---
    schema: 1
    id: {PER}
    url: "{URL}"
    name: "Alex Example"
    headline: "Engineering Manager, Platform"
    company: "Example Corp"
    company_slug: example-corp
    role: hiring_manager
    market: "India (home)"
    found_in:
      - {{kind: target, id: {TGT}}}
      - {{kind: job, id: {JOB}}}
    found_at: 2026-10-19
    ---
    Notes (ignored by code).
    """)


def pdict(**over):
    d = {"schema": 1, "id": PER, "url": URL, "name": "Alex Example", "headline": "Engineering Manager, Platform",
         "company": "Example Corp", "company_slug": "example-corp", "role": "hiring_manager", "market": "India (home)",
         "found_in": [{"kind": "target", "id": TGT}, {"kind": "job", "id": JOB}], "found_at": "2026-10-19"}
    d.update(over)
    return d


def ptext(d):
    return join_frontmatter(d, "\nNotes.\n")


def pparse(d_or_text, rel=PEOPLE_REL):
    text = d_or_text if isinstance(d_or_text, str) else ptext(d_or_text)
    return person.parse(text, rel)


def only_out001(parsed):
    record, issues = parsed
    assert record is None and issues, "expected a refusal"
    assert {i.code for i in issues} == {"OUT001"} and all(i.severity == "error" and i.path for i in issues)


def test_person_spec_example_parses():
    record, issues = person.parse(PERSON_SPEC, PEOPLE_REL)
    assert issues == []
    assert record == Person(PER, URL, "Alex Example", "Engineering Manager, Platform", "Example Corp", "example-corp",
                            "hiring_manager", "India (home)", (FoundIn("target", TGT), FoundIn("job", JOB)), "2026-10-19")


def test_person_optional_market_and_empty_found_in_and_empty_headline():
    d = pdict(headline="", found_in=[])
    d.pop("market")
    record, issues = pparse(d)
    assert issues == [] and record.market is None and record.found_in == () and record.headline == ""
    assert "market" not in person.to_bytes(record).decode()


def test_person_quoted_date_and_yaml_date_agree():
    a, _ = pparse(pdict(found_at="2026-10-19"))
    b, _ = person.parse(PERSON_SPEC, PEOPLE_REL)
    assert a.found_at == b.found_at == "2026-10-19"


PERSON_BAD = {
    "schema 2": pdict(schema=2), "schema string": pdict(schema="1"), "schema bool": pdict(schema=True),
    "id wrong prefix": pdict(id="job_abcdefghjk"), "id bad alphabet": pdict(id="per_abcdefghji"), "id short": pdict(id="per_abc"),
    "id not str": pdict(id=5), "id mismatches file name": pdict(id=PER2),
    "url http": pdict(url="http://www.linkedin.com/in/alex-example"),
    "url trailing slash": pdict(url=URL + "/"), "url upper": pdict(url="https://www.linkedin.com/in/Alex-Example"),
    "url query": pdict(url=URL + "?x=1"), "url other host": pdict(url="https://example.com/in/alex"),
    "url not str": pdict(url=5), "url empty": pdict(url=""), "url country subdomain": pdict(url="https://de.linkedin.com/in/alex-example"),
    "name empty": pdict(name=""), "name blank": pdict(name="   "), "name int": pdict(name=5), "name newline": pdict(name="a\nb"),
    "name bidi": pdict(name="a\u202eb"), "name nul": pdict(name="a\x00b"), "name too long": pdict(name="x" * 501),
    "headline int": pdict(headline=5), "headline escape": pdict(headline="a\x1b[31mb"),
    "company empty": pdict(company=""), "company int": pdict(company=5),
    "company_slug mismatch": pdict(company_slug="example"), "company_slug upper": pdict(company_slug="Example-Corp"),
    "company_slug int": pdict(company_slug=5), "company with no slug": pdict(company="北京", company_slug=""),
    "role unknown": pdict(role="manager"), "role upper": pdict(role="Recruiter"), "role int": pdict(role=1), "role list": pdict(role=["peer"]),
    "market empty": pdict(market=""), "market int": pdict(market=5), "market null": pdict(market=None),
    "found_in not a list": pdict(found_in={"kind": "job", "id": JOB}), "found_in string": pdict(found_in="job"),
    "found_in entry string": pdict(found_in=["job"]),
    "found_in extra key": pdict(found_in=[{"kind": "job", "id": JOB, "x": 1}]),
    "found_in missing id": pdict(found_in=[{"kind": "job"}]), "found_in bad kind": pdict(found_in=[{"kind": "person", "id": PER2}]),
    "found_in job with tgt id": pdict(found_in=[{"kind": "job", "id": TGT}]),
    "found_in target with job id": pdict(found_in=[{"kind": "target", "id": JOB}]),
    "found_in duplicate": pdict(found_in=[{"kind": "job", "id": JOB}, {"kind": "job", "id": JOB}]),
    "found_in too many": pdict(found_in=[{"kind": "job", "id": f"job_{n:010d}".replace("0", "a")} for n in range(51)]),
    "found_at bad month": pdict(found_at="2026-13-01"), "found_at word": pdict(found_at="tomorrow"), "found_at int": pdict(found_at=20261019),
    "found_at timestamp": pdict(found_at="2026-10-19T00:00:00Z"), "found_at none": pdict(found_at=None),
    "found_at non-ascii digits": pdict(found_at="٢026-10-19"),
    "unknown key": pdict(state="invited"), "alias-like key": pdict(status="x"),
}


@pytest.mark.parametrize("name", list(PERSON_BAD))
def test_person_every_bad_shape_is_out001(name):
    d = PERSON_BAD[name]
    only_out001(pparse(d))


@pytest.mark.parametrize("key", [k for k in pdict() if k != "market"])
def test_person_every_required_key_is_required(key):
    d = pdict()
    d.pop(key)
    only_out001(pparse(d))


def test_person_filename_check_is_skipped_only_for_a_non_md_label():
    assert pparse(pdict(), "frontmatter")[1] == []
    only_out001(pparse(pdict(), "people/per_zzzzzzzzzz.md"))


def test_person_unquoted_yaml_scalars_that_are_not_strings_are_refused():
    only_out001(pparse(PERSON_SPEC.replace('name: "Alex Example"', "name: yes")))
    only_out001(pparse(PERSON_SPEC.replace('name: "Alex Example"', "name: 12")))
    only_out001(pparse(PERSON_SPEC.replace("role: hiring_manager", "role: null")))
    only_out001(pparse(PERSON_SPEC.replace("schema: 1", "schema: 1.0")))
    only_out001(pparse(PERSON_SPEC.replace("schema: 1", "schema: 0x1")))


HOSTILE_TEXT = {
    "empty": "", "no fence": "id: x\n", "unterminated": "---\nid: x\n", "list": "---\n- a\n---\n", "scalar": "---\nx\n---\n",
    "alias": PERSON_SPEC.replace("market: \"India (home)\"", "market: &a x\nheadline2: *a"),
    "alias in value": PERSON_SPEC.replace('name: "Alex Example"', "name: &a x").replace("company_slug: example-corp", "company_slug: *a"),
    "merge key": "---\n<<: {a: 1}\n---\n", "duplicate key": PERSON_SPEC.replace("role: hiring_manager", "role: peer\nrole: recruiter"),
    "python tag": "---\nname: !!python/object/apply:os.system [x]\n---\n", "binary tag": "---\nname: !!binary aGk=\n---\n",
    "bad yaml": "---\nname: [unclosed\n---\n", "tab indent": "---\nfound_in:\n\t- x\n---\n", "nul": "---\nname: a\x00b\n---\n",
    "lone surrogate": PERSON_SPEC.replace("Alex Example", "Alex \ud800 Example"),
    "huge": "---\nname: " + "x" * 3_000_000 + "\n---\n", "huge body": PERSON_SPEC + "n" * 3_000_000,
    "deep nesting": "---\nfound_in: " + "[" * 100_000 + "]" * 100_000 + "\n---\n",
    "deep mapping": "---\nfound_in: " + "{a: " * 20_000 + "1" + "}" * 20_000 + "\n---\n",
    "many keys": "---\n" + "\n".join(f"k{n}: {n}" for n in range(20_000)) + "\n---\n",
    "billion laughs": "---\na: &a [x]\nb: &b [*a,*a,*a,*a]\nc: [*b,*b,*b,*b]\n---\n",
}


@pytest.mark.parametrize("name", list(HOSTILE_TEXT))
def test_person_hostile_input_is_out001_and_never_raises(name):
    only_out001(pparse(HOSTILE_TEXT[name]))


@pytest.mark.parametrize("name", list(HOSTILE_TEXT))
def test_target_hostile_input_is_out001_and_never_raises(name):
    text = HOSTILE_TEXT[name]
    only_out001(target.parse(text, f"targets/{'example-corp'}.md"))


def test_person_crlf_is_read_as_lf_and_written_as_lf():
    record, issues = person.parse(PERSON_SPEC.replace("\n", "\r\n"), PEOPLE_REL)
    assert issues == [] and b"\r" not in person.to_bytes(record)


def test_person_to_bytes_round_trips_over_awkward_text():
    rng = random.Random(11)
    alphabet = list("ab Z09") + ["'", '"', ":", "#", "-", "\\", "{", "}", "[", "]", ",", "&", "*", "!", "|", ">", "%", "@", "`",
                                 "é", "中", "\U0001F600", "yes", "null", "~", "?", "1", "2026-10-19", "=", "<<"]
    for _ in range(500):
        def word():
            return "".join(rng.choice(alphabet) for _ in range(rng.randrange(1, 9))).strip() or "x"
        company = word()
        record = Person(PER, URL, word(), rng.choice(["", word()]), company, slug.slugify(company) or "x",
                        rng.choice(rules.ROLES), rng.choice([None, word()]),
                        tuple(FoundIn("job", f"job_abcdefgh{c}{d}") for c, d in [("j", "k"), ("m", "n")][:rng.randrange(0, 3)]), "2026-10-19")
        if slug.slugify(company) is None:
            continue
        record = Person(**{**record.__dict__, "company_slug": slug.slugify(company)})
        parsed, issues = person.parse(person.to_bytes(record).decode(), PEOPLE_REL)
        assert issues == [] and parsed == record, record
        assert person.to_bytes(parsed) == person.to_bytes(record)


def test_person_to_bytes_keeps_the_given_body_and_has_a_default_note():
    record, _ = person.parse(PERSON_SPEC, PEOPLE_REL)
    assert person.to_bytes(record, "\nmy notes\n").decode().endswith("---\n\nmy notes\n")
    assert b"Free notes" in person.to_bytes(record)


# --- target record -----------------------------------------------------------------------------------------------------

TARGET_REL = "targets/example-corp.md"
TARGET_SPEC = textwrap.dedent(f"""\
    ---
    schema: 1
    id: {TGT}
    slug: example-corp
    company: "Example Corp"
    market: "Australia"
    fit: 8
    sponsorship: likely
    evidence:
      - {{source: "sponsor register entry (placeholder)", read_on: 2026-10-19}}
    ---
    Notes.
    """)


def tdict(**over):
    d = {"schema": 1, "id": TGT, "slug": "example-corp", "company": "Example Corp", "market": "Australia", "fit": 8,
         "sponsorship": "likely", "evidence": [{"source": "sponsor register entry (placeholder)", "read_on": "2026-10-19"}]}
    d.update(over)
    return d


def tparse(d_or_text, rel=TARGET_REL):
    text = d_or_text if isinstance(d_or_text, str) else join_frontmatter(d_or_text, "\nNotes.\n")
    return target.parse(text, rel)


def test_target_spec_example_parses():
    record, issues = target.parse(TARGET_SPEC, TARGET_REL)
    assert issues == []
    assert record == Target(TGT, "example-corp", "Example Corp", "Australia", 8, "likely",
                            (Evidence("sponsor register entry (placeholder)", "2026-10-19"),))


@pytest.mark.parametrize("sponsorship", ["offered", "likely", "unknown", "not_offered"])
def test_target_every_sponsorship_value_but_na_needs_evidence(sponsorship):
    assert tparse(tdict(sponsorship=sponsorship))[1] == []
    for bad in (None, [], [{"source": "", "read_on": "2026-10-19"}], [{"source": "   ", "read_on": "2026-10-19"}],
                [{"source": "x", "read_on": "2026-13-19"}], [{"source": "x", "read_on": "soon"}],
                [{"source": "x", "read_on": "2026-10-19T00:00:00Z"}], [{"source": "x"}], [{"read_on": "2026-10-19"}]):
        d = tdict(sponsorship=sponsorship)
        if bad is None:
            d.pop("evidence")
        else:
            d["evidence"] = bad
        only_out001(tparse(d))


def test_target_na_needs_no_evidence_but_given_evidence_is_still_checked():
    d = tdict(sponsorship="n/a")
    d.pop("evidence")
    record, issues = tparse(d)
    assert issues == [] and record.evidence == () and record.sponsorship == "n/a"
    assert tparse(tdict(sponsorship="n/a", evidence=[]))[1] == []
    assert tparse(tdict(sponsorship="n/a"))[1] == []
    only_out001(tparse(tdict(sponsorship="n/a", evidence=[{"source": "", "read_on": "2026-10-19"}])))


def test_target_one_good_evidence_entry_is_enough_but_each_entry_must_be_valid():
    good, bad = {"source": "a", "read_on": "2026-10-19"}, {"source": "b", "read_on": "nope"}
    assert tparse(tdict(evidence=[good, {"source": "c", "read_on": "2026-09-01"}]))[1] == []
    only_out001(tparse(tdict(evidence=[good, bad])))


TARGET_BAD = {
    "schema 2": tdict(schema=2), "schema bool": tdict(schema=True),
    "id wrong prefix": tdict(id=PER), "id not str": tdict(id=1),
    "slug upper": tdict(slug="Example-Corp"), "slug double hyphen": tdict(slug="example--corp"), "slug space": tdict(slug="example corp"),
    "slug leading hyphen": tdict(slug="-example"), "slug empty": tdict(slug=""), "slug int": tdict(slug=5),
    "slug mismatches file name": tdict(slug="other-corp"), "slug slash": tdict(slug="a/b"), "slug dotdot": tdict(slug=".."),
    "company empty": tdict(company=""), "company int": tdict(company=1), "company newline": tdict(company="a\nb"),
    "market empty": tdict(market=""), "market int": tdict(market=1), "market bidi": tdict(market="a\u202eb"),
    "fit -1": tdict(fit=-1), "fit 11": tdict(fit=11), "fit string": tdict(fit="8"), "fit float": tdict(fit=8.0), "fit bool": tdict(fit=True),
    "fit none": tdict(fit=None), "fit hex": tdict(fit="0x8"),
    "sponsorship unknown word": tdict(sponsorship="maybe"), "sponsorship upper": tdict(sponsorship="Likely"),
    "sponsorship bool": tdict(sponsorship=True), "sponsorship none": tdict(sponsorship=None), "sponsorship na spelled": tdict(sponsorship="na"),
    "evidence not a list": tdict(evidence={"source": "x", "read_on": "2026-10-19"}), "evidence string": tdict(evidence="x"),
    "evidence entry string": tdict(evidence=["x"]),
    "evidence extra key": tdict(evidence=[{"source": "x", "read_on": "2026-10-19", "y": 1}]),
    "evidence source int": tdict(evidence=[{"source": 5, "read_on": "2026-10-19"}]),
    "evidence source newline": tdict(evidence=[{"source": "a\nb", "read_on": "2026-10-19"}]),
    "evidence source too long": tdict(evidence=[{"source": "x" * 501, "read_on": "2026-10-19"}]),
    "evidence too many": tdict(evidence=[{"source": f"s{n}", "read_on": "2026-10-19"} for n in range(51)]),
    "unknown key": tdict(status="approved"),
}


@pytest.mark.parametrize("name", list(TARGET_BAD))
def test_target_every_bad_shape_is_out001(name):
    only_out001(tparse(TARGET_BAD[name]))


@pytest.mark.parametrize("key", list(tdict()))
def test_target_every_key_but_evidence_is_required(key):
    d = tdict()
    d.pop(key)
    if key == "evidence":
        assert tparse(d)[1] != []                      # sponsorship likely needs it
    else:
        only_out001(tparse(d))


def test_target_slug_need_not_equal_the_slugified_company():
    record, issues = tparse(tdict(slug="ex-corp"), "targets/ex-corp.md")
    assert issues == [] and record.slug == "ex-corp"


def test_target_to_bytes_round_trips():
    rng = random.Random(5)
    for _ in range(300):
        ev = tuple(Evidence(rng.choice(["a: b", "yes", "x # y", "1", "é", "null"]), f"2026-0{rng.randrange(1, 10)}-1{rng.randrange(0, 10)}")
                   for _ in range(rng.randrange(1, 4)))
        record = Target(TGT, "example-corp", rng.choice(["Example Corp", "yes", "a: b", "1"]), rng.choice(["Australia", "no", "x"]),
                        rng.randrange(0, 11), rng.choice(["offered", "likely", "unknown", "not_offered"]), ev)
        parsed, issues = target.parse(target.to_bytes(record).decode(), TARGET_REL)
        assert issues == [] and parsed == record
    record = Target(TGT, "example-corp", "Example Corp", "India (home)", 5, "n/a", ())
    assert target.parse(target.to_bytes(record).decode(), TARGET_REL) == (record, [])


# --- approvals/people.py -----------------------------------------------------------------------------------------------

def mkev(seq, etype, entity, at=None, *, action=None, artifacts=None, reason=None, source=None):
    return {"id": f"evt_{seq:010d}", "seq": seq, "ts": at or ts(seq), "type": etype, "actor": "agent:test", "entity": entity,
            "prev_state": None, "new_state": None, "action": action or etype, "reason": reason, "approval": "not_required",
            "artifacts": artifacts or [], "source": source, "prev": "0" * 64}


def added(seq, per=PER, url=URL, at=None):
    return mkev(seq, "outreach.person_added", per, at, **people.person_added_fields(f"people/{per}.md", SHA, url))


def proposal(seq, item, per, url, kind="linkedin_invite", at=None):
    arts = [f"approvals/{item}.md", "sha256:" + SHA] + ([f"url:{url}"] if url else [])
    return mkev(seq, "approval.proposed", item, at, action=f"propose {kind} for {per}", artifacts=arts)


def test_person_added_fields_is_the_one_writer_and_validates():
    fields = people.person_added_fields(PEOPLE_REL, SHA, URL)
    assert fields["artifacts"] == [PEOPLE_REL, f"sha256:{SHA}", f"url:{URL}"] and fields["action"].strip()
    assert set(fields) == {"action", "artifacts"}
    for bad in ((PEOPLE_REL, "x" * 64, URL), (PEOPLE_REL, "A" * 64, URL), (PEOPLE_REL, SHA, URL + "/"), (PEOPLE_REL, SHA, "https://example.com/in/a"),
                (5, SHA, URL), ("", SHA, URL)):
        with pytest.raises(ValueError):
            people.person_added_fields(*bad)


def test_writer_and_parser_round_trip():
    event = added(1)
    assert people.parse_person_added(event) == (PER, URL, None)
    urls, errors = people.person_urls([event])
    assert urls == {URL: PER} and errors == []
    mapping, errors = people.person_ids([event])
    assert mapping == {PER: URL} and errors == []


@pytest.mark.parametrize("artifacts", [
    [], [PEOPLE_REL], [PEOPLE_REL, f"sha256:{SHA}"], [PEOPLE_REL, f"sha256:{SHA}", URL], [PEOPLE_REL, f"sha256:{SHA}", "url:"],
    [PEOPLE_REL, f"sha256:{SHA}", f"url:{URL}/"], [PEOPLE_REL, f"sha256:{SHA}", f"url:{URL.upper()}"], [PEOPLE_REL, f"sha256:{SHA}", "url:https://example.com/in/a"],
    [PEOPLE_REL, f"sha256:{SHA}", f"url:{URL}", "extra"], [PEOPLE_REL, "sha256:xyz", f"url:{URL}"], [PEOPLE_REL, SHA, f"url:{URL}"],
    [5, f"sha256:{SHA}", f"url:{URL}"], [PEOPLE_REL, f"sha256:{SHA}", None], "nope", None, {"a": 1},
])
def test_malformed_person_added_url_element_becomes_an_error_and_is_not_mapped(artifacts):
    event = mkev(1, "outreach.person_added", PER, action="add person", artifacts=artifacts)
    urls, errors = people.person_urls([event])
    assert urls == {} and len(errors) == 1
    assert people.person_ids([event]) == ({}, errors)


@pytest.mark.parametrize("entity", [None, "", 5, "job_abcdefghjk", "per_x", "tgt_abcdefghjk"])
def test_person_added_without_a_person_entity_is_an_error(entity):
    event = mkev(1, "outreach.person_added", entity, **people.person_added_fields(PEOPLE_REL, SHA, URL))
    urls, errors = people.person_urls([event])
    assert urls == {} and len(errors) == 1


def test_non_object_events_are_reported_not_dropped():
    for fn in (people.person_urls, people.person_ids, people.closed_people):
        value, errors = fn([added(1), "junk", 5, None])
        assert len(errors) == 3, fn


def test_person_urls_is_the_union_of_proposals_and_person_added_first_seen_wins():
    events = [proposal(1, "apr_aaaaaaaaaa", PER, URL), added(2, PER2, URL2), proposal(3, "apr_aaaaaaaaab", PER, URL, "linkedin_message"),
              proposal(4, "apr_aaaaaaaaac", "job_abcdefghjk", None, "application_submit")]
    urls, errors = people.person_urls(events)
    assert urls == {URL: PER, URL2: PER2} and errors == []
    assert people.person_ids(events)[0] == {PER: URL, PER2: URL2}
    swapped = [events[1], events[0]]
    assert people.person_urls(swapped)[0] == {URL: PER, URL2: PER2}


@pytest.mark.parametrize("artifacts", [
    ["approvals/x.md", "sha256:" + SHA, "url:bad"], ["approvals/x.md", "sha256:" + SHA],
    ["approvals/x.md", "sha256:" + SHA, f"url:{URL}/"], ["approvals/x.md", "sha256:" + SHA, f"url:{URL.upper()}"]])
@pytest.mark.parametrize("kind", ["linkedin_invite", "linkedin_message"])
def test_a_person_proposal_without_a_readable_url_is_reported_by_every_reader(kind, artifacts):
    event = mkev(1, "approval.proposed", "apr_aaaaaaaaaa", action=f"propose {kind} for {PER}", artifacts=artifacts)
    urls, errors = people.person_urls([event])
    assert urls == {} and len(errors) == 1 and "apr_aaaaaaaaaa" in errors[0]
    assert people.person_ids([event]) == ({}, errors)
    assert len(people.identity_problems([event])) == 1


def test_a_submit_proposal_has_no_url_and_no_error():
    event = proposal(1, "apr_aaaaaaaaaa", "job_abcdefghjk", None, "application_submit")
    assert people.person_urls([event]) == ({}, []) and people.identity_problems([event]) == []


def test_person_urls_respects_seq_order_not_list_order():
    a = added(5, "per_bcdefghjkm", URL)
    b = proposal(2, "apr_aaaaaaaaaa", PER, URL)
    assert people.person_urls([a, b])[0] == {URL: PER}


def test_added_and_proposal_for_the_same_pair_is_consistent():
    events = [proposal(1, "apr_aaaaaaaaaa", PER, URL), added(2), added(3)]
    assert people.identity_problems(events) == []


def test_identity_problems_one_url_under_two_ids():
    for events in ([added(1, PER, URL), added(2, PER2, URL)],
                   [proposal(1, "apr_aaaaaaaaaa", PER, URL), added(2, PER2, URL)],
                   [added(1, PER, URL), proposal(2, "apr_aaaaaaaaaa", PER2, URL, "linkedin_message")],
                   [proposal(1, "apr_aaaaaaaaaa", PER, URL), proposal(2, "apr_aaaaaaaaab", PER2, URL)]):
        problems = people.identity_problems(events)
        assert len(problems) == 1 and URL in problems[0] and PER in problems[0] and PER2 in problems[0], events


def test_identity_problems_one_id_under_two_urls():
    for events in ([added(1, PER, URL), added(2, PER, URL2)],
                   [proposal(1, "apr_aaaaaaaaaa", PER, URL), added(2, PER, URL2)],
                   [added(1, PER, URL), proposal(2, "apr_aaaaaaaaaa", PER, URL2)],
                   [proposal(1, "apr_aaaaaaaaaa", PER, URL), proposal(2, "apr_aaaaaaaaab", PER, URL2)]):
        problems = people.identity_problems(events)
        assert len(problems) == 1 and PER in problems[0] and URL in problems[0] and URL2 in problems[0], events


def test_identity_problems_ignore_rejections_unrelated_and_malformed_events_and_render_hostile_urls_safely():
    events = [added(1), mkev(2, "outreach.rejected", PER, reason="x"), mkev(3, "note.added", None), "junk",
              mkev(4, "outreach.person_added", PER2, artifacts=["x"]), proposal(5, "apr_aaaaaaaaaa", "job_abcdefghjk", None, "application_submit")]
    assert people.identity_problems(events) == []
    assert people.identity_problems([]) == []


def test_closed_people_reads_outreach_closed_only():
    events = [mkev(1, "outreach.closed", PER, reason="declined", source="user"), mkev(2, "outreach.accepted", PER2),
              mkev(3, "outreach.rejected", PER2, reason="x"), mkev(4, "approval.consumed", PER2)]
    closed, errors = people.closed_people(events)
    assert closed == {PER} and errors == []
    assert people.closed_people([]) == (set(), [])


@pytest.mark.parametrize("entity", [None, "", 5, ["per_x"]])
def test_entity_less_closed_events_are_reported_and_close_nobody(entity):
    closed, errors = people.closed_people([mkev(1, "outreach.closed", entity, reason="declined")])
    assert closed == set() and len(errors) == 1


def test_a_closed_event_for_a_non_person_id_still_closes_that_entity_and_is_reported():
    closed, errors = people.closed_people([mkev(1, "outreach.closed", "weird", reason="declined")])
    assert closed == {"weird"} and len(errors) == 1


def test_people_module_imports_only_approvals_modules_and_ids():
    import ast
    import inspect
    tree = ast.parse(inspect.getsource(people))
    imported = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.ImportFrom) and n.module and n.module.startswith("careeros"):
            imported |= {f"{n.module}.{a.name}" for a in n.names}
        elif isinstance(n, ast.Import):
            imported |= {a.name for a in n.names if a.name.startswith("careeros")}
    assert all(m == "careeros.core.ids" or m.startswith("careeros.core.approvals.") for m in imported), imported
    assert not any("outreach" in m for m in imported)


# --- the person fold: events -------------------------------------------------------------------------------------------

class Ledger:
    """Builds ledger events with increasing seq and ts (one second apart unless told)."""

    def __init__(self):
        self.events, self.seq, self.n_items = [], 0, 0

    def add(self, etype, entity, at=None, **kw):
        self.seq += 1
        self.events.append(mkev(self.seq, etype, entity, at if at is not None else ts(self.seq), **kw))
        return self.events[-1]

    def item(self, kind, per, consumed_at, url=URL, steps_ts=None):
        self.n_items += 1
        apr = f"apr_aaaaaaa{'0123456789'[self.n_items // 10]}{'0123456789'[self.n_items % 10]}"
        self.add("approval.proposed", apr, steps_ts, action=f"propose {kind} for {per}",
                 artifacts=[f"approvals/{apr}.md", "sha256:" + SHA, f"url:{url}"])
        self.add("approval.approved", apr, steps_ts)
        self.add("approval.started", apr, steps_ts)
        if consumed_at is not None:
            self.add("approval.consumed", apr, consumed_at)
        return apr

    def outcome(self, kind, per, at=None, **kw):
        kw.setdefault("source", "user")
        if kind == "closed":
            kw.setdefault("reason", "declined")
        return self.add(f"outreach.{kind}", per, at, **kw)


def fold_one(ledger, per=PER, now=NOW):
    result = ofold.fold_people(ledger.events, now)
    return result.flags.get(per), result.errors


def test_fold_an_empty_ledger_and_non_events():
    result = ofold.fold_people([], NOW)
    assert result.flags == {} and result.errors == ()
    result = ofold.fold_people([None, "x", 5], NOW)
    assert result.flags == {} and len(result.errors) == 3


def test_a_person_added_only_is_discovered():
    L = Ledger()
    L.events.append(added(1))
    flags, errors = fold_one(L)
    assert errors == () and flags.label == "discovered" and flags.added_at == ts(1) and flags.last_activity == ts(1)
    assert (flags.invited_at, flags.messaged_at, flags.accepted_at, flags.replied_at, flags.closed_at, flags.invite_in_flight) == \
           (None, None, None, None, None, False)


def test_invite_in_flight_and_consumed_and_label():
    L = Ledger()
    L.item("linkedin_invite", PER, None)
    flags, errors = fold_one(L)
    assert errors == () and flags.invite_in_flight and flags.invited_at is None and flags.label == "discovered"
    L2 = Ledger()
    L2.item("linkedin_invite", PER, ts(100))
    flags, errors = fold_one(L2)
    assert errors == () and not flags.invite_in_flight and flags.invited_at == ts(100) and flags.label == "invited"
    assert flags.last_activity == ts(100)


def test_a_message_in_flight_is_not_an_invite_in_flight():
    L = Ledger()
    L.item("linkedin_message", PER, None)
    flags, _ = fold_one(L)
    assert not flags.invite_in_flight and flags.messaged_at is None


def test_invited_at_is_the_earliest_consumed_invite_and_a_second_one_is_reported():
    L = Ledger()
    L.item("linkedin_invite", PER, ts(500))
    L.item("linkedin_invite", PER, ts(300))                # later in the ledger, earlier timestamp
    flags, errors = fold_one(L)
    assert flags.invited_at == ts(300) and len(errors) == 1 and "invite" in errors[0].message and errors[0].entity == PER
    assert flags.last_activity == ts(500)
    L2 = Ledger()
    L2.item("linkedin_invite", PER, ts(300))
    L2.item("linkedin_invite", PER, ts(500))
    flags, errors = fold_one(L2)
    assert flags.invited_at == ts(300) and len(errors) == 1


def test_messaged_at_is_the_latest_consumed_message():
    L = Ledger()
    L.item("linkedin_message", PER, ts(900))
    L.item("linkedin_message", PER, ts(400))
    flags, errors = fold_one(L)
    assert errors == () and flags.messaged_at == ts(900) and flags.label == "messaged"


def test_only_legally_consumed_items_count_and_other_people_are_separate():
    L = Ledger()
    L.add("approval.proposed", "apr_aaaaaaaaaa", action=f"propose linkedin_invite for {PER}",
          artifacts=["approvals/x.md", "sha256:" + SHA, f"url:{URL}"])
    L.add("approval.consumed", "apr_aaaaaaaaaa")           # consumed without started: not a legal transition
    L.item("linkedin_invite", PER2, ts(50), url=URL2)
    result = ofold.fold_people(L.events, NOW)
    assert result.flags[PER].invited_at is None and result.flags[PER2].invited_at == ts(50)


def test_a_declined_expired_voided_invite_gives_no_invited_at():
    L = Ledger()
    L.add("approval.proposed", "apr_aaaaaaaaaa", action=f"propose linkedin_invite for {PER}", artifacts=["approvals/x.md", "sha256:" + SHA, f"url:{URL}"])
    L.add("approval.declined", "apr_aaaaaaaaaa")
    flags, errors = fold_one(L)
    assert flags.invited_at is None and not flags.invite_in_flight and errors == ()


def test_outcomes_in_order_set_the_timestamps_and_labels():
    L = Ledger()
    L.item("linkedin_invite", PER, ts(100))
    L.outcome("accepted", PER, ts(200))
    assert fold_one(L)[0].label == "connected"
    L.outcome("replied", PER, ts(300))
    flags, errors = fold_one(L)
    assert errors == () and flags.label == "replied" and (flags.accepted_at, flags.replied_at) == (ts(200), ts(300))
    L.outcome("closed", PER, ts(400), reason="not_a_fit")
    flags, errors = fold_one(L)
    assert errors == () and flags.label == "closed" and flags.closed_at == ts(400) and flags.closed_reason == "not_a_fit"
    assert flags.last_activity == ts(300)                  # a close is not activity


def test_every_outcome_event_refused_by_the_table_is_reported_and_not_applied():
    L = Ledger()
    L.outcome("accepted", PER)                              # no invite
    L.outcome("replied", PER)                               # no invite or message
    flags, errors = fold_one(L)
    assert len(errors) == 2 and flags.accepted_at is None and flags.replied_at is None and flags.label == "discovered"
    L.outcome("closed", PER)
    L.outcome("closed", PER)                                # second close
    L.outcome("accepted", PER)                              # after close
    flags, errors = fold_one(L)
    assert len(errors) == 4 and flags.closed_at is not None and flags.accepted_at is None


def test_an_outcome_before_the_invite_in_seq_order_is_refused_even_if_its_ts_is_later():
    L = Ledger()
    L.outcome("accepted", PER, ts(900))
    L.item("linkedin_invite", PER, ts(100))
    flags, errors = fold_one(L)
    assert len(errors) == 1 and flags.accepted_at is None and flags.invited_at == ts(100)


def test_replied_is_legal_after_a_message_alone():
    L = Ledger()
    L.item("linkedin_message", PER, ts(100))
    L.outcome("replied", PER, ts(200))
    flags, errors = fold_one(L)
    assert errors == () and flags.label == "replied"


def test_outcome_once_rule_for_each_kind():
    for kind in ("accepted", "replied"):
        L = Ledger()
        L.item("linkedin_invite", PER, ts(100))
        L.outcome(kind, PER, ts(200))
        L.outcome(kind, PER, ts(300))
        flags, errors = fold_one(L)
        assert len(errors) == 1 and getattr(flags, f"{kind}_at") == ts(200), kind


def test_malformed_outreach_events_are_reported_never_dropped():
    L = Ledger()
    L.item("linkedin_invite", PER, ts(100))
    L.add("outreach.bogus", PER)
    L.add("outreach.accepted", None, source="user")
    L.add("outreach.accepted", 7, source="user")
    L.add("outreach.accepted", "weird", source="user")
    L.add("outreach.person_added", None, **people.person_added_fields(PEOPLE_REL, SHA, URL))
    L.add("outreach.person_added", PER2, action="add person", artifacts=["bad"])
    L.add("outreach.accepted", PER, "not-a-ts", source="user")
    result = ofold.fold_people(L.events, NOW)
    assert len(result.errors) == 7 and result.flags[PER].accepted_at is None


def test_unknown_outreach_types_do_not_change_flags():
    L = Ledger()
    L.item("linkedin_invite", PER, ts(100))
    before = ofold.fold_people(L.events, NOW).flags
    L.add("outreach.bogus", PER)
    result = ofold.fold_people(L.events, NOW)
    assert result.flags == before and len(result.errors) == 1 and result.errors[0].entity == PER


def test_closed_without_a_valid_reason_or_source_is_reported_and_closes_nobody():
    L = Ledger()
    L.add("outreach.closed", PER, reason="because", source="user")
    L.add("outreach.closed", PER2, reason="declined", source="carrier-pigeon")
    L.add("outreach.closed", "per_cdefghjkmn", reason=None, source="user")
    result = ofold.fold_people(L.events, NOW)
    assert len(result.errors) == 3 and result.flags == {}


def test_target_events_do_not_create_people_or_errors_in_the_person_fold():
    L = Ledger()
    L.add("outreach.target_added", TGT, action="add target example-corp")
    L.add("outreach.target_approved", TGT)
    L.add("outreach.target_dropped", TGT, reason="x")
    result = ofold.fold_people(L.events, NOW)
    assert result.flags == {} and result.errors == ()


def test_fold_is_independent_of_input_order_by_seq_and_does_not_mutate():
    L = Ledger()
    L.item("linkedin_invite", PER, ts(100))
    L.outcome("accepted", PER, ts(200))
    L.outcome("replied", PER, ts(300))
    shuffled = list(L.events)
    random.Random(1).shuffle(shuffled)
    snapshot = [dict(e) for e in L.events]
    assert ofold.fold_people(shuffled, NOW) == ofold.fold_people(L.events, NOW)
    assert L.events == snapshot


# --- outcome transitions against an independent table-driven simulator -------------------------------------------------

# The simulator keeps its own literal tables; it shares nothing with rules.py.
SIM_NEEDS_ANY = {"accepted": ("inv",), "replied": ("inv", "msg"), "closed": ()}
SIM_BLOCKED_BY = {"accepted": ("acc", "clo"), "replied": ("rep", "clo"), "closed": ("clo",)}
SIM_SLOT = {"accepted": "acc", "replied": "rep", "closed": "clo"}
SIM_LABELS = (("clo", "closed"), ("rep", "replied"), ("acc", "connected"), ("msg", "messaged"), ("inv", "invited"))


def simulate(ops):
    """ops: [(kind, ts_or_None)] in ledger order -> (slots, refused_count, last_activity, label)."""
    slots = {"inv": None, "msg": None, "acc": None, "rep": None, "clo": None}
    refused, last = 0, None
    for kind, at in ops:
        if kind == "invite":
            if slots["inv"] is not None:
                refused += 1
            slots["inv"] = at if slots["inv"] is None else min(slots["inv"], at)
            last = at if last is None else max(last, at)
        elif kind == "message":
            slots["msg"] = at if slots["msg"] is None else max(slots["msg"], at)
            last = at if last is None else max(last, at)
        elif kind.startswith("bad_"):
            refused += 1                    # a malformed outcome is reported (one error) and changes nothing at all
        else:
            needs, blocked = SIM_NEEDS_ANY[kind], SIM_BLOCKED_BY[kind]
            ok = (not needs or any(slots[s] is not None for s in needs)) and not any(slots[s] is not None for s in blocked)
            if not ok:
                refused += 1
                continue
            slots[SIM_SLOT[kind]] = at
            if kind in ("accepted", "replied"):
                last = at if last is None else max(last, at)
    label = next((name for slot, name in SIM_LABELS if slots[slot] is not None), "discovered")
    return slots, refused, last, label


def build_events(ops, rng, noise):
    """Turn ops into ledger events (key-event ts recorded into the returned ops). `noise` inserts outreach.rejected."""
    L = Ledger()
    placed = []
    clock = 0

    def tick():
        nonlocal clock
        clock += 1
        return ts(clock)

    for kind, _ in ops:
        if noise and rng.random() < 0.4:
            L.add("outreach.rejected", PER, tick(), reason="refused")
        if kind in ("invite", "message"):
            at = tick()
            L.item("linkedin_invite" if kind == "invite" else "linkedin_message", PER, at, steps_ts=at)
            placed.append((kind, at))
        elif kind.startswith("bad_"):
            at = tick()
            real = kind[4:]
            if real == "closed" and rng.random() < 0.5:
                L.outcome(real, PER, at, reason=rng.choice([None, "because", ""]))
            else:
                L.outcome(real, PER, at, source=rng.choice([None, "carrier-pigeon", "USER", 7]))
            placed.append((kind, at))
        else:
            at = tick()
            L.outcome(kind, PER, at)
            placed.append((kind, at))
        if noise and rng.random() < 0.3:
            L.add("outreach.rejected", PER, tick(), reason="refused again")
    return L.events, placed


def test_fold_agrees_with_the_independent_simulator_over_20000_random_sequences_and_rejections_change_nothing():
    rng = random.Random(20261019)
    kinds = ("invite", "message", "accepted", "replied", "closed", "bad_accepted", "bad_replied", "bad_closed")
    for n in range(20_000):
        ops = [(rng.choice(kinds), None) for _ in range(rng.randrange(1, 9))]
        events, placed = build_events(ops, rng, noise=False)
        noisy_events, _ = build_events(ops, random.Random(n), noise=True)
        slots, refused, last, label = simulate(placed)
        result = ofold.fold_people(events, NOW)
        flags = result.flags.get(PER, PersonFlags())
        got = {"inv": flags.invited_at, "msg": flags.messaged_at, "acc": flags.accepted_at, "rep": flags.replied_at, "clo": flags.closed_at}
        assert got == slots, ops
        assert len(result.errors) == refused and flags.label == label and flags.last_activity == last, ops
        assert not flags.invite_in_flight
        noisy = ofold.fold_people(noisy_events, NOW)
        assert len(noisy.errors) == len(result.errors)
        a, b = noisy.flags.get(PER, PersonFlags()), flags
        # the noisy ledger has other timestamps, so compare which flags are set, the label and the activity presence
        assert [x is None for x in (a.invited_at, a.messaged_at, a.accepted_at, a.replied_at, a.closed_at, a.last_activity)] == \
               [x is None for x in (b.invited_at, b.messaged_at, b.accepted_at, b.replied_at, b.closed_at, b.last_activity)], ops
        assert a.label == b.label, ops


# --- a malformed outcome event is reported (OUT005) and never applied -------------------------------------------------------

BAD_SOURCES = (None, "carrier-pigeon", "USER", 7)
MALFORMED = (
    [("accepted", {"source": s}) for s in BAD_SOURCES]
    + [("replied", {"source": s}) for s in BAD_SOURCES]
    + [("closed", {"source": s}) for s in BAD_SOURCES]
    + [("closed", {"reason": r}) for r in (None, "", "because", 3)]
)
MALFORMED_IDS = [f"{k}-{'-'.join(f'{a}={b!r}' for a, b in kw.items())}" for k, kw in MALFORMED]


def invited_ledger():
    L = Ledger()
    L.item("linkedin_invite", PER, ts(100))
    L.item("linkedin_message", PER, ts(150))
    return L


def due_in_queue(flags_by_person, now):
    p = Person(PER, URL, "Alex", "h", "Acme Corp", "acme-corp", "recruiter", None, (FoundIn("job", JOB),), "2026-01-01")
    return [i.entity_id for i in derive.build_queue({PER: p}, flags_by_person, {}, [], now).items]


@pytest.mark.parametrize("kind,bad", MALFORMED, ids=MALFORMED_IDS)
def test_a_malformed_outcome_event_is_reported_and_changes_no_derived_state(kind, bad):
    base = invited_ledger()
    expected = ofold.fold_people(base.events, NOW)
    L = invited_ledger()
    L.outcome(kind, PER, ts(200), **bad)
    got = ofold.fold_people(L.events, NOW)
    assert got.flags == expected.flags and got.flags[PER].label == expected.flags[PER].label == "messaged"
    assert expected.errors == ()
    (err,) = got.errors
    assert err.entity == PER and err.message.startswith(f"outreach.{kind}: ")
    assert ("source must be" if "source" in bad else "needs a reason") in err.message
    due = "2026-03-01T00:00:00Z"
    assert due_in_queue(got.flags, due) == due_in_queue(expected.flags, due)


@pytest.mark.parametrize("kind,bad", MALFORMED, ids=MALFORMED_IDS)
def test_a_later_valid_event_of_the_same_kind_applies_after_a_malformed_one(kind, bad):
    L = invited_ledger()
    L.outcome(kind, PER, ts(200), **bad)
    L.outcome(kind, PER, ts(300))
    got = ofold.fold_people(L.events, NOW)
    clean = invited_ledger()
    clean.outcome(kind, PER, ts(300))
    assert got.flags == ofold.fold_people(clean.events, NOW).flags
    assert getattr(got.flags[PER], f"{kind}_at") == ts(300) and len(got.errors) == 1


def test_a_malformed_accepted_does_not_remove_an_invited_person_from_the_due_queue():
    L = Ledger()
    L.item("linkedin_invite", PER, ts(100))
    L.outcome("accepted", PER, ts(200), source="carrier-pigeon")
    flags = ofold.fold_people(L.events, NOW).flags
    assert flags[PER].accepted_at is None and flags[PER].label == "invited" and flags[PER].last_activity == ts(100)
    assert due_in_queue(flags, ts(timedelta(days=8))) == [PER]


def test_a_malformed_closed_sets_neither_closed_at_nor_reason_nor_last_activity_and_queue_still_lists_the_person():
    L = Ledger()
    L.item("linkedin_invite", PER, ts(100))
    L.outcome("closed", PER, ts(200), reason="because")
    flags = ofold.fold_people(L.events, NOW).flags[PER]
    assert (flags.closed_at, flags.closed_reason, flags.last_activity, flags.label) == (None, None, ts(100), "invited")
    assert due_in_queue({PER: flags}, ts(timedelta(days=8))) == [PER]


def test_a_malformed_outcome_for_an_unknown_person_creates_no_person():
    L = Ledger()
    L.outcome("accepted", PER, ts(5), source="nope")
    result = ofold.fold_people(L.events, NOW)
    assert result.flags == {} and len(result.errors) == 1


def test_apply_outcome_every_flag_combination_against_an_explicit_rule():
    names = ("invited_at", "messaged_at", "accepted_at", "replied_at", "closed_at")
    for combo in itertools.product([False, True], repeat=5):
        flags = PersonFlags(**{n: (ts(10 + i) if on else None) for i, (n, on) in enumerate(zip(names, combo))})
        inv, msg, acc, rep, clo = combo
        legal = {"accepted": inv and not acc and not clo, "replied": (inv or msg) and not rep and not clo, "closed": not clo}
        for outcome, ok in legal.items():
            got = ofold.apply_outcome(flags, outcome, ts(99), reason="declined" if outcome == "closed" else None)
            if ok:
                assert isinstance(got, PersonFlags)
                assert getattr(got, f"{ {'accepted': 'accepted', 'replied': 'replied', 'closed': 'closed'}[outcome] }_at") == ts(99)
                other = {n: getattr(got, n) for n in names if n != f"{outcome}_at"}
                assert other == {n: getattr(flags, n) for n in names if n != f"{outcome}_at"}
            else:
                assert isinstance(got, ofold.Refusal) and got.code == "wrong_state", (combo, outcome)


def test_apply_outcome_rejects_unknown_outcomes_and_keeps_closed_terminal():
    with pytest.raises(ValueError):
        ofold.apply_outcome(PersonFlags(), "bogus", ts(1))
    closed = PersonFlags(invited_at=ts(1), closed_at=ts(2))
    for outcome in rules.OUTCOMES:
        assert isinstance(ofold.apply_outcome(closed, outcome, ts(3)), ofold.Refusal)


def test_label_precedence_for_every_flag_combination():
    names = ("invited_at", "messaged_at", "accepted_at", "replied_at", "closed_at")
    for combo in itertools.product([False, True], repeat=5):
        flags = PersonFlags(**{n: ("x" if on else None) for n, on in zip(names, combo)})
        inv, msg, acc, rep, clo = combo
        expected = ("closed" if clo else "replied" if rep else "connected" if acc else "messaged" if msg else "invited" if inv else "discovered")
        assert flags.label == expected, combo
    assert PersonFlags().label == "discovered"


def test_person_flags_is_frozen():
    with pytest.raises(Exception):
        PersonFlags().invited_at = "x"


# --- target decisions --------------------------------------------------------------------------------------------------

def test_every_pair_of_the_decision_table():
    expected = {("candidate", "approve"): "approved", ("candidate", "drop"): "dropped", ("approved", "drop"): "dropped"}
    for status, op in itertools.product(("candidate", "approved", "dropped"), ("approve", "drop")):
        got = ofold.apply_decision(status, op)
        if (status, op) in expected:
            assert got == expected[(status, op)]
        else:
            assert isinstance(got, ofold.Refusal) and got.code == "wrong_state" and got.state == status, (status, op)
    with pytest.raises(ValueError):
        ofold.apply_decision("candidate", "bogus")
    with pytest.raises(ValueError):
        ofold.apply_decision("bogus", "approve")


def tadded(L, tgt=TGT, slug_="example-corp", at=None):
    return L.add("outreach.target_added", tgt, at, **ofold.target_added_fields(f"targets/{slug_}.md", SHA, slug_))


def test_target_added_fields_round_trip_and_validate():
    fields = ofold.target_added_fields("targets/example-corp.md", SHA, "example-corp")
    assert fields["artifacts"] == ["targets/example-corp.md", f"sha256:{SHA}"] and "example-corp" in fields["action"]
    assert set(fields) == {"action", "artifacts"}
    for bad in (("targets/x.md", "z" * 64, "x"), ("targets/x.md", SHA, "Bad Slug"), ("targets/x.md", SHA, "")):
        with pytest.raises(ValueError):
            ofold.target_added_fields(*bad)


def trec(slug_="example-corp", tgt=TGT, evidence=(), sponsorship="n/a", company="Example Corp"):
    return Target(tgt, slug_, company, "Australia", 8, sponsorship, tuple(Evidence("s", d) for d in evidence))


def prec(per=PER, found=((("target", TGT)),), company="Example Corp"):
    return Person(per, URL if per == PER else URL2, "A", "", company, slug.slugify(company), "peer", None,
                  tuple(FoundIn(k, i) for k, i in found), "2026-01-01")


def fold_t(L, targets=(), people_=(), jobs=(), now=NOW):
    return ofold.fold_targets(L.events, {t.slug: t for t in targets}, {p.id: p for p in people_}, list(jobs), now)


def test_target_decision_state_follows_the_events_and_illegal_ones_are_reported():
    L = Ledger()
    tadded(L)
    result = fold_t(L)
    view = result.targets["example-corp"]
    assert (view.id, view.slug, view.decision, view.status, result.errors) == (TGT, "example-corp", "candidate", "candidate", ())
    L.add("outreach.target_approved", TGT)
    assert fold_t(L).targets["example-corp"].decision == "approved"
    L.add("outreach.target_approved", TGT)                          # approving an approved target
    result = fold_t(L)
    assert len(result.errors) == 1 and result.targets["example-corp"].decision == "approved"
    L.add("outreach.target_dropped", TGT, reason="no")
    L.add("outreach.target_approved", TGT)                          # approving after a drop
    L.add("outreach.target_dropped", TGT, reason="again")           # dropping a dropped target
    result = fold_t(L)
    assert len(result.errors) == 3 and result.targets["example-corp"].decision == "dropped"
    assert result.targets["example-corp"].status == "dropped"


def test_malformed_target_events_are_reported_never_dropped():
    L = Ledger()
    L.add("outreach.target_approved", TGT)                          # before target_added
    L.add("outreach.target_added", None, action="add target x", artifacts=["targets/x.md", "sha256:" + SHA])
    L.add("outreach.target_added", "weird", action="add target x", artifacts=["targets/x.md", "sha256:" + SHA])
    L.add("outreach.target_added", TGT, action="what", artifacts=["targets/x.md", "sha256:" + SHA])
    L.add("outreach.target_added", TGT, action="add target example-corp", artifacts=["bad"])
    tadded(L, TGT, "example-corp")                                  # the same entity again
    tadded(L, TGT, "example-corp")                                  # and once more
    tadded(L, TGT2, "example-corp")                                 # the same slug under another entity
    L.add("outreach.target_dropped", TGT, reason=None)              # a drop without a reason
    result = fold_t(L)
    assert len(result.errors) == 9
    assert list(result.targets) == ["example-corp"] and result.targets["example-corp"].id == TGT
    assert result.targets["example-corp"].decision == "dropped"


def test_target_fold_ignores_audit_only_rejections_and_person_events():
    L = Ledger()
    tadded(L)
    L.add("outreach.rejected", TGT, ts(10 ** 6), reason="x")
    L.add("outreach.accepted", PER, source="user")
    clean = Ledger()
    tadded(clean)
    a, b = fold_t(L), fold_t(clean)
    assert a.targets["example-corp"].last_activity == b.targets["example-corp"].last_activity and a.errors == ()


# --- displayed status ----------------------------------------------------------------------------------------------------

def test_status_precedence_for_every_combination_of_conditions():
    for dropped, person_state, opening, approved in itertools.product([False, True], ["none", "contacted", "warm"], [False, True], [False, True]):
        L = Ledger()
        tadded(L)
        if approved:
            L.add("outreach.target_approved", TGT)
        if dropped:
            L.add("outreach.target_dropped", TGT, reason="no")
        recs = []
        if person_state != "none":
            L.add("outreach.person_added", PER, **people.person_added_fields(PEOPLE_REL, SHA, URL))
            L.item("linkedin_invite", PER, ts(1000))
            if person_state == "warm":
                L.outcome("accepted", PER, ts(1001))
            recs.append(prec())
        jobs = [JobInfo(JOB, "Example Corp", "EVALUATED", ())] if opening else []
        view = fold_t(L, [trec()], recs, jobs).targets["example-corp"]
        # an explicit if-chain, independent of the production precedence table
        if dropped:
            expected = "dropped"
        elif person_state == "warm":
            expected = "warm"
        elif person_state == "contacted":
            expected = "contacted"
        elif opening:
            expected = "opening-seen"
        elif approved:
            expected = "approved"
        else:
            expected = "candidate"
        assert view.status == expected, (dropped, person_state, opening, approved)
        assert view.decision == ("dropped" if dropped else "approved" if approved else "candidate")


def test_a_replied_person_makes_a_target_warm_but_a_closed_one_does_not():
    L = Ledger()
    tadded(L)
    L.item("linkedin_invite", PER, ts(1000))
    L.outcome("replied", PER, ts(1001))
    assert fold_t(L, [trec()], [prec()]).targets["example-corp"].status == "warm"
    L.outcome("closed", PER, ts(1002))
    assert fold_t(L, [trec()], [prec()]).targets["example-corp"].status == "contacted"


def test_accepted_then_closed_is_contacted_not_warm():
    L = Ledger()
    tadded(L)
    L.item("linkedin_invite", PER, ts(1000))
    L.outcome("accepted", PER, ts(1001))
    assert fold_t(L, [trec()], [prec()]).targets["example-corp"].status == "warm"
    L.outcome("closed", PER, ts(1002))
    assert fold_t(L, [trec()], [prec()]).targets["example-corp"].status == "contacted"


def test_a_message_only_person_is_not_contacted_because_contact_means_an_invite():
    L = Ledger()
    tadded(L)
    L.item("linkedin_message", PER, ts(1000))
    assert fold_t(L, [trec()], [prec()]).targets["example-corp"].status == "candidate"


def test_only_people_linked_to_the_target_count_and_job_links_do_not():
    L = Ledger()
    tadded(L)
    L.item("linkedin_invite", PER, ts(1000))
    other = prec(found=(("target", TGT2),))
    jobish = prec(found=(("job", JOB),))
    empty = prec(found=())
    for rec in (other, jobish, empty):
        view = fold_t(L, [trec()], [rec]).targets["example-corp"]
        assert view.status == "candidate" and view.people == ()
    view = fold_t(L, [trec()], [prec()]).targets["example-corp"]
    assert view.status == "contacted" and view.people == (PER,)


def test_opening_seen_matches_the_company_slug_of_any_saved_job():
    L = Ledger()
    tadded(L)
    cases = [("Example Corp", True), ("EXAMPLE, corp.", True), ("Example Corporation", False), ("会社", False), ("", False)]
    for company, expected in cases:
        jobs = [JobInfo(JOB, company, "REJECTED", ())]                    # a terminal job still counts as a saved job
        assert (fold_t(L, [trec()], (), jobs).targets["example-corp"].status == "opening-seen") is expected, company


def test_a_person_record_that_was_never_recorded_in_the_ledger_has_no_flags_and_links_only():
    L = Ledger()
    tadded(L)
    view = fold_t(L, [trec()], [prec()]).targets["example-corp"]
    assert view.status == "candidate" and view.people == (PER,)


def test_orphan_target_records_are_not_in_the_result_and_a_missing_record_still_gives_a_view():
    L = Ledger()
    tadded(L)
    result = fold_t(L, [trec(), trec("other-corp", TGT2)])
    assert list(result.targets) == ["example-corp"]
    assert list(fold_t(L).targets) == ["example-corp"]


# --- stale -----------------------------------------------------------------------------------------------------------------

DAY = timedelta(days=1)
OLD = ts(timedelta(days=1))              # far in the past relative to the NOW used below
STALE_NOW_DELTA = timedelta(days=200)
SNOW = ts(STALE_NOW_DELTA)


def date_of(delta):
    return (T0 + delta).strftime("%Y-%m-%d")


def stale_view(L, *, evidence=(), recs=(), now=SNOW):
    return fold_t(L, [trec(evidence=evidence, sponsorship="likely" if evidence else "n/a")], recs, (), now).targets["example-corp"]


def base_ledger(created=OLD):
    L = Ledger()
    tadded(L, at=created)
    return L


def at_age(age_seconds):
    """The ts that is `age_seconds` before SNOW."""
    return ts(STALE_NOW_DELTA - timedelta(seconds=age_seconds))


SIXTY = 60 * 86400


@pytest.mark.parametrize("qualifier", ["target_added", "target_approved", "person_added", "consumed_invite", "consumed_message",
                                       "accepted", "replied"])
def test_each_qualifying_event_refreshes_activity_and_the_boundary_is_strict(qualifier):
    for age, stale in ((SIXTY, False), (SIXTY + 1, True), (SIXTY - 1, False)):
        when = at_age(age)
        L = base_ledger(created=when if qualifier == "target_added" else OLD)
        recs = [prec()]
        if qualifier == "target_approved":
            L.add("outreach.target_approved", TGT, when)
        elif qualifier == "person_added":
            L.add("outreach.person_added", PER, when, **people.person_added_fields(PEOPLE_REL, SHA, URL))
        elif qualifier == "consumed_invite":
            L.item("linkedin_invite", PER, when, steps_ts=OLD)
        elif qualifier == "consumed_message":
            L.item("linkedin_message", PER, when, steps_ts=OLD)
        elif qualifier in ("accepted", "replied"):
            L.item("linkedin_invite", PER, OLD, steps_ts=OLD)
            L.outcome(qualifier, PER, when)
        view = stale_view(L, recs=recs)
        assert view.stale is stale, (qualifier, age)
        assert view.last_activity == when, (qualifier, age)


def test_evidence_read_on_counts_as_midnight_utc_and_the_boundary_is_strict():
    read_on = date_of(timedelta(days=100))
    midnight = T0 + timedelta(days=100)
    for offset, stale in ((0, False), (1, True), (-1, False)):
        now = ts(midnight - T0 + timedelta(days=60, seconds=offset))
        L = base_ledger()
        view = fold_t(L, [trec(evidence=(read_on,), sponsorship="likely")], (), (), now).targets["example-corp"]
        assert view.stale is stale and view.last_activity == ts(timedelta(days=100)), offset


def test_newest_of_several_sources_wins():
    L = base_ledger()
    L.add("outreach.target_approved", TGT, ts(timedelta(days=30)))
    view = fold_t(L, [trec(evidence=(date_of(timedelta(days=50)), date_of(timedelta(days=10))), sponsorship="likely")], (), (), SNOW).targets["example-corp"]
    assert view.last_activity == ts(timedelta(days=50))


def test_the_creation_event_alone_defines_activity():
    L = base_ledger(created=at_age(SIXTY))
    view = stale_view(L)
    assert view.last_activity == at_age(SIXTY) and view.stale is False
    L = base_ledger(created=at_age(SIXTY + 1))
    assert stale_view(L).stale is True


def test_non_qualifying_events_never_refresh_activity():
    L = base_ledger()                                           # created long ago
    recent = at_age(10)
    L.add("outreach.rejected", TGT, recent, reason="x")
    L.add("outreach.rejected", PER, recent, reason="x")
    L.item("linkedin_invite", PER, OLD, steps_ts=recent)          # approved/started recent, consumed old
    L.outcome("closed", PER, recent)
    L.add("approval.rejected", "apr_aaaaaaaaaa", recent, reason="x")
    L.add("job.status_changed", "job_abcdefghjk", recent)
    view = stale_view(L, recs=[prec()])
    assert view.stale is True and view.last_activity == OLD
    # a linked person's activity counts, an unlinked person's does not
    L2 = base_ledger()
    L2.item("linkedin_invite", PER2, recent, url=URL2, steps_ts=OLD)
    assert stale_view(L2, recs=[prec(PER2, (("target", TGT),))]).stale is False
    assert stale_view(L2, recs=[prec(PER2, (("target", TGT2),))]).stale is True
    assert stale_view(L2, recs=[]).stale is True


def test_dropped_targets_are_never_stale_and_keep_their_last_activity():
    L = base_ledger()
    L.add("outreach.target_dropped", TGT, OLD, reason="no")
    view = stale_view(L)
    assert view.stale is False and view.status == "dropped" and view.last_activity == OLD
    L2 = base_ledger()
    L2.add("outreach.target_approved", TGT, OLD)
    assert stale_view(L2).stale is True


def test_an_unparseable_now_is_a_value_error():
    L = base_ledger()
    with pytest.raises(ValueError):
        fold_t(L, now="yesterday")


# --- purity and import isolation ------------------------------------------------------------------------------------------

def _imports(code):
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_importing_the_outreach_package_pulls_in_no_heavy_module():
    _imports("import sys, importlib;"
             "[importlib.import_module('careeros.core.outreach.' + m) for m in ('rules','slug','model','person','target','fold')];"
             "import careeros.core.outreach;"
             "bad=[m for m in sys.modules if m in ('careeros.core.validation','careeros.core.state_machine','careeros.core.approvals.service',"
             "'careeros.core.ledger','careeros.core.compensation.service','careeros.core.evaluation.service','careeros.cli') "
             "or m.startswith('careeros.cli.')];"
             "print(bad); sys.exit(1 if bad else 0)")


def test_importing_approvals_people_does_not_import_outreach_or_the_heavy_modules():
    _imports("import sys; import careeros.core.approvals.people;"
             "bad=[m for m in sys.modules if m.startswith('careeros.core.outreach') or m in ('careeros.core.validation',"
             "'careeros.core.state_machine','careeros.core.approvals.service','careeros.core.ledger') or m.startswith('careeros.cli')];"
             "print(bad); sys.exit(1 if bad else 0)")


def test_modules_read_no_clock_and_no_filesystem():
    import ast
    import inspect
    from careeros.core.outreach import model
    for module in (rules, slug, model, person, target, ofold, people):
        tree = ast.parse(inspect.getsource(module))
        imported = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names} | \
                   {n.module.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
        assert not imported & {"os", "pathlib", "time", "shutil", "tempfile", "subprocess", "socket", "random", "secrets"}, module.__name__
        src = inspect.getsource(module)
        assert "utc_now" not in src and "datetime.now" not in src and "datetime.utcnow" not in src and "open(" not in src, module.__name__


def test_the_target_id_prefix_and_the_rules_constants():
    assert ids.PREFIXES["target"] == "tgt" and ids.is_valid_id("target", TGT) and ids.new_id("target").startswith("tgt_")
    assert (rules.INVITE_FOLLOWUP_DAYS, rules.APPLICATION_FOLLOWUP_DAYS, rules.CAP_WINDOW_DAYS, rules.DEFAULT_WEEKLY_CAP,
            rules.TARGET_STALE_DAYS, rules.MAX_CONTACTS_PER_TARGET) == (7, 10, 7, 15, 60, 2)
    assert rules.ROLES == ("recruiter", "hiring_manager", "peer", "other")
    assert rules.SPONSORSHIP == ("offered", "likely", "unknown", "not_offered", "n/a")
    assert rules.CLOSED_REASONS == ("no_response", "declined", "not_a_fit", "other")
    assert rules.OUTCOME_SOURCES == ("user", "observed", "email")
    assert rules.EVENT_PREFIX == "outreach."
    assert {rules.EV_PERSON_ADDED, rules.EV_TARGET_ADDED, rules.EV_TARGET_APPROVED, rules.EV_TARGET_DROPPED, rules.EV_ACCEPTED,
            rules.EV_REPLIED, rules.EV_CLOSED, rules.EV_REJECTED} == {
        "outreach.person_added", "outreach.target_added", "outreach.target_approved", "outreach.target_dropped",
        "outreach.accepted", "outreach.replied", "outreach.closed", "outreach.rejected"}
    assert rules.AUDIT_ONLY_TYPES == ("outreach.rejected",)
    assert {"not_found", "wrong_state", "duplicate", "invalid_input", "clock_skew", "ledger_corrupt", "identity_conflict",
            "cap_config_invalid", "person_closed", "cap_reached"} == set(rules.REFUSAL_CODES)
    assert (rules.OUT001, rules.OUT005, rules.OUT100) == ("OUT001", "OUT005", "OUT100")
    assert rules.STATUS_PRECEDENCE == ("dropped", "warm", "contacted", "opening-seen", "approved", "candidate")
    assert rules.LABEL_PRECEDENCE == ("closed", "replied", "connected", "messaged", "invited", "discovered")
