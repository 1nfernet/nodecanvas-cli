"""Batch edits of a graph (``nodecanvas-cli apply``).

Operations address nodes by the ``#id`` shown by ``nodecanvas-cli show`` *before* the batch, or by an
alias given to a node created earlier in the same batch (``"id": "wait1"`` then ``"node": "wait1"``).
The batch is atomic: any failing operation aborts it and nothing is written.
"""

from __future__ import annotations

import copy
import difflib
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from .build import Builder, BuildError
from .layout import Layouter
from .lint import ERROR, NESTED_KEYS, lint_graph
from .model import Graph, GraphError, walk
from .schema import simple_name
from .views import render_graph

CHILD_LISTS = {  # lists kept parallel to a node's children, with the element added for a new child
    "NodeCanvas.BehaviourTrees.ProbabilitySelector": ("childWeights", lambda: {"_value": 1.0}),
    "NodeCanvas.DialogueTrees.ProbabilitySelector": ("childOptions", lambda: {"weight": {"_value": 1.0}}),
    "NodeCanvas.DialogueTrees.MultipleConditionNode": ("conditions", lambda: None),
}
CONNECTION_TYPES = {
    "bt": "NodeCanvas.BehaviourTrees.BTConnection",
    "fsm": "NodeCanvas.StateMachines.FSMConnection",
    "dt": "NodeCanvas.DialogueTrees.DTConnection",
}


class OpError(Exception):
    pass


class Editor:
    def __init__(self, ws, g: Graph):
        self.ws = ws
        self.g = g
        self.b = Builder(ws, g)
        self.handles: Dict[str, dict] = {str(i): n for i, n in enumerate(g.nodes)}
        self.lay = Layouter(g, ws.config.layout)
        self.place_roots: List[dict] = []  # created or moved: need a slot under their parent
        self.check_roots: List[dict] = []  # kept in place: only checked for overlaps
        self.relayout_roots: List[dict] = []  # explicit "layout" op
        self.notes: List[str] = []
        self.before = {id(n): self.lay.pos(n) for n in g.nodes}
        self.op_moved: set = set()

    # ------------------------------------------------------------------ addressing

    def node(self, ref: Any) -> dict:
        key = str(ref).lstrip("#")
        if key not in self.handles:
            raise OpError(f"unknown node {ref!r}")
        n = self.handles[key]
        if not any(x is n for x in self.g.nodes):
            raise OpError(f"node {ref!r} was removed earlier in this batch")
        return n

    def nid(self, node: dict) -> str:
        return self.g.nid(node)

    def conn(self, src: dict, dst: dict) -> dict:
        c = self.g.connection(src, dst)
        if c is None:
            raise OpError(f"no connection #{self.nid(src)} -> #{self.nid(dst)}")
        return c

    # ------------------------------------------------------------------ low level

    def _new_connection(self, src: dict, dst: dict, extra: Optional[dict] = None) -> dict:
        c = dict(extra or {})
        c["_sourceNode"] = {"$ref": self.nid(src)}
        c["_targetNode"] = {"$ref": self.nid(dst)}
        c["$type"] = CONNECTION_TYPES.get(self.g.kind, "NodeCanvas.Framework.Connection")
        return c

    def _insert_connection(self, c: dict, src: dict, index: Optional[int]) -> int:
        outs = self.g.out.get(self.nid(src), [])
        if index is None or index > len(outs) or index < 0:
            index = len(outs)
        if index < len(outs):
            pos = self.g.connections.index(outs[index])
        elif outs:
            pos = self.g.connections.index(outs[-1]) + 1
        else:
            pos = len(self.g.connections)
        self.g.connections.insert(pos, c)
        self.g.reindex()
        return index

    def _child_list_insert(self, parent: dict, index: int, item: Any = None, has_item: bool = False) -> None:
        spec = CHILD_LISTS.get(parent.get("$type", "").split("[")[0])
        if spec is None:
            return
        field, factory = spec
        lst = parent.setdefault(field, [])
        lst.insert(index, item if has_item else factory())

    def _child_list_remove(self, parent: dict, index: int) -> Any:
        spec = CHILD_LISTS.get(parent.get("$type", "").split("[")[0])
        if spec is None:
            return None
        lst = parent.get(spec[0])
        if isinstance(lst, list) and index < len(lst):
            return lst.pop(index)
        return None

    def _link(
        self,
        src: dict,
        dst: dict,
        index: Optional[int] = None,
        extra: Optional[dict] = None,
        item: Any = None,
        has_item: bool = False,
    ) -> int:
        if self._is_ancestor(dst, src):
            raise OpError(f"connecting #{self.nid(src)} -> #{self.nid(dst)} would create a cycle")
        c = self._new_connection(src, dst, extra)
        idx = self._insert_connection(c, src, index)
        self._child_list_insert(src, idx, item, has_item)
        return idx

    def _unlink(self, src: dict, dst: dict) -> tuple:
        c = self.conn(src, dst)
        idx = self.g.out[self.nid(src)].index(c)
        self.g.connections.remove(c)
        self.g.reindex()
        item = self._child_list_remove(src, idx)
        return c, idx, item

    def validate_structure(self) -> None:
        """Connection limits are checked once, on the final state of the batch."""
        problems = []
        schema = self.ws.schema
        for n in self.g.nodes:
            t = n.get("$type", "")
            nid = self.nid(n)
            outs, ins = len(self.g.out.get(nid, [])), len(self.g.inc.get(nid, []))
            max_out = schema.const_prop(t, "maxOutConnections")
            max_in = schema.const_prop(t, "maxInConnections")
            if max_out is not None and int(max_out) >= 0 and outs > int(max_out):
                problems.append(f"#{nid} {simple_name(t)} accepts at most {max_out} child(ren), has {outs}")
            if max_in is not None and int(max_in) >= 0 and ins > int(max_in):
                problems.append(f"#{nid} {simple_name(t)} accepts at most {max_in} parent(s), has {ins}")
        if problems:
            raise OpError("after the batch: " + "; ".join(problems) + " (ids are positions before renumbering)")

    def _is_ancestor(self, a: dict, b: dict) -> bool:
        """Is ``a`` an ancestor of (or equal to) ``b``?"""
        if self.g.kind != "bt":
            return False
        stack, seen = [b], set()
        while stack:
            n = stack.pop()
            if n is a:
                return True
            if id(n) in seen:
                continue
            seen.add(id(n))
            stack.extend(self.g.parents(n))
        return False

    def _add_node(self, node: dict, prime: bool = False) -> dict:
        if prime:
            self.g.nodes.insert(0, node)
            self._shift_refs(+1)
        else:
            self.g.nodes.append(node)
        self.g.reindex()
        self.place_roots.append(node)
        return node

    def _shift_refs(self, delta: int, start: int = 0) -> None:
        for c in self.g.connections:
            for k in ("_sourceNode", "_targetNode"):
                v = int(c[k]["$ref"])
                if v >= start:
                    c[k]["$ref"] = str(v + delta)

    def _remove_node(self, node: dict) -> None:
        nid = self.nid(node)
        for c in list(self.g.inc.get(nid, [])):
            self._unlink(self.g.by_id[c["_sourceNode"]["$ref"]], node)
        for c in list(self.g.out.get(nid, [])):
            self.g.connections.remove(c)
        idx = int(nid)
        self.g.nodes.pop(idx)
        for c in self.g.connections:
            for k in ("_sourceNode", "_targetNode"):
                v = int(c[k]["$ref"])
                if v > idx:
                    c[k]["$ref"] = str(v - 1)
        self.g.reindex()

    # ------------------------------------------------------------------ operations

    def op_add(self, op: dict) -> None:
        node = self.b.node(op["node"])
        parent = self.node(op["parent"]) if op.get("parent") is not None else None
        if parent is None and self.g.kind == "bt" and self.g.nodes:
            raise OpError('add: "parent" is required in a non-empty Behaviour Tree')
        self._add_node(node, prime=not self.g.nodes)
        if parent is not None:
            self._link(parent, node, op.get("index"), item=op.get("item"), has_item="item" in op)
        if op.get("id"):
            self.handles[str(op["id"])] = node

    def op_remove(self, op: dict) -> None:
        node = self.node(op["node"])
        kids = self.g.children(node)
        if kids and not op.get("recursive"):
            raise OpError(f'#{self.nid(node)} has children; pass "recursive": true or unwrap/move them first')
        if node is self.g.prime and len(self.g.nodes) > 1 and self.g.kind == "bt" and not op.get("recursive"):
            raise OpError("cannot remove the root of a non-empty tree")
        victims = [node]
        if op.get("recursive"):
            stack = list(kids)
            while stack:
                n = stack.pop()
                if any(v is n for v in victims):
                    continue
                if all(any(p is v for v in victims) for p in self.g.parents(n)):
                    victims.append(n)
                    stack.extend(self.g.children(n))
        for v in victims:
            self._remove_node(v)

    def op_move(self, op: dict) -> None:
        node = self.node(op["node"])
        parent = self.node(op["parent"])
        if self._is_ancestor(node, parent):
            raise OpError(f"cannot move #{self.nid(node)} under its own descendant #{self.nid(parent)}")
        extra, item, has_item = None, None, False
        old_parents = self.g.parents(node)
        if old_parents:
            src = old_parents[0] if op.get("from") is None else self.node(op["from"])
            c, _, item = self._unlink(src, node)
            has_item = CHILD_LISTS.get(src.get("$type", "")) is not None and CHILD_LISTS.get(
                src.get("$type", "")
            ) == CHILD_LISTS.get(parent.get("$type", ""))
            extra = {k: v for k, v in c.items() if k not in ("_sourceNode", "_targetNode", "$type", "$id")}
        self._link(parent, node, op.get("index"), extra, item, has_item)
        self.place_roots.append(node)

    def op_wrap(self, op: dict) -> None:
        node = self.node(op["node"])
        wrapper = self.b.node(op["with"])
        parents = self.g.parents(node)
        is_prime = node is self.g.prime
        self._add_node(wrapper, prime=is_prime)
        old = self.lay.pos(node)
        if old is not None and self.g.kind == "bt":
            self.place_roots.remove(wrapper)
            self.lay.set(wrapper, self.lay.snap(old[0]), self.lay.snap(old[1]))
            self.lay.translate([node], 0.0, self.ws.config.layout.level_gap)
            self.check_roots.append(wrapper)
        if parents:
            p = parents[0]
            c, idx, item = self._unlink(p, node)
            extra = {k: v for k, v in c.items() if k not in ("_sourceNode", "_targetNode", "$type", "$id")}
            self._link(p, wrapper, idx, extra, item, item is not None)
        self._link(wrapper, node, 0)
        if op.get("id"):
            self.handles[str(op["id"])] = wrapper

    def op_unwrap(self, op: dict) -> None:
        node = self.node(op["node"])
        kids = self.g.children(node)
        if len(kids) != 1:
            raise OpError(f"unwrap needs exactly one child, #{self.nid(node)} has {len(kids)}")
        child = kids[0]
        parents = self.g.parents(node)
        old, cpos = self.lay.pos(node), self.lay.pos(child)
        if old is not None and cpos is not None and self.g.kind == "bt":
            self.lay.translate([child], old[0] - cpos[0], old[1] - cpos[1])
        self._unlink(node, child)
        if parents:
            p = parents[0]
            c, idx, item = self._unlink(p, node)
            extra = {k: v for k, v in c.items() if k not in ("_sourceNode", "_targetNode", "$type", "$id")}
            self._remove_node(node)
            self._link(p, child, idx, extra, item, item is not None)
        else:
            self._remove_node(node)
            ci = self.g.nodes.index(child)
            self.g.nodes.insert(0, self.g.nodes.pop(ci))
            self._reorder_refs_after_move(ci)
        self.check_roots.append(child)

    def op_replace(self, op: dict) -> None:
        node = self.node(op["node"])
        new = self.b.node(op["with"])
        keep = {k: node[k] for k in ("_position", "_name", "_tag", "_comment") if k in node}
        node.clear()
        node.update(new)
        for k, v in keep.items():
            node.setdefault(k, v)

    def op_connect(self, op: dict) -> None:
        src, dst = self.node(op["from"]), self.node(op["to"])
        extra = {}
        if op.get("condition") is not None:
            extra["_condition"] = self.b.task(op["condition"], "condition")
        self._link(src, dst, op.get("index"), extra or None, op.get("item"), "item" in op)

    def op_disconnect(self, op: dict) -> None:
        self._unlink(self.node(op["from"]), self.node(op["to"]))

    def op_disable(self, op: dict) -> None:
        c = self.conn(self.node(op["from"]), self.node(op["to"]))
        if op.get("value", True):
            c["_isDisabled"] = True
        else:
            c.pop("_isDisabled", None)

    def op_set(self, op: dict) -> None:
        if "from" in op:
            owner = self.conn(self.node(op["from"]), self.node(op["to"]))
        else:
            owner = self.node(op["node"])
        path = _parse_path(op["path"])
        if not path:
            raise OpError("set: empty path")
        container, ftype, ctx = owner, None, None
        cur_type = owner.get("$type")
        for pos, k in enumerate(path):
            if isinstance(k, int):
                ftype = _element_type(ftype)
            else:
                ftype, ctx = self._field_type(cur_type, k)
            if pos == len(path) - 1:
                break
            nxt = container[k] if isinstance(container, list) else container.get(k)
            if nxt is None:
                raise OpError(f"set: {op['path']}: {k!r} does not exist")
            container = nxt
            cur_type = container.get("$type") if isinstance(container, dict) and "$type" in container else None
        last = path[-1]
        if op.get("raw"):
            value = op["value"]
        elif last in ("_action",) or (ftype and ftype.endswith("ActionTask")):
            value = self.b.task(op["value"], "action")
        elif last in ("_condition",) or (ftype and ftype.endswith("ConditionTask")):
            value = self.b.task(op["value"], "condition")
        elif ftype is None and cur_type is not None and last not in ("_name", "_tag", "_comment", "_position"):
            raise OpError(f'set: {simple_name(cur_type)} has no field {last!r} (use "raw": true to force)')
        elif last == "_position" and container is owner:
            v = self.b.vector(op["value"], "Vector2")
            set_position(owner, v.get("x", 0.0), v.get("y", 0.0))
            return
        else:
            value = self.b.value(op["value"], ftype, ctx, last)
        if isinstance(container, list):
            container[last] = value
        elif value is None and op.get("value") is None:
            container.pop(last, None)
        else:
            container[last] = value

    def op_unset(self, op: dict) -> None:
        owner = self.node(op["node"])
        path = _parse_path(op["path"])
        container = owner
        for k in path[:-1]:
            container = container[k]
        if isinstance(container, list):
            container.pop(path[-1])
        else:
            container.pop(path[-1], None)

    def _field_type(self, type_name: Optional[str], key) -> tuple:
        if type_name is None or isinstance(key, int):
            return None, None
        for name, f, owner in self.b.ordered_fields(type_name):
            if name == key:
                return f.type, owner
        return None, None

    def op_var_add(self, op: dict) -> None:
        name = op["name"]
        if self.g.var_by_name(name) is not None:
            raise OpError(f"variable ${name} already exists")
        var = self.b.new_variable(name, op["type"], op.get("value"), op.get("public", False))
        self.g.blackboard["_variables"][name] = var

    def op_var_rename(self, op: dict) -> None:
        var = self.b.var(op["name"])
        new = op["to"]
        if self.g.var_by_name(new) is not None:
            raise OpError(f"variable ${new} already exists")
        vid = var["_id"]
        old_vars = self.g.blackboard["_variables"]
        self.g.blackboard["_variables"] = {(new if v is var else k): v for k, v in old_vars.items()}
        var["_name"] = new
        for _, d in walk(self.g.data):
            if d.get("_targetVariableID") == vid and "_name" in d:
                d["_name"] = new

    def op_var_remove(self, op: dict) -> None:
        var = self.b.var(op["name"])
        vid = var["_id"]
        users = [g for g in self._var_users(vid)]
        if users and not op.get("force"):
            raise OpError(f'${var["_name"]} is used by {", ".join(users)}; pass "force": true to remove anyway')
        vars_ = self.g.blackboard["_variables"]
        for k in [k for k, v in vars_.items() if v is var]:
            del vars_[k]

    def _var_users(self, vid: str) -> List[str]:
        out = []
        for owner in list(self.g.nodes) + list(self.g.connections):
            for _, d in walk(owner):
                if d.get("_targetVariableID") == vid:
                    out.append(self.g.owner_label(owner))
                    break
        return out

    def op_var_set(self, op: dict) -> None:
        var = self.b.var(op["name"])
        t = var.get("$type", "")
        from .schema import generic_args

        args = generic_args(t)
        inner = args[0] if args else None
        lit = self.b.literal(op.get("value"), inner, None) if inner else op.get("value")
        from .build import _is_default

        keys = list(var)
        if _is_default(lit):
            var.pop("_value", None)
        else:
            new = {"_value": lit}
            new.update({k: var[k] for k in keys if k != "_value"})
            var.clear()
            var.update(new)
        if "public" in op:
            if op["public"]:
                var["_isPublic"] = True
                var["$type"] = var.pop("$type")
            else:
                var.pop("_isPublic", None)

    def op_subgraph_set(self, op: dict) -> None:
        node = self.node(op["node"])
        key = next((k for k in NESTED_KEYS if k in node), None) or op.get("field")
        if key is None:
            fields = [n for n, _, _ in self.b.ordered_fields(node["$type"]) if n in NESTED_KEYS]
            if not fields:
                raise OpError(f"#{self.nid(node)} has no subgraph field")
            key = fields[0]
        node[key] = {"_value": self.b.object_ref(op["graph"])} if op.get("graph") else {}
        if "map" in op:
            node["_variablesMap"] = self._mapping(op["graph"], op["map"])

    def op_map(self, op: dict) -> None:
        node = self.node(op["node"])
        key = next((k for k in NESTED_KEYS if k in node), None)
        if key is None or "_value" not in node[key]:
            raise OpError(f"#{self.nid(node)} has no assigned subgraph")
        ref = self.g.ref(node[key]["_value"])
        sub = self.ws.ref_graph(ref)
        if sub is None:
            raise OpError(f"#{self.nid(node)}: the subgraph asset cannot be loaded")
        if op.get("auto"):
            self._auto_map(node, sub)
            return
        node["_variablesMap"] = self._mapping(sub, op["map"])

    def _auto_map(self, node: dict, sub: Graph) -> None:
        """Map the subgraph's public variables to same-named, same-typed parent variables."""
        from .assets import used_variables

        usage = used_variables(sub, list(sub.nodes))
        entries = list(node.get("_variablesMap") or [])
        have = {m.get("_targetSubGraphVariableID") for m in entries}
        matched, skipped = [], []
        for sname, sv in sub.variables.items():
            if not sv.get("_isPublic") or "VariableSeperator" in sv.get("$type", "") or sv.get("_id") in have:
                continue
            pv = self.g.var_by_name(sname)
            if pv is None or pv.get("$type") != sv.get("$type"):
                skipped.append(sname)
                continue
            mode = usage.get(sv["_id"], "r")
            arrow = "<->" if mode == "rw" else ("->" if mode == "w" else "<-")
            entries.extend(self._mapping(sub, {sname: f"{arrow}${pv['_name']}"}))
            matched.append(f"{sname}{arrow.replace('<->', '↔').replace('<-', '←').replace('->', '→')}${pv['_name']}")
        node["_variablesMap"] = entries
        self.notes.append(
            f"map auto #{self.nid(node)}: mapped {', '.join(matched) or 'nothing'}"
            + (f"; no matching parent variable for {', '.join('$' + s for s in skipped)}" if skipped else "")
        )

    def _mapping(self, sub_graph, mapping: Dict[str, Any]) -> list:
        try:
            return self.b.mapping(sub_graph, mapping)
        except BuildError as e:
            raise OpError(str(e)) from e

    def op_set_start(self, op: dict) -> None:
        node = self.node(op["node"])
        idx = self.g.nodes.index(node)
        if idx == 0:
            return
        if self.g.kind == "bt" and self.g.parents(node):
            raise OpError("the root of a Behaviour Tree cannot have a parent")
        self.g.nodes.insert(0, self.g.nodes.pop(idx))
        self._reorder_refs_after_move(idx)

    def _reorder_refs_after_move(self, old_index: int) -> None:
        for c in self.g.connections:
            for k in ("_sourceNode", "_targetNode"):
                v = int(c[k]["$ref"])
                if v == old_index:
                    c[k]["$ref"] = "0"
                elif v < old_index:
                    c[k]["$ref"] = str(v + 1)
        self.g.reindex()

    def op_transition_add(self, op: dict) -> None:
        if self.g.kind != "fsm":
            raise OpError("transition-add is for FSMs; use connect")
        self.op_connect(op)

    def op_transition_remove(self, op: dict) -> None:
        self.op_disconnect(op)

    def op_choice_add(self, op: dict) -> None:
        node = self.node(op["node"])
        if simple_name(node.get("$type", "")) != "MultipleChoiceNode":
            raise OpError("choice-add works on MultipleChoiceNode")
        choices = node.setdefault("availableChoices", [])
        index = op.get("index", len(choices))
        choice: Dict[str, Any] = {}
        statement = {"_text": op.get("text", "")}
        if op.get("meta"):
            statement["_meta"] = op["meta"]
        choice["statement"] = statement
        if op.get("condition") is not None:
            choice["condition"] = self.b.task(op["condition"], "condition")
        choices.insert(index, choice)
        if op.get("to") is not None:
            dst = self.node(op["to"])
            c = self._new_connection(node, dst)
            self._insert_connection(c, node, index)

    def op_layout(self, op: dict) -> None:
        self.relayout_roots.append(self.node(op.get("node", "0")))

    def op_simplify(self, op: dict) -> None:
        """Unwrap Merge nodes that no longer join branches (one parent or none, one child)."""
        scope = self.lay.subtree(self.node(op["node"])) if op.get("node") is not None else None
        while True:
            pool = scope if scope is not None else list(self.g.nodes)
            target = next(
                (
                    n
                    for n in pool
                    if any(x is n for x in self.g.nodes)
                    and simple_name(n.get("$type", "")) == "Merge"
                    and len(self.g.parents(n)) <= 1
                    and len(self.g.children(n)) == 1
                ),
                None,
            )
            if target is None:
                return
            alias = f"__simplify_{id(target)}"
            self.handles[alias] = target
            self.op_unwrap({"node": alias})

    # ------------------------------------------------------------------ positions

    def layout(self) -> None:
        alive = lambda n: any(x is n for x in self.g.nodes)  # noqa: E731
        if self.g.kind != "bt":
            for n in self.place_roots:
                if alive(n) and self.lay.pos(n) is None:
                    self.lay.place_flat(n)
            return
        pending = {id(n) for n in self.place_roots}
        positioned = {id(n) for n in self.g.nodes if id(n) not in pending}
        roots = sorted((n for n in self.place_roots if alive(n)), key=lambda n: len(self.lay.ancestors(n)))
        for n in roots:
            self.lay.place(n, positioned)
        for n in self.place_roots + self.check_roots:
            if alive(n):
                self.lay.resolve(n)
        for n in self.relayout_roots:
            if alive(n):
                self.lay.relayout(n)
        for root in self.place_roots + self.check_roots + self.relayout_roots:
            if alive(root):
                self.op_moved |= {id(m) for m in self.lay.subtree(root)}

    def layout_summary(self) -> Optional[str]:
        """Which nodes got positions: created, moved by an operation, or shifted to make room."""
        new, moved, shifted = [], [], {}
        for n in self.g.nodes:
            old, cur = self.before.get(id(n), "new"), self.lay.pos(n)
            if old == "new":
                new.append(n)
            elif old != cur and cur is not None:
                if id(n) in self.op_moved:
                    moved.append(n)
                else:
                    shifted[id(n)] = (n, (cur[0] - (old or (0, 0))[0], cur[1] - (old or (0, 0))[1]))
        if not (new or moved or shifted):
            return None
        groups = []
        for n, delta in shifted.values():
            p = self.lay.parent(n)
            if p is not None and id(p) in shifted and shifted[id(p)][1] == delta:
                continue
            size = sum(1 for m in self.lay.subtree(n) if id(m) in shifted and shifted[id(m)][1] == delta)
            groups.append((int(self.nid(n)), size, delta))
        groups.sort()
        head = (
            f"layout: placed {len(new)} new, moved {len(moved)} by the edit, "
            f"shifted {len(shifted)} existing to make room"
        )
        if not groups:
            return head
        parts = [
            f"#{nid}" + (f" subtree ({size} nodes)" if size > 1 else "") + f" {_signed(d[0])},{_signed(d[1])}"
            for nid, size, d in groups
        ]
        return head + ":\n  " + "\n  ".join(parts)


OPS = {
    "add": Editor.op_add,
    "remove": Editor.op_remove,
    "move": Editor.op_move,
    "wrap": Editor.op_wrap,
    "unwrap": Editor.op_unwrap,
    "replace": Editor.op_replace,
    "connect": Editor.op_connect,
    "disconnect": Editor.op_disconnect,
    "disable": Editor.op_disable,
    "set": Editor.op_set,
    "unset": Editor.op_unset,
    "var-add": Editor.op_var_add,
    "var-rename": Editor.op_var_rename,
    "var-remove": Editor.op_var_remove,
    "var-set": Editor.op_var_set,
    "subgraph-set": Editor.op_subgraph_set,
    "map": Editor.op_map,
    "set-start": Editor.op_set_start,
    "transition-add": Editor.op_transition_add,
    "transition-remove": Editor.op_transition_remove,
    "choice-add": Editor.op_choice_add,
    "layout": Editor.op_layout,
    "simplify": Editor.op_simplify,
}


def _signed(v: float) -> str:
    v = int(v) if float(v).is_integer() else v
    return f"+{v}" if v > 0 else str(v)


def set_position(node: dict, x: float, y: float) -> None:
    """Set ``_position`` keeping FullSerializer key order (fields, ``$type``, ``$id`` last)."""
    # FullSerializer omits members equal to the default, so zero components are left out
    pos = {k: float(v) for k, v in (("x", x), ("y", y)) if float(v) != 0.0}
    if "_position" in node:
        node["_position"] = pos
        return
    tail = {k: node.pop(k) for k in ("$type", "$id") if k in node}
    node["_position"] = pos
    node.update(tail)


def _element_type(ftype):
    if not ftype:
        return None
    m = re.match(r"^(?:List|IList)<(.+)>$", ftype) or re.match(r"^(.+)\[\]$", ftype)
    return m.group(1) if m else None


def _parse_path(path) -> list:
    if isinstance(path, list):
        return path
    out = []
    for part in re.findall(r"[^.\[\]]+", str(path)):
        out.append(int(part) if part.isdigit() else part)
    return out


def apply_ops(ws, g: Graph, ops: List[dict]) -> Editor:
    ed = Editor(ws, g)
    for i, op in enumerate(ops):
        name = op.get("op")
        fn = OPS.get(name)
        if fn is None:
            raise OpError(f"op #{i}: unknown op {name!r}; known: {', '.join(OPS)}")
        try:
            fn(ed, op)
        except (OpError, BuildError, GraphError) as e:
            raise OpError(f"op #{i} ({name}): {e}") from e
        except (KeyError, IndexError, TypeError, ValueError) as e:
            raise OpError(f"op #{i} ({name}): bad arguments ({type(e).__name__}: {e})") from e
    ed.validate_structure()
    ed.layout()
    ed.b.normalize(g.nodes)
    ed.b.normalize(g.connections)
    g.renumber()
    return ed


def _strip_ids(lines: List[str]) -> List[str]:
    return [re.sub(r"#\d+", "#", line) for line in lines]


def cmd_apply(ws, a) -> int:
    g = ws.graph(a.graph, a.kind)
    text = sys.stdin.read() if a.ops == "-" else Path(a.ops).read_text(encoding="utf-8")
    try:
        ops = json.loads(text)
    except ValueError as e:
        print(f"error: ops are not valid JSON: {e}", file=sys.stderr)
        return 1
    if isinstance(ops, dict):
        ops = [ops]

    def view():
        r = ws.renderer(g)
        r.positions = a.positions
        return render_graph(g, r, with_header=False)

    before_lines = view()
    before_lint = {(i.code, i.message) for i in lint_graph(ws, g) if i.severity == ERROR}
    from .bindings import call_args, lost_bindings_by_identity

    before_calls = call_args(g)
    snapshot = (copy.deepcopy(g.data), list(g.refs))
    try:
        ed = apply_ops(ws, g, ops)
    except OpError as e:
        g.data.clear()
        g.data.update(snapshot[0])
        g.refs[:] = snapshot[1]
        g.reindex()
        print(f"error: {e}", file=sys.stderr)
        return 1
    after_lines = view()
    diff = list(
        difflib.unified_diff(_strip_ids(before_lines), _strip_ids(after_lines), "before", "after", n=1, lineterm="")
    )
    print("\n".join(diff) if diff else "(no visible change)")
    summary = ed.layout_summary()
    if summary:
        print(summary)
    for note in ed.notes:
        print(note)
    for line in lost_bindings_by_identity(before_calls, g):
        print(f"warning: lost binding: {line}")
    new_errors = [i for i in lint_graph(ws, g) if i.severity == ERROR and (i.code, i.message) not in before_lint]
    for i in new_errors:
        print("lint: " + i.line())
    if new_errors and not a.force:
        print("not saved: the edit introduces lint errors (use --force to save anyway)", file=sys.stderr)
        return 1
    if a.dry_run:
        print("dry run: nothing written")
        return 0
    ws.save(g)
    print(f"saved {g.loaded.slot.address} (node ids renumbered; run `nodecanvas-cli show` for the new ids)")
    return 0


def register(sub) -> None:
    s = sub.add_parser("apply", help="apply a JSON batch of edit operations to a graph")
    s.add_argument("graph")
    s.add_argument("ops", help="path to a JSON file with operations, or - for stdin")
    s.add_argument("--dry-run", action="store_true", help="show the result without writing")
    s.add_argument("--force", action="store_true", help="save even if new lint errors appear")
    s.add_argument("--positions", action="store_true", help="include editor coordinates in the before/after diff")
    s.add_argument("--kind", choices=["bt", "fsm", "dt"], help="pick the graph of this kind when the name is ambiguous")
    s.set_defaults(func=cmd_apply)
