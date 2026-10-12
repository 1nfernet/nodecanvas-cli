import json
import re


def strip_ids(text):
    return [re.sub(r"#\d+", "#", line) for line in text.splitlines()[1:]]


def test_new_graph(ncg, project):
    code, out, err = ncg(
        "new", "Assets/Graphs/Fresh_BT.asset", "--kind", "bt", "--var", "Self:Bot:pub", "--var", "Range:float=4"
    )
    assert code == 0, err
    meta = (project / "Assets/Graphs/Fresh_BT.asset.meta").read_text()
    assert re.search(r"^guid: [0-9a-f]{32}$", meta, re.M) and "mainObjectFileID: 11400000" in meta
    code, out, _ = ncg("vars", "Fresh_BT")
    assert "$Range: float = 4.0" in out and "$Self: Bot  pub" in out
    assert ncg("lint", "Fresh_BT")[0] == 0
    assert ncg("new", "Assets/Graphs/Fresh_BT.asset", "--kind", "bt")[0] == 1  # already exists


def test_new_fsm_and_dialogue(ncg, project):
    assert ncg("new", "Assets/Graphs/Fresh_FSM.asset", "--kind", "fsm")[0] == 0
    assert ncg("new", "Assets/Graphs/Fresh_DT.asset", "--kind", "dt")[0] == 0
    assert "[FSM]" in ncg("show", "Fresh_FSM")[1] and "[DT]" in ncg("show", "Fresh_DT")[1]


def test_extract_then_inline_restores_the_tree(ncg, project):
    original = strip_ids(ncg("show", "Bot_BT")[1])
    code, out, err = ncg("extract", "Bot_BT", "4", "Assets/Graphs/Strike_BT.asset", "--name", "Strike")
    assert code == 0, err
    tree = ncg("show", "Bot_BT")[1]
    assert 'SubTree "Strike" → Strike_BT {Self←$Self, Target←$Target, Cool time←$Cool time}' in tree
    sub = ncg("show", "Strike_BT")[1]
    assert "Self:Bot(pub)  Target:Unit(pub)  Cool time:float(pub)" in sub and "#1 @Self.Attack($Target)" in sub
    assert ncg("lint", "Bot_BT", "Strike_BT")[0] == 0
    code, out, err = ncg("inline", "Bot_BT", "4")
    assert code == 0, err
    assert strip_ids(ncg("show", "Bot_BT")[1]) == original


def test_extract_maps_written_variables_back(ncg, project):
    code, out, err = ncg("extract", "Sub_BT", "0", "Assets/Graphs/Inner_BT.asset")
    assert code == 0, err
    assert "SubTree → Inner_BT {Alert→$Alert}" in ncg("show", "Sub_BT")[1]


def test_inline_rejects_constant_mappings(ncg, project):
    ops = [
        {
            "op": "set",
            "node": "8",
            "path": "_variablesMap.1",
            "raw": True,
            "value": {
                "_targetSubGraphVariableID": json.loads(ncg("node", "Bot_BT", "8", "--raw")[1])["8"]["node"][
                    "_variablesMap"
                ][1]["_targetSubGraphVariableID"],
                "_canWrite": True,
                "_type": "System.Boolean",
                "_value": {"$content": True, "$type": "System.Boolean"},
            },
        }
    ]
    (project / "ops.json").write_text(json.dumps(ops))
    assert ncg("apply", "Bot_BT", project / "ops.json")[0] == 0
    code, _, err = ncg("inline", "Bot_BT", "8")
    assert code == 1 and "mapped to constants: Alert" in err


def test_copy_between_graphs(ncg, project):
    assert ncg("new", "Assets/Graphs/Fresh_BT.asset", "--kind", "bt")[0] == 0
    (project / "ops.json").write_text(json.dumps([{"op": "add", "node": "Sequencer"}]))
    assert ncg("apply", "Fresh_BT", project / "ops.json")[0] == 0
    code, _, err = ncg("copy", "Bot_BT", "4", "Fresh_BT", "0")
    assert code == 1 and "$Cool time" in err and "--create-vars" in err
    code, out, err = ncg("copy", "Bot_BT", "4", "Fresh_BT", "0", "--create-vars")
    assert code == 0, err
    tree = ncg("show", "Fresh_BT")[1]
    assert "Cooldown [coolDownTime=$Cool time]" in tree and "@Self.Attack($Target)" in tree
    assert ncg("lint", "Fresh_BT")[0] == 0


def test_copy_within_graph_keeps_variables(ncg, project):
    code, out, err = ncg("copy", "Bot_BT", "4", ".", "0", "--index", "0")
    assert code == 0, err
    tree = ncg("show", "Bot_BT")[1]
    assert tree.count("Cooldown [coolDownTime=$Cool time]") == 2
    assert ncg("lint", "Bot_BT")[0] == 0


def test_works_without_nodecanvas_sources(ncg, project):
    import shutil

    shutil.rmtree(project / "Assets/Stubs")
    _, out, _ = ncg("show", "Bot_BT")
    assert "#3 ? !$Alert" in out and "Cooldown [coolDownTime=$Cool time]" in out and "$Speed *= 2.0" in out
    (project / "ops.json").write_text(
        json.dumps(
            [{"op": "add", "parent": "2", "node": {"$type": "ActionNode", "_action": {"$type": "Wait", "waitTime": 2}}}]
        )
    )
    assert ncg("apply", "Bot_BT", project / "ops.json")[0] == 0
    code, out, err = ncg("extract", "Bot_BT", "4", "Assets/Graphs/Strike_BT.asset")
    assert code == 0, err
    assert "SubTree → Strike_BT" in ncg("show", "Bot_BT")[1]
    (project / "ops.json").write_text(json.dumps([{"op": "add", "parent": "2", "node": {"$type": "NoSuchNode"}}]))
    code, out, err = ncg("apply", "Bot_BT", project / "ops.json")
    assert code == 1 and err.startswith("error:") and "Traceback" not in err
    _, out, _ = ncg("lint", "Bot_BT")
    assert "no-nodecanvas-src" in out


def test_copy_as_root_and_new_from(ncg, project):
    assert ncg("new", "Assets/Graphs/Empty_BT.asset", "--kind", "bt")[0] == 0
    code, out, err = ncg("copy", "Bot_BT", "4", "Empty_BT", "root", "--create-vars")
    assert code == 0, err
    tree = ncg("show", "Empty_BT", "--positions")[1]
    assert "#0 Cooldown [coolDownTime=$Cool time] @(0,0)" in tree and "└─#1 @Self.Attack($Target)" in tree
    code, _, err = ncg("copy", "Bot_BT", "4", "Empty_BT", "root", "--create-vars")
    assert code == 1 and "already has a root" in err
    code, out, err = ncg("new", "Assets/Graphs/Strike_BT.asset", "--kind", "bt", "--from", "Bot_BT:4")
    assert code == 0, err
    assert "#0 Cooldown" in ncg("show", "Strike_BT")[1]
    assert "Cool time:float(pub)" in ncg("show", "Strike_BT")[1]
    assert "Cool time:float(pub)" not in ncg("show", "Empty_BT")[1]
    assert ncg("new", "Assets/Graphs/Priv_BT.asset", "--kind", "bt", "--from", "Bot_BT:4", "--private-vars")[0] == 0
    assert "Cool time:float  " in ncg("show", "Priv_BT")[1] + "  "
    assert ncg("lint", "Strike_BT", "Empty_BT")[0] == 0
