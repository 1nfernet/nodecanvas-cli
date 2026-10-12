import json


def codes(out):
    return {(i["at"], i["code"]) for i in json.loads(out)}


def test_broken_graph(ncg):
    code, out, _ = ncg("lint", "Broken_BT", "--json", "--min", "info")
    assert code == 1
    found = codes(out)
    assert ("#1", "missing-member") in found
    assert ("#2", "dangling-var") in found
    assert ("#3", "too-many-children") in found
    assert ("#6", "missing-type") in found
    assert ("#6", "unreachable") in found


def test_valid_graphs_are_clean(ncg):
    code, out, _ = ncg("lint", "Bot_BT", "Sub_BT", "Bot_FSM", "Talk_DT", "Sentry", "--json")
    assert code == 0
    assert json.loads(out) == []


def test_missing_member_is_an_error_when_the_chain_is_known(ncg, project):
    cs = project / "Assets/Game/Bot.cs"
    cs.write_text(cs.read_text().replace("public class Bot : MonoBehaviour", "public class Bot"))
    _, out, _ = ncg("lint", "Broken_BT", "--json")
    issues = {(i["code"], i["severity"]) for i in json.loads(out)}
    assert ("missing-member", "error") in issues


def test_missing_type_that_exists_again_is_recoverable(ncg, project):
    cs = project / "Assets/Game/Bot.cs"
    cs.write_text(
        cs.read_text().rstrip()[:-1]
        + "\n    public class Teleport : NodeCanvas.Framework.ActionTask { public float range = 5f; }\n}\n"
    )
    _, out, _ = ncg("lint", "Broken_BT", "--json", "--min", "info")
    issues = {(i["at"], i["code"], i["severity"]) for i in json.loads(out)}
    assert ("#6", "recoverable-missing", "info") in issues
    assert not any(code == "missing-type" for _, code, _ in issues)
    assert "#6 Teleport (recovered)" in ncg("show", "Broken_BT")[1]


def test_types_explains_non_task_types(ncg):
    _, out, _ = ncg("types", "Unit")
    assert "Demo.Game.Unit: class : MonoBehaviour" in out and "not a task or node type" in out


def test_layout_checks(ncg, project):
    ops = [
        {"op": "set", "node": "3", "path": "_position", "value": [230, 285]},
        {"op": "set", "node": "7", "path": "_position", "value": [310, 420]},
    ]
    (project / "ops.json").write_text(json.dumps(ops))
    assert ncg("apply", "Bot_BT", project / "ops.json", "--force")[0] == 0
    _, out, _ = ncg("lint", "Bot_BT", "--json", "--min", "info")
    found = {(i["at"], i["code"]) for i in json.loads(out)}
    assert ("#3", "off-grid") in found
    assert ("#2", "child-order") in found
    assert ("#6", "overlap") in found  # #6 ProbabilitySelector at (200,280) vs #3 moved to (230,285)
    assert ("#6", "child-order") in found  # #7 moved right of #8
    _, out, _ = ncg("lint", "Sub_BT", "Bot_FSM", "Talk_DT", "Sentry", "--json", "--min", "info")
    assert not [i for i in json.loads(out) if i["code"] in ("off-grid", "child-order", "overlap")]


def _apply(ncg, project, graph, ops, *flags):
    (project / "ops.json").write_text(json.dumps(ops))
    return ncg("apply", graph, project / "ops.json", *flags)


def test_overload_change_is_visible_and_fixable(ncg, project):
    import subprocess

    def run(*a):
        return subprocess.run(
            ["git", "-c", "user.name=t", "-c", "user.email=t@t", *a], cwd=project, check=True, capture_output=True
        )

    ops = [
        {
            "op": "add",
            "parent": "2",
            "node": {
                "$type": "ConditionNode",
                "_condition": {
                    "check": "Bot.DistanceTo",
                    "args": ["$Target"],
                    "agent": "$Self",
                    "value": 1.0,
                    "compare": ">",
                },
            },
        }
    ]
    assert _apply(ncg, project, "Bot_BT", ops)[0] == 0
    run("init", "-q")
    run("add", "-A")
    run("commit", "-qm", "base")
    # bind the Vector3 overload while keeping the Unit variable: a stale binding
    _, out, _ = ncg("refs", "Bot.DistanceTo", "--json")
    nid = json.loads(out)[0]["at"].lstrip("#")
    _, out, _ = ncg("node", "Bot_BT", nid, "--raw")
    cond = json.loads(out)[nid]["node"]["_condition"]
    cond["method"]["_paramsInfo"] = "UnityEngine.Vector3"
    assert (
        _apply(ncg, project, "Bot_BT", [{"op": "set", "node": nid, "path": "_condition", "raw": True, "value": cond}])[
            0
        ]
        == 0
    )
    _, out, _ = ncg("diff", "Bot_BT")
    assert "DistanceTo‹Unit›" in out and "DistanceTo‹Vector3›" in out
    _, out, _ = ncg("lint", "Bot_BT", "--json")
    issue = next(i for i in json.loads(out) if i["code"] == "arg-type-mismatch")
    assert "overload DistanceTo(Unit) fits the arguments" in issue["message"]
    assert issue["fix"]["value"]["overload"] == ["Unit"]
    assert _apply(ncg, project, "Bot_BT", [issue["fix"]])[0] == 0
    _, out, _ = ncg("lint", "Bot_BT", "--json")
    assert not [i for i in json.loads(out) if i["code"] == "arg-type-mismatch"]
    assert ncg("diff", "Bot_BT")[1].strip() == "(no changes)"


def test_null_object_argument(ncg, project):
    _, out, _ = ncg("node", "Bot_BT", "5", "--raw")
    action = json.loads(out)["5"]["node"]["_action"]
    action["parameters"] = [{"_type": "Demo.Game.Unit"}]
    code, out, _ = _apply(
        ncg, project, "Bot_BT", [{"op": "set", "node": "5", "path": "_action", "raw": True, "value": action}]
    )
    assert code == 0 and "warning: lost binding: #5 Bot.Attack argument 1: $Target -> empty" in out
    code, out, _ = ncg("lint", "Bot_BT", "--json", "--min", "info")
    issue = next(i for i in json.loads(out) if i["code"] == "null-arg")
    assert issue["severity"] == "info" and "did you mean $Target?" in issue["message"]
    assert _apply(ncg, project, "Bot_BT", [issue["fix"]])[0] == 0
    assert "@Self.Attack($Target)" in ncg("show", "Bot_BT")[1]
    # without a fitting variable it is only info
    assert (
        _apply(
            ncg,
            project,
            "Bot_BT",
            [
                {"op": "set", "node": "5", "path": "_action", "raw": True, "value": action},
                {"op": "var-remove", "name": "Target", "force": True},
            ],
            "--force",
        )[0]
        == 0
    )
    _, out, _ = ncg("lint", "Bot_BT", "--json", "--min", "info")
    assert any(i["code"] == "null-arg" and i["severity"] == "info" for i in json.loads(out))


def test_diff_reports_lost_bindings(ncg, project):
    import subprocess

    def run(*a):
        return subprocess.run(
            ["git", "-c", "user.name=t", "-c", "user.email=t@t", *a], cwd=project, check=True, capture_output=True
        )

    run("init", "-q")
    run("add", "-A")
    run("commit", "-qm", "base")
    _, out, _ = ncg("node", "Bot_BT", "5", "--raw")
    action = json.loads(out)["5"]["node"]["_action"]
    action["parameters"] = [{"_type": "Demo.Game.Unit"}]
    assert (
        _apply(ncg, project, "Bot_BT", [{"op": "set", "node": "5", "path": "_action", "raw": True, "value": action}])[0]
        == 0
    )
    _, out, _ = ncg("diff", "Bot_BT")
    assert "warning: lost binding: Bot.Attack argument 1: 1 call(s) lost their variable (was $Target)" in out


def test_variable_contracts(ncg, project):
    ops = [
        {"op": "var-add", "name": "Spare", "type": "float"},
        {"op": "set", "node": "8", "path": "_variablesMap", "raw": True, "value": []},
    ]
    assert _apply(ncg, project, "Bot_BT", ops)[0] == 0
    _, out, _ = ncg("lint", "Bot_BT", "--json", "--min", "info")
    found = {(i["at"], i["code"]) for i in json.loads(out)}
    assert ("bb", "unused-var") in found
    sub_ops = [
        {
            "op": "add",
            "parent": "0",
            "node": {"$type": "ActionNode", "_action": {"call": "Bot.Attack", "args": ["$Victim"], "agent": "$Self"}},
        },
        {"op": "var-add", "name": "Victim", "type": "Unit", "public": True},
    ]
    (project / "ops.json").write_text(json.dumps(sub_ops[::-1]))
    assert ncg("apply", "Sub_BT", project / "ops.json")[0] == 0
    _, out, _ = ncg("lint", "Bot_BT", "--json", "--min", "info")
    msgs = [i["message"] for i in json.loads(out) if i["code"] == "unmapped-subgraph-var"]
    assert sorted(msgs) == [
        "Sub_BT.$Self (Bot) is read inside but not mapped, so it is null there",
        "Sub_BT.$Victim (Unit) is read inside but not mapped, so it is null there",
    ]
    code, out, _ = _apply(ncg, project, "Bot_BT", [{"op": "map", "node": "8", "auto": True}])
    assert (
        code == 0
        and "map auto #8: mapped Self←$Self, Alert→$Alert" in out
        and "no matching parent variable for $Victim" in out
    )
    assert "SubTree → Sub_BT {Self←$Self, Alert→$Alert}" in ncg("show", "Bot_BT")[1]


def test_mapping_type_mismatch(ncg, project):
    _, out, _ = ncg("node", "Bot_BT", "8", "--raw")
    vmap = json.loads(out)["8"]["node"]["_variablesMap"]
    speed = json.loads(ncg("node", "Bot_BT", "9", "--raw")[1])["9"]["node"]["_action"]["valueA"]
    vmap[1]["_name"], vmap[1]["_targetVariableID"] = speed["_name"], speed["_targetVariableID"]
    assert (
        _apply(
            ncg, project, "Bot_BT", [{"op": "set", "node": "8", "path": "_variablesMap", "raw": True, "value": vmap}]
        )[0]
        == 0
    )
    _, out, _ = ncg("lint", "Bot_BT", "--json")
    assert any(
        i["code"] == "mapping-type-mismatch" and "Sub_BT.$Alert (bool) is mapped to $Speed (float)" in i["message"]
        for i in json.loads(out)
    )


def test_null_arg_prefers_the_enclosing_iterator(ncg, project):
    ops = [
        {"op": "var-add", "name": "Units", "type": "List<Unit>"},
        {"op": "var-add", "name": "Item", "type": "Unit"},
        {
            "op": "add",
            "parent": "2",
            "id": "it",
            "node": {"$type": "Iterate", "targetList": "$Units", "current": "$Item"},
        },
        {
            "op": "add",
            "parent": "it",
            "id": "act",
            "node": {"$type": "ActionNode", "_action": {"call": "Bot.Attack", "args": ["$Target"], "agent": "$Self"}},
        },
    ]
    assert _apply(ncg, project, "Bot_BT", ops)[0] == 0
    _, out, _ = ncg("refs", "Bot.Attack", "--json")
    nid = max((h["at"] for h in json.loads(out)), key=lambda a: int(a[1:])).lstrip("#")
    action = json.loads(ncg("node", "Bot_BT", nid, "--raw")[1])[nid]["node"]["_action"]
    action["parameters"] = [{"_type": "Demo.Game.Unit"}]
    assert (
        _apply(ncg, project, "Bot_BT", [{"op": "set", "node": nid, "path": "_action", "raw": True, "value": action}])[0]
        == 0
    )
    _, out, _ = ncg("lint", "Bot_BT", "--json", "--min", "info")
    issue = next(i for i in json.loads(out) if i["code"] == "null-arg" and i["at"] == f"#{nid}")
    assert "did you mean $Item, $Target?" in issue["message"]
    assert issue["fix"]["value"]["_name"] == "Item"


def test_missing_member_on_unity_subclass_is_an_error(ncg, project):
    _, out, _ = ncg("lint", "Broken_BT", "--json")
    issue = next(i for i in json.loads(out) if i["code"] == "missing-member")
    assert issue["severity"] == "error" and "Bot.Fly" in issue["message"]


def test_allow_outside(ncg, project):
    code, _, err = ncg("new", "Scratch/Tmp_BT.asset", "--kind", "bt")
    assert code == 1 and "--allow-outside" in err
    code, out, err = ncg("new", "Scratch/Tmp_BT.asset", "--kind", "bt", "--allow-outside")
    assert f"created {project.resolve() / 'Scratch/Tmp_BT.asset'}" in out
    assert code == 0 and "Unity will not import it" in err
    assert (project / "Scratch/Tmp_BT.asset.meta").exists()
