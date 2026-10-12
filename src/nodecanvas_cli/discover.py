"""Discovery commands: which task/node types exist, their fields, and members callable by reflection tasks."""

from __future__ import annotations

import json
import re

ROLE_GROUPS = {
    "action": {"action"},
    "condition": {"condition"},
    "bt": {"bt-node", "composite", "decorator"},
    "composite": {"composite"},
    "decorator": {"decorator"},
    "fsm": {"fsm-state", "fsm-node"},
    "dt": {"dt-node"},
}


def cmd_types(ws, a) -> int:
    roles = set()
    for r in a.role or ["action", "condition", "bt", "fsm", "dt"]:
        roles |= ROLE_GROUPS[r]
    decls = ws.schema.all_of_role(roles)
    q = (a.query or "").lower()
    rows = []
    for d in decls:
        disp = d.attr_str("Name") or ""
        cat = d.attr_str("Category") or ""
        if q and q not in d.name.lower() and q not in disp.lower() and q not in cat.lower():
            continue
        rows.append((ws.schema.role(d.full_name), cat, d))
    rows.sort(key=lambda r: (r[0] or "", r[1], r[2].name))
    exact = [
        r
        for r in rows
        if q
        and (r[2].name.lower() == q or (r[2].attr_str("Name") or "").replace(" ", "").lower() == q.replace(" ", ""))
    ]
    if exact and not a.json:
        rows = exact
    if a.json:
        print(
            json.dumps(
                [
                    {
                        "role": r,
                        "category": c,
                        "type": d.full_name,
                        "name": d.attr_str("Name"),
                        "description": d.attr_str("Description"),
                    }
                    for r, c, d in rows
                ],
                ensure_ascii=False,
                indent=1,
            )
        )
        return 0
    if not rows and a.query:
        decl = ws.schema.find(a.query) or ws.schema.find_simple(a.query.split(".")[-1])
        if decl is not None:
            bases = f" : {', '.join(decl.bases)}" if decl.bases else ""
            print(
                f"{decl.full_name}: {decl.kind}{bases} in {decl.file} — not a task or node type "
                f"(use `nodecanvas-cli members {decl.name}` for its members)"
            )
            return 0
    if len(rows) == 1 or a.fields:
        for r, c, d in rows:
            _print_type(ws, d, r)
        return 0
    for r, c, d in rows:
        generic = f"<{','.join('T' * d.arity)}>" if d.arity else ""
        desc = (d.attr_str("Description") or "").split("\n")[0]
        print(f"{r:<10} {d.name}{generic:<4} {('[' + c + ']') if c else '':<24} {desc[:90]}")
    print(f"{len(rows)} types (use `nodecanvas-cli types <Name>` for fields)")
    return 0


def _print_type(ws, d, role) -> None:
    print(f"{d.full_name}  role={role}" + (f'  name="{d.attr_str("Name")}"' if d.attr_str("Name") else ""))
    if d.attr_str("Description"):
        print("  " + d.attr_str("Description").replace("\n", " ")[:300])
    fields = ws.schema.ordered_fields(d.full_name)
    skip = {
        "_UID",
        "_name",
        "_tag",
        "_position",
        "_comment",
        "_isBreakpoint",
        "_collapsed",
        "_color",
        "_verboseLevel",
        "_isDisabled",
        "overrideAgent",
        "_infoCollapsed",
    }
    for name, f, owner in fields:
        if name in skip:
            continue
        extra = ""
        enum = ws.schema.enum_for(re.sub(r"^BBParameter<(.+)>$", r"\1", f.type), owner)
        if enum is not None:
            extra = "  {" + ", ".join(enum.enum_values().values()) + "}"
        init = f" = {f.init}" if f.init else ""
        print(f"  {name}: {f.type}{init}{extra}")
    print()


def cmd_members(ws, a) -> int:
    decl = ws.schema.find(a.type) or ws.schema.find_simple(a.type)
    if decl is None:
        print(f"error: type {a.type!r} not found in project sources")
        return 1
    q = (a.query or "").lower()
    print(f"{decl.full_name}" + (f" : {', '.join(decl.bases)}" if decl.bases else ""))
    seen = set()
    for c in ws.schema.chain(decl):
        for name, typ in c.properties.items():
            if (q and q not in name.lower()) or name in seen:
                continue
            seen.add(name)
            print(f"  prop   {typ} {name}" + (f"   [{c.name}]" if c is not decl else ""))
        for name, sigs in c.methods.items():
            if q and q not in name.lower():
                continue
            for s in sigs:
                if not a.all and "public" not in s.get("mods", []):
                    continue
                key = (name, tuple(s["params"]))
                if key in seen:
                    continue
                seen.add(key)
                static = "static " if "static" in s.get("mods", []) else ""
                print(
                    f"  method {static}{s['ret']} {name}({', '.join(s['params'])})"
                    + (f"   [{c.name}]" if c is not decl else "")
                )
    return 0


def register(sub) -> None:
    s = sub.add_parser("types", help="list task/node types available in the project, or show one type's fields")
    s.add_argument("query", nargs="?", help="filter by name, display name or category; an exact match prints fields")
    s.add_argument("--role", action="append", choices=sorted(ROLE_GROUPS))
    s.add_argument("--fields", action="store_true", help="print fields for every match")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_types)

    s = sub.add_parser("members", help="list properties and methods of a C# type (targets for call/check/get)")
    s.add_argument("type")
    s.add_argument("query", nargs="?")
    s.add_argument("--all", action="store_true", help="include non-public methods")
    s.set_defaults(func=cmd_members)
