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


@pytest.fixture(autouse=True)
def _guard_against_real_browser():
    """Prevent any test from launching a real browser.

    `careeros/browser/driver.py`'s launch_browser does
    `from playwright.sync_api import sync_playwright` inside the function
    body, so it looks up the `sync_playwright` attribute on the
    `playwright.sync_api` module fresh on every call — exactly the
    call-time-lookup shape the SMTP guard above relies on. Patching
    `playwright.sync_api.sync_playwright` therefore intercepts every call
    regardless of how careeros.browser.driver imported it.

    Without this, launch_browser opens real Chrome (channel="chrome")
    against the real machine-global profile, which can carry the user's
    real LinkedIn session, and GreenhouseFiller/LeverFiller auto-submit.
    Per-test explicit patches of launch_browser or sync_playwright still
    override this normally.
    """
    with patch("playwright.sync_api.sync_playwright", side_effect=AssertionError(
            "test attempted to launch a real browser")):
        # careeros/render/resume_pdf.py does `from playwright.sync_api import
        # sync_playwright` at MODULE level, binding its own module-namespace
        # name at import time rather than looking it up per call — the
        # patch above never touches that already-bound name, so it needs
        # its own patch to make the "any test" claim above actually true.
        with patch("careeros.render.resume_pdf.sync_playwright", side_effect=AssertionError(
                "test attempted to launch a real browser")):
            yield
