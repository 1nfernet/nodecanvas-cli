"""Minimal codec for the single YAML scalar Unity writes for a serialized graph.

Unity emits long strings as single-quoted flow scalars and folds them the way
libyaml does: when the column passes ``BEST_WIDTH`` a single space is replaced
by a line break plus indentation. Only that one field is touched; the rest of
the file is kept verbatim.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

BEST_WIDTH = 80


@dataclass
class ScalarSpan:
    key: str
    key_indent: int
    start_line: int
    end_line: int  # exclusive
    value: str
    style: str  # "single", "double", "plain", "empty"


def _key_prefix(indent: int, key: str) -> str:
    return " " * indent + key + ":"


def find_scalar(lines: List[str], key: str, start: int = 0, stop: Optional[int] = None) -> Optional[ScalarSpan]:
    stop = len(lines) if stop is None else stop
    for i in range(start, stop):
        line = lines[i]
        stripped = line.lstrip(" ")
        if not stripped.startswith(key + ":"):
            continue
        indent = len(line) - len(stripped)
        rest = stripped[len(key) + 1 :]
        if rest and not rest.startswith(" "):
            continue
        rest = rest[1:] if rest.startswith(" ") else rest
        if rest == "":
            return ScalarSpan(key, indent, i, i + 1, "", "empty")
        if rest.startswith("'"):
            return _read_single(lines, key, indent, i, rest)
        if rest.startswith('"'):
            return _read_double(lines, key, indent, i, rest)
        j = i + 1
        while j < stop and lines[j].startswith(" " * (indent + 1)) and not lines[j].lstrip(" ").startswith("- "):
            j += 1
        raw = [rest] + [line.strip() for line in lines[i + 1 : j]]
        return ScalarSpan(key, indent, i, j, _fold(raw), "plain")
    return None


def _read_single(lines: List[str], key: str, indent: int, i: int, rest: str) -> ScalarSpan:
    body = rest[1:]
    raw = []
    j = i
    while True:
        text = body if j == i else lines[j].lstrip(" ")
        end = _closing_quote(text)
        if end is not None:
            raw.append(text[:end])
            break
        raw.append(text)
        j += 1
        if j >= len(lines):
            raise ValueError(f"unterminated single-quoted scalar for {key!r}")
    value = _fold(raw).replace("''", "'")
    return ScalarSpan(key, indent, i, j + 1, value, "single")


def _read_double(lines: List[str], key: str, indent: int, i: int, rest: str) -> ScalarSpan:
    body = rest[1:]
    raw = []
    j = i
    while True:
        text = body if j == i else lines[j].lstrip(" ")
        end = _closing_double(text)
        if end is not None:
            raw.append(text[:end])
            break
        raw.append(text)
        j += 1
        if j >= len(lines):
            raise ValueError(f"unterminated double-quoted scalar for {key!r}")
    joined = ""
    for k, part in enumerate(raw):
        if k == 0:
            joined = part
        elif joined.endswith("\\") and not joined.endswith("\\\\"):
            joined = joined[:-1] + part
        else:
            joined += ("\n" if part == "" else " " + part) if part or k < len(raw) - 1 else ""
    return ScalarSpan(key, indent, i, j + 1, _unescape(joined), "double")


def _closing_double(text: str) -> Optional[int]:
    k = 0
    while k < len(text):
        if text[k] == "\\":
            k += 2
            continue
        if text[k] == '"':
            return k
        k += 1
    return None


_DQ_ESC = {"n": "\n", "r": "\r", "t": "\t", '"': '"', "\\": "\\", "/": "/", "0": "\0", "b": "\b", "f": "\f", " ": " "}


def _unescape(s: str) -> str:
    out, k = [], 0
    while k < len(s):
        ch = s[k]
        if ch == "\\" and k + 1 < len(s):
            nxt = s[k + 1]
            if nxt == "u":
                out.append(chr(int(s[k + 2 : k + 6], 16)))
                k += 6
                continue
            if nxt == "x":
                out.append(chr(int(s[k + 2 : k + 4], 16)))
                k += 4
                continue
            out.append(_DQ_ESC.get(nxt, nxt))
            k += 2
            continue
        out.append(ch)
        k += 1
    return "".join(out)


def encode_double(key: str, key_indent: int, value: str) -> List[str]:
    """Single-line double-quoted scalar; exact for any string."""
    esc = []
    for ch in value:
        if ch == "\\":
            esc.append("\\\\")
        elif ch == '"':
            esc.append('\\"')
        elif ch == "\n":
            esc.append("\\n")
        elif ch == "\r":
            esc.append("\\r")
        elif ch == "\t":
            esc.append("\\t")
        elif ord(ch) < 0x20:
            esc.append("\\x%02x" % ord(ch))
        else:
            esc.append(ch)
    return [_key_prefix(key_indent, key) + ' "' + "".join(esc) + '"']


def encode(key: str, key_indent: int, value: str) -> List[str]:
    """Unity-style single-quoted folding when it decodes back exactly, else a double-quoted line."""
    lines = encode_single(key, key_indent, value)
    span = find_scalar(lines, key)
    if span is not None and span.value == value and span.end_line == len(lines):
        return lines
    return encode_double(key, key_indent, value)


def _closing_quote(text: str) -> Optional[int]:
    k = 0
    while k < len(text):
        if text[k] == "'":
            if k + 1 < len(text) and text[k + 1] == "'":
                k += 2
                continue
            return k
        k += 1
    return None


def _fold(raw: List[str]) -> str:
    out = raw[0]
    pending_breaks = 0
    for part in raw[1:]:
        if part == "":
            pending_breaks += 1
            continue
        out += "\n" * pending_breaks if pending_breaks else " "
        pending_breaks = 0
        out += part
    return out + "\n" * pending_breaks if pending_breaks else out


def encode_single(key: str, key_indent: int, value: str) -> List[str]:
    """Emit ``key: 'value'`` folded like Unity/libyaml (``yaml_emitter_write_single_quoted``)."""
    indent = key_indent + 2
    lines: List[str] = []
    cur = _key_prefix(key_indent, key) + " '"
    column = len(cur)
    breaks = False
    n = len(value)
    k = 0
    while k < n:
        ch = value[k]
        if ch == " ":
            if column >= BEST_WIDTH and k != 0 and k != n - 1 and value[k + 1] != " ":
                lines.append(cur)
                cur = " " * indent
                column = indent
            else:
                cur += ch
                column += 1
        elif ch == "\n":
            if not breaks:
                lines.append(cur)
                cur = ""
            lines.append(cur)
            cur = ""
            column = 0
            breaks = True
        else:
            if breaks:
                cur = " " * indent
                column = indent
            if ch == "'":
                cur += "''"
                column += 2
            else:
                cur += ch
                column += 1
            breaks = False
        k += 1
    if breaks:
        cur = " " * indent
    cur += "'"
    lines.append(cur)
    return lines
