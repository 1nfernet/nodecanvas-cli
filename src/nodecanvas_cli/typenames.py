"""C# type text -> CLR names as FullSerializer writes them.

``float`` -> ``System.Single``; ``List<Pawn>`` -> ``System.Collections.Generic.List`1[[Ns.Pawn, Asm, Version=…]]``.
Assembly-qualified forms are harvested from existing graphs first, then derived from ``.asmdef`` files.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Dict, Optional

from .csharp import ClassDecl
from .model import walk

ALIASES = {
    "bool": "System.Boolean",
    "float": "System.Single",
    "double": "System.Double",
    "int": "System.Int32",
    "long": "System.Int64",
    "short": "System.Int16",
    "byte": "System.Byte",
    "sbyte": "System.SByte",
    "uint": "System.UInt32",
    "ulong": "System.UInt64",
    "ushort": "System.UInt16",
    "char": "System.Char",
    "string": "System.String",
    "object": "System.Object",
    "void": "System.Void",
    "decimal": "System.Decimal",
}
SYSTEM_SIMPLE = {v.split(".")[-1]: v for v in ALIASES.values()}
UNITY_CORE = {
    "Vector2",
    "Vector3",
    "Vector4",
    "Vector2Int",
    "Vector3Int",
    "Quaternion",
    "Color",
    "Color32",
    "Rect",
    "Bounds",
    "LayerMask",
    "GameObject",
    "Transform",
    "Component",
    "Behaviour",
    "MonoBehaviour",
    "ScriptableObject",
    "Object",
    "Sprite",
    "Texture",
    "Texture2D",
    "Material",
    "Mesh",
    "AnimationCurve",
    "Gradient",
    "Camera",
    "Light",
    "Renderer",
    "Collider",
    "Rigidbody",
    "AudioClip",
    "AudioSource",
    "Animator",
    "AnimationClip",
    "TextAsset",
    "Space",
    "KeyCode",
    "Ray",
    "RaycastHit",
}
GENERIC_SYSTEM = {
    "List": "System.Collections.Generic.List",
    "Dictionary": "System.Collections.Generic.Dictionary",
    "HashSet": "System.Collections.Generic.HashSet",
    "IList": "System.Collections.Generic.IList",
    "IEnumerable": "System.Collections.Generic.IEnumerable",
}
MSCORLIB = "mscorlib, Version=4.0.0.0, Culture=neutral, PublicKeyToken=b77a5c561934e089"
_QUALIFIED_RE = re.compile(
    r"\[([\w.+`]+(?:\[\[.*?\]\])?), ([\w.\-]+), Version=[\d.]+, Culture=\w+, PublicKeyToken=\w+\]"
)


class TypeNames:
    def __init__(self, ws):
        self.ws = ws
        self._qualified: Optional[Dict[str, str]] = None
        self._asm: Dict[str, Optional[str]] = {}

    @property
    def qualified(self) -> Dict[str, str]:
        if self._qualified is None:
            q: Dict[str, str] = {}
            for g in self.ws.all_graphs():
                for _, d in walk(g.data):
                    for k in ("$type", "_type", "_missingType"):
                        v = d.get(k)
                        if isinstance(v, str) and "[[" in v:
                            for m in _QUALIFIED_RE.finditer(v):
                                full = m.group(0)[1:-1]
                                q.setdefault(m.group(1), full)
            self._qualified = q
        return self._qualified

    def _assembly_of(self, decl: ClassDecl) -> str:
        rel = decl.file
        if rel in self._asm:
            return self._asm[rel] or "Assembly-CSharp"
        root = self.ws.project.root
        p = (root / rel).parent
        name = None
        while True:
            defs = sorted(p.glob("*.asmdef"))
            if defs:
                try:
                    name = json.loads(defs[0].read_text(encoding="utf-8-sig")).get("name")
                except (OSError, ValueError):
                    name = None
                break
            if p == root or p.parent == p or p.name in ("Assets", "Packages"):
                break
            p = p.parent
        if name is None:
            parts = Path(rel).parts
            if "Editor" in parts:
                name = "Assembly-CSharp-Editor-firstpass" if "Plugins" in parts else "Assembly-CSharp-Editor"
            elif "Plugins" in parts or "Standard Assets" in parts:
                name = "Assembly-CSharp-firstpass"
        self._asm[rel] = name
        return name or "Assembly-CSharp"

    def full(self, text: str, ctx: Optional[ClassDecl] = None) -> str:
        """CLR full name (generic arguments assembly-qualified, as FullSerializer writes ``$type``)."""
        t = re.sub(r"\s+", "", text)
        if t in ALIASES:
            return ALIASES[t]
        if t.endswith("[]"):
            return self.full(t[:-2], ctx) + "[]"
        if t.endswith("?"):
            return f"System.Nullable`1[[{self.qualify(t[:-1], ctx)}]]"
        m = re.match(r"^([\w.]+)<(.+)>$", t)
        if m:
            base, args = m.group(1), _split_args(m.group(2))
            base_full = GENERIC_SYSTEM.get(base)
            if base_full is None:
                decl = self.ws.schema.find_simple(base.split(".")[-1], ctx, len(args))
                base_full = decl.full_name.split("`")[0] if decl else base
            return f"{base_full}`{len(args)}[" + ",".join(f"[{self.qualify(a, ctx)}]" for a in args) + "]"
        if "." in t and self.ws.schema.find(t) is not None:
            return self.ws.schema.find(t).full_name
        simple = t.split(".")[-1]
        if simple in SYSTEM_SIMPLE and (t == simple or t.startswith("System.")):
            return SYSTEM_SIMPLE[simple]
        decl = self.ws.schema.find_simple(simple, ctx)
        if decl is not None:
            return decl.full_name
        if simple in UNITY_CORE:
            return f"UnityEngine.{simple}"
        return t

    def qualify(self, text: str, ctx: Optional[ClassDecl] = None) -> str:
        full = self.full(text, ctx)
        if full in self.qualified:
            return self.qualified[full]
        if full.startswith("System."):
            return f"{full}, {MSCORLIB}"
        if full.startswith("UnityEngine."):
            return f"{full}, UnityEngine.CoreModule, Version=0.0.0.0, Culture=neutral, PublicKeyToken=null"
        decl = self.ws.schema.find(full)
        asm = self._assembly_of(decl) if decl is not None else "Assembly-CSharp"
        return f"{full}, {asm}, Version=0.0.0.0, Culture=neutral, PublicKeyToken=null"


def _split_args(s: str):
    out, depth, cur = [], 0, ""
    for ch in s:
        if ch == "<":
            depth += 1
        elif ch == ">":
            depth -= 1
        if ch == "," and depth == 0:
            out.append(cur)
            cur = ""
        else:
            cur += ch
    out.append(cur)
    return [x.strip() for x in out if x.strip()]
