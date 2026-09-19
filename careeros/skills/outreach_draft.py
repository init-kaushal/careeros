import os
import litellm
from careeros.core.models import Company, Goals, Job, Person, Profile

DEFAULT_LLM_MODEL = "claude-haiku-4-5-20251001"

_INSTRUCTIONS_BY_ROLE = {
    "ic": (
        "Write a concise, peer-to-peer outreach email (2-3 short paragraphs) from one engineer to another.\n"
        "Reference shared technical interest, not a job pitch. Close with a low-pressure ask to connect briefly.\n"
    ),
    "em": (
        "Write a concise outreach email (2-3 short paragraphs) from a job candidate to a hiring/engineering manager.\n"
        "Explain briefly why the candidate is a strong fit for their team, referencing the role and company.\n"
        "Close with a request for a brief conversation.\n"
    ),
    "hiring_manager": (
        "Write a concise outreach email (2-3 short paragraphs) from a job candidate to a hiring manager.\n"
        "Explain briefly why the candidate is a strong fit for their team, referencing the role and company.\n"
        "Close with a request for a brief conversation.\n"
    ),
    "recruiter": (
        "Write a concise, direct outreach email (2-3 short paragraphs) from a job candidate to a recruiter.\n"
        "State clear interest in the specific role and briefly highlight fit. Close with availability for a call.\n"
    ),
}

_COMMON_SUFFIX = (
    "Return ONLY the email body — no subject line, no markdown, no commentary.\n\n"
)


def _build_context_text(person: Person, job: Job, company: Company, profile: Profile, goals: Goals) -> str:
    lines = []
    if person.title:
        lines.append("Recipient: " + person.name + " (" + person.title + ")")
    else:
        lines.append("Recipient: " + person.name)
    lines.append("Company: " + company.name)
    if company.industry:
        lines.append("Industry: " + company.industry)
    lines.append("Job: " + job.title)
    if profile.title:
        lines.append("Candidate title: " + profile.title)
    if profile.summary:
        lines.append("Candidate summary: " + profile.summary)
    if goals.short_term:
        lines.append("Candidate goals: " + "; ".join(goals.short_term))
    return "\n".join(lines)


def generate_outreach_message(
    person: Person, job: Job, company: Company, profile: Profile, goals: Goals, model: str | None = None,
) -> str:
    effective_model = model or os.environ.get("CAREEROS_MODEL", DEFAULT_LLM_MODEL)
    instructions = _INSTRUCTIONS_BY_ROLE.get(person.role_category, _INSTRUCTIONS_BY_ROLE["ic"])
    context_text = _build_context_text(person, job, company, profile, goals)
    prompt = instructions + _COMMON_SUFFIX + context_text
    try:
        resp = litellm.completion(
            model=effective_model,
            max_tokens=512,
            messages=[{"role": "user", "content": prompt}],
        )
        content = resp.choices[0].message.content
        if not content:
            return ""
        return content.strip()
    except Exception:
        return ""
