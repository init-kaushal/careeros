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
