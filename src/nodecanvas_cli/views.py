"""Text views of whole graphs: tree for BT, state list for FSM, script for Dialogue Trees."""

from __future__ import annotations

from typing import Callable, List, Optional, Set

from .model import Graph
from .render import Renderer, type_alias
from .schema import short_generic, simple_name


def header(g: Graph, r: Renderer, with_vars: bool = True) -> List[str]:
    kind = {"bt": "BT", "fsm": "FSM", "dt": "DT"}.get(g.kind, simple_name(g.type))
    flags = []
    dd = g.data.get("derivedData") or {}
    if g.kind == "bt":
        flags.append("repeat" if dd.get("repeat", True) else "once")
        if dd.get("updateInterval"):
            flags.append(f"interval={dd['updateInterval']}")
    lines = [f"{g.name}  [{', '.join([kind] + flags)}]  {g.loaded.slot.address}  nodes={len(g.nodes)}"]
    if with_vars and g.variables:
        vs = []
        for name, v in g.variables.items():
            t = var_type(v, r.schema)
            if t == "VariableSeperator":
                vs.append(f"[{name}]")
                continue
            vs.append(f"{name}:{t}{'(pub)' if v.get('_isPublic') else ''}")
        lines.append("vars: " + "  ".join(vs))
    if g.kind == "dt":
        actors = dd.get("actorParameters") or []
        if actors:
            lines.append("actors: " + ", ".join(a.get("_keyName", "?") for a in actors))
    return lines


def var_type(v: dict, schema=None) -> str:
    t = v.get("$type", "")
    if "MissingVariableType" in t:
        mt = v.get("_missingType", "?")
        if schema is not None and schema.resolves(mt):
            s = short_generic(mt)
            s = s[len("Variable<") : -1] if s.startswith("Variable<") else s
            return _alias_generic(s) + "(recovered)"
        return "MISSING " + short_generic(mt)
    s = short_generic(t)
    s = s[len("Variable<") : -1] if s.startswith("Variable<") else s
    return _alias_generic(s)


def _alias_generic(s: str) -> str:
    import re

    return re.sub(
        r"\b(Boolean|Single|Double|Int32|Int64|String|Byte|Char|UInt32)\b", lambda m: type_alias(m.group(1)), s
    )


def _conn_prefix(g: Graph, r: Renderer, parent: Optional[dict], node: dict, index: int) -> str:
    if parent is None:
        return ""
    c = g.connection(parent, node)
    tags = []
    if c is not None and c.get("_isDisabled"):
        tags.append("[off]")
    if simple_name(parent.get("$type", "")) == "ProbabilitySelector":
        w = parent.get("childWeights") or []
        if index < len(w):
            tags.append(f"(w={r.param(w[index], 'BBParameter<float>')})")
    return " ".join(tags) + (" " if tags else "")


def tree(
    g: Graph,
    r: Renderer,
    root_id: Optional[str] = None,
    depth: Optional[int] = None,
    expand: Optional[Callable[[dict], Optional[List[str]]]] = None,
) -> List[str]:
    lines: List[str] = []
    printed: Set[str] = set()

    def visit(node: dict, prefix: str, tail_prefix: str, parent: Optional[dict], index: int, level: int) -> None:
        nid = g.nid(node)
        label = _conn_prefix(g, r, parent, node, index)
        if nid in printed:
            lines.append(f"{prefix}#{nid} ↑ (see above)")
            return
        printed.add(nid)
        lines.append(f"{prefix}{label}#{nid} {_with_pos(r.node_label(node), r, node)}")
        if expand is not None:
            sub = expand(node)
            if sub:
                for s in sub:
                    lines.append(f"{tail_prefix}┆ {s}")
        kids = g.children(node)
        if depth is not None and level >= depth:
            if kids:
                lines.append(f"{tail_prefix}└─ … {len(kids)} children")
            return
        for i, k in enumerate(kids):
            last = i == len(kids) - 1
            visit(k, tail_prefix + ("└─" if last else "├─"), tail_prefix + ("  " if last else "│ "), node, i, level + 1)

    roots = [g.node(root_id)] if root_id is not None else ([g.prime] if g.prime else [])
    for root in roots:
        visit(root, "", "", (g.parents(root) or [None])[0] if root_id is not None else None, 0, 0)
    if root_id is None:
        orphans = [n for n in g.nodes if g.nid(n) not in printed]
        if orphans:
            lines.append("-- unreachable from root --")
            for n in orphans:
                if g.nid(n) in printed or g.parents(n):
                    continue
                visit(n, "", "", None, 0, 0)
            for n in orphans:
                if g.nid(n) not in printed:
                    visit(n, "", "", None, 0, 0)
    return lines


def state_list(
    g: Graph, r: Renderer, only: Optional[str] = None, expand: Optional[Callable[[dict], Optional[List[str]]]] = None
) -> List[str]:
    """FSM view: every state with its outgoing transitions in evaluation order."""
    lines: List[str] = []
    start = g.prime
    if start is not None and only is None:
        lines.append(f"start → #{'0'} {start.get('_name') or ''}".rstrip())
    nodes = [g.node(only)] if only is not None else g.nodes
    for n in nodes:
        lines.append(f"#{g.nid(n)} {_with_pos(r.node_label(n), r, n)}")
        if expand is not None:
            for s in expand(n) or []:
                lines.append(f"   ┆ {s}")
        for c in g.out.get(g.nid(n), []):
            dst = g.by_id.get(c["_targetNode"]["$ref"])
            cond = r.task(c.get("_condition")) if c.get("_condition") else "(always)"
            off = "[off] " if c.get("_isDisabled") else ""
            mode = c.get("_transitionCallMode")
            mode_s = f" mode={r.literal(mode, 'FSM.TransitionCallMode')}" if mode else ""
            dname = (dst.get("_name") or "") if dst else "?"
            lines.append(f"   {off}--[{cond}]--> #{c['_targetNode']['$ref']}{' ' + dname if dname else ''}{mode_s}")
    return lines


def dialogue(g: Graph, r: Renderer, only: Optional[str] = None) -> List[str]:
    """Dialogue view: one block per node with actor, text, choices and next nodes."""
    lines: List[str] = []
    nodes = [g.node(only)] if only is not None else g.nodes
    if g.prime is not None and only is None:
        lines.append(f"start → #{'0'}")
    for n in nodes:
        t = simple_name(n.get("$type", ""))
        actor = n.get("_actorName")
        who = (
            f"[{actor}] "
            if actor and t in ("StatementNode", "MultipleChoiceNode", "ActionNode", "ConditionNode")
            else ""
        )
        if t == "StatementNode":
            st = n.get("statement") or {}
            lines.append(f"#{g.nid(n)} {who}Say {_quote(st.get('_text', ''))}" + _meta(st) + r.at(n))
        elif t == "MultipleChoiceNode":
            lines.append(
                f"#{g.nid(n)} {who}Choice"
                + (f" [time={n['availableTime']}]" if n.get("availableTime") else "")
                + r.at(n)
            )
        elif t == "Jumper":
            uid = (n.get("_sourceNodeUID") or {}).get("_targetNodeUID")
            dst = next((g.nid(x) for x in g.nodes if uid and x.get("_UID") == uid), None)
            lines.append(f"#{g.nid(n)} Jump → " + (f"#{dst}" if dst is not None else f"<missing {uid}>") + r.at(n))
        else:
            lines.append(f"#{g.nid(n)} {who}{_with_pos(r.node_label(n), r, n)}")
        if t == "MultipleChoiceNode":
            kids = g.out.get(g.nid(n), [])
            for i, ch in enumerate(n.get("availableChoices") or []):
                st = ch.get("statement") or {}
                cond = f" if {r.task(ch['condition'])}" if ch.get("condition") else ""
                dst = kids[i]["_targetNode"]["$ref"] if i < len(kids) else "∅"
                lines.append(f"   {i + 1}) {_quote(st.get('_text', ''))}{cond} → #{dst}")
            continue
        if t == "Jumper":
            continue
        for i, c in enumerate(g.out.get(g.nid(n), [])):
            label = ""
            if t == "ConditionNode":
                label = "true " if i == 0 else "false "
            elif t == "MultipleConditionNode":
                conds = n.get("conditions") or []
                label = f"if {r.task(conds[i])} " if i < len(conds) else "else "
            off = "[off] " if c.get("_isDisabled") else ""
            lines.append(f"   {off}{label}→ #{c['_targetNode']['$ref']}")
    return lines


def _with_pos(label: str, r: Renderer, node: dict) -> str:
    at = r.at(node)
    if at and " // " in label:
        head, comment = label.split(" // ", 1)
        return f"{head}{at} // {comment}"
    return label + at


def _quote(text: str) -> str:
    t = text.replace("\n", "\\n")
    return f'"{t[:200]}{"…" if len(t) > 200 else ""}"'


def _meta(st: dict) -> str:
    return f" meta={st['_meta']}" if st.get("_meta") else ""


def render_graph(
    g: Graph,
    r: Renderer,
    node: Optional[str] = None,
    depth: Optional[int] = None,
    expand=None,
    with_header: bool = True,
) -> List[str]:
    lines = header(g, r) if with_header else []
    if g.kind == "fsm":
        lines += state_list(g, r, node, expand)
    elif g.kind == "dt":
        lines += dialogue(g, r, node)
    else:
        lines += tree(g, r, node, depth, expand)
    return lines
