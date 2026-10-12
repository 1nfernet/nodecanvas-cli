"""Graph owners and blackboards in prefabs, scenes and assets (``owners``, ``blackboards``).

* ``GraphOwner`` components (BehaviourTreeOwner, FSMOwner, DialogueTreeController and project
  subclasses) keep ``_graph`` (asset reference), ``_blackboard`` (a Blackboard component) and
  ``_serializedExposedParameters`` — per-owner overrides of the graph's exposed variables.
* ``Blackboard`` / ``GlobalBlackboard`` components and ``AssetBlackboard`` assets keep variables in
  ``_serializedBlackboard`` (and ``_serializedVariables``, which wins when present).

Component classes are recognised through the schema (anything deriving from NodeCanvas's
GraphOwner/Blackboard), not by fixed names.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from . import fsjson
from .container import ObjectRef, _documents, _value_of
from .model import Graph, walk
from .project import Cache
from .schema import generic_args, short_generic
from .yamlscalar import find_scalar

MARKERS = (
    b"_serializedExposedParameters:",
    b"_serializedBlackboard:",
    b"propertyPath: _graph",
    b"propertyPath: _serializedExposedParameters",
)
ROLE_CLASSES = {
    "GraphOwner": "owner",
    "GlobalBlackboard": "global",
    "Blackboard": "blackboard",
    "AssetBlackboard": "asset",
}
_FILE_ID_RE = re.compile(r"fileID: (-?\d+)")


@dataclass
class Exposed:
    var_id: str
    type: str  # ExposedParameter`1[[T, Asm]]
    data: dict
    refs: List[ObjectRef]

    @property
    def value_type(self) -> Optional[str]:
        args = generic_args(self.type)
        return args[0] if args else None


@dataclass
class Component:
    path: str
    anchor: str  # document fileID
    go: str  # GameObject name ("" for assets)
    role: str  # owner / blackboard / global / asset
    cls: str

    @property
    def address(self) -> str:
        return f"{self.path}:{self.go}" if self.go else self.path


@dataclass
class Owner(Component):
    graph: Optional[ObjectRef] = None
    blackboard: Optional[ObjectRef] = None
    bound: bool = False
    exposed: List[Exposed] = field(default_factory=list)


@dataclass
class BB(Component):
    identifier: str = ""
    variables: Dict[str, dict] = field(default_factory=dict)
    refs: List[ObjectRef] = field(default_factory=list)
    parent: Optional[ObjectRef] = None

    def var(self, vid: Optional[str], name: Optional[str]) -> Optional[dict]:
        for v in self.variables.values():
            if vid and v.get("_id") == vid:
                return v
        for k, v in self.variables.items():
            if name and (v.get("_name", k) == name):
                return v
        return None


@dataclass
class InstanceOverride:
    """A PrefabInstance overriding an owner's ``_graph`` or exposed parameters (not parsed further)."""

    path: str
    anchor: str
    source: Optional[ObjectRef]
    graph: Optional[ObjectRef]
    exposed: bool

    @property
    def address(self) -> str:
        return self.path


# ---------------------------------------------------------------------------- YAML helpers


def _ref_of(lines, s, e, key) -> Optional[ObjectRef]:
    v = _value_of(lines, s, e, key)
    if not v or "fileID" not in v:
        return None
    try:
        r = ObjectRef.parse(v)
    except Exception:  # noqa: BLE001
        return None
    return None if r.is_null else r


def _list_at(lines: List[str], i: int, end: int) -> Tuple[List[Tuple[int, str]], int]:
    """Items of the block list under the key on line ``i``: ``[(line, text after '- ')]``, next line."""
    line = lines[i]
    indent = len(line) - len(line.lstrip(" "))
    if line.rstrip().endswith("[]"):
        return [], i + 1
    items, j = [], i + 1
    while j < end:
        st = lines[j].lstrip(" ")
        ind = len(lines[j]) - len(st)
        if st.startswith("- ") and ind == indent:
            items.append((j, st[2:]))
        elif ind <= indent and st:
            break
        j += 1
    return items, j


def _key_line(lines, s, e, key) -> Optional[int]:
    for i in range(s, e):
        st = lines[i].lstrip(" ")
        if st.startswith(key + ":") or st.startswith("- " + key + ":"):
            return i
    return None


def _ref_list(lines, s, e, key) -> List[ObjectRef]:
    i = _key_line(lines, s, e, key)
    if i is None:
        return []
    items, _ = _list_at(lines, i, e)
    out = []
    for _, text in items:
        try:
            out.append(ObjectRef.parse(text))
        except Exception:  # noqa: BLE001
            out.append(ObjectRef(0))
    return out


def _pairs(lines, s, e, key) -> List[Tuple[str, List[ObjectRef]]]:
    """``SerializationPair[]`` (``- _json: '...'`` + ``_references:``) as ``[(json, refs)]``."""
    i = _key_line(lines, s, e, key)
    if i is None:
        return []
    items, end = _list_at(lines, i, e)
    out = []
    starts = [ln for ln, _ in items] + [end]
    for n, ln in enumerate(starts[:-1]):
        stop = starts[n + 1]
        seg = lines[ln:stop]
        seg[0] = seg[0].replace("- ", "  ", 1)
        span = find_scalar(seg, "_json", 0, len(seg))
        text = span.value if span is not None else ""
        out.append((text, _ref_list(lines, ln + 1, stop, "_references")))
    return out


# ---------------------------------------------------------------------------- index


class Index:
    def __init__(self, ws):
        self.ws = ws
        self.owners: List[Owner] = []
        self.blackboards: List[BB] = []
        self.instances: List[InstanceOverride] = []
        self._files: Dict[str, List[str]] = {}
        self._scan()

    def _classify(self, script: Optional[ObjectRef], ident: Optional[str]) -> Optional[Tuple[str, str]]:
        schema = self.ws.schema
        decl = None
        path = self.ws.ref_path(script) if script is not None and script.guid else None
        if path and path.endswith(".cs"):
            decl = schema.find_simple(Path(path).stem)
        if decl is None and ident:
            name = ident.split("::")[-1].strip()
            decl = schema.find(name) or schema.find_simple(name.split(".")[-1])
        if decl is None:
            return None
        for c in schema.chain(decl):
            if c.name in ROLE_CLASSES and c.namespace == "NodeCanvas.Framework":
                return ROLE_CLASSES[c.name], decl.name
        return None

    def _candidates(self) -> List[str]:
        cache = Cache(self.ws.project.root, "owner-files")
        found, seen = [], []
        for f in self.ws.project.iter_files((".prefab", ".unity", ".asset")):
            key = self.ws.project.rel(f)
            seen.append(key)
            st = f.stat()
            has = cache.get(key, st)
            if has is None:
                with open(f, "rb") as fh:
                    data = fh.read()
                has = any(m in data for m in MARKERS)
                cache.put(key, st, has)
            if has:
                found.append(key)
        cache.prune(seen)
        cache.save()
        return sorted(found)

    def _scan(self) -> None:
        for rel in self._candidates():
            try:
                raw = self.ws.project.abs(rel).read_bytes()
                text = raw.decode("utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            lines = text.split("\r\n" if b"\r\n" in raw[:4096] else "\n")
            self._files[rel] = lines
            self._scan_file(rel, lines)

    def _scan_file(self, rel: str, lines: List[str]) -> None:
        docs = _documents(lines)
        go_names = {fid: _value_of(lines, s, e, "m_Name") or "" for s, e, cls, fid in docs if cls == "1"}
        for s, e, cls, fid in docs:
            if cls == "1001":
                self._scan_instance(rel, lines, s, e, fid)
                continue
            if cls != "114":
                continue
            body = "\n".join(lines[s:e])
            if "_serializedExposedParameters:" not in body and "_serializedBlackboard:" not in body:
                continue
            script = _ref_of(lines, s, e, "m_Script")
            ident = _value_of(lines, s, e, "m_EditorClassIdentifier")
            kind = self._classify(script, ident)
            if kind is None:
                kind = self._structural(lines, s, e, ident)
            if kind is None:
                continue
            role, cls_name = kind
            go_ref = _value_of(lines, s, e, "m_GameObject") or ""
            m = _FILE_ID_RE.search(go_ref)
            go = go_names.get(m.group(1), "") if m and m.group(1) != "0" else ""
            if role == "owner":
                self.owners.append(self._owner(rel, lines, s, e, fid, go, cls_name))
            else:
                self.blackboards.append(self._blackboard(rel, lines, s, e, fid, go, role, cls_name))

    @staticmethod
    def _structural(lines, s, e, ident) -> Optional[Tuple[str, str]]:
        """Fallback when the component class is not in the sources: decide by serialized fields."""
        name = (ident or "").split("::")[-1].split(".")[-1] or "?"
        if (
            _key_line(lines, s, e, "_serializedExposedParameters") is not None
            and _key_line(lines, s, e, "_graph") is not None
        ):
            return "owner", name
        if _key_line(lines, s, e, "_serializedBlackboard") is None:
            return None
        if _key_line(lines, s, e, "_identifier") is not None:
            return "global", name
        go = _value_of(lines, s, e, "m_GameObject") or ""
        return ("asset" if "fileID: 0}" in go else "blackboard"), name

    def _owner(self, rel, lines, s, e, fid, go, cls_name) -> Owner:
        span = find_scalar(lines, "_boundGraphSerialization", s, e)
        exposed = []
        for text, refs in _pairs(lines, s, e, "_serializedExposedParameters"):
            try:
                data = fsjson.loads(text)
            except ValueError:
                continue
            if isinstance(data, dict):
                exposed.append(Exposed(data.get("_targetVariableID", ""), data.get("$type", ""), data, refs))
        return Owner(
            rel,
            fid,
            go,
            "owner",
            cls_name,
            graph=_ref_of(lines, s, e, "_graph"),
            blackboard=_ref_of(lines, s, e, "_blackboard"),
            bound=span is not None and span.style != "empty" and bool(span.value),
            exposed=exposed,
        )

    def _blackboard(self, rel, lines, s, e, fid, go, role, cls_name) -> BB:
        variables: Dict[str, dict] = {}
        refs = _ref_list(lines, s, e, "_objectReferences")
        pairs = _pairs(lines, s, e, "_serializedVariables")
        if pairs:
            for text, _ in pairs:
                try:
                    v = fsjson.loads(text)
                except ValueError:
                    continue
                if isinstance(v, dict):
                    variables[v.get("_name", "?")] = v
        else:
            span = find_scalar(lines, "_serializedBlackboard", s, e)
            try:
                data = fsjson.loads(span.value) if span is not None and span.value else {}
            except ValueError:
                data = {}
            if isinstance(data, dict) and isinstance(data.get("_variables"), dict):
                variables = data["_variables"]
        if role == "asset":
            identifier = _value_of(lines, s, e, "m_Name") or Path(rel).stem
        elif role == "global":
            identifier = _value_of(lines, s, e, "_identifier") or go
        else:
            identifier = go
        return BB(
            rel,
            fid,
            go,
            role,
            cls_name,
            identifier=identifier,
            variables=variables,
            refs=refs,
            parent=_ref_of(lines, s, e, "_parentBlackboard"),
        )

    def _scan_instance(self, rel, lines, s, e, fid) -> None:
        graph, exposed = None, False
        i = _key_line(lines, s, e, "m_Modifications")
        if i is None:
            return
        items, _ = _list_at(lines, i, e)
        for n, (ln, _) in enumerate(items):
            stop = items[n + 1][0] if n + 1 < len(items) else e
            prop = _value_of(lines, ln, stop, "propertyPath") or ""
            if prop == "_graph":
                graph = _ref_of(lines, ln, stop, "objectReference")
            elif prop.startswith("_serializedExposedParameters"):
                exposed = True
        if graph is not None or exposed:
            self.instances.append(InstanceOverride(rel, fid, _ref_of(lines, s, e, "m_SourcePrefab"), graph, exposed))

    # ------------------------------------------------------------------ queries

    def owners_of(self, g: Graph) -> List[Owner]:
        slot = g.loaded.slot
        if slot.bound:
            return [o for o in self.owners if o.path == slot.path and o.anchor == slot.anchor]
        guid = self.ws.project.path_to_guid(slot.path)
        return [o for o in self.owners if o.graph is not None and o.graph.guid == guid]

    def instances_of(self, g: Graph) -> List[InstanceOverride]:
        guid = self.ws.project.path_to_guid(g.loaded.slot.path)
        return [i for i in self.instances if i.graph is not None and i.graph.guid == guid]

    def bb_at(self, ref: Optional[ObjectRef], context_path: str) -> Optional[BB]:
        if ref is None:
            return None
        if ref.guid:
            path = self.ws.ref_path(ref)
            return next(
                (b for b in self.blackboards if b.path == path and (b.role == "asset" or b.anchor == str(ref.file_id))),
                None,
            )
        return next((b for b in self.blackboards if b.path == context_path and b.anchor == str(ref.file_id)), None)

    def owner_blackboards(self, o: Owner) -> List[BB]:
        """The owner's blackboard and its parent AssetBlackboard chain."""
        out = []
        bb = self.bb_at(o.blackboard, o.path)
        while bb is not None and all(bb is not x for x in out):
            out.append(bb)
            bb = self.bb_at(bb.parent, bb.path)
        return out

    def globals_named(self, identifier: str) -> List[BB]:
        return [b for b in self.blackboards if b.role == "global" and b.identifier == identifier]

    def local_name(self, path: str, file_id: int) -> str:
        """``GameObject (Component)`` for a local object reference inside ``path``."""
        lines = self._files.get(path)
        if lines is None:
            return f"<local {file_id}>"
        docs = _documents(lines)
        go_names = {fid: _value_of(lines, s, e, "m_Name") or "" for s, e, cls, fid in docs if cls == "1"}
        for s, e, cls, fid in docs:
            if fid != str(file_id):
                continue
            if cls == "1":
                return go_names.get(fid, "?")
            go = _FILE_ID_RE.search(_value_of(lines, s, e, "m_GameObject") or "")
            ident = (_value_of(lines, s, e, "m_EditorClassIdentifier") or "").split("::")[-1].split(".")[-1]
            comp = ident or (lines[s + 1].rstrip(":") if s + 1 < len(lines) else "?")
            return f"{go_names.get(go.group(1), '?') if go else '?'} ({comp})"
        return f"<local {file_id}>"


def index(ws) -> Index:
    cached = getattr(ws, "_owners_index", None)
    if cached is None:
        cached = Index(ws)
        ws._owners_index = cached
    return cached


# ---------------------------------------------------------------------------- variable resolution


@dataclass
class External:
    name: str
    vid: Optional[str]
    users: List[str]
    kind: str  # global / owner / missing-global / missing
    where: str = ""
    var: Optional[dict] = None
    key: Tuple[Optional[str], Optional[str]] = (None, None)


def external_vars(ws, g: Graph) -> List[External]:
    """Variables referenced by the graph but not on its local blackboard, with where they resolve."""
    refs: Dict[Tuple[Optional[str], Optional[str]], List[str]] = {}
    for owner in list(g.nodes) + list(g.connections):
        label = g.owner_label(owner)
        for _, d in walk(owner):
            if "_targetVariableID" not in d and not (
                isinstance(d.get("_name"), str)
                and "/" in d["_name"]
                and set(d) <= {"_name", "_type", "_value", "$type"}
            ):
                continue
            vid, name = d.get("_targetVariableID"), d.get("_name")
            if (vid and g.var_by_id(vid) is not None) or (not vid and name and g.var_by_name(name) is not None):
                continue
            if vid and name and g.var_by_name(name) is not None:
                continue  # var-id-mismatch: a local variable of that name exists
            lst = refs.setdefault((vid, name), [])
            if label not in lst:
                lst.append(label)
    if not refs:
        return []
    idx = index(ws)
    out = []
    owners = idx.owners_of(g)
    for (vid, name), users in refs.items():
        display = name or (vid or "?")[:8]
        if name and "/" in name:
            ident, vname = name.split("/", 1)
            gbs = idx.globals_named(ident)
            hit = next(((b, b.var(None, vname)) for b in gbs if b.var(None, vname) is not None), None)
            if hit is not None:
                out.append(External(display, vid, users, "global", hit[0].address, hit[1], key=(vid, name)))
            elif gbs:
                out.append(
                    External(
                        display,
                        vid,
                        users,
                        "missing-global",
                        f'GlobalBlackboard "{ident}" ({gbs[0].address}) has no variable {vname}',
                        key=(vid, name),
                    )
                )
            else:
                out.append(
                    External(
                        display,
                        vid,
                        users,
                        "missing-global",
                        f'no GlobalBlackboard "{ident}" in the project',
                        key=(vid, name),
                    )
                )
            continue
        found = None
        for o in owners:
            for bb in idx.owner_blackboards(o):
                v = bb.var(vid, name)
                if v is not None:
                    found = (bb, v)
                    break
            if found:
                break
        if found is not None:
            bb, v = found
            out.append(External(display, vid, users, "owner", f"{bb.address} ({bb.cls})", v, key=(vid, name)))
        else:
            out.append(External(display, vid, users, "missing", key=(vid, name)))
    return out


def find_external_var(ws, g: Graph, name: str) -> Optional[dict]:
    """A variable named ``name`` outside the graph's blackboard: ``Ident/Var`` on a GlobalBlackboard, otherwise
    on an owner's blackboard (or its parent AssetBlackboard). Returned with ``_name`` as the graph writes it."""
    idx = index(ws)
    v = None
    if "/" in name:
        ident, vname = name.split("/", 1)
        v = next((b.var(None, vname) for b in idx.globals_named(ident) if b.var(None, vname) is not None), None)
    else:
        for o in idx.owners_of(g):
            v = next((bb.var(None, name) for bb in idx.owner_blackboards(o) if bb.var(None, name) is not None), None)
            if v is not None:
                break
    if v is None:
        return None
    out = dict(v)
    out["_name"] = name
    return out


def exposed_issues(ws, g: Graph) -> List[Tuple[str, str, str, str]]:
    """``(at, severity, code, message)`` for owner overrides of this graph's exposed variables."""
    from .views import var_type

    out = []
    for o in index(ws).owners_of(g):
        at = f"owner {o.address}"
        for ex in o.exposed:
            v = g.var_by_id(ex.var_id)
            if v is None:
                out.append(
                    (
                        at,
                        "warning",
                        "exposed-override-stale",
                        f"overrides variable {ex.var_id[:8]}, which is not on the graph's blackboard",
                    )
                )
                continue
            vt = generic_args(v.get("$type", ""))
            if ex.value_type and vt and _bare(ex.value_type) != _bare(vt[0]):
                out.append(
                    (
                        at,
                        "warning",
                        "exposed-override-type",
                        f"${v.get('_name')} is overridden as {_shown(ex.value_type)}, "
                        f"the variable is {var_type(v, ws.schema)}",
                    )
                )
    return out


def overridden_ids(ws, g: Graph) -> set:
    return {ex.var_id for o in index(ws).owners_of(g) for ex in o.exposed}


def _shown(t: Optional[str]) -> str:
    from .views import _alias_generic

    return _alias_generic(short_generic(t)) if t else "?"


def _bare(t: str) -> str:
    """Type name without assembly qualifiers (also inside generic arguments)."""
    args = generic_args(t)
    base = t.split("[")[0].split(",")[0].strip() if not args else t[: t.find("[")]
    return base + ("[" + ",".join(_bare(a) for a in args) + "]" if args else "")


# ---------------------------------------------------------------------------- rendering


def _bb_graph(bb: BB) -> Graph:
    from .container import LoadedGraph, Slot

    slot = Slot(bb.path, "_serializedBlackboard", "_objectReferences", "", 0, 0, bb.go or None, bb.anchor)
    return Graph(LoadedGraph(slot, {"nodes": [], "localBlackboard": {"_variables": bb.variables}}, bb.refs, None))


def var_lines(ws, bb: BB, indent: str = "  ") -> List[str]:
    from .views import var_type

    g = _bb_graph(bb)
    r = ws.renderer(g)
    out = []
    for name, v in bb.variables.items():
        t = var_type(v, ws.schema)
        if t == "VariableSeperator":
            out.append(f"{indent}-- {name} --")
            continue
        val = " = " + r.literal(v["_value"], t) if "_value" in v else ""
        out.append(f"{indent}${v.get('_name', name)}: {t}{val}")
    return out


def exposed_value(ws, idx: Index, o: Owner, ex: Exposed) -> str:
    if "_value" not in ex.data:
        return "default"
    val = ex.data["_value"]
    t = ex.value_type or ""
    if isinstance(val, int) and not isinstance(val, bool) and ex.refs and _is_object_type(ws, t):
        r = ex.refs[val] if 0 <= val < len(ex.refs) else None
        if r is None or r.is_null:
            return "null"
        if r.guid:
            return ws.ref_display(r)
        return idx.local_name(o.path, r.file_id)
    from .render import Renderer

    return Renderer(_bb_graph(BB(o.path, o.anchor, o.go, "blackboard", "")), ws.schema).literal(val, short_generic(t))


def _is_object_type(ws, t: str) -> bool:
    from .render import Renderer

    r = Renderer(_bb_graph(BB("", "", "", "blackboard", "")), ws.schema)
    return r._is_unity_object(t.split(",")[0], None)


def _graph_label(ws, ref: Optional[ObjectRef]) -> str:
    return ws.ref_display(ref) if ref is not None else "none"


def describe_owner(ws, idx: Index, o: Owner, g: Optional[Graph]) -> List[str]:
    bbs = idx.owner_blackboards(o)
    head = f"{o.address}  {o.cls}"
    if o.bound:
        head += "  (bound graph)"
    if bbs:
        parts = []
        for b in bbs:
            names = ", ".join("$" + v.get("_name", k) for k, v in b.variables.items()) or "no variables"
            parts.append(f"{b.cls} {b.address if b.role == 'asset' else ''}".strip() + f": {names}")
        head += "  bb: " + "; parent ".join(parts)
    elif o.blackboard is not None:
        head += "  bb: <not found>"
    lines = [head]
    for ex in o.exposed:
        v = g.var_by_id(ex.var_id) if g is not None else None
        name = "$" + v.get("_name") if v is not None else f"${ex.var_id[:8]}!"
        lines.append(f"  {name} = {exposed_value(ws, idx, o, ex)}  ({_shown(ex.value_type)})")
    return lines


def subgraph_parents(ws) -> Dict[str, List[str]]:
    """``asset path -> ["Parent #node", ...]`` for graphs used as subgraphs / nested graphs."""
    from .lint import NESTED_KEYS

    out: Dict[str, List[str]] = {}
    for pg in ws.all_graphs():
        for n in pg.nodes:
            for k in NESTED_KEYS:
                p = n.get(k)
                if isinstance(p, dict) and "_value" in p:
                    ref = pg.ref(p["_value"])
                    path = ws.ref_path(ref) if ref is not None else None
                    if path:
                        out.setdefault(path, []).append(f"{pg.name} #{pg.nid(n)}")
    return out


def cmd_owners(ws, a) -> int:
    idx = index(ws)
    if a.all or not a.graph:
        return _owners_all(ws, idx, a)
    g = ws.graph(a.graph, a.kind)
    owners = idx.owners_of(g)
    parents = subgraph_parents(ws).get(g.loaded.slot.path, []) if not g.loaded.slot.bound else []
    insts = idx.instances_of(g) if not g.loaded.slot.bound else []
    if a.json:
        print(
            json.dumps(
                {
                    "graph": g.loaded.slot.address,
                    "owners": [_owner_json(ws, idx, o, g) for o in owners],
                    "prefab_instances": [{"file": i.path, "source": _graph_label(ws, i.source)} for i in insts],
                    "subgraph_of": parents,
                },
                ensure_ascii=False,
                indent=1,
            )
        )
        return 0
    print(f"{g.name}  {g.loaded.slot.address}")
    for o in owners:
        for line in describe_owner(ws, idx, o, g):
            print("  " + line)
    for i in insts:
        print(
            f"  {i.path}  PrefabInstance of {_graph_label(ws, i.source)} overrides _graph "
            "(via PrefabInstance, not parsed further)"
        )
    if parents:
        print("  subgraph of: " + ", ".join(parents))
    if not owners and not insts and not parents:
        print("  not assigned to any owner or parent graph in the project's assets (may be assigned from code)")
    return 0


def _owner_json(ws, idx, o: Owner, g: Optional[Graph]) -> dict:
    out = {
        "at": o.address,
        "class": o.cls,
        "bound": o.bound,
        "graph": _graph_label(ws, o.graph) if o.graph else None,
        "blackboards": [
            {"at": b.address, "class": b.cls, "variables": list(b.variables)} for b in idx.owner_blackboards(o)
        ],
        "exposed": [],
    }
    for ex in o.exposed:
        v = g.var_by_id(ex.var_id) if g is not None else None
        out["exposed"].append(
            {
                "variable": v.get("_name") if v is not None else None,
                "id": ex.var_id,
                "type": _shown(ex.value_type),
                "value": exposed_value(ws, idx, o, ex),
            }
        )
    return out


def _owners_all(ws, idx: Index, a) -> int:
    parents = subgraph_parents(ws)
    rows = []
    for g in ws.all_graphs():
        if a.kind and g.kind != a.kind:
            continue
        owners = idx.owners_of(g)
        subs = parents.get(g.loaded.slot.path, []) if not g.loaded.slot.bound else []
        insts = idx.instances_of(g) if not g.loaded.slot.bound else []
        rows.append((g, owners, subs, insts))
    orphan_owners = [o for o in idx.owners if o.graph is None and not o.bound]
    if a.json:
        print(
            json.dumps(
                {
                    "graphs": [
                        {
                            "graph": g.loaded.slot.address,
                            "kind": g.kind,
                            "owners": [f"{o.address} ({o.cls})" for o in owners],
                            "prefab_instances": [i.path for i in insts],
                            "subgraph_of": subs,
                            "unassigned": not (owners or subs or insts),
                        }
                        for g, owners, subs, insts in rows
                    ],
                    "owners_without_graph": [f"{o.address} ({o.cls})" for o in orphan_owners],
                },
                ensure_ascii=False,
                indent=1,
            )
        )
        return 0
    width = max([len(g.name) for g, *_ in rows] + [10])
    for g, owners, subs, insts in rows:
        parts = [f"{o.address} ({o.cls})" for o in owners if not o.bound]
        parts += [f"bound to {o.go or o.address} ({o.cls})" for o in owners if o.bound]
        parts += [f"{i.path} (PrefabInstance override)" for i in insts]
        if subs:
            parts.append("subgraph of " + ", ".join(subs))
        print(
            f"{g.name:<{width}}  " + ("; ".join(parts) if parts else "— not assigned in assets (may be set from code)")
        )
    for o in orphan_owners:
        print(f"{'(no graph)':<{width}}  {o.address} ({o.cls}) has no graph assigned")
    return 0


def cmd_blackboards(ws, a) -> int:
    idx = index(ws)
    bbs = [b for b in idx.blackboards if not a.globals or b.role in ("global", "asset")]
    if a.json:
        print(
            json.dumps(
                [
                    {
                        "at": b.address,
                        "kind": b.role,
                        "class": b.cls,
                        "identifier": b.identifier,
                        "parent": _graph_label(ws, b.parent) if b.parent else None,
                        "variables": {
                            v.get("_name", k): {"type": short_generic(v.get("$type", "")), "id": v.get("_id")}
                            for k, v in b.variables.items()
                        },
                    }
                    for b in bbs
                ],
                ensure_ascii=False,
                indent=1,
            )
        )
        return 0
    if not bbs:
        print("no blackboards found")
        return 0
    for b in bbs:
        extra = f'  identifier="{b.identifier}"' if b.role == "global" else ""
        if b.parent is not None:
            pb = idx.bb_at(b.parent, b.path)
            extra += f"  parent={pb.address if pb else _graph_label(ws, b.parent)}"
        print(f"{b.address}  {b.cls}{extra}")
        lines = var_lines(ws, b)
        print("\n".join(lines) if lines else "  (no variables)")
    return 0


def register(sub) -> None:
    s = sub.add_parser("owners", help="where a graph runs: GraphOwners (prefabs/scenes) and parent graphs")
    s.add_argument("graph", nargs="?", help="graph to look up (omit or --all for a table of every graph)")
    s.add_argument("--all", action="store_true", help="table graph → owners; flags graphs assigned nowhere")
    s.add_argument("--kind", choices=["bt", "fsm", "dt"])
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_owners)

    s = sub.add_parser(
        "blackboards", help="Blackboard / GlobalBlackboard components and AssetBlackboards with their variables"
    )
    s.add_argument("--global", dest="globals", action="store_true", help="only global and asset blackboards")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_blackboards)
