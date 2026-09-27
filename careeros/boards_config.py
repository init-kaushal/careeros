from __future__ import annotations

import json
from dataclasses import dataclass

from careeros.storage.interface import StorageProvider

_BOARDS_PATH = "config/boards.json"
_CUSTOM_BOARDS_PATH = "config/custom_boards.json"

BROWSER_BOARD_NAMES = frozenset({"linkedin", "indeed", "wellfound"})
API_BOARD_NAMES = frozenset({"greenhouse", "lever"})
ALL_KNOWN_BOARDS = BROWSER_BOARD_NAMES | API_BOARD_NAMES


@dataclass(frozen=True)
class CustomBoard:
    name: str
    login_url: str
    search_url: str  # {query} placeholder filled at browse time


def load_registered_boards(storage: StorageProvider) -> list[str]:
    if not storage.exists(_BOARDS_PATH):
        return []
    try:
        raw = json.loads(storage.read(_BOARDS_PATH).decode())
        return [str(b) for b in raw.get("boards", []) if isinstance(b, str)]
    except Exception:
        return []


def save_registered_boards(storage: StorageProvider, boards: list[str]) -> None:
    storage.atomic_write(_BOARDS_PATH, json.dumps({"boards": boards}, indent=2).encode())


def register_board(storage: StorageProvider, name: str) -> None:
    current = load_registered_boards(storage)
    if name not in current:
        save_registered_boards(storage, current + [name])


def load_custom_boards(storage: StorageProvider) -> dict[str, CustomBoard]:
    if not storage.exists(_CUSTOM_BOARDS_PATH):
        return {}
    try:
        raw = json.loads(storage.read(_CUSTOM_BOARDS_PATH).decode())
        result = {}
        for name, cfg in raw.items():
            if isinstance(cfg, dict) and cfg.get("login_url") and cfg.get("search_url"):
                result[name] = CustomBoard(
                    name=name,
                    login_url=cfg["login_url"],
                    search_url=cfg["search_url"],
                )
        return result
    except Exception:
        return {}


def save_custom_board(storage: StorageProvider, board: CustomBoard) -> None:
    existing: dict = {}
    if storage.exists(_CUSTOM_BOARDS_PATH):
        try:
            existing = json.loads(storage.read(_CUSTOM_BOARDS_PATH).decode())
        except Exception:
            pass
    existing[board.name] = {"login_url": board.login_url, "search_url": board.search_url}
    storage.atomic_write(_CUSTOM_BOARDS_PATH, json.dumps(existing, indent=2).encode())
