# `nodecanvas-cli apply` reference

## Contents
- [Addressing](#addressing)
- [Value forms](#value-forms)
- [Reflection tasks](#reflection-tasks-call--check--get)
- [Structure operations](#structure-operations)
- [Field operations](#field-operations)
- [Blackboard operations](#blackboard-operations)
- [Subgraphs](#subgraphs)
- [FSM](#fsm)
- [Dialogue Trees](#dialogue-trees)
- [Layout and ids](#layout-and-ids)

## Addressing

`"node"`, `"parent"`, `"from"`, `"to"` take a node id as printed by `show` before the batch (`"5"` or
`"#5"`), or an alias created earlier in the same batch with `"id": "name"` on `add` / `wrap`.

## Value forms

nodecanvas-cli looks up the C# type of the field being written and converts:

| You write | For a field of type | Result |
|---|---|---|
| `"$Name"` | any `BBParameter<T>` / task parameter | link to blackboard variable `Name` (must exist) |
| `"$Global/Name"` | any `BBParameter<T>` | name-only link (global blackboards) |
| `1.5`, `3`, `true`, `"text"` | `float`/`int`/`bool`/`string`, `BBParameter<T>` | literal (`default(T)` is stored as `{}`) |
| `[1, 0, 2]` or `{"x":1,"z":2}` | `Vector2/3/4`, `Quaternion`, `Color` | vector; zero components omitted |
| `"Subtract"`, `"Enemy|Ally"`, `2` | enum | member value (flags combine with `|`) |
| `"Patrol_BT"` | a graph (subtree, nested BT/FSM) | object reference added to the asset's reference table |
| `{"$type": "Wait", ...}` | `ActionTask`/`ConditionTask` | task built from its fields |
| `[{...}, {...}]` | `ActionTask` / `ConditionTask` field | `ActionList` / `ConditionList` of those tasks |
| `null` | anything | field reset (`{}` for parameters) |
| `{"raw": <json>}` | anything | inserted verbatim |

Type names may be short (`Wait`), display names (`Repeat` for `Repeater`), generic (`SetVariable<Pawn>`)
or fully qualified. Unknown field names are rejected with the list of valid fields — use
`nodecanvas-cli types <Type>` to see them with defaults and enum members.

Node specs accept `_name`, `_tag`, `_comment` and `_position` (`[x, y]`) in addition to type fields.
Task specs accept `_isDisabled`, `_invert` (conditions) and `agent` (`"$Var"`, the override agent).

## Reflection tasks (`call` / `check` / `get` / `set`)

```json
{"call": "BotController.MoveTo", "args": ["$Target", [0, 1, 0]], "agent": "$Owner", "returns": "$Ok"}
{"check": "BotController.DistanceTo", "args": ["$Target"], "agent": "$Owner", "compare": ">", "value": "$Range"}
{"check": "BotController.IsDead", "agent": "$Owner", "value": false}
{"get": "BotController.Position", "agent": "$Owner", "returns": "$Pos"}
{"set": "BotController.speed", "agent": "$Owner", "value": 4.0}
{"get": "BotController.speed", "agent": "$Owner", "returns": "$Speed"}
{"check": "BotController.speed", "compare": ">", "value": 2.5}
{"set": "BotController.Armor", "agent": "$Owner", "value": 1.5}
{"get": "System.Single.PositiveInfinity", "fieldType": "float", "returns": "$Best"}
```

- `call` → `ExecuteFunction_Multiplatform` (methods); `check` → `CheckFunction_Multiplatform` for methods
  or `CheckProperty_Multiplatform` for properties; `get` → `GetProperty_Multiplatform`.
- On a field, `set` / `get` / `check` make `SetField` / `GetField` / `CheckField` (written with
  `"$version": 1`, as NodeCanvas expects); a static field takes no `agent`. On a property, `set` makes
  `SetProperty_Multiplatform` and needs a public setter.
- Fields of System/Unity types (not in the project sources) need their full type name and the field's
  type: `"fieldType": "float"` (C# alias or full name, e.g. `UnityEngine.Vector3`).
- Overloads are chosen by argument count, then by the types of `$variables` / literals. If ambiguous,
  the error lists the signatures; pick one with `"overload": ["Pawn", "Vector3"]` (parameter type names).
- `compare`: `==` (default), `>`, `<`, `>=`, `<=`. `value` defaults to `true`.
- `nodecanvas-cli members <Type> [filter]` lists what can be called.

## Structure operations

| Op | Fields | Notes |
|---|---|---|
| `add` | `node`, `parent`?, `index`?, `id`?, `item`? | BT: `parent` required unless the graph is empty. `index` defaults to last. `item`: element for a child-synced list (e.g. a ProbabilitySelector weight, default `1`). |
| `remove` | `node`, `recursive`? | Refuses nodes with children unless `recursive` (removes the subtree, keeping nodes that still have other parents). |
| `move` | `node`, `parent`, `index`?, `from`? | Re-parent / reorder; keeps the connection's settings. Cycles are refused. |
| `wrap` | `node`, `with`, `id`? | Inserts a decorator/composite between the node and its parent (works on the root). |
| `unwrap` | `node` | Removes a node with one child, putting the child in its place. |
| `replace` | `node`, `with` | Changes the node's type in place, keeping position, name and connections. |
| `connect` | `from`, `to`, `index`?, `condition`? | Generic connection. BT nodes take one parent except `Merge`. |
| `disconnect` | `from`, `to` | |
| `disable` | `from`, `to`, `value`? | Disables (or with `"value": false` re-enables) a connection. |
| `set-start` | `node` | Makes the node the graph's start/root (BT root must have no parent). |
| `simplify` | `node`? | Removes Merge nodes with at most one parent (in the subtree of `node`, or everywhere). |
| `layout` | `node`? | Re-lays out the subtree of `node` (default: root) on the grid. The subtree root itself is snapped to the grid and placed `level_gap` below its parent (the graph root only gets snapped). |

Connection limits (max children/parents per node type, one parent per BT node except Merge) are checked
once after the whole batch, so a node may temporarily have too many children inside a batch. Cycles
are refused immediately.

Node specs for nested-graph nodes (SubTree, Sub BehaviourTree state, …) may carry `"map"` with the same
syntax as the `map` operation; `nodecanvas-cli node` prints them that way.

## Field operations

| Op | Fields | Notes |
|---|---|---|
| `set` | `node` (or `from`+`to` for a connection), `path`, `value`, `raw`? | `path` is dotted with list indices: `_action.waitTime`, `_condition.conditions.1.valueB`, `childWeights.0`. Value conversion uses the field's type; `raw: true` skips it. |
| `unset` | `node`, `path` | Removes a key (the field returns to its C# initializer). |

## Blackboard operations

| Op | Fields | Notes |
|---|---|---|
| `var-add` | `name`, `type`, `value`?, `public`? | `type` is C# (`float`, `bool`, `Vector3`, `Pawn`, `List<Pawn>`). |
| `var-set` | `name`, `value`?, `public`? | Changes the stored value / exposure. |
| `var-rename` | `name`, `to` | Updates every reference in this graph. Subgraph mappings in *other* graphs keep working (they use ids). |
| `var-remove` | `name`, `force`? | Refuses while the variable is used, unless `force`. |

## Subgraphs

| Op | Fields | Notes |
|---|---|---|
| `subgraph-set` | `node`, `graph`, `map`? | Points a SubTree / nested state at another graph asset. |
| `map` | `node`, `map` | Replaces the variable mapping: `{"SubVar": "<-$Parent"}` passes a value in, `"->$Parent"` reads it back after the subgraph runs, `"<->$Parent"` both; a literal passes a constant in. |
| `map` | `node`, `"auto": true` | Adds mappings for the subgraph's public variables that are not mapped yet, to parent variables with the same name and type; direction from how the subgraph uses each (read → `<-`, written → `->`, both → `<->`). Prints what was mapped and skipped. |

## FSM

States are added with `add` without `parent`. Transitions:

| Op | Fields | Notes |
|---|---|---|
| `transition-add` | `from`, `to`, `condition`?, `index`? | `index` sets evaluation order among the state's transitions. No condition = always. |
| `transition-remove` | `from`, `to` | |
| `set-start` | `node` | Start state. |

Use `set` with `"from"`/`"to"` and `"path": "_condition"` to change a transition's condition.

## Dialogue Trees

| Op | Fields | Notes |
|---|---|---|
| `choice-add` | `node`, `text`, `to`?, `condition`?, `meta`?, `index`? | Adds a choice to a `MultipleChoiceNode` and, with `to`, connects it. |

Statements: `{"$type": "StatementNode", "_actorName": "Guard", "statement": {"_text": "Hi"}}`.

## Layout and ids

- Only created or moved nodes are positioned; existing nodes move only to make room. Coordinates are on
  the NodeCanvas editor grid (20 px). A BT child sits `level_gap` (200 px = 10 cells) below its parent;
  nodes on one row are at least `sibling_gap` (300 px = 15 cells) apart. When there is no room between
  siblings, the siblings to the right shift with their subtrees, and branches that would overlap nodes
  of neighbouring subtrees are pushed apart. Siblings keep a left-to-right order matching the child list
  (the NodeCanvas editor re-sorts children by x when a node is dragged). Moved subtrees keep their
  internal layout. `wrap` puts the wrapper in the wrapped node's place and moves that subtree one level
  down; `unwrap` moves the child up into the removed node's place.
- FSM/DT nodes are placed `sibling_gap` to the right of their source (or the rightmost node), moving down
  a row while that spot is taken.
- After every batch `apply` prints a summary, e.g.
  `layout: placed 1 new, moved 0 by the edit, shifted 5 existing to make room:` followed by
  `#5 subtree (2 nodes) +400,0` lines (ids after renumbering). `apply --positions` adds coordinates to
  the before/after diff; `show --positions` / `diff --positions` print them for the whole graph.
- `nodecanvas-cli node <graph> <id>... --raw` always returns `{"<id>": {"node": ..., "out": [...]}}`.
- Zero coordinates are omitted from `_position`, as FullSerializer does.
- The values come from `nodecanvas-cli.json` in the Unity project root, if present:
  `{"layout": {"grid": 20, "level_gap": 200, "sibling_gap": 300}}`. Use `set` on `_position` to place
  a node yourself.
- After every batch, nodes are renumbered like the NodeCanvas editor does (depth-first from the root),
  so always re-run `show` before writing the next batch.
