"""Project-wide refactorings that keep graphs in sync with C# changes (``nodecanvas-cli refactor ...``)."""

from __future__ import annotations

import copy
import difflib
import re
import sys
from typing import Callable, Dict, List, Optional, Tuple

from . import fsjson
from .lint import ERROR, lint_graph
from .model import Graph, walk
from .schema import simple_name
from .views import render_graph


class RefactorError(Exception):
    pass


def _rewrite_strings(obj, fn: Callable[[str], str]) -> int:
    """Apply ``fn`` to every string value in ``obj`` (recursively, in place); return how many changed."""
    changed = 0
    if isinstance(obj, dict):
        for k, v in list(obj.items()):
            if isinstance(v, str):
                nv = fn(v)
                if nv != v:
                    obj[k] = nv
                    changed += 1
            else:
                changed += _rewrite_strings(v, fn)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            if isinstance(v, str):
                nv = fn(v)
                if nv != v:
                    obj[i] = nv
                    changed += 1
            else:
                changed += _rewrite_strings(v, fn)
    return changed


def _member_refs(obj, fn: Callable[[dict], bool]) -> int:
    """Call ``fn`` on every member reference (a dict with ``_baseInfo``) in ``obj``, including the ones inside a
    Missing* task's ``_recoveryState`` JSON string; ``fn`` edits the dict in place and says whether it did."""
    changed = 0
    if isinstance(obj, dict):
        bi = obj.get("_baseInfo")
        if isinstance(bi, str) and "|" in bi and fn(obj):
            changed += 1
        for k, v in obj.items():
            if k == "_recoveryState" and isinstance(v, str) and "_baseInfo" in v:
                try:
                    inner = fsjson.loads(v)
                except ValueError:
                    continue  # leave a damaged recovery state as it is
                n = _member_refs(inner, fn)
                if n:
                    obj[k] = fsjson.dumps(inner)
                    changed += n
            else:
                changed += _member_refs(v, fn)
    elif isinstance(obj, list):
        for v in obj:
            changed += _member_refs(v, fn)
    return changed


# ---------------------------------------------------------------------- rename-member


def _sig(params: Tuple[str, ...]) -> str:
    return "(" + ", ".join(params) + ")"


def rename_member(
    ws,
    graphs: List[Graph],
    target: str,
    new: str,
    params: Optional[List[str]] = None,
    all_overloads: bool = False,
    notes: Optional[List[str]] = None,
) -> Dict[str, int]:
    """Rename references to ``Type.Member``: fields (``Type|field``), properties (``Type|get_X|R``) and methods
    (``Type|M|R`` + ``_paramsInfo``). Only one overload of a method is renamed when ``params`` picks it, when the
    C# sources tell which overload got the new name, or when the graphs only call one; otherwise it is an error
    unless ``all_overloads``."""
    if "." not in target:
        raise RefactorError("use Type.Member for the old member")
    tname, old = target.rsplit(".", 1)

    def parse(d: dict):
        """(type, accessor prefix, params or None for a field/property) when ``d`` refers to ``old``."""
        parts = d["_baseInfo"].split("|")
        member = parts[1]
        acc = member[:4] if member[:4] in ("get_", "set_") and member[4:] == old else ""
        if member != old and not acc:
            return None
        if acc or len(parts) == 2:
            return parts[0], acc, None
        return (parts[0], acc, tuple(simple_name(p) for p in (d.get("_paramsInfo") or "").split("|") if p))

    refs = []
    for g in graphs:
        _member_refs(g.data, lambda d: bool(refs.append(parse(d))))
    refs = [r for r in refs if r is not None]
    fulls = {t for t, _, _ in refs if t == tname or t.endswith("." + tname) or simple_name(t) == tname}
    if tname in fulls:  # a full name given: don't also touch My.Demo.Game.Bot for Demo.Game.Bot
        fulls = {tname}
    if not fulls:
        return {}
    called = sorted({sig for t, _, sig in refs if t in fulls and sig is not None})
    label = f"{simple_name(tname)}.{old}"
    rename = set(called)
    if params is not None:
        want = tuple(simple_name(p) for p in params)
        if want not in called:
            raise RefactorError(
                f"no graph calls {label}{_sig(want)}; called overloads: "
                + (", ".join(_sig(s) for s in called) or "none")
            )
        rename = {want}
    elif called and not all_overloads:
        # once the C# rename is done, the sources tell which overloads moved: the new name has them, the old one not
        def sigs_of(member):
            return {
                tuple(simple_name(p) for p in o["params"])
                for t in fulls
                for o in (ws.schema.overloads(t, member) if ws is not None else [])
            }

        old_sigs, new_sigs = sigs_of(old), sigs_of(new)
        decided, undecided = set(), []
        for sig in called:
            if new_sigs and sig in new_sigs and sig not in old_sigs:
                decided.add(sig)
            elif new_sigs and sig in old_sigs and sig not in new_sigs:
                if notes is not None:
                    notes.append(f"kept calls of {label}{_sig(sig)}: that overload is still named {old} in the sources")
            else:
                undecided.append(sig)
        if undecided and len(called) > 1:
            example = ",".join(undecided[0]) or "''"
            raise RefactorError(
                f"graphs call {len(called)} overloads of {label}: "
                + ", ".join(_sig(s) for s in called)
                + f"; pick one with --params (e.g. --params {example})"
                + " or rename them all with --all-overloads"
            )
        rename = decided | set(undecided)

    def fn(d: dict) -> bool:
        r = parse(d)
        if r is None or r[0] not in fulls:
            return False
        _, acc, sig = r
        if (sig is None and params is not None) or (sig is not None and sig not in rename):
            return False
        parts = d["_baseInfo"].split("|")
        parts[1] = acc + new
        d["_baseInfo"] = "|".join(parts)
        return True

    counts: Dict[str, int] = {}
    for g in graphs:
        n = _member_refs(g.data, fn)
        if n:
            counts[g.loaded.slot.address] = n
    return counts


# ---------------------------------------------------------------------- rename-type


def rename_type(ws, graphs: List[Graph], old: str, new: str, assembly: Optional[str]) -> Dict[str, int]:
    pat = re.compile(r"(?<![\w.+`])" + re.escape(old) + r"(?![\w`.])")
    asm = assembly
    if asm is None:
        decl = ws.schema.find(new)
        if decl is not None:
            from .typenames import TypeNames

            asm = TypeNames(ws)._assembly_of(decl)
    asm_pat = re.compile(r"(?<![\w.+`])" + re.escape(new) + r"((?:\+[\w`]+)*), ([\w.\-]+), Version=")

    def fn(s: str) -> str:
        out = pat.sub(new, s)
        if out != s and asm:
            out = asm_pat.sub(lambda m: f"{new}{m.group(1)}, {asm}, Version=", out)
        return out

    counts: Dict[str, int] = {}
    for g in graphs:
        n = _rewrite_strings(g.data, fn)
        if n:
            counts[g.loaded.slot.address] = n
    return counts


# ---------------------------------------------------------------------- retarget


def retarget(
    ws,
    graphs: List[Graph],
    target: str,
    to: List[str],
    frm: Optional[List[str]],
    force_all: bool = False,
    skipped: Optional[List[str]] = None,
) -> Dict[str, int]:
    from .bindings import _retarget_fix, assignable, var_type, ws_full
    from .ops import OpError, apply_ops

    tname, member = target.rsplit(".", 1)
    decl = ws.schema.find(tname) or ws.schema.find_simple(tname.split(".")[-1])
    sigs = [o["params"] for o in ws.schema.overloads(decl.full_name, member, len(to))] if decl else []
    new_sig = next((s for s in sigs if [simple_name(p) for p in s] == to), None)
    if new_sig is None:
        raise RefactorError(f"{tname}.{member} has no overload ({', '.join(to)}) in the sources")
    new_full = [ws_full(ws, p, decl) for p in new_sig]
    counts: Dict[str, int] = {}
    for g in graphs:
        fixes = []
        for owner in list(g.nodes) + list(g.connections):
            for path, d in walk(owner):
                m = d.get("method")
                if not (isinstance(m, dict) and isinstance(d.get("parameters"), list)):
                    continue
                parts = (m.get("_baseInfo") or "").split("|")
                if len(parts) < 2 or parts[1] != member or simple_name(parts[0]) != simple_name(tname):
                    continue
                sig = [simple_name(p) for p in (m.get("_paramsInfo") or "").split("|") if p]
                if len(sig) != len(to) or sig == to or (frm is not None and sig != frm):
                    continue
                if not force_all:
                    bad = None
                    for k, p in enumerate(d["parameters"]):
                        var = (
                            g.var_by_id(p.get("_targetVariableID"))
                            if isinstance(p, dict) and p.get("_targetVariableID")
                            else None
                        )
                        if var is not None and not assignable(ws.schema, var_type(var), new_full[k]):
                            got = simple_name(var_type(var) or "?")
                            bad = f"argument {k + 1} is ${var['_name']} ({got}), not {to[k]}"
                            break
                    if bad:
                        if skipped is not None:
                            skipped.append(f"{g.name}{g.owner_label(owner)}: {bad}")
                        continue
                fix = _retarget_fix(ws, g, owner, path, d, to)
                if fix is None:
                    raise RefactorError(f"{g.name} {g.owner_label(owner)}: cannot rebuild this call")
                fixes.append(fix)
        if fixes:
            try:
                apply_ops(ws, g, fixes)
            except OpError as e:
                raise RefactorError(f"{g.name}: {e}") from e
            counts[g.loaded.slot.address] = len(fixes)
    return counts


# ---------------------------------------------------------------------- command


def _view(ws, g: Graph) -> List[str]:
    lines = render_graph(g, ws.renderer(g), with_header=True)[1:]
    return [re.sub(r"#\d+", "#", line) for line in lines]


def cmd_refactor(ws, a) -> int:
    graphs = [ws.graph(a.graph, None)] if getattr(a, "graph", None) else ws.all_graphs()

    def watched(i):
        return i.severity == ERROR or i.code in ("arg-type-mismatch", "missing-member", "null-agent")

    before = {
        g.loaded.slot.address: (_view(ws, g), {(i.code, i.message) for i in lint_graph(ws, g) if watched(i)})
        for g in graphs
    }
    snapshots = {g.loaded.slot.address: (copy.deepcopy(g.data), list(g.refs)) for g in graphs}
    try:
        if a.action == "rename-member":
            notes: List[str] = []
            counts = rename_member(
                ws,
                graphs,
                a.target,
                a.new,
                [t.strip() for t in a.params.split(",") if t.strip()] if a.params is not None else None,
                a.all_overloads,
                notes,
            )
            for line in notes:
                print("note: " + line)
            if not ws.schema.member_kinds(a.target.rsplit(".", 1)[0], a.new):
                print(f"note: {a.target.rsplit('.', 1)[0]}.{a.new} does not exist in the C# sources yet")
        elif a.action == "rename-type":
            counts = rename_type(ws, graphs, a.old, a.new, a.assembly)
        else:
            skipped: List[str] = []
            counts = retarget(
                ws,
                graphs,
                a.target,
                [t.strip() for t in a.to.split(",") if t.strip()],
                [t.strip() for t in a.frm.split(",")] if a.frm is not None else None,
                a.all,
                skipped,
            )
            if skipped:
                print(f"skipped {len(skipped)} call(s) whose arguments do not fit (use --all to retarget them anyway):")
                shown = skipped if a.verbose else skipped[:20]
                for line in shown:
                    print("  " + line)
                if len(skipped) > len(shown):
                    print(f"  … {len(skipped) - len(shown)} more (use --verbose for the full list)")
    except RefactorError as e:
        for g in graphs:
            data, refs = snapshots[g.loaded.slot.address]
            g.data.clear()
            g.data.update(data)
            g.refs[:] = refs
            g.reindex()
        print(f"error: {e}", file=sys.stderr)
        return 1
    if not counts:
        print("nothing to change")
        return 0
    changed = [g for g in graphs if g.loaded.slot.address in counts]
    new_errors = 0
    for g in changed:
        addr = g.loaded.slot.address
        g.reindex()
        print(f"== {addr}: {counts[addr]} change(s)")
        old_view, old_errors = before[addr]
        diff = list(difflib.unified_diff(old_view, _view(ws, g), "before", "after", n=0, lineterm=""))
        print("\n".join(diff[2:]) if diff else "(no visible change in the compact view)")
        for i in lint_graph(ws, g):
            if watched(i) and (i.code, i.message) not in old_errors:
                print("lint: " + i.line())
                # renaming graphs before the C# member is renamed is a normal order of work
                expected = (
                    a.action == "rename-member"
                    and i.code == "missing-member"
                    and any(f".{p}{a.new} " in i.message for p in ("", "get_", "set_"))
                )
                new_errors += i.severity == ERROR and not expected
    total = sum(counts.values())
    if new_errors and not a.force:
        print(f"not saved: {new_errors} new lint error(s) (use --force)", file=sys.stderr)
        return 1
    if a.dry_run:
        print(f"dry run: {total} change(s) in {len(changed)} graph(s), nothing written")
        return 0
    for g in changed:
        ws.save(g)
    print(f"saved {len(changed)} graph(s), {total} change(s)")
    return 0


def register(sub) -> None:
    s = sub.add_parser("refactor", help="project-wide renames and overload changes in graphs")
    rs = s.add_subparsers(dest="action", required=True)

    r = rs.add_parser("rename-member", help="rename a method/property/field used by reflection tasks in every graph")
    r.add_argument("target", help="Type.OldMember")
    r.add_argument("new", help="NewMember")
    r.add_argument("--params", help="only calls bound to this overload, e.g. Vector3 or Unit,Vector3 ('' for none)")
    r.add_argument("--all-overloads", action="store_true", help="rename calls of every overload")
    r.add_argument("--graph", help="limit to one graph")

    r = rs.add_parser("rename-type", help="rename a C# type (namespace, name or nesting) in every graph")
    r.add_argument("old", help="Old.Full.Name (use + for nested types)")
    r.add_argument("new", help="New.Full.Name")
    r.add_argument(
        "--assembly", help="assembly name for assembly-qualified references (default: from the new type's asmdef)"
    )
    r.add_argument("--graph", help="limit to one graph")

    r = rs.add_parser("retarget", help="switch calls of a method to another overload")
    r.add_argument("target", help="Type.Method")
    r.add_argument("--to", required=True, help="parameter types of the new overload, e.g. Booster or Pawn,Vector3")
    r.add_argument("--from", dest="frm", help="only calls bound to this overload")
    r.add_argument("--all", action="store_true", help="also retarget calls whose argument variables do not fit")
    r.add_argument("--verbose", action="store_true", help="list every skipped call")
    r.add_argument("--graph", help="limit to one graph")

    for p in rs.choices.values():
        p.add_argument("--dry-run", action="store_true")
        p.add_argument("--force", action="store_true", help="save even if new lint errors appear")
    s.set_defaults(func=cmd_refactor)
