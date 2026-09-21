from __future__ import annotations

import json
import os
from dataclasses import dataclass

import litellm

from careeros.core.models import Evidence, Skill, Skills
from careeros.skills.resume_evidence import verify_quote
from careeros.skills.sanitize import wrap_untrusted

DEFAULT_LLM_MODEL = "claude-haiku-4-5-20251001"
_CONTENT_CAP = 4000

_INGEST_INSTRUCTIONS = """\
Extract the candidate's technical skills from the resume below.
Return ONLY valid JSON — no markdown, no explanation.
Limit to the 20 most prominent skills.

For each skill you MUST supply a "quote": a short verbatim span copied
exactly from the resume, 200 characters or fewer, that demonstrates the
skill. Copy it character for character. Do not paraphrase, summarise, or
construct a quote. If you cannot find a verbatim span for a skill, omit
that skill entirely.

"last_used" is the year the skill was most recently used, inferred from
the role the evidence sits under, or null if unclear.

{"skills": [{"name": "<skill>", "quote": "<verbatim span>", "last_used": "<year or null>"}]}
"""


@dataclass(frozen=True)
class IngestResult:
    skills: Skills
    dropped: tuple[str, ...]


def _parse_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
    return json.loads(text)


def ingest_resume(
    resume_text: str, source_file: str, model: str | None = None
) -> IngestResult:
    """Extract skills, keeping only those whose quote is verifiably present.

    Never raises: any LLM or parse failure yields an empty IngestResult,
    matching the sentinel pattern every other skill in this codebase uses.
    """
    effective_model = model or os.environ.get("CAREEROS_MODEL", DEFAULT_LLM_MODEL)
    capped = resume_text[:_CONTENT_CAP]

    try:
        resp = litellm.completion(
            model=effective_model,
            max_tokens=2048,
            messages=[
                {"role": "system", "content": _INGEST_INSTRUCTIONS},
                {"role": "user", "content": "Resume:\n" + wrap_untrusted(capped)},
            ],
        )
        payload = _parse_json(resp.choices[0].message.content)
    except Exception:
        return IngestResult(skills=Skills(), dropped=())

    verified: list[Skill] = []
    dropped: list[str] = []
    seen: set[str] = set()

    for candidate in payload.get("skills", []):
        if not isinstance(candidate, dict):
            continue
        name = (candidate.get("name") or "").strip()
        if not name:
            continue
        key = name.lower()
        if key in seen:
            continue

        line = verify_quote(candidate.get("quote") or "", capped)
        if line is None:
            seen.add(key)
            dropped.append(name)
            continue

        seen.add(key)
        verified.append(Skill(
            name=name,
            last_used=candidate.get("last_used"),
            source=source_file,
            evidence=Evidence(
                quote=candidate["quote"], line=line, source_file=source_file,
            ),
        ))

    return IngestResult(skills=Skills(skills=verified), dropped=tuple(dropped))
