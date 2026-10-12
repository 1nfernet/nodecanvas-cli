"""Lightweight C# declaration scanner.

Extracts what nodecanvas-cli needs from a project's sources without compiling anything:
classes (namespace, nesting, generic arity, base list, attributes), their serialized fields
with initializers, properties and methods, and enums with member values. It is a heuristic
reader of well-formed code, not a parser; anything it cannot classify is skipped.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

_KEYWORDS_BEFORE_PAREN = {
    "if",
    "for",
    "foreach",
    "while",
    "switch",
    "catch",
    "using",
    "lock",
    "return",
    "new",
    "typeof",
    "sizeof",
    "nameof",
    "default",
    "fixed",
    "checked",
    "unchecked",
    "base",
    "this",
    "when",
}
_MODIFIERS = {
    "public",
    "private",
    "protected",
    "internal",
    "static",
    "readonly",
    "const",
    "volatile",
    "new",
    "abstract",
    "virtual",
    "override",
    "sealed",
    "extern",
    "unsafe",
    "partial",
    "async",
    "ref",
}
_TYPE_DECL_RE = re.compile(
    r"\b(class|struct|interface|enum|record)\s+(@?\w+)\s*(<[^{]*?>)?\s*(?::\s*([^{]*?))?\s*(?:where\b[^{]*)?$", re.S
)
_NAMESPACE_RE = re.compile(r"\bnamespace\s+([\w.]+)\s*$")
_ATTR_RE = re.compile(r"\[\s*([\w.]+)\s*(?:\((.*?)\))?\s*\]", re.S)
_STR_ARG_RE = re.compile(r'"((?:[^"\\]|\\.)*)"')


@dataclass
class FieldDecl:
    name: str
    type: str
    init: Optional[str]
    attrs: Dict[str, str]
    modifiers: List[str]

    @property
    def serialized(self) -> bool:
        mods = set(self.modifiers)
        if mods & {"static", "const", "readonly"}:
            return False
        if any(a in self.attrs for a in ("NonSerialized", "System.NonSerialized", "fsIgnore")):
            return False
        if "public" in mods:
            return True
        return any(
            a in self.attrs
            for a in (
                "SerializeField",
                "UnityEngine.SerializeField",
                "fsProperty",
                "fsSerializeAs",
                "SerializeReference",
            )
        )

    @property
    def serialized_name(self) -> str:
        alias = self.attrs.get("fsSerializeAs") or self.attrs.get("fsProperty")
        return _first_str(alias) or self.name


@dataclass
class ClassDecl:
    kind: str  # class | struct | interface | enum | record
    name: str
    namespace: str
    outer: List[str]
    arity: int
    bases: List[str]
    attrs: Dict[str, str]
    file: str
    fields: List[FieldDecl] = field(default_factory=list)
    properties: Dict[str, str] = field(default_factory=dict)  # name -> type
    methods: Dict[str, List[dict]] = field(default_factory=dict)  # name -> [{ret, params, mods}]
    enum_members: List[Tuple[str, object]] = field(default_factory=list)
    is_abstract: bool = False
    usings: List[str] = field(default_factory=list)
    const_props: Dict[str, str] = field(default_factory=dict)  # property -> literal it always returns
    setters: List[str] = field(default_factory=list)  # properties with a public set accessor

    @property
    def full_name(self) -> str:
        """Name the way FullSerializer writes ``$type`` (``Ns.Outer+Inner`1``)."""
        parts = self.outer + [self.name + (f"`{self.arity}" if self.arity else "")]
        nested = "+".join(parts)
        return f"{self.namespace}.{nested}" if self.namespace else nested

    def attr_str(self, name: str) -> Optional[str]:
        return _first_str(self.attrs.get(name))

    def enum_values(self) -> Dict[int, str]:
        out: Dict[int, str] = {}
        known: Dict[str, int] = {}
        cur = -1
        for name, val in self.enum_members:
            if val is None:
                cur += 1
            elif isinstance(val, int):
                cur = val
            else:
                computed = _eval_enum_expr(val, known)
                cur = computed if computed is not None else cur + 1
            known[name] = cur
            out.setdefault(cur, name)
        return out

    @property
    def is_flags(self) -> bool:
        return "Flags" in self.attrs or "System.Flags" in self.attrs

    def enum_name(self, value: int) -> str:
        values = self.enum_values()
        if value in values:
            return values[value]
        if self.is_flags and value > 0:
            parts = [n for v, n in sorted(values.items()) if v > 0 and v & (v - 1) == 0 and value & v]
            covered = 0
            for v, n in values.items():
                if n in parts:
                    covered |= v
            if covered == value:
                return "|".join(parts)
        return str(value)


_ENUM_EXPR_RE = re.compile(r"^[\w\s|&<>()~+\-*x0-9A-Fa-f]+$")


def _eval_enum_expr(expr: str, known: Dict[str, int]) -> Optional[int]:
    e = expr.strip()
    if not _ENUM_EXPR_RE.match(e):
        return None
    e = re.sub(r"\b[A-Za-z_]\w*\.(\w+)\b", r"\1", e)
    for name in sorted(known, key=len, reverse=True):
        e = re.sub(rf"\b{re.escape(name)}\b", str(known[name]), e)
    if re.search(r"[A-Za-z_]", re.sub(r"0[xX][0-9A-Fa-f]+", "", e)):
        return None
    try:
        return int(eval(e, {"__builtins__": {}}, {}))  # noqa: S307 - digits and operators only
    except Exception:
        return None


def _first_str(args: Optional[str]) -> Optional[str]:
    if not args:
        return None
    m = _STR_ARG_RE.search(args)
    return bytes(m.group(1), "utf-8").decode("unicode_escape") if m else None


def strip_comments_and_strings(src: str) -> Tuple[str, List[str]]:
    """Remove comments; replace string/char literals with placeholders ``"\\x00N"``.

    Returns the cleaned text and the literal table, so initializers can be recovered.
    """
    out: List[str] = []
    lits: List[str] = []
    i, n = 0, len(src)
    while i < n:
        c = src[i]
        nxt = src[i + 1] if i + 1 < n else ""
        if c == "/" and nxt == "/":
            j = src.find("\n", i)
            i = n if j < 0 else j
            continue
        if c == "/" and nxt == "*":
            j = src.find("*/", i + 2)
            i = n if j < 0 else j + 2
            out.append(" ")
            continue
        if c == '"' or (c in "@$" and nxt == '"') or (c in "@$" and nxt in "@$" and i + 2 < n and src[i + 2] == '"'):
            start = i
            verbatim = False
            while src[i] in "@$":
                verbatim = verbatim or src[i] == "@"
                i += 1
            i += 1
            while i < n:
                if verbatim:
                    if src[i] == '"':
                        if i + 1 < n and src[i + 1] == '"':
                            i += 2
                            continue
                        break
                else:
                    if src[i] == "\\":
                        i += 2
                        continue
                    if src[i] == '"' or src[i] == "\n":
                        break
                i += 1
            lits.append(src[start : i + 1])
            out.append(f'"\x00{len(lits) - 1}"')
            i += 1
            continue
        if c == "'":
            j = i + 1
            while j < n and src[j] != "'" and src[j] != "\n":
                j += 2 if src[j] == "\\" else 1
            lits.append(src[i : j + 1])
            out.append(f'"\x00{len(lits) - 1}"')
            i = j + 1
            continue
        out.append(c)
        i += 1
    return "".join(out), lits


def _restore(text: str, lits: List[str]) -> str:
    return re.sub(r'"\x00(\d+)"', lambda m: lits[int(m.group(1))], text)


def _split_attrs(header: str) -> Tuple[Dict[str, str], str]:
    attrs: Dict[str, str] = {}
    rest = header.strip()
    while rest.startswith("["):
        depth, j = 0, 0
        for j, ch in enumerate(rest):
            if ch == "[":
                depth += 1
            elif ch == "]":
                depth -= 1
                if depth == 0:
                    break
        block = rest[1:j]
        for part in _split_top(block, ","):
            part = part.strip()
            if ":" in part.split("(")[0]:
                part = part.split(":", 1)[1].strip()
            m = re.match(r"([\w.]+)\s*(?:\((.*)\))?$", part, re.S)
            if m:
                name = m.group(1)
                if name.endswith("Attribute"):
                    name = name[: -len("Attribute")]
                attrs[name] = m.group(2) or ""
        rest = rest[j + 1 :].strip()
    return attrs, rest


def _split_top(s: str, sep: str, brackets: str = "(<[{", closers: str = ")>]}") -> List[str]:
    parts, depth, cur = [], 0, []
    for ch in s:
        if ch in brackets:
            depth += 1
        elif ch in closers:
            depth -= 1
        if ch == sep and depth == 0:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    parts.append("".join(cur))
    return parts


def _arity(generic: Optional[str]) -> int:
    if not generic:
        return 0
    return len(_split_top(generic.strip()[1:-1], ","))


def _bases(text: Optional[str]) -> List[str]:
    if not text:
        return []
    return [b.strip() for b in _split_top(text, ",") if b.strip()]


_FIELD_RE = re.compile(
    r"^(?P<type>[\w.]+(?:\s*<.*>)?(?:\s*\[[\s,]*\])*\??)\s+(?P<name>@?\w+)\s*(?:=\s*(?P<init>.*))?$", re.S
)


def _member(header: str, lits: List[str], cls: ClassDecl, terminator: str) -> None:
    attrs, rest = _split_attrs(header)
    words = rest.split()
    mods = []
    while words and words[0] in _MODIFIERS:
        mods.append(words.pop(0))
    rest = " ".join(words)
    if not rest or rest.startswith(("using ", "event ", "delegate ", "operator ", "implicit ", "explicit ")):
        return
    if "=>" in rest and "(" not in rest.split("=>")[0]:
        m = re.match(r"^([\w.<>\[\],\s?]+?)\s+(@?\w+)\s*=>\s*(.*)$", rest, re.S)
        if m:
            cls.properties[m.group(2)] = m.group(1).strip()
            lit = m.group(3).strip()
            if re.match(r"^(-?\d+|true|false)$", lit):
                cls.const_props[m.group(2)] = lit
        return
    paren = rest.find("(")
    eq = rest.find("=")
    if paren >= 0 and (eq < 0 or paren < eq):
        m = re.search(r"([\w.<>\[\],\s?]+?)\s+(@?\w+)\s*(<[^()]*>)?\s*\($", rest[: paren + 1])
        if m and m.group(2) not in _KEYWORDS_BEFORE_PAREN:
            cls.methods.setdefault(m.group(2), []).append(
                {"ret": re.sub(r"\s+", "", m.group(1)), "params": _param_types(rest[paren + 1 :]), "mods": mods}
            )
        return
    if terminator == "{":
        m = re.match(r"^([\w.<>\[\],\s?]+?)\s+(@?\w+)$", rest)
        if m and m.group(1) not in ("get", "set"):
            cls.properties[m.group(2)] = m.group(1).strip()
        return
    decls = _split_top(rest, ",")
    first = _FIELD_RE.match(decls[0].strip())
    if not first:
        return
    ftype = re.sub(r"\s+", "", first.group("type"))
    for k, d in enumerate(decls):
        d = d.strip()
        if k == 0:
            name, init = first.group("name"), first.group("init")
        else:
            m = re.match(r"^(@?\w+)\s*(?:=\s*(.*))?$", d, re.S)
            if not m:
                continue
            name, init = m.group(1), m.group(2)
        cls.fields.append(
            FieldDecl(
                name.lstrip("@"),
                ftype,
                _restore(init.strip(), lits) if init else None,
                {k2: _restore(v, lits) for k2, v in attrs.items()},
                mods,
            )
        )


def _param_types(after_paren: str) -> List[str]:
    depth, end = 1, len(after_paren)
    for k, ch in enumerate(after_paren):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                end = k
                break
    inner = after_paren[:end].strip()
    if not inner:
        return []
    out = []
    for part in _split_top(inner, ","):
        _, part = _split_attrs(part)
        part = part.split("=")[0].strip()
        words = part.split()
        while words and words[0] in ("ref", "out", "in", "params", "this", "scoped"):
            words.pop(0)
        if len(words) >= 2:
            out.append(re.sub(r"\s+", "", " ".join(words[:-1])))
        elif words:
            out.append(words[0])
    return out


def scan(src: str, filename: str = "") -> List[ClassDecl]:
    text, lits = strip_comments_and_strings(src)
    text = re.sub(r"^\s*#.*$", "", text, flags=re.M)
    usings = re.findall(r"^\s*using\s+([\w.]+)\s*;", text, flags=re.M)
    out: List[ClassDecl] = []
    # scope stack entries: ("ns", name) | ("type", ClassDecl) | ("block", None)
    stack: List[Tuple[str, object]] = []
    seg_start = 0
    i, n = 0, len(text)
    file_ns = None

    def namespace() -> str:
        parts = [s[1] for s in stack if s[0] == "ns"]
        if file_ns:
            parts.insert(0, file_ns)
        return ".".join(parts)

    def outer_types() -> List[str]:
        return [s[1].name for s in stack if s[0] == "type"]

    def current_type() -> Optional[ClassDecl]:
        return stack[-1][1] if stack and stack[-1][0] == "type" else None

    paren_depth = 0
    while i < n:
        c = text[i]
        if c == "(":
            paren_depth += 1
        elif c == ")":
            paren_depth -= 1
        elif c == "{" and paren_depth == 0:
            header = text[seg_start:i].strip()
            cls = current_type()
            attrs, rest = _split_attrs(header)
            m_ns = _NAMESPACE_RE.search(rest)
            m_t = _TYPE_DECL_RE.search(rest) if not m_ns else None
            if m_ns and (not stack or all(s[0] == "ns" for s in stack)):
                stack.append(("ns", m_ns.group(1)))
            elif m_t and (not stack or stack[-1][0] in ("ns", "type")) and "(" not in rest[: m_t.start()]:
                decl = ClassDecl(
                    m_t.group(1),
                    m_t.group(2).lstrip("@"),
                    namespace(),
                    outer_types(),
                    _arity(m_t.group(3)),
                    _bases(m_t.group(4)),
                    {k: _restore(v, lits) for k, v in attrs.items()},
                    filename,
                )
                decl.usings = usings
                decl.is_abstract = bool(re.search(r"\babstract\b", rest[: m_t.start()]))
                out.append(decl)
                stack.append(("type", decl))
                if decl.kind == "enum":
                    j = text.find("}", i)
                    body = text[i + 1 : j if j >= 0 else n]
                    for part in _split_top(body, ",", "([{", ")]}"):
                        _, part = _split_attrs(part)
                        m = re.match(r"^\s*(@?\w+)\s*(?:=\s*(.+))?\s*$", part, re.S)
                        if m:
                            val = None
                            if m.group(2):
                                raw = m.group(2).strip()
                                try:
                                    val = int(raw, 0)
                                except ValueError:
                                    val = raw
                            decl.enum_members.append((m.group(1).lstrip("@"), val))
                    stack.pop()
                    i = j if j >= 0 else n
                    seg_start = i + 1
                    i += 1
                    continue
            else:
                is_init = cls is not None and re.search(r"(?<![=!<>])=(?![=>])", rest) is not None
                if cls is not None and header and not is_init:
                    _member(header, lits, cls, "{")
                stack.append(("block", None))
                depth = 1
                body_start = i + 1
                i += 1
                while i < n and depth:
                    if text[i] == "{":
                        depth += 1
                    elif text[i] == "}":
                        depth -= 1
                    i += 1
                stack.pop()
                if cls is not None and not is_init:
                    pm = re.match(r"^\s*get\s*\{\s*return\s+(-?\d+|true|false)\s*;\s*\}\s*$", text[body_start : i - 1])
                    hm = re.search(r"(@?\w+)\s*$", header)
                    if pm and hm and "(" not in header:
                        cls.const_props[hm.group(1)] = pm.group(1)
                    sm = re.search(r"(?:^|[;}\s])((?:\w+\s+)*)set\s*[;{=]", text[body_start : i - 1])
                    if (
                        sm
                        and hm
                        and "(" not in header
                        and not set(sm.group(1).split()) & {"private", "protected", "internal"}
                    ):
                        cls.setters.append(hm.group(1).lstrip("@"))
                if is_init:
                    j = text.find(";", i)
                    j = n if j < 0 else j
                    _member(text[seg_start:j], lits, cls, ";")
                    i = j + 1
                    seg_start = i
                    continue
                tail = re.match(r"\s*(=>[^;]*;|=[^;]*;|;)?", text[i:])
                i += tail.end() if tail else 0
                seg_start = i
                continue
            seg_start = i + 1
        elif c == "}" and paren_depth == 0:
            if stack:
                stack.pop()
            seg_start = i + 1
        elif c == ";" and paren_depth == 0:
            header = text[seg_start:i].strip()
            m_fns = re.match(r"^namespace\s+([\w.]+)$", header)
            if m_fns and not stack:
                file_ns = m_fns.group(1)
            else:
                cls = current_type()
                if cls is not None and header:
                    _member(header, lits, cls, ";")
            seg_start = i + 1
        i += 1
    return out
