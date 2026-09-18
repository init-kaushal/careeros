import os
import litellm
from careeros.core.models import Goals, Profile, Skills

DEFAULT_LLM_MODEL = "claude-haiku-4-5-20251001"
_JD_CAP = 4000

_CL_INSTRUCTIONS = """\
Write a concise, professional cover letter (3-4 paragraphs) for the candidate below.
- Open with why this specific role interests them based on their goals
- Highlight 2-3 relevant skills from their profile that match the job description
- Close with a brief call to action
Return ONLY the cover letter text — no subject line, no markdown, no commentary.

Candidate profile:
"""


def _build_profile_text(profile: Profile, skills: Skills, goals: Goals) -> str:
    lines = []
    if profile.title:
        lines.append("Title: " + profile.title)
    if profile.summary:
        lines.append("Summary: " + profile.summary)
    if skills.skills:
        lines.append("Skills: " + ", ".join(s.name for s in skills.skills))
    if goals.short_term:
        lines.append("Short-term goals: " + "; ".join(goals.short_term))
    return "\n".join(lines)


def generate_cover_letter(
    jd_text: str,
    profile: Profile,
    skills: Skills,
    goals: Goals,
    model: str | None = None,
) -> str:
    """Generate a tailored cover letter. Returns '' on any failure. Never raises."""
    effective_model = model or os.environ.get("CAREEROS_MODEL", DEFAULT_LLM_MODEL)
    profile_text = _build_profile_text(profile, skills, goals)
    prompt = _CL_INSTRUCTIONS + profile_text + "\n\nJob description:\n" + jd_text[:_JD_CAP]
    try:
        resp = litellm.completion(
            model=effective_model,
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )
        content = resp.choices[0].message.content
        if not content:
            return ""
        return content.strip()
    except Exception:
        return ""
