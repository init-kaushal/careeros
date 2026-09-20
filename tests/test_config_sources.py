import json

from careeros.config_sources import BoardEntry, build_source, load_board_entries
from careeros.storage.filesystem import LocalFilesystemStorage


def _write(tmp_path, payload):
    storage = LocalFilesystemStorage(str(tmp_path))
    storage.atomic_write("config/sources.json", json.dumps(payload).encode())
    return storage


def test_loads_well_formed_entries(tmp_path):
    storage = _write(tmp_path, {"sources": [
        {"source": "greenhouse", "board": "stripe", "company": "Stripe",
         "mode": "SEARCH_ONLY"},
    ]})
    assert load_board_entries(storage) == [
        BoardEntry(source="greenhouse", board="stripe", company="Stripe")
    ]


def test_skips_legacy_entries_without_a_board(tmp_path):
    # Legacy Phase 1 entries are inert now exactly as they always were.
    storage = _write(tmp_path, {"sources": [
        {"source": "greenhouse", "mode": "SEARCH_ONLY"},
        {"source": "naukri", "mode": "SEARCH_ONLY"},
    ]})
    assert load_board_entries(storage) == []


def test_skips_entries_naming_an_unknown_source(tmp_path):
    storage = _write(tmp_path, {"sources": [
        {"source": "naukri", "board": "x", "company": "X"},
    ]})
    assert load_board_entries(storage) == []


def test_defaults_company_to_the_board_slug_when_absent(tmp_path):
    storage = _write(tmp_path, {"sources": [
        {"source": "lever", "board": "acme"},
    ]})
    assert load_board_entries(storage) == [
        BoardEntry(source="lever", board="acme", company="acme")
    ]


def test_missing_file_yields_no_entries(tmp_path):
    assert load_board_entries(LocalFilesystemStorage(str(tmp_path))) == []


def test_malformed_json_yields_no_entries(tmp_path):
    storage = LocalFilesystemStorage(str(tmp_path))
    storage.atomic_write("config/sources.json", b"{ not json")
    assert load_board_entries(storage) == []


def test_build_source_returns_the_matching_connector():
    gh = build_source(BoardEntry(source="greenhouse", board="stripe", company="Stripe"))
    lv = build_source(BoardEntry(source="lever", board="acme", company="Acme"))
    assert gh.name == "greenhouse"
    assert lv.name == "lever"
