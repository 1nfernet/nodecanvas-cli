# nodecanvas-cli

Read, search, lint and edit [NodeCanvas](https://nodecanvas.paradoxnotion.com/) graphs — Behaviour Trees,
FSMs and Dialogue Trees — in a Unity project from the command line, without opening Unity.

Built for coding agents (it ships as a Claude Code plugin) and just as usable by hand.

NodeCanvas stores a graph as one long JSON string inside a Unity YAML asset. A 120-node behaviour tree
is ~85 KB of folded JSON with GUID-linked variables and index-linked nodes. `nodecanvas-cli` shows the same tree
in ~10 KB of readable text, answers "where is this used?", and applies edits as validated batches that
are written back exactly the way Unity writes them.

```
$ nodecanvas-cli show Bot_BT
Bot_BT  [BT, repeat]  Assets/Graphs/Bot_BT.asset  nodes=10
vars: Self:Bot(pub)  Target:Unit  Alert:bool  Cool time:float  Speed:float
#0 Selector
├─#1 ? @Self.IsDead
├─#2 Sequencer "Fight"
│ ├─#3 ? !$Alert
│ ├─#4 Cooldown [coolDownTime=$Cool time]
│ │ └─#5 @Self.Attack($Target)
│ └─#6 ProbabilitySelector
│   ├─(w=1.0) #7 @Self.MoveTo((1.0, 0, -2.5))
│   └─(w=3.0) #8 SubTree → Sub_BT {Self←$Self, Alert→$Alert}
└─[off] #9 $Speed *= 2.0 // double the speed
```

## Features

- **show** — compact tree (BT), state list with ordered transitions (FSM), or script (Dialogue Tree).
  Default values are shown with their real meaning, read from the project's own C# sources.
- **refs** — reverse lookup across all graphs: who calls `Bot.Attack`, which graphs use a
  subtree, a variable, or a task type. Graphs bind C# members by name and break silently on renames;
  this finds them.
- **lint** — missing task/variable types, dangling blackboard references, reflection bindings to members
  that no longer exist, missing subgraphs and stale variable mappings, too many children for a node
  type, unreachable nodes.
- **apply** — atomic batches of edit operations: add / remove / move / wrap / unwrap / replace nodes, set
  fields, connect and disable, blackboard variables, subgraph mappings, FSM transitions, dialogue
  choices. Short value forms (`"$Var"`, enum names, `[x, y, z]`, `{"call": "Type.Method", ...}`) are
  converted using the C# field types. Batches that introduce lint errors are not saved.
- **dt export / import / lint** — dialogue lines (statements, choices, translations) to CSV/JSON for writers
  and translators and back, keyed by node UIDs, with conflict detection; checks for empty lines,
  dangling choices and jumps, unknown actors and missing translations.
- **why / paths / stats** — why control reaches a node (conditions along the path from the root, FSM
  transitions in, dialogue choices), the same for every call of a member or write of a variable, and
  graph statistics.
- **owners / blackboards** — which prefabs and scenes run a graph (GraphOwner components, exposed
  variable overrides, owner blackboards), graphs assigned nowhere, and Blackboard / GlobalBlackboard /
  AssetBlackboard contents; lint checks global variables and owner overrides against them.
- **types / members** — discover task and node types with their fields, defaults and enum members, and the
  properties/methods reflection tasks can call.
- Works on graph assets (`.asset`) and graphs bound to a `GraphOwner` in prefabs and scenes.
- Byte-exact: unchanged graphs round-trip identically; edited graphs are re-serialized with Unity's
  YAML folding and FullSerializer's key order, so diffs stay minimal.
- **verify --unity** — after editing, let the open Unity Editor import, deserialize and validate the graphs
  (through the [Unity CLI](https://docs.unity.com/) and the project's `com.unity.pipeline` package) and
  report NodeCanvas errors, Missing types, broken reflection bindings and console messages.
- Pure Python ≥ 3.9, no dependencies, no Unity required (except for `verify --unity`); the optional MCP
  server needs `nodecanvas-cli[mcp]`.

## Install

```bash
pipx install nodecanvas-cli                                                     # once published to PyPI
uvx --from git+https://github.com/Primeer/nodecanvas-cli nodecanvas-cli --help  # straight from the repository
python -m nodecanvas_cli --help                                                 # from a checkout, with src/ on PYTHONPATH
```

The Claude Code plugin also bundles a launcher, `skills/nodecanvas-cli/scripts/nodecanvas-cli`, which runs the CLI
from the plugin's own sources with any Python 3.9+.

### Claude Code plugin

The repository is also a Claude Code plugin marketplace containing the `nodecanvas-cli` plugin, which adds a
skill that teaches the agent when and how to use the CLI:

```
/plugin marketplace add Primeer/nodecanvas-cli
/plugin install nodecanvas-cli@nodecanvas-cli
```

### MCP server (Cursor, Codex, Gemini and other agents)

```bash
pipx install "nodecanvas-cli[mcp]"            # Python 3.10+
nodecanvas-cli --project /path/to/UnityProject mcp
```

A stdio MCP server whose tools wrap the commands (`show`, `node`, `refs`, `lint`, `apply`, `refactor_*`,
`why`, `owners`, …) and return the same compact text. Tools that write files (`apply`, `refactor_*`,
`new_graph`, `extract`, `inline`, `copy`) default to `dry_run=true`; pass `dry_run=false` to save.
Example client config:

```json
{"mcpServers": {"nodecanvas-cli": {"command": "nodecanvas-cli", "args": ["--project", "/path/to/UnityProject", "mcp"]}}}
```

## Usage

Run inside a Unity project (any subdirectory) or pass `--project <root>`.

```bash
nodecanvas-cli list [--kind bt|fsm|dt]
nodecanvas-cli show <graph> [--node N] [--depth D] [--expand]
nodecanvas-cli vars <graph>
nodecanvas-cli node <graph> <id>... [--raw]         # spec form accepted by apply (or raw JSON)
nodecanvas-cli diff <graph> [--rev HEAD]            # compact view vs a git revision
nodecanvas-cli refs <Type.Member | GraphName | $Variable | TaskType>
nodecanvas-cli dt export [graph...] [--locales en,ru] [--format csv|json] [-o file] | dt import <file> | dt lint
nodecanvas-cli why <graph> <node> [--expand]       # conditions on the way from the root to a node
nodecanvas-cli paths [graph] --to <Type.Member | $Var> [--reads]
nodecanvas-cli stats [graph] [--json]
nodecanvas-cli scaffold action|condition|decorator|composite <Name> --dir Assets/... [--field "x:BBParameter<float>=1"]
nodecanvas-cli owners [<graph> | --all]            # GraphOwners in prefabs/scenes, parent graphs
nodecanvas-cli blackboards [--global]
nodecanvas-cli lint [graph...] [--min info|warning|error] [--json]
nodecanvas-cli types [query] [--role action|condition|bt|composite|decorator|fsm|dt]
nodecanvas-cli members <Type> [filter]
nodecanvas-cli refactor rename-member|rename-type|retarget ... [--dry-run]
nodecanvas-cli new <path.asset> --kind bt|fsm|dt [--var "Name:type[=value][:pub]"]
nodecanvas-cli extract <graph> <node> <new.asset> | inline <graph> <node> | copy <src> <node> <dst> <parent>
nodecanvas-cli apply <graph> <ops.json | -> [--dry-run] [--force]
nodecanvas-cli verify --unity [graph... | --changed] [--resave]   # load the graphs in the open Unity Editor
```

`<graph>` is a path, an asset name, a unique prefix, or `Prefab.prefab:GameObject` for bound graphs;
`--kind bt|fsm|dt` disambiguates names shared by graphs of different kinds.

Layout of created/moved nodes can be tuned with `nodecanvas-cli.json` in the Unity project root:

```json
{"layout": {"grid": 20, "level_gap": 200, "sibling_gap": 300}, "scaffold": {"comments": false}}
```

### Editing example

```json
[
  {"op": "add", "parent": "3", "index": 0, "id": "w",
   "node": {"$type": "ActionNode", "_action": {"$type": "Wait", "waitTime": "$Delay"}}},
  {"op": "wrap", "node": "w", "with": {"$type": "Cooldown", "coolDownTime": 2}},
  {"op": "add", "parent": "3", "node": {"$type": "ConditionNode",
   "_condition": {"check": "Bot.DistanceTo", "args": ["$Target"], "agent": "$Owner", "compare": ">", "value": "$Range"}}},
  {"op": "var-add", "name": "Range", "type": "float", "value": 4},
  {"op": "remove", "node": "12", "recursive": true}
]
```

```bash
nodecanvas-cli apply Bot_BT ops.json --dry-run   # prints a before/after diff of the compact view
nodecanvas-cli apply Bot_BT ops.json
```

All operations and value forms: [skills/nodecanvas-cli/references/ops.md](skills/nodecanvas-cli/references/ops.md).

## How it works

- **Container codec** — finds `_serializedGraph` (assets) or `_boundGraphSerialization` (GraphOwner
  components) and decodes Unity's folded single-quoted YAML scalar; encoding reproduces Unity's folding
  rule exactly, so untouched graphs stay byte-identical.
- **FullSerializer JSON** — numbers keep their original text and keys their order; new objects are
  written in FullSerializer's field order. Semantics of omitted values follow FullSerializer: a missing
  field keeps its C# initializer, while a serialized parameter without `_value` holds `default(T)`.
- **Schema from sources** — NodeCanvas ships as source code, so nodecanvas-cli scans the project's C# files
  (lazily, with an mtime cache in `~/.cache/nodecanvas-cli`) for task/node fields, initializers, enums,
  display names, connection limits and the methods available to reflection tasks. Without NodeCanvas
  sources, reading and editing still work with reduced detail.
- **Normalization** — after an edit, nodes are renumbered the way the NodeCanvas editor does
  (depth-first from the root), `$id`s are written only on referenced nodes, and BT children keep a
  left-to-right position order matching their execution order.

## Limitations

- NodeCanvas 3.x serialization only.
- Graphs serialized to an external JSON file (`_externalSerializationFile`) are not supported.
- C# scanning is heuristic (no compiler): unusual declarations may be missed; lint reports unresolved
  members as warnings when a type's base classes live outside the project sources.
- Close the graph in the NodeCanvas editor (or let Unity reimport) before editing it externally.

## Development

```bash
pip install -e ".[dev]"
pytest
python tests/fixtures/generate.py      # regenerate synthetic fixtures
UPDATE_SNAPSHOTS=1 pytest              # refresh show snapshots
python scripts/roundtrip_check.py <UnityProject>   # verify byte-exact round-trip on a real project
```

Tests run against synthetic graphs and small stubs of the NodeCanvas API surface
(`tests/fixtures/project/Assets/Stubs`); NodeCanvas itself is a commercial asset and is not included.

## License

MIT. NodeCanvas is a product of Paradox Notion; this project is not affiliated with it.
