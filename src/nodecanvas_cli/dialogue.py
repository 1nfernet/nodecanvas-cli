"""Dialogue Tree texts: ``dt export`` / ``dt import`` (CSV or JSON) and ``dt lint``.

A row is one statement: a StatementNode's text, or one choice of a MultipleChoiceNode. Rows are keyed
by the node ``_UID`` (plus the choice number), so the file survives structural edits; nodes without a
UID fall back to ``#id`` keys (with a warning) unless ``--assign-uids`` writes UIDs first. ``import``
only touches texts, meta and translations — never structure — and detects rows whose statement changed
in the asset since the export (``rev`` column).
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import sys
import uuid
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from . import fsjson
from .model import Graph
from .schema import simple_name

FIXED = ["graph", "key", "node", "actor", "kind", "index", "text", "meta"]
TAIL = ["original", "rev"]
SHORT_LOCALES = {
    "zh": "Chinese",
    "en": "English",
    "fr": "French",
    "de": "German",
    "el": "Greek",
    "it": "Italian",
    "ja": "Japanese",
    "ru": "Russian",
    "es": "Spanish",
}
STATEMENT_ORDER = ("_text", "_audio", "_meta", "_localizations")
INSTIGATOR = "SELF"


class DialogueError(Exception):
    pass


# ---------------------------------------------------------------------------- locales


def locale_enum(ws) -> Dict[str, int]:
    """``name -> value`` of the project's ``Locales`` enum (NodeCanvas lets projects edit it)."""
    decl = ws.schema.find("NodeCanvas.DialogueTrees.Locales") or ws.schema.find_simple("Locales")
    if decl is None or decl.kind != "enum":
        raise DialogueError("the Locales enum (NodeCanvas.DialogueTrees.Locales) is not found")
    return {name: val for val, name in decl.enum_values().items()}


def resolve_locale(ws, token: str) -> Tuple[str, int]:
    enum = locale_enum(ws)
    name = SHORT_LOCALES.get(token.lower(), token)
    for n, v in enum.items():
        if n.lower() == name.lower():
            if v == 0:
                raise DialogueError("Default is the main text column, not a translation")
            return n, v
    raise DialogueError(f"unknown locale {token!r}; known: " + ", ".join(n for n in enum if n != "Default"))


# ---------------------------------------------------------------------------- statements


def statements(g: Graph):
    """Yield ``(node, kind, index, statement dict)`` in node order; index is 1-based for choices."""
    for n in g.nodes:
        t = simple_name(n.get("$type", ""))
        if t == "StatementNode" and isinstance(n.get("statement"), dict):
            yield n, "say", "", n["statement"]
        elif t == "MultipleChoiceNode":
            for i, ch in enumerate(n.get("availableChoices") or []):
                if isinstance(ch, dict) and isinstance(ch.get("statement"), dict):
                    yield n, "choice", str(i + 1), ch["statement"]


def translations(st: dict) -> Dict[int, str]:
    loc = st.get("_localizations")
    out: Dict[int, str] = {}
    if isinstance(loc, list):
        for e in loc:
            if isinstance(e, dict) and isinstance(e.get("Key"), int):
                out[e["Key"]] = (e.get("Value") or {}).get("text") or ""
    elif isinstance(loc, dict):  # string-keyed form, if enums were ever serialized as names
        for k, v in loc.items():
            if isinstance(v, dict):
                out[k] = v.get("text") or ""
    return out


def rev(st: dict) -> str:
    """Fingerprint of everything import may change in a statement."""
    payload = json.dumps(
        [st.get("_text", ""), st.get("_meta", ""), sorted((str(k), v) for k, v in translations(st).items())],
        ensure_ascii=False,
    )
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:12]


def set_statement(st: dict, text: str, meta: str, locs: Dict[int, Optional[str]]) -> bool:
    """Write text/meta/translations, keeping FullSerializer's field order and omitting defaults."""
    before = fsjson.dumps(st)
    st["_text"] = text
    if meta:
        st["_meta"] = meta
    else:
        st.pop("_meta", None)
    if locs:
        current = st.get("_localizations")
        if isinstance(current, dict):  # keep the representation the asset already uses
            for k, v in locs.items():
                if v:
                    current.setdefault(k, {})["text"] = v
                else:
                    current.pop(k, None)
        else:
            entries = {e["Key"]: e for e in (current or []) if isinstance(e, dict) and isinstance(e.get("Key"), int)}
            for k, v in locs.items():
                if v:
                    entries.setdefault(k, {"Key": k, "Value": {}})
                    value = entries[k].setdefault("Value", {})
                    value["text"] = v
                else:
                    entries.pop(k, None)
            current = [entries[k] for k in sorted(entries)]
        if current:
            st["_localizations"] = current
        else:
            st.pop("_localizations", None)
    ordered = {k: st[k] for k in STATEMENT_ORDER if k in st}
    ordered.update({k: v for k, v in st.items() if k not in ordered})
    st.clear()
    st.update(ordered)
    return fsjson.dumps(st) != before


def row_key(g: Graph, node: dict, index: str) -> str:
    uid = node.get("_UID")
    base = uid if uid else f"#{g.nid(node)}"
    return f"{base}:{index}" if index else base


# ---------------------------------------------------------------------------- export


def dt_graphs(ws, names: List[str]) -> List[Graph]:
    graphs = [ws.graph(q, "dt") for q in names] if names else [g for g in ws.all_graphs() if g.kind == "dt"]
    if not graphs:
        raise DialogueError("no Dialogue Trees in the project")
    return graphs


def assign_uids(ws, graphs: List[Graph]) -> int:
    from .build import Builder

    count = 0
    for g in graphs:
        fresh = []
        for n, _, _, _ in statements(g):
            if not n.get("_UID") and all(n is not f for f in fresh):
                n["_UID"] = str(uuid.uuid4())
                fresh.append(n)
        if fresh:
            Builder(ws, g).normalize(fresh)  # FullSerializer field order (_UID among the Node base fields)
            ws.save(g)
            count += len(fresh)
    return count


def cmd_export(ws, a) -> int:
    graphs = dt_graphs(ws, a.graphs)
    if a.assign_uids:
        n = assign_uids(ws, graphs)
        if n:
            print(f"assigned {n} UIDs (graphs saved)", file=sys.stderr)
            graphs = dt_graphs(ws, [g.loaded.slot.address for g in graphs])
    locales = [resolve_locale(ws, t.strip()) for t in (a.locales or "").split(",") if t.strip()]
    cols = [t.strip() for t in (a.locales or "").split(",") if t.strip()]
    rows, no_uid = [], set()
    for g in graphs:
        for node, kind, index, st in statements(g):
            if not node.get("_UID"):
                no_uid.add(g.name)
            tr = translations(st)
            row = {
                "graph": g.loaded.slot.address,
                "key": row_key(g, node, index),
                "node": f"#{g.nid(node)}",
                "actor": node.get("_actorName") or INSTIGATOR,
                "kind": kind,
                "index": index,
                "text": st.get("_text", ""),
                "meta": st.get("_meta", ""),
            }
            for col, (_, val) in zip(cols, locales):
                row[col] = tr.get(val, "")
            row["original"] = st.get("_text", "")
            row["rev"] = rev(st)
            rows.append(row)
    for name in sorted(no_uid):
        print(
            f"warning: {name}: nodes without _UID are keyed by #id; keys shift when nodes are added or removed "
            "(use --assign-uids to give them UIDs)",
            file=sys.stderr,
        )
    header = FIXED + cols + TAIL
    if a.format == "json":
        text = json.dumps(rows, ensure_ascii=False, indent=1) + "\n"
    else:
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=header, lineterminator="\r\n")
        w.writeheader()
        w.writerows(rows)
        text = buf.getvalue()
    if a.output:
        with open(Path(a.output), "w", encoding="utf-8-sig" if a.format == "csv" else "utf-8", newline="") as fh:
            fh.write(text)
        print(f"exported {len(rows)} lines from {len(graphs)} dialogue trees to {a.output}", file=sys.stderr)
    else:
        sys.stdout.write(text)
    return 0


# ---------------------------------------------------------------------------- import


def read_rows(path: Path) -> List[dict]:
    raw = path.read_text(encoding="utf-8-sig")
    if path.suffix.lower() == ".json" or raw.lstrip().startswith("["):
        rows = json.loads(raw)
        if not isinstance(rows, list):
            raise DialogueError("the JSON file must hold a list of rows")
        return [{k: ("" if v is None else str(v)) for k, v in r.items()} for r in rows]
    return list(csv.DictReader(io.StringIO(raw, newline="")))


def find_statement(g: Graph, key: str) -> Optional[dict]:
    base, _, index = key.partition(":")
    for node, _, idx, st in statements(g):
        nkey = node.get("_UID") if not base.startswith("#") else f"#{g.nid(node)}"
        if nkey == base and idx == index:
            return st
    return None


def cmd_import(ws, a) -> int:
    rows = read_rows(Path(a.file))
    if not rows:
        print("nothing to import")
        return 0
    loc_cols = [c for c in rows[0] if c not in FIXED + TAIL]
    locales = {c: resolve_locale(ws, c) for c in loc_cols}
    graphs: Dict[str, Graph] = {}
    changed_graphs: Dict[str, Graph] = {}
    changed, same, missing, conflicts = [], 0, [], []
    for i, row in enumerate(rows, start=2):
        gname, key = row.get("graph", ""), row.get("key", "")
        if gname not in graphs:
            try:
                graphs[gname] = ws.graph(gname, "dt")
            except Exception as e:  # noqa: BLE001 - reported per row
                graphs[gname] = None
                missing.append(f"line {i}: graph {gname!r}: {e}")
        g = graphs[gname]
        if g is None:
            continue
        st = find_statement(g, key)
        if st is None:
            missing.append(f"line {i}: {g.name} {key} not found")
            continue
        current = translations(st)
        locs = {val: row.get(col, "") for col, (_, val) in locales.items()}
        wanted_same = (
            row.get("text", "") == st.get("_text", "")
            and row.get("meta", "") == st.get("_meta", "")
            and all(current.get(v, "") == t for v, t in locs.items())
        )
        if wanted_same:
            same += 1
            continue
        if row.get("rev") and row["rev"] != rev(st):
            conflicts.append(
                f"line {i}: {g.name} {key}: changed in the asset since the export "
                f"(was {row.get('original', '')!r}, now {st.get('_text', '')!r})"
            )
            continue
        if set_statement(st, row.get("text", ""), row.get("meta", ""), locs):
            changed.append(f"{g.name} {key}: {row.get('text', '')[:60]!r}")
            changed_graphs[g.loaded.slot.address] = g
    for line in changed:
        print("changed   " + line)
    for line in conflicts:
        print("conflict  " + line)
    for line in missing:
        print("not found " + line)
    print(f"{len(changed)} changed, {same} unchanged, {len(conflicts)} conflicts, {len(missing)} not found")
    if a.dry_run:
        print("dry run: nothing written")
    else:
        for addr, g in changed_graphs.items():
            ws.save(g)
            print(f"saved {addr}")
    return 1 if conflicts or missing else 0


# ---------------------------------------------------------------------------- lint


def lint_dialogue(ws, g: Graph, locales: List[Tuple[str, int]]):
    from .lint import ERROR, INFO, WARNING, Issue

    name = g.loaded.slot.address
    out = []
    actors = {
        p.get("_keyName")
        for p in ((g.data.get("derivedData") or {}).get("actorParameters") or [])
        if isinstance(p, dict)
    }
    uids = {n.get("_UID") for n in g.nodes if n.get("_UID")}
    for n in g.nodes:
        at = f"#{g.nid(n)}"
        t = simple_name(n.get("$type", ""))
        actor = n.get("_actorName")
        if (
            actor
            and actor != INSTIGATOR
            and actor not in actors
            and t in ("StatementNode", "MultipleChoiceNode", "ActionNode", "ConditionNode")
        ):
            out.append(
                Issue(name, at, WARNING, "unknown-actor", f"actor {actor!r} is not among the tree's actor parameters")
            )
        if t == "Jumper":
            uid = (n.get("_sourceNodeUID") or {}).get("_targetNodeUID")
            if not uid or uid not in uids:
                out.append(Issue(name, at, ERROR, "jump-missing", f"Jump target {uid or '(none)'} does not exist"))
        if t == "MultipleChoiceNode":
            outs = len(g.out.get(g.nid(n), []))
            for i, _ in enumerate(n.get("availableChoices") or []):
                if i >= outs:
                    out.append(Issue(name, at, WARNING, "choice-unlinked", f"choice {i + 1} leads nowhere"))
    for n, kind, index, st in statements(g):
        at = f"#{g.nid(n)}" + (f":{index}" if index else "")
        if not (st.get("_text") or "").strip():
            out.append(
                Issue(name, at, WARNING, "empty-text", "empty " + ("choice" if kind == "choice" else "statement"))
            )
        tr = translations(st)
        for lname, val in locales:
            if not (tr.get(val) or "").strip():
                out.append(Issue(name, at, INFO, "missing-translation", f"no {lname} text"))
    return out


def cmd_lint(ws, a) -> int:
    from .lint import ERROR, INFO, WARNING

    graphs = dt_graphs(ws, a.graphs)
    locales = [resolve_locale(ws, t.strip()) for t in (a.locales or "").split(",") if t.strip()]
    issues = [i for g in graphs for i in lint_dialogue(ws, g, locales)]
    levels = {INFO: 0, WARNING: 1, ERROR: 2}
    issues = [i for i in issues if levels[i.severity] >= levels[a.min]]
    if a.json:
        print(json.dumps([i.__dict__ for i in issues], ensure_ascii=False, indent=1))
    else:
        cur = None
        for i in issues:
            if i.graph != cur:
                cur = i.graph
                print(cur)
            print("  " + i.line())
        counts = {s: sum(1 for i in issues if i.severity == s) for s in (ERROR, WARNING, INFO)}
        print(f"{len(graphs)} dialogue trees: {counts[ERROR]} errors, {counts[WARNING]} warnings, {counts[INFO]} info")
    return 1 if any(i.severity == ERROR for i in issues) else 0


def _guard(fn):
    def run(ws, a):
        try:
            return fn(ws, a)
        except DialogueError as e:
            print(f"error: {e}", file=sys.stderr)
            return 1

    return run


def register(sub) -> None:
    s = sub.add_parser("dt", help="Dialogue Tree texts: export / import (CSV, JSON) and checks")
    ds = s.add_subparsers(dest="dt_cmd", required=True)

    e = ds.add_parser("export", help="statements and choices as CSV (UTF-8 with BOM, for Excel) or JSON")
    e.add_argument("graphs", nargs="*", help="dialogue trees (default: all)")
    e.add_argument("--format", choices=["csv", "json"], default="csv")
    e.add_argument("--locales", help="translation columns, e.g. en,ru (names of the Locales enum or ISO codes)")
    e.add_argument("-o", "--output", help="file to write (default: stdout)")
    e.add_argument(
        "--assign-uids", action="store_true", help="give statement nodes without _UID one first (saves the graphs)"
    )
    e.set_defaults(func=_guard(cmd_export))

    i = ds.add_parser(
        "import", help="update texts, meta and translations from an exported file (structure is untouched)"
    )
    i.add_argument("file")
    i.add_argument("--dry-run", action="store_true")
    i.set_defaults(func=_guard(cmd_import))

    li = ds.add_parser(
        "lint", help="empty lines, choices leading nowhere, broken jumps, unknown actors, missing translations"
    )
    li.add_argument("graphs", nargs="*")
    li.add_argument("--locales", help="report statements without these translations")
    li.add_argument("--min", choices=["info", "warning", "error"], default="info")
    li.add_argument("--json", action="store_true")
    li.set_defaults(func=_guard(cmd_lint))
