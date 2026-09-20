import os
import platform
import stat
from unittest.mock import MagicMock, patch

import pytest

from careeros.browser.driver import (
    BrowserProfileBusy,
    get_careeros_profile_path,
    launch_browser,
)


def test_profile_path_macos(monkeypatch):
    monkeypatch.setattr(platform, "system", lambda: "Darwin")
    path = get_careeros_profile_path()
    assert path.endswith("careeros/browser")
    assert "Application Support" in path
    assert path.startswith("/")


def test_profile_path_linux_honours_xdg_state_home(monkeypatch):
    monkeypatch.setattr(platform, "system", lambda: "Linux")
    monkeypatch.setenv("XDG_STATE_HOME", "/custom/state")
    assert get_careeros_profile_path() == "/custom/state/careeros/browser"


def test_profile_path_linux_defaults_without_xdg(monkeypatch):
    monkeypatch.setattr(platform, "system", lambda: "Linux")
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    path = get_careeros_profile_path()
    assert path.endswith(".local/state/careeros/browser")


def test_profile_path_windows(monkeypatch):
    monkeypatch.setattr(platform, "system", lambda: "Windows")
    monkeypatch.setenv("LOCALAPPDATA", r"C:\Users\test\AppData\Local")
    path = get_careeros_profile_path()
    assert "careeros" in path
    assert "browser" in path


def test_profile_path_is_never_the_live_chrome_profile(monkeypatch):
    # The whole point of Phase 9c. Asserted on every platform.
    for system in ("Darwin", "Linux", "Windows"):
        monkeypatch.setattr(platform, "system", lambda s=system: s)
        monkeypatch.setenv("LOCALAPPDATA", r"C:\Users\test\AppData\Local")
        path = get_careeros_profile_path().lower()
        assert "google" not in path
        assert "google-chrome" not in path


def test_get_chrome_profile_path_is_gone():
    import careeros.browser.driver as driver
    assert not hasattr(driver, "get_chrome_profile_path")


def _fake_playwright(context):
    fake = MagicMock()
    fake.__enter__.return_value.chromium.launch_persistent_context.return_value = context
    fake.__exit__.return_value = False
    return fake


def test_launch_browser_uses_isolated_profile_and_creates_it_0700(tmp_path, monkeypatch):
    profile = tmp_path / "careeros" / "browser"
    monkeypatch.setattr(
        "careeros.browser.driver.get_careeros_profile_path", lambda: str(profile)
    )
    context = MagicMock()
    with patch("playwright.sync_api.sync_playwright", return_value=_fake_playwright(context)):
        with launch_browser(headless=True) as (ctx, _page):
            assert ctx is context

    assert profile.is_dir()
    assert stat.S_IMODE(os.stat(profile).st_mode) == 0o700


def test_launch_browser_passes_the_isolated_dir_to_playwright(tmp_path, monkeypatch):
    profile = tmp_path / "prof"
    monkeypatch.setattr(
        "careeros.browser.driver.get_careeros_profile_path", lambda: str(profile)
    )
    context = MagicMock()
    fake = _fake_playwright(context)
    with patch("playwright.sync_api.sync_playwright", return_value=fake):
        with launch_browser(headless=True):
            pass
    kwargs = fake.__enter__.return_value.chromium.launch_persistent_context.call_args.kwargs
    assert kwargs["user_data_dir"] == str(profile)
    assert kwargs["headless"] is True
    assert kwargs["channel"] == "chrome"


def test_launch_browser_closes_the_context(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "careeros.browser.driver.get_careeros_profile_path", lambda: str(tmp_path / "p")
    )
    context = MagicMock()
    with patch("playwright.sync_api.sync_playwright", return_value=_fake_playwright(context)):
        with launch_browser(headless=True):
            pass
    context.close.assert_called_once()


def test_profile_lock_error_becomes_browser_profile_busy(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "careeros.browser.driver.get_careeros_profile_path", lambda: str(tmp_path / "p")
    )
    fake = MagicMock()
    fake.__enter__.return_value.chromium.launch_persistent_context.side_effect = Exception(
        "Failed to create a ProcessSingleton for your profile directory."
    )
    fake.__exit__.return_value = False
    with patch("playwright.sync_api.sync_playwright", return_value=fake):
        with pytest.raises(BrowserProfileBusy) as excinfo:
            with launch_browser(headless=True):
                pass
    assert "already in use" in str(excinfo.value)


def test_unrelated_launch_error_is_not_swallowed(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "careeros.browser.driver.get_careeros_profile_path", lambda: str(tmp_path / "p")
    )
    fake = MagicMock()
    fake.__enter__.return_value.chromium.launch_persistent_context.side_effect = Exception(
        "Executable doesn't exist"
    )
    fake.__exit__.return_value = False
    with patch("playwright.sync_api.sync_playwright", return_value=fake):
        with pytest.raises(Exception) as excinfo:
            with launch_browser(headless=True):
                pass
    assert not isinstance(excinfo.value, BrowserProfileBusy)


def test_port_collision_is_not_mistaken_for_a_profile_lock(tmp_path, monkeypatch):
    # "Address already in use" is a port/IPC collision, not a profile lock.
    # Misclassifying it as BrowserProfileBusy would hide the real cause from
    # the CLI handler and tell the user to close a browser that isn't the problem.
    monkeypatch.setattr(
        "careeros.browser.driver.get_careeros_profile_path", lambda: str(tmp_path / "p")
    )
    fake = MagicMock()
    fake.__enter__.return_value.chromium.launch_persistent_context.side_effect = Exception(
        "bind() returned an error: Address already in use"
    )
    fake.__exit__.return_value = False
    with patch("playwright.sync_api.sync_playwright", return_value=fake):
        with pytest.raises(Exception) as excinfo:
            with launch_browser(headless=True):
                pass
    assert not isinstance(excinfo.value, BrowserProfileBusy)
    assert "Address already in use" in str(excinfo.value)
