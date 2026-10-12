"""Project-level access: graph lookup by name, loading, subgraph resolution, saving."""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional

from .config import load as load_config
from .container import ContainerError, ObjectRef, Slot, UnityFile
from .model import Graph, GraphError
from .project import Project, find_project_root
from .render import Renderer
from .schema import Schema

SUPPORTED_MAJOR = 3


class Workspace:
    def __init__(self, root: Optional[Path] = None):
        self.project = Project(find_project_root(root))
        self.schema = Schema(self.project)
        self.config = load_config(self.project.root)
        self._files: Dict[str, UnityFile] = {}
        self._graphs: Dict[str, Graph] = {}
        self._slots: Optional[List[Slot]] = None

    def close(self) -> None:
        self.schema.close()

    # ------------------------------------------------------------------ discovery

    def file(self, rel: str) -> UnityFile:
        if rel not in self._files:
            self._files[rel] = UnityFile(self.project.abs(rel), rel)
        return self._files[rel]

    @property
    def slots(self) -> List[Slot]:
        if self._slots is None:
            out = []
            for rel in self.project.containers:
                try:
                    out.extend(self.file(rel).slots())
                except (OSError, UnicodeDecodeError, ContainerError, ValueError):
                    continue
            self._slots = out
        return self._slots

    def _kind_of(self, slot: Slot) -> Optional[str]:
        try:
            return self.load(slot).kind
        except (GraphError, ContainerError):
            return None

    def find(self, query: str, kind: Optional[str] = None) -> Slot:
        """Resolve a graph query; ``kind`` (bt/fsm/dt) narrows the candidates first."""
        q = query.strip()
        pool = [s for s in self.slots if kind is None or self._kind_of(s) == kind]
        p = Path(q)
        if p.exists() or (self.project.root / q).exists():
            rel = self.project.rel(p if p.is_absolute() or p.exists() else self.project.root / q)
            slots = [s for s in pool if s.path == rel] or [
                s for s in self.file(rel).slots() if kind is None or self._kind_of(s) == kind
            ]
            if len(slots) == 1:
                return slots[0]
            if not slots:
                raise GraphError(f"{rel}: no NodeCanvas graph in this file")
            raise GraphError(f"{rel} holds several graphs, use one of: " + ", ".join(s.address for s in slots))
        exact = [s for s in pool if q in (s.address, s.name, s.path)]
        if len(exact) == 1:
            return exact[0]
        low = q.lower()
        ci = [s for s in pool if low in (s.address.lower(), s.name.lower())]
        if len(ci) == 1:
            return ci[0]
        word = [s for s in pool if s.name.lower().startswith((low + "_", low + " ", low + "-", low + "."))]
        if len(word) == 1:
            return word[0]
        sub = [s for s in pool if low in s.address.lower()]
        if len(sub) == 1:
            return sub[0]
        cands = exact or ci or word or sub
        if not cands:
            raise GraphError(f"no {kind + ' ' if kind else ''}graph matches {query!r} (try `nodecanvas-cli list`)")
        kinds = sorted({self._kind_of(s) or "?" for s in cands})
        hint = f"; narrow it with --kind {'/'.join(kinds)}" if kind is None and len(kinds) > 1 else ""
        raise GraphError(
            f"{query!r} is ambiguous: "
            + ", ".join(s.address for s in cands[:12])
            + (" …" if len(cands) > 12 else "")
            + hint
        )

    def load(self, slot: Slot) -> Graph:
        key = slot.address
        if key not in self._graphs:
            loaded = self.file(slot.path).load(slot)
            if loaded.version is not None and int(loaded.version) != SUPPORTED_MAJOR:
                raise GraphError(
                    f"{slot.address}: NodeCanvas serialization version {loaded.version} is not supported (3.x only)"
                )
            self._graphs[key] = Graph(loaded)
        return self._graphs[key]

    def graph(self, query: str, kind: Optional[str] = None) -> Graph:
        return self.load(self.find(query, kind))

    def all_graphs(self) -> List[Graph]:
        out = []
        for s in self.slots:
            try:
                out.append(self.load(s))
            except (GraphError, ContainerError):
                continue
        return out

    # ------------------------------------------------------------------ references

    def ref_path(self, ref: ObjectRef) -> Optional[str]:
        if ref.guid:
            return self.project.guid_to_path.get(ref.guid)
        return None

    def ref_graph(self, ref: ObjectRef) -> Optional[Graph]:
        path = self.ref_path(ref)
        if not path:
            return None
        slots = [s for s in self.slots if s.path == path and not s.bound]
        return self.load(slots[0]) if slots else None

    def ref_display(self, ref: ObjectRef) -> str:
        path = self.ref_path(ref)
        if path:
            return Path(path).stem
        if ref.guid:
            return f"<missing {ref.guid[:8]}>"
        return f"<local {ref.file_id}>"

    def sub_vars(self, ref: ObjectRef) -> Dict[str, str]:
        g = self.ref_graph(ref)
        if g is None:
            return {}
        return {v.get("_id"): v.get("_name", k) for k, v in g.variables.items()}

    def renderer(self, graph: Graph) -> Renderer:
        return Renderer(graph, self.schema, self.ref_display, self.sub_vars)

    def register_new(self, f, graph: Graph, guid: str) -> None:
        """Make a not-yet-written asset resolvable (by name and GUID) for the rest of this run."""
        self._files[f.relpath] = f
        self.slots.append(graph.loaded.slot)
        self._graphs[graph.loaded.slot.address] = graph
        self.project.guid_to_path[guid] = f.relpath

    # ------------------------------------------------------------------ saving

    def save(self, graph: Graph) -> None:
        path = graph.loaded.slot.path
        f = self.file(path)
        f.store(graph.loaded)
        f.save()
        self._files.pop(path, None)
        self._graphs = {k: v for k, v in self._graphs.items() if v.loaded.slot.path != path}
        self._slots = None
