import json
import stat
import sys

import pytest

FAKE = r"""#!{python}
import json, os, re, sys
argv = sys.argv[1:]
with open(os.environ["FAKE_UNITY_LOG"], "a") as fh:
    fh.write(json.dumps(argv) + "\n")
mode = os.environ.get("FAKE_UNITY_MODE", "ok")
if mode == "down":
    print(json.dumps({{"success": False,
                      "errors": [{{"code": "COMMAND_FAILED", "message": "No Pipeline instance found"}}]}}))
    sys.exit(1)
name = argv[1]
if name == "eval":
    print(json.dumps({{"success": True, "data": {{"result": {{"success": True, "result": False}}}}}}))
    sys.exit(0)
if mode == "nocompile":
    print(json.dumps({{"success": False, "data": {{"success": False, "error": "Compilation Failed",
                       "diagnostics": [{{"severity": "error", "line": 3,
                         "message": "The type or namespace name 'NodeCanvas' could not be found"}}]}}}}))
    sys.exit(1)
code = open(argv[2], encoding="utf-8").read()
targets = re.findall(r'new string\[\] \{{ "([^"]*)", "([^"]*)" \}}', code)
graphs = []
for path, anchor in targets:
    g = {{"path": path, "anchor": anchor, "status": "ok", "nodes": 10, "variables": 5, "prime": 0, "saved": False,
          "issues": [], "console": []}}
    if path.endswith("Broken_BT.asset"):
        g["nodes"] = 7; g["variables"] = 1
        g["issues"] = [{{"severity": "warning", "at": "#1", "text": "* Missing Reflected Info 'Demo.Game.Bot.Fly ()'"}},
                       {{"severity": "warning", "at": "#4", "text": "* 'Bot' target agent is null"}}]
        g["console"] = [{{"type": "Error", "text": "(Serialization Error) Method Request Error"}},
                        {{"type": "Log", "text": "noise"}}]
    if path.endswith("Sentry.prefab"):
        g["nodes"] = 2; g["variables"] = 0
    graphs.append(g)
report = {{"nodecanvas_cli_verify": 1, "unity": "6000.0.1f1", "windows": [], "graphs": graphs}}
print(json.dumps({{"success": True, "data": {{"result": {{"success": True, "result": json.dumps(report)}}}}}}))
"""


@pytest.fixture
def fake_unity(tmp_path, monkeypatch):
    exe = tmp_path / "unity"
    exe.write_text(FAKE.format(python=sys.executable))
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    log = tmp_path / "unity.log"
    monkeypatch.setenv("NODECANVAS_CLI_UNITY", str(exe))
    monkeypatch.setenv("FAKE_UNITY_LOG", str(log))

    def calls():
        return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []

    return calls


def test_verify_requires_unity_flag(ncg):
    code, _, err = ncg("verify", "Bot_BT")
    assert code == 2 and "--unity" in err


def test_verify_editor_unavailable(ncg, fake_unity, monkeypatch):
    monkeypatch.setenv("FAKE_UNITY_MODE", "down")
    code, _, err = ncg("verify", "--unity", "Bot_BT")
    assert code == 2 and "No Pipeline instance found" in err and "unity pipeline install" in err


def test_verify_missing_cli(ncg, monkeypatch):
    monkeypatch.setenv("NODECANVAS_CLI_UNITY", "")
    monkeypatch.setenv("PATH", "/nonexistent")
    code, _, err = ncg("verify", "--unity", "Bot_BT")
    assert code == 2 and "not on PATH" in err


def test_verify_reports(ncg, fake_unity, project):
    code, out, err = ncg("verify", "--unity", "Bot_BT", "Broken_BT", "Sentry")
    assert code == 1, err
    assert "ok   Assets/Graphs/Bot_BT.asset  nodes=10 vars=5" in out
    assert "FAIL Assets/Graphs/Broken_BT.asset" in out
    assert "error   #1         Missing Reflected Info 'Demo.Game.Bot.Fly ()'" in out
    assert "target agent is null" not in out  # context-dependent editor warning
    assert "Method Request Error" in out and "noise" not in out
    assert "ok   Assets/Prefabs/Sentry.prefab:Sentry  nodes=2" in out
    calls = fake_unity()
    assert [c[1] for c in calls] == ["eval", "eval", "eval_file"]  # refresh, wait for compilation, check
    assert calls[0][calls[0].index("--project-path") + 1] == str(project)
    assert "--skill" in calls[0]


def test_verify_snippet_targets_and_resave(fake_unity):
    from nodecanvas_cli.verify import build_snippet

    code = build_snippet([("Assets/A.asset", ""), ("Assets/P.prefab", "1003")], resave=True)
    assert 'new string[] { "Assets/A.asset", "" }, new string[] { "Assets/P.prefab", "1003" }' in code
    assert "var resave = true;" in code


def test_verify_count_mismatch_and_json(ncg, fake_unity):
    code, out, _ = ncg("verify", "--unity", "Sub_BT", "--json")
    rows = json.loads(out)["graphs"]
    assert code == 1 and rows[0]["ok"] is False
    assert any("Unity loaded 10 nodes, the file has 4" in i["message"] for i in rows[0]["issues"])


def test_verify_snippet_does_not_compile(ncg, fake_unity, monkeypatch):
    monkeypatch.setenv("FAKE_UNITY_MODE", "nocompile")
    code, _, err = ncg("verify", "--unity", "Bot_BT")
    assert code == 1 and "does not compile" in err and "NodeCanvas" in err


def test_verify_changed(ncg, fake_unity, project):
    import subprocess

    def run(*a):
        return subprocess.run(
            ["git", "-c", "user.name=t", "-c", "user.email=t@t", *a], cwd=project, check=True, capture_output=True
        )

    run("init", "-q")
    run("add", "-A")
    run("commit", "-qm", "base")
    code, out, _ = ncg("verify", "--unity", "--changed")
    assert code == 0 and "no changed graphs" in out
    (project / "ops.json").write_text('[{"op": "set", "node": "1", "path": "_position", "value": [0, 160]}]')
    assert ncg("apply", "Sentry", project / "ops.json")[0] == 0
    code, out, _ = ncg("verify", "--unity", "--changed")
    assert "Sentry.prefab:Sentry" in out and "Bot_BT" not in out
