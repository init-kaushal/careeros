import pytest
from unittest.mock import patch
from careeros.storage.filesystem import LocalFilesystemStorage
from careeros.workspace.manager import init_workspace, WorkspaceContext


def pytest_collection_modifyitems(config, items):
    for item in items:
        if item.get_closest_marker("integration"):
            item.add_marker(pytest.mark.skip(reason="requires live browser — run with pytest -m integration"))

SAMPLE_RESUME = """
Alice Johnson
Senior Site Reliability Engineer | San Francisco, CA
alice@example.com

EXPERIENCE
Site Reliability Engineer — MegaCorp (2018–present, 6 years)
  - Built distributed monitoring platform handling 1M events/sec
  - Reduced MTTR by 40% through improved alerting and runbooks

Software Engineer — StartupXYZ (2016–2018)
  - Built microservices in Go and Python

SKILLS
Python, Go, Kubernetes, Terraform, AWS, Prometheus, Grafana, Linux

EDUCATION
BS Computer Science, UC Berkeley, 2016
"""


@pytest.fixture
def tmp_workspace(tmp_path) -> WorkspaceContext:
    storage = LocalFilesystemStorage(str(tmp_path))
    return init_workspace(storage)


@pytest.fixture(autouse=True)
def _guard_against_real_smtp():
    """Prevent any test from making real SMTP connections.

    This is an autouse guard at the smtplib layer: it patches
    `smtplib.SMTP`/`smtplib.SMTP_SSL` on the module, so it covers any caller
    that does `import smtplib` and looks up the attribute at call time (as
    `careeros/mailer.py` does). It would not catch a caller that instead did
    `from smtplib import SMTP`, since that binds the real class before this
    fixture ever runs. Per-test explicit patches override it, so tests that
    legitimately exercise the mailer (test_mailer.py) can still patch
    send_email to run their own SMTP mocks.
    """
    with patch("smtplib.SMTP", side_effect=AssertionError(
            "test attempted a real SMTP connection")):
        with patch("smtplib.SMTP_SSL", side_effect=AssertionError(
                "test attempted a real SMTP connection")):
            yield
