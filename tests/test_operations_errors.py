"""Shape tests for the operations error vocabulary.

Only the properties a caller actually depends on: operations never print, so
callers branch on these types, and the CLI's queue drains catch
OperationError as the one thing that separates "this item failed" from a bug.
"""

import pytest

from careeros.operations.errors import ConnectionNotSent, OperationError


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
