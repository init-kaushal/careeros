from pathlib import Path
from pydantic import BaseModel

CONFIG_PATH = Path.home() / ".config" / "careeros" / "config.json"


class GlobalConfig(BaseModel):
    workspace_path: str | None = None

    def save(self) -> None:
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(self.model_dump_json(indent=2))

    @classmethod
    def load(cls) -> "GlobalConfig":
        if not CONFIG_PATH.exists():
            return cls()
        return cls.model_validate_json(CONFIG_PATH.read_text())
