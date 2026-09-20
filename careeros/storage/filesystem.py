import os
import tempfile
from pathlib import Path


class LocalFilesystemStorage:
    def __init__(self, root: str) -> None:
        # Resolved once here (not re-resolved per call) so every comparison against
        # self._root — including list()'s relative_to() — uses the same symlink-free
        # basis. Without this, a root reached through a symlink (e.g. /tmp on macOS,
        # or any home directory behind a symlink) made list() raise ValueError, since
        # _resolve() below returns fully-resolved paths but self._root stayed unresolved.
        self._root = Path(root).resolve()

    def _resolve(self, path: str) -> Path:
        resolved = (self._root / path).resolve()
        try:
            resolved.relative_to(self._root)
        except ValueError:
            raise ValueError(f"Path '{path}' escapes workspace root")
        return resolved

    def read(self, path: str) -> bytes:
        return self._resolve(path).read_bytes()

    def write(self, path: str, data: bytes) -> None:
        full = self._resolve(path)
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_bytes(data)
        full.chmod(0o600)

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

    def resolve(self, path: str) -> str:
        """Return the absolute filesystem path for a workspace-relative path."""
        return str(self._resolve(path))
