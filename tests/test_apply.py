import json

from nodecanvas_cli.container import UnityFile
from nodecanvas_cli.project import Project, find_project_root


def apply(ncg, project, graph, ops, *flags):
    path = project / "ops.json"
    path.write_text(json.dumps(ops))
    return ncg("apply", graph, path, *flags)


def show(ncg, graph):
    return ncg("show", graph)[1]


def assert_roundtrip(project):
    proj = Project(find_project_root(project))
    for rel in proj.containers:
        f = UnityFile(proj.abs(rel), rel)
        original = list(f.lines)
        for slot in f.slots():
            f.store(f.load(slot))
        assert f.lines == original, rel


def test_set_add_wrap_move_remove(ncg, project):
    ops = [
        {"op": "set", "node": "4", "path": "coolDownTime", "value": 0.75},
        {
            "op": "add",
            "parent": "2",
            "index": 0,
            "id": "w",
            "node": {"$type": "ActionNode", "_action": {"$type": "Wait", "waitTime": "$Cool time"}},
        },
        {"op": "wrap", "node": "w", "with": {"$type": "Repeat", "repeatTimes": 3}},
        {"op": "move", "node": "1", "parent": "2", "index": 1},
        {"op": "remove", "node": "9"},
    ]
    code, out, err = apply(ncg, project, "Bot_BT", ops)
    assert code == 0, err
    tree = show(ncg, "Bot_BT")
    assert '└─#1 Sequencer "Fight"' in tree
    assert "  ├─#2 Repeat [repeatTimes=3]" in tree
    assert "  │ └─#3 wait $Cool time" in tree
    assert "  ├─#4 ? @Self.IsDead" in tree
    assert "Cooldown [coolDownTime=0.75]" in tree
    assert "$Speed *=" not in tree
    assert ncg("lint", "Bot_BT")[0] == 0
    assert_roundtrip(project)


def test_new_node_json_matches_fullserializer_layout(ncg, project):
    ops = [
        {
            "op": "add",
            "parent": "0",
            "node": {
                "$type": "ActionNode",
                "_action": {"$type": "SetFloat", "valueA": "$Speed", "Operation": "Add", "valueB": 1},
            },
        }
    ]
    assert apply(ncg, project, "Bot_BT", ops)[0] == 0
    _, out, _ = ncg("node", "Bot_BT", "10", "--raw")
    node = json.loads(out)["10"]["node"]
    assert list(node) == ["_action", "_position", "$type", "$id"]
    assert node["_action"] == {
        "valueA": {"_name": "Speed", "_targetVariableID": node["_action"]["valueA"]["_targetVariableID"]},
        "Operation": 1,
        "valueB": {"_value": 1.0},
        "$type": "NodeCanvas.Tasks.Actions.SetFloat",
    }
    siblings = [
        json.loads(ncg("node", "Bot_BT", i, "--raw")[1])[i]["node"]["_position"].get("x", 0.0)
        for i in ("1", "2", "9", "10")
    ]
    assert siblings == sorted(siblings)


def test_reflection_sugar(ncg, project):
    ops = [
        {
            "op": "add",
            "parent": "2",
            "node": {
                "$type": "ActionNode",
                "_action": {"call": "Bot.MoveTo", "args": ["$Target", [0, 1, 0]], "agent": "$Self"},
            },
        },
        {
            "op": "add",
            "parent": "2",
            "node": {
                "$type": "ConditionNode",
                "_condition": {
                    "check": "Bot.DistanceTo",
                    "args": ["$Target"],
                    "agent": "$Self",
                    "value": "$Speed",
                    "compare": ">",
                },
            },
        },
        {
            "op": "add",
            "parent": "2",
            "node": {"$type": "ConditionNode", "_condition": {"check": "Bot.IsDead", "agent": "$Self", "value": False}},
        },
    ]
    code, out, err = apply(ncg, project, "Bot_BT", ops)
    assert code == 0, err
    tree = show(ncg, "Bot_BT")
    assert "@Self.MoveTo($Target, (0, 1.0, 0))" in tree
    assert "? @Self.DistanceTo‹Unit›($Target) > $Speed" in tree  # several 1-arg overloads -> signature shown
    assert "? !@Self.IsDead" in tree
    assert ncg("lint", "Bot_BT")[0] == 0


def test_overload_errors_are_explained(ncg, project):
    ops = [
        {
            "op": "add",
            "parent": "2",
            "node": {"$type": "ActionNode", "_action": {"call": "Bot.MoveTo", "args": [], "agent": "$Self"}},
        }
    ]
    code, _, err = apply(ncg, project, "Bot_BT", ops)
    assert code == 1
    assert "MoveTo(Vector3); MoveTo(Unit, Vector3)" in err


def test_batch_is_atomic(ncg, project):
    before = (project / "Assets/Graphs/Bot_BT.asset").read_bytes()
    ops = [{"op": "set", "node": "4", "path": "coolDownTime", "value": 9}, {"op": "remove", "node": "999"}]
    code, _, err = apply(ncg, project, "Bot_BT", ops)
    assert code == 1 and "op #1 (remove)" in err
    assert (project / "Assets/Graphs/Bot_BT.asset").read_bytes() == before


def test_lint_errors_block_save(ncg, project):
    before = (project / "Assets/Graphs/Bot_BT.asset").read_bytes()
    ops = [
        {"op": "set", "node": "3", "path": "_condition.valueA", "value": {"_name": "Nope", "_targetVariableID": "x"}}
    ]
    code, out, err = apply(ncg, project, "Bot_BT", ops)
    assert code == 1 and "dangling-var" in out
    assert (project / "Assets/Graphs/Bot_BT.asset").read_bytes() == before


def test_dry_run_writes_nothing(ncg, project):
    before = (project / "Assets/Graphs/Bot_BT.asset").read_bytes()
    code, out, _ = apply(ncg, project, "Bot_BT", [{"op": "remove", "node": "9"}], "--dry-run")
    assert code == 0 and "dry run" in out
    assert (project / "Assets/Graphs/Bot_BT.asset").read_bytes() == before


def test_probability_weights_follow_children(ncg, project):
    ops = [
        {"op": "add", "parent": "6", "index": 1, "node": {"$type": "ActionNode", "_action": {"$type": "Wait"}}},
        {"op": "move", "node": "7", "parent": "2"},
    ]
    assert apply(ncg, project, "Bot_BT", ops)[0] == 0
    tree = show(ncg, "Bot_BT")
    assert "(w=1.0) #" in tree and "wait 1s" in tree
    assert "(w=3.0) #" in tree
    assert ncg("lint", "Bot_BT")[0] == 0


def test_unwrap_and_replace(ncg, project):
    ops = [{"op": "unwrap", "node": "4"}, {"op": "replace", "node": "2", "with": {"$type": "Selector", "random": True}}]
    assert apply(ncg, project, "Bot_BT", ops)[0] == 0
    tree = show(ncg, "Bot_BT")
    assert "Cooldown" not in tree
    assert 'Selector "Fight" [random=true]' in tree


def test_variables(ncg, project):
    ops = [
        {"op": "var-add", "name": "Range", "type": "float", "value": 4},
        {"op": "var-rename", "name": "Alert", "to": "Alarm"},
        {"op": "set", "node": "4", "path": "coolDownTime", "value": "$Range"},
    ]
    assert apply(ncg, project, "Bot_BT", ops)[0] == 0
    tree = show(ncg, "Bot_BT")
    assert "? !$Alarm" in tree and "coolDownTime=$Range" in tree
    assert "Range:float" in tree
    code, _, err = apply(ncg, project, "Bot_BT", [{"op": "var-remove", "name": "Range"}])
    assert code == 1 and "is used by #4" in err


def test_fsm_transitions(ncg, project):
    ops = [
        {"op": "add", "id": "flee", "node": {"$type": "NestedBTState", "_name": "Flee", "_nestedBT": "Sub_BT"}},
        {"op": "map", "node": "flee", "map": {"Self": "<-$Self", "Alert": "<->$Alert"}},
        {
            "op": "transition-add",
            "from": "1",
            "to": "flee",
            "index": 0,
            "condition": {"$type": "CheckBoolean", "valueA": "$Alert"},
        },
        {"op": "disable", "from": "0", "to": "1"},
        {"op": "set-start", "node": "1"},
    ]
    code, out, err = apply(ncg, project, "Bot_FSM", ops)
    assert code == 0, err
    tree = show(ncg, "Bot_FSM")
    assert "start → #0 Combat" in tree
    assert 'SubBehaviourTree "Flee" → Sub_BT {Self←$Self, Alert↔$Alert}' in tree
    assert "   --[$Alert]--> #1 Flee" in tree
    assert "   [off] --[$Alert]--> #0 Combat" in tree
    assert ncg("lint", "Bot_FSM")[0] == 0


def test_dialogue_choice(ncg, project):
    ops = [
        {
            "op": "add",
            "id": "bye",
            "node": {"$type": "StatementNode", "_actorName": "Guard", "statement": {"_text": "Farewell."}},
        },
        {"op": "choice-add", "node": "1", "text": "Bye.", "to": "bye"},
    ]
    code, _, err = apply(ncg, project, "Talk_DT", ops)
    assert code == 0, err
    tree = show(ncg, "Talk_DT")
    assert '3) "Bye." → #' in tree
    assert '[Guard] Say "Farewell."' in tree
    assert ncg("lint", "Talk_DT")[0] == 0


def test_bound_graph_in_prefab(ncg, project):
    ops = [{"op": "add", "parent": "0", "node": {"$type": "ActionNode", "_action": {"$type": "Wait", "waitTime": 1.5}}}]
    code, _, err = apply(ncg, project, "Sentry", ops)
    assert code == 0, err
    assert "wait 1.5s" in show(ncg, "Sentry")
    text = (project / "Assets/Prefabs/Sentry.prefab").read_text()
    assert "m_Name: Sentry" in text and "_boundGraphObjectReferences: []" in text
    assert_roundtrip(project)


def test_documented_extras(ncg, project):
    ops = [
        {
            "op": "set",
            "node": "3",
            "path": "_condition",
            "value": [
                {"$type": "CheckBoolean", "valueA": "$Alert"},
                {"check": "Bot.IsDead", "agent": "$Self", "value": False},
            ],
        },
        {"op": "unset", "node": "9", "path": "_comment"},
        {"op": "var-set", "name": "Speed", "value": 7},
        {"op": "subgraph-set", "node": "8", "graph": "Sub_BT", "map": {"Self": "<-$Self", "Alert": "->$Alert"}},
        {"op": "set", "node": "6", "path": "childWeights.1", "value": 5},
    ]
    code, _, err = apply(ncg, project, "Bot_BT", ops)
    assert code == 0, err
    tree = show(ncg, "Bot_BT")
    assert "? ($Alert && !@Self.IsDead)" in tree
    assert "// double the speed" not in tree
    assert "(w=5.0)" in tree
    assert "$Speed: float = 7.0" in ncg("vars", "Bot_BT")[1]
    fsm_ops = [
        {
            "op": "set",
            "from": "0",
            "to": "1",
            "path": "_condition",
            "value": {"$type": "CheckBoolean", "valueA": "$Alert", "valueB": False},
        }
    ]
    assert apply(ncg, project, "Bot_FSM", fsm_ops)[0] == 0
    assert "--[!$Alert]--> #1 Combat" in show(ncg, "Bot_FSM")


def positions(ncg, graph):
    from nodecanvas_cli.workspace import Workspace

    ws = Workspace()
    g = ws.graph(graph)
    return g, {g.nid(n): (n["_position"].get("x", 0.0), n["_position"].get("y", 0.0)) for n in g.nodes}


def test_arity_is_checked_after_the_whole_batch(ncg, project):
    ops = [
        {
            "op": "add",
            "parent": "2",
            "id": "b",
            "node": {"$type": "BinarySelector", "_condition": {"$type": "CheckBoolean", "valueA": "$Alert"}},
        },
        {"op": "add", "parent": "b", "node": {"$type": "ActionNode", "_action": {"$type": "Wait"}}},
        {"op": "add", "parent": "b", "node": {"$type": "ActionNode", "_action": {"$type": "Wait", "waitTime": 2}}},
        {"op": "move", "node": "1", "parent": "b"},
        {"op": "move", "node": "1", "parent": "0", "index": 0},
    ]
    code, _, err = apply(ncg, project, "Bot_BT", ops)
    assert code == 0, err
    code, _, err = apply(ncg, project, "Bot_BT", ops[:4])
    assert code == 1 and "accepts at most 2 child(ren), has 3" in err


def test_simplify_removes_redundant_merges(ncg, project):
    assert apply(ncg, project, "Bot_BT", [{"op": "wrap", "node": "5", "with": "Merge"}])[0] == 0
    _, out, _ = ncg("lint", "Bot_BT", "--min", "info")
    assert "redundant-merge" in out
    assert apply(ncg, project, "Bot_BT", [{"op": "simplify"}])[0] == 0
    tree = show(ncg, "Bot_BT")
    assert "Merge" not in tree and "│ │ └─#5 @Self.Attack($Target)" in tree


def test_grid_layout(ncg, project):
    _, before = positions(ncg, "Bot_BT")
    ops = [
        {
            "op": "add",
            "parent": "2",
            "index": 1,
            "id": "a",
            "node": {"$type": "ActionNode", "_action": {"$type": "Wait"}},
        },
        {"op": "add", "parent": "a", "node": {"$type": "ActionNode", "_action": {"$type": "Wait"}}}
        if False
        else {"op": "wrap", "node": "a", "with": "Cooldown"},
        {"op": "add", "parent": "0", "node": {"$type": "ActionNode", "_action": {"$type": "Wait", "waitTime": 4}}},
    ]
    assert apply(ncg, project, "Bot_BT", ops)[0] == 0
    g, after = positions(ncg, "Bot_BT")
    kids_of = lambda nid: [g.nid(k) for k in g.children(g.node(nid))]  # noqa: E731
    # ids after renumbering: 0 Selector, 1 cond, 2 Fight, 3 ? !$Alert, 4 Cooldown(new), 5 wait(new), 6 Cooldown, ...
    for nid in ("4", "5"):
        x, y = after[nid]
        assert x % 20 == 0 and y % 20 == 0
    assert after["4"][1] == after["2"][1] + 200
    assert after["5"][1] == after["4"][1] + 200
    for parent in ("0", "2"):
        xs = [after[k][0] for k in kids_of(parent)]
        assert xs == sorted(xs)
        assert all(b - a >= 300 for a, b in zip(xs, xs[1:])) or parent == "2" and xs[1] - xs[0] >= 300
    assert after["0"] == before["0"] and after["1"] == before["1"]
    rows = {}
    new_ids = {"4", "5", str(len(g.nodes) - 1)}
    for nid, (x, y) in after.items():
        rows.setdefault(y, []).append((x, nid))
    for y, items in rows.items():
        for x, nid in items:
            if nid in new_ids:
                assert all(abs(x - x2) >= 300 for x2, n2 in items if n2 != nid), (nid, items)


def test_layout_config(ncg, project):
    (project / "nodecanvas-cli.json").write_text('{"layout": {"level_gap": 100, "sibling_gap": 400}}')
    assert (
        apply(
            ncg,
            project,
            "Bot_BT",
            [{"op": "add", "parent": "6", "node": {"$type": "ActionNode", "_action": {"$type": "Wait"}}}],
        )[0]
        == 0
    )
    g, after = positions(ncg, "Bot_BT")
    new = g.nid(g.children(g.node("6"))[-1])
    assert after[new][1] == after["6"][1] + 100
    xs = [after[g.nid(k)][0] for k in g.children(g.node("6"))]
    assert xs[-1] - xs[-2] >= 400


def test_diff_against_git(ncg, project):
    import subprocess

    run = lambda *a: subprocess.run(["git", *a], cwd=project, check=True, capture_output=True)  # noqa: E731
    run("init", "-q")
    run("-c", "user.name=t", "-c", "user.email=t@t", "add", "-A")
    run("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "base")
    assert ncg("diff", "Bot_BT")[1].strip() == "(no changes)"
    assert apply(ncg, project, "Bot_BT", [{"op": "set", "node": "4", "path": "coolDownTime", "value": 9}])[0] == 0
    code, out, _ = ncg("diff", "Bot_BT")
    assert code == 0
    assert "-│ ├─# Cooldown [coolDownTime=$Cool time]" in out
    assert "+│ ├─# Cooldown [coolDownTime=9.0]" in out


def test_apply_reports_layout_shifts(ncg, project):
    ops = [{"op": "add", "parent": "2", "index": 1, "node": {"$type": "ActionNode", "_action": {"$type": "Wait"}}}]
    code, out, _ = apply(ncg, project, "Bot_BT", ops, "--dry-run")
    assert code == 0
    assert "layout: placed 1 new, moved 0 by the edit, shifted 5 existing to make room:" in out
    assert "#5 subtree (2 nodes) +400,0" in out and "#7 subtree (3 nodes) +500,0" in out
    code, out, _ = apply(
        ncg, project, "Bot_BT", [{"op": "set", "node": "4", "path": "coolDownTime", "value": 3}], "--dry-run"
    )
    assert "layout:" not in out


def test_layout_op_aligns_subtree_root_to_parent(ncg, project):
    assert apply(ncg, project, "Bot_BT", [{"op": "layout", "node": "6"}])[0] == 0
    g, after = positions(ncg, "Bot_BT")
    assert after["6"][1] == after["2"][1] + 200
    assert after["7"][1] == after["8"][1] == after["6"][1] + 200
    assert all(v % 20 == 0 for xy in after.values() for v in xy)


MERGE_OPS = [
    {"op": "var-add", "name": "Targets", "type": "List<Unit>"},
    {"op": "add", "parent": "6", "id": "m", "node": {"$type": "Merge"}},
    {"op": "connect", "from": "4", "to": "m"},
    {"op": "remove", "node": "5"},
    {
        "op": "add",
        "parent": "m",
        "id": "it",
        "node": {"$type": "Iterator", "targetList": "$Targets", "current": "$Target"},
    },
    {"op": "add", "parent": "it", "node": {"$type": "ActionNode", "_action": {"$type": "Wait"}}},
]


def test_merge_with_two_parents_gets_a_slot(ncg, project):
    _, before = positions(ncg, "Bot_BT")
    code, _, err = apply(ncg, project, "Bot_BT", MERGE_OPS, "--force")
    assert code == 0, err
    g, after = positions(ncg, "Bot_BT")
    # ids after renumbering: 4 Cooldown, 5 Merge, 6 Iterator, 7 wait, 8 ProbabilitySelector, 9 MoveTo, 10 SubTree
    assert after["5"][1] == max(after["4"][1], after["8"][1]) + 200
    assert after["6"][1] == after["5"][1] + 200 and after["7"][1] == after["6"][1] + 200
    xs = [after[g.nid(k)][0] for k in g.children(g.node("8"))]
    assert xs == sorted(xs) and xs[-1] - xs[-2] == 300  # Merge is the last child of the ProbabilitySelector
    assert all(v % 20 == 0 for nid in ("5", "6", "7") for v in after[nid])
    assert after["8"] == before["6"] and after["4"] == before["4"]
    assert all(abs(x) < 2000 for x, _ in after.values()), after


def test_separate_terminates_with_shared_children(ncg, project, monkeypatch):
    # Slot the Merge under its first parent (#4): resolving the overlap with #6's children must not
    # shift #6's branch together with the Merge, which is #6's child too.
    from nodecanvas_cli.layout import Layouter

    monkeypatch.setattr(Layouter, "slot_parent", lambda self, n, positioned: self.parent(n))
    code, _, err = apply(ncg, project, "Bot_BT", MERGE_OPS, "--force")
    assert code == 0, err
    _, after = positions(ncg, "Bot_BT")
    assert all(abs(x) < 2000 for x, _ in after.values()), after
    row = [(x, nid) for nid, (x, y) in after.items() if nid != "5" and abs(y - after["5"][1]) < 100]
    assert all(abs(x - after["5"][0]) >= 300 for x, _ in row), (after["5"], row)


def raw_node(ncg, graph, nid):
    out = ncg("node", graph, nid, "--raw")[1]
    return json.loads(out[out.index("{") :])[nid]["node"]


def test_field_sugar(ncg, project):
    specs = [
        ("ActionNode", "_action", {"set": "Bot.speed", "agent": "$Self", "value": "$Speed"}),
        ("ActionNode", "_action", {"get": "Bot.speed", "agent": "$Self", "returns": "$Speed"}),
        ("ConditionNode", "_condition", {"check": "Bot.speed", "value": 2.5, "compare": ">"}),
        ("ActionNode", "_action", {"set": "Bot.Count", "value": 3}),
    ]
    ops = [{"op": "add", "parent": "2", "node": {"$type": t, key: spec}} for t, key, spec in specs]
    code, _, err = apply(ncg, project, "Bot_BT", ops)
    assert code == 0, err
    tree = show(ncg, "Bot_BT")
    for line in ("#9 @Self.speed = $Speed", "#10 $Speed = @Self.speed", "#11 ? Bot.speed > 2.5", "#12 Bot.Count = 3"):
        assert line in tree
    for nid, (t, key, spec) in zip(("9", "10", "11", "12"), specs):
        assert ncg("node", "Bot_BT", nid)[1].startswith(f"#{nid} " + json.dumps({"$type": t, key: spec}))
    speed = "Demo.Game.Bot|speed"
    assert raw_node(ncg, "Bot_BT", "9")["_action"] == {
        "field": {"_baseInfo": speed},
        "setValue": {
            "_type": "System.Single",
            "_name": "Speed",
            "_targetVariableID": raw_node(ncg, "Bot_BT", "9")["_action"]["setValue"]["_targetVariableID"],
        },
        "overrideAgent": raw_node(ncg, "Bot_BT", "9")["_action"]["overrideAgent"],
        "$type": "NodeCanvas.Tasks.Actions.SetField",
        "$version": 1,
    }
    assert list(raw_node(ncg, "Bot_BT", "11")["_condition"]) == [
        "checkValue",
        "comparison",
        "field",
        "$type",
        "$version",
    ]
    assert raw_node(ncg, "Bot_BT", "12")["_action"] == {
        "field": {"_baseInfo": "Demo.Game.Bot|Count"},
        "setValue": {"_type": "System.Int32", "_value": {"$content": 3, "$type": "System.Int32"}},
        "$type": "NodeCanvas.Tasks.Actions.SetField",
        "$version": 1,
    }
    assert ncg("lint", "Bot_BT")[0] == 0
    assert_roundtrip(project)


def test_field_sugar_errors_and_legacy_layout(ncg, project):
    for spec, msg in (
        ({"call": "Bot.speed"}, "is a field; use"),
        ({"set": "Bot.Attack", "value": 1}, "is not a field"),
        ({"set": "Bot.Count", "value": 1, "agent": "$Self"}, "is static and takes no agent"),
        ({"get": "Bot.speed", "args": [1]}, "is a field and takes no args"),
    ):
        code, _, err = apply(
            ncg, project, "Bot_BT", [{"op": "add", "parent": "2", "node": {"$type": "ActionNode", "_action": spec}}]
        )
        assert code == 1 and msg in err, (spec, err)
    # without "$version" the task is in NodeCanvas' pre-migration layout: keep it as it is
    legacy = {
        "field": {"_baseInfo": "Demo.Game.Bot|speed"},
        "setValue": {"_type": "System.Single"},
        "$type": "NodeCanvas.Tasks.Actions.SetField",
    }
    ops = [{"op": "add", "parent": "2", "node": {"$type": "ActionNode", "_action": {"raw": legacy}}}]
    assert apply(ncg, project, "Bot_BT", ops)[0] == 0
    out = ncg("node", "Bot_BT", "9")[1]
    assert '"$type": "SetField", "field": {"raw": ' in out and '"set"' not in out


def assert_spec_rebuilds(ncg, project, graph, nid, key):
    """The spec ``node`` prints for #nid, added again, gives the same task JSON."""
    out = ncg("node", graph, nid)[1]
    spec = json.loads(out[len(f"#{nid} ") : out.index("\n")])
    before = raw_node(ncg, graph, nid)[key]
    assert apply(ncg, project, graph, [{"op": "add", "parent": "0", "id": "x", "node": spec}])[0] == 0
    count = int(show(ncg, graph).split("nodes=")[1].split()[0])
    assert raw_node(ncg, graph, str(count - 1))[key] == before
    return spec


def test_set_property(ncg, project):
    ops = [
        {"op": "add", "parent": "2", "node": {"$type": "ActionNode", "_action": spec}}
        for spec in ({"set": "Bot.Armor", "agent": "$Self", "value": "$Speed"}, {"set": "Bot.Armor", "value": 1.5})
    ]
    code, _, err = apply(ncg, project, "Bot_BT", ops)
    assert code == 0, err
    tree = show(ncg, "Bot_BT")
    assert "#9 @Self.Armor = $Speed" in tree and "#10 Bot.Armor = 1.5" in tree
    assert raw_node(ncg, "Bot_BT", "10")["_action"] == {
        "method": {"_baseInfo": "Demo.Game.Bot|set_Armor|System.Void", "_paramsInfo": "System.Single"},
        "parameter": {"_type": "System.Single", "_value": {"$content": 1.5, "$type": "System.Single"}},
        "$type": "NodeCanvas.Tasks.Actions.SetProperty_Multiplatform",
    }
    assert assert_spec_rebuilds(ncg, project, "Bot_BT", "9", "_action") == {
        "$type": "ActionNode",
        "_action": {"set": "Bot.Armor", "agent": "$Self", "value": "$Speed"},
    }
    assert assert_spec_rebuilds(ncg, project, "Bot_BT", "10", "_action")["_action"] == {
        "set": "Bot.Armor",
        "value": 1.5,
    }
    assert ncg("lint", "Bot_BT")[0] == 0
    for spec, msg in (
        ({"set": "Bot.IsDead", "value": True}, "Bot.IsDead has no public setter"),  # private set
        ({"set": "Bot.Stamina", "value": 1}, "Bot.Stamina has no public setter"),  # => 1f
        ({"set": "Bot.Armor", "args": [1]}, "is a property and takes no args"),
        ({"set": "Bot.Attack", "value": 1}, "Bot.Attack is not a field or property"),
    ):
        code, _, err = apply(
            ncg, project, "Bot_BT", [{"op": "add", "parent": "2", "node": {"$type": "ActionNode", "_action": spec}}]
        )
        assert code == 1 and msg in err, (spec, err)


def test_external_field(ncg, project):
    ops = [
        {
            "op": "add",
            "parent": "2",
            "node": {
                "$type": "ActionNode",
                "_action": {"get": "System.Single.PositiveInfinity", "fieldType": "float", "returns": "$Speed"},
            },
        },
        {
            "op": "add",
            "parent": "2",
            "node": {
                "$type": "ConditionNode",
                "_condition": {"check": "System.Int32.MaxValue", "fieldType": "int", "value": 5, "compare": ">"},
            },
        },
    ]
    code, _, err = apply(ncg, project, "Bot_BT", ops)
    assert code == 0, err
    tree = show(ncg, "Bot_BT")
    assert "#9 $Speed = Single.PositiveInfinity" in tree and "#10 ? Int32.MaxValue > 5" in tree
    speed = raw_node(ncg, "Bot_BT", "9")["_action"]["saveAs"]["_targetVariableID"]
    # the layout NodeCanvas writes (seen in a real project)
    assert raw_node(ncg, "Bot_BT", "9")["_action"] == {
        "field": {"_baseInfo": "System.Single|PositiveInfinity"},
        "saveAs": {"_type": "System.Single", "_name": "Speed", "_targetVariableID": speed},
        "$type": "NodeCanvas.Tasks.Actions.GetField",
        "$version": 1,
    }
    assert assert_spec_rebuilds(ncg, project, "Bot_BT", "9", "_action")["_action"] == {
        "get": "System.Single.PositiveInfinity",
        "fieldType": "float",
        "returns": "$Speed",
    }
    assert assert_spec_rebuilds(ncg, project, "Bot_BT", "10", "_condition")["_condition"] == {
        "check": "System.Int32.MaxValue",
        "fieldType": "int",
        "value": 5,
        "compare": ">",
    }
    assert ncg("lint", "Bot_BT")[0] == 0
    for spec, msg in (
        ({"get": "Mathf.PI", "fieldType": "float"}, "use its full name"),
        ({"get": "Bot.speed", "fieldType": "float"}, "fieldType is only for fields of types outside"),
        ({"get": "System.Single.PositiveInfinity"}, 'give "fieldType"'),
        ({"call": "System.Single.PositiveInfinity", "fieldType": "float"}, "is a field; use"),
    ):
        code, _, err = apply(
            ncg, project, "Bot_BT", [{"op": "add", "parent": "2", "node": {"$type": "ActionNode", "_action": spec}}]
        )
        assert code == 1 and msg in err, (spec, err)
