import csv
import io
import json

from nodecanvas_cli import fsjson
from nodecanvas_cli.container import UnityFile

ASSET = "Assets/Graphs/Talk_DT.asset"


def _rows(path):
    return list(csv.DictReader(io.StringIO(path.read_text(encoding="utf-8-sig"), newline="")))


def _write(path, rows):
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]), lineterminator="\r\n")
        w.writeheader()
        w.writerows(rows)


def _graph_data(project):
    f = UnityFile(project / ASSET, ASSET)
    return f, f.load(f.slots()[0]).data


def test_export_csv(ncg, project):
    out = project / "talk.csv"
    code, _, err = ncg("dt", "export", "Talk_DT", "--locales", "en,ru", "-o", out)
    assert code == 0 and "keyed by #id" in err
    assert out.read_bytes().startswith(b"\xef\xbb\xbf")  # BOM for Excel
    rows = _rows(out)
    assert [r["key"] for r in rows] == ["#0", "#1:1", "#1:2", "uid-guard-intro"]
    intro = rows[3]
    assert (intro["actor"], intro["kind"], intro["meta"], intro["ru"], intro["en"]) == (
        "Guard",
        "say",
        "calm",
        "Я охраняю эти ворота.",
        "",
    )
    assert rows[2]["kind"] == "choice" and rows[2]["index"] == "2" and rows[2]["text"] == "Let me pass."


def test_import_roundtrip_and_minimal_change(ncg, project):
    out = project / "talk.csv"
    ncg("dt", "export", "Talk_DT", "--locales", "ru", "-o", out)
    before_file, before = _graph_data(project)
    before_lines = list(before_file.lines)
    code, stdout, _ = ncg("dt", "import", out)  # unchanged file: nothing to do
    assert code == 0 and "0 changed, 4 unchanged" in stdout
    assert (project / ASSET).read_bytes().decode("utf-8").split("\n") == before_lines

    rows = _rows(out)
    rows[0]["text"] = "Halt, it's late!\nWho goes there?"
    rows[2]["ru"] = "Пропусти меня."
    rows[3]["meta"] = ""
    _write(out, rows)
    code, stdout, _ = ncg("dt", "import", out, "--dry-run")
    assert code == 0 and "3 changed" in stdout and "dry run" in stdout
    assert (project / ASSET).read_bytes().decode("utf-8").split("\n") == before_lines
    code, stdout, _ = ncg("dt", "import", out)
    assert code == 0 and "saved Assets/Graphs/Talk_DT.asset" in stdout
    _, show, _ = ncg("show", "Talk_DT")
    assert '#0 [Guard] Say "Halt, it\'s late!\\nWho goes there?"' in show
    assert '#2 [Guard] Say "I guard this gate."\n' in show  # meta removed

    after_file, after = _graph_data(project)
    nodes = after["nodes"]
    assert nodes[0]["statement"] == {"_text": "Halt, it's late!\nWho goes there?"}
    assert nodes[1]["availableChoices"][1]["statement"]["_localizations"] == [
        {"Key": 8, "Value": {"text": "Пропусти меня."}}
    ]
    assert list(nodes[2]["statement"]) == ["_text", "_localizations"]
    # everything else is unchanged: put the old statements back and compare the whole JSON
    nodes[0]["statement"] = before["nodes"][0]["statement"]
    nodes[1]["availableChoices"][1]["statement"] = before["nodes"][1]["availableChoices"][1]["statement"]
    nodes[2]["statement"] = before["nodes"][2]["statement"]
    assert fsjson.dumps(after) == fsjson.dumps(before)

    # and the lines outside the graph scalar are byte-identical
    def span(f):
        return [line for line in f.lines if not line.startswith(("  _serializedGraph", "    "))]

    assert span(after_file) == span(before_file)


def test_import_conflict_and_not_found(ncg, project):
    out = project / "talk.csv"
    ncg("dt", "export", "Talk_DT", "-o", out)
    (project / "ops.json").write_text(
        json.dumps([{"op": "set", "node": "0", "path": "statement._text", "value": "Changed in Unity"}])
    )
    assert ncg("apply", "Talk_DT", project / "ops.json")[0] == 0
    rows = _rows(out)
    rows[0]["text"] = "Translator's version"
    rows[1]["key"] = "#1:9"
    _write(out, rows)
    code, stdout, _ = ncg("dt", "import", out)
    assert code == 1
    assert "conflict  line 2: Talk_DT #0: changed in the asset since the export" in stdout
    assert "not found line 3: Talk_DT #1:9 not found" in stdout
    _, show, _ = ncg("show", "Talk_DT")
    assert "Changed in Unity" in show and "Translator" not in show


def test_assign_uids_and_json(ncg, project):
    out = project / "talk.json"
    code, _, err = ncg("dt", "export", "--assign-uids", "--format", "json", "-o", out)
    assert code == 0 and "assigned 2 UIDs" in err and "keyed by #id" not in err
    rows = json.loads(out.read_text(encoding="utf-8"))
    assert all(not r["key"].startswith("#") for r in rows)
    _, data = _graph_data(project)
    first = data["nodes"][0]
    assert list(first).index("_UID") > list(first).index("_actorName")  # FullSerializer order: base fields last
    rows[1]["text"] = "And you are?"
    out.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    code, stdout, _ = ncg("dt", "import", out)
    assert code == 0 and "1 changed" in stdout
    assert '1) "And you are?"' in ncg("show", "Talk_DT")[1]
    assert ncg("lint", "Talk_DT")[0] == 0


def test_dt_lint(ncg, project):
    code, out, _ = ncg("dt", "lint", "--locales", "ru")
    assert code == 0 and "missing-translation  no Russian text" in out and "#2 " not in out
    ops = [
        {"op": "set", "node": "5", "path": "_sourceNodeUID._targetNodeUID", "value": "nope", "raw": True},
        {"op": "set", "node": "0", "path": "_actorName", "value": "Ghost"},
        {"op": "set", "node": "2", "path": "statement._text", "value": ""},
        {"op": "choice-add", "node": "1", "text": "Bye."},
    ]
    (project / "ops.json").write_text(json.dumps(ops))
    assert ncg("apply", "Talk_DT", project / "ops.json", "--force")[0] == 0
    code, out, _ = ncg("dt", "lint", "--min", "warning")
    assert code == 1
    assert "jump-missing" in out and "unknown-actor        actor 'Ghost'" in out
    assert "empty-text" in out and "choice 3 leads nowhere" in out
    code, _, err = ncg("dt", "export", "--locales", "xx")
    assert code == 1 and "unknown locale" in err
