from html.parser import HTMLParser

from careeros.core.models import Evidence, Profile, ResumeVariant, VariantSection
from careeros.render.resume_html import build_resume_html


def _ev(quote, line=1):
    return Evidence(quote=quote, line=line, source_file="resumes/master.md")


def _variant(sections=None):
    return ResumeVariant(
        job_id="acme-sre-abc1",
        job_company="Acme",
        job_title="Senior SRE",
        generated_at="2026-09-22T00:00:00+00:00",
        source_file="resumes/master.md",
        sections=sections if sections is not None else [
            VariantSection(heading="Skills", entries=[_ev("Python, Go, Kubernetes", 10)]),
        ],
    )


def _profile():
    return Profile(
        name="Alice Johnson",
        email="alice@example.com",
        title="Senior Site Reliability Engineer",
        location="Berlin",
    )


_ALLOWED_TAGS = {"html", "head", "meta", "title", "style", "body",
                 "h1", "p", "h2", "ul", "li"}
_ALLOWED_ATTRS = {"lang", "charset", "class"}


class _TagCollector(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags: set[str] = set()
        self.attrs: set[str] = set()

    def handle_starttag(self, tag, attrs):
        self.tags.add(tag)
        for name, _value in attrs:
            self.attrs.add(name)


def test_contact_header_comes_from_the_profile():
    html = build_resume_html(_variant(), _profile())
    assert "Alice Johnson" in html
    assert "alice@example.com" in html
    assert "Senior Site Reliability Engineer" in html
    assert "Berlin" in html


def test_headings_and_entries_render_in_order():
    variant = _variant([
        VariantSection(heading="Experience", entries=[_ev("Built monitoring", 6)]),
        VariantSection(heading="Skills", entries=[_ev("Python, Go", 10)]),
    ])
    html = build_resume_html(variant, _profile())
    assert html.index("Experience") < html.index("Skills")
    assert html.index("Built monitoring") < html.index("Python, Go")


def test_markup_in_a_quote_is_escaped_not_rendered():
    # Verbatim resume text becomes markup here. A resume saying
    # "C++ & <legacy> tooling" must not produce broken output, and must not
    # be able to inject markup.
    variant = _variant([VariantSection(
        heading="Experience",
        entries=[_ev("Wrote C++ & <legacy> tooling")],
    )])
    html = build_resume_html(variant, _profile())
    assert "<legacy>" not in html
    assert "&lt;legacy&gt;" in html
    assert "C++ &amp; " in html


def test_markup_in_a_profile_field_is_escaped():
    profile = Profile(name="Alice <script>alert(1)</script>", email="a@b.c")
    html = build_resume_html(_variant(), profile)
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_no_external_asset_markup():
    html = build_resume_html(_variant(), _profile())
    # These cannot appear via escaped user text: a quote containing
    # "<link" renders as "&lt;link", so a literal match here means real
    # markup. http:// and src= are NOT safe to assert this way — see
    # test_document_emits_no_asset_referencing_markup below.
    for marker in ("<link", "<script", "@import"):
        assert marker not in html, marker


def test_document_emits_no_asset_referencing_markup():
    # A resume legitimately containing a URL must not be able to introduce a
    # tag or an attribute. Substring checks cannot tell escaped text from
    # live markup, so parse the document and assert an allowlist.
    variant = _variant([VariantSection(heading="Projects", entries=[
        _ev('Built portfolio at http://example.com/me using src="x.png"'),
        _ev('<img src="http://evil/x.png"> <link rel="stylesheet" href="http://evil/s.css">'),
        _ev('<script src="http://evil/x.js"></script>'),
    ])])
    parser = _TagCollector()
    parser.feed(build_resume_html(variant, _profile()))
    assert parser.tags <= _ALLOWED_TAGS, parser.tags - _ALLOWED_TAGS
    assert parser.attrs <= _ALLOWED_ATTRS, parser.attrs - _ALLOWED_ATTRS


def test_stylesheet_references_no_external_resource():
    from careeros.render.resume_html import _CSS
    for marker in ("url(", "@import", "http://", "https://"):
        assert marker not in _CSS, marker


def test_section_with_no_entries_is_not_rendered():
    variant = _variant([
        VariantSection(heading="Skills", entries=[_ev("Python, Go", 10)]),
        VariantSection(heading="Projects", entries=[]),
    ])
    html = build_resume_html(variant, _profile())
    assert "Projects" not in html
    assert "Skills" in html


def test_line_numbers_and_dropped_list_are_not_in_the_document():
    # The sidecar carries provenance; the document an employer sees is clean.
    variant = _variant()
    variant.dropped = ["some rejected span"]
    html = build_resume_html(variant, _profile())
    assert "some rejected span" not in html
    assert "resumes/master.md" not in html


def test_empty_profile_still_renders_a_document():
    html = build_resume_html(_variant(), Profile())
    assert html.startswith("<!DOCTYPE html>")
    assert "Python, Go, Kubernetes" in html
