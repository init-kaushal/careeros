from __future__ import annotations

from datetime import datetime, timezone

import typer
from pydantic import ValidationError
from rich import print as rprint
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt

from careeros.cli._display import verbatim
from careeros.core.models import Approval, CadencePolicy, OutreachMessage, Person
from careeros.operations.approval_queue import queue_only
from careeros.operations.approvals import (
    APPROVED, list_by_state, list_pending, payload_value, resolve_approval,
    supersede_approval,
)
from careeros.operations.errors import (
    CadenceExhausted, DraftFailed, EntityNotFound, MalformedTouchTimestamp,
    MissingRecipient, NotDueForFollowUp, OperationError, PolicyBlocked,
    RelationshipClosed,
)
from careeros.operations.follow_up import ACTION as FOLLOW_UP_ACTION
from careeros.operations.follow_up import (
    CadenceStatus, check_follow_up_due, decline_follow_up, execute_follow_up,
    propose_follow_up,
)
from careeros.operations.outreach import (
    ACTION, decline_outreach_send, execute_outreach_send, make_message_id,
    propose_outreach_send,
)
from careeros.runtime.automation import AutomationRuntime
from careeros.runtime.base import ActionProposal, ApprovalResult
from careeros.runtime.factory import (
    WorkspaceNotConfigured, open_automation_runtime, open_local_runtime, resolve_storage,
)
from careeros.runtime.local import LocalRuntime

outreach_app = typer.Typer(help="Draft, approve, and send outreach messages.")
people_app = typer.Typer(help="Manage researched people.")
console = Console()

MAX_REGENERATIONS = 5

# action_label stamped on every operations call this command makes, so the
# activity log and any approval payload can be traced back to this specific
# scheduled entrypoint rather than to the interactive `outreach send` flow.
FOLLOW_UP_CMD_ACTION_LABEL = "outreach-follow-up"

# And a distinct one for the interactive drainer below. The two commands
# work the same queue from opposite ends, so folding them under one label
# would make the audit log unable to answer the first question anyone asks
# of it: was this follow-up drafted by the cron job and sent by a person, or
# did one entrypoint do both? A re-propose from the review loop is stamped
# with this too, because that draft was written at a human's request.
REVIEW_CMD_ACTION_LABEL = "outreach-review"

# And the off switch. Distinct from the two above for the same reason they
# are distinct from each other: an approval this command declines was not
# declined by a reviewer who read the draft and judged it, and the log has to
# be able to tell those two apart.
CLOSE_CMD_ACTION_LABEL = "outreach-close"

# Three drafting failures in a row means the LLM provider is down or the key
# is dead, not that three relationships are individually unlucky — and every
# DraftFailed has already been paid for, because
# generate_follow_up_message swallows every provider exception into "".
# Stopping the whole run follows the precedent discover-and-apply sets for a
# profile_busy BrowserUnavailable, for exactly the same reason: continuing
# buys nothing but identical paid failures.
MAX_CONSECUTIVE_DRAFT_FAILURES = 3


@people_app.callback()
def _people_app_callback() -> None:
    """Manage researched people."""


def _open_runtime(workspace_path: str | None) -> LocalRuntime:
    try:
        return open_local_runtime(resolve_storage(workspace_path))
    except (WorkspaceNotConfigured, FileNotFoundError):
        rprint("[red]No workspace configured. Run 'careeros onboard' first.[/red]")
        raise typer.Exit(1)


def _open_automation_runtime(workspace_path: str | None) -> AutomationRuntime:
    try:
        # AutomationRuntime's own default is auto-approve — the right default
        # for discover-and-apply, which is the one command in this project
        # where a wrong decision only costs an application. It is the wrong
        # default here: this command proposes and stops, and must never
        # accidentally approve anything if a future code path along this
        # command's route ever calls request_approval. Passing queue_only
        # makes that path deny-by-default instead of silently saying yes,
        # leaving the class-wide default untouched for everyone else.
        return open_automation_runtime(
            resolve_storage(workspace_path), approval_callback=queue_only,
        )
    except (WorkspaceNotConfigured, FileNotFoundError):
        rprint("[red]No workspace configured. Run 'careeros onboard' first.[/red]")
        raise typer.Exit(1)


def _due_status(
    message: OutreachMessage, policy: CadencePolicy, now: datetime,
) -> CadenceStatus | None:
    """This command's non-raising view of check_follow_up_due.

    The shared checker in the operations layer raises, because each refusal
    carries structured detail its other callers need. An enumeration filter
    needs none of it — only "consider this one or not" — so it catches the
    three ordinary refusals here and answers None. A CadenceStatus rather
    than a bool comes back on the due path because the caller needs the
    days_since and touch count the check already computed, and handing back
    the value it computed them from is what keeps one rule in one place —
    a bool would force this command to re-derive both from the timestamp
    itself, which is the duplicate logic this consolidation removes.

    MalformedTouchTimestamp is deliberately NOT caught here: an unusable
    timestamp is a workspace defect the operator has to see, not a
    relationship that merely isn't due yet.
    """
    try:
        return check_follow_up_due(message, policy, now)
    except (RelationshipClosed, CadenceExhausted, NotDueForFollowUp):
        return None


def _due_relationships(
    runtime: AutomationRuntime, policy: CadencePolicy, now: datetime,
) -> list[tuple[OutreachMessage, CadenceStatus]]:
    """Every relationship due at `now`, paired with what made it due.

    Nothing this loop does may escape it. A single unusable last-touch
    timestamp used to take the entire run down — the parse happened outside
    the try, so a hand-edited "not-a-date" (ValueError) or a legal but
    offset-naive "2026-01-01T10:00:00" (TypeError on the subtraction) exited
    1 with empty output and nothing proposed for any healthy relationship.
    The checker now reports that as a structured refusal, and it costs only
    the one record.
    """
    due: list[tuple[OutreachMessage, CadenceStatus]] = []
    for path in sorted(runtime.storage.list("outreach/")):
        if not path.endswith(".json"):
            continue
        path_id = path[len("outreach/"):-len(".json")]
        try:
            message = OutreachMessage.load(runtime.storage, path_id)
        except (FileNotFoundError, ValueError) as exc:
            # Warned about for the same reason a bad timestamp is: a record
            # under outreach/ that will not load at all is a worse workspace
            # defect than one that loads and is merely not due, and only the
            # operator can fix it. Dropping it silently meant a relationship
            # could stop being followed up with no trace anywhere.
            # pydantic.ValidationError subclasses ValueError, so this covers
            # unparseable JSON and valid-JSON-wrong-schema alike.
            rprint(
                "[yellow]Skipping " + path + ": could not be loaded ("
                + type(exc).__name__ + ").[/yellow]"
            )
            continue
        # storage.list is a recursive rglob, but this slice assumes the flat
        # outreach/<id>.json layout. A nested copy — a backup, an archive/
        # subdir — yields path_id "archive/<id>" while the record it loads
        # still carries id "<id>", so trusting the slice enumerated one
        # relationship twice: two paid LLM calls, an immediately-superseded
        # approval, and two of the per-run cap spent on one person. Keying
        # off the loaded id, and requiring it to round-trip to the path it
        # was found at, drops the copy; the canonical file is enumerated in
        # its own right, so the relationship itself is never lost. No
        # separate seen-set is needed on top: two distinct paths cannot
        # produce the same path_id, so requiring the round-trip already
        # makes one id reachable from exactly one file.
        if message.id != path_id:
            continue
        try:
            status = _due_status(message, policy, now)
        except MalformedTouchTimestamp as exc:
            # Visible, because careeros never writes such a value itself:
            # it came from a hand edit or an agent writing through the
            # documented integration contract, and only the operator can
            # fix it.
            rprint("[yellow]Skipping " + message.id + ": " + str(exc) + "[/yellow]")
            continue
        if status is not None:
            due.append((message, status))
    return due


def _log_propose_error(
    runtime: AutomationRuntime, message_id: str, exc: Exception,
) -> None:
    """The only durable trace that a relationship was tried and failed.

    propose_follow_up logs nothing of its own for these refusals — unlike
    PolicyBlocked, which it logs itself — so without this there is nothing
    in the activity log to distinguish "considered and failed" from "was not
    due". str(exc) goes in alongside the type name because the type alone
    discards the entire detail: which entity was missing, how many days
    short, what the unusable timestamp actually said.
    """
    runtime.record_activity(runtime.new_event(
        "follow_up_propose_error", FOLLOW_UP_CMD_ACTION_LABEL,
        "Error proposing a follow-up for " + message_id + ": "
        + type(exc).__name__ + ": " + str(exc),
        status="failed", entity_type="outreach_message", entity_id=message_id,
    ))


def _entity_ids_with_pending_follow_up(runtime: AutomationRuntime) -> set[str]:
    return {
        approval.entity_id for approval in list_pending(runtime.storage)
        if approval.action == FOLLOW_UP_ACTION
    }


@outreach_app.command()
def send(
    job: str = typer.Option(..., "--job", help="Job ID"),
    person: str = typer.Option(..., "--person", help="Person ID"),
    workspace: str = typer.Option(None, "--workspace", help="Workspace path"),
    model: str = typer.Option(None, "--model", help="Override LLM model"),
) -> None:
    runtime = _open_runtime(workspace)

    try:
        proposal = propose_outreach_send(runtime, job, person, model=model, action_label="outreach")

        regenerations = 0
        while True:
            console.print(Panel(
                verbatim(proposal.draft_text),
                title=verbatim("Outreach to " + proposal.recipient_name),
            ))
            if regenerations >= MAX_REGENERATIONS:
                choice = Prompt.ask("[A]ccept / [Q]uit", choices=["a", "q"], default="a")
            else:
                choice = Prompt.ask("[A]ccept / [R]egenerate / [Q]uit", choices=["a", "r", "q"], default="a")
            if choice == "q":
                resolve_approval(
                    runtime, proposal.approval_id,
                    ApprovalResult(approved=False, reason="aborted at review"),
                    action_label="outreach",
                )
                decline_outreach_send(runtime, proposal.approval_id, action_label="outreach")
                rprint("Aborted.")
                raise typer.Exit(0)
            if choice == "r":
                regenerations += 1
                proposal = propose_outreach_send(runtime, job, person, model=model, action_label="outreach")
                continue
            break

        if proposal.already_sent_at:
            rprint(
                "[yellow]An outreach email to " + proposal.recipient_name
                + " was already sent on " + proposal.already_sent_at
                + ". This will send another.[/yellow]"
            )

        result = runtime.request_approval(ActionProposal(
            action=ACTION,
            summary=proposal.summary,
            entity_type="outreach_message", entity_id=proposal.message_id,
        ))
        resolve_approval(runtime, proposal.approval_id, result, action_label="outreach")

        if not result.approved:
            decline_outreach_send(runtime, proposal.approval_id, action_label="outreach")
            rprint("Aborted.")
            raise typer.Exit(0)

        outcome = execute_outreach_send(runtime, proposal.approval_id, action_label="outreach")
    except MissingRecipient as exc:
        rprint("[red]" + str(exc) + "[/red]")
        raise typer.Exit(1)
    except OperationError as exc:
        rprint("[red]" + str(exc) + "[/red]")
        raise typer.Exit(1)

    rprint("[green]Sent to " + outcome.recipient_name + "[/green]")


@outreach_app.command(name="mark-referral-requested")
def mark_referral_requested(
    job: str = typer.Option(..., "--job", help="Job ID"),
    person: str = typer.Option(..., "--person", help="Person ID"),
    workspace: str = typer.Option(None, "--workspace", help="Workspace path"),
) -> None:
    runtime = _open_runtime(workspace)

    message_id = make_message_id(job, person)
    try:
        message = OutreachMessage.load(runtime.storage, message_id)
    except (FileNotFoundError, ValueError):
        rprint("[red]No outreach message found for this job/person pair.[/red]")
        raise typer.Exit(1)

    message = message.model_copy(update={"referral_state": "referral_requested"})
    message.save(runtime.storage)
    runtime.record_activity(runtime.new_event(
        "referral_requested", "outreach",
        "Referral requested for job " + job,
        entity_type="outreach_message", entity_id=message_id,
    ))
    rprint("[green]Referral state updated.[/green]")


def _decline_queued_follow_ups(
    runtime: LocalRuntime, message_id: str, reason: str,
) -> int:
    """Take this relationship's queued follow-up drafts out of the queue.

    `outreach review` drains `list_pending`, so a pending follow-up left
    behind by a close would still be offered to the reviewer — and accepting
    it would email someone the user has just declared themselves finished
    with. Declining it is the fix, and `resolve_approval` is the whole of it:
    it is the only legal exit from `pending`, and `execute_follow_up`
    requires `approved`, so a `declined` record can never send.

    `decline_follow_up` is deliberately *not* called afterwards, even though
    it is what `review` pairs with a decline. Its purpose is to advance
    `last_touched_at` so the next scheduled run does not re-propose what was
    just declined, and that is moot here: `check_follow_up_due` raises
    RelationshipClosed before it ever reads the touch timestamp, so this
    relationship is already out of the cadence permanently. What it would
    cost is real, though — it writes `send_state="declined"` and a fresh
    `last_touched_at` onto the very record this command is closing,
    claiming a touch instant that never happened and overwriting the
    outcome of the last message that genuinely was sent, and it would log
    `follow_up_send_declined`, which says the user judged that draft. They
    did not; they ended the relationship, and `cadence_closed` is the event
    that says so.

    Scoped to this message's `send_follow_up` approvals. A pending approval
    for another relationship, or for another action against this one, belongs
    to another flow and must come out of this command untouched.
    """
    queued = [
        approval for approval in list_pending(runtime.storage)
        if approval.action == FOLLOW_UP_ACTION and approval.entity_id == message_id
    ]
    for approval in queued:
        resolve_approval(
            runtime, approval.id,
            # Recorded on the approval itself, so why that draft was thrown
            # away is recoverable from the record and not only by correlating
            # timestamps in the activity log.
            ApprovalResult(approved=False, reason="cadence closed: " + reason),
            action_label=CLOSE_CMD_ACTION_LABEL,
        )
    return len(queued)


def _supersede_stranded_follow_ups(
    runtime: LocalRuntime, message_id: str, reason: str,
) -> int:
    """Invalidate any approved-but-unsent follow-up this close leaves behind.

    A send refused *before* it was attempted — no address on file, or the
    stored draft edited out from under the approval — leaves the approval
    `approved` on purpose, so the refusal stays retryable. Normally the next
    scheduled run supersedes that record by proposing a replacement, which is
    what `outreach review`'s stranded-approval warning tells the user. Closing
    the relationship means no replacement will ever be proposed, so left alone
    the record would sit `approved`, with a digest still matching the stored
    draft, forever.

    `execute_follow_up` now refuses a closed relationship at the point of
    action, so such a record cannot send whatever anyone does with the id.
    This is hygiene on top of that guarantee, not the guarantee: an approval
    that can never be acted on should not still read `approved` in the audit
    trail, and `outreach review` reports exactly this set as stranded work
    the user might chase.

    `superseded` is the existing transition for precisely this
    approved-and-unexecuted case — `open_approval` writes it, for the same
    records, for the same reason — so this reuses it rather than widening the
    state machine with a `revoked` state whose only gain would be a nicer
    label. `mark_failed` would be the wrong terminal state: it means "the send
    was attempted and raised", so it would file a failure that never happened.
    The cause is carried on the activity event, which is where the real
    explanation belongs either way.

    Scoped to this message's `send_follow_up` approvals, like the decline
    above: an approved approval from another flow, or for another
    relationship, is not this command's business.
    """
    stranded = [
        approval for approval in list_by_state(runtime.storage, APPROVED)
        if approval.action == FOLLOW_UP_ACTION and approval.entity_id == message_id
    ]
    for approval in stranded:
        supersede_approval(
            runtime, approval, action_label=CLOSE_CMD_ACTION_LABEL,
            cause="cadence closed: " + reason,
        )
    return len(stranded)


@outreach_app.command()
def close(
    job: str = typer.Option(..., "--job", help="Job ID"),
    person: str = typer.Option(..., "--person", help="Person ID"),
    reason: str = typer.Option(
        ..., "--reason", help="Why the cadence is ending; recorded on the relationship",
    ),
    workspace: str = typer.Option(None, "--workspace", help="Workspace path"),
) -> None:
    """End the follow-up cadence for one relationship, recording why.

    The explicit off switch the cadence needs: a decline defers by one
    period rather than stopping, so without this a relationship the user has
    finished with is re-proposed every `days_between_touches` forever.
    """
    # Validated before the workspace is even opened. typer's `...` proves the
    # flag was supplied, not that it says anything, so `--reason ""` would
    # otherwise record a close with no reason — which is precisely the state
    # this phase exists to prevent, and worse than refusing, because the
    # record would then look complete.
    recorded_reason = reason.strip()
    if not recorded_reason:
        rprint("[red]--reason must say why the cadence is ending.[/red]")
        raise typer.Exit(1)

    runtime = _open_runtime(workspace)

    message_id = make_message_id(job, person)
    try:
        message = OutreachMessage.load(runtime.storage, message_id)
    except (FileNotFoundError, ValueError):
        # The same pair mark_referral_requested catches: FileNotFoundError for
        # an absent record, ValueError for an id storage rejects as unsafe —
        # and pydantic.ValidationError subclasses ValueError, so a corrupt
        # record lands here too rather than as a traceback.
        rprint("[red]No outreach message found for this job/person pair.[/red]")
        raise typer.Exit(1)

    if message.referral_state == "closed" or message.closed_reason:
        # Refused rather than treated as idempotent, and the two are not
        # equivalent here. This command's product is not a state — it is the
        # recorded reason, which is what the user will not remember in three
        # months. A second close would either overwrite the first reason
        # (destroying the record) or no-op silently while the user believes
        # their new reason was filed. Naming the recorded reason instead
        # answers the question they were probably asking.
        rprint("[red]This relationship is already closed. Recorded reason:[/red]")
        # verbatim because the stored reason is user-authored text: a
        # bracketed span would be deleted from the display, and a
        # closing-tag-shaped one would raise MarkupError.
        console.print(verbatim(message.closed_reason or "(none recorded)"))
        raise typer.Exit(1)

    # Approvals first, record second. If this run dies between the two, a
    # resolved draft against a still-open relationship is the benign failure —
    # the next scheduled run finds it still due and re-drafts. The reverse
    # order would leave a closed relationship with a live pending draft that
    # `review` would offer and sending would mail.
    declined = _decline_queued_follow_ups(runtime, message_id, recorded_reason)
    superseded = _supersede_stranded_follow_ups(runtime, message_id, recorded_reason)

    # Read before the copy overwrites it: it goes into the log line below.
    prior_state = message.referral_state

    # Only these two fields change; model_copy carries the rest of the record
    # — sent_at, last_touched_at, touch_count — forward untouched. Nothing
    # here rewrites history: what was sent stays sent.
    message.model_copy(update={
        "referral_state": "closed", "closed_reason": recorded_reason,
    }).save(runtime.storage)
    runtime.record_activity(runtime.new_event(
        "cadence_closed", CLOSE_CMD_ACTION_LABEL,
        # The prior referral_state is named because setting "closed"
        # overwrites it, and the append-only log is then the only place a
        # confirmed referral that was later closed out is still visible.
        "Cadence closed for job " + job + " (was " + prior_state + "): "
        + recorded_reason,
        entity_type="outreach_message", entity_id=message_id,
        reason=recorded_reason,
    ))

    rprint("[green]Cadence closed. No further follow-ups will be proposed.[/green]")
    if declined:
        rprint(
            "Declined " + str(declined) + " queued follow-up draft(s) that were "
            "awaiting review."
        )
    if superseded:
        rprint(
            "Superseded " + str(superseded) + " follow-up draft(s) that were "
            "already approved but never sent."
        )


@outreach_app.command(name="follow-up")
def follow_up_cmd(
    workspace: str = typer.Option(None, "--workspace", help="Workspace path"),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="List relationships due for a follow-up without proposing anything.",
    ),
) -> None:
    """Scheduled proposer: drafts a follow-up for every due relationship and
    stops. It sends nothing — every draft is left as a pending Approval for
    a human to review with 'careeros outreach review'.
    """
    runtime = _open_automation_runtime(workspace)

    try:
        policy = CadencePolicy.load(runtime.storage)
    except FileNotFoundError:
        rprint(
            "[red]config/cadence_policy.json not found — create one before "
            "running outreach follow-up.[/red]"
        )
        raise typer.Exit(1)
    except ValidationError as exc:
        # A missing policy got a friendly message and an invalid one got a
        # raw pydantic traceback, though both land on the same operator with
        # the same remedy — open the file and fix it.
        rprint(
            "[red]config/cadence_policy.json is not a valid cadence policy — "
            "fix it before running outreach follow-up:[/red]"
        )
        rprint(str(exc))
        raise typer.Exit(1)

    # One instant for the whole run. Every due-ness decision below — the
    # enumeration filter and the check inside each propose_follow_up — is
    # made against this same value, so a run that straddles midnight cannot
    # classify one relationship two ways.
    now = datetime.now(timezone.utc)

    due = _due_relationships(runtime, policy, now)

    # Skip any relationship that already has a pending follow-up approval.
    # Nothing sends until a human reviews it, so an unreviewed relationship's
    # last_touched_at never advances and it stays due forever — without this
    # skip, a daily cron would re-draft it (and pay for another LLM call)
    # every single day, and the user would eventually review a draft written
    # days after the one they were first offered. open_approval's supersede
    # rule keeps the *approval* count at one but does nothing about the
    # repeated drafting, so the fix has to live here, before propose_follow_up
    # is ever called.
    #
    # Counted separately rather than filtered out of `due`, because these
    # relationships really are past due: folding them away made the summary
    # report "Due: 0" for three genuinely past-due relationships, and made
    # --dry-run claim none were due at all.
    already_pending = _entity_ids_with_pending_follow_up(runtime)
    actionable = [(m, s) for m, s in due if m.id not in already_pending]
    awaiting_review = len(due) - len(actionable)

    if dry_run:
        for message, status in actionable:
            rprint(
                message.id + ": touch_count=" + str(status.effective_touch_count)
                + ", days_since_last_touch=" + str(status.days_since)
            )
        # The cap applies to the real run, so a preview that omits it
        # overstates what the next run will actually do. Reported here as
        # well as in the run summary, for the same reason: this output is
        # the operator's only view of the cadence.
        over_cap = len(actionable) - policy.max_follow_ups_per_run
        if over_cap > 0:
            rprint(
                "Of those, " + str(policy.max_follow_ups_per_run)
                + " would be drafted this run; " + str(over_cap)
                + " held back by max_follow_ups_per_run."
            )
        if not due:
            rprint("No relationships are due for a follow-up.")
        elif awaiting_review:
            # Worded as a count of its own so it is true whether or not
            # anything was listed above it. When everything due is awaiting
            # review, the old code printed "No relationships are due for a
            # follow-up." instead, which was simply false.
            rprint(
                "Due but already awaiting review, so not re-drafted: "
                + str(awaiting_review)
            )
        return

    proposed_count = 0
    blocked_count = 0
    error_count = 0
    deferred_count = 0
    # Paid LLM attempts, which is what the cap has to bound. A DraftFailed
    # has already reached the provider, because generate_follow_up_message
    # swallows every litellm exception into "" — so counting successes
    # instead turned a cap of 1 into one paid, discarded call for every due
    # relationship. PolicyBlocked is refused before drafting and spends
    # nothing, so it must not spend a slot either.
    drafting_attempts = 0
    consecutive_draft_failures = 0
    unexamined_count = 0
    aborted = False
    for index, (message, _status) in enumerate(actionable):
        if drafting_attempts >= policy.max_follow_ups_per_run:
            deferred_count += 1
            continue
        try:
            propose_follow_up(
                runtime, message.job_id, message.person_id,
                action_label=FOLLOW_UP_CMD_ACTION_LABEL, now=now,
            )
        except PolicyBlocked:
            # Already logged (policy_blocked) inside propose_follow_up, and
            # refused before any LLM call, so the budget is untouched.
            blocked_count += 1
            continue
        except DraftFailed as exc:
            drafting_attempts += 1
            consecutive_draft_failures += 1
            error_count += 1
            _log_propose_error(runtime, message.id, exc)
            if consecutive_draft_failures >= MAX_CONSECUTIVE_DRAFT_FAILURES:
                unexamined_count = len(actionable) - index - 1
                aborted = True
                runtime.record_activity(runtime.new_event(
                    "follow_up_run_aborted", FOLLOW_UP_CMD_ACTION_LABEL,
                    "Aborted the run after "
                    + str(MAX_CONSECUTIVE_DRAFT_FAILURES)
                    + " consecutive drafting failures; "
                    + str(unexamined_count) + " due relationship(s) not examined.",
                    status="failed",
                ))
                rprint(
                    "[red]Aborting: " + str(MAX_CONSECUTIVE_DRAFT_FAILURES)
                    + " follow-up drafts failed in a row, so the LLM provider is "
                    "down rather than these relationships being unlucky. Every "
                    "further attempt would be paid for and discarded.[/red]"
                )
                break
            continue
        except OperationError as exc:
            error_count += 1
            _log_propose_error(runtime, message.id, exc)
            continue
        drafting_attempts += 1
        consecutive_draft_failures = 0
        proposed_count += 1

    # Distinct outcomes get distinct counters. The old single line folded
    # awaiting-review into "not due" and left cap-deferred relationships
    # unaccounted for entirely, so "Due: 7, Proposed: 2" said nothing about
    # the other five. This is the operator's whole view of the cadence in a
    # cron mail, so no number in it may be false.
    rprint(
        "Due: " + str(len(due)) + " — " + str(len(actionable)) + " actionable, "
        + str(awaiting_review) + " awaiting review."
    )
    rprint(
        "Proposed: " + str(proposed_count)
        + ", Deferred by cap: " + str(deferred_count)
        + ", Blocked: " + str(blocked_count)
        + ", Errors: " + str(error_count)
    )
    if aborted:
        rprint("Not examined (run aborted): " + str(unexamined_count))
        # Nonzero so a cron run that achieved nothing is not silently
        # indistinguishable from one with nothing to do.
        raise typer.Exit(1)


def _review_recipient_name(runtime: LocalRuntime, person_id: str) -> str:
    """The name to show a reviewer, or the raw id if the record is gone.

    Deliberately tolerant, for the same reason decline_follow_up is: a
    missing people/<id>.json must not stop the user reading a draft and
    deciding about it. The draft itself lives on the OutreachMessage, so
    everything needed for the decision is still in hand — only the label is
    degraded.
    """
    try:
        return Person.load(runtime.storage, person_id).name
    except (FileNotFoundError, ValueError):
        return person_id


def _review_message(runtime: LocalRuntime, message_id: str) -> OutreachMessage:
    """The record holding the exact bytes execute_follow_up will transmit.

    Where the displayed text comes from, and the one real difference from
    `outreach send`: there is no fresh proposal in hand here, because a
    different process — the scheduled proposer — wrote this approval.
    execute_follow_up reads draft_text back off this same record and checks
    it against the draft_sha256 taken at propose time, so this record's
    draft_text is what will actually go out rather than a reconstruction of
    it. Displaying it byte-for-byte is a separate obligation, discharged by
    _verbatim at the point of printing: this docstring used to claim the
    display was faithful while Rich markup was silently deleting bracketed
    spans from it.

    Converted to EntityNotFound so the caller's per-item handler catches
    this the same way it catches every other single-item failure.
    """
    try:
        return OutreachMessage.load(runtime.storage, message_id)
    except (FileNotFoundError, ValueError) as exc:
        raise EntityNotFound(
            "Outreach message " + message_id + " not found."
        ) from exc


def _days_since_last_touch(message: OutreachMessage) -> int | None:
    """Days since the last touch, or None when the timestamp is unusable.

    On screen because it is the number the decision actually turns on — a
    third nudge after eleven days is a different proposition from one after
    three — and it is the only thing the reviewer needs that is recoverable
    from neither the draft text nor the approval summary (which already
    carries the touch number). FollowUpProposal exposes it for exactly this
    reason; the initial display has no proposal in hand, so it comes off the
    record instead.

    None rather than a raise: this is a display detail, so a hand-edited
    timestamp costs the reviewer one line, not the item. The cadence
    decision itself is check_follow_up_due's, and it refuses such a value
    structurally.
    """
    last_touch = message.last_touched_at or message.sent_at
    if not last_touch:
        return None
    try:
        parsed = datetime.fromisoformat(last_touch)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return None
    return (datetime.now(timezone.utc) - parsed).days


def _review_one_follow_up(
    runtime: LocalRuntime, approval: Approval, *, model: str | None,
) -> str:
    """Show one queued follow-up, take the decision, and carry it out.

    Returns "sent", "declined", or "skipped". Every failure that belongs to
    this one item — a missing recipient, an out-of-band edit to the draft, a
    malformed payload, a dead SMTP server — is raised as an OperationError
    for the caller to count and move past.

    The loop lives in the command body (via this helper) rather than in the
    operations layer on purpose: an agent draining the same queue makes its
    own decisions about re-proposing, and non-approval prompts stay direct
    Prompt.ask calls, both settled in an earlier phase.
    """
    job_id = payload_value(approval, "job_id")
    person_id = payload_value(approval, "person_id")
    message_id = payload_value(approval, "message_id")

    approval_id = approval.id
    summary = approval.summary
    message = _review_message(runtime, message_id)
    draft_text = message.draft_text
    days_since = _days_since_last_touch(message)
    recipient = _review_recipient_name(runtime, person_id)

    regenerations = 0
    while True:
        # Verbatim for the same reason the panel is: the summary the proposer
        # wrote embeds job.company and job.title straight from the posting.
        console.print(verbatim(summary))
        if days_since is not None:
            rprint(str(days_since) + " day(s) since the last touch.")
        console.print(Panel(
            verbatim(draft_text), title=verbatim("Follow-up to " + recipient),
        ))
        # Default is skip, not accept: a stray Enter on a queue drainer must
        # not send an email to someone you want a referral from. `outreach
        # send` can safely default to accept because the user got there by
        # naming that one person; here the prompt arrives unbidden, once per
        # queued item.
        if regenerations >= MAX_REGENERATIONS:
            choice = Prompt.ask(
                "[A]ccept / [D]ecline / [S]kip", choices=["a", "d", "s"], default="s",
            )
        else:
            choice = Prompt.ask(
                "[A]ccept / [R]egenerate / [D]ecline / [S]kip",
                choices=["a", "r", "d", "s"], default="s",
            )
        if choice == "s":
            # Left pending, so the next review offers it again unchanged.
            # Nothing is written, so last_touched_at does not advance and
            # the relationship stays due — a skip is "not now", where a
            # decline is "not this one".
            return "skipped"
        if choice == "r":
            try:
                proposal = propose_follow_up(
                    runtime, job_id, person_id, model=model,
                    action_label=REVIEW_CMD_ACTION_LABEL,
                )
            except (OperationError, FileNotFoundError, ValidationError) as exc:
                # A re-propose re-runs the whole due-ness check against the
                # cadence policy as it stands *now*, and the policy file can
                # have been edited between the cron run that queued this and
                # this review: a tightened days_between_touches raises
                # NotDueForFollowUp, a lowered max_touches raises
                # CadenceExhausted, and a deleted or broken
                # config/cadence_policy.json raises FileNotFoundError or
                # ValidationError, neither of which is an OperationError.
                # None of them may cost the user this item, let alone the
                # rest of the queue: propose_follow_up writes nothing when it
                # refuses, so the approval on screen is still pending and the
                # draft they were offered is still exactly what would send.
                rprint(
                    "[yellow]Could not redraft this follow-up, so the queued "
                    "draft stays on offer:[/yellow]"
                )
                rprint(str(exc))
                continue
            # From here the loop is reviewing a different approval.
            # propose_follow_up goes through open_approval, which supersedes
            # the one it replaces — so resolving the id this item started
            # with would now raise ApprovalNotGranted, and its digest no
            # longer matches the stored draft either. Everything displayed
            # and everything resolved switches to the new proposal.
            # Counted only now that a draft actually came back. It used to
            # be incremented before the call, so MAX_REGENERATIONS refusals
            # in a row — a corrupt cadence policy refuses every time —
            # permanently withdrew regenerate for this item even though
            # nothing had ever been drafted. The bound exists to cap paid
            # LLM calls; a refusal costs none.
            regenerations += 1
            approval_id = proposal.approval_id
            summary = proposal.summary
            draft_text = proposal.draft_text
            days_since = proposal.days_since_last_touch
            recipient = proposal.recipient_name
            continue
        break

    if choice == "d":
        resolve_approval(
            runtime, approval_id,
            ApprovalResult(approved=False, reason="declined at outreach review"),
            action_label=REVIEW_CMD_ACTION_LABEL,
        )
        decline_follow_up(runtime, approval_id, action_label=REVIEW_CMD_ACTION_LABEL)
        rprint("Declined the follow-up to " + recipient + ".")
        return "declined"

    # The keystroke above *is* the per-item approval, so the result is built
    # here rather than routed through runtime.request_approval — which on a
    # LocalRuntime would put a second confirmation prompt in front of the
    # same decision the user just made. `outreach send` already constructs
    # an ApprovalResult directly on its abort path for the same reason. No
    # path here approves without that keystroke.
    resolve_approval(
        runtime, approval_id,
        ApprovalResult(approved=True, reason="approved at outreach review"),
        action_label=REVIEW_CMD_ACTION_LABEL,
    )
    outcome = execute_follow_up(
        runtime, approval_id, action_label=REVIEW_CMD_ACTION_LABEL,
    )
    rprint(
        "[green]Sent follow-up to " + outcome.recipient_name + " (touch #"
        + str(outcome.touch_count) + ")[/green]"
    )
    return "sent"


def _report_stranded_follow_ups(runtime: LocalRuntime) -> None:
    """Name the approved-but-unsent follow-ups this command cannot touch.

    A send refused *before* it was attempted — no address on file, or the
    stored draft edited out from under the approval — deliberately leaves
    the approval `approved` so the refusal stays retryable. But
    `list_pending` does not report an `approved` record, so the queue above
    cannot see it and the reviewer has no way to learn it exists. It is a
    decision the user already made that silently produced nothing.

    Reported, not retried, and that limit is structural rather than a
    choice to defer work: `resolve_approval` requires `pending`, so the
    accept path raises ApprovalNotGranted against an `approved` record
    immediately, and the decline path has no legal transition at all —
    `resolve_approval` is the only writer of `declined`, and
    `decline_follow_up` then requires `declined`. Retrying in place would
    need either a new approved->declined edge or a reopen-to-pending
    operation, and neither is worth widening the approval state machine for
    when the scheduled proposer already recovers the relationship: since
    nothing was sent, `last_touched_at` never advanced, so it is still due,
    and open_approval now supersedes `approved` as well as `pending`.

    Deliberately prints nothing when there are none: this is an anomaly
    notice, and a clean run must not carry a line about it.
    """
    stranded = [
        approval for approval in list_by_state(runtime.storage, APPROVED)
        if approval.action == FOLLOW_UP_ACTION
    ]
    if not stranded:
        return
    rprint(
        "[yellow]" + str(len(stranded)) + " follow-up(s) were approved but "
        "never sent: the send was refused before it was attempted (no email "
        "address on file, or the draft changed under the approval). They "
        "cannot be decided again here — only a pending approval can be "
        "decided. The next 'careeros outreach follow-up' run supersedes each "
        "one and re-drafts it if the relationship is still due.[/yellow]"
    )


@outreach_app.command()
def review(
    workspace: str = typer.Option(None, "--workspace", help="Workspace path"),
    model: str = typer.Option(None, "--model", help="Override LLM model"),
) -> None:
    """Review the queued follow-up drafts and send the ones you accept.

    The interactive drainer for the queue 'careeros outreach follow-up'
    fills. Nothing here drafts on its own — every item on screen was queued by the
    scheduled proposer — and nothing sends without a keystroke against that
    specific draft.
    """
    runtime = _open_runtime(workspace)

    # list_pending returns every pending approval regardless of action, so
    # filtering is this command's job. An unrelated pending send_outreach or
    # apply_to_job approval belongs to another flow's reviewer, carries a
    # different payload shape, and must come out of this run untouched.
    queue = [
        approval for approval in list_pending(runtime.storage)
        if approval.action == FOLLOW_UP_ACTION
    ]
    if not queue:
        rprint("No follow-ups are waiting for review.")
        # Still worth saying on this path — in fact most worth saying here.
        # A relationship whose send was refused pre-attempt has an approved
        # approval and no pending one, so "nothing is waiting" was the whole
        # of what this command told the user about it.
        _report_stranded_follow_ups(runtime)
        return

    counts = {"sent": 0, "declined": 0, "skipped": 0}
    failed = 0
    for approval in queue:
        try:
            counts[_review_one_follow_up(runtime, approval, model=model)] += 1
        except OperationError as exc:
            # One item's failure costs one item. The queue is the user's
            # whole follow-up to-do list, and these failures are per-item and
            # real — a person with no email address, a draft edited out of
            # band since the approval was written, a payload missing a key —
            # so abandoning the rest of the queue on the first one would
            # leave the remaining relationships unreviewed with no
            # indication that they had been reached.
            failed += 1
            rprint(
                "[red]" + (approval.entity_id or approval.id) + ": " + str(exc)
                + "[/red]"
            )
            continue

    rprint(
        "Reviewed " + str(len(queue)) + " follow-up(s): " + str(counts["sent"])
        + " sent, " + str(counts["declined"]) + " declined, "
        + str(counts["skipped"]) + " skipped, " + str(failed) + " failed."
    )
    # Read after the loop, so a missing recipient hit *this* run is included:
    # its approval is approved-and-unexecuted by now, exactly like one
    # stranded by an earlier run.
    _report_stranded_follow_ups(runtime)
    if failed:
        # Nonzero so a run where an item could not be acted on is
        # distinguishable from a clean drain, which matters most when this is
        # driven from a script rather than read off a terminal.
        raise typer.Exit(1)


@people_app.command()
def update(
    person_id: str = typer.Argument(..., help="Person ID"),
    email: str = typer.Option(..., "--email", help="Email address to set"),
    workspace: str = typer.Option(None, "--workspace", help="Workspace path"),
) -> None:
    runtime = _open_runtime(workspace)

    try:
        person_obj = Person.load(runtime.storage, person_id)
    except (FileNotFoundError, ValueError):
        rprint("[red]Person " + person_id + " not found.[/red]")
        raise typer.Exit(1)

    person_obj = person_obj.model_copy(update={"email": email})
    person_obj.save(runtime.storage)
    rprint("[green]Updated email for " + person_obj.name + "[/green]")
