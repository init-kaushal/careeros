import json
from careeros.workspace.migrations import register
from careeros.storage.interface import StorageProvider


@register("005_custom_boards")
def m005_custom_boards(storage: StorageProvider) -> None:
    if not storage.exists("config/custom_boards.json"):
        storage.write("config/custom_boards.json", json.dumps({}, indent=2).encode())
