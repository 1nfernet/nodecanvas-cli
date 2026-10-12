import os

from conftest import SNAPSHOTS

from nodecanvas_cli.container import UnityFile
from nodecanvas_cli.project import Project, find_project_root

GRAPHS = ["Bot_BT", "Sub_BT", "Bot_FSM", "Talk_DT", "Sentry", "Broken_BT", "Patrol_BT"]


def test_roundtrip_is_byte_identical(project):
    proj = Project(find_project_root(project))
    assert len(proj.containers) == 7
    for rel in proj.containers:
        f = UnityFile(proj.abs(rel), rel)
        original = list(f.lines)
        for slot in f.slots():
            f.store(f.load(slot))
        assert f.lines == original, rel


def test_list(ncg):
    code, out, _ = ncg("list")
    assert code == 0
    assert "bt     10  Assets/Graphs/Bot_BT.asset  uses: Sub_BT" in out
    assert "Assets/Prefabs/Sentry.prefab:Sentry" in out
    assert "dt      6  Assets/Graphs/Talk_DT.asset" in out


def test_show_snapshots(ncg):
    update = os.environ.get("UPDATE_SNAPSHOTS")
    for name in GRAPHS:
        code, out, err = ncg("show", name)
        assert code == 0, err
        snap = SNAPSHOTS / f"{name}.txt"
        if update or not snap.exists():
            snap.parent.mkdir(parents=True, exist_ok=True)
            snap.write_text(out, encoding="utf-8")
        assert out == snap.read_text(encoding="utf-8"), name


def test_show_semantics(ncg):
    _, out, _ = ncg("show", "Bot_BT")
    assert "#3 ? !$Alert" in out  # serialized BBParameter without _value is default(T) == false
    assert "(w=3.0) #8 SubTree → Sub_BT {Self←$Self, Alert→$Alert}" in out
    assert "[off] #9 $Speed *= 2.0 // double the speed" in out
    _, out, _ = ncg("show", "Sub_BT")
    assert "#1 $Alert = true" in out  # missing setTo field keeps the C# initializer
    _, out, _ = ncg("show", "Bot_FSM")
    assert "--[!$Alert]--> #0 Idle" in out
    _, out, _ = ncg("show", "Talk_DT")
    assert '2) "Let me pass." if $Met → #3' in out
    assert "#5 Jump → #2" in out


def test_show_subtree_and_expand(ncg):
    _, out, _ = ncg("show", "Bot_BT", "--node", "6")
    assert out.splitlines()[2].startswith("#6 ProbabilitySelector")
    _, out, _ = ncg("show", "Bot_BT", "--expand")
    assert "┆ Sub_BT  [BT, repeat]" in out
    assert "┆ ├─#2 wait 0.5s" in out and '┆ └─#3 Bot.speed = 4.0 "Set speed"' in out


def test_ambiguous_and_missing_graph(ncg):
    code, _, err = ncg("show", "nope")
    assert code == 1 and "no graph matches" in err
    code, _, err = ncg("show", "_BT")
    assert code == 1 and "ambiguous" in err


def test_node_and_vars(ncg):
    code, out, _ = ncg("node", "Bot_BT", "4")
    assert code == 0 and '"coolDownTime": "$Cool time"' in out
    _, out, _ = ncg("vars", "Bot_BT")
    assert "$Cool time: float = 2.0" in out
    assert "used: #4" in out


def test_refs(ncg):
    _, out, _ = ncg("refs", "Bot.Attack")
    assert "Assets/Graphs/Bot_BT.asset" in out and "#5" in out
    _, out, _ = ncg("refs", "Sub_BT")
    assert "Bot_BT.asset" in out and "Bot_FSM.asset" in out
    _, out, _ = ncg("refs", "$Alert")
    assert "#3" in out
    _, out, _ = ncg("refs", "Cooldown", "--kind", "type")
    assert "Bot_BT.asset" in out and "Broken_BT.asset" in out


def test_types_and_members(ncg):
    _, out, _ = ncg("types", "Cooldown")
    assert "coolDownTime: BBParameter<float> = 1f" in out
    _, out, _ = ncg("types", "--role", "action")
    assert "Wait" in out and "SetBoolean" in out
    _, out, _ = ncg("members", "Bot")
    assert "method void MoveTo(Unit, Vector3)" in out
    assert "prop   bool IsDead" in out
    assert "Secret" not in out


def test_kind_disambiguates(ncg, project):
    import shutil

    g = project / "Assets/Graphs"
    shutil.copy(g / "Bot_FSM.asset", g / "Bot_BT_Copy_FSM.asset")
    code, _, err = ncg("show", "Bot_BT")
    assert code == 0
    code, _, err = ncg("show", "Bot_B")
    assert code == 1 and "--kind bt/fsm" in err
    code, out, _ = ncg("show", "Bot_B", "--kind", "fsm")
    assert code == 0 and "[FSM]" in out


def test_show_and_diff_positions(ncg, project):
    import subprocess

    _, out, _ = ncg("show", "Bot_BT", "--positions")
    node_lines = [line for line in out.splitlines() if "#" in line and not line.startswith(("Bot_BT", "vars:"))]
    assert all("@(" in line for line in node_lines)
    assert "[off] #9 $Speed *= 2.0 @(300,140) // double the speed" in out

    def run(*a):
        return subprocess.run(
            ["git", "-c", "user.name=t", "-c", "user.email=t@t", *a], cwd=project, check=True, capture_output=True
        )

    run("init", "-q")
    run("add", "-A")
    run("commit", "-qm", "base")
    (project / "ops.json").write_text('[{"op": "set", "node": "5", "path": "_position", "value": [40, 420]}]')
    assert ncg("apply", "Bot_BT", project / "ops.json")[0] == 0
    assert ncg("diff", "Bot_BT")[1].strip() == "(no changes)"
    _, out, _ = ncg("diff", "Bot_BT", "--positions")
    assert "-│ │ └─# @Self.Attack($Target) @(0,420)" in out and "+│ │ └─# @Self.Attack($Target) @(40,420)" in out
