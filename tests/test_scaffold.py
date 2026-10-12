import json


def test_scaffold_action_is_usable_right_away(ncg, project):
    code, out, err = ncg(
        "scaffold",
        "action",
        "Patrol",
        "--dir",
        "Assets/Game/Tasks",
        "--agent",
        "Bot",
        "--field",
        "speed:BBParameter<float>=1.5",
        "--field",
        "label:string=hi",
        "--category",
        "AI/Move",
    )
    assert code == 0, err
    text = (project / "Assets/Game/Tasks/Patrol.cs").read_text()
    assert "namespace Demo.Game.Tasks" in text  # parent folder's namespace + sub-folder
    assert "public class Patrol : ActionTask<Bot>" in text and "using Demo.Game;" in text
    assert "public BBParameter<float> speed = 1.5f;" in text and 'public string label = "hi";' in text
    assert '[Category("AI/Move")]' in text and "EndAction(true);" in text and "// TODO:" in text
    assert not (project / "Assets/Game/Tasks/Patrol.cs.meta").exists()
    _, out, _ = ncg("types", "Patrol")
    assert "role=action" in out and "speed: BBParameter<float> = 1.5f" in out
    ops = [
        {"op": "add", "parent": "2", "node": {"$type": "ActionNode", "_action": {"$type": "Patrol", "speed": "$Speed"}}}
    ]
    (project / "ops.json").write_text(json.dumps(ops))
    code, _, err = ncg("apply", "Bot_BT", project / "ops.json")
    assert code == 0, err
    code, out, _ = ncg("lint", "Bot_BT")
    assert code == 0 and "0 errors, 0 warnings" in out


def test_scaffold_kinds_and_style(ncg, project):
    (project / "nodecanvas-cli.json").write_text('{"scaffold": {"comments": false}}')
    code, out, _ = ncg("scaffold", "condition", "IsHungry", "--dir", "Assets/Game", "--dry-run")
    assert code == 0 and "return true;" in out and "TODO" not in out and "public class IsHungry : ConditionTask" in out
    assert "dry run: would write Assets/Game/IsHungry.cs" in out
    assert not (project / "Assets/Game/IsHungry.cs").exists()
    _, out, _ = ncg("scaffold", "decorator", "Gate", "--dir", "Assets/Game", "--dry-run")
    assert "public class Gate : BTDecorator" in out and "decoratedConnection.Execute(agent, blackboard)" in out
    _, out, _ = ncg("scaffold", "composite", "Chain", "--dir", "Assets/Game", "--namespace", "My.Nodes", "--dry-run")
    assert "namespace My.Nodes" in out and "outConnections[i].Execute" in out
    assert ncg("types", "Chain")[1].count("Chain") == 0  # dry run wrote nothing


def test_scaffold_refuses_bad_targets(ncg, project):
    code, _, err = ncg("scaffold", "action", "Bot", "--dir", "Assets/Game")
    assert code == 1 and "already exists" in err
    code, _, err = ncg("scaffold", "action", "X", "--dir", "ProjectSettings")
    assert code == 1 and "inside Assets/" in err
    code, _, err = ncg("scaffold", "action", "1bad", "--dir", "Assets/Game")
    assert code == 1 and "not a valid C# class name" in err
    code, _, err = ncg("scaffold", "action", "Y", "--dir", "Assets/Game", "--field", "oops")
    assert code == 1 and "--field" in err
    code, _, err = ncg("scaffold", "action", "Y", "--dir", "Assets/Game", "--field", "name:Type\nOther=x")
    assert code == 1 and "--field" in err
