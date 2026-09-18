import json
from careeros.workspace.migrations import register
from careeros.storage.interface import StorageProvider


@register("001_initial")
def m001_initial(storage: StorageProvider) -> None:
    keep_dirs = [
        "profile/.keep",
        "resumes/versions/.keep",
        "config/.keep",
        "activity/.keep",
        ".careeros/migrations/.keep",
    ]
    for path in keep_dirs:
        if not storage.exists(path):
            storage.write(path, b"")

    if not storage.exists("config/sources.json"):
        storage.write(
            "config/sources.json",
            json.dumps({"sources": []}, indent=2).encode(),
        )

    if not storage.exists("config/policies.json"):
        storage.write(
            "config/policies.json",
            json.dumps(
                {
                    "hard_requirements": {},
                    "soft_requirements": {},
                    "approval_required": True,
                },
                indent=2,
            ).encode(),
        )

    if not storage.exists("config/storage.json"):
        storage.write(
            "config/storage.json",
            json.dumps({"backend": "local"}, indent=2).encode(),
        )
