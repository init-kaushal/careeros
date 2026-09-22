from __future__ import annotations


class OperationError(Exception):
    """Base for every failure an operation reports to its caller.

    Operations never print and never exit, so this hierarchy is the whole
    vocabulary a caller branches on. Each subclass carries the structured
    detail a caller needs to word its own message, so no caller has to
    string-match.
    """


class EntityNotFound(OperationError):
    pass


class ResumeNotFound(EntityNotFound):
    """No resume exists in resumes/versions/ for propose_apply to select.

    A subclass of EntityNotFound, not a sibling, so every existing catch of
    EntityNotFound (apply_cmd.py, its tests) keeps matching this case exactly
    as it did before this type existed. A dedicated type exists only so a
    caller that needs to tell "no resume" apart from "no filler" (the
    scheduled discover-and-apply command does) can dispatch on type instead
    of string-matching the message.
    """

    def __init__(self) -> None:
        super().__init__(
            "No resume found in resumes/versions/ — add one first."
        )


class NoFillerAvailable(EntityNotFound):
    """No registered filler can handle the job's URL.

    See ResumeNotFound's docstring: same reasoning, same subclass relationship.
    """

    def __init__(self, url: str) -> None:
        super().__init__(
            "No filler available for this URL: " + url
        )
        self.url = url


class PolicyBlocked(OperationError):
    def __init__(self, rule: str) -> None:
        super().__init__(
            "Blocked by policy (" + rule
            + "). Edit config/policies.json to change this."
        )
        self.rule = rule


class DraftFailed(OperationError):
    pass


class MissingRecipient(OperationError):
    def __init__(self, person_id: str, person_name: str) -> None:
        super().__init__(
            "No email on file for " + person_name
            + ". Run 'careeros people update " + person_id
            + " --email <address>' and retry."
        )
        self.person_id = person_id
        self.person_name = person_name


class ApprovalNotGranted(OperationError):
    def __init__(self, approval_id: str, state: str) -> None:
        super().__init__(
            "Approval " + approval_id + " is in state " + repr(state)
            + ", which does not permit this step."
        )
        self.approval_id = approval_id
        self.state = state


class MalformedApproval(OperationError):
    def __init__(self, approval_id: str, key: str) -> None:
        super().__init__(
            "Approval " + approval_id + " is missing payload key " + repr(key)
        )
        self.approval_id = approval_id
        self.key = key


class WrongApprovalAction(OperationError):
    """The approval id passed to an execute_* belongs to a different action.

    Nothing else guards this: an execute_apply given an outreach approval id
    (or vice versa) would otherwise be safe only by accident of the two
    actions' payloads not sharing key names — the exact kind of mistake the
    cross-process contract (a different, later process supplying an id it
    did not mint) most invites. Raised before any state change, so a
    transposed id never consumes the approval it names.
    """

    def __init__(self, approval_id: str, expected: str, actual: str) -> None:
        super().__init__(
            "Approval " + approval_id + " is a " + repr(actual)
            + " approval, not " + repr(expected) + "."
        )
        self.approval_id = approval_id
        self.expected = expected
        self.actual = actual


class ArtifactChanged(OperationError):
    def __init__(self, path: str) -> None:
        super().__init__(
            "The approved content at " + path + " has changed since it was approved. "
            "Re-propose so the review covers what would actually be sent."
        )
        self.path = path


class SendFailed(OperationError):
    def __init__(self, detail: str) -> None:
        super().__init__("Send failed: " + detail)
        self.detail = detail


class BoardSessionRequired(OperationError):
    def __init__(self, board: str) -> None:
        super().__init__(
            "Not signed in to " + board
            + ". Run: careeros browser login --board " + board
        )
        self.board = board


class FillIncomplete(OperationError):
    pass


class BrowserUnavailable(OperationError):
    def __init__(self, detail: str, *, profile_busy: bool = False) -> None:
        super().__init__(detail)
        # A locked browser profile is a whole-run condition, not a per-job
        # one: the scheduled discover-and-apply command must stop rather
        # than pay for a cover letter on every remaining job only to fail
        # identically at launch, while other browser failures only sink the
        # one job being applied to. Carrying the distinction as a flag keeps
        # that decision with the caller rather than baking a policy choice
        # in here, and it stops a raw browser exception from leaking through
        # the operations boundary.
        self.profile_busy = profile_busy
