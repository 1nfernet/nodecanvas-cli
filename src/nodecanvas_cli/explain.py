"""Explaining behaviour: ``why`` (what must happen for control to reach a node), ``paths`` (``why`` for every
call of a member / write of a variable) and ``stats``."""

from __future__ import annotations

import json
from collections import Counter
from typing import Dict, List, Optional, Tuple

from .lint import NESTED_KEYS
from .model import Graph, walk
from .schema import simple_name

MAX_PATHS = 50
LABEL_WIDTH = 56


# ---------------------------------------------------------------------------- BT


class BTExplainer:
    def __init__(self, ws, g: Graph):
        self.ws = ws
        self.g = g
        self.r = ws.renderer(g)

    def f(self, node: dict, name: str) -> str:
        t = node.get("$type", "")
        return self.r.field(node, name, self.ws.schema.fields(t), self.ws.schema.find(t))

    def paths(self, target: dict) -> List[List[Tuple[dict, Optional[dict]]]]:
        """Root → target paths as ``[(node, connection into the next node)]`` (the target's entry has ``None``)."""
        found: List[List[dict]] = []
        root = self.g.prime

        def back(node, suffix, seen):
            if len(found) >= MAX_PATHS:
                return
            chain = [node] + suffix
            if node is root:
                found.append(chain)
                return
            for c in self.g.inc.get(self.g.nid(node), []):
                parent = self.g.by_id.get(c["_sourceNode"]["$ref"])
                if parent is not None and id(parent) not in seen:
                    back(parent, chain, seen | {id(parent)})

        back(target, [], {id(target)})
        return [
            [(n, self.g.connection(n, p[i + 1]) if i + 1 < len(p) else None) for i, n in enumerate(p)] for p in found
        ]

    def label(self, node: dict) -> str:
        return f"#{self.g.nid(node)} {self.r.node_label(node)}"

    def step(self, parent: dict, child: dict, conn: Optional[dict]) -> str:
        """What has to happen at ``parent`` for control to pass to ``child``."""
        g = self.g
        kids = g.children(parent)
        idx = next((i for i, k in enumerate(kids) if k is child), 0)
        t = simple_name(parent.get("$type", ""))
        earlier = ", ".join(f"#{g.nid(k)}" for k in kids[:idx])
        off = "connection disabled: never runs; " if conn is not None and conn.get("_isDisabled") else ""
        dyn = " (dynamic: re-evaluated every tick)" if self.f(parent, "dynamic") == "true" else ""
        if t == "Sequencer":
            if self.f(parent, "random") == "true":
                return off + "children run in random order; the ones before it must succeed" + dyn
            return off + (f"{earlier} must succeed first" if idx else "first child") + dyn
        if t == "Selector":
            if self.f(parent, "random") == "true":
                return off + "children run in random order; the ones before it must fail" + dyn
            return off + (f"{earlier} must fail first" if idx else "first child") + dyn
        if t == "Parallel":
            return off + f"runs together with its siblings (policy {self.f(parent, 'policy')})" + dyn
        if t == "BinarySelector":
            return off + ("branch true: the condition holds" if idx == 0 else "branch false: the condition fails") + dyn
        if t == "ProbabilitySelector":
            weights = parent.get("childWeights") or []
            shown = [self.r.param(w, "BBParameter<float>") for w in weights]
            text = f"picked with weight {shown[idx]}" if idx < len(shown) else "picked by weight"
            try:
                nums = [float(x) for x in shown]
                if sum(nums) > 0:
                    text += f" ({nums[idx] / sum(nums):.0%})"
            except (ValueError, IndexError):
                pass
            fail = self.f(parent, "failChance")
            if fail not in ("0", "∅"):
                text += f", fail chance {fail}"
            return off + text + "; if it fails, another child is picked"
        if t == "StepIterator":
            return off + f"step {idx + 1} of {len(kids)} (one child per execution)"
        if t == "Switch":
            mode = self.f(parent, "selectionMode")
            case = self.f(parent, "enumCase" if mode == "EnumBased" else "intCase")
            return off + f"case {idx} of {case}" + dyn
        if t == "PrioritySelector":
            desires = parent.get("desires") or []
            name = desires[idx].get("name") if idx < len(desires) and isinstance(desires[idx], dict) else None
            return off + (f'desire "{name}": ' if name else "") + "the highest utility runs first, the next on failure"
        if t == "ConditionalEvaluator":
            return (
                off
                + "the condition must be true"
                + (" (re-checked every tick)" if self.f(parent, "isDynamic") == "true" else "")
            )
        if t == "WaitUntil":
            return off + "waits until the condition is true"
        if t == "Cooldown":
            return off + f"at most once per {_secs(self.f(parent, 'coolDownTime'))}"
        if t == "Repeater":
            mode = self.f(parent, "repeaterMode")
            if mode == "RepeatTimes":
                return off + f"repeated {self.f(parent, 'repeatTimes')} time(s)"
            if mode == "RepeatUntil":
                return off + f"repeated until it returns {self.f(parent, 'repeatUntilStatus')}"
            return off + "repeated forever"
        if t == "Iterator":
            cur = self.f(parent, "current")
            return (
                off
                + f"for each element of {self.f(parent, 'targetList')}"
                + (f", current={cur}" if cur not in ("∅", "null") else "")
            )
        if t in ("Inverter", "Optional", "Succeed", "Fail"):
            return (
                off
                + {
                    "Inverter": "result inverted",
                    "Optional": "result ignored",
                    "Succeed": "always succeeds",
                    "Fail": "always fails",
                }[t]
            )
        if t == "Timeout":
            return off + f"interrupted after {_secs(self.f(parent, 'timeout'))}"
        if t == "Guard":
            return off + f"only if no other Guard holds {self.f(parent, 'token')}"
        if t == "Merge":
            return off + "joins several branches"
        role = self.ws.schema.role(parent.get("$type", ""))
        if role == "decorator":
            return off + "decorated"
        return off + (f"child {idx + 1} of {len(kids)}" if len(kids) > 1 else "only child")

    def explain(self, target: dict) -> List[str]:
        paths = self.paths(target)
        if not paths:
            return [f"#{self.g.nid(target)} is not reachable from the root (#0)"]
        lines = []
        for n, p in enumerate(paths):
            if len(paths) > 1:
                lines.append(
                    f"path {n + 1}/{len(paths)}" + (" (stops at a disabled connection)" if _disabled(p) else "") + ":"
                )
            elif _disabled(p):
                lines.append("(stops at a disabled connection)")
            for i, (node, conn) in enumerate(p):
                if i + 1 < len(p):
                    lines.append(f"  {_cut(self.label(node))}  → {self.step(node, p[i + 1][0], conn)}")
                else:
                    lines.append(f"  {self.label(node)}")
        return lines


def _secs(v: str) -> str:
    try:
        float(v)
        return v + "s"
    except ValueError:
        return v


def _disabled(path) -> bool:
    return any(c is not None and c.get("_isDisabled") for _, c in path)


def _cut(text: str) -> str:
    text = text if len(text) <= LABEL_WIDTH else text[: LABEL_WIDTH - 1] + "…"
    return text.ljust(LABEL_WIDTH)


# ---------------------------------------------------------------------------- FSM / DT


def fsm_why(ws, g: Graph, target: dict) -> List[str]:
    r = ws.renderer(g)
    nid = g.nid(target)
    lines = []
    if target is g.prime:
        lines.append("start state: entered when the FSM starts")
    for c in g.inc.get(nid, []):
        src = g.by_id.get(c["_sourceNode"]["$ref"])
        if src is None:
            continue
        outs = g.out.get(g.nid(src), [])
        idx = outs.index(c)
        cond = r.task(c.get("_condition")) if c.get("_condition") else "(always)"
        anyst = simple_name(src.get("$type", "")) == "AnyState"
        who = f"#{g.nid(src)} {src.get('_name') or simple_name(src.get('$type', ''))}"
        text = f"  {'from any state via ' if anyst else 'from '}{who} --[{cond}]-->"
        notes = []
        if c.get("_isDisabled"):
            notes.append("disabled")
        if idx:
            notes.append(
                "after "
                + ", ".join(f"#{o['_targetNode']['$ref']}" for o in outs[:idx])
                + " transitions did not fire (checked in order)"
            )
        lines.append(text + (f"  ({'; '.join(notes)})" if notes else ""))
    if len(lines) == (1 if target is g.prime else 0):
        lines.append("  no transitions lead here" + ("" if target is g.prime else " — the state is never entered"))
    return lines


def dt_why(ws, g: Graph, target: dict) -> List[str]:
    r = ws.renderer(g)
    nid = g.nid(target)
    lines = []
    if target is g.prime:
        lines.append("  start of the dialogue")
    for c in g.inc.get(nid, []):
        src = g.by_id.get(c["_sourceNode"]["$ref"])
        if src is None:
            continue
        t = simple_name(src.get("$type", ""))
        idx = g.out.get(g.nid(src), []).index(c)
        off = " (disabled)" if c.get("_isDisabled") else ""
        if t == "MultipleChoiceNode":
            choices = src.get("availableChoices") or []
            ch = choices[idx] if idx < len(choices) else {}
            text = (ch.get("statement") or {}).get("_text", "")
            cond = f" if {r.task(ch['condition'])}" if ch.get("condition") else ""
            lines.append(f'  #{g.nid(src)} choice {idx + 1} "{text}"{cond}{off}')
        elif t == "ConditionNode":
            lines.append(
                f"  #{g.nid(src)} {'true' if idx == 0 else 'false'} branch of {r.task(src.get('_condition'))}{off}"
            )
        elif t == "MultipleConditionNode":
            conds = src.get("conditions") or []
            lines.append(f"  #{g.nid(src)} " + (f"if {r.task(conds[idx])}" if idx < len(conds) else "else") + off)
        else:
            lines.append(f"  after #{g.nid(src)} {r.node_label(src)}{off}")
    uid = target.get("_UID")
    for n in g.nodes:
        if (
            uid
            and simple_name(n.get("$type", "")) == "Jumper"
            and (n.get("_sourceNodeUID") or {}).get("_targetNodeUID") == uid
        ):
            lines.append(f"  jump from #{g.nid(n)}")
    if not lines:
        lines.append("  nothing leads here — the node is never reached")
    return lines


# ---------------------------------------------------------------------------- why


def subtree_users(ws, g: Graph) -> List[Tuple[Graph, dict]]:
    """``(parent graph, node)`` for every node that nests ``g``."""
    out = []
    if g.loaded.slot.bound:
        return out
    for pg in ws.all_graphs():
        for n in pg.nodes:
            for k in NESTED_KEYS:
                p = n.get(k)
                if isinstance(p, dict) and "_value" in p:
                    ref = pg.ref(p["_value"])
                    if ref is not None and ws.ref_path(ref) == g.loaded.slot.path:
                        out.append((pg, n))
    return out


def why_lines(ws, g: Graph, node: dict, expand: int = 0) -> List[str]:
    if g.kind == "fsm":
        body = fsm_why(ws, g, node)
    elif g.kind == "dt":
        body = dt_why(ws, g, node)
    else:
        body = BTExplainer(ws, g).explain(node)
    if expand <= 0:
        return body
    users = subtree_users(ws, g)
    if not users:
        return body
    out = []
    for pg, pn in users:
        out.append(f"via {pg.name} #{pg.nid(pn)}:")
        for line in why_lines(ws, pg, pn, expand - 1):
            out.append("  " + line)
        out.append(f"  ↳ inside {g.name}:")
        out.extend("    " + line for line in body)
    return out


def cmd_why(ws, a) -> int:
    g = ws.graph(a.graph, a.kind)
    node = g.node(a.node)
    r = ws.renderer(g)
    print(f"{g.name} #{g.nid(node)} {r.node_label(node)}")
    for line in why_lines(ws, g, node, a.expand_depth if a.expand else 0):
        print(line)
    return 0


# ---------------------------------------------------------------------------- paths


def _member_hits(owner: dict, query: str) -> bool:
    q = query.split(".")[-1].lower()
    tq = ".".join(query.split(".")[:-1]).lower()
    for _, d in walk(owner):
        bi = d.get("_baseInfo")
        if isinstance(bi, str) and "|" in bi:
            t, m = bi.split("|")[0], bi.split("|")[1]
            mm = m[4:] if m.startswith(("get_", "set_")) else m
            if q in (m.lower(), mm.lower()) and (
                not tq or simple_name(t).lower() == tq.split(".")[-1] or t.lower() == tq
            ):
                return True
    return False


def _var_hits(g: Graph, owner: dict, name: str, reads: bool) -> bool:
    from .assets import used_variables

    if "_sourceNode" in owner:  # FSM transition conditions only read
        if not reads:
            return False
        return any(d.get("_name") == name for _, d in walk(owner) if "_targetVariableID" in d)
    for vid, mode in used_variables(g, [owner]).items():
        v = g.var_by_id(vid)
        if v is not None and v.get("_name") == name and ("w" in mode or reads):
            return True
    if reads:
        return any(d.get("_name") == name for _, d in walk(owner) if "_targetVariableID" in d)
    return False


def cmd_paths(ws, a) -> int:
    graphs = [ws.graph(a.graph, a.kind)] if a.graph else [g for g in ws.all_graphs() if not a.kind or g.kind == a.kind]
    q = a.to
    found = 0
    for g in graphs:
        hits = []
        for owner in list(g.nodes) + list(g.connections):
            ok = _var_hits(g, owner, q[1:], a.reads) if q.startswith("$") else _member_hits(owner, q)
            if ok:
                hits.append(owner)
        if not hits:
            continue
        r = ws.renderer(g)
        print(f"== {g.loaded.slot.address}")
        for owner in hits:
            found += 1
            if "_sourceNode" in owner:
                src = g.by_id.get(owner["_sourceNode"]["$ref"])
                print(f"-- transition {g.owner_label(owner)} --[{r.task(owner.get('_condition'))}]-->")
                if src is not None:
                    print(f"   in state #{g.nid(src)} {r.node_label(src)}, entered:")
                    for line in why_lines(ws, g, src, a.expand_depth if a.expand else 0):
                        print("   " + line)
                continue
            print(f"-- #{g.nid(owner)} {r.node_label(owner)}")
            for line in why_lines(ws, g, owner, a.expand_depth if a.expand else 0):
                print("   " + line)
    if not found:
        what = "writes of" if q.startswith("$") and not a.reads else "uses of"
        print(f"no {what} {q}" + (f" in {graphs[0].name}" if a.graph else ""))
    return 0


# ---------------------------------------------------------------------------- stats


def _role(ws, node: dict) -> str:
    t = node.get("$type", "")
    s = simple_name(t)
    if s in ("ActionNode", "ActionState"):
        return "action"
    if s == "ConditionNode":
        return "condition"
    if any(k in node for k in NESTED_KEYS):
        return "subgraph"
    if "Missing" in t:
        return "missing"
    role = ws.schema.role(t) or "other"
    return {"bt-node": "other", "fsm-state": "state", "fsm-node": "state", "dt-node": "dialogue"}.get(role, role)


def _depth(g: Graph) -> int:
    best = 0
    stack = [(g.prime, 1, frozenset())] if g.prime else []
    while stack:
        n, d, seen = stack.pop()
        best = max(best, d)
        for k in g.children(n):
            if id(k) not in seen:
                stack.append((k, d + 1, seen | {id(n)}))
    return best


def graph_stats(ws, g: Graph) -> dict:
    from .assets import used_variables

    members: Counter = Counter()
    for owner in list(g.nodes) + list(g.connections):
        for _, d in walk(owner):
            bi = d.get("_baseInfo")
            if isinstance(bi, str) and "|" in bi:
                t, m = bi.split("|")[0], bi.split("|")[1]
                members[f"{simple_name(t)}.{m[4:] if m.startswith(('get_', 'set_')) else m}"] += 1
    writes: Counter = Counter()
    for n in g.nodes:
        for vid, mode in used_variables(g, [n]).items():
            if "w" in mode:
                v = g.var_by_id(vid)
                writes["$" + (v.get("_name") if v else vid[:8])] += 1
    subs = []
    for n in g.nodes:
        for k in NESTED_KEYS:
            p = n.get(k)
            if isinstance(p, dict) and "_value" in p:
                ref = g.ref(p["_value"])
                if ref is not None and not ref.is_null:
                    subs.append(ws.ref_display(ref))
    return {
        "graph": g.loaded.slot.address,
        "name": g.name,
        "kind": g.kind,
        "nodes": len(g.nodes),
        "connections": len(g.connections),
        "variables": len(g.variables),
        "depth": _depth(g) if g.kind == "bt" else None,
        "roles": dict(Counter(_role(ws, n) for n in g.nodes).most_common()),
        "members": dict(members.most_common()),
        "subgraphs": sorted(set(subs)),
        "writes": dict(writes.most_common()),
        "unreachable": len(g.nodes) - len(g.reachable()) if g.kind == "bt" else None,
    }


def cmd_stats(ws, a) -> int:
    graphs = [ws.graph(a.graph, a.kind)] if a.graph else [g for g in ws.all_graphs() if not a.kind or g.kind == a.kind]
    stats = [graph_stats(ws, g) for g in graphs]
    used_by: Dict[str, List[str]] = {}
    for s in stats:
        for sub in s["subgraphs"]:
            used_by.setdefault(sub, []).append(s["name"])
    for s in stats:
        s["used_by"] = sorted(used_by.get(s["name"], []))
    if a.json:
        print(
            json.dumps(
                stats[0] if a.graph else {"graphs": stats, "totals": _totals(stats)}, ensure_ascii=False, indent=1
            )
        )
        return 0
    if a.graph:
        _print_one(stats[0], a.top)
        return 0
    t = _totals(stats)
    print(f"{t['graphs']} graphs ({', '.join(f'{k} {v}' for k, v in t['kinds'].items())}), {t['nodes']} nodes")
    print(
        "largest: "
        + ", ".join(f"{s['name']} ({s['nodes']})" for s in sorted(stats, key=lambda s: -s["nodes"])[: a.top])
    )
    print("most called members: " + (", ".join(f"{m} ×{c}" for m, c in list(t["members"].items())[: a.top]) or "-"))
    shared = sorted(((k, v) for k, v in used_by.items() if len(v) > 1), key=lambda kv: -len(kv[1]))
    if shared:
        print("shared subgraphs:")
        for name, users in shared[: a.top]:
            print(f"  {name} ← {', '.join(sorted(set(users)))}")
    print("per graph:")
    for s in sorted(stats, key=lambda s: s["name"]):
        extra = f" depth={s['depth']}" if s["depth"] is not None else ""
        uses = f"  uses: {', '.join(s['subgraphs'])}" if s["subgraphs"] else ""
        print(f"  {s['kind']:<4} {s['name']:<40} nodes={s['nodes']}{extra}{uses}")
    return 0


def _totals(stats: List[dict]) -> dict:
    members: Counter = Counter()
    for s in stats:
        members.update(s["members"])
    return {
        "graphs": len(stats),
        "nodes": sum(s["nodes"] for s in stats),
        "kinds": dict(Counter(s["kind"] for s in stats).most_common()),
        "members": dict(members.most_common()),
    }


def _print_one(s: dict, top: int) -> None:
    head = f"{s['name']}  [{s['kind']}]  nodes={s['nodes']} connections={s['connections']} variables={s['variables']}"
    if s["depth"] is not None:
        head += f" depth={s['depth']}"
    if s["unreachable"]:
        head += f" unreachable={s['unreachable']}"
    print(head)
    print("roles: " + ", ".join(f"{k} {v}" for k, v in s["roles"].items()))
    if s["members"]:
        print("members: " + ", ".join(f"{m} ×{c}" for m, c in list(s["members"].items())[:top]))
    if s["writes"]:
        print("most written: " + ", ".join(f"{v} ×{c}" for v, c in list(s["writes"].items())[:top]))
    if s["subgraphs"]:
        print("uses: " + ", ".join(s["subgraphs"]))
    if s["used_by"]:
        print("used by: " + ", ".join(sorted(set(s["used_by"]))))


def register(sub) -> None:
    s = sub.add_parser(
        "why", help="what has to happen for control to reach a node (BT path conditions, FSM transitions)"
    )
    s.add_argument("graph")
    s.add_argument("node")
    s.add_argument("--expand", action="store_true", help="also explain how the graph is reached from graphs nesting it")
    s.add_argument("--expand-depth", type=int, default=3)
    s.add_argument("--kind", choices=["bt", "fsm", "dt"])
    s.set_defaults(func=cmd_why)

    s = sub.add_parser("paths", help="`why` for every call of a member or write of a variable")
    s.add_argument("graph", nargs="?", help="graph to search (default: all)")
    s.add_argument("--to", required=True, help="Type.Member, Member or $Variable")
    s.add_argument("--reads", action="store_true", help="for $Variable: include reads, not only writes")
    s.add_argument("--expand", action="store_true", help="also explain how the graph is reached from graphs nesting it")
    s.add_argument("--expand-depth", type=int, default=3)
    s.add_argument("--kind", choices=["bt", "fsm", "dt"])
    s.set_defaults(func=cmd_paths)

    s = sub.add_parser("stats", help="size, depth, node roles, most called members, shared subgraphs")
    s.add_argument("graph", nargs="?")
    s.add_argument("--top", type=int, default=10)
    s.add_argument("--kind", choices=["bt", "fsm", "dt"])
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_stats)
