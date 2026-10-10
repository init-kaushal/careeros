import hashlib
import json
import os
import threading
from pathlib import Path

import pytest

from careeros.core import ledger


def _append(root: Path, n: int = 1, **overrides: object) -> list[dict]:
    out = []
    for i in range(n):
        spec = {"type": "note.added", "actor": "system", "action": f"event {i}"}
        spec.update(overrides)
        out.append(ledger.append_event(root, **spec))
    return out


def _rewrite_lines(root: Path, edit) -> None:
    path = root / "ledger.jsonl"
    lines = path.read_text(encoding="utf-8").splitlines()
    path.write_text("\n".join(edit(lines)) + "\n", encoding="utf-8")


def test_append_creates_contiguous_seq_and_hash_chain(tmp_path: Path, clock) -> None:
    first, second = _append(tmp_path, 2)
    assert (first["seq"], second["seq"]) == (1, 2)
    assert first["prev"] == "0" * 64
    assert list(first)[:3] == ["id", "seq", "ts"]
    assert first["ts"] == "2026-10-06T09:00:01Z"
    assert first["id"].startswith("evt_")
    lines = (tmp_path / "ledger.jsonl").read_bytes().split(b"\n")
    assert lines[-1] == b""
    assert second["prev"] == hashlib.sha256(lines[0]).hexdigest()
    assert set(first) == set(ledger.FIELDS)


def test_verify_clean_and_missing_ledgers_have_no_issues(tmp_path: Path) -> None:
    assert ledger.verify_chain(tmp_path) == []
    _append(tmp_path, 3)
    assert ledger.verify_chain(tmp_path) == []


def test_editing_a_past_line_breaks_the_chain(tmp_path: Path) -> None:
    _append(tmp_path, 3)

    def edit(lines: list[str]) -> list[str]:
        event = json.loads(lines[0])
        event["action"] = "tampered"
        lines[0] = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
        return lines

    _rewrite_lines(tmp_path, edit)
    issues = ledger.verify_chain(tmp_path)
    assert [i.code for i in issues] == ["LED002"]
    assert "line 2" in issues[0].message


def test_deleting_or_reordering_lines_is_detected(tmp_path: Path) -> None:
    _append(tmp_path, 4)
    original = (tmp_path / "ledger.jsonl").read_text(encoding="utf-8")

    _rewrite_lines(tmp_path, lambda lines: [lines[0], lines[2], lines[3]])
    assert {i.code for i in ledger.verify_chain(tmp_path)} == {"LED002"}

    (tmp_path / "ledger.jsonl").write_text(original, encoding="utf-8")
    _rewrite_lines(tmp_path, lambda lines: [lines[0], lines[2], lines[1], lines[3]])
    assert {i.code for i in ledger.verify_chain(tmp_path)} == {"LED002"}


def test_torn_last_line_is_reported_and_refuses_further_appends(tmp_path: Path) -> None:
    _append(tmp_path, 2)
    path = tmp_path / "ledger.jsonl"
    path.write_bytes(path.read_bytes() + b'{"id":"evt_partial')
    codes = [i.code for i in ledger.verify_chain(tmp_path)]
    assert "LED001" in codes
    with pytest.raises(ledger.LedgerError, match="partial line"):
        _append(tmp_path)


def test_invalid_json_and_missing_fields_are_reported(tmp_path: Path) -> None:
    (tmp_path / "ledger.jsonl").write_text('not json\n{"seq": 1}\n', encoding="utf-8")
    issues = ledger.verify_chain(tmp_path)
    assert issues and all(i.code == "LED001" for i in issues)
    assert any("line 1" in i.message for i in issues)


def test_batch_append_rolls_back_to_original_bytes_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _append(tmp_path, 1)
    path = tmp_path / "ledger.jsonl"
    before = path.read_bytes()

    def boom(fd: int) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(os, "fsync", boom)
    spec = {"type": "note.added", "actor": "system", "action": "x"}
    with pytest.raises(OSError):
        ledger.append_events(tmp_path, [spec, spec])
    monkeypatch.undo()
    assert path.read_bytes() == before


def test_failed_first_append_leaves_no_ledger_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(fd: int) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(os, "fsync", boom)
    with pytest.raises(OSError):
        _append(tmp_path)
    monkeypatch.undo()
    assert not (tmp_path / "ledger.jsonl").exists()


def test_reserved_types() -> None:
    for reserved in ("workspace.created", "job.status_changed", "application.approved", "job.imported"):
        assert ledger.is_reserved(reserved)
    assert not ledger.is_reserved("note.added")


@pytest.mark.parametrize(
    "spec,match",
    [
        ({"type": "BadType", "actor": "system", "action": "x"}, "type"),
        ({"type": "note.added", "actor": "robot", "action": "x"}, "actor"),
        ({"type": "note.added", "actor": "system", "action": ""}, "action"),
        ({"type": "note.added", "actor": "system", "action": "x", "approval": "maybe"}, "approval"),
        ({"type": "job.status_corrected", "actor": "user", "action": "x"}, "reason"),
        ({"type": "note.added", "actor": "system", "action": "x", "colour": "red"}, "unknown"),
    ],
)
def test_spec_validation(tmp_path: Path, spec: dict, match: str) -> None:
    with pytest.raises(ledger.LedgerError, match=match):
        ledger.append_events(tmp_path, [spec])


def test_read_events_reports_the_bad_line(tmp_path: Path) -> None:
    _append(tmp_path, 1)
    path = tmp_path / "ledger.jsonl"
    path.write_text(path.read_text(encoding="utf-8") + "garbage\n", encoding="utf-8")
    with pytest.raises(ledger.LedgerError, match="line 2"):
        ledger.read_events(tmp_path)


def test_concurrent_appends_keep_the_chain_intact(tmp_path: Path) -> None:
    def worker() -> None:
        for _ in range(10):
            ledger.append_event(tmp_path, type="note.added", actor="system", action="x")

    threads = [threading.Thread(target=worker) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert [e["seq"] for e in ledger.read_events(tmp_path)] == list(range(1, 61))
    assert ledger.verify_chain(tmp_path) == []
