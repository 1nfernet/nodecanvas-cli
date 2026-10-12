"""JSON reader/writer that round-trips FullSerializer output byte for byte.

Numbers keep their original text (C# formats floats differently from Python), key order is
preserved, and strings are escaped the way FullSerializer escapes them.
"""

from __future__ import annotations

import json
from typing import Any


class Num(float):
    """A float that remembers how it was written."""

    __slots__ = ("text",)

    def __new__(cls, text: str):
        obj = float.__new__(cls, text)
        obj.text = text
        return obj

    def __repr__(self) -> str:
        return self.text


def loads(text: str) -> Any:
    return json.loads(text, parse_float=Num)


_ESC = {'"': '\\"', "\\": "\\\\", "\n": "\\n", "\r": "\\r", "\t": "\\t", "\b": "\\b", "\f": "\\f"}


def _str(s: str) -> str:
    out = []
    for ch in s:
        e = _ESC.get(ch)
        if e is not None:
            out.append(e)
        elif ord(ch) < 0x20:
            out.append("\\u%04x" % ord(ch))
        else:
            out.append(ch)
    return '"' + "".join(out) + '"'


def _num(v) -> str:
    if isinstance(v, Num):
        return v.text
    if isinstance(v, int):
        return str(v)
    if v != v or v in (float("inf"), float("-inf")):
        raise ValueError("NaN/Infinity cannot be serialized")
    if float(v).is_integer() and abs(v) < 1e15:
        return f"{v:.1f}"
    r = repr(float(v))
    if "e" in r:
        mant, exp = r.split("e")
        sign = "-" if exp.startswith("-") else ""
        r = f"{mant}E{sign}{exp.lstrip('+-').rjust(2, '0')}"
    return r


def dumps(v: Any) -> str:
    parts = []
    _write(v, parts)
    return "".join(parts)


def _write(v: Any, out: list) -> None:
    if v is None:
        out.append("null")
    elif v is True:
        out.append("true")
    elif v is False:
        out.append("false")
    elif isinstance(v, str):
        out.append(_str(v))
    elif isinstance(v, (int, float)):
        out.append(_num(v))
    elif isinstance(v, dict):
        out.append("{")
        first = True
        for k, x in v.items():
            if not first:
                out.append(",")
            first = False
            out.append(_str(k))
            out.append(":")
            _write(x, out)
        out.append("}")
    elif isinstance(v, (list, tuple)):
        out.append("[")
        for i, x in enumerate(v):
            if i:
                out.append(",")
            _write(x, out)
        out.append("]")
    else:
        raise TypeError(f"cannot serialize {type(v).__name__}")
