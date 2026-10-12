"""Command line entry point."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List, Optional

from . import __version__
from .container import ContainerError
from .model import GraphError
from .project import ProjectError
from .views import render_graph
from .workspace import Workspace


def _out(lines: List[str]) -> None:
    sys.stdout.write("\n".join(lines) + ("\n" if lines else ""))


def cmd_list(ws: Workspace, a) -> int:
    rows = []
    for g in ws.all_graphs():
        if a.kind and g.kind != a.kind:
            continue
        subs = []
        for n in g.nodes:
            for k in ("_subTree", "_nestedBT", "_nestedFSM", "_nestedDLG", "_subGraph"):
                v = n.get(k)
                if isinstance(v, dict) and "_value" in v:
                    r = g.ref(v["_value"])
                    if r is not None and not r.is_null:
                        subs.append(ws.ref_display(r))
        rows.append((g, sorted(set(subs))))
    if a.json:
        print(
            json.dumps(
                [
                    {
                        "name": g.name,
                        "address": g.loaded.slot.address,
                        "kind": g.kind,
                        "nodes": len(g.nodes),
                        "subgraphs": s,
                    }
                    for g, s in rows
                ],
                ensure_ascii=False,
                indent=1,
            )
        )
        return 0
    for g, subs in rows:
        line = f"{g.kind:<4} {len(g.nodes):>4}  {g.loaded.slot.address}"
        if subs and not a.brief:
            line += "  uses: " + ", ".join(subs)
        print(line)
    return 0


def cmd_show(ws: Workspace, a) -> int:
    g = ws.graph(a.graph, a.kind)
    r = ws.renderer(g)
    r.positions = a.positions
    expand = None
    if a.expand:
        seen = {g.loaded.slot.address}

        def expand(node, level=[0]):
            for k in ("_subTree", "_nestedBT", "_nestedFSM", "_nestedDLG", "_subGraph"):
                v = node.get(k)
                if isinstance(v, dict) and "_value" in v:
                    ref = g.ref(v["_value"])
                    sub = ws.ref_graph(ref) if ref is not None else None
                    if sub is None or sub.loaded.slot.address in seen or level[0] >= a.expand_depth:
                        return None
                    seen.add(sub.loaded.slot.address)
                    level[0] += 1
                    try:
                        return render_graph(sub, ws.renderer(sub), expand=expand, with_header=True)
                    finally:
                        level[0] -= 1
                        seen.discard(sub.loaded.slot.address)
            return None

    from .owners import external_vars

    ext = external_vars(ws, g)
    r.resolved = {e.key for e in ext if e.kind in ("global", "owner")}
    lines = render_graph(g, r, a.node, a.depth, expand)
    if ext:
        from .views import header

        at = len(header(g, r))
        lines.insert(at, "external: " + "  ".join(f"${e.name}({EXT_KIND[e.kind]})" for e in ext))
    _out(lines)
    return 0


EXT_KIND = {"global": "global", "owner": "owner", "missing-global": "missing", "missing": "missing"}


def cmd_node(ws: Workspace, a) -> int:
    g = ws.graph(a.graph, a.kind)
    if a.raw:
        out = {}
        for nid in a.ids:
            n = g.node(nid)
            out[g.nid(n)] = {"node": n, "out": [c for c in g.out.get(g.nid(n), [])]}
        print(json.dumps(out, ensure_ascii=False, indent=1))
        return 0
    from .spec import Speccer

    sp = Speccer(ws, g)
    for nid in a.ids:
        n = g.node(nid)
        print(f"#{g.nid(n)} " + json.dumps(sp.node(n), ensure_ascii=False))
        outs = []
        for c in g.out.get(g.nid(n), []):
            extra = {k: v for k, v in c.items() if k not in ("_sourceNode", "_targetNode", "$type", "$id")}
            label = f"#{c['_targetNode']['$ref']}" + (" [off]" if extra.pop("_isDisabled", False) else "")
            if "_condition" in extra:
                label += " if " + json.dumps(sp.task(extra.pop("_condition")), ensure_ascii=False)
            if extra:
                label += " " + json.dumps(extra, ensure_ascii=False)
            outs.append(label)
        if outs:
            print("   → " + ", ".join(outs))
    return 0


def cmd_vars(ws: Workspace, a) -> int:
    from .refs import variable_usage

    g = ws.graph(a.graph, a.kind)
    usage = variable_usage(g)
    r = ws.renderer(g)
    from .views import var_type

    for name, v in g.variables.items():
        t = var_type(v, ws.schema)
        if t == "VariableSeperator":
            print(f"-- {name} --")
            continue
        val = ""
        if "_value" in v:
            val = " = " + r.literal(v["_value"], t)
        used = usage.get(v.get("_id"), [])
        flags = "pub " if v.get("_isPublic") else ""
        print(f"${name}: {t}{val}  {flags}id={v.get('_id', '?')[:8]}  used: {' '.join(used) if used else '-'}")
    from .owners import external_vars, index

    ext = external_vars(ws, g)
    if ext:
        print("-- referenced, not on this blackboard --")
        for e in ext:
            if e.kind in ("global", "owner"):
                t = var_type(e.var, ws.schema) if e.var else "?"
                src = f"{e.kind}: {e.where}"
            else:
                t, src = "?", "MISSING" + (f" ({e.where})" if e.where else "")
            print(f"${e.name}: {t}  {src}  used: {' '.join(e.users)}")
    for o in index(ws).owners_of(g):
        for ex in o.exposed:
            v = g.var_by_id(ex.var_id)
            if v is not None:
                from .owners import exposed_value

                print(f"${v.get('_name')} overridden by {o.address} = {exposed_value(ws, index(ws), o, ex)}")
    return 0


def cmd_diff(ws: Workspace, a) -> int:
    import difflib
    import re
    import subprocess

    from .container import UnityFile
    from .model import Graph

    g = ws.graph(a.graph, a.kind)
    slot = g.loaded.slot
    try:
        data = subprocess.run(
            ["git", "show", f"{a.rev}:./{slot.path}"], cwd=ws.project.root, capture_output=True, check=True
        ).stdout
    except FileNotFoundError:
        print("error: git is not available", file=sys.stderr)
        return 1
    except subprocess.CalledProcessError as e:
        print(f"error: git show {a.rev}:{slot.path}: {e.stderr.decode(errors='replace').strip()}", file=sys.stderr)
        return 1
    old_file = UnityFile(ws.project.abs(slot.path), slot.path, data)
    match = [
        s
        for s in old_file.slots()
        if s.graph_key == slot.graph_key and (s.owner, s.anchor) == (slot.owner, slot.anchor)
    ]
    if not match:
        print(f"error: {slot.address} does not exist at {a.rev}", file=sys.stderr)
        return 1
    old = Graph(old_file.load(match[0]))

    def lines(graph):
        r = ws.renderer(graph)
        r.positions = a.positions
        out = render_graph(graph, r, with_header=True)
        return out if a.keep_ids else [re.sub(r"#\d+", "#", line) for line in out]

    diff = list(difflib.unified_diff(lines(old), lines(g), f"{a.rev}", "working tree", n=a.context, lineterm=""))
    print("\n".join(diff) if diff else "(no changes)")
    from .bindings import lost_bindings_by_member

    for line in lost_bindings_by_member(old, g):
        print(f"warning: lost binding: {line}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="nodecanvas-cli", description="Inspect and edit NodeCanvas graphs in a Unity project."
    )
    p.add_argument("--version", action="version", version=f"nodecanvas-cli {__version__}")
    p.add_argument("--project", type=Path, help="Unity project root (default: search upwards from cwd)")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("list", help="list graphs in the project")
    s.add_argument("--kind", choices=["bt", "fsm", "dt"])
    s.add_argument("--brief", action="store_true", help="omit subgraph usage")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_list)

    s = sub.add_parser("show", help="print a graph in compact notation")
    s.add_argument("graph")
    s.add_argument("--node", help="start from this node id")
    s.add_argument("--depth", type=int, help="limit tree depth (BT)")
    s.add_argument("--expand", action="store_true", help="inline nested graphs")
    s.add_argument("--expand-depth", type=int, default=3)
    s.add_argument("--positions", action="store_true", help="append editor coordinates @(x,y) to every node")
    s.add_argument("--kind", choices=["bt", "fsm", "dt"], help="pick the graph of this kind when the name is ambiguous")
    s.set_defaults(func=cmd_show)

    s = sub.add_parser(
        "node", help="print nodes in the spec form `apply` accepts (or raw JSON) with outgoing connections"
    )
    s.add_argument("graph")
    s.add_argument("ids", nargs="+")
    s.add_argument("--raw", action="store_true", help="print the serialized JSON instead of the spec")
    s.add_argument("--kind", choices=["bt", "fsm", "dt"], help="pick the graph of this kind when the name is ambiguous")
    s.set_defaults(func=cmd_node)

    s = sub.add_parser("diff", help="compare a graph's compact view with a git revision")
    s.add_argument("graph")
    s.add_argument("--rev", default="HEAD", help="git revision to compare against (default: HEAD)")
    s.add_argument("--keep-ids", action="store_true", help="keep node ids (they shift when nodes are added or removed)")
    s.add_argument("--positions", action="store_true", help="include editor coordinates, so layout changes show up")
    s.add_argument("-U", "--context", type=int, default=2)
    s.add_argument("--kind", choices=["bt", "fsm", "dt"], help="pick the graph of this kind when the name is ambiguous")
    s.set_defaults(func=cmd_diff)

    s = sub.add_parser("vars", help="print the blackboard and where each variable is used")
    s.add_argument("graph")
    s.add_argument("--kind", choices=["bt", "fsm", "dt"], help="pick the graph of this kind when the name is ambiguous")
    s.set_defaults(func=cmd_vars)

    from . import refs as refs_mod

    refs_mod.register(sub)
    from . import lint as lint_mod

    lint_mod.register(sub)
    from . import ops as ops_mod

    ops_mod.register(sub)
    from . import discover as discover_mod

    discover_mod.register(sub)
    from . import refactor as refactor_mod

    refactor_mod.register(sub)
    from . import assets as assets_mod

    assets_mod.register(sub)
    from . import owners as owners_mod

    owners_mod.register(sub)
    from . import scaffold as scaffold_mod

    scaffold_mod.register(sub)
    from . import explain as explain_mod

    explain_mod.register(sub)
    from . import dialogue as dialogue_mod

    dialogue_mod.register(sub)
    from . import mcp_server as mcp_mod

    mcp_mod.register(sub)
    from . import verify as verify_mod

    verify_mod.register(sub)
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        ws = Workspace(args.project)
    except ProjectError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    from .assets import AssetError
    from .build import BuildError
    from .config import ConfigError
    from .ops import OpError
    from .refactor import RefactorError

    try:
        return args.func(ws, args) or 0
    except (GraphError, ContainerError, BuildError, OpError, AssetError, RefactorError, ConfigError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    finally:
        ws.close()


if __name__ == "__main__":
    sys.exit(main())
