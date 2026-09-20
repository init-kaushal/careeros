# Phase 9c — Browser Profile Isolation Design

## Why

`careeros/browser/driver.py` launches Playwright against the user's live Chrome
profile:

```python
user_data_dir=get_chrome_profile_path()   # ~/Library/Application Support/Google/Chrome
```

Every `browse`, `apply`, `research`, and `discover-and-apply` therefore drives a browser
holding every cookie and saved credential the user owns — not just job-board sessions.
The 2026-09-20 external review filed this as S2 (Critical): an unattended, cron-driven,
headless browser loads attacker-controlled pages while authenticated to the user's bank,
email, and everything else. Playwright also writes into that profile, risking corruption,
and `launch_persistent_context` fails outright whenever Chrome is already running.

9a closed the policy gap and 9b closed the prompt-injection gap. Browser isolation is the
third and last part of Phase 9.

## Scope

**In:**

- A dedicated, machine-global CareerOS Chrome profile, replacing the live one
- `careeros browser login --board <name>` — interactive, user-driven authorization
- `careeros browser status` — which boards are authorized
- A board registry consolidating the board list that currently exists in four places
- Per-board session pre-flight in `discover-and-apply`, `browse`, `apply`, and `research`

**Out (deferred, not forgotten):**

- Cookie import from the user's live Chrome profile. Rejected: it requires decrypting
  Chrome's cookie store (a different scheme per OS, a Keychain prompt on macOS, and
  version-fragile), and it reaches into the exact profile this phase exists to isolate
  from. The interactive login costs one manual step and keeps credentials out of
  CareerOS entirely.
- `careeros browser logout`. Deleting the profile directory is already the answer; a
  command implies per-board cookie surgery nothing needs.
- Any credential storage or automatic re-login. CareerOS never sees a password.
- Live probe pre-flight (navigate to an authenticated URL, detect a login redirect).
  Strictly more truthful than a cookie check — it catches server-side revocation — but it
  needs a probe URL and a logged-in selector per board, which puts session correctness
  back on the markup treadmill that Phase 10 exists to get off. Revisit only if silent
  revocation proves common in practice.
- Lifting the `discover-and-apply` opt-in gate. See **Gate** below.

**Not in the workspace.** The profile lives outside it. `careeros export` rglobs the
whole workspace into an unencrypted zip (`portability.py:30`), so a profile inside it
would put live session cookies into a portable archive — a new hole that would undercut
this phase. The master design spec (§3) also requires the workspace to hold only data
"readable without CareerOS (plain JSON/Markdown)"; a Chrome profile is neither.

**Machine-global, not per-workspace.** A person has one LinkedIn account, not one per
workspace. Sharing the profile keeps the login ceremony at N boards rather than
N boards × M workspaces, and it keeps `careeros/browser/` free of any dependency on the
workspace manifest.

## Mechanism

### Board registry

The browser-board list exists in four places today, and this phase would otherwise add a
fifth:

| Location | Form |
|---|---|
| `core/models.py:135` | `_VALID_AUTOMATION_BOARDS = ("linkedin", "indeed", "wellfound")` |
| `cli/browse_cmd.py:27` | `SCRAPERS` dict |
| `cli/browse_cmd.py:57` | `valid_boards` set (adds `"url"`) |
| `cli/discover_and_apply_cmd.py:27` | `SCRAPERS` dict, duplicate |
| *this phase* | board → session-cookie map |

Five copies is how a board ends up scrapeable but unauthorizable, or authorized but not
scrapeable — a silent skip with no error. New module `careeros/browser/boards.py` becomes
the single source:

```python
@dataclass(frozen=True)
class Board:
    name: str              # "linkedin"
    scraper: Scraper
    login_url: str         # where `browser login` sends the user
    session_cookie: str    # "li_at" — presence proves authorization
    cookie_domain: str     # "https://www.linkedin.com"

BOARDS: dict[str, Board] = {...}   # linkedin, indeed, wellfound
```

**Determining `session_cookie` values.** LinkedIn's is `li_at` (long-standing and
well-known). Indeed's and Wellfound's are not something this design can assert offline,
and a guessed cookie name would produce a pre-flight that is confidently wrong — the
failure mode Approach 3 was rejected for. They are resolved during implementation by a
defined procedure: run `careeros browser login --board <name>` against each board once,
inspect `context.cookies()` for the cookie that appears on authentication and persists,
and record it in the registry. The registry is the single place any later correction
lands, which is the point of consolidating it.

Derivations, all one-way:

- `SCRAPERS` in both CLI modules becomes a lookup into `BOARDS`
- `_VALID_AUTOMATION_BOARDS` becomes `tuple(BOARDS)`
- `browse`'s `valid_boards` becomes `set(BOARDS) | {"url"}`
- `session.py` reads `session_cookie` / `cookie_domain` / `login_url` off the same record

`boards.py` performs no I/O and imports no Playwright. It holds the `Scraper` instances
already constructed at those call sites today.

Dependency direction is strictly one-way: `cli → session → boards → scrapers`, with
`driver` at the bottom. Nothing under `careeros/browser/` learns about the workspace or
the manifest.

### Profile location and launch

`driver.py` gains a path function mirroring the three-branch platform switch of the
function it replaces:

```python
def get_careeros_profile_path() -> str:
    system = platform.system()
    if system == "Darwin":
        return os.path.expanduser("~/Library/Application Support/careeros/browser")
    if system == "Linux":
        base = os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state")
        return os.path.join(base, "careeros", "browser")
    return os.path.expandvars(r"%LOCALAPPDATA%\careeros\browser")
```

Session data is state, not config — hence `XDG_STATE_HOME` on Linux.

`launch_browser` repoints `user_data_dir` at it and keeps `channel="chrome"`: this phase
changes which profile Chrome opens, not which browser, so existing
`playwright install chrome` instructions stay valid. Before launching, the directory is
created with `mkdir(parents=True, exist_ok=True)` and `chmod(0o700)` — 9b moved workspace
writes to `0o600`, and a directory of live session cookies should not be more permissive
than the JSON beside it.

`get_chrome_profile_path()` is **deleted**, along with its three tests in
`tests/test_browser_driver.py`. Leaving it in place would leave a loaded gun in the file
whose purpose this phase is to remove.

All seven existing `launch_browser` call sites are unchanged. They already funnel through
one function, which is what makes this phase tractable.

### Session check

New module `careeros/browser/session.py`:

```python
def check_board_sessions(names: list[str]) -> dict[str, bool]: ...
```

Opens the isolated profile once, calls `context.cookies(board.cookie_domain)` per
requested board, and reports whether `board.session_cookie` is present. No navigation, no
markup matching. Browsers prune expired cookies, so presence is a reasonable proxy for
"not stale."

Known limitation, accepted: presence is not validity. A server-side-revoked session still
shows a cookie. That case degrades to "board returns zero listings," which existing code
already handles — the failure mode is a wasted board, not a wrong action.

**`check_board_sessions` must fully close its browser context before returning.** Every
caller opens the profile again immediately afterwards — the pre-flight runs, then
`launch_browser` runs — and `launch_persistent_context` holds an exclusive lock on the
profile directory. A context left open would make the pre-flight deadlock against the very
run it is clearing, surfacing as `BrowserProfileBusy` on every invocation. The function
owns its context in a `try/finally` and never yields it to callers.

### `careeros browser` commands

New module `careeros/cli/browser_cmd.py`, registered in `cli/main.py` as `browser`.

**`careeros browser login --board <name>`**

1. Resolve `<name>` against `BOARDS`; unknown names error with the valid list.
2. Launch headful — always. Credentials get typed; headless is never valid here.
3. Navigate to `board.login_url`.
4. Poll `context.cookies()` every 2s for up to 300s, waiting for `board.session_cookie`.
   Auto-detection beats "press Enter when done" (the user never switches back to the
   terminal), and 300s accommodates MFA.
5. On success: close the browser, confirm, log a `board_session_authorized` activity
   event.
6. On timeout: exit non-zero stating the session cookie never appeared.

**`careeros browser status`**

Prints each registry board × authorized, plus the resolved profile path.

**Workspace requirement asymmetry.** `login` requires a workspace; `status` does not.
Every other CLI command calls `_get_storage` and exits 1 without one, and `login` follows
that because it writes an audit record — authorizing a board session is a real
authorization decision, and the master spec (§2.7) wants those logged. `status` writes
nothing, and requiring a workspace to answer "am I signed in?" would be a failure mode
with no purpose. The rule: **a workspace is required exactly when writing to the activity
log.**

### Pre-flight matrix

The pre-flight is per-board, never blanket. A blanket check would wrongly block Greenhouse
applies and compensation research, both of which work logged-out today.

| Command | Session required |
|---|---|
| `discover-and-apply` | each board in `policy.boards`; unauthorized ones skipped |
| `browse --board linkedin\|indeed\|wellfound` | that board |
| `browse --board url` | none — `GenericScraper` hits a user-supplied URL |
| `apply` | only when the resolved filler is `LinkedInFiller` |
| `research company` / `research people` | linkedin |
| `research compensation` | none — levels.fyi is public |

`apply` keys off the filler it already resolves at `apply_cmd.py:129` — a lookup on
existing state, not new detection.

For `browse`, `apply`, and `research`, an unauthorized board exits non-zero with
`Not signed in to <board>. Run: careeros browser login --board <board>` **before**
launching a browser. Opening a browser onto a login wall is worse than a one-line
instruction.

For `discover-and-apply`, unauthorized boards are skipped with a `session_unauthorized`
activity event at `status="failed"` — not `"blocked"`, which 9a's PolicyEngine owns; a
stale cookie is not a policy decision. The cron summary line gains a segment:

```
Discovered: N, Auto-applied: M, Skipped: K, Unauthorized boards: linkedin
```

If every configured board is unauthorized, the run exits non-zero rather than reporting a
healthy-looking row of zeros.

### Error handling

| Condition | Behaviour |
|---|---|
| Profile locked by a concurrent run | `launch_browser` catches the Playwright lock error and raises `BrowserProfileBusy` with a plain message, not a raw traceback |
| Board unauthorized (interactive commands) | Exit non-zero with the `browser login` instruction |
| Board unauthorized (`discover-and-apply`) | Skip board, log `session_unauthorized`, name it in the summary |
| All boards unauthorized (`discover-and-apply`) | Exit non-zero |
| `login` timeout | Exit non-zero, session cookie never appeared |
| Unknown `--board` | Registry-driven error listing valid boards |

Lock contention is new only in form: it already broke every command whenever the user
merely had Chrome open. It is now CareerOS-vs-CareerOS and is currently uncaught —
`discover_and_apply_cmd.py:109` catches only `ImportError`.

## Gate

**The `discover-and-apply` opt-in gate stays up.** The ROADMAP says it lifts once all three
Phase 9 parts land, but `_GATE_WARNING` cites two hazards and only one is Phase 9's:

- prompt-injectable score with no policy layer → **fixed** by 9a + 9b
- no deduplication, so a scheduled run re-submits to the same employer every run →
  **Phase 10**

Shipping an ungated unattended command that still re-applies to the same employer on every
cron run would trade a fixed critical finding for a live one. So 9c rewrites
`_GATE_WARNING` to drop the now-false injection and policy language and cite only the
remaining dedup hazard, and updates the ROADMAP's Phase 9 exit condition to say the gate
lifts after Phase 10. `--i-accept-the-risk` and `CAREEROS_ALLOW_UNSAFE_AUTOMATION` are
unchanged.

`config/policies.json` population is 9a's business and is not revisited here.

## Testing

All tests offline; no real browser, no network. Playwright is mocked at the
`launch_browser` / `BrowserContext` seam, matching the existing house pattern where tests
patch at the caller's import site.

- `tests/test_boards.py` — registry completeness: every board has a non-empty
  `login_url`, `session_cookie`, `cookie_domain`, and a scraper whose `source_board`
  matches its key; `tuple(BOARDS)` equals the previous `_VALID_AUTOMATION_BOARDS`, so the
  consolidation is provably behaviour-preserving.
- `tests/test_browser_driver.py` — replace the three deleted `get_chrome_profile_path`
  tests with equivalents for `get_careeros_profile_path` (macOS / Linux / Windows, plus
  `XDG_STATE_HOME` honoured and defaulted); the path is not the live Chrome profile;
  `launch_browser` passes the isolated dir and creates it `0o700`; a Playwright lock error
  surfaces as `BrowserProfileBusy`.
- `tests/test_browser_session.py` — `check_board_sessions` returns `True` when the session
  cookie is present, `False` when absent, `False` for a board whose cookie exists on a
  different domain; multiple boards resolved in one context open; the context is closed
  before returning, including when the cookie read raises — the self-deadlock invariant
  above, asserted on the mock rather than left to review.
- `tests/test_browser_cmd.py` — `login` rejects an unknown board; launches headful (never
  headless); returns success once the cookie appears on a later poll; times out
  non-zero when it never appears; logs `board_session_authorized`; requires a workspace.
  `status` renders authorized and unauthorized boards and requires no workspace.
- `tests/test_browse_cmd.py`, `test_apply_cmd.py`, `test_research_cmd.py` — pre-flight
  blocks before any browser launch when unauthorized; `--board url`, Greenhouse/Lever
  applies, and `research compensation` all proceed without any session.
- `tests/test_discover_and_apply_cmd.py` — an unauthorized board is skipped while
  authorized boards still run; `session_unauthorized` is logged at `status="failed"`; the
  summary names unauthorized boards; all-unauthorized exits non-zero.

Existing tests that patch `launch_browser` at the caller's import site keep working
unchanged. Tests that assert on the live Chrome path are updated, not deleted.

## Migration note

Existing users' first post-upgrade run finds an empty profile and every board
unauthorized. This surfaces as the pre-flight's actionable message rather than a login
wall, a zero-result scrape, or a stack trace. `README.md` and `docs/getting-started.md`
gain a `careeros browser login --board <name>` step ahead of first `browse`, and the
README's Playwright bullet is corrected: CareerOS drives a dedicated profile, not "your
own logged-in browser session."

No workspace migration is needed — nothing in the workspace changes.
