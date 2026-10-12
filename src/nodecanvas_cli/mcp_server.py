"""``nodecanvas-cli mcp``: a stdio MCP server exposing the CLI to agents without the Claude Code skill.

Tools are thin wrappers that run the same commands in-process and return their text output (the
compact notation of ``show``). Commands that write files default to ``dry_run=True``; a write happens
only with an explicit ``dry_run=false``. The Unity project is fixed at start (``--project``).

Requires the optional dependency: ``pip install "nodecanvas-cli[mcp]"`` (Python 3.10+).
"""

from __future__ import annotations

import contextlib
import io
import json
import sys
from pathlib import Path
from typing import Any, List, Optional

NOTATION = (Path(__file__).with_name("notation.txt")).read_text(encoding="utf-8")

INSTRUCTIONS = f"""\
nodecanvas-cli reads, searches, lints and edits NodeCanvas graphs (Behaviour Trees, FSMs, Dialogue Trees)
of one Unity project. Graphs are named by file name (Bot_BT), unique prefix, path, or Prefab.prefab:GameObject
for bound graphs; pass kind="bt"|"fsm"|"dt" when a name is shared by graphs of different kinds.

Workflow for edits: show → note #ids (they change after every edit) → node (spec form of a similar node) →
apply(ops, dry_run=true) → read the diff → apply(ops, dry_run=false) → show again. Batches are atomic and
refused when they add lint errors. Before renaming C# members used by graphs: refs, then refactor_*.

Notation of show:
{NOTATION}
Reflected members print as @Var.Member with an agent override, Type.Member otherwise. FSMs print states with
transitions in order (--[condition]--> #3 Name), dialogue trees print a script."""


def run_cli(project: Optional[str], args: List[str], stdin: Optional[str] = None) -> str:
    """Run one CLI command in-process and return its output (stdout + stderr, exit code if non-zero)."""
    from .cli import main

    argv = (["--project", project] if project else []) + [str(a) for a in args]
    out, err = io.StringIO(), io.StringIO()
    old_stdin = sys.stdin
    try:
        if stdin is not None:
            sys.stdin = io.StringIO(stdin)
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = main(argv)
            except SystemExit as e:  # argparse errors
                code = e.code if isinstance(e.code, int) else 2
    finally:
        sys.stdin = old_stdin
    text = out.getvalue()
    if err.getvalue():
        text += ("\n" if text and not text.endswith("\n") else "") + err.getvalue()
    if code:
        text += f"\n(exit code {code})"
    return text.rstrip("\n") or "(no output)"


def _opt(flag: str, value: Any) -> List[str]:
    if value is None or value is False:
        return []
    if value is True:
        return [flag]
    return [flag, str(value)]


def build_server(project: Optional[str] = None):
    from mcp.server import MCPServer
    from mcp.types import ToolAnnotations

    server = MCPServer("nodecanvas-cli", instructions=INSTRUCTIONS)
    ro = ToolAnnotations(read_only_hint=True, open_world_hint=False)
    rw = ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=False)
    cli = lambda *a, stdin=None: run_cli(project, list(a), stdin)  # noqa: E731

    @server.tool(annotations=ro)
    def list_graphs(kind: Optional[str] = None) -> str:
        """All graphs: kind, node count, path, subgraphs used."""
        return cli("list", *_opt("--kind", kind))

    @server.tool(annotations=ro)
    def show(
        graph: str,
        node: Optional[str] = None,
        depth: Optional[int] = None,
        expand: bool = False,
        positions: bool = False,
        kind: Optional[str] = None,
    ) -> str:
        """A graph in compact notation (tree for BT, states for FSM, script for DT). node/depth limit the BT view,
        expand inlines subgraphs, positions appends editor coordinates @(x,y)."""
        return cli(
            "show",
            graph,
            *_opt("--node", node),
            *_opt("--depth", depth),
            *_opt("--expand", expand),
            *_opt("--positions", positions),
            *_opt("--kind", kind),
        )

    @server.tool(annotations=ro)
    def node(graph: str, ids: List[str], raw: bool = False, kind: Optional[str] = None) -> str:
        """Nodes in the spec form apply accepts (copy one to add a similar node); raw=true for serialized JSON."""
        return cli("node", graph, *ids, *_opt("--raw", raw), *_opt("--kind", kind))

    @server.tool(annotations=ro)
    def vars(graph: str, kind: Optional[str] = None) -> str:
        """Blackboard variables, values, users; variables resolved through owners or global blackboards."""
        return cli("vars", graph, *_opt("--kind", kind))

    @server.tool(annotations=ro)
    def refs(query: str, kind: str = "auto") -> str:
        """Where a Type.Member, subgraph, $variable or task/node type is used, across all graphs."""
        return cli("refs", query, "--kind", kind)

    @server.tool(annotations=ro)
    def lint(graphs: Optional[List[str]] = None, min_severity: str = "warning") -> str:
        """Static checks (JSON list of issues): missing types/members/variables/subgraphs, arity, layout, owners."""
        return cli("lint", *(graphs or []), "--min", min_severity, "--json")

    @server.tool(annotations=ro)
    def types(query: Optional[str] = None, role: Optional[str] = None) -> str:
        """Task/node types in the project; an exact name prints fields, defaults and enum values.
        role: action|condition|bt|composite|decorator|fsm|dt."""
        return cli("types", *([query] if query else []), *_opt("--role", role))

    @server.tool(annotations=ro)
    def members(type: str, query: Optional[str] = None, all: bool = False) -> str:
        """Properties and methods of a C# type that reflection tasks can call."""
        return cli("members", type, *([query] if query else []), *_opt("--all", all))

    @server.tool(annotations=ro)
    def diff(graph: str, rev: str = "HEAD", positions: bool = False, kind: Optional[str] = None) -> str:
        """Compact before/after of a graph against a git revision."""
        return cli("diff", graph, "--rev", rev, *_opt("--positions", positions), *_opt("--kind", kind))

    @server.tool(annotations=ro)
    def why(graph: str, node: str, expand: bool = False, kind: Optional[str] = None) -> str:
        """What has to happen for control to reach a node (conditions along each path from the root,
        FSM transitions in, dialogue choices); expand continues through graphs nesting this one."""
        return cli("why", graph, node, *_opt("--expand", expand), *_opt("--kind", kind))

    @server.tool(annotations=ro)
    def paths(to: str, graph: Optional[str] = None, reads: bool = False) -> str:
        """`why` for every call of Type.Member or write of $Variable (reads=true: also reads)."""
        return cli("paths", *([graph] if graph else []), "--to", to, *_opt("--reads", reads))

    @server.tool(annotations=ro)
    def stats(graph: Optional[str] = None) -> str:
        """Size, depth, roles, most called members, shared subgraphs (JSON)."""
        return cli("stats", *([graph] if graph else []), "--json")

    @server.tool(annotations=ro)
    def owners(graph: Optional[str] = None) -> str:
        """GraphOwners (prefabs/scenes) running a graph with their blackboards and exposed overrides;
        without a graph, a table of every graph (graphs assigned nowhere are flagged)."""
        return cli("owners", *([graph] if graph else ["--all"]))

    @server.tool(annotations=ro)
    def blackboards(global_only: bool = False) -> str:
        """Blackboard / GlobalBlackboard components and AssetBlackboards with their variables."""
        return cli("blackboards", *_opt("--global", global_only))

    @server.tool(annotations=rw)
    def apply(
        graph: str, ops: List[dict], dry_run: bool = True, force: bool = False, kind: Optional[str] = None
    ) -> str:
        """Apply a batch of edit operations (JSON list, see the ops reference: add, remove, move, wrap, unwrap,
        replace, set, connect, var-add, map, transition-add, choice-add, ...). Ids refer to the graph before the
        batch. dry_run defaults to true: it prints the diff without writing; pass dry_run=false to save."""
        return cli(
            "apply",
            graph,
            "-",
            *_opt("--dry-run", dry_run),
            *_opt("--force", force),
            *_opt("--kind", kind),
            stdin=json.dumps(ops),
        )

    @server.tool(annotations=rw)
    def refactor_rename_member(
        target: str,
        new: str,
        params: Optional[str] = None,
        all_overloads: bool = False,
        graph: Optional[str] = None,
        dry_run: bool = True,
    ) -> str:
        """Rename Type.OldMember → NewMember in every graph (params picks an overload, e.g. "Unit,Vector3")."""
        return cli(
            "refactor",
            "rename-member",
            target,
            new,
            *_opt("--params", params),
            *_opt("--all-overloads", all_overloads),
            *_opt("--graph", graph),
            *_opt("--dry-run", dry_run),
        )

    @server.tool(annotations=rw)
    def refactor_rename_type(
        old: str, new: str, assembly: Optional[str] = None, graph: Optional[str] = None, dry_run: bool = True
    ) -> str:
        """Rename a C# type (full names; + for nested types) in every graph."""
        return cli(
            "refactor",
            "rename-type",
            old,
            new,
            *_opt("--assembly", assembly),
            *_opt("--graph", graph),
            *_opt("--dry-run", dry_run),
        )

    @server.tool(annotations=rw)
    def refactor_retarget(
        target: str,
        to: str,
        frm: Optional[str] = None,
        all: bool = False,
        graph: Optional[str] = None,
        dry_run: bool = True,
    ) -> str:
        """Move calls of Type.Method to the overload with parameter types `to` (e.g. "Booster")."""
        return cli(
            "refactor",
            "retarget",
            target,
            "--to",
            to,
            *_opt("--from", frm),
            *_opt("--all", all),
            *_opt("--graph", graph),
            *_opt("--dry-run", dry_run),
        )

    @server.tool(annotations=rw)
    def new_graph(
        path: str, kind: str, variables: Optional[List[str]] = None, source: Optional[str] = None, dry_run: bool = True
    ) -> str:
        """Create a graph asset (+ .meta). variables: "Name:type[=value][:pub]"; source: "<graph>:<node>" to copy."""
        var_args = [x for v in variables or [] for x in ("--var", v)]
        return cli("new", path, "--kind", kind, *var_args, *_opt("--from", source), *_opt("--dry-run", dry_run))

    @server.tool(annotations=rw)
    def extract(graph: str, node: str, path: str, name: Optional[str] = None, dry_run: bool = True) -> str:
        """Move a BT subtree into a new SubTree asset and put a SubTree node in its place."""
        return cli("extract", graph, node, path, *_opt("--name", name), *_opt("--dry-run", dry_run))

    @server.tool(annotations=rw)
    def inline(graph: str, node: str, dry_run: bool = True) -> str:
        """Replace a SubTree node with a copy of its graph's nodes."""
        return cli("inline", graph, node, *_opt("--dry-run", dry_run))

    @server.tool(annotations=rw)
    def copy(
        src: str,
        node: str,
        dst: str,
        parent: str,
        index: Optional[int] = None,
        create_vars: bool = False,
        dry_run: bool = True,
    ) -> str:
        """Copy a subtree into another (dst=".": the same) graph under `parent` ("root" for an empty graph)."""
        return cli(
            "copy",
            src,
            node,
            dst,
            parent,
            *_opt("--index", index),
            *_opt("--create-vars", create_vars),
            *_opt("--dry-run", dry_run),
        )

    @server.tool(annotations=ro)
    def verify_unity(graphs: Optional[List[str]] = None, changed: bool = False) -> str:
        """Load graphs in the open Unity Editor (Unity CLI + com.unity.pipeline) and report NodeCanvas errors."""
        return cli("verify", "--unity", *(graphs or []), *_opt("--changed", changed))

    return server


def cmd_mcp(ws, a) -> int:
    try:
        import mcp.server  # noqa: F401
    except ImportError:
        print(
            'error: the MCP server needs the optional dependency: pip install "nodecanvas-cli[mcp]" (Python 3.10+)',
            file=sys.stderr,
        )
        return 2
    build_server(str(ws.project.root)).run()
    return 0


def register(sub) -> None:
    s = sub.add_parser("mcp", help="run a stdio MCP server for agents (needs nodecanvas-cli[mcp])")
    s.set_defaults(func=cmd_mcp)
