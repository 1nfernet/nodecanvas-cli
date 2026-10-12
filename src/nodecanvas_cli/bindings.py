"""Checks for reflection task bindings (``method`` + ``parameters``) with ready-to-apply fixes."""

from __future__ import annotations

import copy
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .model import Graph
from .schema import generic_args, short_generic, simple_name

PRIMITIVES = {
    "System.Boolean",
    "System.Single",
    "System.Double",
    "System.Int32",
    "System.Int64",
    "System.String",
    "System.Byte",
    "System.Char",
    "System.UInt32",
    "System.Int16",
}


def _strip(t: str) -> str:
    return t.split(",")[0].strip() if "[[" not in t else t


def var_type(var: dict) -> Optional[str]:
    t = var.get("$type", "")
    if "MissingVariableType" in t:
        t = var.get("_missingType", "")
    args = generic_args(t)
    return _strip(args[0]) if args else None


def is_reference_type(schema, type_name: str) -> bool:
    """Classes and Unity objects (null is a meaningful value); not primitives, strings, structs or enums."""
    t = _strip(type_name)
    if t in PRIMITIVES or t.startswith("System.Nullable") or "[[" in t:
        return False
    if t.startswith("UnityEngine."):
        return t.split(".")[-1] not in UNITY_VALUE_TYPES
    decl = schema.find(t)
    return decl is not None and decl.kind in ("class", "interface", "record")


UNITY_VALUE_TYPES = {
    "Vector2",
    "Vector3",
    "Vector4",
    "Quaternion",
    "Color",
    "Color32",
    "Rect",
    "Bounds",
    "LayerMask",
    "Vector2Int",
    "Vector3Int",
    "Ray",
    "RaycastHit",
}


def _known(schema, t: str) -> bool:
    """Types whose assignability can be decided offline (sources, primitives, Unity value types)."""
    t = _strip(t)
    if t.startswith("UnityEngine."):
        return t.split(".")[-1] in UNITY_VALUE_TYPES
    return t in PRIMITIVES or schema.find(t) is not None


def assignable(schema, var_t: Optional[str], target: str) -> bool:
    """Can a variable of ``var_t`` be passed where ``target`` is expected (same type or a source subclass)?"""
    if not var_t:
        return False
    if _strip(var_t) == _strip(target):
        return True
    decl = schema.find(_strip(var_t))
    if decl is None:
        return False
    want = simple_name(target)
    return any(
        c.name == want or any(b.split("<")[0].split(".")[-1] == want for b in c.bases) for c in schema.chain(decl)
    )


def _path_str(path: Tuple) -> str:
    return ".".join(str(p) for p in path)


def _target(g: Graph, owner: dict) -> Dict[str, Any]:
    if "_sourceNode" in owner:
        return {"from": owner["_sourceNode"]["$ref"], "to": owner["_targetNode"]["$ref"]}
    return {"node": g.nid(owner)}


def check_task(ws, g: Graph, owner: dict, path: Tuple, task: dict) -> Iterable[Tuple[str, str, str, Optional[dict]]]:
    """Yield ``(severity, code, message, fix_op)`` for one reflection task."""
    method = task.get("method")
    params = task.get("parameters")
    if not isinstance(method, dict) or not isinstance(params, list):
        return
    base = (method.get("_baseInfo") or "").split("|")
    if len(base) < 2:
        return
    type_name, member = base[0], base[1]
    sig = [s for s in (method.get("_paramsInfo") or "").split("|") if s] if method.get("_paramsInfo") else []
    label = f"{simple_name(type_name)}.{member}"
    schema = ws.schema

    # stored argument types that differ from the bound overload
    mismatch = [
        k
        for k, p in enumerate(params)
        if isinstance(p, dict) and p.get("_type") and k < len(sig) and _strip(p["_type"]) != _strip(sig[k])
    ]
    if mismatch:
        k = mismatch[0]
        msg = (
            f"argument {k + 1} of {label} is stored as {short_generic(params[k]['_type'])} "
            f"but the bound overload takes {short_generic(sig[k])}"
        )
        fix = None
        better = _fitting_overload(ws, g, type_name, member, params, sig)
        if better is not None:
            msg += f"; overload {member}({', '.join(simple_name(p) for p in better)}) fits the arguments"
            fix = _retarget_fix(ws, g, owner, path, task, better)
        yield "warning", "arg-type-mismatch", msg, fix

    # variable bound to a parameter of an incompatible type (both types known)
    for k, p in enumerate(params):
        if not isinstance(p, dict) or not p.get("_targetVariableID") or k >= len(sig) or k in mismatch:
            continue
        var = g.var_by_id(p["_targetVariableID"])
        vt = var_type(var) if var is not None else None
        pt = _strip(sig[k])
        if not vt or _strip(vt) == pt or not _known(schema, vt) or not _known(schema, pt):
            continue
        if not assignable(schema, vt, pt):
            msg = (
                f"argument {k + 1} of {label} is ${var.get('_name')} ({short_generic(vt)}) "
                f"but the parameter takes {short_generic(pt)}"
            )
            better = _fitting_overload(ws, g, type_name, member, params, sig)
            fix = None
            if better is not None:
                msg += f"; overload {member}({', '.join(simple_name(x) for x in better)}) fits the arguments"
                fix = _retarget_fix(ws, g, owner, path, task, better)
            yield "warning", "arg-type-mismatch", msg, fix

    # empty object arguments
    for k, p in enumerate(params):
        if not isinstance(p, dict) or p.get("_name") or p.get("_targetVariableID") or "_value" in p:
            continue
        t = p.get("_type") or (sig[k] if k < len(sig) else None)
        if not t or not is_reference_type(schema, t):
            continue
        cands = _scoped_candidates(g, owner, [v for v in g.variables.values() if assignable(schema, var_type(v), t)])
        where = f"argument {k + 1} of {label}"
        if cands:
            best = cands[0]
            names = ", ".join("$" + v.get("_name", "?") for v in cands[:3])
            fix = dict(
                _target(g, owner),
                op="set",
                path=_path_str(path + ("parameters", k)),
                raw=True,
                value={"_type": p.get("_type") or _strip(t), "_name": best["_name"], "_targetVariableID": best["_id"]},
            )
            yield (
                "info",
                "null-arg",
                f"{where} ({short_generic(t)}) is empty (null is passed); if unintended, did you mean {names}?",
                fix,
            )
        else:
            yield ("info", "null-arg", f"{where} ({short_generic(t)}) is empty (null is passed)", None)

    agent = task.get("overrideAgent")
    if (
        isinstance(agent, dict)
        and agent.get("_type")
        and not (agent.get("_name") or agent.get("_targetVariableID") or "_value" in agent)
    ):
        cands = [v for v in g.variables.values() if assignable(schema, var_type(v), agent["_type"])]
        hint = f"; did you mean {', '.join('$' + v.get('_name', '?') for v in cands[:3])}?" if cands else ""
        yield ("warning", "null-agent", f"{label} overrides the agent with an empty variable{hint}", None)


def _scoped_candidates(g: Graph, owner: dict, cands: List[dict]) -> List[dict]:
    """Order fitting variables by scope: the ``current`` of the nearest enclosing Iterate first, then
    variables written earlier on the way from the root (nearest first), then the rest."""
    if "_sourceNode" in owner or not cands:
        return cands
    from .assets import used_variables

    ranked: List[str] = []
    child, cur = owner, (g.parents(owner) or [None])[0]
    while cur is not None:
        cv = cur.get("current") if simple_name(cur.get("$type", "")) == "Iterator" else None
        if isinstance(cv, dict) and cv.get("_targetVariableID"):
            ranked.append(cv["_targetVariableID"])
        kids = g.children(cur)
        earlier = kids[: kids.index(child)] if child in kids else []
        for sib in reversed(earlier):
            from .layout import Layouter

            sub = Layouter(g, None).subtree(sib)
            ranked += [vid for vid, mode in used_variables(g, sub).items() if "w" in mode]
        child, cur = cur, (g.parents(cur) or [None])[0]
    order = {vid: i for i, vid in reversed(list(enumerate(ranked)))}
    return sorted(cands, key=lambda v: order.get(v.get("_id"), len(ranked)))


def call_args(g: Graph) -> Dict[Tuple, Tuple[str, List[Optional[str]]]]:
    """``(id(owner), path) -> (Type.Member, [variable name or None per argument])`` for every reflection call."""
    from .model import walk

    out = {}
    for owner in list(g.nodes) + list(g.connections):
        for path, d in walk(owner):
            m, params = d.get("method"), d.get("parameters")
            if isinstance(m, dict) and isinstance(params, list) and "|" in (m.get("_baseInfo") or ""):
                t, member = m["_baseInfo"].split("|")[:2]
                args = []
                for p in params:
                    var = (
                        g.var_by_id(p.get("_targetVariableID"))
                        if isinstance(p, dict) and p.get("_targetVariableID")
                        else None
                    )
                    bound = (
                        "$" + var["_name"]
                        if var
                        else ("$" + p["_name"] if isinstance(p, dict) and p.get("_name") else None)
                    )
                    empty = isinstance(p, dict) and not bound and "_value" not in p
                    args.append(bound if bound else (None if empty else "literal"))
                out[(id(owner), path)] = (f"{simple_name(t)}.{member}", args)
    return out


def lost_bindings_by_identity(before: Dict, g: Graph) -> List[str]:
    """Arguments that were bound to a variable before an edit and are empty after it (same node object)."""
    after = call_args(g)
    lines = []
    for key, (member, args) in after.items():
        old = before.get(key)
        if old is None or old[0] != member:
            continue
        for k, (a, b) in enumerate(zip(old[1], args)):
            if a and a.startswith("$") and b is None:
                owner = next((o for o in list(g.nodes) + list(g.connections) if id(o) == key[0]), None)
                where = g.owner_label(owner) if owner is not None else "?"
                lines.append(f"{where} {member} argument {k + 1}: {a} -> empty")
    return lines


def lost_bindings_by_member(old_g: Graph, new_g: Graph) -> List[str]:
    """Per member and argument position: more empty arguments and fewer bound ones than before."""

    def tally(g):
        t: Dict[Tuple[str, int], List[int]] = {}
        names: Dict[Tuple[str, int], set] = {}
        for member, args in call_args(g).values():
            for k, a in enumerate(args):
                c = t.setdefault((member, k), [0, 0])
                if a is None:
                    c[1] += 1
                elif a.startswith("$"):
                    c[0] += 1
                    names.setdefault((member, k), set()).add(a)
        return t, names

    (old, old_names), (new, _) = tally(old_g), tally(new_g)
    lines = []
    for key, (bound, empty) in new.items():
        ob, oe = old.get(key, [0, 0])
        if empty > oe and bound < ob:
            was = ", ".join(sorted(old_names.get(key, [])))
            lines.append(f"{key[0]} argument {key[1] + 1}: {empty - oe} call(s) lost their variable (was {was})")
    return lines


def _fitting_overload(
    ws, g: Graph, type_name: str, member: str, params: List[dict], sig: List[str]
) -> Optional[List[str]]:
    schema = ws.schema
    actual = []
    for k, p in enumerate(params):
        var = g.var_by_id(p.get("_targetVariableID")) if isinstance(p, dict) and p.get("_targetVariableID") else None
        actual.append(var_type(var) if var is not None else (p.get("_type") if isinstance(p, dict) else None))
    fits = []
    for o in schema.overloads(type_name, member, len(params)):
        decl = schema.find(type_name)
        full = [ws_full(ws, t, decl) for t in o["params"]]
        if [_strip(f) for f in full] == [_strip(s) for s in sig]:
            continue
        if all(a is None or assignable(schema, a, f) for a, f in zip(actual, full)):
            fits.append(o["params"])
    return fits[0] if len(fits) == 1 else None


def ws_full(ws, type_text: str, ctx) -> str:
    from .typenames import TypeNames

    if not hasattr(ws, "_typenames"):
        ws._typenames = TypeNames(ws)
    return ws._typenames.full(type_text, ctx)


def _retarget_fix(ws, g: Graph, owner: dict, path: Tuple, task: dict, overload: List[str]) -> Optional[dict]:
    from .spec import Speccer

    sp = Speccer(ws, g)
    kind = {"ExecuteFunction_Multiplatform": "call", "CheckFunction_Multiplatform": "check"}.get(
        simple_name(task.get("$type", ""))
    )
    if kind is None:
        return None
    try:
        spec = sp.reflection(copy.deepcopy(task), kind)
    except (KeyError, ValueError, IndexError):
        return None
    args = []
    for p in task.get("parameters") or []:
        ref = sp.var_ref(p) if isinstance(p, dict) and (p.get("_name") or p.get("_targetVariableID")) else None
        args.append(ref if isinstance(ref, str) else sp.param(p, p.get("_type"), None))
    spec["args"] = args
    spec["overload"] = [simple_name(t) for t in overload]
    return dict(_target(g, owner), op="set", path=_path_str(path), value=spec)
