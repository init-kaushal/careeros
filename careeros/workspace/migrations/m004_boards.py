import json
from careeros.workspace.migrations import register
from careeros.storage.interface import StorageProvider


@register("004_boards")
def m004_boards(storage: StorageProvider) -> None:
    if not storage.exists("config/boards.json"):
        storage.write("config/boards.json", json.dumps({"boards": []}, indent=2).encode())
