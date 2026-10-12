"""Creating graph assets and moving subtrees between graphs (``new``, ``extract``, ``inline``, ``copy``)."""

from __future__ import annotations

import copy
import difflib
import re
import sys
import uuid
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from . import fsjson
from .container import ObjectRef, UnityFile
from .model import BT, DT, FSM, Graph, walk
from .schema import generic_args, simple_name
from .views import render_graph
from .yamlscalar import encode

GRAPH_TYPES = {"bt": BT, "fsm": FSM, "dt": DT}
DERIVED = {
    "bt": {"repeat": True, "$type": BT + "+DerivedSerializationData"},
    "fsm": None,
    "dt": {"actorParameters": [], "$type": DT + "+DerivedSerializationData"},
}
NESTED_KEYS = ("_subTree", "_nestedBT", "_nestedFSM", "_nestedDLG", "_subGraph")
WRITE_FIELDS = {
    "saveAs",
    "returnValue",
    "current",
    "storeIndex",
    "boolVariable",
    "floatVariable",
    "intVariable",
    "saveIndexAs",
    "saveDistanceAs",
    "saveObjectAs",
    "savePositionAs",
    "saveTo",
}
META = (
    "fileFormatVersion: 2\nguid: {guid}\nNativeFormatImporter:\n  externalObjects: {{}}\n"
    "  mainObjectFileID: 11400000\n  userData: \n  assetBundleName: \n  assetBundleVariant: \n"
)


class AssetError(Exception):
    pass


# ---------------------------------------------------------------------- new asset


def _template_lines(ws, kind: str) -> Tuple[List[str], str]:
    """Header/footer of an existing asset of the same kind (exact Unity layout), or a built-in one."""
    for s in ws.slots:
        if s.bound or not s.path.endswith(".asset"):
            continue
        try:
            g = ws.load(s)
        except Exception:
            continue
        if g.kind == kind:
            return list(ws.file(s.path).lines), s.path
    decl = ws.schema.find(GRAPH_TYPES[kind])
    script_guid = ws.project.path_to_guid(decl.file) if decl is not None else None
    if not script_guid:
        raise AssetError(f"cannot find the {GRAPH_TYPES[kind]} script in the project to reference from a new asset")
    lines = [
        "%YAML 1.1",
        "%TAG !u! tag:unity3d.com,2011:",
        "--- !u!114 &11400000",
        "MonoBehaviour:",
        "  m_ObjectHideFlags: 0",
        "  m_CorrespondingSourceObject: {fileID: 0}",
        "  m_PrefabInstance: {fileID: 0}",
        "  m_PrefabAsset: {fileID: 0}",
        "  m_GameObject: {fileID: 0}",
        "  m_Enabled: 1",
        "  m_EditorHideFlags: 0",
        f"  m_Script: {{fileID: 11500000, guid: {script_guid}, type: 3}}",
        "  m_Name: X",
        f"  m_EditorClassIdentifier: NodeCanvas::{GRAPH_TYPES[kind]}",
        "  _serializedGraph: '{}'",
        "  _objectReferences: []",
        "  _graphSource:",
        "    _version: 3.33",
        "    _category: ",
        "    _comments: ",
        "    _translation: {x: 0, y: 0}",
        "    _zoomFactor: 1",
        "  _haltSerialization: 0",
        "  _externalSerializationFile: {fileID: 0}",
        "",
    ]
    return lines, ""


def make_asset(ws, rel: str, kind: str, data: dict, refs: List[ObjectRef]) -> UnityFile:
    """Build (in memory) a new graph asset file at project-relative ``rel``."""
    lines, _ = _template_lines(ws, kind)
    out = []
    i = 0
    name = Path(rel).stem
    while i < len(lines):
        line = lines[i]
        st = line.lstrip(" ")
        if st.startswith("m_Name:"):
            out.append("  m_Name: " + name)
        elif st.startswith("_serializedGraph:"):
            out += encode("_serializedGraph", 2, fsjson.dumps(data))
            i += 1
            while i < len(lines) and not lines[i].lstrip(" ").startswith("_objectReferences:"):
                i += 1
            continue
        elif st.startswith("_objectReferences:"):
            out.append("  _objectReferences:" if refs else "  _objectReferences: []")
            out += ["  - " + r.dump() for r in refs]
            i += 1
            while i < len(lines) and lines[i].startswith("  - "):
                i += 1
            continue
        elif st.startswith("_translation:"):
            out.append("    _translation: {x: 0, y: 0}")
        elif st.startswith("_zoomFactor:"):
            out.append("    _zoomFactor: 1")
        elif st.startswith(("_category:", "_comments:")):
            out.append("    " + st.split(":")[0] + ": ")
        else:
            out.append(line)
        i += 1
    data_bytes = "\n".join(out).encode("utf-8")
    return UnityFile(ws.project.abs(rel), rel, data_bytes)


def empty_graph(kind: str) -> dict:
    return {
        "type": GRAPH_TYPES[kind],
        "nodes": [],
        "connections": [],
        "canvasGroups": [],
        "localBlackboard": {"_variables": {}},
        "derivedData": copy.deepcopy(DERIVED[kind]),
    }


def _check_new_path(ws, rel: str, allow_outside: bool = False) -> None:
    if not rel.endswith(".asset"):
        raise AssetError("the new graph path must end with .asset")
    if rel.startswith("/") or rel.startswith(".."):
        raise AssetError("the new graph must be inside the Unity project")
    if not (rel.startswith("Assets/") or rel.startswith("Packages/")):
        if not allow_outside:
            raise AssetError("the new graph must be inside Assets/ or Packages/ (or pass --allow-outside)")
        print(f"note: {rel} is outside Assets/ and Packages/; Unity will not import it", file=sys.stderr)
    if ws.project.abs(rel).exists():
        raise AssetError(f"{rel} already exists")


def _rel(ws, path: str) -> str:
    p = Path(path)
    return ws.project.rel(p if p.is_absolute() else Path.cwd() / p)


# ---------------------------------------------------------------------- copying subtrees


def subtree_nodes(g: Graph, root: dict) -> List[dict]:
    order, seen = [], set()

    def visit(n):
        if id(n) in seen:
            return
        seen.add(id(n))
        order.append(n)
        for k in g.children(n):
            visit(k)

    visit(root)
    inside = {id(n) for n in order}
    for n in order[1:]:
        outside = [p for p in g.parents(n) if id(p) not in inside]
        if outside:
            raise AssetError(
                f"#{g.nid(n)} also has a parent outside the subtree (#{g.nid(outside[0])}); "
                "move or remove that link first"
            )
    return order


def used_variables(g: Graph, nodes: List[dict]) -> Dict[str, str]:
    """``variable id -> "r" | "w" | "rw"`` for variables referenced by ``nodes``."""
    out: Dict[str, str] = {}
    for n in nodes:
        for path, d in walk(n):
            vid = d.get("_targetVariableID")
            if not vid or g.var_by_id(vid) is None:
                continue
            key = path[-1] if path else ""
            owner_type = _owner_type(n, path)
            write = key in WRITE_FIELDS or (key == "valueA" and simple_name(owner_type).startswith("Set"))
            if isinstance(key, int) and len(path) >= 2 and path[-2] == "_variablesMap":
                write = bool(d.get("_canRead"))
            mode = out.get(vid, "")
            out[vid] = "".join(sorted(set(mode + ("w" if write else "r"))))
    return out


def _owner_type(node: dict, path: Tuple) -> str:
    cur, t = node, node.get("$type", "")
    for p in path[:-1]:
        cur = cur[p]
        if isinstance(cur, dict) and "$type" in cur:
            t = cur["$type"]
    return t


def clone(
    src: Graph, dst: Graph, nodes: List[dict], var_map: Dict[str, dict], keep_uid: bool = True
) -> Tuple[List[dict], List[dict]]:
    """Deep-copy ``nodes`` (with their internal connections) for ``dst``: variable ids and object refs remapped.

    Returned connections use indices into the returned node list.
    """
    index = {id(n): i for i, n in enumerate(nodes)}
    copies = [copy.deepcopy(n) for n in nodes]
    for c in copies:
        c.pop("$id", None)
        if not keep_uid:
            c.pop("_UID", None)
        for _, d in walk(c):
            vid = d.get("_targetVariableID")
            if vid and vid in var_map:
                d["_targetVariableID"] = var_map[vid]["_id"]
                if "_name" in d:
                    d["_name"] = var_map[vid]["_name"]
        for k in NESTED_KEYS:
            v = c.get(k)
            if isinstance(v, dict) and isinstance(v.get("_value"), int):
                ref = src.ref(v["_value"])
                v["_value"] = dst.ref_index(ref) if ref is not None and not ref.is_null else 0
    conns = []
    for n in nodes:
        for c in src.out.get(src.nid(n), []):
            t = src.by_id[c["_targetNode"]["$ref"]]
            if id(t) in index:
                cc = copy.deepcopy(c)
                cc.pop("$id", None)
                cc["_sourceNode"] = {"$ref": str(index[id(n)])}
                cc["_targetNode"] = {"$ref": str(index[id(t)])}
                conns.append(cc)
    return copies, conns


def graft(ed, copies: List[dict], conns: List[dict], parent: Optional[dict], index: Optional[int]) -> dict:
    """Append cloned nodes to the editor's graph and hang their root under ``parent``."""
    g = ed.g
    base = len(g.nodes)
    g.nodes.extend(copies)
    for c in conns:
        c["_sourceNode"]["$ref"] = str(base + int(c["_sourceNode"]["$ref"]))
        c["_targetNode"]["$ref"] = str(base + int(c["_targetNode"]["$ref"]))
        g.connections.append(c)
    g.reindex()
    root = copies[0]
    if parent is not None:
        ed._link(parent, root, index)
    return root


# ---------------------------------------------------------------------- commands


def _print_views(ws, before: Dict[str, List[str]], graphs: List[Graph]) -> None:
    for g in graphs:
        addr = g.loaded.slot.address
        after = [re.sub(r"#\d+", "#", line) for line in render_graph(g, ws.renderer(g), with_header=True)[1:]]
        print(f"== {addr}")
        if addr in before:
            diff = list(difflib.unified_diff(before[addr], after, "before", "after", n=1, lineterm=""))
            print("\n".join(diff[2:]) if diff else "(no visible change)")
        else:
            print("\n".join(after))


def _view(ws, g: Graph) -> List[str]:
    return [re.sub(r"#\d+", "#", line) for line in render_graph(g, ws.renderer(g), with_header=True)[1:]]


def _new_graph(ws, rel: str, kind: str) -> Tuple[UnityFile, Graph]:
    f = make_asset(ws, rel, kind, empty_graph(kind), [])
    slot = f.slots()[0]
    return f, Graph(f.load(slot))


def _finish(ws, a, created: List[Tuple[UnityFile, Graph, str]], changed: List[Graph]) -> int:
    from .lint import ERROR, lint_graph

    errors = [i for g in [c[1] for c in created] + changed for i in lint_graph(ws, g) if i.severity == ERROR]
    for i in errors:
        print("lint: " + i.line())
    if errors and not getattr(a, "force", False):
        print("not saved: new lint errors (use --force)", file=sys.stderr)
        return 1
    if a.dry_run:
        print("dry run: nothing written")
        return 0
    for f, g, guid in created:
        f.store(g.loaded)
        f.abspath.parent.mkdir(parents=True, exist_ok=True)
        f.dirty = True
        f.save()
        Path(str(f.abspath) + ".meta").write_text(META.format(guid=guid), encoding="utf-8")
        where = f.relpath if f.relpath.startswith(("Assets/", "Packages/")) else str(f.abspath)
        print(f"created {where} (guid {guid})")
    for g in changed:
        ws.save(g)
        print(f"saved {g.loaded.slot.address}")
    ws.project._containers = None
    ws._slots = None
    return 0


def cmd_new(ws, a) -> int:
    from .build import Builder

    rel = _rel(ws, a.path)
    _check_new_path(ws, rel, a.allow_outside)
    f, g = _new_graph(ws, rel, a.kind)
    b = Builder(ws, g)
    for spec in a.var or []:
        m = re.match(r"^\s*([^:=]+?)\s*:\s*([^=:]+?)\s*(?:=\s*([^:]*?))?\s*(:\s*pub(?:lic)?)?\s*$", spec)
        if not m:
            raise AssetError(f"--var {spec!r}: use Name:type[=value][:pub]")
        value = None
        if m.group(3):
            import json as _json

            try:
                value = _json.loads(m.group(3))
            except ValueError:
                value = m.group(3)
        g.blackboard["_variables"][m.group(1)] = b.new_variable(m.group(1), m.group(2), value, bool(m.group(4)))
    if a.source:
        if ":" not in a.source:
            raise AssetError("--from takes <graph>:<node>")
        src_name, node_ref = a.source.rsplit(":", 1)
        src = ws.graph(src_name)
        _, created = copy_subtree(ws, src, node_ref, g, "root", None, True, public_vars=not a.private_vars)
        if created:
            print("created variables: " + ", ".join("$" + n for n in created))
    _print_views(ws, {}, [g])
    return _finish(ws, a, [(f, g, uuid.uuid4().hex)], [])


def cmd_extract(ws, a) -> int:
    from .layout import Layouter
    from .ops import apply_ops, set_position

    g = ws.graph(a.graph, "bt")
    root = g.node(a.node)
    rel = _rel(ws, a.path)
    _check_new_path(ws, rel, a.allow_outside)
    nodes = subtree_nodes(g, root)
    usage = used_variables(g, nodes)
    before = {g.loaded.slot.address: _view(ws, g)}

    f, sub = _new_graph(ws, rel, "bt")
    sub.data["derivedData"] = copy.deepcopy(g.data.get("derivedData")) or sub.data["derivedData"]
    var_map: Dict[str, dict] = {}
    for name, v in g.variables.items():
        if v.get("_id") in usage:
            nv = {k: copy.deepcopy(x) for k, x in v.items() if k not in ("_isPublic", "$type")}
            nv["_isPublic"] = True
            nv["$type"] = v["$type"]
            sub.blackboard["_variables"][name] = nv
            var_map[v["_id"]] = nv
    copies, conns = clone(g, sub, nodes, var_map)
    sub.data["nodes"] = copies
    sub.data["connections"] = conns
    sub.reindex()
    rx, ry = Layouter.pos(root) or (0.0, 0.0)
    for c in copies:
        p = Layouter.pos(c) or (0.0, 0.0)
        set_position(c, p[0] - rx, p[1] - ry)
    sub.renumber()

    guid = uuid.uuid4().hex
    ws.register_new(f, sub, guid)
    ref_index = g.ref_index(ObjectRef(11400000, guid, 2))
    mapping = []
    order = [v.get("_id") for v in g.variables.values()]
    for vid, mode in sorted(usage.items(), key=lambda kv: order.index(kv[0]) if kv[0] in order else len(order)):
        v = g.var_by_id(vid)
        vt = generic_args(v.get("$type", "")) or [None]
        entry = {"_targetSubGraphVariableID": vid}
        if "w" in mode:
            entry["_canRead"] = True
        if "r" in mode or "w" not in mode:
            entry["_canWrite"] = True
        entry["_type"] = vt[0].split(",")[0].strip() if vt[0] and "[[" not in vt[0] else vt[0]
        entry["_name"] = v["_name"]
        entry["_targetVariableID"] = vid
        mapping.append(entry)
    spec = {"$type": "SubTree", "_subTree": {"raw": {"_value": ref_index}}, "_variablesMap": {"raw": mapping}}
    if a.name:
        spec["_name"] = a.name
    ops = [{"op": "remove", "node": g.nid(k), "recursive": True} for k in g.children(root)]
    ops.append({"op": "replace", "node": g.nid(root), "with": spec})
    apply_ops(ws, g, ops)
    _print_views(ws, before, [sub, g])
    return _finish(ws, a, [(f, sub, guid)], [g])


def cmd_inline(ws, a) -> int:
    from .ops import Editor, OpError

    g = ws.graph(a.graph, "bt")
    node = g.node(a.node)
    if simple_name(node.get("$type", "")) != "SubTree":
        raise AssetError(f"#{g.nid(node)} is not a SubTree")
    ref = g.ref((node.get("_subTree") or {}).get("_value"))
    sub = ws.ref_graph(ref) if ref is not None else None
    if sub is None or not sub.nodes:
        raise AssetError("the SubTree has no (or an empty) graph assigned")
    before = {g.loaded.slot.address: _view(ws, g)}
    var_map: Dict[str, dict] = {}
    literal = []
    for m in node.get("_variablesMap") or []:
        sid = m.get("_targetSubGraphVariableID")
        if m.get("_targetVariableID") and g.var_by_id(m["_targetVariableID"]) is not None:
            var_map[sid] = g.var_by_id(m["_targetVariableID"])
        elif "_value" in m:
            literal.append(next((v.get("_name") for v in sub.variables.values() if v.get("_id") == sid), sid))
    if literal:
        raise AssetError(
            f"mapped to constants: {', '.join(literal)}; give them parent variables first (map op), then inline"
        )
    created_vars = []
    for name, v in sub.variables.items():
        if v.get("_id") in var_map or "VariableSeperator" in v.get("$type", ""):
            continue
        new_name = name
        while g.var_by_name(new_name) is not None:
            new_name = f"{new_name} ({sub.name})"
        nv = {k: copy.deepcopy(x) for k, x in v.items() if k not in ("_isPublic",)}
        nv["_name"] = new_name
        nv["_id"] = v["_id"] if g.var_by_id(v["_id"]) is None else str(uuid.uuid4())
        g.blackboard["_variables"][new_name] = nv
        var_map[v["_id"]] = nv
        created_vars.append(new_name)
    nodes = subtree_nodes(sub, sub.prime)
    copies, conns = clone(sub, g, nodes, var_map)
    ed = Editor(ws, g)
    parent = (g.parents(node) or [None])[0]
    old_pos = ed.lay.pos(node)
    index = g.children(parent).index(node) if parent is not None else None
    root = graft(ed, copies, conns, None, None)
    sub_root_pos = ed.lay.pos(root) or (0.0, 0.0)
    if old_pos is not None:
        ed.lay.translate([root], old_pos[0] - sub_root_pos[0], old_pos[1] - sub_root_pos[1])
    if parent is not None:
        _, _, item = ed._unlink(parent, node)
        ed._link(parent, root, index, item=item, has_item=item is not None)
    ed._remove_node(node)
    if parent is None:
        ri = g.nodes.index(root)
        g.nodes.insert(0, g.nodes.pop(ri))
        ed._reorder_refs_after_move(ri)
    ed.check_roots.append(root)
    try:
        ed.validate_structure()
    except OpError as e:
        raise AssetError(str(e)) from e
    ed.layout()
    ed.b.normalize(g.nodes)
    g.renumber()
    _print_views(ws, before, [g])
    if created_vars:
        print("created variables: " + ", ".join("$" + n for n in created_vars))
    return _finish(ws, a, [], [g])


def copy_subtree(
    ws,
    src: Graph,
    node_ref: str,
    dst: Graph,
    parent_ref: str,
    index: Optional[int],
    create_vars: bool,
    public_vars: bool = False,
):
    """Copy ``src``'s subtree at ``node_ref`` under ``parent_ref`` in ``dst`` (``root``: as the root of an
    empty graph). Returns ``(editor, created variable names)``."""
    from .ops import Editor, OpError

    root = src.node(node_ref)
    nodes = subtree_nodes(src, root)
    usage = used_variables(src, nodes)
    var_map: Dict[str, dict] = {}
    missing = []
    for vid in usage:
        v = src.var_by_id(vid)
        same = dst.var_by_id(vid) if dst is src else None
        match = same or dst.var_by_name(v["_name"])
        if match is not None and match.get("$type") == v.get("$type"):
            var_map[vid] = match
        elif match is not None:
            raise AssetError(f"${v['_name']} exists in {dst.name} with another type")
        else:
            missing.append(v)
    if missing and not create_vars:
        raise AssetError(
            "missing in the destination: "
            + ", ".join(f"${v['_name']}" for v in missing)
            + " (pass --create-vars to add them)"
        )
    as_root = str(parent_ref).lower() == "root"
    if as_root and dst.nodes and dst.kind == "bt":
        raise AssetError(f"{dst.name} already has a root; give a parent node to attach the copy to")
    for v in missing:
        nv = copy.deepcopy(v)
        nv.pop("_isPublic", None)
        if public_vars:
            nv["_isPublic"] = True
        nv["$type"] = nv.pop("$type")
        if dst.var_by_id(nv["_id"]) is not None:
            nv["_id"] = str(uuid.uuid4())
        dst.blackboard["_variables"][nv["_name"]] = nv
        var_map[v["_id"]] = nv
    copies, conns = clone(src, dst, nodes, var_map, keep_uid=False)
    ed = Editor(ws, dst)
    was_empty = not dst.nodes
    parent = None if as_root else (dst.node(parent_ref) if dst is not src else ed.node(parent_ref))
    newroot = graft(ed, copies, conns, parent, index)
    if parent is None:
        if was_empty:
            p = ed.lay.pos(newroot) or (0.0, 0.0)
            ed.lay.translate([newroot], -p[0], -p[1])
        ed.check_roots.append(newroot)
    else:
        ed.place_roots.append(newroot)
    try:
        ed.validate_structure()
    except OpError as e:
        raise AssetError(str(e)) from e
    ed.layout()
    ed.b.normalize(dst.nodes)
    dst.renumber()
    return ed, [v["_name"] for v in missing]


def cmd_copy(ws, a) -> int:
    src = ws.graph(a.src)
    dst = src if a.dst in (a.src, ".") else ws.graph(a.dst)
    before = {dst.loaded.slot.address: _view(ws, dst)}
    ed, created = copy_subtree(ws, src, a.node, dst, a.parent, a.index, a.create_vars, a.public_vars)
    _print_views(ws, before, [dst])
    summary = ed.layout_summary()
    if summary:
        print(summary)
    if created:
        print("created variables: " + ", ".join("$" + n for n in created))
    return _finish(ws, a, [], [dst])


def _guard(fn):
    def run(ws, a):
        try:
            return fn(ws, a)
        except AssetError as e:
            print(f"error: {e}", file=sys.stderr)
            return 1

    return run


def register(sub) -> None:
    s = sub.add_parser("new", help="create a new graph asset (with .meta)")
    s.add_argument("path", help="Assets/.../Name.asset")
    s.add_argument("--kind", choices=["bt", "fsm", "dt"], required=True)
    s.add_argument("--var", action="append", help='blackboard variable "Name:type[=value][:pub]", repeatable')
    s.add_argument(
        "--from",
        dest="source",
        help="start with a copy of <graph>:<node>; its variables are created public, "
        "like extract does, so the graph is ready to be a SubTree",
    )
    s.add_argument("--private-vars", action="store_true", help="with --from: create the variables private")
    s.add_argument("--dry-run", action="store_true")
    s.add_argument("--force", action="store_true")
    s.add_argument(
        "--allow-outside", action="store_true", help="allow a path outside Assets/ and Packages/ (for experiments)"
    )
    s.set_defaults(func=_guard(cmd_new))

    s = sub.add_parser("extract", help="move a BT subtree into a new SubTree asset and reference it in place")
    s.add_argument("graph")
    s.add_argument("node", help="root of the subtree")
    s.add_argument("path", help="Assets/.../NewSubTree.asset")
    s.add_argument("--name", help="node name for the SubTree")
    s.add_argument("--dry-run", action="store_true")
    s.add_argument("--force", action="store_true")
    s.add_argument(
        "--allow-outside", action="store_true", help="allow a path outside Assets/ and Packages/ (for experiments)"
    )
    s.set_defaults(func=_guard(cmd_extract))

    s = sub.add_parser("inline", help="replace a SubTree node with a copy of its graph's nodes")
    s.add_argument("graph")
    s.add_argument("node", help="the SubTree node")
    s.add_argument("--dry-run", action="store_true")
    s.add_argument("--force", action="store_true")
    s.set_defaults(func=_guard(cmd_inline))

    s = sub.add_parser("copy", help="copy a subtree into another (or the same) graph")
    s.add_argument("src")
    s.add_argument("node")
    s.add_argument("dst", help="destination graph (or . for the same graph)")
    s.add_argument("parent", help="node in the destination to attach the copy to, or 'root' for an empty graph")
    s.add_argument("--index", type=int)
    s.add_argument("--create-vars", action="store_true", help="create missing variables in the destination")
    s.add_argument("--public-vars", action="store_true", help="make variables created by --create-vars public")
    s.add_argument("--dry-run", action="store_true")
    s.add_argument("--force", action="store_true")
    s.set_defaults(func=_guard(cmd_copy))
