"""Deterministic text normalisation and number/year/duration extraction. No models, no network."""

from __future__ import annotations

import re
from dataclasses import dataclass

_SCALE = {
    "k": 1e3, "thousand": 1e3, "m": 1e6, "mm": 1e6, "million": 1e6, "b": 1e9, "bn": 1e9, "billion": 1e9,
    "lakh": 1e5, "lakhs": 1e5, "crore": 1e7, "crores": 1e7,
}
_CURRENCY = {"$": "USD", "€": "EUR", "£": "GBP", "₹": "INR"}
_SCALE_WORDS = r"k|mm|m|bn|b|thousand|million|billion|lakhs?|crores?"
_YEAR = re.compile(r"^(19[5-9]\d|20\d\d|2100)$")
_YEAR_CONNECTORS = {
    "to", "and", "through", "until", "till", "as", "when", "while", "i", "we", "he", "she", "they", "it",
    "at", "in", "on", "for", "with", "my", "the", "a", "an", "or", "by", "from", "was", "is",
}
_MASK = re.compile(r"https?://\S+|www\.\S+|\S+@\S+|\+\d[\d\s().-]{7,}\d")
_VERSION = re.compile(r"\bv?\d+(?:\.\d+){2,}\b")
_DURATION = re.compile(r"(?P<n>\d+)(?P<plus>\+)?[\s-]{0,2}years?\b", re.I)
_NOT_EXPERIENCE = re.compile(r"\s+(?:old|ago|degree|program|course|bachelor|master)\b", re.I)
_NUMTXT = r"\d+(?:,\d{2,3})*(?:\.\d+)?(?!\d)"
_NUMBER = re.compile(
    r"(?P<cur>[$€£₹])\s?(?P<cnum>" + _NUMTXT + r")\s?(?P<cscale>" + _SCALE_WORDS + r")?(?![A-Za-z])(?P<cplus>\+)?"
    r"|(?<![\d/])(?P<snum>\d+(?:\.\d+)?)\s?/\s?(?P<sden>\d+(?:\.\d+)?)(?![\d/])"
    r"|(?P<pnum>" + _NUMTXT + r")\s?(?P<pct>%|percent\b|per\s?cent\b)"
    r"|(?P<mnum>\d+(?:\.\d+)?)\s?[x×](?![A-Za-z0-9])"
    r"|(?<![\w.+])(?P<num>" + _NUMTXT + r")\s?(?P<scale>" + _SCALE_WORDS + r")?(?![A-Za-z0-9])(?P<plus>\+)?",
    re.I,
)
SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(\[])")


@dataclass(frozen=True)
class NumberAtom:
    raw: str
    value: float
    unit: str       # percent | count | multiplier | score | currency:<code>
    plus: bool
    start: int
    end: int

    def metric(self) -> dict:
        return {"raw": self.raw, "value": self.value, "unit": self.unit, "plus": self.plus}


@dataclass(frozen=True)
class YearAtom:
    year: int
    start: int
    end: int


@dataclass(frozen=True)
class DurationAtom:
    years: int
    plus: bool
    start: int
    end: int


@dataclass
class Scan:
    numbers: list[NumberAtom]
    years: list[YearAtom]
    durations: list[DurationAtom]


def norm_text(value: str) -> str:
    """Case-folded, punctuation-free, '&' = 'and', single-spaced."""
    value = value.casefold().replace("&", " and ")
    return " ".join(re.sub(r"[^\w]+", " ", value).split())


def norm_org(value: str, suffixes: frozenset[str]) -> str:
    tokens = norm_text(value).split()
    while len(tokens) > 1 and tokens[-1] in suffixes:
        tokens.pop()
    return " ".join(tokens)


def metric_key(value: float, unit: str) -> tuple[float, str]:
    return (round(float(value), 6), unit)


def split_sentences(line: str) -> list[str]:
    return [part.strip() for part in SENTENCE_END.split(line) if part.strip()]


def _to_float(text: str) -> float:
    return float(text.replace(",", ""))


def _overlaps(start: int, end: int, spans: list[tuple[int, int]]) -> bool:
    return any(start < e and s < end for s, e in spans)


def scan(text: str, skip: list[tuple[int, int]] | None = None) -> Scan:
    """Find numbers, years and durations. `skip` lists spans (such as technology versions) to ignore."""
    blocked: list[tuple[int, int]] = list(skip or ())
    blocked.extend(m.span() for m in _MASK.finditer(text))
    blocked.extend(m.span() for m in _VERSION.finditer(text))
    result = Scan([], [], [])
    for m in _DURATION.finditer(text):
        if _overlaps(m.start(), m.end(), blocked):
            continue
        blocked.append(m.span())
        if not _NOT_EXPERIENCE.match(text[m.end():]):
            result.durations.append(DurationAtom(int(m.group("n")), bool(m.group("plus")), m.start(), m.end()))
    for m in _NUMBER.finditer(text):
        if _overlaps(m.start(), m.end(), blocked):
            continue
        raw = m.group(0).strip()
        if m.group("cur"):
            value = _to_float(m.group("cnum")) * _SCALE.get((m.group("cscale") or "").lower(), 1)
            atom = NumberAtom(raw, value, f"currency:{_CURRENCY[m.group('cur')]}", bool(m.group("cplus")), m.start(), m.end())
        elif m.group("snum"):
            num, den = float(m.group("snum")), float(m.group("sden"))
            if den == 0 or num > den or den > 1000:
                continue
            atom = NumberAtom(raw, num / den, "score", False, m.start(), m.end())
        elif m.group("pnum"):
            atom = NumberAtom(raw, _to_float(m.group("pnum")), "percent", False, m.start(), m.end())
        elif m.group("mnum"):
            atom = NumberAtom(raw, float(m.group("mnum")), "multiplier", False, m.start(), m.end())
        else:
            digits = m.group("num")
            if "," not in digits and "." not in digits and len(digits) >= 7:
                continue  # phone numbers, ids
            scale = (m.group("scale") or "").lower()
            if not scale and _YEAR.match(digits):
                follower = re.match(r"\s+([a-z]+)", text[m.end():])
                if not follower or follower.group(1) in _YEAR_CONNECTORS:
                    result.years.append(YearAtom(int(digits), m.start(), m.end()))
                    continue
            atom = NumberAtom(raw, _to_float(digits) * _SCALE.get(scale, 1), "count", bool(m.group("plus")), m.start(), m.end())
        result.numbers.append(atom)
    return result
