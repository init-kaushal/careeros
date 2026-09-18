import json
import os
import litellm
from careeros.core.models import Profile, Skills

DEFAULT_LLM_MODEL = "claude-haiku-4-5-20251001"
_JD_CAP = 4000

_SCORE_INSTRUCTIONS = """\
You are evaluating how well a candidate profile matches a job description.
Return ONLY valid JSON — no markdown, no explanation.

{
  "score": <integer 1-100>,
  "reasoning": "<1-2 sentences>",
  "strengths": ["<strength>", "..."],
  "gaps": ["<gap>", "..."]
}

Scoring guide:
90-100 = near-perfect match
70-89  = strong fit, minor gaps
50-69  = partial fit, notable gaps
1-49   = significant misalignment

Candidate profile:
"""

_FAILURE = {"score": 0, "reasoning": "Could not score.", "strengths": [], "gaps": []}


def _parse_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
    return json.loads(text)


def _build_profile_text(profile: Profile, skills: Skills) -> str:
    lines = []
    if profile.title:
        lines.append("Title: " + profile.title)
    if profile.summary:
        lines.append("Summary: " + profile.summary)
    if skills.skills:
        names = ", ".join(s.name for s in skills.skills)
        lines.append("Skills: " + names)
    return "\n".join(lines)


def score_job(
    jd_text: str,
    profile: Profile,
    skills: Skills,
    model: str | None = None,
) -> dict:
    effective_model = model or os.environ.get("CAREEROS_MODEL", DEFAULT_LLM_MODEL)
    profile_text = _build_profile_text(profile, skills)
    prompt = _SCORE_INSTRUCTIONS + profile_text + "\n\nJob description:\n" + jd_text[:_JD_CAP]
    try:
        resp = litellm.completion(
            model=effective_model,
            max_tokens=512,
            messages=[{"role": "user", "content": prompt}],
        )
        return _parse_json(resp.choices[0].message.content)
    except Exception:
        return dict(_FAILURE)
