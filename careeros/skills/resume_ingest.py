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

"last_used" must be a JSON string containing the year the skill was most
recently used, inferred from the role the evidence sits under, or null
if unclear.

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

    # Ensure payload is a dict before trying to extract skills
    candidates = payload.get("skills", []) if isinstance(payload, dict) else []

    verified: list[Skill] = []
    dropped: list[str] = []
    seen: set[str] = set()

    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        name = (candidate.get("name") or "").strip()
        if not name:
            continue
        key = name.lower()
        if key in seen:
            continue

        try:
            quote = candidate.get("quote")
            if not isinstance(quote, str):
                raise ValueError("quote is not a string")
            line = verify_quote(quote, capped)
            if line is None:
                raise ValueError("quote did not verify")
            last_used = candidate.get("last_used")
            skill = Skill(
                name=name,
                last_used=str(last_used) if last_used is not None else None,
                source=source_file,
                evidence=Evidence(quote=quote, line=line, source_file=source_file),
            )
        except Exception:
            # A malformed candidate is a dropped candidate, not a dead batch.
            if name not in dropped:
                dropped.append(name)
            continue

        seen.add(key)
        verified.append(skill)

    # Remove from dropped any names that were eventually verified
    verified_names = {s.name for s in verified}
    final_dropped = tuple(d for d in dropped if d not in verified_names)

    return IngestResult(skills=Skills(skills=verified), dropped=final_dropped)
