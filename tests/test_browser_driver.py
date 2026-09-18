import platform
import pytest
from careeros.browser.driver import get_chrome_profile_path


def test_get_chrome_profile_path_macos(monkeypatch):
    monkeypatch.setattr(platform, "system", lambda: "Darwin")
    path = get_chrome_profile_path()
    assert "Google/Chrome" in path
    assert path.startswith("/")


def test_get_chrome_profile_path_linux(monkeypatch):
    monkeypatch.setattr(platform, "system", lambda: "Linux")
    path = get_chrome_profile_path()
    assert "google-chrome" in path
    assert path.startswith("/")


def test_get_chrome_profile_path_windows(monkeypatch):
    monkeypatch.setattr(platform, "system", lambda: "Windows")
    monkeypatch.setenv("LOCALAPPDATA", r"C:\Users\test\AppData\Local")
    path = get_chrome_profile_path()
    assert "Google" in path
    assert "Chrome" in path
