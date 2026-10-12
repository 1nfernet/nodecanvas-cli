"""Compact, human/agent-readable rendering of values, tasks and nodes.

Notation:
  ``$name``   blackboard variable           ``@name``  agent override variable
  ``∅``       default value of a field     ``<Asset>`` Unity object reference
Field defaults come from the project's C# sources, so omitted (default) values are shown
with their real meaning.
"""

from __future__ import annotations

import re
from typing import Optional

from .csharp import ClassDecl
from .model import Graph
from .schema import Schema, short_generic, simple_name

COMPARE = {"EqualTo": "==", "GreaterThan": ">", "LessThan": "<", "GreaterOrEqualTo": ">=", "LessOrEqualTo": "<="}
OPERATION = {"Set": "=", "Add": "+=", "Subtract": "-=", "Multiply": "*=", "Divide": "/="}
UNITY_OBJECT_BASES = {
    "Object",
    "GameObject",
    "Component",
    "Behaviour",
    "MonoBehaviour",
    "ScriptableObject",
    "Transform",
    "Graph",
    "AudioClip",
    "Texture",
    "Texture2D",
    "Sprite",
    "Material",
    "Mesh",
    "AnimationClip",
    "RuntimeAnimatorController",
    "TextAsset",
}
NODE_BASE_FIELDS = {
    "_UID",
    "_name",
    "_tag",
    "_position",
    "_comment",
    "_isBreakpoint",
    "_collapsed",
    "_color",
    "_verboseLevel",
    "$type",
    "$id",
    "$version",
}
TASK_BASE_FIELDS = {"$type", "$version", "_isDisabled", "overrideAgent", "_invert"}


CS_ALIASES = {
    "Boolean": "bool",
    "Single": "float",
    "Double": "double",
    "Int32": "int",
    "Int64": "long",
    "String": "string",
    "Object": "object",
    "Byte": "byte",
    "Char": "char",
    "UInt32": "uint",
}
VALUE_DEFAULTS = {
    "bool": "false",
    "float": "0",
    "double": "0",
    "int": "0",
    "long": "0",
    "uint": "0",
    "byte": "0",
    "Vector2": "(0, 0)",
    "Vector3": "(0, 0, 0)",
    "Vector4": "(0, 0, 0, 0)",
    "Quaternion": "(0, 0, 0, 0)",
    "Color": "rgba(0, 0, 0, 0)",
}


def type_alias(name: str) -> str:
    """``System.Single`` / ``Single`` -> ``float``; ``List<Pawn>`` stays as is."""
    base = name.split(".")[-1]
    return CS_ALIASES.get(base, base)


def default_for_type(ftype: Optional[str]) -> str:
    if not ftype:
        return "∅"
    t = _bb_inner(ftype) or ftype
    t = type_alias(t.split("<")[0] if not t.startswith(("List<", "Dictionary<")) else t)
    return VALUE_DEFAULTS.get(t, "null")


def _short_ns(type_name: str) -> str:
    return short_generic(type_name)


class Renderer:
    def __init__(self, graph: Graph, schema: Optional[Schema], resolve_ref=None, sub_vars=None):
        self.positions = False
        self.resolved = set()  # (variable id, name) found outside the local blackboard (owner / global)
        self.g = graph
        self.schema = schema
        self.resolve_ref = resolve_ref  # (ObjectRef) -> display str
        self.sub_vars = sub_vars  # (ObjectRef) -> {var_id: name}

    # ------------------------------------------------------------------ values

    def var(self, name: Optional[str], vid: Optional[str]) -> str:
        if vid:
            v = self.g.var_by_id(vid)
            if v is not None:
                return "$" + v.get("_name", name or "?")
            if (vid, name) in self.resolved:
                return f"${name}"
            return f"${name}!" if name else f"$<missing {vid[:8]}>"
        if name:
            return "$" + name
        return "∅"

    def objref(self, index) -> str:
        r = self.g.ref(index)
        if r is None or r.is_null:
            return "null"
        if self.resolve_ref:
            return self.resolve_ref(r)
        return f"<{r.guid or r.file_id}>"

    def _is_unity_object(self, type_name: Optional[str], ctx: Optional[ClassDecl]) -> bool:
        if not type_name:
            return False
        t = type_name.split("<")[-1].rstrip(">").strip()
        name = t.split(".")[-1]
        if name in UNITY_OBJECT_BASES:
            return True
        if self.schema is None:
            return False
        decl = self.schema.find_simple(name, ctx)
        if decl is None:
            return False
        for c in self.schema.chain(decl):
            if any(re.sub(r"<.*>", "", b).split(".")[-1] in UNITY_OBJECT_BASES for b in c.bases):
                return True
        return False

    def default_of(self, init: Optional[str], ftype: Optional[str], ctx: Optional[ClassDecl]) -> str:
        if init is None:
            if ftype and self.schema is not None and self.schema.enum_for(_bb_inner(ftype) or ftype, ctx) is not None:
                enum = self.schema.enum_for(_bb_inner(ftype) or ftype, ctx)
                return enum.enum_values().get(0, "0")
            return default_for_type(ftype)
        t = init.strip()
        t = re.sub(r"(?<=\d)[fFdDmM]$", "", t)
        m = re.match(r"^new\s+BBParameter<[^>]+>\s*\(\s*(.*)\s*\)$", t)
        if m:
            t = m.group(1) or "∅"
        if "." in t and re.match(r"^[\w.]+$", t) and not re.match(r"^-?\d", t):
            t = t.split(".")[-1]
        return t

    def literal(self, v, ftype: Optional[str] = None, ctx: Optional[ClassDecl] = None) -> str:
        if isinstance(v, bool):
            return "true" if v else "false"
        if isinstance(v, (int, float)):
            enum = self.schema.enum_for(ftype, ctx) if (self.schema and ftype and not isinstance(v, float)) else None
            if enum is not None:
                return enum.enum_name(int(v))
            if ftype and self._is_unity_object(ftype, ctx) and not isinstance(v, float):
                return self.objref(v)
            return repr(v) if isinstance(v, float) else str(v)
        if isinstance(v, str):
            return '"' + v.replace("\n", "\\n") + '"'
        if v is None:
            return "null"
        if isinstance(v, list):
            return "[" + ", ".join(self.literal(x, _elem_type(ftype), ctx) for x in v) + "]"
        if isinstance(v, dict):
            return self.dict_value(v, ftype, ctx)
        return str(v)

    def dict_value(self, d: dict, ftype: Optional[str] = None, ctx: Optional[ClassDecl] = None) -> str:
        if "$content" in d:
            t = d.get("$type")
            enum = self.schema.find(t) if (self.schema and t) else None
            if enum is not None and enum.kind == "enum" and isinstance(d["$content"], int):
                return enum.enum_name(d["$content"])
            return self.literal(d["$content"], t, ctx)
        if "_baseInfo" in d:
            return member_display(d["_baseInfo"])
        if "$ref" in d and len(d) == 1:
            return f"#{d['$ref']}"
        if _is_bbparam(d, ftype):
            return self.param(d, ftype, ctx)
        t = d.get("$type")
        if t and self.schema is not None and self.schema.role(t) in ("action", "condition", "task"):
            return self.task(d)
        keys = [k for k in d if not k.startswith("$")]
        vt = (t or _bb_inner(ftype) or ftype or "").split(".")[-1]
        if set(keys) <= {"x", "y", "z", "w"} and (keys or vt.startswith(("Vector", "Quaternion"))):
            n = {"Vector2": 2, "Vector2Int": 2, "Vector4": 4, "Quaternion": 4}.get(vt, 4 if "w" in d else 3)
            return "(" + ", ".join(_num(d.get(c, 0)) for c in "xyzw"[:n]) + ")"
        if set(keys) <= {"r", "g", "b", "a"} and keys:
            return "rgba(" + ", ".join(_num(d.get(c, 0)) for c in "rgba") + ")"
        name = _short_ns(t) if t else ""
        inner = self.fields_inline(d, t)
        return f"{name}{{{inner}}}" if name else "{" + inner + "}"

    def param(
        self, d: dict, ftype: Optional[str] = None, ctx: Optional[ClassDecl] = None, init: Optional[str] = None
    ) -> str:
        if "_name" in d or "_targetVariableID" in d:
            return self.var(d.get("_name"), d.get("_targetVariableID"))
        if "_value" in d:
            inner = _bb_inner(ftype) or d.get("_type")
            return self.literal(d["_value"], inner, ctx)
        # A serialized BBParameter without "_value" holds default(T): FullSerializer omits members
        # equal to a freshly constructed BBParameter, and deserialization creates a new instance.
        inner = _bb_inner(ftype) or d.get("_type")
        if inner and self.schema is not None:
            enum = self.schema.enum_for(inner, ctx)
            if enum is not None:
                return enum.enum_values().get(0, "0")
        return default_for_type(inner) if inner else "∅"

    def field(self, owner: dict, name: str, decl_fields=None, ctx=None) -> str:
        f = (decl_fields or {}).get(name)
        ftype = f.type if f else None
        init = f.init if f else None
        if name not in owner:
            return self.default_of(init, ftype, ctx)
        v = owner[name]
        if v == {} and ftype is None:
            return "default"
        if isinstance(v, dict) and _is_bbparam(v, ftype):
            return self.param(v, ftype, ctx, init)
        return self.literal(v, ftype, ctx)

    def fields_inline(self, d: dict, type_name: Optional[str], skip=()) -> str:
        decl = self.schema.find(type_name) if (self.schema and type_name) else None
        fields = self.schema.fields(type_name) if decl is not None else {}
        parts = []
        for k, v in d.items():
            if k in skip or k.startswith("$") or k in NODE_BASE_FIELDS or k in TASK_BASE_FIELDS:
                continue
            f = fields.get(k)
            rendered = self.field(d, k, fields, decl)
            if f is not None and rendered == self.default_of(f.init, f.type, decl):
                continue
            if rendered in ("∅", "{}"):
                continue
            parts.append(f"{k}={rendered}")
        return ", ".join(parts)

    # ------------------------------------------------------------------ tasks

    def owner(self, task: dict, info: dict) -> str:
        """Who a reflected member is read from: ``@Var`` for an overridden agent, else the member's type
        (the graph's agent for instance members, the type itself for static ones)."""
        if task.get("overrideAgent"):
            return self.agent(task)
        return simple_name((info.get("_baseInfo") or "?").split("|")[0])

    def agent(self, task: dict) -> str:
        oa = task.get("overrideAgent")
        if isinstance(oa, dict) and (oa.get("_name") or oa.get("_targetVariableID")):
            return "@" + self.var(oa.get("_name"), oa.get("_targetVariableID"))[1:]
        if isinstance(oa, dict) and "_value" in oa:
            return "@" + self.literal(oa["_value"], "Component")
        return "self"

    def task(self, task: Optional[dict]) -> str:
        if not isinstance(task, dict):
            return "∅"
        t = task.get("$type", "?")
        try:
            text = self._task_template(task, t)
        except (KeyError, TypeError, ValueError):
            text = None
        if text is None:
            inner = self.fields_inline(task, t)
            text = f"{_short_ns(t)}({inner})" if inner else _short_ns(t)
        if task.get("_invert"):
            text = f"NOT {text}"
        if task.get("_isDisabled"):
            text = f"[off] {text}"
        oa = task.get("overrideAgent")
        if (
            isinstance(oa, dict)
            and (oa.get("_name") or oa.get("_targetVariableID") or "_value" in oa)
            and "@" not in text
        ):
            text = f"{text} on {self.agent(task)}"
        return text

    def recovered(self, missing: dict) -> Optional[dict]:
        """The object NodeCanvas would restore from a Missing* wrapper, if its type now exists."""
        mt = missing.get("_missingType")
        if not mt or self.schema is None or not self.schema.resolves(mt):
            return None
        try:
            from . import fsjson

            obj = fsjson.loads(missing.get("_recoveryState") or "{}")
        except ValueError:
            return None
        if not isinstance(obj, dict):
            return None
        obj["$type"] = mt
        return obj

    def _cmp(self, task: dict, field: str, t: str) -> str:
        return COMPARE.get(self.field(task, field, self._fields(t), self._decl(t)), "==")

    def _fields(self, t):
        return self.schema.fields(t) if self.schema else {}

    def _decl(self, t):
        return self.schema.find(t) if self.schema else None

    def _f(self, task, name, t):
        return self.field(task, name, self._fields(t), self._decl(t))

    def _member(self, task: dict) -> str:
        """Member name, with ``‹T1, T2›`` when the source has several overloads of that arity."""
        method = task.get("method") or {}
        name = _member_name(method)
        parts = (method.get("_baseInfo") or "").split("|")
        info = method.get("_paramsInfo") or ""
        if self.schema is not None and len(parts) > 1 and not parts[1].startswith("get_"):
            arity = len(info.split("|")) if info else 0
            if len(self.schema.overloads(parts[0], parts[1], arity)) > 1:
                name += "‹" + ", ".join(short_generic(p) for p in info.split("|")) + "›" if info else "‹›"
        return name

    def _args(self, task: dict) -> str:
        params = task.get("parameters") or []
        info = (task.get("method") or {}).get("_paramsInfo") or ""
        types = info.split("|") if info else []
        out = []
        for i, p in enumerate(params):
            pt = types[i] if i < len(types) else None
            out.append(self.param(p, f"BBParameter<{pt}>" if pt else None) if isinstance(p, dict) else self.literal(p))
        return ", ".join(out)

    def _task_template(self, task: dict, t: str) -> Optional[str]:
        if not (t.startswith("NodeCanvas.Tasks.") or t.startswith("NodeCanvas.Framework.")):
            return None
        name = simple_name(t)
        f = lambda k: self._f(task, k, t)  # noqa: E731
        if name == "ExecuteFunction_Multiplatform":
            call = f"{self.owner(task, task['method'])}.{self._member(task)}({self._args(task)})"
            ret = task.get("returnValue")
            if isinstance(ret, dict) and (ret.get("_name") or ret.get("_targetVariableID")):
                return f"{self.param(ret)} = {call}"
            return call
        if name in ("CheckFunction_Multiplatform", "CheckProperty_Multiplatform"):
            m = self._member(task)
            call = f"{self.owner(task, task['method'])}.{m}" + (
                f"({self._args(task)})" if name.startswith("CheckFunction") else ""
            )
            cv = task.get("checkValue")
            val = self.param(cv) if isinstance(cv, dict) else "∅"
            op = self._cmp(task, "comparison", t)
            if op == "==" and val == "true":
                return call
            if op == "==" and val == "false":
                return f"!{call}"
            return f"{call} {op} {val}"
        if name == "GetProperty_Multiplatform":
            target = self.param(task.get("returnValue") or {})
            return f"{target} = {self.owner(task, task['method'])}.{_member_name(task['method'])}"
        if name == "SetProperty_Multiplatform":
            params = task.get("parameter") or task.get("parameters")
            val = (
                self.param(params[0])
                if isinstance(params, list) and params
                else (self.param(params) if isinstance(params, dict) else "∅")
            )
            return f"{self.owner(task, task['method'])}.{_member_name(task['method'])} = {val}"
        if name in ("GetField", "GetField_Multiplatform"):
            return f"{f('saveAs')} = {self.owner(task, task['field'])}.{_member_name(task['field'])}"
        if name in ("SetField", "SetField_Multiplatform"):
            return f"{self.owner(task, task['field'])}.{_member_name(task['field'])} = {f('setValue')}"
        if name == "CheckField":
            m = f"{self.owner(task, task['field'])}.{_member_name(task['field'])}"
            cv = task.get("checkValue")
            val = self.param(cv) if isinstance(cv, dict) else "∅"
            op = self._cmp(task, "comparison", t)
            if op == "==" and val in ("true", "false"):
                return m if val == "true" else f"!{m}"
            return f"{m} {op} {val}"
        if name == "SetBoolean":
            mode = f("setTo")
            return f"{f('boolVariable')} = {'!' + f('boolVariable') if mode == 'Toggle' else mode.lower()}"
        if name in ("SetFloat", "SetInt", "SetVector3"):
            op = OPERATION.get(
                f("Operation") if ("Operation" in task or "Operation" in self._fields(t)) else f("operation"), "="
            )
            per = " per sec" if task.get("perSecond") else ""
            return f"{f('valueA')} {op} {f('valueB')}{per}"
        if name in ("SetFloatRandom", "SetIntRandom"):
            target = "floatVariable" if name == "SetFloatRandom" else "intVariable"
            return f"{f(target)} = rand({f('minValue')}, {f('maxValue')})"
        if name == "SetVariable":
            return f"{f('valueA')} = {f('valueB')}"
        if name == "CheckBoolean":
            a, b = f("valueA"), f("valueB")
            return a if b == "true" else (f"!{a}" if b == "false" else f"{a} == {b}")
        if name in ("CheckFloat", "CheckInt"):
            return f"{f('valueA')} {self._cmp(task, 'checkType', t)} {f('valueB')}"
        if name in ("CheckVariable", "CheckString", "CheckVector3"):
            return f"{f('valueA')} == {f('valueB')}"
        if name == "CheckNull":
            return f"{f('variable')} == null"
        if name == "Wait":
            fin = f("finishStatus")
            wt = f("waitTime")
            return f"wait {wt}{'s' if re.match(r'^[0-9.]+$', wt) else ''}" + (
                "" if fin in ("Success", "∅") else f" -> {fin}"
            )
        if name == "ConditionList":
            items = [self.task(c) for c in task.get("conditions") or []]
            mode = f("checkMode")
            joiner = " || " if mode == "AnyTrueSuffice" else " && "
            return "(" + joiner.join(items) + ")" if items else "(no conditions)"
        if name == "ActionList":
            items = [self.task(a) for a in task.get("actions") or []]
            mode = f("executionMode")
            joiner = " | " if mode == "ActionsRunInParallel" else "; "
            return "[" + joiner.join(items) + "]" if items else "[no actions]"
        if name == "GetListCount":
            return f"{f('saveAs')} = count({f('targetList')})"
        if name in ("MissingAction", "MissingCondition"):
            mt = task.get("_missingType", "?")
            recovered = self.recovered(task)
            if recovered is not None:
                return f"{self.task(recovered)} (recovered)"
            return f"MISSING {short_generic(mt)}"
        return None

    # ------------------------------------------------------------------ nodes

    def at(self, node: dict) -> str:
        """`` @(x,y)`` when positions are requested."""
        if not self.positions:
            return ""
        p = node.get("_position")
        if not isinstance(p, dict):
            return " @(?)"
        return f" @({_coord(p.get('x', 0))},{_coord(p.get('y', 0))})"

    def node_label(self, node: dict) -> str:
        t = node.get("$type", "?")
        name = simple_name(t)
        title = _short_ns(t)
        decl = self._decl(t)
        if decl is not None:
            disp = decl.attr_str("Name")
            if disp and simple_name(t) not in ("ActionNode", "ConditionNode"):
                title = disp.replace(" ", "")
        parts = [title]
        if node.get("_name"):
            parts.append(f'"{node["_name"]}"')
        skip = {
            "_action",
            "_condition",
            "_actionList",
            "_conditionList",
            "_variablesMap",
            "statement",
            "availableChoices",
            "childWeights",
            "_transitionEvaluation",
            "_actorName",
            "_actorParameterID",
        }
        nested = None
        for k, v in node.items():
            if (
                isinstance(v, dict)
                and set(v) <= {"_value", "_name", "_targetVariableID", "_type"}
                and k.startswith("_")
                and k
                in (
                    "_subTree",
                    "_nestedBT",
                    "_nestedFSM",
                    "_nestedDLG",
                    "_subGraph",
                    "_subFSM",
                    "_subDialogue",
                    "_subTree",
                )
            ):
                nested = (k, v)
        if nested:
            skip.add(nested[0])
        if name in ("ActionNode",):
            parts = [self.task(node.get("_action"))] + parts[1:]
        elif name in ("ConditionNode",):
            parts = ["? " + self.task(node.get("_condition"))] + parts[1:]
        else:
            if "_condition" in node:
                parts.append("? " + self.task(node.get("_condition")))
            if "_action" in node:
                parts.append(": " + self.task(node.get("_action")))
            if "_actionList" in node:
                parts.append(": " + self.task(node.get("_actionList")))
            extra = self.fields_inline(node, t, skip=skip)
            if extra:
                parts.append(f"[{extra}]")
        if nested:
            parts.append("→ " + self.nested_target(node, nested[1]))
            vm = self.vars_map(node, nested[1])
            if vm:
                parts.append("{" + vm + "}")
        if node.get("_tag"):
            parts.append(f"tag={node['_tag']}")
        if node.get("_comment"):
            c = node["_comment"].replace("\n", " ")
            parts.append(f"// {c[:80]}{'…' if len(c) > 80 else ''}")
        return " ".join(p for p in parts if p)

    def nested_target(self, node: dict, param: dict) -> str:
        if param.get("_name") or param.get("_targetVariableID"):
            return self.var(param.get("_name"), param.get("_targetVariableID"))
        if "_value" in param:
            return self.objref(param["_value"])
        return "null"

    def vars_map(self, node: dict, param: dict) -> str:
        vm = node.get("_variablesMap") or []
        if not vm:
            return ""
        sub_names = {}
        if "_value" in param and self.sub_vars is not None:
            r = self.g.ref(param["_value"])
            if r is not None and not r.is_null:
                sub_names = self.sub_vars(r) or {}
        out = []
        for m in vm:
            sub = sub_names.get(m.get("_targetSubGraphVariableID"), m.get("_targetSubGraphVariableID", "?")[:8])
            src = (
                self.var(m.get("_name"), m.get("_targetVariableID"))
                if (m.get("_name") or m.get("_targetVariableID"))
                else self.literal(m.get("_value"), m.get("_type"))
            )
            r, w = m.get("_canRead", False), m.get("_canWrite", False)
            arrow = "↔" if r and w else ("→" if r else ("←" if w else "×"))
            out.append(f"{sub}{arrow}{src}")
        return ", ".join(out)


def _coord(v) -> str:
    f = float(v)
    return str(int(f)) if f.is_integer() else f"{f:g}"


def _num(v) -> str:
    return repr(v) if isinstance(v, float) else str(v)


def _elem_type(ftype: Optional[str]) -> Optional[str]:
    if not ftype:
        return None
    m = re.match(r"^(?:List|IList|IEnumerable)<(.+)>$", ftype)
    if m:
        return m.group(1)
    if ftype.endswith("[]"):
        return ftype[:-2]
    return None


def _bb_inner(ftype: Optional[str]) -> Optional[str]:
    if not ftype:
        return None
    m = re.match(r"^BBParameter<(.+)>$", ftype)
    return m.group(1) if m else None


def _is_bbparam(d: dict, ftype: Optional[str]) -> bool:
    if ftype and (ftype.startswith("BBParameter") or ftype.startswith("BBObjectParameter")):
        return True
    keys = {k for k in d if k != "$type"}
    if "$type" in d and "BBParameter" not in d["$type"] and "BBObjectParameter" not in d["$type"]:
        return False
    return (
        bool(keys)
        and keys <= {"_value", "_name", "_targetVariableID", "_type"}
        and ("_name" in keys or "_targetVariableID" in keys or "_value" in keys)
    )


def member_display(base_info: str) -> str:
    parts = base_info.split("|")
    if len(parts) >= 2:
        return f"{simple_name(parts[0])}.{_strip_accessor(parts[1])}"
    return base_info


def _strip_accessor(m: str) -> str:
    return m[4:] if m.startswith(("get_", "set_")) else m


def _member_name(method: dict) -> str:
    parts = (method.get("_baseInfo") or "?").split("|")
    return _strip_accessor(parts[1]) if len(parts) > 1 else parts[0]
