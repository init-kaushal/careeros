"""The vocabulary the importer and the checker recognise: technologies, role words, certifications."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from careeros.core.workspace import WorkspaceError

BUNDLED = Path(__file__).resolve().parents[2] / "data" / "lexicon.yaml"
_TECH_KEYS = {"name", "aliases", "case_sensitive"}


class LexiconError(WorkspaceError):
    """A lexicon file cannot be used."""


@dataclass(frozen=True)
class TechSpan:
    canonical: str
    start: int
    end: int


class Lexicon:
    def __init__(self, data: dict) -> None:
        self.role_nouns = frozenset(w.lower() for w in data.get("role_nouns", ()))
        self.seniority = frozenset(w.lower() for w in data.get("seniority", ()))
        self.title_words = frozenset(w.lower() for w in data.get("title_words", ()))
        self.org_suffixes = frozenset(w.lower() for w in data.get("org_suffixes", ()))
        self.stoplist = frozenset(w.lower() for w in data.get("stoplist", ()))
        self._tech_defs: list[dict] = list(data.get("technologies", ()))
        self._cert_defs: list[dict] = list(data.get("certifications", ()))
        self._data = data
        self._fold: dict[str, str] = {}      # lower-cased alias -> canonical (case-insensitive aliases)
        self._exact: dict[str, str] = {}     # alias -> canonical (aliases that must match exactly)
        for entry in self._tech_defs:
            canonical = entry["name"]
            sensitive = set(entry.get("case_sensitive", ()))
            for alias in [canonical, *entry.get("aliases", ())]:
                if alias in sensitive:
                    self._exact[alias] = canonical
                else:
                    self._fold[alias.lower()] = canonical
        self.certs: dict[str, str] = {}      # lower-cased alias -> canonical
        self.cert_aliases: list[tuple[str, str]] = []  # (alias as written, canonical)
        for entry in self._cert_defs:
            for alias in [entry["name"], *entry.get("aliases", ())]:
                self.certs[alias.lower()] = entry["name"]
                self.cert_aliases.append((alias, entry["name"]))
        self._fold_re = self._compile(sorted(self._fold, key=len, reverse=True), re.IGNORECASE)
        self._exact_re = self._compile(sorted(self._exact, key=len, reverse=True), 0)

    @staticmethod
    def _compile(aliases: list[str], flags: int) -> re.Pattern | None:
        if not aliases:
            return None
        body = "|".join(re.escape(a) for a in aliases)
        return re.compile(rf"(?<![A-Za-z0-9_])(?:{body})(?![A-Za-z0-9_])", flags)

    def canonical_tech(self, name: str) -> str | None:
        name = name.strip()
        return self._exact.get(name) or self._fold.get(name.lower())

    def find_techs(self, text: str) -> list[TechSpan]:
        found: list[TechSpan] = []
        for pattern, lookup in ((self._fold_re, lambda s: self._fold[s.lower()]), (self._exact_re, lambda s: self._exact[s])):
            if pattern is None:
                continue
            for match in pattern.finditer(text):
                found.append(TechSpan(lookup(match.group(0)), match.start(), match.end()))
        found.sort(key=lambda s: (s.start, -(s.end - s.start)))
        result: list[TechSpan] = []
        for span in found:
            if result and span.start < result[-1].end:
                continue  # overlaps a longer or earlier match
            result.append(span)
        return result

    def with_extra_techs(self, names: list[str]) -> "Lexicon":
        known = {n.lower() for n in self._fold} | set(self._exact)
        extra = [{"name": n} for n in dict.fromkeys(names) if n and n.lower() not in known]
        if not extra:
            return self
        data = dict(self._data)
        data["technologies"] = [*self._tech_defs, *extra]
        return Lexicon(data)


def _read(path: Path) -> dict:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise LexiconError(f"{path.name} could not be read: {exc}") from exc
    if not isinstance(data, dict):
        raise LexiconError(f"{path.name} must be a mapping")
    return data


def _check_tech(entry: object, where: str) -> None:
    if not isinstance(entry, dict) or not isinstance(entry.get("name"), str) or not entry["name"].strip():
        raise LexiconError(f"{where}: every technology needs a name")
    unknown = set(entry) - _TECH_KEYS
    if unknown:
        raise LexiconError(f"{where}: unknown key(s) {', '.join(sorted(unknown))} for {entry['name']}")


def load_lexicon(root: Path | None = None) -> Lexicon:
    """The bundled lexicon plus the additions in <workspace>/career/lexicon.yaml, if present."""
    data = _read(BUNDLED)
    if root is not None:
        extra_path = Path(root) / "career" / "lexicon.yaml"
        if extra_path.is_file():
            extra = _read(extra_path)
            for entry in extra.get("technologies", ()):
                _check_tech(entry, "career/lexicon.yaml")
            for key in ("technologies", "certifications"):
                data[key] = [*data.get(key, ()), *extra.get(key, ())]
            for key in ("role_nouns", "seniority", "title_words", "org_suffixes", "stoplist"):
                data[key] = [*data.get(key, ()), *extra.get(key, ())]
    return Lexicon(data)
