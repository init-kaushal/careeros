"""Canonical JSON, hashing and text normalisation for evaluation records. Pure; no I/O.

Canonical form (used for every hash stored in or derived from an evaluation):

* UTF-8, no escaping of non-ASCII (`ensure_ascii=False`), no whitespace (`separators=(",", ":")`),
  `allow_nan=False`;
* mappings are sorted by key (keys must be strings); lists and tuples keep their order and both serialise
  as arrays; `None` is kept as `null`; empty lists and mappings are kept;
* **floats, including NaN and infinities, are forbidden** in a hashed structure and raise `TypeError`
  (so do `Fraction`, sets, bytes and any other type). Exact quantities are carried as integers or as
  string fractions such as `"13/20"`, so a hash cannot depend on the Python version or on float repr.
  `bool` is allowed (it serialises as `true`/`false`).

Notes for callers:

* `canonical_json` does not Unicode-normalise strings: the same bytes give the same hash.
* `True` and `1` are distinct in the hashed form but equal under Python `==`: compare structures by their
  canonical form, never with `==`.
* `result_sha256`/`result_canonical` may raise `TypeError`, `ValueError` or `AttributeError` on a
  hand-edited malformed result; callers (the validator) must catch these and report, not crash.

Exact values: `exact(x)` encodes an `int`/`Fraction` as an `int` when the reduced denominator is 1, else as
the reduced string `"p/q"` (positive denominator); `parse_exact` is its strict inverse (an int or a reduced
`"p/q"` only; `"0.5"`, `"2/2"`, `"1/0"`, whitespace, bools and floats are rejected). Every quantity in the
basis uses this one encoder.

Scores: `fixed4(x)` is the one rounding routine: round half up to four decimals using integer arithmetic
only (no `decimal`, no float arithmetic, so no process-wide setting can change it). A float is read through
its shortest repr as an exact fraction. The value stored in YAML for each score field MUST be
`float(fixed4(x))`, never Python's `round()`. Scores outside [0, 1] are a `ValueError`.

The committed `result:` mapping, whose top-level `coverage`, `fit` and `combined`
are stored as four-decimal numbers. `result_canonical` renders those three (float, int or Fraction) as
fixed-point strings such as `"0.8000"` (round half up on the decimal repr), leaves an omitted or `null`
`fit` as `null`, passes an already fixed-point string through (so it is idempotent), and rejects any other
float anywhere in the result. The three keys are always emitted: a missing key and `null` hash identically. `result_sha256` hashes that form: it is the hash `evaluation.completed`
carries.

`normalize_text` is the form hashed for the `jd` stamp: NFC, all line endings and whitespace runs become
one space, ends stripped; a leading U+FEFF (editor byte-order mark) is dropped first. A zero-width space
is not whitespace here and stays, so inserting one makes the posting stale. Case is kept, so a reflowed posting stays fresh and a reworded one goes stale.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from fractions import Fraction
from typing import Any

SCORE_FIELDS = ("coverage", "fit", "combined")
_FIXED = re.compile(r"\d\.\d{4}")
_WS = re.compile(r"\s+")


def _check(obj: Any, path: str = "$") -> None:
    if obj is None or isinstance(obj, (bool, int, str)):
        return
    if isinstance(obj, float):
        raise TypeError(f"float {obj!r} at {path}: hashed structures carry integers or string fractions")
    if isinstance(obj, dict):
        for key, value in obj.items():
            if not isinstance(key, str):
                raise TypeError(f"non-string key {key!r} at {path}")
            _check(value, f"{path}.{key}")
        return
    if isinstance(obj, (list, tuple)):
        for i, value in enumerate(obj):
            _check(value, f"{path}[{i}]")
        return
    raise TypeError(f"{type(obj).__name__} at {path} cannot be canonicalised")


def canonical_json(obj: Any) -> bytes:
    _check(obj)
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fixed4(value: Any) -> str:
    """A score in [0, 1] as a four-decimal string, rounded half up with integer arithmetic only."""
    if isinstance(value, bool):
        raise TypeError("a score is a number, not a bool")
    if isinstance(value, str):
        if not _FIXED.fullmatch(value):
            raise ValueError(f"score {value!r} is not fixed-point with four decimals")
        exact_value = Fraction(value)
    elif isinstance(value, float):
        if not math.isfinite(value):
            raise TypeError(f"non-finite score {value!r}")
        exact_value = Fraction(repr(value))
    elif isinstance(value, (int, Fraction)):
        exact_value = Fraction(value)
    else:
        raise TypeError(f"score of type {type(value).__name__}")
    if not 0 <= exact_value <= 1:
        raise ValueError(f"score {value!r} is outside [0, 1]")
    q, r = divmod(exact_value.numerator * 10000, exact_value.denominator)
    if 2 * r >= exact_value.denominator:
        q += 1
    return f"{q // 10000}.{q % 10000:04d}"


_EXACT = re.compile(r"(0|-?[1-9]\d*)/([1-9]\d*)")


def exact(value: int | Fraction) -> int | str:
    """Encode an exact quantity: an int when whole, else the reduced string `"p/q"`."""
    if isinstance(value, bool) or not isinstance(value, (int, Fraction)):
        raise TypeError(f"{type(value).__name__} is not an exact quantity")
    f = Fraction(value)
    return f.numerator if f.denominator == 1 else f"{f.numerator}/{f.denominator}"


def parse_exact(value: Any) -> Fraction:
    """Inverse of `exact`; accepts only what `exact` produces."""
    if isinstance(value, bool):
        raise TypeError("bool is not an exact quantity")
    if isinstance(value, int):
        return Fraction(value)
    if not isinstance(value, str):
        raise TypeError(f"{type(value).__name__} is not an exact quantity")
    m = _EXACT.fullmatch(value)
    if not m:
        raise ValueError(f"{value!r} is not an exact quantity")
    f = Fraction(int(m[1]), int(m[2]))
    if exact(f) != value:
        raise ValueError(f"{value!r} is not in reduced form")
    return f


def _plain(obj: Any, path: str = "$") -> Any:
    if isinstance(obj, dict):
        return {k: _plain(v, f"{path}.{k}") for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_plain(v, f"{path}[{i}]") for i, v in enumerate(obj)]
    _check(obj, path)
    return obj


def result_canonical(result: dict) -> dict:
    """A copy of `result` ready to hash: score fields as fixed-point strings; any other float is an error."""
    out = {k: _plain(v, f"$.{k}") for k, v in result.items() if k not in SCORE_FIELDS}
    for name in SCORE_FIELDS:
        value = result.get(name)
        out[name] = None if value is None else fixed4(value)
    return out


def result_sha256(result: dict) -> str:
    return sha256_hex(canonical_json(result_canonical(result)))


def normalize_text(text: str) -> str:
    text = text.removeprefix("\ufeff")
    return _WS.sub(" ", unicodedata.normalize("NFC", text)).strip()
