"""Reverse lookups across all graphs: members, subgraphs, variables, task/node types."""

from __future__ import annotations

import json
from typing import Dict, List

from .model import Graph, walk
from .schema import short_generic, simple_name


def variable_usage(g: Graph) -> Dict[str, List[str]]:
    """Map variable id -> list of ``#node`` / ``#a->#b`` labels that reference it."""
    out: Dict[str, List[str]] = {}
    for owner in list(g.nodes) + list(g.connections):
        label = g.owner_label(owner)
        for _, d in walk(owner):
            vid = d.get("_targetVariableID")
            if vid:
                lst = out.setdefault(vid, [])
                if label not in lst:
                    lst.append(label)
    return out


def _members(owner: dict):
    for path, d in walk(owner):
        bi = d.get("_baseInfo")
        if isinstance(bi, str):
            parts = bi.split("|")
            yield path, parts[0], parts[1] if len(parts) > 1 else ""


def find_refs(ws, query: str, kind: str = "auto"):
    """Yield ``(graph, label, what)`` hits for ``query``."""
    q = query.strip()
    ql = q.lower()
    member_q = q.split(".")[-1].lower()
    type_q = ".".join(q.split(".")[:-1]).lower() if "." in q else ""
    for g in ws.all_graphs():
        for owner in list(g.nodes) + list(g.connections):
            label = g.owner_label(owner)
            if kind in ("auto", "member"):
                for _, t, m in _members(owner):
                    mm = m[4:] if m.startswith(("get_", "set_")) else m
                    if mm.lower() == member_q or m.lower() == member_q:
                        if not type_q or simple_name(t).lower() == type_q.split(".")[-1] or t.lower() == type_q:
                            yield g, label, f"member {simple_name(t)}.{m}"
            if kind in ("auto", "type"):
                for _, d in walk(owner):
                    t = d.get("$type")
                    if t and (simple_name(t).lower() == ql or short_generic(t).lower() == ql or t.lower() == ql):
                        yield g, label, f"type {short_generic(t)}"
            if kind in ("auto", "var"):
                for _, d in walk(owner):
                    if d.get("_name") and "_targetVariableID" in d and d["_name"].lower() == ql.lstrip("$"):
                        yield g, label, f"var ${d['_name']}"
                        break
            if kind in ("auto", "graph"):
                for _, d in walk(owner):
                    for k, v in d.items():
                        if (
                            isinstance(v, dict)
                            and set(v) == {"_value"}
                            and isinstance(v["_value"], int)
                            and k.startswith("_")
                        ):
                            r = g.ref(v["_value"])
                            if (
                                r is not None
                                and not r.is_null
                                and ws.ref_display(r).lower() in (ql, ql.replace(".asset", ""))
                            ):
                                yield g, label, f"subgraph {ws.ref_display(r)}"


def cmd_refs(ws, a) -> int:
    hits = list(find_refs(ws, a.query, a.kind))
    if a.json:
        print(
            json.dumps(
                [{"graph": g.loaded.slot.address, "at": label, "what": what} for g, label, what in hits],
                ensure_ascii=False,
                indent=1,
            )
        )
        return 0
    if not hits:
        print(f"no references to {a.query!r}")
        return 0
    cur = None
    for g, label, what in hits:
        if g.loaded.slot.address != cur:
            cur = g.loaded.slot.address
            print(cur)
        r = ws.renderer(g)
        owner = g.node(label[1:]) if label.startswith("#") and "->" not in label else None
        detail = r.node_label(owner) if owner is not None else ""
        print(f"  {label:<10} {what:<28} {detail}")
    return 0


def register(sub) -> None:
    s = sub.add_parser("refs", help="find where a method/property, subgraph, variable or type is used")
    s.add_argument("query", help="Type.member, member, subgraph name, $variable or task/node type")
    s.add_argument("--kind", choices=["auto", "member", "graph", "var", "type"], default="auto")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_refs)
