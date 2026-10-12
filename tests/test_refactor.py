import json
from types import SimpleNamespace

from nodecanvas_cli.refactor import rename_member


def run(ncg, *a):
    return ncg("refactor", *a)


def test_rename_member(ncg, project):
    code, out, _ = run(ncg, "rename-member", "Bot.Attack", "Strike", "--dry-run")
    assert code == 0 and "Bot.Strike does not exist" in out and "dry run: 1 change(s) in 1 graph(s)" in out
    assert "-│ │ └─# @Self.Attack($Target)" in out and "+│ │ └─# @Self.Strike($Target)" in out
    cs = project / "Assets/Game/Bot.cs"
    cs.write_text(cs.read_text().replace("void Attack(", "void Strike("))
    _, out, _ = ncg("lint", "Bot_BT", "--json")
    assert any(i["code"] == "missing-member" for i in json.loads(out))
    assert run(ncg, "rename-member", "Bot.Attack", "Strike")[0] == 0
    _, out, _ = ncg("lint", "Bot_BT", "--json")
    assert not any(i["code"] == "missing-member" for i in json.loads(out))


def test_rename_member_overloads(ncg, project):
    ops = [
        {
            "op": "add",
            "parent": "2",
            "node": {
                "$type": "ActionNode",
                "_action": {"call": "Bot.MoveTo", "args": ["$Target", [0.0, 0.0, 1.0]], "agent": "$Self"},
            },
        }
    ]
    (project / "ops.json").write_text(json.dumps(ops))
    assert ncg("apply", "Bot_BT", project / "ops.json")[0] == 0
    asset = project / "Assets/Graphs/Bot_BT.asset"
    before = asset.read_text()
    # graphs first: nothing tells which overload is meant
    code, _, err = run(ncg, "rename-member", "Bot.MoveTo", "Go")
    assert (
        code == 1 and "graphs call 2 overloads of Bot.MoveTo: (Unit, Vector3), (Vector3)" in err and "--params" in err
    )
    code, _, err = run(ncg, "rename-member", "Bot.MoveTo", "Go", "--params", "Single")
    assert code == 1 and "no graph calls Bot.MoveTo(Single)" in err
    code, out, _ = run(ncg, "rename-member", "Bot.MoveTo", "Go", "--all-overloads", "--dry-run")
    assert code == 0 and "dry run: 2 change(s) in 1 graph(s)" in out
    code, out, _ = run(ncg, "rename-member", "Bot.MoveTo", "Go", "--params", "Unit,Vector3", "--dry-run")
    assert code == 0 and "dry run: 1 change(s) in 1 graph(s)" in out
    assert "+│ └─# @Self.Go($Target, (0, 0, 1.0))" in out and "MoveTo((1.0, 0, -2.5))" not in out
    assert asset.read_text() == before
    # C# first: the sources tell which overload got the new name
    cs = project / "Assets/Game/Bot.cs"
    cs.write_text(cs.read_text().replace("void MoveTo(Unit target", "void Go(Unit target"))
    code, out, _ = run(ncg, "rename-member", "Bot.MoveTo", "Go")
    assert code == 0, out
    assert "note: kept calls of Bot.MoveTo(Vector3): that overload is still named MoveTo in the sources" in out
    assert "saved 1 graph(s), 1 change(s)" in out
    tree = ncg("show", "Bot_BT")[1]
    assert "@Self.MoveTo((1.0, 0, -2.5))" in tree and "@Self.Go($Target, (0, 0, 1.0))" in tree
    _, out, _ = ncg("lint", "Bot_BT", "--json")
    assert not any(i["code"] == "missing-member" for i in json.loads(out))


def test_rename_property_accessor(ncg, project):
    assert run(ncg, "rename-member", "Demo.Game.Bot.IsDead", "Dead")[0] == 0
    assert "? @Self.Dead" in ncg("show", "Bot_BT")[1]


def test_rename_field(ncg, project):
    asset = project / "Assets/Graphs/Sub_BT.asset"
    before = asset.read_text()
    assert before.count("Demo.Game.Bot|speed") == 1
    code, out, _ = run(ncg, "rename-member", "Bot.speed", "moveSpeed", "--dry-run")
    assert code == 0 and "dry run: 1 change(s) in 1 graph(s)" in out
    assert '-└─# Bot.speed = 4.0 "Set speed"' in out and '+└─# Bot.moveSpeed = 4.0 "Set speed"' in out
    assert asset.read_text() == before
    assert run(ncg, "rename-member", "Bot.speed", "moveSpeed")[0] == 0
    assert asset.read_text() == before.replace("Demo.Game.Bot|speed", "Demo.Game.Bot|moveSpeed")
    _, out, _ = ncg("refs", "Bot.moveSpeed")
    assert "member Bot.moveSpeed" in out


def test_rename_member_reference_boundaries():
    data = {
        "field": {"_baseInfo": "Demo.Game.Bot|speed"},
        "method": {"_baseInfo": "Demo.Game.Bot|Heal|System.Void", "_paramsInfo": "System.Single"},
        "setter": {"_baseInfo": "Demo.Game.Bot|set_speed|System.Void"},
        "_recoveryState": '{"field":{"_baseInfo":"Demo.Game.Bot|speed"},'
        '"method":{"_baseInfo":"Demo.Game.Bot|Heal|System.Void","_paramsInfo":"System.Single"}}',
        "others": [
            {"_baseInfo": s}
            for s in (
                "Demo.Game.Bot|HealAll|System.Void",
                "Demo.Game.Bot|speedy",
                "My.Demo.Game.Bot|speed",
                "Demo.Game.BotFactory|speed",
                "My.Demo.Game.Bot|Heal|System.Void",
            )
        ],
    }
    g = SimpleNamespace(data=data, loaded=SimpleNamespace(slot=SimpleNamespace(address="g")))
    assert rename_member(None, [g], "Demo.Game.Bot.speed", "moveSpeed") == {"g": 3}
    assert rename_member(None, [g], "Demo.Game.Bot.Heal", "Cure") == {"g": 2}
    assert data["field"] == {"_baseInfo": "Demo.Game.Bot|moveSpeed"}
    assert data["method"] == {"_baseInfo": "Demo.Game.Bot|Cure|System.Void", "_paramsInfo": "System.Single"}
    assert data["setter"] == {"_baseInfo": "Demo.Game.Bot|set_moveSpeed|System.Void"}
    assert data["_recoveryState"] == (
        '{"field":{"_baseInfo":"Demo.Game.Bot|moveSpeed"},'
        '"method":{"_baseInfo":"Demo.Game.Bot|Cure|System.Void","_paramsInfo":"System.Single"}}'
    )
    assert [o["_baseInfo"] for o in data["others"]] == [
        "Demo.Game.Bot|HealAll|System.Void",
        "Demo.Game.Bot|speedy",
        "My.Demo.Game.Bot|speed",
        "Demo.Game.BotFactory|speed",
        "My.Demo.Game.Bot|Heal|System.Void",
    ]


def test_rename_type(ncg, project):
    cs = project / "Assets/Game/Bot.cs"
    cs.write_text(cs.read_text().replace("class Unit ", "class Creature "))
    code, out, _ = run(ncg, "rename-type", "Demo.Game.Unit", "Demo.Game.Creature", "--assembly", "Demo.Game")
    assert code == 0, out
    assert "+vars: Self:Bot(pub)  Target:Creature" in out
    raw = (project / "Assets/Graphs/Bot_BT.asset").read_text().replace("\n    ", " ")
    assert "Demo.Game.Unit" not in raw and "Demo.Game.Creature, Demo.Game, Version" in raw
    assert "Target:Creature" in ncg("show", "Bot_BT")[1]


def test_retarget(ncg, project):
    ops = [{"op": "var-add", "name": "Pos", "type": "Vector3"}] + [
        {
            "op": "add",
            "parent": "2",
            "node": {
                "$type": "ConditionNode",
                "_condition": {
                    "check": "Bot.DistanceTo",
                    "args": [arg],
                    "agent": "$Self",
                    "value": 1.0,
                    "compare": ">",
                },
            },
        }
        for arg in ("$Target", "$Pos")
    ]
    (project / "ops.json").write_text(json.dumps(ops))
    assert ncg("apply", "Bot_BT", project / "ops.json")[0] == 0
    code, out, _ = run(ncg, "retarget", "Bot.DistanceTo", "--to", "Vector3", "--from", "Unit")
    assert code == 0, out
    assert "skipped 1 call(s)" in out and "argument 1 is $Target (Unit), not Vector3" in out
    tree = ncg("show", "Bot_BT")[1]
    assert "DistanceTo‹Unit›($Target)" in tree and "DistanceTo‹Vector3›($Pos)" in tree
    code, out, _ = run(ncg, "retarget", "Bot.DistanceTo", "--to", "Vector3", "--all", "--dry-run", "--force")
    assert "DistanceTo‹Vector3›($Target)" in out and "$Target (Unit) but the parameter takes Vector3" in out
    code, _, err = run(ncg, "retarget", "Bot.DistanceTo", "--to", "Wall")
    assert code == 1 and "no overload (Wall)" in err


def test_rename_type_reaches_missing_wrappers(ncg, project):
    cs = project / "Assets/Game/Bot.cs"
    cs.write_text(cs.read_text().rstrip()[:-1] + "\n    public class Blink : NodeCanvas.Framework.ActionTask { }\n}\n")
    assert run(ncg, "rename-type", "Demo.Game.Teleport", "Demo.Game.Blink")[0] == 0
    _, out, _ = ncg("lint", "Broken_BT", "--json", "--min", "info")
    assert ("#6", "recoverable-missing") in {(i["at"], i["code"]) for i in json.loads(out)}
