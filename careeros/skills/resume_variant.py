from __future__ import annotations

import json
import os
from dataclasses import dataclass

import litellm

from careeros.core.models import Evidence, Skills, VariantSection
from careeros.skills.resume_evidence import normalize_quote, verify_quote
from careeros.skills.sanitize import wrap_untrusted

DEFAULT_LLM_MODEL = "claude-haiku-4-5-20251001"

# 11a caps ingest content at 4000, roughly one page. A two-page resume would
# have its tail invisible to the selector, so this is larger. Verification
# runs against exactly this capped text, never the full file.
_MASTER_CAP = 8000

_SKILL_HINT_CAP = 500

# Headings are rendered text on the finished document, so a free-text heading
# is a fabrication surface like any other claim: nothing otherwise stops a
# model titling a section "Perfect Match For This Role".
ALLOWED_HEADINGS = ("Summary", "Experience", "Skills", "Education", "Projects")

# Heading matching is case-insensitive, but the stored heading is always the
# canonical allowlist spelling, never whatever casing the model sent — so the
# rendered heading is still one of exactly five known strings and the
# fabrication surface does not widen.
_HEADING_LOOKUP = {h.casefold(): h for h in ALLOWED_HEADINGS}

_DROPPED_LABEL_CAP = 80

_VARIANT_INSTRUCTIONS = """\
You are tailoring a resume to one job description.
Return ONLY valid JSON — no markdown, no explanation.

You may ONLY select text that already appears in the resume. Every "quote"
must be a span copied from the resume character for character, 200
characters or fewer. Do not paraphrase, reword, summarise, translate, or
stitch together separate spans. Do not write any new text of your own. If
you cannot find a verbatim span, omit it.

Select the spans most relevant to the job description and order them most
relevant first. Prefer spans evidencing a skill the job asks for. Aim for
at most 12 spans in total.

"heading" must be exactly one of: Summary, Experience, Skills, Education, Projects

{"sections": [{"heading": "<heading>", "quotes": ["<verbatim span>"]}]}
"""


@dataclass(frozen=True)
class VariantResult:
    sections: tuple[VariantSection, ...]
    dropped: tuple[str, ...]
    # Set only when the LLM call itself failed (bad API key, network error,
    # provider outage) — distinct from "the model answered but nothing it
    # proposed verified," which is a normal outcome and not an error.
    error: str | None = None

    def entry_count(self) -> int:
        return sum(len(s.entries) for s in self.sections)


def _parse_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
    return json.loads(text)


def _note(dropped: list[str], label: str) -> None:
    label = label[:_DROPPED_LABEL_CAP]
    if label and label not in dropped:
        dropped.append(label)


def select_variant_content(
    jd_text: str,
    master_text: str,
    skills: Skills,
    source_file: str,
    model: str | None = None,
) -> VariantResult:
    """Select verbatim spans of the master resume relevant to one job.

    Total, never raises: any LLM failure, parse failure, or malformed section
    or quote yields a VariantResult rather than propagating an exception.
    The `resume variant` command depends on this holding for every possible
    model response, not just well-formed ones.

    `skills` is a hint only. It supplies the names of skills already carrying
    verified Evidence so the selector can favour spans the job asks about; it
    can never contribute text, because every quote must still be a verbatim
    span of the capped master resume.
    """
    effective_model = model or os.environ.get("CAREEROS_MODEL", DEFAULT_LLM_MODEL)
    capped = master_text[:_MASTER_CAP]

    hint = ", ".join(
        s.name for s in skills.skills if isinstance(s.name, str) and s.name
    )[:_SKILL_HINT_CAP]

    try:
        resp = litellm.completion(
            model=effective_model,
            max_tokens=2048,
            messages=[
                {"role": "system", "content": _VARIANT_INSTRUCTIONS},
                {"role": "user", "content": (
                    "Verified skills (hint for ranking only — never copy from this list): "
                    + hint
                    + "\n\nJob description:\n" + wrap_untrusted(jd_text)
                    + "\n\nResume:\n" + wrap_untrusted(capped)
                )},
            ],
        )
    except Exception as exc:
        return VariantResult(sections=(), dropped=(), error=str(exc) or type(exc).__name__)

    try:
        payload = _parse_json(resp.choices[0].message.content)
    except Exception:
        return VariantResult(sections=(), dropped=())

    raw_sections = payload.get("sections") if isinstance(payload, dict) else None
    sections_in = raw_sections if isinstance(raw_sections, list) else []

    sections: list[VariantSection] = []
    dropped: list[str] = []
    seen: set[str] = set()

    for raw_section in sections_in:
        if not isinstance(raw_section, dict):
            continue

        raw_heading = raw_section.get("heading")
        stripped = raw_heading.strip() if isinstance(raw_heading, str) else ""
        heading = _HEADING_LOOKUP.get(stripped.casefold(), "")
        if not heading:
            _note(dropped, "section heading: " + (stripped or "<missing>"))
            continue

        raw_quotes = raw_section.get("quotes")
        quotes_in = raw_quotes if isinstance(raw_quotes, list) else []

        entries: list[Evidence] = []
        for quote in quotes_in:
            try:
                if not isinstance(quote, str):
                    raise ValueError("quote is not a string")
                key = normalize_quote(quote)
                if not key:
                    raise ValueError("quote is empty")
                if key in seen:
                    # A collapsed duplicate is not a failure and must not be
                    # reported as dropped.
                    continue
                line = verify_quote(quote, capped)
                if line is None:
                    raise ValueError("quote did not verify")
                entries.append(Evidence(quote=quote, line=line, source_file=source_file))
                seen.add(key)
            except Exception:
                # A malformed or unverifiable span is a dropped span, not a
                # dead section.
                _note(dropped, quote.strip() if isinstance(quote, str) else repr(quote))
                continue

        if entries:
            sections.append(VariantSection(heading=heading, entries=entries))

    return VariantResult(sections=tuple(sections), dropped=tuple(dropped))
