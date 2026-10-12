# Changelog

## 0.1.0 — 2026-10-12

First public release.

- `list`, `show`, `vars`, `node`, `diff`: compact views of Behaviour Trees, FSMs and Dialogue Trees in graph
  assets and in graphs bound to GraphOwners in prefabs and scenes.
- `refs`, `why`, `paths`, `stats`: reverse lookup of members, subtrees, variables and task types across all
  graphs; conditions on the way to a node; every call of a member or write of a variable.
- `lint`: missing types, dangling blackboard references, broken reflection bindings, subgraph mappings,
  connection limits, unreachable nodes, owner and global variables.
- `apply`: atomic, validated batches of edit operations with short value forms and `--dry-run` diffs;
  `new`, `extract`, `inline`, `copy`, `refactor rename-member|rename-type|retarget`, `scaffold`.
- `dt export` / `dt import` / `dt lint`: dialogue lines and translations to CSV/JSON and back.
- `owners`, `blackboards`, `types`, `members`.
- `verify --unity`: load the edited graphs in the open Unity Editor and report what NodeCanvas sees.
- `mcp`: stdio MCP server (`nodecanvas-cli[mcp]`, Python 3.10+).
- Claude Code plugin with a skill and a bundled launcher.
- Byte-exact round-trip of unchanged graphs; Unity's YAML folding and FullSerializer key order on save.
