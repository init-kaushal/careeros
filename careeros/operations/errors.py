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
