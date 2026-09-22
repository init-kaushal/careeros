from careeros.workspace.migrations import register
from careeros.storage.interface import StorageProvider


@register("003_approvals")
def m003_approvals(storage: StorageProvider) -> None:
    if not storage.exists("approvals/.keep"):
        storage.write("approvals/.keep", b"")
