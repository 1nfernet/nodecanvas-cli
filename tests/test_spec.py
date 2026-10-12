import copy
import json

from nodecanvas_cli.build import Builder
from nodecanvas_cli.spec import Speccer
from nodecanvas_cli.workspace import Workspace


def strip(node):
    return {k: v for k, v in node.items() if k not in ("_position", "$id")}


def test_spec_rebuilds_every_fixture_node(project, monkeypatch):
    monkeypatch.chdir(project)
    ws = Workspace()
    checked = 0
    for g in ws.all_graphs():
        sp, b = Speccer(ws, g), Builder(ws, g)
        refs_before = list(g.refs)
        for n in g.nodes:
            spec = sp.node(n)
            # unresolvable variables stay raw: $Ghost (Broken_BT), $World/Panic (no such GlobalBlackboard)
            assert g.name == "Broken_BT" or "World/Panic" in json.dumps(spec) or "raw" not in json.dumps(spec), (
                g.name,
                spec,
            )
            rebuilt = b.node(copy.deepcopy(spec))
            assert json.dumps(strip(rebuilt)) == json.dumps(strip(n)), (g.name, spec)
            checked += 1
        assert g.refs == refs_before
    assert checked >= 30


def test_node_command_prints_spec_and_raw(ncg):
    _, out, _ = ncg("node", "Bot_BT", "8")
    assert out.startswith(
        '#8 {"$type": "SubTree", "_subTree": "Sub_BT", "map": {"Self": "<-$Self", "Alert": "->$Alert"}}'
    )
    _, out, _ = ncg("node", "Bot_BT", "0")
    assert "→ #1, #2, #9 [off]" in out
    _, out, _ = ncg("node", "Bot_BT", "4", "--raw")
    assert json.loads(out)["4"]["node"]["coolDownTime"]["_name"] == "Cool time"
