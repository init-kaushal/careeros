import os
import tempfile
from pathlib import Path


class LocalFilesystemStorage:
    def __init__(self, root: str) -> None:
        self._root = Path(root)

    def _resolve(self, path: str) -> Path:
        resolved = (self._root / path).resolve()
        try:
            resolved.relative_to(self._root.resolve())
        except ValueError:
            raise ValueError(f"Path '{path}' escapes workspace root")
        return resolved

    def read(self, path: str) -> bytes:
        return self._resolve(path).read_bytes()

    def write(self, path: str, data: bytes) -> None:
        full = self._resolve(path)
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_bytes(data)

    def atomic_write(self, path: str, data: bytes) -> None:
        full = self._resolve(path)
        full.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=full.parent)
        try:
            os.write(fd, data)
            os.close(fd)
            os.replace(tmp, str(full))
        except Exception:
            try:
                os.close(fd)
            except OSError:
                pass
            os.unlink(tmp)
            raise

    def exists(self, path: str) -> bool:
        return self._resolve(path).exists()

    def delete(self, path: str) -> None:
        self._resolve(path).unlink()

    def list(self, prefix: str) -> list[str]:
        base = self._resolve(prefix)
        if not base.exists():
            return []
        return [
            str(p.relative_to(self._root))
            for p in base.rglob("*")
            if p.is_file()
        ]

    def append(self, path: str, data: bytes) -> None:
        full = self._resolve(path)
        full.parent.mkdir(parents=True, exist_ok=True)
        with open(full, "ab") as f:
            f.write(data)
