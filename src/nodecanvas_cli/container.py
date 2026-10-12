"""Locating, reading and writing serialized graphs inside Unity YAML files.

Two container kinds exist:

* graph assets (``.asset``) — the ``Graph`` ScriptableObject keeps ``_serializedGraph``,
  ``_objectReferences`` and ``_graphSource``;
* bound graphs — a ``GraphOwner`` component inside a ``.prefab`` / ``.unity`` keeps
  ``_boundGraphSerialization``, ``_boundGraphObjectReferences`` and ``_boundGraphSource``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple

from . import fsjson
from .yamlscalar import encode, find_scalar

_DOC_RE = re.compile(r"^--- !u!(\d+) &(-?\d+)")
_REF_RE = re.compile(r"\{fileID: (-?\d+)(?:, guid: ([0-9a-f]{32}), type: (\d+))?\}")


class ContainerError(Exception):
    pass


@dataclass
class ObjectRef:
    file_id: int
    guid: Optional[str] = None
    type: Optional[int] = None

    @classmethod
    def parse(cls, text: str) -> "ObjectRef":
        m = _REF_RE.search(text)
        if not m:
            raise ContainerError(f"cannot parse object reference: {text!r}")
        return cls(int(m.group(1)), m.group(2), int(m.group(3)) if m.group(3) else None)

    def dump(self) -> str:
        if self.guid:
            return f"{{fileID: {self.file_id}, guid: {self.guid}, type: {self.type}}}"
        return f"{{fileID: {self.file_id}}}"

    @property
    def is_null(self) -> bool:
        return self.file_id == 0 and not self.guid


@dataclass
class Slot:
    """One serialized graph inside a file."""

    path: str  # project-relative
    graph_key: str
    refs_key: str
    source_key: str
    doc_start: int
    doc_end: int
    owner: Optional[str] = None  # GameObject name for bound graphs
    anchor: Optional[str] = None  # document fileID for bound graphs

    @property
    def bound(self) -> bool:
        return self.graph_key == "_boundGraphSerialization"

    @property
    def address(self) -> str:
        return f"{self.path}:{self.owner or self.anchor}" if self.bound else self.path

    @property
    def name(self) -> str:
        stem = Path(self.path).stem
        return f"{stem}:{self.owner or self.anchor}" if self.bound else stem


@dataclass
class LoadedGraph:
    slot: Slot
    data: dict
    refs: List[ObjectRef]
    version: Optional[float]
    file: "UnityFile" = field(repr=False, default=None)


def _documents(lines: List[str]) -> List[Tuple[int, int, str, str]]:
    starts = [(i, m.group(1), m.group(2)) for i, line in enumerate(lines) if (m := _DOC_RE.match(line))]
    if not starts:
        return [(0, len(lines), "", "")]
    docs = []
    for n, (i, cls, fid) in enumerate(starts):
        end = starts[n + 1][0] if n + 1 < len(starts) else len(lines)
        docs.append((i, end, cls, fid))
    return docs


def _value_of(lines: List[str], start: int, end: int, key: str) -> Optional[str]:
    for i in range(start, end):
        s = lines[i].lstrip(" ")
        if s.startswith(key + ":"):
            return s[len(key) + 1 :].strip()
    return None


class UnityFile:
    def __init__(self, abspath: Path, relpath: str, data: Optional[bytes] = None):
        self.abspath = abspath
        self.relpath = relpath
        raw = abspath.read_bytes() if data is None else data
        self.newline = "\r\n" if b"\r\n" in raw[:4096] else "\n"
        text = raw.decode("utf-8")
        self.lines = text.split(self.newline)
        self.dirty = False

    def slots(self) -> List[Slot]:
        out: List[Slot] = []
        docs = _documents(self.lines)
        go_names = {}
        for s, e, cls, fid in docs:
            if cls == "1":
                name = _value_of(self.lines, s, e, "m_Name")
                if name is not None:
                    go_names[fid] = name
        for s, e, cls, fid in docs:
            for gk, rk, sk in (
                ("_serializedGraph", "_objectReferences", "_graphSource"),
                ("_boundGraphSerialization", "_boundGraphObjectReferences", "_boundGraphSource"),
            ):
                span = find_scalar(self.lines, gk, s, e)
                if span is None or span.style == "empty":
                    continue
                owner = None
                if gk.startswith("_bound"):
                    go = _value_of(self.lines, s, e, "m_GameObject")
                    if go:
                        m = re.search(r"fileID: (-?\d+)", go)
                        owner = go_names.get(m.group(1)) if m else None
                out.append(Slot(self.relpath, gk, rk, sk, s, e, owner, fid or None))
        names = [x.owner for x in out if x.owner]
        for x in out:
            if x.owner and names.count(x.owner) > 1:
                x.owner = f"{x.owner}#{x.anchor}"
        return out

    def load(self, slot: Slot) -> LoadedGraph:
        span = find_scalar(self.lines, slot.graph_key, slot.doc_start, slot.doc_end)
        if span is None:
            raise ContainerError(f"{slot.address}: {slot.graph_key} not found")
        try:
            data = fsjson.loads(span.value)
        except ValueError as e:
            raise ContainerError(f"{slot.address}: graph JSON is invalid: {e}") from e
        refs = [ObjectRef.parse(t) for t in self._refs_lines(slot)[1]]
        version = None
        vi = self._find_key(slot.source_key, slot.doc_start, slot.doc_end)
        if vi is not None:
            v = _value_of(self.lines, vi + 1, min(vi + 8, slot.doc_end), "_version")
            try:
                version = float(v) if v else None
            except ValueError:
                version = None
        return LoadedGraph(slot, data, refs, version, self)

    def _find_key(self, key: str, start: int, end: int) -> Optional[int]:
        for i in range(start, end):
            s = self.lines[i].lstrip(" ")
            if s.startswith(key + ":"):
                return i
        return None

    def _refs_lines(self, slot: Slot) -> Tuple[Tuple[int, int, int], List[str]]:
        i = self._find_key(slot.refs_key, slot.doc_start, slot.doc_end)
        if i is None:
            raise ContainerError(f"{slot.address}: {slot.refs_key} not found")
        line = self.lines[i]
        indent = len(line) - len(line.lstrip(" "))
        rest = line.strip()[len(slot.refs_key) + 1 :].strip()
        if rest == "[]":
            return (i, i + 1, indent), []
        items = []
        j = i + 1
        while j < slot.doc_end:
            s = self.lines[j]
            st = s.lstrip(" ")
            if st.startswith("- ") and len(s) - len(st) == indent:
                items.append(st[2:])
                j += 1
            else:
                break
        return (i, j, indent), items

    def store(self, graph: LoadedGraph) -> None:
        slot = graph.slot
        (ri, rj, rindent), _ = self._refs_lines(slot)
        if graph.refs:
            new_refs = [" " * rindent + slot.refs_key + ":"] + [" " * rindent + "- " + r.dump() for r in graph.refs]
        else:
            new_refs = [" " * rindent + slot.refs_key + ": []"]
        span = find_scalar(self.lines, slot.graph_key, slot.doc_start, slot.doc_end)
        text = fsjson.dumps(graph.data)
        new_graph = encode(slot.graph_key, span.key_indent, text)
        edits = sorted([(span.start_line, span.end_line, new_graph), (ri, rj, new_refs)], reverse=True)
        delta = 0
        for a, b, repl in edits:
            self.lines[a:b] = repl
            delta += len(repl) - (b - a)
        slot.doc_end += delta
        self.dirty = True

    def save(self) -> None:
        if self.dirty:
            self.abspath.write_bytes(self.newline.join(self.lines).encode("utf-8"))
            self.dirty = False
