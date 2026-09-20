# Phase 9c Browser Profile Isolation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** CareerOS drives a dedicated Chrome profile containing only the job-board sessions the user explicitly authorized, instead of the user's live profile holding every cookie and saved credential they own.

**Architecture:** A new `boards.py` registry becomes the single source for the board list that currently exists in four places, carrying each board's scraper, login URL, and session-cookie name. `driver.py` repoints `user_data_dir` at a machine-global CareerOS profile. A new `session.py` reads cookies from that profile to answer "is this board authorized?". A new `careeros browser` command group lets the user authorize boards interactively. Four existing commands gain a per-board pre-flight, and the `discover-and-apply` opt-in gate comes down.

**Tech Stack:** Python 3.11+, Playwright (`launch_persistent_context`, `BrowserContext.cookies`), Typer, Rich, pytest with `unittest.mock.patch`.

**Spec:** `docs/superpowers/specs/2026-09-20-phase9c-browser-isolation-design.md`

## Global Constraints

- The browser profile lives **outside** the workspace. It is machine-global, not per-workspace, and nothing under `careeros/browser/` may import from `careeros/workspace/` or read `manifest.json`.
- Filesystem access in `careeros/browser/driver.py` is system-path resolution, not workspace I/O, and is exempt from the `StorageProvider` constraint — the existing exemption comments in that file record this.
- All other workspace I/O goes through `StorageProvider`.
- Activity logs are append-only; no event is ever edited or deleted.
- `AgentRuntime.record_activity` always overwrites `event.agent_runtime` and `event.session_id`.
- No f-strings with user data — string concatenation only (existing project-wide convention).
- Tests run fully offline: no real browser, no network. Playwright is mocked at the `launch_browser` / `BrowserContext` seam, patched at the *caller's* import site (existing house pattern).
- Dependency direction is one-way: `cli → session → boards → scrapers`, with `driver` at the bottom.
- `activity` event `status` is one of `success` / `failed` / `blocked`. `blocked` belongs to 9a's `PolicyEngine`; a missing browser session uses `failed`.

## File Structure

**Create:**

| File | Responsibility |
|---|---|
| `careeros/browser/boards.py` | The `Board` record and `BOARDS` registry. Pure data; no I/O, no Playwright import. |
| `careeros/browser/session.py` | `check_board_sessions()` — reads cookies from the isolated profile. No CLI, no printing. |
| `careeros/cli/preflight.py` | `require_board_session()` — the print-and-exit wrapper the three interactive commands share. |
| `careeros/cli/browser_cmd.py` | The `careeros browser` Typer app (`login`, `status`). The only module here that prompts or prints tables. |
| `tests/test_boards.py` | Registry invariants. |
| `tests/test_browser_session.py` | Session reads and the close-before-return invariant. |
| `tests/test_browser_cmd.py` | `login` / `status` behaviour. |

**Modify:**

| File | Change |
|---|---|
| `careeros/browser/driver.py` | Add `get_careeros_profile_path()`, `BrowserProfileBusy`; delete `get_chrome_profile_path()`; repoint `launch_browser`. |
| `careeros/core/models.py:135` | `_VALID_AUTOMATION_BOARDS` derives from `BOARDS`. |
| `careeros/cli/browse_cmd.py` | `SCRAPERS`/`valid_boards` derive from `BOARDS`; add pre-flight; fix `result["reasoning"]`. |
| `careeros/cli/discover_and_apply_cmd.py` | `SCRAPERS` derives from `BOARDS`; add pre-flight + summary segment; remove the gate. |
| `careeros/cli/apply_cmd.py` | Pre-flight when the resolved filler is `LinkedInFiller`. |
| `careeros/cli/research_cmd.py` | Pre-flight `linkedin` for `company` and `people` (not `compensation`). |
| `careeros/cli/main.py` | Register `browser_app`. |
| `tests/test_browser_driver.py` | Replace the three `get_chrome_profile_path` tests. |
| `tests/test_browse_cmd.py`, `test_apply_cmd.py`, `test_research_cmd.py`, `test_discover_and_apply_cmd.py` | Pre-flight coverage; delete gate tests. |
| `README.md`, `docs/getting-started.md`, `ROADMAP.md` | Login step, corrected Playwright claim, Phase 9/10 status. |

---

### Task 1: Board registry, and collapse the four duplicate board lists into it

**Files:**
- Create: `careeros/browser/boards.py`
- Create: `tests/test_boards.py`
- Modify: `careeros/core/models.py:135`
- Modify: `careeros/cli/browse_cmd.py:12-15,27-31,57`
- Modify: `careeros/cli/discover_and_apply_cmd.py:9-11,27-31`

**Interfaces:**
- Produces: `Board` (frozen dataclass with `name: str`, `scraper: Scraper`, `login_url: str`, `session_cookie: str`, `cookie_domain: str`), `BOARDS: dict[str, Board]`, `BOARD_NAMES: tuple[str, ...]`. Used by Tasks 3, 4, 5, 6.

The board list exists in four places today. This task makes it exist in one, and proves the collapse is behaviour-preserving by asserting the derived tuple equals the literal it replaces.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_boards.py`:

```python
from careeros.browser.boards import BOARDS, BOARD_NAMES, Board


def test_registry_covers_the_three_browser_boards():
    assert set(BOARDS) == {"linkedin", "indeed", "wellfound"}


def test_board_names_matches_registry_keys_in_order():
    assert BOARD_NAMES == tuple(BOARDS)


def test_every_board_is_fully_populated():
    for name, board in BOARDS.items():
        assert isinstance(board, Board)
        assert board.name == name
        assert board.login_url.startswith("https://")
        assert board.session_cookie
        assert board.cookie_domain.startswith("https://")


def test_every_scraper_source_board_matches_its_key():
    # Guards the failure this registry exists to prevent: a board that is
    # scrapeable under one name but authorizable under another.
    for name, board in BOARDS.items():
        assert board.scraper.source_board == name


def test_board_is_immutable():
    import dataclasses
    import pytest
    board = BOARDS["linkedin"]
    with pytest.raises(dataclasses.FrozenInstanceError):
        board.session_cookie = "tampered"


def test_registry_matches_the_automation_board_whitelist():
    # The literal that _VALID_AUTOMATION_BOARDS used to hold, asserted
    # against the derived value, so the consolidation is provably
    # behaviour-preserving rather than assumed to be.
    from careeros.core.models import _VALID_AUTOMATION_BOARDS
    assert _VALID_AUTOMATION_BOARDS == ("linkedin", "indeed", "wellfound")
    assert _VALID_AUTOMATION_BOARDS == BOARD_NAMES
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_boards.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'careeros.browser.boards'`

- [ ] **Step 3: Create `careeros/browser/boards.py`**

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from careeros.browser.scrapers.indeed import IndeedScraper
from careeros.browser.scrapers.linkedin import LinkedInScraper
from careeros.browser.scrapers.wellfound import WellfoundScraper

if TYPE_CHECKING:
    from careeros.browser.scrapers.base import Scraper


@dataclass(frozen=True)
class Board:
    """A browser-driven job board.

    Single source of truth for everything CareerOS needs to know about a
    board: how to scrape it, where to send the user to sign in, and which
    cookie proves that sign-in happened.
    """

    name: str
    scraper: "Scraper"
    login_url: str
    session_cookie: str
    cookie_domain: str


# session_cookie values: LinkedIn's `li_at` is long-standing and well known.
# Indeed's and Wellfound's are recorded here as the best available values and
# are verified empirically in Task 4, Step 8 — a guessed cookie name produces a
# pre-flight that is confidently wrong, which is the failure mode this design
# rejected receipt-based checking for. Corrections land here and nowhere else.
BOARDS: dict[str, Board] = {
    "linkedin": Board(
        name="linkedin",
        scraper=LinkedInScraper(),
        login_url="https://www.linkedin.com/login",
        session_cookie="li_at",
        cookie_domain="https://www.linkedin.com",
    ),
    "indeed": Board(
        name="indeed",
        scraper=IndeedScraper(),
        login_url="https://secure.indeed.com/auth",
        session_cookie="CTK",
        cookie_domain="https://www.indeed.com",
    ),
    "wellfound": Board(
        name="wellfound",
        scraper=WellfoundScraper(),
        login_url="https://wellfound.com/login",
        session_cookie="_wellfound_session",
        cookie_domain="https://wellfound.com",
    ),
}

BOARD_NAMES: tuple[str, ...] = tuple(BOARDS)
```

- [ ] **Step 4: Run tests to verify all but the last pass**

Run: `.venv/bin/python -m pytest tests/test_boards.py -v`
Expected: 5 PASS, `test_registry_matches_the_automation_board_whitelist` still PASSES too (the literal in `models.py` happens to match already). If it fails, the registry order is wrong — fix the registry, not the test.

- [ ] **Step 5: Derive `_VALID_AUTOMATION_BOARDS` from the registry**

In `careeros/core/models.py`, replace line 135:

```python
_VALID_AUTOMATION_BOARDS = ("linkedin", "indeed", "wellfound")
```

with:

```python
from careeros.browser.boards import BOARD_NAMES

_VALID_AUTOMATION_BOARDS = BOARD_NAMES
```

Put the import with the other top-of-file imports, not inline.

- [ ] **Step 6: Derive `browse_cmd`'s two copies from the registry**

In `careeros/cli/browse_cmd.py`, delete the three scraper imports (lines 13-15: `IndeedScraper`, `LinkedInScraper`, `WellfoundScraper`) and add:

```python
from careeros.browser.boards import BOARDS
```

Replace the `SCRAPERS` dict (lines 27-31) with:

```python
SCRAPERS: dict = {name: board.scraper for name, board in BOARDS.items()}
```

Replace line 57:

```python
    valid_boards = {"linkedin", "indeed", "wellfound", "url"}
```

with:

```python
    valid_boards = set(BOARDS) | {"url"}
```

Leave the `GenericScraper` import — `--board url` is not a registry board.

- [ ] **Step 7: Derive `discover_and_apply_cmd`'s copy from the registry**

In `careeros/cli/discover_and_apply_cmd.py`, delete the three scraper imports (lines 9-11), add `from careeros.browser.boards import BOARDS`, and replace the `SCRAPERS` dict (lines 27-31) with:

```python
SCRAPERS: dict = {name: board.scraper for name, board in BOARDS.items()}
```

- [ ] **Step 8: Run the full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: 360 passed, 6 skipped (354 existing + 6 new). No failures — this task changes no behaviour.

- [ ] **Step 9: Commit**

```bash
git add careeros/browser/boards.py tests/test_boards.py careeros/core/models.py careeros/cli/browse_cmd.py careeros/cli/discover_and_apply_cmd.py
git commit -m "feat: add board registry and collapse four duplicate board lists into it (Phase 9c)"
```

---

### Task 2: Point the browser at an isolated CareerOS profile

**Files:**
- Modify: `careeros/browser/driver.py:12-37`
- Modify: `tests/test_browser_driver.py` (replace all three existing tests)

**Interfaces:**
- Consumes: nothing from Task 1.
- Produces: `get_careeros_profile_path() -> str`, `BrowserProfileBusy(RuntimeError)`. `launch_browser(headless: bool = False)` keeps its existing signature and yields `(BrowserContext, Page)` unchanged. Used by Tasks 3, 4.
- Removes: `get_chrome_profile_path()`.

This is the task that actually closes the security finding. All seven existing `launch_browser` call sites are untouched — they already funnel through this one function.

- [ ] **Step 1: Write the failing tests**

Replace the entire contents of `tests/test_browser_driver.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_browser_driver.py -v`
Expected: FAIL with `ImportError: cannot import name 'BrowserProfileBusy'`

- [ ] **Step 3: Rewrite `careeros/browser/driver.py` lines 1-37**

Replace everything above `def fetch_jd_text` with:

```python
from __future__ import annotations

import os
import platform
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Generator

if TYPE_CHECKING:
    from playwright.sync_api import BrowserContext, Page


class BrowserProfileBusy(RuntimeError):
    """The isolated CareerOS browser profile is locked by another CareerOS process."""


# Substrings Chrome/Chromium use when a profile directory is already locked.
_LOCK_MARKERS = (
    "processsingleton",
    "singletonlock",
    "already in use",
    "profile appears to be in use",
)


def _is_profile_lock_error(exc: BaseException) -> bool:
    text = str(exc).lower()
    return any(marker in text for marker in _LOCK_MARKERS)


def get_careeros_profile_path() -> str:
    """Path to the dedicated CareerOS browser profile.

    Deliberately NOT the user's live Chrome profile: an unattended run must not
    hold the user's banking/email session cookies. Machine-global rather than
    per-workspace — a person has one LinkedIn account, not one per workspace.

    Session data is state, not config, hence XDG_STATE_HOME on Linux.
    """
    system = platform.system()
    if system == "Darwin":
        # os.path functions here resolve system paths, not workspace I/O — exempt from StorageProvider constraint
        return os.path.expanduser("~/Library/Application Support/careeros/browser")
    if system == "Linux":
        # os.path functions here resolve system paths, not workspace I/O — exempt from StorageProvider constraint
        base = os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state")
        return os.path.join(base, "careeros", "browser")
    # os.path functions here resolve system paths, not workspace I/O — exempt from StorageProvider constraint
    return os.path.expandvars(r"%LOCALAPPDATA%\careeros\browser")


@contextmanager
def launch_browser(headless: bool = False) -> Generator[tuple[BrowserContext, Page], None, None]:
    from playwright.sync_api import sync_playwright

    profile_dir = Path(get_careeros_profile_path())
    profile_dir.mkdir(parents=True, exist_ok=True)
    # 0700: this directory holds live session cookies. Workspace JSON is 0600;
    # a directory of sessions should not be more permissive than that.
    profile_dir.chmod(0o700)

    with sync_playwright() as p:
        try:
            context = p.chromium.launch_persistent_context(
                user_data_dir=str(profile_dir),
                headless=headless,
                channel="chrome",
            )
        except Exception as exc:
            if _is_profile_lock_error(exc):
                raise BrowserProfileBusy(
                    "The CareerOS browser profile is already in use by another CareerOS "
                    "process. Wait for it to finish, or stop it, then retry."
                ) from exc
            raise
        page = context.new_page()
        try:
            yield context, page
        finally:
            context.close()
```

`fetch_jd_text` below is unchanged.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_browser_driver.py -v`
Expected: 11 PASS

- [ ] **Step 5: Run the full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: 368 passed, 6 skipped. Existing command tests patch `launch_browser` at their own import site, so they are unaffected.

- [ ] **Step 6: Commit**

```bash
git add careeros/browser/driver.py tests/test_browser_driver.py
git commit -m "feat: drive a dedicated CareerOS browser profile instead of the user's live Chrome profile (Phase 9c)"
```

---

### Task 3: Session check against the isolated profile

**Files:**
- Create: `careeros/browser/session.py`
- Create: `tests/test_browser_session.py`

**Interfaces:**
- Consumes: `BOARDS` from Task 1; `launch_browser` from Task 2.
- Produces: `check_board_sessions(names: list[str]) -> dict[str, bool]`. Used by Tasks 4, 5, 6.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_browser_session.py`:

```python
from contextlib import contextmanager
from unittest.mock import MagicMock

import pytest

from careeros.browser.session import check_board_sessions


def _patch_launch(monkeypatch, context, record=None):
    @contextmanager
    def fake_launch(headless=False):
        if record is not None:
            record["headless"] = headless
            record["entered"] = True
        try:
            yield context, MagicMock()
        finally:
            if record is not None:
                record["exited"] = True

    monkeypatch.setattr("careeros.browser.session.launch_browser", fake_launch)


def _context_with(cookies_by_domain):
    context = MagicMock()
    context.cookies.side_effect = lambda domain: cookies_by_domain.get(domain, [])
    return context


def test_authorized_when_session_cookie_present(monkeypatch):
    context = _context_with(
        {"https://www.linkedin.com": [{"name": "li_at", "value": "x"}]}
    )
    _patch_launch(monkeypatch, context)
    assert check_board_sessions(["linkedin"]) == {"linkedin": True}


def test_unauthorized_when_session_cookie_absent(monkeypatch):
    context = _context_with(
        {"https://www.linkedin.com": [{"name": "bcookie", "value": "x"}]}
    )
    _patch_launch(monkeypatch, context)
    assert check_board_sessions(["linkedin"]) == {"linkedin": False}


def test_cookie_on_a_different_domain_does_not_authorize(monkeypatch):
    # li_at present, but only under Indeed's domain.
    context = _context_with(
        {"https://www.indeed.com": [{"name": "li_at", "value": "x"}]}
    )
    _patch_launch(monkeypatch, context)
    assert check_board_sessions(["linkedin"]) == {"linkedin": False}


def test_multiple_boards_resolved_in_one_context_open(monkeypatch):
    context = _context_with(
        {
            "https://www.linkedin.com": [{"name": "li_at", "value": "x"}],
            "https://wellfound.com": [],
        }
    )
    record = {}
    _patch_launch(monkeypatch, context, record)
    result = check_board_sessions(["linkedin", "wellfound"])
    assert result == {"linkedin": True, "wellfound": False}
    assert record["entered"] is True


def test_unknown_board_is_false_and_opens_no_browser(monkeypatch):
    record = {}
    _patch_launch(monkeypatch, MagicMock(), record)
    assert check_board_sessions(["nonsense"]) == {"nonsense": False}
    assert "entered" not in record


def test_empty_input_opens_no_browser(monkeypatch):
    record = {}
    _patch_launch(monkeypatch, MagicMock(), record)
    assert check_board_sessions([]) == {}
    assert "entered" not in record


def test_runs_headless(monkeypatch):
    context = _context_with({"https://www.linkedin.com": []})
    record = {}
    _patch_launch(monkeypatch, context, record)
    check_board_sessions(["linkedin"])
    assert record["headless"] is True


def test_context_is_closed_even_when_cookie_read_raises(monkeypatch):
    # The self-deadlock invariant: every caller opens the profile again
    # immediately afterwards, and launch_persistent_context holds an exclusive
    # lock. A context left open would make the pre-flight deadlock against the
    # run it is clearing.
    context = MagicMock()
    context.cookies.side_effect = RuntimeError("boom")
    record = {}
    _patch_launch(monkeypatch, context, record)
    assert check_board_sessions(["linkedin"]) == {"linkedin": False}
    assert record["exited"] is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_browser_session.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'careeros.browser.session'`

- [ ] **Step 3: Create `careeros/browser/session.py`**

```python
from __future__ import annotations

from careeros.browser.boards import BOARDS
from careeros.browser.driver import launch_browser


def check_board_sessions(names: list[str]) -> dict[str, bool]:
    """Report which of `names` have an authorized session in the isolated profile.

    Cookie presence, not a live probe: browsers prune expired cookies, so
    presence is a reasonable proxy for "not stale", and it keeps session
    checking off the markup treadmill. A server-side-revoked session still
    shows a cookie; that degrades to "board returns zero listings", which
    callers already handle.

    The browser context is always closed before returning. Callers open the
    profile again immediately afterwards and launch_persistent_context holds an
    exclusive lock, so a context left open here would deadlock the very run
    this function is clearing.
    """
    result: dict[str, bool] = {name: False for name in names}
    known = [name for name in names if name in BOARDS]
    if not known:
        return result

    with launch_browser(headless=True) as (context, _page):
        for name in known:
            board = BOARDS[name]
            try:
                cookies = context.cookies(board.cookie_domain)
            except Exception:
                result[name] = False
                continue
            result[name] = any(
                cookie.get("name") == board.session_cookie for cookie in cookies
            )
    return result
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_browser_session.py -v`
Expected: 8 PASS

- [ ] **Step 5: Run the full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: 376 passed, 6 skipped

- [ ] **Step 6: Commit**

```bash
git add careeros/browser/session.py tests/test_browser_session.py
git commit -m "feat: add check_board_sessions() against the isolated browser profile (Phase 9c)"
```

---

### Task 4: `careeros browser login` and `careeros browser status`

**Files:**
- Create: `careeros/cli/browser_cmd.py`
- Create: `tests/test_browser_cmd.py`
- Modify: `careeros/cli/main.py`

**Interfaces:**
- Consumes: `BOARDS` (Task 1), `get_careeros_profile_path` / `launch_browser` (Task 2), `check_board_sessions` (Task 3).
- Produces: `browser_app` Typer app registered as `careeros browser`.

`login` requires a workspace because it writes an audit record; `status` does not, because it writes nothing. The rule: a workspace is required exactly when writing to the activity log.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_browser_cmd.py`:

```python
import json
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from careeros.cli.main import app

runner = CliRunner()


def _workspace(tmp_path):
    from careeros.storage.filesystem import LocalFilesystemStorage
    from careeros.workspace.manager import init_workspace

    ws = tmp_path / "ws"
    storage = LocalFilesystemStorage(str(ws))
    init_workspace(storage)
    return ws


def _patch_launch(cookie_sequence, record=None):
    """cookie_sequence: list of cookie-lists returned on successive calls."""
    context = MagicMock()
    context.cookies.side_effect = list(cookie_sequence)
    page = MagicMock()

    @contextmanager
    def fake_launch(headless=False):
        if record is not None:
            record["headless"] = headless
            record["page"] = page
        yield context, page

    return fake_launch, context, page


def test_login_rejects_unknown_board(tmp_path):
    ws = _workspace(tmp_path)
    result = runner.invoke(app, ["browser", "login", "--board", "nonsense", "--workspace", str(ws)])
    assert result.exit_code == 1
    assert "Unknown board" in result.output
    assert "linkedin" in result.output


def test_login_succeeds_when_cookie_appears_on_a_later_poll(tmp_path):
    ws = _workspace(tmp_path)
    fake_launch, _ctx, page = _patch_launch([[], [], [{"name": "li_at", "value": "x"}]])
    with patch("careeros.cli.browser_cmd.launch_browser", fake_launch), \
         patch("careeros.cli.browser_cmd.time.sleep"):
        result = runner.invoke(app, ["browser", "login", "--board", "linkedin", "--workspace", str(ws)])
    assert result.exit_code == 0
    assert "Signed in to linkedin" in result.output
    page.goto.assert_called_once()
    assert "linkedin.com/login" in page.goto.call_args[0][0]


def test_login_is_never_headless(tmp_path):
    ws = _workspace(tmp_path)
    record = {}
    fake_launch, _ctx, _page = _patch_launch([[{"name": "li_at", "value": "x"}]], record)
    with patch("careeros.cli.browser_cmd.launch_browser", fake_launch), \
         patch("careeros.cli.browser_cmd.time.sleep"):
        runner.invoke(app, ["browser", "login", "--board", "linkedin", "--workspace", str(ws)])
    assert record["headless"] is False


def test_login_times_out_when_cookie_never_appears(tmp_path):
    ws = _workspace(tmp_path)
    fake_launch, _ctx, _page = _patch_launch([[]] * 200)
    times = iter([0.0] + [1000.0] * 50)
    with patch("careeros.cli.browser_cmd.launch_browser", fake_launch), \
         patch("careeros.cli.browser_cmd.time.sleep"), \
         patch("careeros.cli.browser_cmd.time.monotonic", lambda: next(times)):
        result = runner.invoke(app, ["browser", "login", "--board", "linkedin", "--workspace", str(ws)])
    assert result.exit_code == 1
    assert "Timed out" in result.output


def test_login_logs_board_session_authorized(tmp_path):
    ws = _workspace(tmp_path)
    fake_launch, _ctx, _page = _patch_launch([[{"name": "li_at", "value": "x"}]])
    with patch("careeros.cli.browser_cmd.launch_browser", fake_launch), \
         patch("careeros.cli.browser_cmd.time.sleep"):
        runner.invoke(app, ["browser", "login", "--board", "linkedin", "--workspace", str(ws)])

    logs = sorted((ws / "activity").glob("*.jsonl"))
    events = [json.loads(line) for line in logs[-1].read_text().strip().split("\n") if line]
    authorized = [e for e in events if e["event_type"] == "board_session_authorized"]
    assert len(authorized) == 1
    assert authorized[0]["entity_id"] == "linkedin"
    assert authorized[0]["status"] == "success"


def test_login_requires_a_workspace(tmp_path, monkeypatch):
    monkeypatch.setattr("careeros.config.CONFIG_PATH", tmp_path / "missing.json")
    result = runner.invoke(app, ["browser", "login", "--board", "linkedin"])
    assert result.exit_code == 1
    assert "No workspace configured" in result.output


def test_status_lists_authorized_and_unauthorized_boards():
    with patch(
        "careeros.cli.browser_cmd.check_board_sessions",
        return_value={"linkedin": True, "indeed": False, "wellfound": False},
    ):
        result = runner.invoke(app, ["browser", "status"])
    assert result.exit_code == 0
    assert "linkedin" in result.output
    assert "indeed" in result.output


def test_status_needs_no_workspace(tmp_path, monkeypatch):
    monkeypatch.setattr("careeros.config.CONFIG_PATH", tmp_path / "missing.json")
    with patch(
        "careeros.cli.browser_cmd.check_board_sessions",
        return_value={"linkedin": False, "indeed": False, "wellfound": False},
    ):
        result = runner.invoke(app, ["browser", "status"])
    assert result.exit_code == 0
    assert "No workspace configured" not in result.output
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_browser_cmd.py -v`
Expected: FAIL — `browser` is not a registered command.

- [ ] **Step 3: Create `careeros/cli/browser_cmd.py`**

```python
from __future__ import annotations

import time

import typer
from rich import print as rprint
from rich.console import Console
from rich.table import Table

from careeros.browser.boards import BOARDS
from careeros.browser.driver import get_careeros_profile_path, launch_browser
from careeros.browser.session import check_board_sessions
from careeros.config import GlobalConfig
from careeros.runtime.factory import open_local_runtime
from careeros.storage.filesystem import LocalFilesystemStorage

browser_app = typer.Typer(name="browser", help="Manage the isolated CareerOS browser profile.")
console = Console()

_LOGIN_TIMEOUT_SECONDS = 300
_POLL_INTERVAL_SECONDS = 2


def _get_storage(workspace_path: str | None) -> LocalFilesystemStorage:
    if workspace_path:
        return LocalFilesystemStorage(workspace_path)
    config = GlobalConfig.load()
    if not config.workspace_path:
        rprint("[red]No workspace configured. Run 'careeros onboard' first.[/red]")
        raise typer.Exit(1)
    return LocalFilesystemStorage(config.workspace_path)


@browser_app.command()
def login(
    board: str = typer.Option(..., "--board", help="Board to sign in to"),
    workspace: str = typer.Option(None, "--workspace", help="Workspace path"),
) -> None:
    """Sign in to a job board in the isolated CareerOS browser profile."""
    board_obj = BOARDS.get(board)
    if board_obj is None:
        rprint("[red]Unknown board '" + board + "'. Valid: " + ", ".join(BOARDS) + "[/red]")
        raise typer.Exit(1)

    # A workspace is required here because this command writes an audit record.
    try:
        runtime = open_local_runtime(_get_storage(workspace))
    except FileNotFoundError:
        rprint("[red]No workspace configured. Run 'careeros onboard' first.[/red]")
        raise typer.Exit(1)

    rprint("Opening " + board_obj.login_url)
    rprint("[yellow]Sign in in the browser window. CareerOS detects it and closes automatically.[/yellow]")

    authorized = False
    with launch_browser(headless=False) as (context, page):
        page.goto(board_obj.login_url, timeout=30000)
        deadline = time.monotonic() + _LOGIN_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            cookies = context.cookies(board_obj.cookie_domain)
            if any(c.get("name") == board_obj.session_cookie for c in cookies):
                authorized = True
                break
            time.sleep(_POLL_INTERVAL_SECONDS)

    if not authorized:
        rprint(
            "[red]Timed out — the " + board_obj.session_cookie
            + " session cookie never appeared. Not signed in.[/red]"
        )
        raise typer.Exit(1)

    runtime.record_activity(runtime.new_event(
        "board_session_authorized", "browser-login",
        "Authorized browser session for " + board,
        entity_type="board", entity_id=board,
    ))
    rprint("[green]Signed in to " + board + ".[/green]")


@browser_app.command()
def status() -> None:
    """Show which boards have an authorized session. Needs no workspace."""
    names = list(BOARDS)
    sessions = check_board_sessions(names)

    table = Table(show_header=True)
    table.add_column("Board")
    table.add_column("Authorized")
    for name in names:
        table.add_row(name, "[green]yes[/green]" if sessions.get(name) else "[red]no[/red]")
    console.print(table)
    rprint("Profile: " + get_careeros_profile_path())
```

- [ ] **Step 4: Register the app in `careeros/cli/main.py`**

Add the import beside the others:

```python
from careeros.cli.browser_cmd import browser_app
```

and the registration after the `browse` line:

```python
app.add_typer(browser_app, name="browser")
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_browser_cmd.py -v`
Expected: 8 PASS

- [ ] **Step 6: Run the full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: 384 passed, 6 skipped

- [ ] **Step 7: Commit**

```bash
git add careeros/cli/browser_cmd.py tests/test_browser_cmd.py careeros/cli/main.py
git commit -m "feat: add 'careeros browser login' and 'careeros browser status' (Phase 9c)"
```

- [ ] **Step 8: Verify the Indeed and Wellfound cookie names (manual, one-time)**

This is the empirical step the spec defers to. It needs a real browser and real accounts, so it is not automatable and is not part of the test suite.

For each of `indeed` and `wellfound`:

```bash
careeros browser login --board indeed
```

If it reports success, the recorded `session_cookie` is correct. If it times out despite a completed sign-in, the cookie name in `careeros/browser/boards.py` is wrong. Find the real one by opening the profile and dumping cookies:

```bash
.venv/bin/python -c "
from careeros.browser.driver import launch_browser
with launch_browser(headless=True) as (ctx, _):
    for c in ctx.cookies('https://www.indeed.com'):
        print(c['name'], '|', str(c.get('expires')))
"
```

Pick the cookie that appears only after authentication and carries a long expiry, correct the constant in `boards.py`, and re-run `careeros browser login --board indeed` to confirm. Commit any correction as `fix: correct <board> session cookie name (Phase 9c)`. If both were already right, there is nothing to commit.

---

### Task 5: Pre-flight in `discover-and-apply`, and remove the opt-in gate

**Files:**
- Modify: `careeros/cli/discover_and_apply_cmd.py:29-42,66-77,98-99`, and the final summary `rprint`
- Modify: `tests/test_discover_and_apply_cmd.py`

**Interfaces:**
- Consumes: `check_board_sessions` (Task 3).
- Produces: nothing new.

Two changes in one task because they touch the same command entry point and a reviewer would sensibly judge them together: unattended runs now skip unauthorized boards, and the gate that made the command opt-in comes down.

- [ ] **Step 1: Write the failing tests**

Add these to the existing `TestDiscoverAndApplyCmd` class in
`tests/test_discover_and_apply_cmd.py`. The file already provides module-level
`_setup_workspace(tmp_path, policy=None, with_resume=True)` (which returns the workspace
path as a string), `_mock_launch(mock_page=None)`, and `_posting(...)`, and invokes the
sub-app `discover_and_apply_app` rather than the root `app`. Reuse all of that; do not
introduce parallel helpers.

First add a counting variant beside the existing `_mock_launch`, since two of these tests
need to know how many times a browser was opened:

```python
def _mock_launch_counting(counter: list, mock_page=None):
    """Same as _mock_launch, but appends to `counter` on each context entry."""
    if mock_page is None:
        mock_page = MagicMock()

    @contextmanager
    def _ctx(headless=False) -> Iterator:
        counter.append(headless)
        yield MagicMock(), mock_page

    return _ctx
```

Then the tests:

```python
    def test_runs_without_the_gate_flag(self, tmp_path):
        # The gate is gone: neither --i-accept-the-risk nor the env var is needed.
        policy = AutomationPolicy(
            auto_apply_min_score=90, max_auto_applies_per_run=5, boards=["linkedin"]
        )
        ws_path = _setup_workspace(tmp_path, policy=policy)
        with patch("careeros.cli.discover_and_apply_cmd.check_board_sessions",
                   return_value={"linkedin": True}), \
             patch("careeros.cli.discover_and_apply_cmd.launch_browser", _mock_launch()), \
             patch("careeros.cli.discover_and_apply_cmd.score_job",
                   return_value={"score": 10, "reasoning": "meh"}):
            result = runner.invoke(discover_and_apply_app, ["--workspace", ws_path])
        assert result.exit_code == 0
        assert "i-accept-the-risk" not in result.output
        assert "unsafe automation" not in result.output.lower()

    def test_removed_gate_flag_is_rejected(self, tmp_path):
        policy = AutomationPolicy(
            auto_apply_min_score=90, max_auto_applies_per_run=5, boards=["linkedin"]
        )
        ws_path = _setup_workspace(tmp_path, policy=policy)
        result = runner.invoke(
            discover_and_apply_app, ["--workspace", ws_path, "--i-accept-the-risk"]
        )
        assert result.exit_code != 0

    def test_unauthorized_board_is_skipped_and_logged(self, tmp_path):
        policy = AutomationPolicy(
            auto_apply_min_score=90, max_auto_applies_per_run=5,
            boards=["linkedin", "indeed"],
        )
        ws_path = _setup_workspace(tmp_path, policy=policy)
        with patch("careeros.cli.discover_and_apply_cmd.check_board_sessions",
                   return_value={"linkedin": False, "indeed": True}), \
             patch("careeros.cli.discover_and_apply_cmd.launch_browser", _mock_launch()), \
             patch("careeros.cli.discover_and_apply_cmd.score_job",
                   return_value={"score": 10, "reasoning": "meh"}):
            result = runner.invoke(discover_and_apply_app, ["--workspace", ws_path])

        assert result.exit_code == 0
        assert "Unauthorized boards: linkedin" in result.output

        logs = sorted((tmp_path / "activity").glob("*.jsonl"))
        events = [json.loads(line) for line in logs[-1].read_text().strip().split("\n") if line]
        unauth = [e for e in events if e["event_type"] == "session_unauthorized"]
        assert len(unauth) == 1
        assert unauth[0]["entity_id"] == "linkedin"
        assert unauth[0]["status"] == "failed"

    def test_authorized_boards_still_run_when_another_is_unauthorized(self, tmp_path):
        policy = AutomationPolicy(
            auto_apply_min_score=90, max_auto_applies_per_run=5,
            boards=["linkedin", "indeed"],
        )
        ws_path = _setup_workspace(tmp_path, policy=policy)
        launches: list = []

        with patch("careeros.cli.discover_and_apply_cmd.check_board_sessions",
                   return_value={"linkedin": False, "indeed": True}), \
             patch("careeros.cli.discover_and_apply_cmd.launch_browser",
                   _mock_launch_counting(launches)), \
             patch("careeros.cli.discover_and_apply_cmd.score_job",
                   return_value={"score": 10, "reasoning": "meh"}):
            runner.invoke(discover_and_apply_app, ["--workspace", ws_path])

        # One board authorized -> exactly one discovery browser launch, headless.
        assert launches == [True]

    def test_all_boards_unauthorized_exits_non_zero(self, tmp_path):
        policy = AutomationPolicy(
            auto_apply_min_score=90, max_auto_applies_per_run=5,
            boards=["linkedin", "indeed"],
        )
        ws_path = _setup_workspace(tmp_path, policy=policy)
        launches: list = []
        with patch("careeros.cli.discover_and_apply_cmd.check_board_sessions",
                   return_value={"linkedin": False, "indeed": False}), \
             patch("careeros.cli.discover_and_apply_cmd.launch_browser",
                   _mock_launch_counting(launches)):
            result = runner.invoke(discover_and_apply_app, ["--workspace", ws_path])
        assert result.exit_code == 1
        assert "careeros browser login" in result.output
        assert launches == []
```

Add `import json` to the file's imports if it is not already present.

- [ ] **Step 2: Delete the obsolete gate tests**

In `tests/test_discover_and_apply_cmd.py`, delete every test asserting the gate blocks — the ones invoking without `--i-accept-the-risk` and expecting exit 1, and the ones setting `CAREEROS_ALLOW_UNSAFE_AUTOMATION`. Delete them; do not mark them skipped. Every remaining test that passes `--i-accept-the-risk` must have that argument removed, or it will now fail on an unknown option.

- [ ] **Step 3: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_discover_and_apply_cmd.py -v`
Expected: the new tests FAIL (`check_board_sessions` is not imported in that module; `--i-accept-the-risk` still exists).

- [ ] **Step 4: Remove the gate**

In `careeros/cli/discover_and_apply_cmd.py`, delete `_GATE_ENV_VAR` and the whole `_GATE_WARNING` string (lines 29-42), and remove the now-unused `import os` if nothing else in the file uses it.

Delete the `i_accept_the_risk` parameter from the signature and both gate lines from the body, so the command opens:

```python
@discover_and_apply_app.command()
def discover_and_apply_cmd(
    board: str = typer.Option(None, "--board", help="Single board to run (overrides policy's board list)"),
    workspace: str = typer.Option(None, "--workspace", help="Workspace path"),
) -> None:
    try:
        runtime = open_automation_runtime(_get_storage(workspace))
```

- [ ] **Step 5: Add the pre-flight**

Add the import:

```python
from careeros.browser.session import check_board_sessions
```

Replace line 98 (`boards = [board] if board else policy.boards`) with:

```python
    boards = [board] if board else policy.boards
    sessions = check_board_sessions(boards)
    unauthorized = [b for b in boards if not sessions.get(b)]
    for b in unauthorized:
        runtime.record_activity(runtime.new_event(
            "session_unauthorized", "discover-and-apply",
            "No authorized browser session for " + b + " — board skipped",
            status="failed", entity_type="board", entity_id=b,
        ))
    boards = [b for b in boards if sessions.get(b)]
    if not boards:
        rprint(
            "[red]No authorized board sessions. Run: careeros browser login --board <name>[/red]"
        )
        raise typer.Exit(1)
```

- [ ] **Step 6: Add the summary segment**

Replace the final `rprint` with:

```python
    rprint(
        "Discovered: " + str(saved_count) + ", Duplicates: " + str(duplicate_count)
        + ", Blocked: " + str(blocked_count)
        + ", Auto-applied: " + str(applied_count) + ", Skipped: " + str(skipped_count)
        + ", Unauthorized boards: " + (", ".join(unauthorized) if unauthorized else "none")
    )
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_discover_and_apply_cmd.py -v`
Expected: all PASS

- [ ] **Step 8: Run the full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: all pass, 6 skipped. Total drops by however many gate tests were deleted, then rises by 5.

- [ ] **Step 9: Commit**

```bash
git add careeros/cli/discover_and_apply_cmd.py tests/test_discover_and_apply_cmd.py
git commit -m "feat: pre-flight board sessions and lift the discover-and-apply opt-in gate (Phase 9c)"
```

---

### Task 6: Pre-flight in `browse`, `apply`, and `research`, plus the reasoning KeyError fix

**Files:**
- Create: `careeros/cli/preflight.py`
- Modify: `careeros/cli/browse_cmd.py` (pre-flight, line 96 fix, `BrowserProfileBusy` handler)
- Modify: `careeros/cli/apply_cmd.py` (after filler resolution, ~line 130)
- Modify: `careeros/cli/research_cmd.py` (`company` and `people` only, plus `BrowserProfileBusy` handlers)
- Modify: `careeros/cli/discover_and_apply_cmd.py` (`BrowserProfileBusy` handlers only — Task 5 owns its pre-flight)
- Modify: `tests/test_browse_cmd.py`, `tests/test_apply_cmd.py`, `tests/test_research_cmd.py`

**Interfaces:**
- Consumes: `check_board_sessions` (Task 3).
- Produces: `require_board_session(board: str) -> None` — exits non-zero with an actionable message when unauthorized, returns `None` otherwise.

The pre-flight is per-board, never blanket: `--board url`, Greenhouse/Lever applies, and `research compensation` all work logged-out today and must keep working.

- [ ] **Step 1: Write the failing tests**

`check_board_sessions` is always patched at **`careeros.cli.preflight`** — that is where
`require_board_session` imported it, so patching it anywhere else has no effect. Each file
invokes its own sub-app (`browse_app`, `apply_app`, `research_app`), not the root `app`.

Add to `tests/test_browse_cmd.py`, using its module-level `_setup_workspace(tmp_path)`,
`_mock_launch()`, `_mock_postings()`:

```python
    def test_unauthorized_board_blocks_before_launching_a_browser(self, tmp_path):
        ws_path = _setup_workspace(tmp_path)
        with patch("careeros.cli.preflight.check_board_sessions",
                   return_value={"linkedin": False}), \
             patch("careeros.cli.browse_cmd.launch_browser") as mock_browser:
            result = runner.invoke(
                browse_app, ["--board", "linkedin", "--workspace", ws_path]
            )
        assert result.exit_code == 1
        assert "careeros browser login --board linkedin" in result.output
        mock_browser.assert_not_called()

    def test_board_url_needs_no_session(self, tmp_path):
        ws_path = _setup_workspace(tmp_path)
        mock_page = MagicMock()
        with patch("careeros.cli.preflight.check_board_sessions") as cbs, \
             patch("careeros.cli.browse_cmd.launch_browser", _mock_launch(mock_page)), \
             patch("careeros.cli.browse_cmd.GenericScraper") as gs, \
             patch("careeros.cli.browse_cmd.fetch_jd_text", return_value="jd"), \
             patch("careeros.cli.browse_cmd.score_job", return_value=_mock_score()), \
             patch("careeros.cli.browse_cmd.Prompt.ask", return_value="q"):
            gs.return_value.search.return_value = _mock_postings()
            result = runner.invoke(
                browse_app,
                ["--board", "url", "--url", "https://x.test/jobs", "--workspace", ws_path],
            )
        assert result.exit_code == 0
        cbs.assert_not_called()

    def test_score_result_without_reasoning_does_not_crash(self, tmp_path):
        # Review finding #2: score_job returns the raw parsed model JSON and
        # coerces only "score", so a model omitting "reasoning" aborted the
        # entire run with KeyError after all page loads and LLM spend.
        ws_path = _setup_workspace(tmp_path)
        mock_page = MagicMock()
        with patch("careeros.cli.preflight.check_board_sessions",
                   return_value={"linkedin": True}), \
             patch("careeros.cli.browse_cmd.launch_browser", _mock_launch(mock_page)), \
             patch("careeros.cli.browse_cmd.SCRAPERS") as scrapers, \
             patch("careeros.cli.browse_cmd.fetch_jd_text", return_value="jd"), \
             patch("careeros.cli.browse_cmd.score_job", return_value={"score": 85}), \
             patch("careeros.cli.browse_cmd.Prompt.ask", return_value="q"):
            scrapers.__getitem__.return_value.search.return_value = _mock_postings()
            result = runner.invoke(
                browse_app, ["--board", "linkedin", "--workspace", ws_path]
            )
        assert result.exit_code == 0
        assert "KeyError" not in result.output
```

Add to `tests/test_apply_cmd.py`. This file mocks storage and the runtime wholesale via
`_mock_runtime(tmp_path)` rather than building a real workspace, and patches
`apply_cmd.FILLERS`. The pre-flight uses `isinstance(filler, LinkedInFiller)`, so these
tests must patch `FILLERS` with **real filler instances**, not `MagicMock`s:

```python
    def test_linkedin_apply_requires_a_linkedin_session(self, tmp_path):
        from careeros.browser.fillers.linkedin import LinkedInFiller

        runtime = _mock_runtime(tmp_path)
        job = _make_job(url="https://www.linkedin.com/jobs/view/1")
        with patch("careeros.cli.apply_cmd._get_storage", return_value=MagicMock()), \
             patch("careeros.cli.apply_cmd.open_local_runtime", return_value=runtime), \
             patch("careeros.cli.apply_cmd.Job.load", return_value=job), \
             patch("careeros.cli.apply_cmd.Profile.load_or_empty", return_value=_make_profile()), \
             patch("careeros.cli.apply_cmd.Skills.load_or_empty", return_value=Skills()), \
             patch("careeros.cli.apply_cmd.Goals.load_or_empty", return_value=Goals()), \
             patch("careeros.cli.apply_cmd.generate_cover_letter", return_value="Dear team"), \
             patch("careeros.cli.apply_cmd.FILLERS", [LinkedInFiller()]), \
             patch("careeros.cli.preflight.check_board_sessions",
                   return_value={"linkedin": False}), \
             patch("careeros.cli.apply_cmd.launch_browser") as mock_browser, \
             patch("careeros.cli.apply_cmd.Prompt.ask", side_effect=["a"]):
            result = runner.invoke(apply_app, ["acme-sre-abc1"])

        assert result.exit_code == 1
        assert "careeros browser login --board linkedin" in result.output
        mock_browser.assert_not_called()

    def test_greenhouse_apply_needs_no_session(self, tmp_path):
        from careeros.browser.fillers.greenhouse import GreenhouseFiller

        runtime = _mock_runtime(tmp_path)
        job = _make_job(url="https://boards.greenhouse.io/acme/jobs/1")
        with patch("careeros.cli.apply_cmd._get_storage", return_value=MagicMock()), \
             patch("careeros.cli.apply_cmd.open_local_runtime", return_value=runtime), \
             patch("careeros.cli.apply_cmd.Job.load", return_value=job), \
             patch("careeros.cli.apply_cmd.Profile.load_or_empty", return_value=_make_profile()), \
             patch("careeros.cli.apply_cmd.Skills.load_or_empty", return_value=Skills()), \
             patch("careeros.cli.apply_cmd.Goals.load_or_empty", return_value=Goals()), \
             patch("careeros.cli.apply_cmd.generate_cover_letter", return_value="Dear team"), \
             patch("careeros.cli.apply_cmd.FILLERS", [GreenhouseFiller()]), \
             patch("careeros.cli.preflight.check_board_sessions") as cbs, \
             patch("careeros.cli.apply_cmd.launch_browser") as mock_browser, \
             patch("careeros.cli.apply_cmd.Prompt.ask", side_effect=["a"]):
            mock_browser.return_value.__enter__ = MagicMock(
                return_value=(MagicMock(), MagicMock())
            )
            mock_browser.return_value.__exit__ = MagicMock(return_value=False)
            runner.invoke(apply_app, ["acme-sre-abc1"])

        cbs.assert_not_called()
        mock_browser.assert_called()
```

Add to `tests/test_research_cmd.py`, using its `_setup_workspace(tmp_path, with_job=True)`
and the existing job id `acme-sre-abc1`:

```python
    def test_research_people_requires_a_linkedin_session(self, tmp_path):
        ws_path = _setup_workspace(tmp_path)
        with patch("careeros.cli.preflight.check_board_sessions",
                   return_value={"linkedin": False}), \
             patch("careeros.cli.research_cmd.launch_browser") as mock_browser:
            result = runner.invoke(
                research_app, ["people", "--job", "acme-sre-abc1", "--workspace", ws_path]
            )
        assert result.exit_code == 1
        assert "careeros browser login --board linkedin" in result.output
        mock_browser.assert_not_called()

    def test_research_company_requires_a_linkedin_session(self, tmp_path):
        ws_path = _setup_workspace(tmp_path)
        with patch("careeros.cli.preflight.check_board_sessions",
                   return_value={"linkedin": False}), \
             patch("careeros.cli.research_cmd.launch_browser") as mock_browser:
            result = runner.invoke(
                research_app, ["company", "--job", "acme-sre-abc1", "--workspace", ws_path]
            )
        assert result.exit_code == 1
        mock_browser.assert_not_called()

    def test_research_compensation_needs_no_session(self, tmp_path):
        ws_path = _setup_workspace(tmp_path)
        with patch("careeros.cli.preflight.check_board_sessions") as cbs, \
             patch("careeros.cli.research_cmd.launch_browser") as mock_browser, \
             patch("careeros.cli.research_cmd.fetch_jd_text", return_value="page"), \
             patch("careeros.cli.research_cmd.extract_compensation_data",
                   return_value={"base_min": None, "base_max": None, "bonus": None,
                                 "equity": None, "confidence": "low"}):
            mock_browser.return_value.__enter__ = MagicMock(
                return_value=(MagicMock(), MagicMock())
            )
            mock_browser.return_value.__exit__ = MagicMock(return_value=False)
            result = runner.invoke(
                research_app,
                ["compensation", "--job", "acme-sre-abc1", "--workspace", ws_path],
            )
        assert result.exit_code == 0
        cbs.assert_not_called()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_browse_cmd.py tests/test_apply_cmd.py tests/test_research_cmd.py -v`
Expected: FAIL — `careeros.cli.preflight` does not exist.

- [ ] **Step 3: Create `careeros/cli/preflight.py`**

```python
from __future__ import annotations

import typer
from rich import print as rprint

from careeros.browser.session import check_board_sessions


def require_board_session(board: str) -> None:
    """Exit non-zero with an actionable message if `board` has no authorized session.

    Called before launching a browser, never after: opening a browser window
    onto a login wall is worse than a one-line instruction.
    """
    if check_board_sessions([board]).get(board):
        return
    rprint(
        "[red]Not signed in to " + board
        + ". Run: careeros browser login --board " + board + "[/red]"
    )
    raise typer.Exit(1)
```

- [ ] **Step 4: Wire `browse_cmd` and fix the reasoning KeyError**

Add the import:

```python
from careeros.cli.preflight import require_board_session
```

Immediately after the `board == "url" and not url` check (around line 63), add:

```python
    if board != "url":
        require_board_session(board)
```

Then fix line 96:

```python
                scored.append({**posting, "score": result["score"], "reasoning": result["reasoning"]})
```

becomes:

```python
                scored.append({
                    **posting,
                    "score": result["score"],
                    # score_job returns the raw parsed model JSON and coerces only
                    # "score"; a model that omits "reasoning" must not abort the run.
                    "reasoning": result.get("reasoning", ""),
                })
```

- [ ] **Step 5: Wire `apply_cmd`**

Add the import:

```python
from careeros.cli.preflight import require_board_session
```

Immediately after the `filler is None` check (around line 132), add:

```python
    # Only LinkedIn Easy Apply needs a session; Greenhouse, Lever, and the
    # generic fallback all work signed-out.
    if isinstance(filler, LinkedInFiller):
        require_board_session("linkedin")
```

`LinkedInFiller` is already imported at line 15.

- [ ] **Step 6: Wire `research_cmd`**

Add the import:

```python
from careeros.cli.preflight import require_board_session
```

In `company()`, immediately before the `try:` that calls `launch_browser` (around line 58), add:

```python
    require_board_session("linkedin")
```

In `people()`, immediately before its `try:` that calls `launch_browser` (around line 100), add the same line.

Do **not** add it to `compensation()` — levels.fyi is public.

- [ ] **Step 7: Surface `BrowserProfileBusy` as a plain message, not a traceback**

Task 2 raises `BrowserProfileBusy`, but no CLI catches it — `browse_cmd`,
`discover_and_apply_cmd`, and `research_cmd` catch only `ImportError`, so a locked profile
would still print a raw traceback. The spec's error-handling table requires otherwise.

First add the test to `tests/test_browse_cmd.py`:

```python
    def test_locked_profile_reports_a_plain_message(self, tmp_path):
        from careeros.browser.driver import BrowserProfileBusy

        ws_path = _setup_workspace(tmp_path)
        with patch("careeros.cli.preflight.check_board_sessions",
                   return_value={"linkedin": True}), \
             patch("careeros.cli.browse_cmd.launch_browser",
                   side_effect=BrowserProfileBusy("already in use by another CareerOS process")):
            result = runner.invoke(
                browse_app, ["--board", "linkedin", "--workspace", ws_path]
            )
        assert result.exit_code == 1
        assert "already in use" in result.output
        assert "Traceback" not in result.output
```

Then, in each of `careeros/cli/browse_cmd.py`, `careeros/cli/discover_and_apply_cmd.py`,
and `careeros/cli/research_cmd.py`, import the exception:

```python
from careeros.browser.driver import BrowserProfileBusy
```

and add a handler beside every existing `except ImportError:` that wraps a
`launch_browser` call — three sites in `research_cmd`, one in `browse_cmd`, two in
`discover_and_apply_cmd`:

```python
    except BrowserProfileBusy as exc:
        rprint("[red]" + str(exc) + "[/red]")
        raise typer.Exit(1)
```

`apply_cmd` already has a broad `except Exception as exc` after its `except ImportError`
that prints `"Browser error: " + str(exc)`, so it needs no change — but place the
`BrowserProfileBusy` clause *before* that broad clause if you add one there, since
exception clauses are evaluated in order.

- [ ] **Step 8: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_browse_cmd.py tests/test_apply_cmd.py tests/test_research_cmd.py -v`
Expected: all PASS. If existing tests in these files now fail because they launch a browser without a session, add `patch("careeros.cli.preflight.check_board_sessions", return_value={"linkedin": True, "indeed": True, "wellfound": True})` to their patch stacks — do not weaken the pre-flight to accommodate them.

- [ ] **Step 9: Run the full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: all pass, 6 skipped

- [ ] **Step 10: Commit**

```bash
git add careeros/cli/preflight.py careeros/cli/browse_cmd.py careeros/cli/apply_cmd.py careeros/cli/research_cmd.py careeros/cli/discover_and_apply_cmd.py tests/test_browse_cmd.py tests/test_apply_cmd.py tests/test_research_cmd.py
git commit -m "feat: per-board session pre-flight for browse/apply/research; fix browse reasoning KeyError (Phase 9c)"
```

---

### Task 7: Documentation

**Files:**
- Modify: `README.md`
- Modify: `docs/getting-started.md`
- Modify: `ROADMAP.md`

**Interfaces:**
- Consumes: the finished commands from Tasks 4-6.
- Produces: nothing code-facing.

Docs get their own task because they span the whole phase rather than any one deliverable, and because two of these edits are corrections to claims that are now false.

- [ ] **Step 1: Correct the Playwright requirement in `README.md`**

The Requirements bullet currently says the browser commands "drive your own logged-in browser session rather than an API, so there's no separate account to connect." That is now false. Replace with:

```markdown
- [Playwright](https://playwright.dev/) with Chrome, for `browse`, `apply`, `discover-and-apply`,
  and `research`. These drive a **dedicated CareerOS browser profile**, not your everyday Chrome
  profile — so an unattended run never holds your banking or email sessions. Sign in to each board
  once with `careeros browser login --board <name>`.
  `pip install playwright && playwright install chrome`.
```

- [ ] **Step 2: Add the `browser` commands to the README command reference**

Under the **Job discovery + scoring** heading, above the `browse` entry:

```markdown
- **`careeros browser login --board <linkedin|indeed|wellfound>`** — sign in to a job board in
  the isolated CareerOS profile. Opens a real browser window; CareerOS detects the completed
  sign-in and closes it. Your credentials are never seen or stored by CareerOS.
- **`careeros browser status`** — which boards are currently authorized, and where the profile lives.
```

- [ ] **Step 3: Drop the gate language from the README**

Remove any text describing `discover-and-apply` as opt-in, gated, or requiring `--i-accept-the-risk`. The command is now first-class.

- [ ] **Step 4: Add the login step to `docs/getting-started.md`**

Insert a step before the first `careeros browse` example:

```markdown
### Sign in to your job boards

CareerOS drives a dedicated browser profile, separate from your everyday Chrome, so a
scheduled run never holds sessions for anything but the boards you authorized. Sign in once
per board:

```bash
careeros browser login --board linkedin
```

A browser window opens at the board's login page. Sign in as normal — CareerOS detects the
session and closes the window. Check what's authorized any time with `careeros browser status`.
Re-run `login` whenever a session expires.
```

- [ ] **Step 5: Update `ROADMAP.md` Phase 9**

Mark Phase 9 complete, note the gate is lifted, and record what Phase 10 inherits. Replace the Phase 9 **Exit condition** paragraph with:

```markdown
**Status: shipped.** All three parts landed (9a policy engine, 9b content sanitization,
9c browser isolation) and the `discover-and-apply` opt-in gate has been removed.

**Inherited by Phase 10:** deduplication is a stopgap matching exact case-insensitive
`(company, title)`. A posting re-listed under a variant title ("Senior SRE" vs "Senior Site
Reliability Engineer") still creates a second record and can be applied to twice. Phase 10's
canonical-URL fingerprinting closes this.
```

- [ ] **Step 6: Verify the docs match reality**

Run: `grep -rn "i-accept-the-risk\|ALLOW_UNSAFE_AUTOMATION\|your own logged-in browser" README.md docs/ ROADMAP.md`
Expected: no matches outside `docs/superpowers/` (the specs and plans are historical records and stay as written).

- [ ] **Step 7: Run the full suite one final time**

Run: `.venv/bin/python -m pytest -q`
Expected: all pass, 6 skipped

- [ ] **Step 8: Commit**

```bash
git add README.md docs/getting-started.md ROADMAP.md
git commit -m "docs: document browser login, correct the Playwright claim, mark Phase 9 shipped (Phase 9c)"
```
