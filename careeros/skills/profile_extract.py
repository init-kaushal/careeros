import json
import anthropic
from careeros.core.models import Profile, Skill, Skills


def _parse_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
    return json.loads(text)

EXTRACTION_MODEL = "claude-haiku-4-5-20251001"

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

_SKILLS_INSTRUCTIONS = """\
Extract the top technical skills from this resume as JSON.
Return ONLY valid JSON — no markdown, no explanation.
Limit to 20 most prominent skills.

{"skills": [{"name": "<skill name>", "level": "<beginner|intermediate|advanced|expert or null>", "source": "resume"}]}

Resume:
"""


def extract_basic_profile(
    resume_text: str,
    client: anthropic.Anthropic | None = None,
) -> tuple[Profile, Skills]:
    if client is None:
        client = anthropic.Anthropic()

    profile_resp = client.messages.create(
        model=EXTRACTION_MODEL,
        max_tokens=512,
        messages=[{"role": "user", "content": _PROFILE_INSTRUCTIONS + resume_text}],
    )
    profile_data = _parse_json(profile_resp.content[0].text)
    profile = Profile.model_validate(profile_data)

    skills_resp = client.messages.create(
        model=EXTRACTION_MODEL,
        max_tokens=1024,
        messages=[{"role": "user", "content": _SKILLS_INSTRUCTIONS + resume_text}],
    )
    skills_data = _parse_json(skills_resp.content[0].text)
    skills = Skills(skills=[Skill(**s) for s in skills_data.get("skills", [])])

    return profile, skills
