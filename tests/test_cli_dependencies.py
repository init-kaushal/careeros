"""Every third-party import under careeros/ must be a declared dependency."""
import ast
import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# distribution name -> import name, only where they differ
IMPORT_NAMES = {"pyyaml": "yaml"}


def _declared() -> set[str]:
    deps = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["dependencies"]
    names = {re.split(r"[\[<>=!~ ;]", d, maxsplit=1)[0].lower() for d in deps}
    return {IMPORT_NAMES.get(n, n) for n in names}


def test_no_undeclared_third_party_imports():
    allowed = _declared() | {"careeros"} | set(sys.stdlib_module_names)
    offenders = []
    for path in sorted((ROOT / "careeros").rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(), str(path))):
            if isinstance(node, ast.Import):
                mods = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                mods = [node.module]
            else:
                continue
            offenders += [f"{path.relative_to(ROOT)}: {m}" for m in mods if m.split(".")[0] not in allowed]
    assert not offenders, offenders
