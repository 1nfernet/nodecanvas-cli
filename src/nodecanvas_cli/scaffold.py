"""``nodecanvas-cli scaffold``: C# templates for custom ActionTasks, ConditionTasks and BT decorators/composites.

The style follows the project: the namespace and indentation of neighbouring scripts, and
``nodecanvas-cli.json`` (``{"scaffold": {"comments": false}}`` drops the TODO comments). No ``.meta`` is
written: Unity creates it on import.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import List, Optional, Tuple

KINDS = {
    "action": ("ActionTask", ["NodeCanvas.Framework", "ParadoxNotion.Design", "UnityEngine"]),
    "condition": ("ConditionTask", ["NodeCanvas.Framework", "ParadoxNotion.Design", "UnityEngine"]),
    "decorator": (
        "BTDecorator",
        ["NodeCanvas.BehaviourTrees", "NodeCanvas.Framework", "ParadoxNotion.Design", "UnityEngine"],
    ),
    "composite": (
        "BTComposite",
        ["NodeCanvas.BehaviourTrees", "NodeCanvas.Framework", "ParadoxNotion.Design", "UnityEngine"],
    ),
}
_NS_RE = re.compile(r"^\s*namespace\s+([\w.]+)", re.M)
_FIELD_RE = re.compile(r"^\s*([A-Za-z_]\w*)\s*:\s*([^=\r\n]+?)\s*(?:=\s*(.*?))?\s*$")


class ScaffoldError(Exception):
    pass


def _neighbour_sources(root: Path, d: Path) -> List[Path]:
    cur = d
    while True:
        if cur.is_dir():
            files = sorted(cur.glob("*.cs"))
            if files:
                return files
        if cur == root or root not in cur.parents:
            return []
        cur = cur.parent


def detect_style(root: Path, d: Path) -> Tuple[Optional[str], str]:
    """Namespace and indentation of the nearest scripts at or above ``d`` (inside the project)."""
    ns, indent = None, "    "
    for f in _neighbour_sources(root, d)[:5]:
        try:
            text = f.read_text(encoding="utf-8-sig", errors="replace")
        except OSError:
            continue
        m = _NS_RE.search(text)
        if m and ns is None:
            ns = m.group(1)
            if f.parent != d:  # a parent folder's namespace: extend it with the sub-folders, Unity/Rider style
                rel = d.relative_to(f.parent).parts
                ns = ".".join([ns] + [re.sub(r"\W", "", p) for p in rel if re.sub(r"\W", "", p)])
        lines = [line for line in text.splitlines() if line.startswith(("\t", " ")) and line.strip()]
        if lines:
            first = lines[0]
            indent = "\t" if first.startswith("\t") else " " * (len(first) - len(first.lstrip(" ")))
            break
    return ns, indent or "    "


def parse_field(spec: str) -> Tuple[str, str, Optional[str]]:
    m = _FIELD_RE.match(spec)
    if not m:
        raise ScaffoldError(f'--field {spec!r}: use "name:Type[=default]", e.g. "speed:BBParameter<float>=1"')
    return m.group(1), m.group(2).replace(" ", ""), m.group(3)


def csharp_default(ftype: str, value: Optional[str]) -> Optional[str]:
    if value is None or value == "":
        return None
    inner = re.sub(r"^BBParameter<(.+)>$", r"\1", ftype)
    if inner in ("float", "Single") and re.match(r"^-?\d+(\.\d+)?$", value):
        return value + "f"
    if inner in ("string", "String") and not value.startswith('"'):
        return '"' + value.replace('"', '\\"') + '"'
    if inner in ("bool", "Boolean"):
        return value.lower()
    if re.match(r"^Vector[234]$", inner) and re.match(r"^\[.*\]$", value):
        nums = [n.strip() for n in value[1:-1].split(",")]
        return f"new {inner}(" + ", ".join(n + ("f" if "." in n else "") for n in nums) + ")"
    return value


def render(
    kind: str,
    name: str,
    namespace: Optional[str],
    fields: List[Tuple[str, str, Optional[str]]],
    category: Optional[str],
    title: Optional[str],
    description: Optional[str],
    agent: Optional[str],
    agent_ns: Optional[str],
    comments: bool,
    indent: str,
) -> str:
    base, usings = KINDS[kind]
    usings = list(usings)
    if agent_ns and agent_ns != namespace and agent_ns not in usings:
        usings.append(agent_ns)
    if any(t.startswith(("List<", "IList<", "Dictionary<")) for _, t, _ in fields):
        usings.insert(0, "System.Collections.Generic")
    out: List[str] = [f"using {u};" for u in sorted(set(usings), key=lambda u: (not u.startswith("System"), u))] + [""]
    lvl = 0
    if namespace:
        out += [f"namespace {namespace}", "{"]
        lvl = 1
    I = lambda n: indent * (lvl + n)  # noqa: E731,E741
    if category:
        out.append(f'{I(0)}[Category("{category}")]')
    if title:
        out.append(f'{I(0)}[Name("{title}")]')
    if description:
        out.append(f'{I(0)}[Description("{description}")]')
    generic = f"<{agent}>" if agent and kind in ("action", "condition") else ""
    out += [f"{I(0)}public class {name} : {base}{generic}", f"{I(0)}{{"]
    for fname, ftype, default in fields:
        d = csharp_default(ftype, default)
        out.append(f"{I(1)}public {ftype} {fname}{' = ' + d if d else ''};")
    if fields:
        out.append("")
    todo = lambda text: [f"{I(2)}// TODO: {text}"] if comments else []  # noqa: E731
    shown = title or re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", name)
    if kind in ("action", "condition"):
        params = [f for f, t, _ in fields if t.startswith("BBParameter")]
        if params:
            fmt = shown + " " + " ".join("{" + str(i) + "}" for i in range(len(params)))
            info = f'string.Format("{fmt}", {", ".join(params)})'
        else:
            info = f'"{shown}"'
        out += [f"{I(1)}protected override string info {{", f"{I(2)}get {{ return {info}; }}", f"{I(1)}}}", ""]
    if kind == "action":
        out += (
            [f"{I(1)}protected override void OnExecute() {{"]
            + todo("start the action; call EndAction(false) on failure")
            + [f"{I(2)}EndAction(true);", f"{I(1)}}}", "", f"{I(1)}protected override void OnUpdate() {{"]
            + todo("per-frame work while the action runs")
            + [f"{I(1)}}}"]
        )
    elif kind == "condition":
        out += (
            [f"{I(1)}protected override bool OnCheck() {{"]
            + todo("evaluate the condition")
            + [f"{I(2)}return true;", f"{I(1)}}}"]
        )
    elif kind == "decorator":
        out += (
            [
                f"{I(1)}protected override Status OnExecute(Component agent, IBlackboard blackboard) {{",
                f"{I(2)}if ( decoratedConnection == null ) {{ return Status.Optional; }}",
            ]
            + todo("decide whether and how to run the child")
            + [f"{I(2)}status = decoratedConnection.Execute(agent, blackboard);", f"{I(2)}return status;", f"{I(1)}}}"]
        )
    else:
        out += (
            [f"{I(1)}protected override Status OnExecute(Component agent, IBlackboard blackboard) {{"]
            + todo("choose which children run; this runs them in order like a Sequencer")
            + [
                f"{I(2)}for ( var i = 0; i < outConnections.Count; i++ ) {{",
                f"{I(3)}status = outConnections[i].Execute(agent, blackboard);",
                f"{I(3)}if ( status != Status.Success ) {{ return status; }}",
                f"{I(2)}}}",
                f"{I(2)}return Status.Success;",
                f"{I(1)}}}",
            ]
        )
    out.append(f"{I(0)}}}")
    if namespace:
        out.append("}")
    return "\n".join(out) + "\n"


def cmd_scaffold(ws, a) -> int:
    root = ws.project.root
    if not re.match(r"^[A-Za-z_]\w*$", a.name):
        raise ScaffoldError(f"{a.name!r} is not a valid C# class name")
    d = Path(a.dir)
    d = d if d.is_absolute() else (Path.cwd() / d)
    d = d.resolve()
    if root not in d.parents and d != root:
        raise ScaffoldError(f"{a.dir} is outside the Unity project")
    rel_dir = d.relative_to(root).as_posix()
    if not rel_dir.startswith(("Assets", "Packages")):
        raise ScaffoldError("the target folder must be inside Assets/ or Packages/")
    path = d / f"{a.name}.cs"
    if path.exists() and not a.force:
        raise ScaffoldError(f"{ws.project.rel(path)} already exists (use --force to overwrite)")
    existing = ws.schema.find_simple(a.name)
    if existing is not None and existing.file != ws.project.rel(path) and not a.force:
        raise ScaffoldError(f"a type named {a.name} already exists in {existing.file} (use --force to create anyway)")
    ns, indent = detect_style(root, d)
    namespace = a.namespace if a.namespace is not None else ns
    if ws.config.scaffold.indent:
        indent = ws.config.scaffold.indent
    agent_ns = None
    if a.agent:
        decl = ws.schema.find_simple(a.agent.split(".")[-1])
        if decl is None and "." not in a.agent:
            print(
                f"note: agent type {a.agent} not found in project sources; add its namespace by hand", file=sys.stderr
            )
        agent_ns = decl.namespace if decl is not None else (a.agent.rsplit(".", 1)[0] if "." in a.agent else None)
    fields = [parse_field(f) for f in a.field or []]
    text = render(
        a.kind,
        a.name,
        namespace or None,
        fields,
        a.category,
        a.title,
        a.description,
        a.agent.split(".")[-1] if a.agent else None,
        agent_ns,
        ws.config.scaffold.comments,
        indent,
    )
    if a.dry_run:
        print(text, end="")
        print(f"dry run: would write {ws.project.rel(path)}")
        return 0
    d.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    print(f"created {ws.project.rel(path)}" + (f" (namespace {namespace})" if namespace else ""))
    print(f"use it right away: `nodecanvas-cli types {a.name}`; Unity compiles it and creates the .meta on focus")
    return 0


def _guard(fn):
    def run(ws, a):
        try:
            return fn(ws, a)
        except ScaffoldError as e:
            print(f"error: {e}", file=sys.stderr)
            return 1

    return run


def register(sub) -> None:
    s = sub.add_parser("scaffold", help="create a C# template for a custom task or BT node")
    s.add_argument("kind", choices=sorted(KINDS))
    s.add_argument("name", help="class name")
    s.add_argument("--dir", required=True, help="target folder inside Assets/ or Packages/")
    s.add_argument("--namespace", help="default: the namespace of neighbouring scripts")
    s.add_argument("--field", action="append", help='serialized field "name:Type[=default]", repeatable')
    s.add_argument("--category", help='[Category] attribute, e.g. "AI/Combat"')
    s.add_argument("--title", help="[Name] attribute (display name)")
    s.add_argument("--description", help="[Description] attribute")
    s.add_argument("--agent", help="agent type for ActionTask<T> / ConditionTask<T>")
    s.add_argument("--dry-run", action="store_true", help="print the file instead of writing it")
    s.add_argument("--force", action="store_true", help="overwrite an existing file / allow a duplicate type name")
    s.set_defaults(func=_guard(cmd_scaffold))
