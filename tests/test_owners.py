import json


def test_owners_of_graph(ncg):
    code, out, _ = ncg("owners", "Patrol_BT")
    assert code == 0
    # GuardOwner is a project class deriving from BehaviourTreeOwner: found through the schema
    assert "Assets/Prefabs/Guard.prefab:Guard  GuardOwner" in out
    assert "bb: Blackboard: $Home dist; parent AssetBlackboard Assets/Blackboards/Shared.asset: $Reach" in out
    assert "$Patience = 3  (int)" in out and "$Spare = 1.5  (float)" in out
    assert "= Guard (GuardOwner)" in out  # object override resolved to a local component


def test_owners_prefab_instance_and_subgraph(ncg):
    _, out, _ = ncg("owners", "Bot_FSM")
    assert "Assets/Scenes/World.unity  PrefabInstance of Guard overrides _graph" in out
    _, out, _ = ncg("owners", "Sub_BT")
    assert "subgraph of: Bot_BT #8, Bot_FSM #0" in out
    _, out, _ = ncg("owners", "Talk_DT")
    assert "not assigned to any owner or parent graph" in out
    _, out, _ = ncg("owners", "Sentry")
    assert "BehaviourTreeOwner  (bound graph)" in out


def test_owners_all(ncg):
    code, out, _ = ncg("owners", "--all")
    assert code == 0
    lines = {line.split()[0]: line for line in out.splitlines()}
    assert "Guard.prefab:Guard (GuardOwner)" in lines["Patrol_BT"]
    assert "not assigned in assets" in lines["Talk_DT"]
    assert "subgraph of Bot_FSM #1" in lines["Bot_BT"]
    assert "World.unity:Runner (FSMOwner) has no graph assigned" in out
    data = json.loads(ncg("owners", "--all", "--json")[1])
    assert {g["graph"] for g in data["graphs"] if g["unassigned"]} == {
        "Assets/Graphs/Broken_BT.asset",
        "Assets/Graphs/Talk_DT.asset",
    }


def test_blackboards(ncg):
    _, out, _ = ncg("blackboards")
    assert (
        "Assets/Prefabs/Guard.prefab:Guard  Blackboard  parent=Assets/Blackboards/Shared.asset\n"
        "  $Home dist: float = 4.0" in out
    )
    assert 'Assets/Scenes/World.unity:Globals  GlobalBlackboard  identifier="Global"\n  $Alarm: bool' in out
    assert "Assets/Blackboards/Shared.asset  AssetBlackboard\n  $Reach: float = 2.5" in out
    _, out, _ = ncg("blackboards", "--global")
    assert "Guard.prefab" not in out and "Globals" in out
    data = json.loads(ncg("blackboards", "--json")[1])
    assert any(b["kind"] == "global" and "Alarm" in b["variables"] for b in data)


def test_vars_and_show_external_sources(ncg):
    _, out, _ = ncg("vars", "Patrol_BT")
    assert "$Global/Alarm: bool  global: Assets/Scenes/World.unity:Globals  used: #1" in out
    assert "$Home dist: float  owner: Assets/Prefabs/Guard.prefab:Guard (Blackboard)  used: #2" in out
    assert "$Reach: float  owner: Assets/Blackboards/Shared.asset (AssetBlackboard)  used: #4" in out
    assert '$World/Panic: ?  MISSING (no GlobalBlackboard "World" in the project)' in out
    assert "$Patience overridden by Assets/Prefabs/Guard.prefab:Guard = 3" in out
    _, out, _ = ncg("show", "Patrol_BT")
    assert "external: $Global/Alarm(global)  $Home dist(owner)  $World/Panic(missing)  $Reach(owner)" in out
    assert "#2 $Home dist = $Patience" in out and "#3 $World/Panic! = true" in out


def test_lint_owner_checks(ncg):
    _, out, _ = ncg("lint", "Patrol_BT", "--min", "info", "--json")
    codes = {(i["at"], i["code"]) for i in json.loads(out)}
    assert ("#1", "global-var") in codes and ("#2", "owner-var") in codes and ("#4", "owner-var") in codes
    assert ("#3", "missing-global-var") in codes
    assert ("owner Assets/Prefabs/Guard.prefab:Guard", "exposed-override-type") in codes
    assert ("owner Assets/Prefabs/Guard.prefab:Guard", "exposed-override-stale") in codes
    assert not any(c == "unused-var" for _, c in codes)  # $Spare is overridden by the owner
    assert not any(c == "dangling-var" for _, c in codes)
    _, out, _ = ncg("lint")
    assert "owner-missing-graph  FSMOwner has no graph assigned" in out
    _, out, _ = ncg("lint", "Broken_BT")
    assert "dangling-var" in out and "owner-missing-graph" not in out


def test_apply_uses_external_variables(ncg, project):
    ops = [
        {
            "op": "add",
            "parent": "0",
            "node": {"$type": "ActionNode", "_action": {"$type": "SetFloat", "valueA": "$Reach", "valueB": 3}},
        },
        {
            "op": "add",
            "parent": "0",
            "node": {"$type": "ConditionNode", "_condition": {"$type": "CheckBoolean", "valueA": "$Global/Alarm"}},
        },
    ]
    (project / "ops.json").write_text(json.dumps(ops))
    code, out, err = ncg("apply", "Patrol_BT", project / "ops.json")
    assert code == 0, err
    _, out, _ = ncg("node", "Patrol_BT", "5", "6", "--raw")
    raw = json.loads(out)
    assert raw["5"]["node"]["_action"]["valueA"]["_name"] == "Reach"
    assert raw["6"]["node"]["_condition"]["valueA"]["_name"] == "Global/Alarm"
    assert "_targetVariableID" in raw["6"]["node"]["_condition"]["valueA"]  # the global variable's id
    _, out, _ = ncg("node", "Patrol_BT", "6")
    assert '"valueA": "$Global/Alarm"' in out
    (project / "bad.json").write_text(
        json.dumps(
            [
                {
                    "op": "add",
                    "parent": "0",
                    "node": {"$type": "ActionNode", "_action": {"$type": "SetFloat", "valueA": "$Nowhere"}},
                }
            ]
        )
    )
    code, _, err = ncg("apply", "Patrol_BT", project / "bad.json")
    assert code == 1 and "owners' blackboards" in err
