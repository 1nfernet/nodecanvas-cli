"""In-memory view over a deserialized NodeCanvas graph."""

from __future__ import annotations

from typing import Dict, Iterable, List, Optional, Tuple

from .container import LoadedGraph, ObjectRef

BT = "NodeCanvas.BehaviourTrees.BehaviourTree"
FSM = "NodeCanvas.StateMachines.FSM"
DT = "NodeCanvas.DialogueTrees.DialogueTree"
KINDS = {BT: "bt", FSM: "fsm", DT: "dt"}


class GraphError(Exception):
    pass


def walk(obj, path=()):
    """Yield ``(path, dict)`` for every dict nested in ``obj``."""
    if isinstance(obj, dict):
        yield path, obj
        for k, v in obj.items():
            yield from walk(v, path + (k,))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from walk(v, path + (i,))


class Graph:
    def __init__(self, loaded: LoadedGraph):
        self.loaded = loaded
        self.data = loaded.data
        if not isinstance(self.data.get("nodes"), list):
            raise GraphError(f"{loaded.slot.address}: not a NodeCanvas graph")
        self.data.setdefault("connections", [])
        self._index()

    # ---------------------------------------------------------------- structure

    def _index(self) -> None:
        """Node ids are list indices; FullSerializer writes ``$id`` (== index) only on referenced nodes."""
        self.by_id: Dict[str, dict] = {str(i): n for i, n in enumerate(self.nodes)}
        self._ids = {id(n): str(i) for i, n in enumerate(self.nodes)}
        self.out: Dict[str, List[dict]] = {nid: [] for nid in self.by_id}
        self.inc: Dict[str, List[dict]] = {nid: [] for nid in self.by_id}
        for c in self.connections:
            s = c.get("_sourceNode", {}).get("$ref")
            t = c.get("_targetNode", {}).get("$ref")
            if s in self.out:
                self.out[s].append(c)
            if t in self.inc:
                self.inc[t].append(c)

    def nid(self, node: dict) -> str:
        return self._ids[id(node)]

    @property
    def type(self) -> str:
        return self.data.get("type", "")

    @property
    def kind(self) -> str:
        return KINDS.get(self.type, "graph")

    @property
    def name(self) -> str:
        return self.loaded.slot.name

    @property
    def nodes(self) -> List[dict]:
        return self.data["nodes"]

    @property
    def connections(self) -> List[dict]:
        return self.data["connections"]

    @property
    def prime(self) -> Optional[dict]:
        return self.nodes[0] if self.nodes else None

    def node(self, nid) -> dict:
        key = str(nid).lstrip("#")
        if key not in self.by_id:
            raise GraphError(f"{self.name}: no node #{key}")
        return self.by_id[key]

    def children(self, node: dict) -> List[dict]:
        return [
            self.by_id[c["_targetNode"]["$ref"]]
            for c in self.out.get(self.nid(node), [])
            if c["_targetNode"]["$ref"] in self.by_id
        ]

    def parents(self, node: dict) -> List[dict]:
        return [
            self.by_id[c["_sourceNode"]["$ref"]]
            for c in self.inc.get(self.nid(node), [])
            if c["_sourceNode"]["$ref"] in self.by_id
        ]

    def connection(self, src: dict, dst: dict) -> Optional[dict]:
        for c in self.out.get(self.nid(src), []):
            if c["_targetNode"]["$ref"] == self.nid(dst):
                return c
        return None

    def reachable(self) -> List[str]:
        seen: List[str] = []
        stack = ["0"] if self.prime else []
        while stack:
            nid = stack.pop()
            if nid in seen:
                continue
            seen.append(nid)
            stack.extend(reversed([c["_targetNode"]["$ref"] for c in self.out.get(nid, [])]))
        return seen

    # ---------------------------------------------------------------- blackboard

    @property
    def blackboard(self) -> dict:
        bb = self.data.get("localBlackboard")
        if not isinstance(bb, dict):
            bb = {"_variables": {}, "$type": "NodeCanvas.Framework.Internal.BlackboardSource"}
            self.data["localBlackboard"] = bb
        if not isinstance(bb.get("_variables"), dict):
            bb["_variables"] = {}
        return bb

    @property
    def variables(self) -> Dict[str, dict]:
        bb = self.data.get("localBlackboard")
        if isinstance(bb, dict) and isinstance(bb.get("_variables"), dict):
            return bb["_variables"]
        return {}

    def var_by_id(self, vid: str) -> Optional[dict]:
        for v in self.variables.values():
            if v.get("_id") == vid:
                return v
        return None

    def var_by_name(self, name: str) -> Optional[dict]:
        v = self.variables.get(name)
        if v is not None:
            return v
        for v in self.variables.values():
            if v.get("_name") == name:
                return v
        return None

    # ---------------------------------------------------------------- object references

    @property
    def refs(self) -> List[ObjectRef]:
        return self.loaded.refs

    def ref(self, index) -> Optional[ObjectRef]:
        try:
            i = int(index)
        except (TypeError, ValueError):
            return None
        return self.refs[i] if 0 <= i < len(self.refs) else None

    def ref_index(self, ref: ObjectRef) -> int:
        for i, r in enumerate(self.refs):
            if r.guid == ref.guid and r.file_id == ref.file_id:
                return i
        if not self.refs:
            self.refs.append(ObjectRef(0))
        self.refs.append(ref)
        return len(self.refs) - 1

    # ---------------------------------------------------------------- normalization

    def renumber(self) -> Dict[str, str]:
        """Reorder nodes and connections the way ``Graph.UpdateNodeIDs`` + serialization do.

        Returns the old→new id map.
        """
        order: List[str] = []

        def assign(nid: str) -> None:
            if nid in order:
                return
            order.append(nid)
            for c in self.out.get(nid, []):
                t = c["_targetNode"]["$ref"]
                if t in self.by_id:
                    assign(t)

        if self.prime is not None:
            assign("0")
        rest = [str(i) for i in range(len(self.nodes))]
        for nid in sorted(rest, key=lambda x: 0 if not self.inc.get(x) else 1):
            assign(nid)
        mapping = {old: str(i) for i, old in enumerate(order)}
        new_nodes = [self.by_id[old] for old in order]
        new_conns = []
        for old in order:
            new_conns.extend(self.out.get(old, []))
        for c in new_conns:
            for k in ("_sourceNode", "_targetNode"):
                c[k]["$ref"] = mapping.get(c[k]["$ref"], c[k]["$ref"])
        self.data["nodes"] = new_nodes
        self.data["connections"] = new_conns
        self._index()
        self.sync_ids()
        return mapping

    def sync_ids(self) -> None:
        """Write ``$id`` exactly on nodes referenced by a connection, as the last key."""
        referenced = set()
        for c in self.connections:
            referenced.add(c["_sourceNode"]["$ref"])
            referenced.add(c["_targetNode"]["$ref"])
        for i, n in enumerate(self.nodes):
            n.pop("$id", None)
            if str(i) in referenced:
                n["$id"] = str(i)

    def reindex(self) -> None:
        self._index()

    def iter_tasks(self) -> Iterable[Tuple[dict, tuple, dict]]:
        """Yield ``(owner, path, task)`` for every typed object inside nodes and connections."""
        for owner in list(self.nodes) + list(self.connections):
            for path, d in walk(owner):
                if path and "$type" in d:
                    yield owner, path, d

    def owner_label(self, owner: dict) -> str:
        if "_sourceNode" in owner:
            return f"#{owner['_sourceNode'].get('$ref')}->#{owner['_targetNode'].get('$ref')}"
        return f"#{self._ids.get(id(owner), '?')}"
