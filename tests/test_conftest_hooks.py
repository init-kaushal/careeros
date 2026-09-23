"""The collection hook that gates the integration suite.

This exists because the hook shipped broken: it added a skip marker to every
`integration`-marked test unconditionally, including when the caller passed the
very `-m integration` its own skip reason advertises. The whole integration
suite was therefore unreachable by any invocation, and that broken gate was
silently load-bearing for safety — it was the only thing preventing
`tests/integration/test_live_apply.py` from filling real application forms.

Testing the hook directly rather than through a subprocess run keeps this cheap
and keeps it honest: the defect was in the hook's own branching.
"""
from unittest.mock import MagicMock

from tests.conftest import pytest_collection_modifyitems


def _item(*, integration: bool):
    item = MagicMock()
    item.get_closest_marker.return_value = object() if integration else None
    return item


def _config(markexpr):
    config = MagicMock()
    config.getoption.return_value = markexpr
    return config


class TestIntegrationGate:
    def test_integration_items_are_skipped_by_default(self):
        item = _item(integration=True)
        pytest_collection_modifyitems(_config(""), [item])
        item.add_marker.assert_called_once()

    def test_non_integration_items_are_never_skipped(self):
        item = _item(integration=False)
        pytest_collection_modifyitems(_config(""), [item])
        item.add_marker.assert_not_called()

    def test_explicit_dash_m_integration_is_honoured(self):
        """The opt-in the skip reason names must actually work."""
        item = _item(integration=True)
        pytest_collection_modifyitems(_config("integration"), [item])
        item.add_marker.assert_not_called()

    def test_deselecting_integration_needs_no_skip_marker(self):
        # `-m "not integration"` already deselects them, so adding a skip
        # marker would be redundant; returning early is correct either way.
        item = _item(integration=True)
        pytest_collection_modifyitems(_config("not integration"), [item])
        item.add_marker.assert_not_called()

    def test_an_unset_marker_expression_does_not_crash(self):
        # pytest can hand back None rather than "" depending on invocation.
        item = _item(integration=True)
        pytest_collection_modifyitems(_config(None), [item])
        item.add_marker.assert_called_once()
