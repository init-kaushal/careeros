import json
from careeros.core.models import Profile, Skills
from careeros.llm import complete
from careeros.skills.sanitize import wrap_untrusted
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
    profile_text = _build_profile_text(profile, skills)
    system_text = _SCORE_INSTRUCTIONS + profile_text
    user_text = "Job description:\n" + wrap_untrusted(jd_text[:_JD_CAP])
    try:
        resp = complete(
            model=model,
            max_tokens=512,
            messages=[
                {"role": "system", "content": system_text},
                {"role": "user", "content": user_text},
            ],
        )
        result = _parse_json(resp.choices[0].message.content)
        score = max(1, min(100, int(result["score"])))
        return {
            "score": score,
            "reasoning": result.get("reasoning", ""),
            "strengths": result.get("strengths", []),
            "gaps": result.get("gaps", []),
        }
    except Exception:
        return dict(_FAILURE)
