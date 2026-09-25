from __future__ import annotations

import urllib.parse
from dataclasses import dataclass

from pydantic import ValidationError

from careeros.browser.connect import LinkedInConnector
from careeros.browser.driver import BrowserProfileBusy
from careeros.browser.session import check_board_sessions
from careeros.core.models import (
    Company, ConnectionRequest, Goals, Job, Person, PolicyConfig, Profile,
)
from careeros.core.policy_engine import PolicyEngine
from careeros.operations._shared import digest_text as note_digest
from careeros.operations.approvals import open_approval
from careeros.operations.errors import (
    BoardSessionRequired, BrowserUnavailable, ConnectionAlreadySent, DraftFailed,
    EntityNotFound, PolicyBlocked,
)
from careeros.operations.outreach import make_message_id
from careeros.runtime.base import AgentRuntime
from careeros.skills.connection_note import NOTE_CHAR_LIMIT, generate_connection_note

ACTION = "send_connection_request"

# One module-level instance, exactly as apply.py holds its FILLERS: the
# connector is stateless, and a single well-known name is what a test patches.
CONNECTOR = LinkedInConnector()

_CONNECTIONS_PREFIX = "connections/"
_JSON_SUFFIX = ".json"

# The origin a root-relative href is resolved against, and the only path
# shape that names a person. `careeros research people` stores the raw href
# the people-search scraper captured, which on a LinkedIn search page can be
# "/in/jane" rather than an absolute URL.
_CANONICAL_ORIGIN = "https://www.linkedin.com"
_PROFILE_PREFIX = "/in/"


@dataclass(frozen=True)
class ConnectionProposal:
    approval_id: str
    request_id: str
    summary: str
    note_text: str
    recipient_name: str
    # The canonical form, not whatever was on the Person record: this is the
    # value that was persisted and bound into the approval, so a caller
    # displaying it for review shows exactly what execute will navigate to.
    linkedin_url: str


def canonical_linkedin_url(raw: str) -> str | None:
    """Canonical absolute https:// profile URL for `raw`, or None.

    Normalization lives here, at the moment the URL becomes load-bearing,
    rather than in `people update` or the people-search scraper — because
    both of those write values careeros cannot control the shape of, and
    neither is the last writer. `people update --linkedin-url` stores
    whatever string the user pasted, and a scheme-less paste
    ("www.linkedin.com/in/jane") is the common case; `careeros research
    people` stores the scraper's raw href, which can be root-relative
    ("/in/jane"). So a *researched* person can be as non-canonical as a
    hand-entered one, and normalizing in one writer would leave the other.

    `LinkedInConnector.can_handle` is deliberately strict — it parses the
    host rather than searching for it, because a wrong match there sends a
    message to the wrong human being — and it therefore answers False for
    every scheme-less string. That strictness stays: this function is what
    makes a non-canonical stored value usable, and it finishes by asking
    can_handle, so the URL persisted onto the record is by construction one
    the connector will navigate. The two cannot drift apart.

    What it refuses, and why each is a refusal rather than a rewrite:

    - Anything whose scheme is not http/https, including "javascript:" and
      "mailto:". There is nothing to navigate.
    - Userinfo ("https://evil@www.linkedin.com/in/jane") and an explicit
      port ("https://www.linkedin.com:8443/in/jane"). Neither appears in a
      genuine LinkedIn profile URL. Stripping them would silently redirect
      the browser somewhere the user did not paste, and honouring them would
      navigate a credential-embedding or off-port URL — so the value is
      handed back to the user instead.
    - Any path that is not /in/<slug>: a company page, a job posting, a
      people-search URL, or a bare "/in/", which names nobody.
    - Any host that is not linkedin.com or a subdomain of it, which is
      can_handle's own check and the reason for delegating to it.

    What it rewrites, and what it preserves: http becomes https; the host is
    lowercased (hosts are case-insensitive) but otherwise kept as given, so
    a country subdomain like uk.linkedin.com is not quietly moved to www;
    the slug's own case is untouched, since a vanity URL can carry
    uppercase. Query, fragment and any sub-path below the profile root are
    dropped — a tracking parameter identifies nothing, and the top card the
    connector reads only renders on the profile root, so
    "/in/jane/recent-activity/all/" would otherwise be a page with no
    Connect button on it.
    """
    candidate = raw.strip()
    if not candidate:
        return None
    try:
        parsed = urllib.parse.urlparse(candidate)
    except ValueError:
        # urlparse raises on a malformed authority (an unterminated IPv6
        # literal, say). Unparseable is not navigable.
        return None

    if not parsed.scheme:
        # Three scheme-less shapes reach here and they resolve differently,
        # so they are told apart rather than all given an "https://" prefix:
        # a protocol-relative href already carries its host, a root-relative
        # href carries none and resolves against LinkedIn's origin, and a
        # bare paste has the host sitting in the path.
        if parsed.netloc:
            candidate = "https:" + candidate
        elif candidate.startswith("/"):
            candidate = _CANONICAL_ORIGIN + candidate
        else:
            candidate = "https://" + candidate
        try:
            parsed = urllib.parse.urlparse(candidate)
        except ValueError:
            return None

    if parsed.scheme not in ("http", "https"):
        return None
    if "@" in parsed.netloc or ":" in parsed.netloc:
        return None

    segments = [segment for segment in parsed.path.split("/") if segment]
    if len(segments) < 2 or segments[0] != _PROFILE_PREFIX.strip("/"):
        return None

    normalized = "https://" + parsed.netloc.lower() + _PROFILE_PREFIX + segments[1]
    # The final authority on whether this URL is a LinkedIn profile is the
    # object that will navigate it. Asking it here — rather than restating
    # its host rule — is what guarantees a stored record execute_* can use.
    return normalized if CONNECTOR.can_handle(normalized) else None


def _require_linkedin_url(person: Person) -> str:
    """The person's LinkedIn URL in canonical form, or a named remedy.

    Both refusals name `careeros people update --linkedin-url`, which exists
    for exactly this: the field is otherwise only ever written by `careeros
    research people`, so a person the user added by hand had no path to one
    at all and every LinkedIn-facing flow stayed permanently closed to them.
    An error naming a flag that does not parse would be a dead end, so the
    spelling here is the one the command actually takes.

    EntityNotFound for both, rather than a second type for the unusable
    case: the spec names EntityNotFound for the absent URL, the remedy is
    identical, and every existing caller already catches it.
    """
    raw = person.linkedin_url
    if not raw or not raw.strip():
        raise EntityNotFound(
            "Person " + person.id + " has no LinkedIn URL. Run 'careeros people "
            "update " + person.id + " --linkedin-url <url>' and retry."
        )
    normalized = canonical_linkedin_url(raw)
    if normalized is None:
        raise EntityNotFound(
            "Person " + person.id + " has " + repr(raw) + " on file, which is not "
            "a LinkedIn profile URL. Run 'careeros people update " + person.id
            + " --linkedin-url https://www.linkedin.com/in/<slug>' and retry."
        )
    return normalized


def _sent_request_to(runtime: AgentRuntime, person_id: str) -> ConnectionRequest | None:
    """The first sent ConnectionRequest addressed to `person_id`, if any.

    Enumerates connections/ rather than loading this (job, person) pair's
    own record, because the record id is derived from *both* ids: a second
    job produces a second record, and a connection request is to a human
    being, not to a role — the recipient sees one notification either way.

    Keyed off sent_at, not send_state. A re-draft rewrites send_state, so it
    does not remember that a real send happened; sent_at is only ever written
    by one, which makes it the durable fact (the ConnectionRequest model says
    so in its own docstring).

    An unparseable record is skipped rather than raised on, matching
    list_by_state: one corrupt file must not make this answer "nobody has
    been contacted", which is the direction that sends a duplicate. An
    unexpected error is deliberately not swallowed, for the same reason
    list_by_state does not swallow one.
    """
    for path in runtime.storage.list(_CONNECTIONS_PREFIX):
        if not path.endswith(_JSON_SUFFIX):
            continue
        request_id = path[len(_CONNECTIONS_PREFIX):-len(_JSON_SUFFIX)]
        try:
            record = ConnectionRequest.load(runtime.storage, request_id)
        except (ValueError, FileNotFoundError, ValidationError):
            continue
        if record.person_id == person_id and record.sent_at:
            return record
    return None


def propose_connection_request(
    runtime: AgentRuntime,
    job_id: str,
    person_id: str,
    *,
    model: str | None = None,
    action_label: str,
) -> ConnectionProposal:
    """Draft a LinkedIn note and record a pending approval for sending it.

    Every refusal is checked, cheapest-first, before the LLM is called or
    anything is written, so a refusal leaves the workspace exactly as it
    found it: the URL must exist and must normalize, the person must not
    already have had a request sent to them, the job must pass policy, and
    the LinkedIn session must be authorized.

    Two of those orderings are load-bearing rather than tidy:

    The duplicate refusal comes before the policy and session checks because
    it is the strongest of the three — a policy block or a signed-out
    session can be fixed and retried, while a request already sitting in
    somebody's notifications cannot be unsent.

    The session check comes before drafting, which is 12b's lesson and the
    one §6.3 names: a user who is not signed in should not pay for an LLM
    call before being told to sign in. Because regeneration works by
    re-calling this function, the check runs again on every regeneration —
    accepted, since it also catches a session that expires during a long
    review, and nothing expensive precedes it.

    Calling this again is how regeneration works: the new call overwrites the
    note on the record and open_approval supersedes the prior open approval,
    so a stale approval id can never execute against a note that has since
    been replaced.
    """
    try:
        job = Job.load(runtime.storage, job_id)
        person = Person.load(runtime.storage, person_id)
        company = Company.load(runtime.storage, person.company_id)
    except (FileNotFoundError, ValueError) as exc:
        raise EntityNotFound("Job, person, or company not found.") from exc

    # Normalized here, before anything else reads it: the duplicate check,
    # the record and the payload must all hold the same canonical value, and
    # execute navigates exactly what was approved.
    linkedin_url = _require_linkedin_url(person)

    already_sent = _sent_request_to(runtime, person_id)
    if already_sent is not None:
        raise ConnectionAlreadySent(
            person_id, person.name, already_sent.id, already_sent.sent_at or "",
        )

    policy_result = PolicyEngine(PolicyConfig.load(runtime.storage)).check_job(job)
    if policy_result.blocked:
        rule = policy_result.rule or "unknown"
        # Logged before the raise, not after: the caller only ever sees the
        # exception, so this event is the whole durable record that a policy
        # refusal happened at all.
        runtime.record_activity(runtime.new_event(
            "policy_blocked", action_label,
            "Blocked by policy (" + rule + "): " + job.company + " — " + job.title,
            status="failed", entity_type="job", entity_id=job.id,
        ))
        raise PolicyBlocked(rule)

    try:
        authorized = check_board_sessions(["linkedin"]).get("linkedin")
    except BrowserProfileBusy as exc:
        # A locked profile is a whole-run condition rather than a per-person
        # one, and the flag is how the caller tells the two apart. Converted
        # here so no raw browser exception crosses the operations boundary.
        raise BrowserUnavailable(str(exc), profile_busy=True) from exc
    if not authorized:
        raise BoardSessionRequired("linkedin")

    profile = Profile.load_or_empty(runtime.storage)
    goals = Goals.load_or_empty(runtime.storage)

    note_text = generate_connection_note(
        person, job, company, profile, goals, model=model,
    )
    # generate_connection_note returns '' and never raises — the convention
    # all four drafting skills share — so §6.2's requirement that an
    # over-cap or blank note *is* a DraftFailed exists only if this layer
    # performs the conversion. The cap is re-checked rather than trusted to
    # the skill for the same reason the connector re-checks it before
    # typing: a note over the limit is silently clipped by the textarea's
    # maxlength, delivering a sentence that stops mid-word to a person. A
    # whitespace-only note is refused too, though the skill already strips:
    # it would persist as a note, digest cleanly, and then be refused at the
    # point of action, which is a worse place to discover it.
    if not note_text.strip() or len(note_text) > NOTE_CHAR_LIMIT:
        raise DraftFailed("Connection note generation failed.")

    request_id = make_message_id(job_id, person_id)
    # Constructed fresh rather than copied over any existing record: the only
    # field worth carrying forward is sent_at, and the duplicate refusal
    # above has already established that no sent request exists for this
    # person — so for this record sent_at is provably None.
    ConnectionRequest(
        id=request_id, job_id=job_id, person_id=person_id,
        linkedin_url=linkedin_url, note_text=note_text, send_state="drafted",
        sent_at=None,
    ).save(runtime.storage)
    runtime.record_activity(runtime.new_event(
        "connection_note_drafted", action_label,
        "Drafted LinkedIn connection note to " + person.name + " re: "
        + job.company + " — " + job.title,
        entity_type="connection_request", entity_id=request_id,
    ))

    summary = (
        "Send a LinkedIn connection request to " + person.name + " re: "
        + job.company + " — " + job.title + "?"
    )

    # Exactly the four keys §6.3 names. linkedin_url is bound here rather
    # than re-read off the Person at execute time for the reason that
    # section calls out: if the Person's URL changes between approval and
    # execution, re-reading it would send the request to a different human
    # being. Bound, a changed URL is an ArtifactChanged refusal instead.
    approval = open_approval(
        runtime, ACTION, summary,
        {
            "person_id": person_id,
            "job_id": job_id,
            "linkedin_url": linkedin_url,
            "note_sha256": note_digest(note_text),
        },
        entity_type="connection_request", entity_id=request_id,
        action_label=action_label,
    )

    return ConnectionProposal(
        approval_id=approval.id, request_id=request_id, summary=summary,
        note_text=note_text, recipient_name=person.name,
        linkedin_url=linkedin_url,
    )
