import os
import litellm
from careeros.core.models import Company, Goals, Job, Person, Profile
from careeros.skills.sanitize import wrap_untrusted

DEFAULT_LLM_MODEL = "claude-haiku-4-5-20251001"

_INSTRUCTIONS_BY_ROLE = {
    "ic": (
        "Write a short follow-up nudge (2-4 sentences) from one engineer to another, following up on an "
        "earlier outreach message that got no reply.\n"
        "Reference that you reached out before without restating or re-summarizing that message.\n"
        "Do not re-pitch yourself or repeat your earlier points — this is a brief bump, not a second message.\n"
        "Close with a low-pressure ask to connect briefly.\n"
    ),
    "em": (
        "Write a short follow-up nudge (2-4 sentences) from a job candidate to a hiring/engineering manager, "
        "following up on an earlier outreach message that got no reply.\n"
        "Reference that you reached out before without restating or re-summarizing that message.\n"
        "Do not re-pitch the candidate or repeat earlier points — this is a brief bump, not a second cover letter.\n"
        "Close with a request for a brief conversation.\n"
    ),
    "hiring_manager": (
        "Write a short follow-up nudge (2-4 sentences) from a job candidate to a hiring manager, following up "
        "on an earlier outreach message that got no reply.\n"
        "Reference that you reached out before without restating or re-summarizing that message.\n"
        "Do not re-pitch the candidate or repeat earlier points — this is a brief bump, not a second cover letter.\n"
        "Close with a request for a brief conversation.\n"
    ),
    "recruiter": (
        "Write a short, direct follow-up nudge (2-4 sentences) from a job candidate to a recruiter, following "
        "up on an earlier outreach message that got no reply.\n"
        "Reference that you reached out before without restating or re-summarizing that message.\n"
        "Do not re-pitch the candidate or repeat earlier points — this is a brief bump, not a second message.\n"
        "Close with availability for a call.\n"
    ),
}

_COMMON_SUFFIX = (
    "Keep it noticeably shorter than a first outreach message. Never repeat or quote the earlier message "
    "verbatim. Return ONLY the email body — no subject line, no markdown, no commentary.\n\n"
)


def _build_trusted_context(profile: Profile, goals: Goals) -> str:
    lines = []
    if profile.title:
        lines.append("Candidate title: " + profile.title)
    if profile.summary:
        lines.append("Candidate summary: " + profile.summary)
    if goals.short_term:
        lines.append("Candidate goals: " + "; ".join(goals.short_term))
    return "\n".join(lines)


def _build_untrusted_context(
    person: Person, job: Job, company: Company, prior_text: str, touch_number: int,
) -> str:
    lines = []
    if person.title:
        lines.append("Recipient: " + person.name + " (" + person.title + ")")
    else:
        lines.append("Recipient: " + person.name)
    lines.append("Company: " + company.name)
    if company.industry:
        lines.append("Industry: " + company.industry)
    lines.append("Job: " + job.title)
    lines.append("Follow-up touch number: " + str(touch_number))
    lines.append("Prior message sent to this recipient:\n" + prior_text)
    return "\n".join(lines)


def generate_follow_up_message(
    person: Person, job: Job, company: Company, profile: Profile, goals: Goals,
    prior_text: str, touch_number: int, model: str | None = None,
) -> str:
    effective_model = model or os.environ.get("CAREEROS_MODEL", DEFAULT_LLM_MODEL)
    instructions = _INSTRUCTIONS_BY_ROLE.get(person.role_category, _INSTRUCTIONS_BY_ROLE["ic"])
    trusted_context = _build_trusted_context(profile, goals)
    system_text = instructions + _COMMON_SUFFIX + trusted_context
    untrusted_context = _build_untrusted_context(person, job, company, prior_text, touch_number)
    user_text = wrap_untrusted(untrusted_context)
    try:
        resp = litellm.completion(
            model=effective_model,
            max_tokens=256,
            messages=[
                {"role": "system", "content": system_text},
                {"role": "user", "content": user_text},
            ],
        )
        content = resp.choices[0].message.content
        if not content:
            return ""
        return content.strip()
    except Exception:
        return ""
