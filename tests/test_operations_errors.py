"""Shape tests for the operations error vocabulary.

Only the properties a caller actually depends on: operations never print, so
callers branch on these types, and the CLI's queue drains catch
OperationError as the one thing that separates "this item failed" from a bug.
"""

import pytest

from careeros.operations.errors import (
    ConnectionAlreadySent, ConnectionNotSent, OperationError,
)


def test_connection_not_sent_is_an_operation_error():
    assert issubclass(ConnectionNotSent, OperationError)


def test_connection_not_sent_takes_no_required_arguments():
    # The spec gives it no constructor arguments — FillIncomplete is the
    # precedent — so raising it must not require inventing a detail string.
    with pytest.raises(OperationError):
        raise ConnectionNotSent()


def test_connection_not_sent_carries_a_message_when_given_one():
    with pytest.raises(ConnectionNotSent) as exc_info:
        raise ConnectionNotSent("No Connect button on the profile.")
    assert "No Connect button on the profile." in str(exc_info.value)


def test_connection_already_sent_is_an_operation_error():
    assert issubclass(ConnectionAlreadySent, OperationError)


def test_connection_already_sent_is_not_a_connection_not_sent():
    """The two mean opposite things to a retry.

    ConnectionNotSent means the page declined to complete the request, so
    trying again later is reasonable. ConnectionAlreadySent means careeros
    declined to offer it because the recipient already has one — trying
    again is the harm. A caller must be able to tell them apart by type
    rather than by string-matching a message.
    """
    assert not issubclass(ConnectionAlreadySent, ConnectionNotSent)
    assert not issubclass(ConnectionNotSent, ConnectionAlreadySent)


def test_connection_already_sent_carries_the_prior_request_and_its_timestamp():
    # Carried as attributes so a caller can point at the record without
    # re-scanning connections/ to find out which request it was.
    with pytest.raises(ConnectionAlreadySent) as exc_info:
        raise ConnectionAlreadySent(
            "acme-corp-jane-doe", "Jane Doe", "job1__acme-corp-jane-doe",
            "2026-09-20T10:00:00+00:00",
        )
    error = exc_info.value
    assert error.person_id == "acme-corp-jane-doe"
    assert error.person_name == "Jane Doe"
    assert error.request_id == "job1__acme-corp-jane-doe"
    assert error.sent_at == "2026-09-20T10:00:00+00:00"
    assert "Jane Doe" in str(error)
    assert "2026-09-20T10:00:00+00:00" in str(error)
    # Names the record, so a user who disagrees knows what to look at.
    assert "connections/job1__acme-corp-jane-doe.json" in str(error)
