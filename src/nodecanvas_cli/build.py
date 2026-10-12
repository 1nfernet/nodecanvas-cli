"""Turning short, schema-checked specs into FullSerializer JSON for nodes, tasks and values.

Spec conventions (all optional sugar; raw FullSerializer JSON is always accepted):
  "$Var"            blackboard variable reference
  1.5 / true / "x"  literal for a BBParameter or plain field
  [1, 2, 3]         Vector2/3/4, Quaternion, Color, or a list
  "Enemy|Ally"      enum member(s) by name
  "Guard_Wait"       a graph asset for Unity-object fields (subgraphs)
  {"$type": "Wait", "waitTime": 2}               a task or node, short type names allowed
  {"$type": "SetVariable<Pawn>", ...}              generic tasks
  {"call": "BotController.MoveTo", "args": ["$Target", [0, 0, 0]], "agent": "$Owner", "returns": "$X"}
  {"check": "BotController.IsDead", "args": [...], "agent": "$Owner", "value": false, "compare": ">"}
  {"get": "BotController.Position", "agent": "$Owner", "returns": "$Pos"}
  {"raw": {...}}                                   bypass all conversion
"""

from __future__ import annotations

import re
import uuid
from typing import Any, Dict, List, Optional, Tuple

from .csharp import ClassDecl, FieldDecl
from .model import Graph
from .render import UNITY_OBJECT_BASES, _bb_inner
from .schema import generic_args, simple_name
from .typenames import ALIASES, TypeNames

COMPARE_NAMES = {"==": "EqualTo", ">": "GreaterThan", "<": "LessThan", ">=": "GreaterOrEqualTo", "<=": "LessOrEqualTo"}
KIND_NAMESPACES = {
    "bt": "NodeCanvas.BehaviourTrees",
    "fsm": "NodeCanvas.StateMachines",
    "dt": "NodeCanvas.DialogueTrees",
}
NODE_ROLES = {"bt": {"bt-node", "composite", "decorator"}, "fsm": {"fsm-state", "fsm-node"}, "dt": {"dt-node"}}
TASK_ROLES = {"action", "condition", "task"}
VECTOR_COMPONENTS = {
    "Vector2": "xy",
    "Vector3": "xyz",
    "Vector4": "xyzw",
    "Quaternion": "xyzw",
    "Color": "rgba",
    "Vector2Int": "xy",
    "Vector3Int": "xyz",
}


class BuildError(Exception):
    pass


class Raw:
    """Already-serialized JSON, inserted as is."""

    def __init__(self, value):
        self.value = value


class Builder:
    def __init__(self, ws, graph: Graph):
        self.ws = ws
        self.g = graph
        self.schema = ws.schema
        self.types = TypeNames(ws)

    # ------------------------------------------------------------------ types

    def resolve_type(self, name: str, roles: Optional[set] = None) -> str:
        name = name.strip()
        m = re.match(r"^([\w.]+)<(.+)>$", name)
        if m:
            base, args = m.group(1), _split_generic(m.group(2))
            decl = self._pick(base.split(".")[-1], roles, len(args))
            return decl.full_name + "[" + ",".join(f"[{self.types.qualify(a, decl)}]" for a in args) + "]"
        if "`" in name or (("." in name) and self.schema.find(name) is not None):
            decl = self.schema.find(name)
            if decl is None:
                raise BuildError(f"type {name!r} not found in project sources")
            return name if "[[" in name else decl.full_name
        return self._pick(name, roles, None).full_name

    def _pick(self, simple: str, roles: Optional[set], arity: Optional[int]) -> ClassDecl:
        cands = [
            c
            for c in self.schema._load_simple(simple)
            if c.kind == "class" and not c.is_abstract and (arity is None or c.arity == arity)
        ]
        if not cands:
            cands = self._by_display_name(simple, roles)
        if roles:
            cands = [c for c in cands if self.schema.role(c.full_name) in roles]
        if len(cands) > 1:
            ns = KIND_NAMESPACES.get(self.g.kind)
            pref = [c for c in cands if ns and c.namespace == ns]
            if len(pref) == 1:
                return pref[0]
            nc = [c for c in cands if c.namespace.startswith("NodeCanvas.Tasks")]
            if len(nc) == 1:
                return nc[0]
            raise BuildError(f"type {simple!r} is ambiguous: " + ", ".join(c.full_name for c in cands))
        if not cands:
            raise BuildError(
                f"no {'/'.join(sorted(roles)) if roles else ''} type named {simple!r} "
                f"(see `nodecanvas-cli types {simple}`)"
            )
        return cands[0]

    def _by_display_name(self, name: str, roles) -> List[ClassDecl]:
        out = []
        roles = roles or TASK_ROLES | NODE_ROLES.get(self.g.kind, set())
        pool = list(self.schema.all_of_role(roles))
        known = {d.full_name for d in pool}
        pool += [
            d
            for d in self.schema._builtin_full.values()
            if d.full_name not in known and not d.is_abstract and self.schema.role(d.full_name) in roles
        ]
        for d in pool:
            disp = d.attr_str("Name")
            if disp and disp.replace(" ", "").lower() == name.replace(" ", "").lower():
                out.append(d)
        return out

    def ordered_fields(self, full: str) -> List[Tuple[str, FieldDecl, ClassDecl]]:
        return self.schema.ordered_fields(full)

    # ------------------------------------------------------------------ objects

    def node(self, spec: Any) -> dict:
        if isinstance(spec, str):
            spec = {"$type": spec}
        if not isinstance(spec, dict) or "$type" not in spec:
            raise BuildError('a node spec needs "$type"')
        full = self.resolve_type(spec["$type"], NODE_ROLES.get(self.g.kind) or None)
        values = {k: v for k, v in spec.items() if k not in ("$type", "map")}
        node = self.obj(full, values, extra_ok={"_name", "_tag", "_comment", "_position"})
        if "map" in spec:
            sub = self._nested_graph(node)
            if sub is None:
                raise BuildError('"map" needs the node\'s subgraph field to point at a graph asset')
            mapped = self.mapping(sub, spec["map"])
            items = list(node.items())
            node.clear()
            node.update(items)
            node["_variablesMap"] = mapped
            self.normalize(node)
        return node

    def _nested_graph(self, node: dict):
        for k in ("_subTree", "_nestedBT", "_nestedFSM", "_nestedDLG", "_subGraph"):
            v = node.get(k)
            if isinstance(v, dict) and "_value" in v:
                ref = self.g.ref(v["_value"])
                return self.ws.ref_graph(ref) if ref is not None else None
        return None

    def task(self, spec: Any, role: Optional[str] = None) -> Optional[dict]:
        if spec is None:
            return None
        if isinstance(spec, Raw):
            return spec.value
        if isinstance(spec, dict) and "raw" in spec and len(spec) == 1:
            return spec["raw"]
        if isinstance(spec, dict) and ("call" in spec or "check" in spec or "get" in spec or "set" in spec):
            return self.reflection_task(spec)
        if isinstance(spec, str):
            spec = {"$type": spec}
        if isinstance(spec, list):
            key = "conditions" if role == "condition" else "actions"
            return self.task({"$type": "ConditionList" if role == "condition" else "ActionList", key: spec}, role)
        if not isinstance(spec, dict) or "$type" not in spec:
            raise BuildError(f'a task spec needs "$type": {spec!r}')
        roles = {role} if role in ("action", "condition") else TASK_ROLES
        full = self.resolve_type(spec["$type"], roles)
        return self.obj(
            full, {k: v for k, v in spec.items() if k != "$type"}, extra_ok={"_isDisabled", "_invert", "overrideAgent"}
        )

    def obj(self, full: str, values: Dict[str, Any], extra_ok=frozenset(), with_type: bool = True) -> dict:
        fields = self.ordered_fields(full)
        known = {n: (f, c) for n, f, c in fields}
        aliases = {f.name: n for n, f, _ in fields}
        out: Dict[str, Any] = {}
        pending = dict(values)
        if "agent" in pending:
            pending["overrideAgent"] = pending.pop("agent")
        for k in list(pending):
            if k not in known and k in aliases:
                pending[aliases[k]] = pending.pop(k)
        version = pending.pop("$version", None)
        unknown = [k for k in pending if k not in known and k not in extra_ok]
        if unknown:
            raise BuildError(
                f"{simple_name(full)} has no field(s) {', '.join(unknown)}; fields: "
                + ", ".join(
                    n
                    for n, _, _ in fields
                    if not n.startswith("_")
                    or n
                    in (
                        "_action",
                        "_condition",
                        "_actionList",
                        "_subTree",
                        "_nestedBT",
                        "_nestedFSM",
                        "_nestedDLG",
                        "_variablesMap",
                        "_transitionEvaluation",
                    )
                )
            )
        for name, f, owner in fields:
            if name in pending:
                out[name] = self.value(pending.pop(name), f.type, owner, field_name=name)
        for k, v in pending.items():
            if k == "overrideAgent":
                out[k] = self.agent(v)
            elif k == "_position":
                out[k] = self.vector(v, "Vector2")
            else:
                out[k] = v.value if isinstance(v, Raw) else v
        if with_type:
            out["$type"] = full
        if version is not None:
            out["$version"] = version
        return out

    def agent(self, v: Any) -> dict:
        if isinstance(v, Raw):
            return v.value
        if isinstance(v, dict):
            return v
        if isinstance(v, str) and v.startswith("$"):
            var = self.var(v)
            return {
                "_type": self._var_value_type(var) or "UnityEngine.Component",
                "_name": var["_name"],
                "_targetVariableID": var["_id"],
            }
        raise BuildError(f"agent must be a $variable, got {v!r}")

    def normalize(self, obj: Any) -> None:
        """Reorder keys of typed objects the way FullSerializer writes them (recursively)."""
        if isinstance(obj, list):
            for x in obj:
                self.normalize(x)
            return
        if not isinstance(obj, dict):
            return
        for v in obj.values():
            self.normalize(v)
        t = obj.get("$type")
        if not t or self.schema.find(t) is None:
            return
        order = [n for n, _, _ in self.ordered_fields(t)]
        rank = {n: i for i, n in enumerate(order)}
        keys = list(obj)
        known = sorted([k for k in keys if k in rank], key=lambda k: rank[k])
        other = [k for k in keys if k not in rank and k not in ("$type", "$id", "$version")]
        tail = [k for k in ("$type", "$version", "$id") if k in obj]
        new_keys = known + other + tail
        if new_keys != keys:
            items = {k: obj[k] for k in new_keys}
            obj.clear()
            obj.update(items)

    # ------------------------------------------------------------------ values

    def var(self, ref: str) -> dict:
        name = ref[1:] if ref.startswith("$") else ref
        v = self.g.var_by_name(name)
        if v is None:
            from .owners import find_external_var

            v = find_external_var(self.ws, self.g, name)
        if v is None:
            where = "on a GlobalBlackboard" if "/" in name else f"in {self.g.name} or its owners' blackboards"
            raise BuildError(f"no blackboard variable ${name} {where} (add it with var-add)")
        return v

    def _var_value_type(self, var: dict) -> Optional[str]:
        t = var.get("$type", "")
        if "MissingVariableType" in t:
            t = var.get("_missingType", "")
        args = generic_args(t)
        return args[0] if args else None

    def value(self, v: Any, ftype: Optional[str], ctx: Optional[ClassDecl], field_name: str = "") -> Any:
        if isinstance(v, Raw):
            return v.value
        if isinstance(v, dict) and set(v) == {"raw"}:
            return v["raw"]
        t = re.sub(r"\s+", "", ftype or "")
        inner = _bb_inner(t)
        if inner is not None or t == "BBParameter" or t.startswith("BBObjectParameter"):
            return self.bbparam(v, inner, ctx, boxed=(inner is None))
        role = self.schema.role(self.types.full(t, ctx)) if t else None
        if role in ("action", "condition", "task") or t in ("ActionTask", "ConditionTask", "Task"):
            r = (
                "condition"
                if (t == "ConditionTask" or role == "condition")
                else ("action" if (t == "ActionTask" or role == "action") else None)
            )
            return self.task(v, r)
        if t in ("ActionList", "ConditionList"):
            return self.task(
                v if not isinstance(v, list) else {"$type": t, ("actions" if t == "ActionList" else "conditions"): v},
                "action" if t == "ActionList" else "condition",
            )
        m = re.match(r"^(?:List|IList)<(.+)>$", t) or re.match(r"^(.+)\[\]$", t)
        if m and isinstance(v, list):
            return [self.value(x, m.group(1), ctx) for x in v]
        return self.literal(v, t or None, ctx)

    def bbparam(self, v: Any, inner: Optional[str], ctx: Optional[ClassDecl], boxed: bool = False) -> dict:
        if isinstance(v, Raw):
            return v.value
        if isinstance(v, dict) and set(v) == {"raw"}:
            return v["raw"]
        if isinstance(v, dict) and (set(v) - {"$type"}) <= {"_value", "_name", "_targetVariableID", "_type"} and v:
            return v
        out: Dict[str, Any] = {}
        if boxed and inner:
            out["_type"] = self.types.full(inner, ctx)
        if v is None:
            return out
        if isinstance(v, str) and v.startswith("$"):
            name = v[1:]
            if "/" in name:
                from .owners import find_external_var

                gvar = find_external_var(self.ws, self.g, name)
                out["_name"] = name
                if gvar is not None and gvar.get("_id"):
                    out["_targetVariableID"] = gvar["_id"]
                return out
            var = self.var(v)
            if boxed and not inner:
                vt = self._var_value_type(var)
                if vt:
                    out["_type"] = vt if "[[" in vt else _first_part(vt)
            out["_name"] = var["_name"]
            out["_targetVariableID"] = var["_id"]
            return out
        lit = self.literal(v, inner, ctx, boxed=boxed)
        if not boxed and _is_default(lit):
            return out
        out["_value"] = lit
        return out

    def literal(self, v: Any, t: Optional[str], ctx: Optional[ClassDecl], boxed: bool = False) -> Any:
        if isinstance(v, Raw):
            return v.value
        if t is None:
            return v
        from .spec import dict_args

        kv = dict_args(t)
        if kv and isinstance(v, dict):
            enum = self.schema.enum_for(kv[0], ctx)
            if enum is not None:
                return [{"Key": self.enum_value(enum, k), "Value": self.value(x, kv[1], ctx)} for k, x in v.items()]
        simple = t.split(".")[-1]
        full = self.types.full(t, ctx)
        if simple in VECTOR_COMPONENTS or full.startswith("UnityEngine.") and simple in VECTOR_COMPONENTS:
            vec = self.vector(v, simple)
            if boxed:
                vec["$type"] = full
            return vec
        alias = {vv: k for k, vv in ALIASES.items()}.get(full, t)
        prim = None
        if alias in ("float", "double"):
            prim = float(v) if not isinstance(v, bool) else float(v)
        elif alias in ("int", "long", "short", "byte", "uint", "ulong", "ushort", "sbyte"):
            prim = int(v)
        elif alias == "bool":
            if not isinstance(v, bool):
                raise BuildError(f"expected bool, got {v!r}")
            prim = v
        elif alias == "string":
            prim = str(v)
        if prim is not None:
            return {"$content": prim, "$type": full} if boxed else prim
        enum = self.schema.find(full)
        if enum is not None and enum.kind == "enum":
            val = self.enum_value(enum, v)
            return {"$content": val, "$type": full} if boxed else val
        if self._is_unity_object(full, ctx):
            return self.object_ref(v)
        if isinstance(v, dict) and enum is not None and enum.kind in ("class", "struct"):
            obj = self.obj(full, v, with_type=boxed)
            return obj
        return v

    def enum_value(self, enum: ClassDecl, v: Any) -> int:
        if isinstance(v, int) and not isinstance(v, bool):
            return v
        names = {n.lower(): val for val, n in enum.enum_values().items()}
        total = 0
        for part in str(v).split("|"):
            key = part.strip().lower()
            if key not in names:
                raise BuildError(
                    f"{enum.name} has no member {part.strip()!r}; members: {', '.join(enum.enum_values().values())}"
                )
            total |= names[key]
        return total

    def vector(self, v: Any, simple: str) -> dict:
        comps = VECTOR_COMPONENTS.get(simple, "xyz")
        if isinstance(v, dict):
            src = v
        elif isinstance(v, (list, tuple)):
            src = dict(zip(comps, v))
        else:
            raise BuildError(f"expected a {simple} as [..] or {{..}}, got {v!r}")
        return {c: float(src[c]) for c in comps if c in src and float(src[c]) != 0.0}

    def _is_unity_object(self, full: str, ctx) -> bool:
        if full.split(".")[-1] in UNITY_OBJECT_BASES:
            return True
        decl = self.schema.find(full)
        if decl is None:
            return False
        return any(
            any(re.sub(r"<.*>", "", b).split(".")[-1] in UNITY_OBJECT_BASES for b in c.bases)
            for c in self.schema.chain(decl)
        )

    def object_ref(self, v: Any) -> int:
        from .container import ObjectRef

        if v is None:
            return 0
        if isinstance(v, int):
            return v
        slot = self.ws.find(str(v))
        if slot.bound:
            raise BuildError(f"{v!r} is a bound graph and cannot be referenced as an asset")
        guid = self.ws.project.path_to_guid(slot.path)
        if not guid:
            raise BuildError(f"no .meta/guid for {slot.path}")
        return self.g.ref_index(ObjectRef(11400000, guid, 2))

    def mapping(self, sub_graph, mapping: Dict[str, Any]) -> list:
        """``{"SubVar": "<-$Parent" | "->$Parent" | "<->$Parent" | literal}`` -> ``_variablesMap``."""
        sub = sub_graph if isinstance(sub_graph, Graph) else self.ws.graph(str(sub_graph))
        out = []
        for sub_name, spec in mapping.items():
            sv = sub.var_by_name(sub_name)
            if sv is None:
                raise BuildError(f"{sub.name} has no variable ${sub_name}")
            entry: Dict[str, Any] = {"_targetSubGraphVariableID": sv["_id"]}
            text = spec if isinstance(spec, str) else None
            direction = "<-"
            if text:
                m = re.match(r"^\s*(<->|<-|->|↔|←|→)\s*(.*)$", text)
                if m:
                    direction = {"↔": "<->", "←": "<-", "→": "->"}.get(m.group(1), m.group(1))
                    spec = m.group(2)
            if direction in ("->", "<->"):
                entry["_canRead"] = True
            if direction in ("<-", "<->"):
                entry["_canWrite"] = True
            from .schema import generic_args

            vt = (generic_args(sv.get("$type", "")) or [None])[0]
            entry["_type"] = vt.split(",")[0] if vt and "[[" not in vt else vt
            entry.update(
                {
                    k: v
                    for k, v in self.bbparam(spec, vt.split(",")[0] if vt else None, None, boxed=True).items()
                    if k != "_type"
                }
            )
            out.append(entry)
        return out

    # ------------------------------------------------------------------ reflection tasks

    def reflection_task(self, spec: dict) -> dict:
        kind = next(k for k in ("call", "check", "get", "set") if k in spec)
        target = spec[kind]
        if "." not in target:
            raise BuildError(f"{kind}: use Type.Member, got {target!r}")
        tname, member = target.rsplit(".", 1)
        decl = self.schema.find(tname) or self.schema.find_simple(tname.split(".")[-1])
        if decl is None:
            if "fieldType" in spec:
                full = self.types.full(tname)
                if not self.schema.resolves(full):
                    raise BuildError(
                        f"type {tname!r} is not in the project sources; for a System/Unity type use its "
                        "full name (System.Single, UnityEngine.Mathf)"
                    )
                return self.field_task(spec, kind, full, member, spec["fieldType"], None, static=False)
            raise BuildError(
                f"type {tname!r} not found in project sources"
                + (' (for a field of a System/Unity type give "fieldType")' if kind != "call" else "")
            )
        if "fieldType" in spec:
            raise BuildError("fieldType is only for fields of types outside the project sources")
        fdecl = self._field(decl, member)
        if fdecl is not None:
            return self.field_task(
                spec, kind, decl.full_name, fdecl.name, fdecl.type, decl, static="static" in fdecl.modifiers
            )
        if kind == "set":
            return self.set_property(spec, decl, member)
        args = spec.get("args") or []
        sig, is_prop = self._member(decl, member, args, spec.get("overload"))
        ret = sig["ret"]
        params = sig["params"]
        method = {
            "_baseInfo": f"{decl.full_name}|{('get_' + member) if is_prop else member}|{self.types.full(ret, decl)}",
            "_paramsInfo": "|".join(self.types.full(p, decl) for p in params),
        }
        values: Dict[str, Any] = {"method": Raw(method)}
        if not is_prop or kind == "call":
            values["parameters"] = Raw([self.bbparam(a, p, decl, boxed=True) for a, p in zip(args, params)])
        if kind == "check":
            if is_prop:
                values.pop("parameters", None)
            values["checkValue"] = Raw(self.bbparam(spec.get("value", True), ret, decl, boxed=True))
            if spec.get("compare", "==") != "==":
                values["comparison"] = Raw(
                    self.enum_value(self.schema.find_simple("CompareMethod"), COMPARE_NAMES[spec["compare"]])
                )
            type_name = "CheckProperty_Multiplatform" if is_prop else "CheckFunction_Multiplatform"
        elif kind == "get":
            values.pop("parameters", None)
            values["returnValue"] = Raw(self.bbparam(spec.get("returns"), ret, decl, boxed=True))
            type_name = "GetProperty_Multiplatform"
        else:
            if is_prop:
                raise BuildError(f'{member} is a property; use "get", "set" or "check"')
            values["returnValue"] = Raw(
                {} if ret == "void" else self.bbparam(spec.get("returns"), ret, decl, boxed=True)
            )
            type_name = "ExecuteFunction_Multiplatform"
        return self._reflected(type_name, values, spec, decl.full_name)

    def _reflected(self, type_name: str, values: Dict[str, Any], spec: dict, agent_type: str) -> dict:
        if spec.get("agent"):
            agent = self.agent(spec["agent"])
            agent["_type"] = agent_type
            values["overrideAgent"] = Raw(agent)
        for k in ("_isDisabled", "_invert"):
            if k in spec:
                values[k] = spec[k]
        full = self.resolve_type(type_name, TASK_ROLES)
        return self.obj(full, values, extra_ok={"_isDisabled", "_invert", "overrideAgent"})

    def set_property(self, spec: dict, decl: ClassDecl, member: str) -> dict:
        """SetProperty_Multiplatform: ``{"set": "Type.Property", "value": ...}``."""
        chain = self.schema.chain(decl)
        ptype = next((c.properties[member] for c in chain if member in c.properties), None)
        if ptype is None:
            raise BuildError(f"set: {decl.name}.{member} is not a field or property")
        if not any(member in c.setters for c in chain):
            raise BuildError(f"set: {decl.name}.{member} has no public setter")
        if spec.get("args"):
            raise BuildError(f"{decl.name}.{member} is a property and takes no args")
        method = {
            "_baseInfo": f"{decl.full_name}|set_{member}|System.Void",
            "_paramsInfo": self.types.full(ptype, decl),
        }
        values: Dict[str, Any] = {
            "method": Raw(method),
            "parameter": Raw(self.bbparam(spec.get("value"), ptype, decl, boxed=True)),
        }
        return self._reflected("SetProperty_Multiplatform", values, spec, decl.full_name)

    def _field(self, decl: ClassDecl, member: str):
        """The field ``member`` of the type or its source bases, unless a method/property of that name hides it."""
        for c in self.schema.chain(decl):
            if member in c.methods or member in c.properties:
                return None
            for f in c.fields:
                if f.name == member:
                    return f
        return None

    def field_task(
        self, spec: dict, kind: str, type_full: str, name: str, ftype: str, ctx: Optional[ClassDecl], static: bool
    ) -> dict:
        """GetField / SetField / CheckField: ``{"get"|"set"|"check": "Type.field", ...}``."""
        label = f"{simple_name(type_full)}.{name}"
        if kind == "call":
            raise BuildError(f'{label} is a field; use "get", "set" or "check"')
        if spec.get("args"):
            raise BuildError(f"{label} is a field and takes no args")
        if spec.get("agent") and static:
            raise BuildError(f"{label} is static and takes no agent")
        values: Dict[str, Any] = {"field": Raw({"_baseInfo": f"{type_full}|{name}"})}
        if kind == "set":
            values["setValue"] = Raw(self.bbparam(spec.get("value"), ftype, ctx, boxed=True))
            type_name = "SetField"
        elif kind == "get":
            values["saveAs"] = Raw(self.bbparam(spec.get("returns"), ftype, ctx, boxed=True))
            type_name = "GetField"
        else:
            values["checkValue"] = Raw(self.bbparam(spec.get("value", True), ftype, ctx, boxed=True))
            if spec.get("compare", "==") != "==":
                values["comparison"] = Raw(
                    self.enum_value(self.schema.find_simple("CompareMethod"), COMPARE_NAMES[spec["compare"]])
                )
            type_name = "CheckField"
        # these tasks migrate from an older layout: without "$version" Unity would read them as version 0
        values["$version"] = 1
        return self._reflected(type_name, values, spec, type_full)

    def _member(self, decl: ClassDecl, member: str, args: list, overload: Optional[list] = None) -> Tuple[dict, bool]:
        overloads, prop_type = [], None
        for c in self.schema.chain(decl):
            overloads += list(c.methods.get(member, []))
            if prop_type is None and member in c.properties:
                prop_type = c.properties[member]
        if prop_type is not None and not overloads:
            if args:
                raise BuildError(f"{decl.name}.{member} is a property and takes no args")
            return {"ret": prop_type, "params": []}, True
        cands = [o for o in overloads if len(o["params"]) == len(args)]
        if overload is not None:
            want = [str(x).split(".")[-1] for x in overload]
            cands = [o for o in cands if [p.split(".")[-1] for p in o["params"]] == want]
        if len(cands) > 1:
            cands = [o for o in cands if all(self._arg_fits(a, p, decl) for a, p in zip(args, o["params"]))] or cands
        if len(cands) != 1:
            sigs = "; ".join(f"{member}({', '.join(o['params'])})" for o in overloads) or "none"
            raise BuildError(f"cannot pick {decl.name}.{member} for {len(args)} arg(s); overloads: {sigs}")
        return cands[0], False

    def _arg_fits(self, arg: Any, ptype: str, ctx) -> bool:
        if isinstance(arg, str) and arg.startswith("$"):
            from .owners import find_external_var

            var = self.g.var_by_name(arg[1:]) or find_external_var(self.ws, self.g, arg[1:])
            vt = self._var_value_type(var) if var else None
            return vt is not None and vt.split(",")[0].split(".")[-1] == self.types.full(ptype, ctx).split(".")[-1]
        if isinstance(arg, bool):
            return ptype == "bool"
        if isinstance(arg, (int, float)):
            return ptype in ("float", "int", "double", "long")
        if isinstance(arg, list):
            return ptype.startswith(("Vector", "Quaternion", "Color"))
        if isinstance(arg, str):
            return ptype == "string" or self.schema.find_simple(ptype, ctx) is not None
        return True

    # ------------------------------------------------------------------ blackboard

    def new_variable(self, name: str, type_text: str, value: Any = None, public: bool = False) -> dict:
        qualified = self.types.qualify(type_text)
        var: Dict[str, Any] = {}
        if value is not None:
            lit = self.literal(value, type_text, None)
            if not _is_default(lit):
                var["_value"] = lit
        var["_name"] = name
        var["_id"] = str(uuid.uuid4())
        if public:
            var["_isPublic"] = True
        var["$type"] = f"NodeCanvas.Framework.Variable`1[[{qualified}]]"
        return var


def _first_part(qualified: str) -> str:
    return qualified.split(",")[0].strip()


def _is_default(v: Any) -> bool:
    if v is None or v is False:
        return True
    if isinstance(v, (int, float)) and not isinstance(v, bool) and v == 0:
        return True
    if v == {}:
        return True
    return False


def _split_generic(s: str) -> List[str]:
    out, depth, cur = [], 0, ""
    for ch in s:
        if ch == "<":
            depth += 1
        elif ch == ">":
            depth -= 1
        if ch == "," and depth == 0:
            out.append(cur.strip())
            cur = ""
        else:
            cur += ch
    out.append(cur.strip())
    return out
