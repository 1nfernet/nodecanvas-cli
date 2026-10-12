"""Static checks over graphs: missing types, dangling variables, broken reflection bindings,
broken subgraph links, structural arity, unreachable nodes."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import List, Optional

from .bindings import check_task
from .model import Graph, walk
from .schema import short_generic, simple_name

ERROR, WARNING, INFO = "error", "warning", "info"
NESTED_KEYS = ("_subTree", "_nestedBT", "_nestedFSM", "_nestedDLG", "_subGraph", "_subFSM", "_subDialogue")
_SKIP_TYPE_PREFIXES = ("System.", "UnityEngine.", "Unity.")


@dataclass
class Issue:
    graph: str
    at: str
    severity: str
    code: str
    message: str
    fix: Optional[dict] = None

    def line(self) -> str:
        text = f"{self.severity:<7} {self.at:<10} {self.code:<20} {self.message}"
        if self.fix is not None:
            text += "\n          fix: " + json.dumps(self.fix, ensure_ascii=False)
        return text


def lint_graph(ws, g: Graph) -> List[Issue]:
    issues: List[Issue] = []
    name = g.loaded.slot.address
    schema = ws.schema

    def add(at, sev, code, msg, fix=None):
        issues.append(Issue(name, at, sev, code, msg, fix))

    nc = schema.has_nodecanvas
    from . import owners as owners_mod

    cache = {}

    def external():
        if "ext" not in cache:
            cache["ext"] = {e.key: e for e in owners_mod.external_vars(ws, g)}
        return cache["ext"]

    def owned():
        return bool(owners_mod.index(ws).owners_of(g))

    for vname, v in g.variables.items():
        if "MissingVariableType" in v.get("$type", ""):
            mt = v.get("_missingType", "?")
            if schema.resolves(mt):
                add(
                    "bb",
                    INFO,
                    "recoverable-missing",
                    f"${vname}: saved as missing {short_generic(mt)}, but the type exists; "
                    "NodeCanvas restores it on load and drops the wrapper on the next save in the editor",
                )
            else:
                add("bb", ERROR, "missing-var-type", f"${vname}: type {short_generic(mt)} not found")

    for c in g.connections:
        for k in ("_sourceNode", "_targetNode"):
            ref = (c.get(k) or {}).get("$ref")
            if ref not in g.by_id:
                add("conn", ERROR, "dangling-connection", f"{k} refers to missing node #{ref}")

    for owner in list(g.nodes) + list(g.connections):
        at = g.owner_label(owner)
        for path, d in walk(owner):
            t = d.get("$type")
            if t and "Framework.Internal.Missing" in t:
                mt = d.get("_missingType", "?")
                if schema.resolves(mt):
                    add(
                        at,
                        INFO,
                        "recoverable-missing",
                        f"{simple_name(t)}: saved as missing {short_generic(mt)}, but the type exists; "
                        "NodeCanvas restores it on load and drops the wrapper on the next save in the editor",
                    )
                else:
                    add(at, ERROR, "missing-type", f"{simple_name(t)}: {short_generic(mt)} not found")
            elif (
                t
                and not t.startswith(_SKIP_TYPE_PREFIXES)
                and not t.startswith("NodeCanvas.Framework.Variable")
                and (nc or not t.startswith(("NodeCanvas.", "ParadoxNotion.")))
                and schema.find(t) is None
            ):
                add(at, WARNING, "unknown-type", f"{short_generic(t)} not found in project sources")
            vid = d.get("_targetVariableID")
            if vid:
                var = g.var_by_id(vid)
                vn = d.get("_name")
                if var is None:
                    ext = external().get((vid, vn))
                    if vn and "/" not in vn and g.var_by_name(vn) is not None:
                        add(
                            at,
                            WARNING,
                            "var-id-mismatch",
                            f"${vn}: id {vid[:8]} not found, a variable with this name exists",
                        )
                    elif ext is not None and ext.kind == "global":
                        add(at, INFO, "global-var", f"${vn} resolves to the GlobalBlackboard in {ext.where}")
                    elif ext is not None and ext.kind == "missing-global":
                        add(at, WARNING, "missing-global-var", f"${vn}: {ext.where}")
                    elif ext is not None and ext.kind == "owner":
                        add(at, INFO, "owner-var", f"${vn} comes from the owner's blackboard {ext.where}")
                    else:
                        add(
                            at,
                            ERROR,
                            "dangling-var",
                            f"${vn or '?'} ({vid[:8]}) is not on the blackboard"
                            + (" nor on its owners' blackboards" if owned() else ""),
                        )
                elif vn and var.get("_name") != vn:
                    add(at, INFO, "var-renamed", f"${vn} is now named ${var.get('_name')}")
            if isinstance(d.get("method"), dict) and isinstance(d.get("parameters"), list):
                for sev, code, msg, fix in check_task(ws, g, owner, path, d):
                    add(at, sev, code, msg, fix)
            bi = d.get("_baseInfo")
            if isinstance(bi, str) and "|" in bi:
                tname, member = bi.split("|")[0], bi.split("|")[1]
                kinds = schema.member_kinds(tname, member)
                if kinds is not None and not kinds:
                    from .builtin import UNITY_BASE_MEMBERS

                    bare = member[4:] if member.startswith(("get_", "set_")) else member
                    complete = schema.chain_is_complete(tname) or (
                        schema.chain_ends_in_unity(tname) and bare not in UNITY_BASE_MEMBERS
                    )
                    add(
                        at,
                        ERROR if complete else WARNING,
                        "missing-member",
                        f"{simple_name(tname)}.{member} not found in sources"
                        + ("" if complete else " (type has bases outside the project sources)"),
                    )
        if "_sourceNode" in owner:
            continue
        for k in NESTED_KEYS:
            p = owner.get(k)
            if not isinstance(p, dict):
                continue
            if "_value" in p:
                ref = g.ref(p["_value"])
                if ref is None or ref.is_null:
                    add(at, WARNING, "empty-subgraph", f"{k} is not assigned")
                    continue
                if ref.guid and ws.ref_path(ref) is None:
                    add(at, ERROR, "missing-subgraph", f"{k} points to a missing asset (guid {ref.guid})")
                    continue
                sub = ws.ref_graph(ref)
                if sub is not None:
                    ids = {v.get("_id") for v in sub.variables.values()}
                    for m in owner.get("_variablesMap") or []:
                        sid = m.get("_targetSubGraphVariableID")
                        if sid and sid not in ids:
                            add(at, WARNING, "stale-mapping", f"mapping to {sid[:8]} has no variable in {sub.name}")
            elif not (p.get("_name") or p.get("_targetVariableID")):
                add(at, WARNING, "empty-subgraph", f"{k} is not assigned")

    reach = set(g.reachable())
    for node in g.nodes:
        nid = g.nid(node)
        t = node.get("$type", "")
        at = f"#{nid}"
        kids = g.out.get(nid, [])
        parents = g.inc.get(nid, [])
        max_out = _int(schema.const_prop(t, "maxOutConnections"))
        max_in = _int(schema.const_prop(t, "maxInConnections"))
        role = schema.role(t)
        if max_out is not None and max_out >= 0 and len(kids) > max_out:
            add(at, ERROR, "too-many-children", f"{simple_name(t)} allows {max_out} outgoing, has {len(kids)}")
        if max_in is not None and max_in >= 0 and len(parents) > max_in:
            add(at, ERROR, "too-many-parents", f"{simple_name(t)} allows {max_in} incoming, has {len(parents)}")
        if g.kind == "bt":
            if simple_name(t) == "Merge" and len(parents) <= 1:
                add(at, INFO, "redundant-merge", 'Merge with a single parent can be removed (apply {"op": "simplify"})')
            if role == "decorator" and not kids:
                add(at, INFO, "empty-decorator", f"{simple_name(t)} has no child")
            if role == "composite" and not kids:
                add(at, WARNING, "empty-composite", f"{simple_name(t)} has no children")
        if nid not in reach:
            if max_in == 0:
                continue
            add(at, WARNING, "unreachable", f"{simple_name(t)} is not reachable from the start node")
        for fname, f in schema.fields(t).items():
            if any(a.endswith("AutoSortWithChildrenConnections") for a in f.attrs):
                lst = node.get(fname)
                if isinstance(lst, list) and len(lst) != len(kids):
                    add(at, ERROR, "child-list-mismatch", f"{fname} has {len(lst)} entries for {len(kids)} children")
    for at, sev, code, msg in owners_mod.exposed_issues(ws, g):
        add(at, sev, code, msg)
    issues.extend(_layout_issues(ws, g, name))
    issues.extend(_variable_contracts(ws, g, name))
    return issues


def parent_mappings(ws) -> dict:
    """``subgraph address -> {sub variable ids mapped by some parent}`` (computed once per run)."""
    cached = getattr(ws, "_parent_maps", None)
    if cached is not None:
        return cached
    out: dict = {}
    for pg in ws.all_graphs():
        for n in pg.nodes:
            for k in NESTED_KEYS:
                p = n.get(k)
                if isinstance(p, dict) and "_value" in p:
                    ref = pg.ref(p["_value"])
                    path = ws.ref_path(ref) if ref is not None else None
                    if path:
                        ids = out.setdefault(path, set())
                        ids.update(m.get("_targetSubGraphVariableID") for m in n.get("_variablesMap") or [])
    ws._parent_maps = out
    return out


def _variable_contracts(ws, g: Graph, name: str) -> List[Issue]:
    from .bindings import var_type
    from .refs import variable_usage

    out: List[Issue] = []
    used = variable_usage(g)
    mapped_by_parents = parent_mappings(ws).get(g.loaded.slot.path, set())
    from .owners import overridden_ids

    overridden = overridden_ids(ws, g)
    for vname, v in g.variables.items():
        if "VariableSeperator" in v.get("$type", ""):
            continue
        if v.get("_id") not in used and v.get("_id") not in mapped_by_parents and v.get("_id") not in overridden:
            out.append(
                Issue(
                    name,
                    "bb",
                    INFO,
                    "unused-var",
                    f"${vname} is not used by any node, parent mapping or owner override",
                )
            )
    for n in g.nodes:
        key = next((k for k in NESTED_KEYS if isinstance(n.get(k), dict) and "_value" in n[k]), None)
        if key is None:
            continue
        ref = g.ref(n[key]["_value"])
        sub = ws.ref_graph(ref) if ref is not None else None
        if sub is None:
            continue
        from .assets import used_variables

        sub_modes = used_variables(sub, list(sub.nodes))
        entries = {m.get("_targetSubGraphVariableID"): m for m in n.get("_variablesMap") or []}
        for sname, sv in sub.variables.items():
            if not sv.get("_isPublic") or "VariableSeperator" in sv.get("$type", ""):
                continue
            m = entries.get(sv.get("_id"))
            if m is None:
                # an input the subgraph only reads, with no default value of its own: nobody provides it
                from .bindings import is_reference_type

                vt = var_type(sv)
                if (
                    sub_modes.get(sv.get("_id")) == "r"
                    and "_value" not in sv
                    and vt
                    and is_reference_type(ws.schema, vt)
                ):
                    out.append(
                        Issue(
                            name,
                            f"#{g.nid(n)}",
                            INFO,
                            "unmapped-subgraph-var",
                            f"{sub.name}.${sname} ({short_generic(vt)}) is read inside but not mapped, "
                            "so it is null there",
                        )
                    )
                continue
            pv = g.var_by_id(m.get("_targetVariableID")) if m.get("_targetVariableID") else None
            if pv is not None and var_type(pv) and var_type(sv) and var_type(pv) != var_type(sv):
                from .views import var_type as shown_type

                out.append(
                    Issue(
                        name,
                        f"#{g.nid(n)}",
                        WARNING,
                        "mapping-type-mismatch",
                        f"{sub.name}.${sname} ({shown_type(sv, ws.schema)}) is mapped to "
                        f"${pv.get('_name')} ({shown_type(pv, ws.schema)})",
                    )
                )
    return out


NODE_MIN_SIZE = (80.0, 20.0)  # NodeCanvas editor: smallest node rect, so closer nodes always overlap


def _layout_issues(ws, g: Graph, name: str) -> List[Issue]:
    out: List[Issue] = []
    grid = ws.config.layout.grid
    pos = {}
    for n in g.nodes:
        p = n.get("_position")
        if isinstance(p, dict):
            pos[g.nid(n)] = (float(p.get("x", 0.0)), float(p.get("y", 0.0)))
    for nid, (x, y) in pos.items():
        if x % grid or y % grid:
            out.append(
                Issue(name, f"#{nid}", INFO, "off-grid", f"position ({x:g}, {y:g}) is not on the {grid:g} px grid")
            )
    if g.kind == "bt":
        for n in g.nodes:
            kids = [g.nid(k) for k in g.children(n)]
            xs = [pos[k][0] for k in kids if k in pos]
            if len(xs) == len(kids) > 1 and xs != sorted(xs):
                out.append(
                    Issue(
                        name,
                        f"#{g.nid(n)}",
                        WARNING,
                        "child-order",
                        "children are not left-to-right in execution order; dragging any of them in the "
                        "editor re-sorts them by x and changes the order",
                    )
                )
    items = sorted(pos.items(), key=lambda kv: (kv[1][1], kv[1][0]))
    w, h = NODE_MIN_SIZE
    for i, (a, (ax, ay)) in enumerate(items):
        for b, (bx, by) in items[i + 1 :]:
            if by - ay >= h:
                break
            if abs(bx - ax) < w:
                out.append(
                    Issue(
                        name,
                        f"#{a}",
                        WARNING,
                        "overlap",
                        f"overlaps #{b}: closer than the editor's minimum node size ({w:g}×{h:g} px)",
                    )
                )
    return out


def _int(v: Optional[str]) -> Optional[int]:
    try:
        return int(v) if v is not None else None
    except ValueError:
        return None


def cmd_lint(ws, a) -> int:
    graphs = (
        [ws.graph(q, a.kind) for q in a.graphs]
        if a.graphs
        else [g for g in ws.all_graphs() if a.kind is None or g.kind == a.kind]
    )
    issues: List[Issue] = []
    if not ws.schema.has_nodecanvas:
        issues.append(
            Issue(
                "(project)",
                "-",
                WARNING,
                "no-nodecanvas-src",
                "NodeCanvas sources not found: defaults, enums and arity checks are limited",
            )
        )
    for g in graphs:
        issues.extend(lint_graph(ws, g))
    if not a.graphs:
        from .owners import index

        for o in index(ws).owners:
            if o.graph is None and not o.bound:
                issues.append(
                    Issue(
                        o.address,
                        "owner",
                        WARNING,
                        "owner-missing-graph",
                        f"{o.cls} has no graph assigned and no bound graph",
                    )
                )
    levels = {INFO: 0, WARNING: 1, ERROR: 2}
    issues = [i for i in issues if levels[i.severity] >= levels[a.min]]
    if a.json:
        print(json.dumps([i.__dict__ for i in issues], ensure_ascii=False, indent=1))
    else:
        cur = None
        for i in issues:
            if i.graph != cur:
                cur = i.graph
                print(cur)
            print("  " + i.line())
        counts = {s: sum(1 for i in issues if i.severity == s) for s in (ERROR, WARNING, INFO)}
        print(f"{len(graphs)} graphs: {counts[ERROR]} errors, {counts[WARNING]} warnings, {counts[INFO]} info")
    return 1 if any(i.severity == ERROR for i in issues) else 0


def register(sub) -> None:
    s = sub.add_parser("lint", help="check graphs for broken references and structural problems")
    s.add_argument("graphs", nargs="*", help="graphs to check (default: all)")
    s.add_argument("--min", choices=[INFO, WARNING, ERROR], default=WARNING, help="lowest severity to report")
    s.add_argument("--kind", choices=["bt", "fsm", "dt"], help="only graphs of this kind")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_lint)
