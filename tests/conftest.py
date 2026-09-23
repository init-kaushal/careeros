import pytest
from unittest.mock import patch
from careeros.storage.filesystem import LocalFilesystemStorage
from careeros.workspace.manager import init_workspace, WorkspaceContext


def pytest_collection_modifyitems(config, items):
    # `-m integration` is the documented opt-in, named in the skip reason
    # below. Honour it: when the marker expression mentions integration at
    # all, leave those items alone and let pytest's own -m selection decide.
    # This hook previously skipped them unconditionally, which made the
    # opt-in it advertises impossible — the integration suite was
    # unreachable by any invocation.
    if "integration" in (config.getoption("markexpr", default="") or ""):
        return
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


def _is_opted_in_integration(request) -> bool:
    """True for an `integration`-marked test that -m selection let through.

    The two autouse guards below exist to protect the default suite. An
    integration test's whole purpose is to exercise the real resource, and
    reaching one already requires two deliberate acts: passing
    `-m integration`, and — for the tests that actually submit a form —
    setting an env var naming a specific real job URL, without which they
    skip themselves. Leaving the guards armed for those tests would make
    them fail on the guard instead of running, which is what kept the
    suite unreachable in the first place.
    """
    return request.node.get_closest_marker("integration") is not None


@pytest.fixture(autouse=True)
def _guard_against_real_smtp(request):
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
    if _is_opted_in_integration(request):
        yield
        return
    with patch("smtplib.SMTP", side_effect=AssertionError(
            "test attempted a real SMTP connection")):
        with patch("smtplib.SMTP_SSL", side_effect=AssertionError(
                "test attempted a real SMTP connection")):
            yield


@pytest.fixture(autouse=True)
def _guard_against_real_browser(request):
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
    if _is_opted_in_integration(request):
        yield
        return
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
