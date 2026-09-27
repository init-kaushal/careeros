import json
from careeros.llm import complete
from careeros.core.models import Profile

_CONTENT_CAP = 4000

# Instruction-only templates — resume text is concatenated, never interpolated,
# so braces in user content cannot cause KeyError or prompt injection via formatting.
_PROFILE_INSTRUCTIONS = """\
Extract the following fields from this resume as JSON.
Return ONLY valid JSON — no markdown, no explanation.
Use null for any field not found.

{
  "name": "<full name>",
  "email": "<email or null>",
  "title": "<current or most recent job title or null>",
  "years_of_experience": <total years as integer or null>,
  "location": "<city, state/country or null>",
  "summary": "<1-2 sentence professional summary or null>"
}

Resume:
"""


def _parse_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
    return json.loads(text)


def extract_basic_profile(
    resume_text: str,
    model: str | None = None,
) -> Profile:
    """Extract identity fields from a resume.

    Skills are NOT extracted here — careeros.skills.resume_ingest.ingest_resume
    owns those, because a skill needs verified evidence and this does not
    produce any.
    """
    capped_text = resume_text[:_CONTENT_CAP]

    try:
        profile_resp = complete(
            model=model,
            max_tokens=512,
            messages=[{"role": "user", "content": _PROFILE_INSTRUCTIONS + capped_text}],
        )
        profile_data = _parse_json(profile_resp.choices[0].message.content)
        profile = Profile.model_validate(profile_data)

        return profile
    except Exception:
        return Profile()
