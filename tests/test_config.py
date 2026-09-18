import pytest
from pathlib import Path
from careeros.config import GlobalConfig


def test_save_and_load(tmp_path, monkeypatch):
    monkeypatch.setattr("careeros.config.CONFIG_PATH", tmp_path / "config.json")
    config = GlobalConfig(workspace_path="/my/career")
    config.save()
    loaded = GlobalConfig.load()
    assert loaded.workspace_path == "/my/career"


def test_load_missing_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setattr("careeros.config.CONFIG_PATH", tmp_path / "nonexistent" / "config.json")
    config = GlobalConfig.load()
    assert config.workspace_path is None


def test_save_creates_parent_dirs(tmp_path, monkeypatch):
    nested = tmp_path / "a" / "b" / "config.json"
    monkeypatch.setattr("careeros.config.CONFIG_PATH", nested)
    GlobalConfig(workspace_path="/ws").save()
    assert nested.exists()


def test_overwrite_updates_value(tmp_path, monkeypatch):
    monkeypatch.setattr("careeros.config.CONFIG_PATH", tmp_path / "config.json")
    GlobalConfig(workspace_path="/first").save()
    GlobalConfig(workspace_path="/second").save()
    loaded = GlobalConfig.load()
    assert loaded.workspace_path == "/second"
