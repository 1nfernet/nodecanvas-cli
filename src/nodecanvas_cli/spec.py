"""Serialized JSON -> the short spec form accepted by ``nodecanvas-cli apply`` (inverse of ``build.Builder``)."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from .csharp import ClassDecl
from .model import Graph
from .render import UNITY_OBJECT_BASES, _bb_inner
from .schema import Schema, short_generic, simple_name
from .typenames import ALIASES, TypeNames

REFLECTION = {
    "ExecuteFunction_Multiplatform": "call",
    "CheckFunction_Multiplatform": "check",
    "CheckProperty_Multiplatform": "check",
    "GetProperty_Multiplatform": "get",
}
FIELD_TASKS = {"GetField": "get", "SetField": "set", "CheckField": "check"}
COMPARE_SYMBOLS = {"GreaterThan": ">", "LessThan": "<", "GreaterOrEqualTo": ">=", "LessOrEqualTo": "<="}
VECTOR_COMPONENTS = {
    "Vector2": "xy",
    "Vector2Int": "xy",
    "Vector3": "xyz",
    "Vector3Int": "xyz",
    "Vector4": "xyzw",
    "Quaternion": "xyzw",
    "Color": "rgba",
}
NESTED_KEYS = ("_subTree", "_nestedBT", "_nestedFSM", "_nestedDLG", "_subGraph")


class Speccer:
    def __init__(self, ws, g: Graph):
        self.ws = ws
        self.g = g
        self.schema: Schema = ws.schema

    # ------------------------------------------------------------------ entry points

    def node(self, node: dict) -> Dict[str, Any]:
        out: Dict[str, Any] = {"$type": self._type_name(node.get("$type", ""))}
        out.update(self.fields(node, skip={"_position", "_variablesMap"}))
        if node.get("_variablesMap"):
            out["map"] = self.mapping(node)
        return out

    def task(self, task: Any) -> Any:
        if not isinstance(task, dict) or "$type" not in task:
            return task
        name = simple_name(task["$type"])
        if name in REFLECTION and task["$type"].startswith("NodeCanvas.Tasks."):
            try:
                return self.reflection(task, REFLECTION[name])
            except (KeyError, ValueError, IndexError):
                pass
        if name in FIELD_TASKS and task["$type"].startswith("NodeCanvas.Tasks."):
            try:
                return self.field_task(task, FIELD_TASKS[name])
            except (KeyError, ValueError, IndexError, AttributeError):
                pass
        if name == "SetProperty_Multiplatform" and task["$type"].startswith("NodeCanvas.Tasks."):
            try:
                return self.set_property(task)
            except (KeyError, ValueError, IndexError, AttributeError):
                pass
        if name in ("ConditionList", "ActionList") and set(task) <= {"conditions", "actions", "$type"}:
            return [self.task(t) for t in task.get("conditions") or task.get("actions") or []]
        out: Dict[str, Any] = {"$type": self._type_name(task["$type"])}
        out.update(self.fields(task, skip=set()))
        return out

    # ------------------------------------------------------------------ fields

    def fields(self, obj: dict, skip) -> Dict[str, Any]:
        t = obj.get("$type")
        decl_fields = {n: (f, c) for n, f, c in self.schema.ordered_fields(t)} if t else {}
        out: Dict[str, Any] = {}
        for k, v in obj.items():
            if k in skip or k in ("$type", "$id"):
                continue
            if k == "overrideAgent":
                out["agent"] = (
                    self.var_ref(v)
                    if isinstance(v, dict) and (v.get("_name") or v.get("_targetVariableID"))
                    else {"raw": v}
                )
                continue
            f = decl_fields.get(k)
            out[k] = self.value(v, f[0].type if f else None, f[1] if f else None)
        return out

    def value(self, v: Any, ftype: Optional[str], ctx: Optional[ClassDecl]) -> Any:
        t = re.sub(r"\s+", "", ftype or "")
        inner = _bb_inner(t)
        if inner is not None:
            return self.param(v, inner, ctx)
        if t == "BBParameter" or t.startswith("BBObjectParameter"):
            # an object parameter's literal is boxed with its runtime type, which only the JSON knows
            if isinstance(v, dict) and "_value" in v and not (v.get("_name") or v.get("_targetVariableID")):
                return {"raw": v}
            return self.param(v, None, ctx)
        if isinstance(v, dict) and "$type" in v and self.schema.role(v["$type"]) in ("action", "condition", "task"):
            return self.task(v)
        if t in ("ActionTask", "ConditionTask") and v is None:
            return None
        m = re.match(r"^(?:List|IList)<(.+)>$", t) or re.match(r"^(.+)\[\]$", t)
        if m and isinstance(v, list):
            return [self.value(x, m.group(1), ctx) for x in v]
        return self.literal(v, t or None, ctx)

    def param(self, d: Any, inner: Optional[str], ctx) -> Any:
        if not isinstance(d, dict):
            return {"raw": d}
        if d.get("_name") or d.get("_targetVariableID"):
            return self.var_ref(d)
        if "_value" in d:
            return self.literal(d["_value"], inner or d.get("_type"), ctx)
        if not (set(d) - {"_type", "$type"}):
            return None
        return {"raw": d}

    def var_ref(self, d: dict) -> Any:
        vid = d.get("_targetVariableID")
        var = self.g.var_by_id(vid) if vid else None
        if var is not None:
            return "$" + var.get("_name", d.get("_name", "?"))
        if d.get("_name") and "/" in d["_name"] and not vid:
            return "$" + d["_name"]
        if d.get("_name") and self.g.var_by_name(d["_name"]) is None:
            from .owners import find_external_var

            ext = find_external_var(self.ws, self.g, d["_name"])
            if ext is not None and (not vid or ext.get("_id") == vid):
                return "$" + d["_name"]
        return {"raw": d}

    def literal(self, v: Any, t: Optional[str], ctx) -> Any:
        kv = dict_args(t)
        if kv and isinstance(v, list) and all(isinstance(e, dict) and set(e) <= {"Key", "Value"} for e in v):
            enum = self.schema.enum_for(kv[0], ctx)
            if enum is not None and all(isinstance(e.get("Key"), int) for e in v):
                return {enum.enum_name(e["Key"]): self.value(e.get("Value"), kv[1], ctx) for e in v}
        if isinstance(v, dict) and "$content" in v:
            return self.literal(v["$content"], v.get("$type") or t, ctx)
        simple = (t or "").split(".")[-1]
        if isinstance(v, dict) and (
            simple in VECTOR_COMPONENTS or (v.get("$type") or "").split(".")[-1] in VECTOR_COMPONENTS
        ):
            vt = simple if simple in VECTOR_COMPONENTS else v["$type"].split(".")[-1]
            return [v.get(c, 0.0) for c in VECTOR_COMPONENTS[vt]]
        if t and isinstance(v, int) and not isinstance(v, bool):
            enum = self.schema.find(t) or self.schema.enum_for(t, ctx)
            if enum is not None and enum.kind == "enum":
                return enum.enum_name(v)
            if self._is_unity_object(t, ctx):
                ref = self.g.ref(v)
                if ref is None or ref.is_null:
                    return None
                path = self.ws.ref_path(ref)
                return self.ws.ref_display(ref) if path else {"raw": v}
        if isinstance(v, dict) and t:
            decl = self.schema.find(t) or self.schema.find_simple(simple.split("<")[0], ctx)
            if decl is not None and decl.kind in ("class", "struct"):
                fields = {n: (f, c) for n, f, c in self.schema.ordered_fields(decl.full_name)}
                out = {}
                for k, x in v.items():
                    if k == "$type":
                        continue
                    f = fields.get(k)
                    out[k] = self.value(x, f[0].type if f else None, f[1] if f else None)
                return out
        if isinstance(v, (dict, list)):
            return {"raw": v}
        return v

    def _is_unity_object(self, t: str, ctx) -> bool:
        name = t.split("<")[0].split(".")[-1]
        if name in UNITY_OBJECT_BASES:
            return True
        decl = self.schema.find(t) or self.schema.find_simple(name, ctx)
        if decl is None:
            return False
        return any(
            any(re.sub(r"<.*>", "", b).split(".")[-1] in UNITY_OBJECT_BASES for b in c.bases)
            for c in self.schema.chain(decl)
        )

    # ------------------------------------------------------------------ special shapes

    def reflection(self, task: dict, kind: str) -> Dict[str, Any]:
        type_name, raw_member, _ = (task["method"]["_baseInfo"].split("|") + ["", ""])[:3]
        if not self.schema.member_kinds(type_name, raw_member):
            raise ValueError("member not found in sources; keep the serialized form")
        member = raw_member[4:] if raw_member.startswith("get_") else raw_member
        out: Dict[str, Any] = {kind: f"{simple_name(type_name)}.{member}"}
        params = task.get("parameters") or []
        info_types = (task["method"].get("_paramsInfo") or "").split("|")
        if params:
            args = []
            for k, p in enumerate(params):
                sig_t = info_types[k] if k < len(info_types) else None
                if p.get("_type") and sig_t and p["_type"].split(",")[0] != sig_t.split(",")[0]:
                    args.append({"raw": p})  # stale binding: argument type differs from the signature
                else:
                    args.append(self.param(p, p.get("_type"), None))
            out["args"] = args
        decl = self.schema.find(type_name)
        sigs = []
        for c in self.schema.chain(decl) if decl else []:
            sigs += [o["params"] for o in c.methods.get(raw_member, []) if len(o["params"]) == len(params)]
        if len(sigs) > 1:
            info = task["method"].get("_paramsInfo") or ""
            out["overload"] = [simple_name(p) for p in info.split("|")] if info else []
        oa = task.get("overrideAgent")
        if isinstance(oa, dict) and (oa.get("_name") or oa.get("_targetVariableID")):
            out["agent"] = self.var_ref(oa)
        if kind == "check":
            val = self.param(task.get("checkValue") or {}, (task.get("checkValue") or {}).get("_type"), None)
            if val is not True:
                out["value"] = val
            cmp_ = task.get("comparison")
            if cmp_:
                enum = self.schema.find_simple("CompareMethod")
                name = enum.enum_name(cmp_) if enum is not None else str(cmp_)
                out["compare"] = COMPARE_SYMBOLS.get(name, name)
        else:
            ret = task.get("returnValue") or {}
            if ret.get("_name") or ret.get("_targetVariableID"):
                out["returns"] = self.var_ref(ret)
        for k in ("_isDisabled", "_invert"):
            if task.get(k):
                out[k] = True
        return out

    def field_task(self, task: dict, kind: str) -> Dict[str, Any]:
        if task.get("$version") != 1:
            raise ValueError("an older layout; keep the serialized form")
        type_name, fname = task["field"]["_baseInfo"].split("|")
        key = {"set": "setValue", "get": "saveAs", "check": "checkValue"}[kind]
        p = task.get(key) or {}
        types = TypeNames(self.ws)
        decl = self.schema.find(type_name)
        if decl is not None:
            fdecl = None
            for c in self.schema.chain(decl):
                if fname in c.methods or fname in c.properties:
                    break
                fdecl = next((f for f in c.fields if f.name == fname), None)
                if fdecl is not None:
                    break
            if fdecl is None:
                raise ValueError("field not found in sources; keep the serialized form")
            out: Dict[str, Any] = {kind: f"{simple_name(type_name)}.{fname}"}
            expected = types.full(fdecl.type, decl)
        elif self.schema.resolves(type_name) and isinstance(p.get("_type"), str):
            # a System/Unity type: only the boxed value tells the field's type
            expected = p["_type"]
            field_type = {v: k for k, v in ALIASES.items()}.get(expected, expected)
            if types.full(field_type) != expected:
                raise ValueError("a field type the spec cannot name")
            out = {kind: f"{type_name}.{fname}", "fieldType": field_type}
        else:
            raise ValueError("type not found; keep the serialized form")
        if p.get("_type") != expected:
            raise ValueError("the value is boxed with another type than the field's")
        self._agent(task, out)
        val = self.param(p, expected, None)
        if kind == "get":
            if set(p) - {"_type"}:
                out["returns"] = val
        elif kind == "set" or val is not True:
            out["value"] = val
        if kind == "check" and task.get("comparison"):
            enum = self.schema.find_simple("CompareMethod")
            name = enum.enum_name(task["comparison"]) if enum is not None else str(task["comparison"])
            out["compare"] = COMPARE_SYMBOLS.get(name, name)
        return self._flags(task, out, {"field", key, "comparison", "$version"})

    def set_property(self, task: dict) -> Dict[str, Any]:
        method = task["method"]
        type_name, accessor, ret = method["_baseInfo"].split("|")
        if not accessor.startswith("set_") or ret != "System.Void" or set(method) - {"_baseInfo", "_paramsInfo"}:
            raise ValueError("not a plain property setter")
        prop = accessor[4:]
        decl = self.schema.find(type_name)
        chain = self.schema.chain(decl) if decl else []
        ptype = next((c.properties[prop] for c in chain if prop in c.properties), None)
        if ptype is None or not any(prop in c.setters for c in chain):
            raise ValueError("no settable property in sources; keep the serialized form")
        expected = TypeNames(self.ws).full(ptype, decl)
        p = task.get("parameter") or {}
        if method.get("_paramsInfo") != expected or p.get("_type") != expected:
            raise ValueError("the value is boxed with another type than the property's")
        out: Dict[str, Any] = {"set": f"{simple_name(type_name)}.{prop}"}
        self._agent(task, out)
        out["value"] = self.param(p, expected, None)
        return self._flags(task, out, {"method", "parameter"})

    def _agent(self, task: dict, out: Dict[str, Any]) -> None:
        oa = task.get("overrideAgent")
        if oa is not None:
            if not (isinstance(oa, dict) and (oa.get("_name") or oa.get("_targetVariableID"))):
                raise ValueError("an agent that is not a variable")
            out["agent"] = self.var_ref(oa)

    def _flags(self, task: dict, out: Dict[str, Any], own: set) -> Dict[str, Any]:
        for k in ("_isDisabled", "_invert"):
            if task.get(k):
                out[k] = True
        extra = set(task) - own - {"overrideAgent", "_isDisabled", "_invert", "$type"}
        if extra:
            raise ValueError(f"unexpected keys {sorted(extra)}")
        return out

    def mapping(self, node: dict) -> Dict[str, Any]:
        sub = None
        for k in NESTED_KEYS:
            v = node.get(k)
            if isinstance(v, dict) and "_value" in v:
                ref = self.g.ref(v["_value"])
                sub = self.ws.ref_graph(ref) if ref is not None else None
        names = {x.get("_id"): x.get("_name") for x in sub.variables.values()} if sub is not None else {}
        out: Dict[str, Any] = {}
        for m in node.get("_variablesMap") or []:
            key = names.get(m.get("_targetSubGraphVariableID"), m.get("_targetSubGraphVariableID"))
            r, w = m.get("_canRead", False), m.get("_canWrite", False)
            arrow = "<->" if r and w else ("->" if r else "<-")
            if m.get("_name") or m.get("_targetVariableID"):
                out[key] = arrow + self.var_ref(m) if isinstance(self.var_ref(m), str) else {"raw": m}
            else:
                lit = self.literal(m.get("_value"), m.get("_type"), None)
                out[key] = lit if not r else {"raw": m}
        return out

    def _type_name(self, full: str) -> str:
        name = short_generic(full)
        cands = [c for c in self.schema._load_simple(simple_name(full)) if c.kind == "class"]
        return name if len(cands) <= 1 or "<" in name else name if self._unique_in_kind(full, cands) else full

    def _unique_in_kind(self, full: str, cands: List[ClassDecl]) -> bool:
        from .build import KIND_NAMESPACES

        ns = KIND_NAMESPACES.get(self.g.kind)
        same = [c for c in cands if ns and c.namespace == ns]
        return len(same) == 1 and same[0].full_name == full.split("[")[0]


def dict_args(t: Optional[str]):
    """``Dictionary<K, V>`` -> ``(K, V)`` (top-level comma), else ``None``."""
    import re as _re

    m = _re.match(r"^\s*(?:System\.Collections\.Generic\.)?Dictionary<(.+)>\s*$", t or "")
    if not m:
        return None
    body, depth = m.group(1), 0
    for i, ch in enumerate(body):
        depth += ch == "<"
        depth -= ch == ">"
        if ch == "," and depth == 0:
            return body[:i].strip(), body[i + 1 :].strip()
    return None
