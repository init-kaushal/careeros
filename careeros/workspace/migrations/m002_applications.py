from careeros.workspace.migrations import register
from careeros.storage.interface import StorageProvider


@register("002_applications")
def m002_applications(storage: StorageProvider) -> None:
    if not storage.exists("applications/.keep"):
        storage.write("applications/.keep", b"")
