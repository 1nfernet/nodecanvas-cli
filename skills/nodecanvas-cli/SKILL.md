---
name: nodecanvas-cli
description: Read, search, lint and edit NodeCanvas graphs (Behaviour Trees, FSMs, Dialogue Trees) in a Unity project with the nodecanvas-cli command instead of parsing .asset/.prefab YAML by hand. Use it whenever a task touches NodeCanvas graphs or bot/NPC AI built with them — understanding what a behaviour tree or state machine does, finding which graphs call a C# method or use a subtree or blackboard variable before renaming/removing it, checking graphs for broken references, or adding, removing, moving or re-wiring nodes, transitions, tasks and variables — even if the user only says "the bot's tree", "the AI asset", "the FSM" or names a *_BehaviourTree.asset file.
---

# nodecanvas-cli — NodeCanvas graphs from the command line

NodeCanvas stores each graph as one long JSON string inside a Unity YAML file. Reading it raw is
expensive and editing it by hand is error-prone (node ids, `$ref` links, variable GUIDs, object
reference tables, defaults FullSerializer leaves out). `nodecanvas-cli` turns a graph into a compact tree you
can read in one go and applies edits as validated, atomic batches that are written back in the exact
format Unity produces.

## Running it

Run commands from anywhere inside the Unity project (or pass `--project <root>`). Use the first that works:

1. `nodecanvas-cli <command> ...` — if it is on PATH (`pipx install nodecanvas-cli`).
2. `<this skill's base directory>/scripts/nodecanvas-cli <command> ...` — the CLI bundled with the plugin;
   needs only Python 3.9+. The base directory is shown when this skill loads.
3. `uvx nodecanvas-cli <command> ...` — once the package is on PyPI.

Below, `nodecanvas-cli` stands for whichever of these works.

The first run in a big project scans assets and C# sources and caches the results (~1–3 s); later runs are fast.

## Reading graphs

| Command | Use it to |
|---|---|
| `nodecanvas-cli list [--kind bt\|fsm\|dt]` | see all graphs, their node counts and which subgraphs they use |
| `nodecanvas-cli show <graph> [--node N] [--depth D] [--expand] [--positions]` | read a graph; `--expand` inlines subtrees/nested graphs, `--positions` appends editor coordinates `@(x,y)` |
| `nodecanvas-cli vars <graph>` | blackboard variables, values, and which nodes use each |
| `nodecanvas-cli node <graph> <id>...` | a node in the exact spec form `apply` accepts — copy it to add a similar node (`--raw` for the serialized JSON, rarely needed) |
| `nodecanvas-cli diff <graph> [--rev HEAD] [--positions]` | compact before/after of a graph against a git revision — use it to review edits; `--positions` also shows moved nodes |

`<graph>` is a file path, a file name (`Guard_Attack_BehaviourTree`), a unique prefix (`Guard_Attack`),
or `Scene.unity:GameObject` / `Prefab.prefab:GameObject` for graphs bound to a GraphOwner. When a
name matches graphs of different kinds (`X_BehaviourTree` and `X_FSM`), add `--kind bt|fsm|dt`.

Notation in `show`:

```
#0 BinarySelector ? !@Owner.IsDead          ? condition      @Owner  agent override variable
├─#1 Sequencer "Attack"                      "..." node name  $Var    blackboard variable
│ ├─#2 $Timer -= 1.0 per sec                 [off] disabled connection
│ ├─(w=2.0) #3 @Owner.MoveTo($Target, (0, 1.0, 0))      (w=..) child weight of a ProbabilitySelector
│ └─#4 SubTree → Patrol_BT {Self←$Owner, Found→$Found}   ← value passed in, → read back, ↔ both
└─#5 ↑ (see above)                           node already printed (Merge / multiple parents)
```

Reflected members (methods, properties, fields) print as `@Var.Member` when the task overrides its
agent with a variable, otherwise as `Type.Member` (the graph's agent, or the type itself for static
members): `@Owner.speed = 4.0`, `Bot.Attack($Target)`, `$Best = Single.PositiveInfinity`.

FSMs print each state with its transitions in evaluation order (`--[condition]--> #3 Name`), Dialogue
Trees print a script (`[Actor] Say "..."`, numbered choices, `Jump → #N`). Values that are left at
their defaults are shown with their real meaning (taken from the project's C# sources).

## Explaining behaviour

- `nodecanvas-cli why <graph> <node> [--expand]` — what has to happen for control to reach a node: the chain
  from the root with each step's requirement (`#1 must fail first` under a Selector, `#3 must succeed
  first` under a Sequencer, `branch true` of a BinarySelector, weight and chance under a
  ProbabilitySelector, `at most once per 2s` under a Cooldown, `for each element of $List` under
  Iterate, step N of a Step Sequencer…). Merge nodes give several numbered paths; disabled connections
  and unreachable nodes are called out. For an FSM state: the transitions leading in (with the earlier
  transitions of the source state that must not fire) and AnyState; for a dialogue node: choices,
  condition branches and jumps. `--expand` continues through the graphs that nest this one as a
  SubTree / nested state. Use it to answer "why does (doesn't) the bot do X".
- `nodecanvas-cli paths [graph] --to <Type.Member | $Var> [--reads]` — `why` for every call of a member or
  every write (`--reads`: also reads) of a variable, across all graphs when no graph is given.
- `nodecanvas-cli stats [graph] [--json]` — size, BT depth, nodes by role, most called members, most written
  variables, subgraphs used / shared.

## Finding usages before you change code

`nodecanvas-cli refs <query>` searches every graph:

- `refs BotController.BeginBlock` — tasks that call a method/property via reflection. Do this before
  renaming or deleting a C# member: graphs bind members by name and break silently.
- `refs Patrol_BT` — where a graph is used as a subtree / nested state.
- `refs '$Target'` — nodes referencing a variable name; `refs Cooldown` — nodes/tasks of a type.

## Where graphs run and where variables come from

- `nodecanvas-cli owners <graph>` — prefabs/scenes whose GraphOwner (BehaviourTreeOwner, FSMOwner, project
  subclasses) runs the graph, with the owner's Blackboard (and its parent AssetBlackboard) and the
  exposed-variable overrides (`$Var = value`); also parent graphs using it as a subgraph, and PrefabInstances
  overriding `_graph` (listed, not parsed further). `owners --all` — table of every graph; graphs
  assigned nowhere in assets are flagged (they may still be assigned from code).
- `nodecanvas-cli blackboards [--global]` — Blackboard / GlobalBlackboard components and AssetBlackboards
  with their variables.
- `vars` and `show` mark variables that are not on the graph's own blackboard with their source:
  `owner` (the owner's Blackboard or its parent asset), `global` (`$Global/Var` → a GlobalBlackboard
  with that identifier) or `missing`. `apply` accepts such variables too (`"$Global/Alarm"`, `"$HomeDist"`).

## Refactoring C# that graphs use

When renaming or changing C# members/types that graphs call, update the graphs in the same change —
all commands work on every graph at once, print a compact diff per graph and support `--dry-run`:

- `nodecanvas-cli refactor rename-member BotController.LookAt FaceTowards` — methods, properties and fields.
  For an overloaded method only the renamed overload's calls change: rename it in C# first and the sources
  tell which one moved (calls of the others are kept and noted), or pick it with `--params Pawn,Vector3`
  (`--params ''` for no parameters); `--all-overloads` renames every overload. When graphs call several
  overloads and nothing tells which one is meant, the command stops with an error.
- `nodecanvas-cli refactor rename-type Old.Namespace.Type New.Namespace.Type [--assembly Asm]` — namespace,
  name or nesting changes (also fixes "missing type" wrappers left by an earlier rename).
- `nodecanvas-cli refactor retarget BotController.DistanceTo --to Booster [--from Pawn]` — move calls to another
  overload. Only calls whose argument variables fit the new overload are changed; the others are listed
  as skipped — the first 20, `--verbose` for all (`--all` changes them too, and lint then reports each
  as `arg-type-mismatch`).

`show` prints the bound overload as `Member‹T1, T2›` whenever a method has several overloads of that
arity, so overload changes are visible in `show` and `diff`.

## Creating graphs and moving subtrees

- `nodecanvas-cli new Assets/.../X_BehaviourTree.asset --kind bt|fsm|dt [--var "Name:type[=value][:pub]"]
  [--from <graph>:<node>]` creates a graph with its `.meta` (new GUID), optionally starting with a copy
  of a subtree; with `--from` the copied variables are public, like `extract` makes them, so the graph
  is ready to be used as a SubTree (`--private-vars` to keep them private). `--allow-outside` permits a path outside `Assets/`/`Packages/` for experiments.
- `nodecanvas-cli extract <graph> <node> Assets/.../Part_BehaviourTree.asset [--name N]` moves a BT subtree into
  a new SubTree asset and puts a SubTree node in its place. Variables the subtree uses become public
  variables of the new graph and are mapped automatically (`←` read, `→` written, `↔` both).
- `nodecanvas-cli inline <graph> <subtree-node>` is the reverse.
- `nodecanvas-cli copy <src> <node> <dst|.> <parent|root> [--index i] [--create-vars]` copies a subtree
  (`root` makes it the root of an empty graph); variables are matched by name and type in the
  destination. Variables created by `--create-vars` are private unless `--public-vars` is given, and keep their ids from the source graph (a new id only
  on collision), so mappings that pointed at them keep working.

## Dialogue texts

- `nodecanvas-cli dt export [graph...] [--locales en,ru] [--format csv|json] [-o file]` — every line of the
  dialogue trees (statements and choices) with actor, kind, meta and one column per locale. CSV is UTF-8
  with BOM for Excel. Rows are keyed by the node `_UID` (+ choice number); nodes without one are keyed
  by `#id`, which shifts after structural edits — `--assign-uids` writes UIDs first (changes the assets).
- `nodecanvas-cli dt import <file> [--dry-run]` — writes back texts, meta and translations only (never
  structure); rows whose statement changed in the asset since the export are reported as conflicts and
  skipped; exit code 1 on conflicts or unknown keys. Use it for bulk text edits too: export, edit, import.
- `nodecanvas-cli dt lint [--locales ru]` — empty lines, choices leading nowhere, broken Jumps, actors that are
  not actor parameters, missing translations.

## Custom tasks and nodes

When a graph needs logic that no existing task covers (instead of chaining many reflection calls),
create a task class: `nodecanvas-cli scaffold action|condition|decorator|composite <Name> --dir Assets/...
[--field "speed:BBParameter<float>=1"]... [--category "AI/Combat"] [--agent BotController] [--title T]
[--description D] [--dry-run]`. It writes `<Name>.cs` with the attributes, fields and stub bodies
(`EndAction(true)` / `return true`), using the namespace and indentation of neighbouring scripts; no
`.meta` (Unity creates it). `nodecanvas-cli types <Name>` sees it at once, so `apply` can use it in the same
session; compiling in Unity is up to the user. `{"scaffold": {"comments": false}}` in `nodecanvas-cli.json`
drops the TODO comments (for projects that do not want comments).

## Checking graphs

`nodecanvas-cli lint [graph...] [--min info]` reports missing task/variable types, variables that are not on
the blackboard, reflection bindings to members that no longer exist, unassigned or missing subgraphs,
stale subgraph variable mappings, arguments whose stored or variable type does not fit the bound
overload (`arg-type-mismatch`, with a `fix:` op when another overload fits), subgraph mappings between
variables of different types, too many children for a node type, and unreachable nodes. It exits with 1 when there are errors. Run it
after C# refactors that touch types used by graphs.

Owners and blackboards: `missing-global-var` (warning) — `$Name/Var` has no GlobalBlackboard
`Name` with that variable; `exposed-override-type` / `exposed-override-stale` (warning) — an owner
overrides an exposed variable with another type, or one the graph no longer has; `owner-missing-graph`
(warning, whole-project lint) — a GraphOwner with neither a graph asset nor a bound graph. A variable
missing from the graph's blackboard but found on an owner's blackboard is `owner-var` (info), not `dangling-var`.

Binding and variable hints at `--min info`: `null-arg` — an object argument is empty (null is passed;
often intended, e.g. clearing a target), with a `fix:` op naming fitting variables — the `current` of
an enclosing Iterate first, then variables written earlier on the path from the root; `unused-var` (owner overrides count as use);
`unmapped-subgraph-var` — a subgraph reads an object variable nobody maps, so it is null there.
`apply` and `diff` print `warning: lost binding: …` when an argument that was bound to a variable
became empty — that one is almost always a mistake. A `fix:` line is a ready operation for `apply`.

Layout checks: `child-order` (warning) — siblings are not left-to-right in execution order, so dragging
one in the editor would re-sort them and change behaviour; `overlap` (warning) — nodes closer than the
editor's minimum node size; `off-grid` (info).

`recoverable-missing` (info) means NodeCanvas once saved a task or variable as "missing" while its type
did not compile; the type exists again, Unity restores it on load and the wrapper disappears on the
next save in the editor. `show` renders such tasks with `(recovered)`. It is not an error.

## Checking graphs in the Unity Editor

`nodecanvas-cli verify --unity <graph>...` (or `--changed` for graphs in files changed since `HEAD`) asks the
open Unity Editor to re-import each graph, deserialize and validate it, and reports what NodeCanvas sees:
node/task errors shown red in the graph editor (missing tasks, missing reflected members), Missing
wrappers left after loading, overloads resolved by name fallback, node/variable counts that differ from
the file, and console errors logged meanwhile. It waits for script compilation first. Exit code 0 — all
fine, 1 — problems, 2 — no Editor reachable (it needs the `unity` CLI and the project's
`com.unity.pipeline` package; `unity pipeline install` adds it). Use it after edits when an Editor is
open, instead of asking the user to open the graph. Graphs bound in scenes are checked only when the
scene is open. `--resave` lets NodeCanvas re-serialize graph assets (drops recoverable Missing
wrappers); it changes files, so use it only when asked.

## Editing graphs

1. `nodecanvas-cli show <graph>` — note the `#ids` you will touch (ids are positions; they change after every edit).
2. To learn how to write a node, print a similar existing one with `nodecanvas-cli node <graph> <id>` and
   adapt it. For a task or node type you have not seen yet: `nodecanvas-cli types <Name>` (fields, defaults,
   enum values) or `nodecanvas-cli types --role action|condition|bt|composite|decorator|fsm|dt`.
   For reflection tasks: `nodecanvas-cli members <Type> [filter]` lists callable properties/methods.
3. Write a JSON array of operations and run `nodecanvas-cli apply <graph> ops.json --dry-run` (or `-` for stdin).
   Read the printed before/after diff and the `layout:` summary under it — it says how many nodes were
   placed, moved by the edit, and which existing subtrees were shifted (and by how much) to make room.
   Add `--positions` to see coordinates in the diff. Then run again without `--dry-run`.
4. `nodecanvas-cli show <graph>` again to get the new ids before the next batch.
5. If a Unity Editor is open on the project, `nodecanvas-cli verify --unity --changed` confirms Unity loads the result.

A batch is atomic: if any operation fails, or the result has new lint errors, nothing is written (use
`--force` only when the error is expected). Connection limits (e.g. a BinarySelector's two children)
are checked on the final state, so operations can be in any order that makes sense. Ids inside one
batch always refer to the graph as it was before the batch; give new nodes an `"id"` alias to reference
them later in the same batch. New and moved nodes are placed on the editor grid (children 10 cells
below the parent, neighbours at least 15 cells apart); `{"op": "simplify"}` removes Merge nodes left
with a single parent.

```json
[
  {"op": "add", "parent": "3", "index": 0, "id": "w",
   "node": {"$type": "ActionNode", "_action": {"$type": "Wait", "waitTime": "$Delay"}}},
  {"op": "wrap", "node": "w", "with": {"$type": "Cooldown", "coolDownTime": 2}},
  {"op": "add", "parent": "3", "node": {"$type": "ActionNode",
   "_action": {"call": "BotController.MoveTo", "args": ["$Target", [0, 1, 0]], "agent": "$Owner"}}},
  {"op": "set", "node": "7", "path": "_condition.valueB", "value": false},
  {"op": "move", "node": "9", "parent": "2", "index": 1},
  {"op": "remove", "node": "12", "recursive": true}
]
```

Values use short forms that nodecanvas-cli converts using the field's C# type: `"$Var"` for a variable,
plain literals, `[x, y, z]` for vectors, enum member names (`"Add"`, `"Enemy|Ally"` for flags),
graph names for subgraph fields, short type names (`"Wait"`, `"SetVariable<Pawn>"`), and
`{"call"|"check"|"get"|"set": "Type.Member", ...}` for reflection tasks (methods, properties, fields). Raw FullSerializer JSON is accepted
anywhere (`{"raw": ...}` forces it).

The full list of operations and value forms is in `references/ops.md` — read it before writing a
non-trivial batch (FSM transitions, dialogue choices, subgraph variable maps, variable renames).

## Things to keep in mind

- Ask before editing graphs the user did not mention, and keep edits minimal: the diff Unity shows in
  version control is the user's review surface. nodecanvas-cli keeps unrelated bytes unchanged, but node ids
  are renumbered the way the NodeCanvas editor does it, so the JSON diff can be larger than the change.
- After editing, tell the user to let Unity reimport the asset (focus the editor) before opening it in
  the NodeCanvas window; an open graph editor window may otherwise hold stale data.
- A missing `_value` in the raw JSON means `default(T)` (false, 0, null) — not the C# initializer.
  Trust `show` for meaning rather than reading the raw JSON.
- If `lint` warns that NodeCanvas sources were not found, nodecanvas-cli falls back to built-in declarations of
  the common NodeCanvas nodes and tasks; reading, editing and `extract` still work, other types are
  handled less precisely.
- `missing-member` is an error when the type's base chain is known — including classes deriving from
  MonoBehaviour/ScriptableObject — and a warning only when a base lives outside the project sources.
