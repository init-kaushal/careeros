import os
import litellm
from careeros.core.models import Company, Goals, Job, Person, Profile
from careeros.skills.sanitize import wrap_untrusted

DEFAULT_LLM_MODEL = "claude-haiku-4-5-20251001"

# LinkedIn caps a connection-request note at 300 characters. This is why the
# note is its own skill rather than a reuse of the email drafters: an
# email-shaped draft does not fit, and trimming one to length produces
# something worse than text written to the limit — a sentence that stops
# mid-word, transmitted to a human being.
NOTE_CHAR_LIMIT = 300

# Matches cover_letter.py and job_score.py. Job.save already truncates
# description to 4000 on write, so for any job loaded from the workspace this
# slice is a no-op; it binds only for a Job constructed in memory.
_JD_CAP = 4000

_INSTRUCTIONS_BY_ROLE = {
    "ic": (
        "Write a LinkedIn connection-request note from one engineer to another.\n"
        "Reference one concrete piece of shared technical ground — not a job pitch.\n"
        "Close with a brief, low-pressure reason to connect.\n"
    ),
    "em": (
        "Write a LinkedIn connection-request note from a job candidate to a hiring/engineering manager.\n"
        "Name the role and give one concrete reason the candidate fits their team.\n"
        "Close with a brief ask to connect.\n"
    ),
    "hiring_manager": (
        "Write a LinkedIn connection-request note from a job candidate to a hiring manager.\n"
        "Name the role and give one concrete reason the candidate fits their team.\n"
        "Close with a brief ask to connect.\n"
    ),
    "recruiter": (
        "Write a direct LinkedIn connection-request note from a job candidate to a recruiter.\n"
        "State clear interest in the specific role in one line.\n"
        "Close with a brief ask to connect.\n"
    ),
}

# The limit is interpolated from NOTE_CHAR_LIMIT rather than spelled out, so
# the number the model is told and the number it is checked against cannot
# drift apart. Telling it the note is rejected rather than trimmed is the
# truth (see generate_connection_note) and is the pressure that makes it write
# short instead of writing long and hoping.
_COMMON_SUFFIX = (
    "HARD LIMIT: at most " + str(NOTE_CHAR_LIMIT) + " characters including spaces and "
    "punctuation. A note over the limit is rejected outright, not trimmed, so write to "
    "the limit rather than writing long.\n"
    "That is two or three short sentences. No salutation line and no sign-off — there is "
    "no room for either.\n"
    "Return ONLY the note text — no markdown, no surrounding quotation marks, no commentary.\n\n"
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


def _build_untrusted_context(person: Person, job: Job, company: Company) -> str:
    """Everything here is scraped, or derived from something scraped.

    person.name/title come from the people-search scraper; job.title and
    job.description from a scraped posting; company.industry and company.notes
    are company_research.py's own LLM output summarized from scraped pages.
    That last pair is the same case follow_up_draft handles by putting
    prior_text on the untrusted side: text derived from scraped input inherits
    the taint, so it is data here and never instruction.
    """
    lines = []
    if person.title:
        lines.append("Recipient: " + person.name + " (" + person.title + ")")
    else:
        lines.append("Recipient: " + person.name)
    lines.append("Company: " + company.name)
    if company.industry:
        lines.append("Industry: " + company.industry)
    if company.notes:
        lines.append("Company research notes: " + company.notes)
    lines.append("Job: " + job.title)
    if job.description:
        lines.append("Job description:\n" + job.description[:_JD_CAP])
    return "\n".join(lines)


def generate_connection_note(
    person: Person, job: Job, company: Company, profile: Profile, goals: Goals,
    model: str | None = None,
) -> str:
    """Draft a LinkedIn connection-request note. Returns '' on any failure.

    Never raises, and never returns a note over NOTE_CHAR_LIMIT — the
    operations layer turns '' into DraftFailed, the same contract
    follow_up_draft.generate_follow_up_message has with operations/follow_up.
    """
    effective_model = model or os.environ.get("CAREEROS_MODEL", DEFAULT_LLM_MODEL)
    instructions = _INSTRUCTIONS_BY_ROLE.get(person.role_category, _INSTRUCTIONS_BY_ROLE["ic"])
    trusted_context = _build_trusted_context(profile, goals)
    system_text = instructions + _COMMON_SUFFIX + trusted_context
    untrusted_context = _build_untrusted_context(person, job, company)
    user_text = wrap_untrusted(untrusted_context)
    try:
        resp = litellm.completion(
            model=effective_model,
            # 300 characters is well under 128 tokens; the ceiling exists so a
            # model that ignores the limit is cut off cheaply rather than
            # billed for an essay we are about to reject anyway.
            max_tokens=128,
            messages=[
                {"role": "system", "content": system_text},
                {"role": "user", "content": user_text},
            ],
        )
        content = resp.choices[0].message.content
        if not content:
            return ""
        note = content.strip()
        # Both arms are failures rather than salvageable notes. Over the cap is
        # never truncated, per the module comment. Blank is refused because the
        # Send button would submit an empty request quite happily; the
        # connector refuses one too, so this is defence in depth.
        if not note or len(note) > NOTE_CHAR_LIMIT:
            return ""
        return note
    except Exception:
        return ""
