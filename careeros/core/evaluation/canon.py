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

The one exception is the committed `result:` mapping, whose top-level `coverage`, `fit` and `combined`
are stored as four-decimal numbers. `result_canonical` renders those three (float, int or Fraction) as
fixed-point strings such as `"0.8000"` (round half up on the decimal repr), leaves an omitted or `null`
`fit` as it is, passes an already fixed-point string through (so it is idempotent), and rejects any other
float anywhere in the result. `result_sha256` hashes that form: it is the hash `evaluation.completed`
carries.

`normalize_text` is the form hashed for the `jd` stamp: NFC, all line endings and whitespace runs become
one space, ends stripped. Case is kept, so a reflowed posting stays fresh and a reworded one goes stale.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from decimal import ROUND_HALF_UP, Decimal
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


def _fixed4(value: Any) -> str:
    if isinstance(value, bool):
        raise TypeError("a score is a number, not a bool")
    if isinstance(value, str):
        if not _FIXED.fullmatch(value):
            raise ValueError(f"score {value!r} is not fixed-point with four decimals")
        return value
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            raise TypeError(f"non-finite score {value!r}")
        exact = Fraction(Decimal(repr(value)))
    elif isinstance(value, (int, Fraction)):
        exact = Fraction(value)
    else:
        raise TypeError(f"score of type {type(value).__name__}")
    scaled = (Decimal(exact.numerator) * 10000 / Decimal(exact.denominator)).quantize(Decimal(1), ROUND_HALF_UP)
    return f"{int(scaled) // 10000}.{int(scaled) % 10000:04d}"


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
        if name in result:
            out[name] = None if result[name] is None else _fixed4(result[name])
    return out


def result_sha256(result: dict) -> str:
    return sha256_hex(canonical_json(result_canonical(result)))


def normalize_text(text: str) -> str:
    return _WS.sub(" ", unicodedata.normalize("NFC", text)).strip()
