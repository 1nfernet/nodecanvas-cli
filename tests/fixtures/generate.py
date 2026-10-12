#!/usr/bin/env python3
"""Regenerate the synthetic graph fixtures under tests/fixtures/project/Assets.

The graphs are written the way Unity + NodeCanvas serialize them (single-quoted folded JSON
scalar, FullSerializer conventions), using nodecanvas-cli's own scalar codec, which is verified
byte-for-byte against real projects. Run: python tests/fixtures/generate.py
"""

import hashlib
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "src"))

from nodecanvas_cli import fsjson  # noqa: E402
from nodecanvas_cli.yamlscalar import encode_single  # noqa: E402

PROJECT = HERE / "project"
SCRIPT_GUIDS = {
    "bt": "7a686a47eee2fa44cb0a34b5d86e4d5e",
    "fsm": "f945e777233a59f4aba40aeca29093a6",
    "dt": "b2d1e2b9d45c5e143b84b6d3d4a1b0c2",
    "owner": "5c1f2a9e1d3b4c6e8f0a1b2c3d4e5f60",
}
TYPES = {
    "bt": "NodeCanvas.BehaviourTrees.BehaviourTree",
    "fsm": "NodeCanvas.StateMachines.FSM",
    "dt": "NodeCanvas.DialogueTrees.DialogueTree",
}
SYS = "mscorlib, Version=4.0.0.0, Culture=neutral, PublicKeyToken=b77a5c561934e089"
GAME = "Demo.Game, Version=0.0.0.0, Culture=neutral, PublicKeyToken=null"
VAR = "NodeCanvas.Framework.Variable`1[[{}]]"


def guid(name: str) -> str:
    return hashlib.md5(name.encode()).hexdigest()


def var(name, vid, qualified, value=None, public=False):
    v = {}
    if value is not None:
        v["_value"] = value
    v["_name"] = name
    v["_id"] = vid
    if public:
        v["_isPublic"] = True
    v["$type"] = VAR.format(qualified)
    return v


def ref(name, vid):
    return {"_name": name, "_targetVariableID": vid}


def conn(kind, s, t, **extra):
    c = dict(extra)
    c["_sourceNode"] = {"$ref": str(s)}
    c["_targetNode"] = {"$ref": str(t)}
    c["$type"] = {
        "bt": "NodeCanvas.BehaviourTrees.BTConnection",
        "fsm": "NodeCanvas.StateMachines.FSMConnection",
        "dt": "NodeCanvas.DialogueTrees.DTConnection",
    }[kind]
    return c


def drop_zero_positions(nodes):
    for n in nodes:
        p = n.get("_position")
        if isinstance(p, dict):
            n["_position"] = {k: v for k, v in p.items() if v != 0.0}


def finish_nodes(nodes, connections):
    drop_zero_positions(nodes)
    referenced = set()
    for c in connections:
        referenced.add(c["_sourceNode"]["$ref"])
        referenced.add(c["_targetNode"]["$ref"])
    for i, n in enumerate(nodes):
        n.pop("$id", None)
        if str(i) in referenced:
            n["$id"] = str(i)
    return nodes


def graph_json(kind, nodes, connections, variables, derived=None):
    g = {
        "type": TYPES[kind],
        "nodes": finish_nodes(nodes, connections),
        "connections": connections,
        "canvasGroups": [],
        "localBlackboard": {"_variables": variables},
    }
    g["derivedData"] = derived
    return fsjson.dumps(g)


def write_asset(rel, name, kind, text, refs=None):
    refs = refs or []
    lines = [
        "%YAML 1.1",
        "%TAG !u! tag:unity3d.com,2011:",
        "--- !u!114 &11400000",
        "MonoBehaviour:",
        "  m_ObjectHideFlags: 0",
        "  m_CorrespondingSourceObject: {fileID: 0}",
        "  m_PrefabInstance: {fileID: 0}",
        "  m_PrefabAsset: {fileID: 0}",
        "  m_GameObject: {fileID: 0}",
        "  m_Enabled: 1",
        "  m_EditorHideFlags: 0",
        f"  m_Script: {{fileID: 11500000, guid: {SCRIPT_GUIDS[kind]}, type: 3}}",
        f"  m_Name: {name}",
        f"  m_EditorClassIdentifier: NodeCanvas::{TYPES[kind]}",
    ]
    lines += encode_single("_serializedGraph", 2, text)
    if refs:
        lines.append("  _objectReferences:")
        lines += [f"  - {r}" for r in refs]
    else:
        lines.append("  _objectReferences: []")
    lines += [
        "  _graphSource:",
        "    _version: 3.33",
        "    _category: ",
        "    _comments: ",
        "    _translation: {x: 0, y: 0}",
        "    _zoomFactor: 1",
        "  _haltSerialization: 0",
        "  _externalSerializationFile: {fileID: 0}",
        "",
    ]
    path = PROJECT / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes("\n".join(lines).encode())
    meta = PROJECT / (rel + ".meta")
    meta.write_text(
        f"fileFormatVersion: 2\nguid: {guid(rel)}\nNativeFormatImporter:\n  externalObjects: {{}}\n"
        f"  mainObjectFileID: 11400000\n  userData: \n  assetBundleName: \n  assetBundleVariant: \n"
    )


def asset_ref(rel):
    return f"{{fileID: 11400000, guid: {guid(rel)}, type: 2}}"


BOT = "Demo.Game.Bot"
UNIT = "Demo.Game.Unit"
V = {
    k: hashlib.md5(k.encode()).hexdigest()[:8] + "-0000-4000-8000-" + hashlib.md5(k.encode()).hexdigest()[:12]
    for k in [
        "self",
        "target",
        "alert",
        "cool",
        "w1",
        "w2",
        "sub_self",
        "sub_alert",
        "fsm_self",
        "fsm_alert",
        "dt_flag",
        "speed",
        "patience",
        "spare",
        "home",
        "reach",
        "alarm",
        "panic",
    ]
}


def sub_bt():
    variables = {
        "Self": var("Self", V["sub_self"], f"{BOT}, {GAME}", public=True),
        "Alert": var("Alert", V["sub_alert"], f"System.Boolean, {SYS}", public=True),
    }
    nodes = [
        {"_position": {"x": 0.0, "y": 0.0}, "$type": "NodeCanvas.BehaviourTrees.Sequencer"},
        {
            "_action": {"boolVariable": ref("Alert", V["sub_alert"]), "$type": "NodeCanvas.Tasks.Actions.SetBoolean"},
            "_position": {"x": -100.0, "y": 140.0},
            "$type": "NodeCanvas.BehaviourTrees.ActionNode",
        },
        {
            "_action": {"waitTime": {"_value": 0.5}, "$type": "NodeCanvas.Tasks.Actions.Wait"},
            "_position": {"x": 200.0, "y": 140.0},
            "$type": "NodeCanvas.BehaviourTrees.ActionNode",
        },
        {
            "_action": {
                "field": {"_baseInfo": f"{BOT}|speed"},
                "setValue": {"_type": "System.Single", "_value": {"$content": 4.0, "$type": "System.Single"}},
                "$type": "NodeCanvas.Tasks.Actions.SetField",
                "$version": 1,
            },
            "_name": "Set speed",
            "_position": {"x": 500.0, "y": 200.0},
            "$type": "NodeCanvas.BehaviourTrees.ActionNode",
        },
    ]
    conns = [conn("bt", 0, 1), conn("bt", 0, 2), conn("bt", 0, 3)]
    write_asset(
        "Assets/Graphs/Sub_BT.asset",
        "Sub_BT",
        "bt",
        graph_json(
            "bt",
            nodes,
            conns,
            variables,
            {"repeat": True, "$type": "NodeCanvas.BehaviourTrees.BehaviourTree+DerivedSerializationData"},
        ),
    )


def bot_bt():
    agent = {"_type": BOT, "_name": "Self", "_targetVariableID": V["self"]}
    variables = {
        "Self": var("Self", V["self"], f"{BOT}, {GAME}", public=True),
        "Target": var("Target", V["target"], f"{UNIT}, {GAME}"),
        "Alert": var("Alert", V["alert"], f"System.Boolean, {SYS}"),
        "Cool time": var("Cool time", V["cool"], f"System.Single, {SYS}", value=2.0),
        "Speed": var("Speed", V["speed"], f"System.Single, {SYS}", value=3.5),
    }
    nodes = [
        # 0
        {"_position": {"x": 0.0, "y": 0.0}, "$type": "NodeCanvas.BehaviourTrees.Selector"},
        # 1
        {
            "_condition": {
                "method": {"_baseInfo": f"{BOT}|get_IsDead|System.Boolean", "_paramsInfo": ""},
                "checkValue": {"_type": "System.Boolean", "_value": {"$content": True, "$type": "System.Boolean"}},
                "overrideAgent": agent,
                "$type": "NodeCanvas.Tasks.Conditions.CheckProperty_Multiplatform",
            },
            "_position": {"x": -300.0, "y": 140.0},
            "$type": "NodeCanvas.BehaviourTrees.ConditionNode",
        },
        # 2
        {"_name": "Fight", "_position": {"x": 0.0, "y": 140.0}, "$type": "NodeCanvas.BehaviourTrees.Sequencer"},
        # 3
        {
            "_condition": {
                "valueA": ref("Alert", V["alert"]),
                "valueB": {},
                "$type": "NodeCanvas.Tasks.Conditions.CheckBoolean",
            },
            "_position": {"x": -200.0, "y": 280.0},
            "$type": "NodeCanvas.BehaviourTrees.ConditionNode",
        },
        # 4
        {
            "coolDownTime": ref("Cool time", V["cool"]),
            "_position": {"x": 0.0, "y": 280.0},
            "$type": "NodeCanvas.BehaviourTrees.Cooldown",
        },
        # 5
        {
            "_action": {
                "method": {"_baseInfo": f"{BOT}|Attack|System.Void", "_paramsInfo": UNIT},
                "parameters": [{"_type": UNIT, "_name": "Target", "_targetVariableID": V["target"]}],
                "returnValue": {},
                "overrideAgent": agent,
                "$type": "NodeCanvas.Tasks.Actions.ExecuteFunction_Multiplatform",
            },
            "_position": {"x": 0.0, "y": 420.0},
            "$type": "NodeCanvas.BehaviourTrees.ActionNode",
        },
        # 6
        {
            "childWeights": [{"_value": 1.0}, {"_value": 3.0}],
            "_position": {"x": 200.0, "y": 280.0},
            "$type": "NodeCanvas.BehaviourTrees.ProbabilitySelector",
        },
        # 7
        {
            "_action": {
                "method": {"_baseInfo": f"{BOT}|MoveTo|System.Void", "_paramsInfo": "UnityEngine.Vector3"},
                "parameters": [
                    {"_type": "UnityEngine.Vector3", "_value": {"x": 1.0, "z": -2.5, "$type": "UnityEngine.Vector3"}}
                ],
                "returnValue": {},
                "overrideAgent": agent,
                "$type": "NodeCanvas.Tasks.Actions.ExecuteFunction_Multiplatform",
            },
            "_position": {"x": 100.0, "y": 420.0},
            "$type": "NodeCanvas.BehaviourTrees.ActionNode",
        },
        # 8
        {
            "_subTree": {"_value": 1},
            "_variablesMap": [
                {
                    "_targetSubGraphVariableID": V["sub_self"],
                    "_canWrite": True,
                    "_type": BOT,
                    "_name": "Self",
                    "_targetVariableID": V["self"],
                },
                {
                    "_targetSubGraphVariableID": V["sub_alert"],
                    "_canRead": True,
                    "_type": "System.Boolean",
                    "_name": "Alert",
                    "_targetVariableID": V["alert"],
                },
            ],
            "_position": {"x": 300.0, "y": 420.0},
            "$type": "NodeCanvas.BehaviourTrees.SubTree",
        },
        # 9
        {
            "_action": {
                "valueA": ref("Speed", V["speed"]),
                "Operation": 3,
                "valueB": {"_value": 2.0},
                "$type": "NodeCanvas.Tasks.Actions.SetFloat",
            },
            "_comment": "double the speed",
            "_position": {"x": 300.0, "y": 140.0},
            "$type": "NodeCanvas.BehaviourTrees.ActionNode",
        },
    ]
    conns = [
        conn("bt", 0, 1),
        conn("bt", 0, 2),
        conn("bt", 0, 9, _isDisabled=True),
        conn("bt", 2, 3),
        conn("bt", 2, 4),
        conn("bt", 2, 6),
        conn("bt", 4, 5),
        conn("bt", 6, 7),
        conn("bt", 6, 8),
    ]
    # FullSerializer writes connections grouped by source node in node order.
    order = {str(i): i for i in range(len(nodes))}
    conns.sort(key=lambda c: order[c["_sourceNode"]["$ref"]])
    write_asset(
        "Assets/Graphs/Bot_BT.asset",
        "Bot_BT",
        "bt",
        graph_json(
            "bt",
            nodes,
            conns,
            variables,
            {"repeat": True, "$type": "NodeCanvas.BehaviourTrees.BehaviourTree+DerivedSerializationData"},
        ),
        ["{fileID: 0}", asset_ref("Assets/Graphs/Sub_BT.asset")],
    )


def bot_fsm():
    variables = {
        "Self": var("Self", V["fsm_self"], f"{BOT}, {GAME}", public=True),
        "Alert": var("Alert", V["fsm_alert"], f"System.Boolean, {SYS}"),
    }

    def mapping(sub_var):
        return [
            {
                "_targetSubGraphVariableID": V[sub_var],
                "_canWrite": True,
                "_type": BOT,
                "_name": "Self",
                "_targetVariableID": V["fsm_self"],
            }
        ]

    nodes = [
        {
            "_nestedBT": {"_value": 1},
            "_variablesMap": mapping("sub_self"),
            "_name": "Idle",
            "_position": {"x": 0.0, "y": 0.0},
            "$type": "NodeCanvas.StateMachines.NestedBTState",
        },
        {
            "_nestedBT": {"_value": 2},
            "_variablesMap": mapping("self"),
            "_name": "Combat",
            "_position": {"x": 300.0, "y": 0.0},
            "$type": "NodeCanvas.StateMachines.NestedBTState",
        },
        {"_name": "Any", "_position": {"x": 160.0, "y": -200.0}, "$type": "NodeCanvas.StateMachines.AnyState"},
    ]
    conns = [
        conn(
            "fsm",
            0,
            1,
            _condition={
                "valueA": ref("Alert", V["fsm_alert"]),
                "valueB": {"_value": True},
                "$type": "NodeCanvas.Tasks.Conditions.CheckBoolean",
            },
        ),
        conn(
            "fsm",
            1,
            0,
            _condition={
                "valueA": ref("Alert", V["fsm_alert"]),
                "valueB": {},
                "$type": "NodeCanvas.Tasks.Conditions.CheckBoolean",
            },
        ),
        conn("fsm", 2, 0),
    ]
    write_asset(
        "Assets/Graphs/Bot_FSM.asset",
        "Bot_FSM",
        "fsm",
        graph_json("fsm", nodes, conns, variables),
        ["{fileID: 0}", asset_ref("Assets/Graphs/Sub_BT.asset"), asset_ref("Assets/Graphs/Bot_BT.asset")],
    )


def talk_dt():
    variables = {"Met": var("Met", V["dt_flag"], f"System.Boolean, {SYS}")}
    nodes = [
        {
            "statement": {"_text": "Hello, traveller!"},
            "_actorName": "Guard",
            "_position": {"x": 0.0, "y": 0.0},
            "$type": "NodeCanvas.DialogueTrees.StatementNode",
        },
        {
            "availableChoices": [
                {"statement": {"_text": "Who are you?"}},
                {
                    "statement": {"_text": "Let me pass."},
                    "condition": {
                        "valueA": ref("Met", V["dt_flag"]),
                        "valueB": {"_value": True},
                        "$type": "NodeCanvas.Tasks.Conditions.CheckBoolean",
                    },
                },
            ],
            "_actorName": "Player",
            "_position": {"x": 0.0, "y": 160.0},
            "$type": "NodeCanvas.DialogueTrees.MultipleChoiceNode",
        },
        {
            "statement": {
                "_text": "I guard this gate.",
                "_meta": "calm",
                "_localizations": [{"Key": 8, "Value": {"text": "Я охраняю эти ворота."}}],
            },
            "_actorName": "Guard",
            "_UID": "uid-guard-intro",
            "_position": {"x": -160.0, "y": 300.0},
            "$type": "NodeCanvas.DialogueTrees.StatementNode",
        },
        {
            "_condition": {
                "valueA": ref("Met", V["dt_flag"]),
                "valueB": {},
                "$type": "NodeCanvas.Tasks.Conditions.CheckBoolean",
            },
            "_position": {"x": 160.0, "y": 300.0},
            "$type": "NodeCanvas.DialogueTrees.ConditionNode",
        },
        {"_position": {"x": 100.0, "y": 440.0}, "$type": "NodeCanvas.DialogueTrees.FinishNode"},
        {
            "_sourceNodeUID": {"_targetNodeUID": "uid-guard-intro"},
            "_position": {"x": 260.0, "y": 440.0},
            "$type": "NodeCanvas.DialogueTrees.Jumper",
        },
    ]
    conns = [conn("dt", 0, 1), conn("dt", 1, 2), conn("dt", 1, 3), conn("dt", 2, 1), conn("dt", 3, 4), conn("dt", 3, 5)]
    derived = {
        "actorParameters": [{"_keyName": "Guard", "_id": "actor-guard"}, {"_keyName": "Player", "_id": "actor-player"}],
        "$type": "NodeCanvas.DialogueTrees.DialogueTree+DerivedSerializationData",
    }
    write_asset("Assets/Graphs/Talk_DT.asset", "Talk_DT", "dt", graph_json("dt", nodes, conns, variables, derived))


def broken_bt():
    variables = {"Self": var("Self", V["self"], f"{BOT}, {GAME}")}
    nodes = [
        {"_position": {"x": 0.0, "y": 0.0}, "$type": "NodeCanvas.BehaviourTrees.Sequencer"},
        {
            "_action": {
                "method": {"_baseInfo": f"{BOT}|Fly|System.Void", "_paramsInfo": ""},
                "parameters": [],
                "returnValue": {},
                "$type": "NodeCanvas.Tasks.Actions.ExecuteFunction_Multiplatform",
            },
            "_position": {"x": 0.0, "y": 140.0},
            "$type": "NodeCanvas.BehaviourTrees.ActionNode",
        },
        {
            "_condition": {
                "valueA": ref("Ghost", "deadbeef-0000-4000-8000-000000000000"),
                "valueB": {},
                "$type": "NodeCanvas.Tasks.Conditions.CheckBoolean",
            },
            "_position": {"x": 200.0, "y": 140.0},
            "$type": "NodeCanvas.BehaviourTrees.ConditionNode",
        },
        {"_position": {"x": 400.0, "y": 140.0}, "$type": "NodeCanvas.BehaviourTrees.Cooldown"},
        {
            "_action": {"waitTime": {"_value": 1.0}, "$type": "NodeCanvas.Tasks.Actions.Wait"},
            "_position": {"x": 300.0, "y": 280.0},
            "$type": "NodeCanvas.BehaviourTrees.ActionNode",
        },
        {
            "_action": {"waitTime": {"_value": 2.0}, "$type": "NodeCanvas.Tasks.Actions.Wait"},
            "_position": {"x": 500.0, "y": 280.0},
            "$type": "NodeCanvas.BehaviourTrees.ActionNode",
        },
        {
            "_action": {
                "_missingType": "Demo.Game.Teleport",
                "_recoveryState": "{}",
                "$type": "NodeCanvas.Framework.Internal.MissingAction",
            },
            "_position": {"x": 900.0, "y": 0.0},
            "$type": "NodeCanvas.BehaviourTrees.ActionNode",
        },
    ]
    conns = [conn("bt", 0, 1), conn("bt", 0, 2), conn("bt", 0, 3), conn("bt", 3, 4), conn("bt", 3, 5)]
    write_asset(
        "Assets/Graphs/Broken_BT.asset",
        "Broken_BT",
        "bt",
        graph_json(
            "bt",
            nodes,
            conns,
            variables,
            {"repeat": True, "$type": "NodeCanvas.BehaviourTrees.BehaviourTree+DerivedSerializationData"},
        ),
    )


def owner_prefab():
    nodes = [
        {"_position": {"x": 0.0, "y": 0.0}, "$type": "NodeCanvas.BehaviourTrees.Sequencer"},
        {
            "_action": {"waitTime": {"_value": 3.0}, "$type": "NodeCanvas.Tasks.Actions.Wait"},
            "_position": {"x": 0.0, "y": 140.0},
            "$type": "NodeCanvas.BehaviourTrees.ActionNode",
        },
    ]
    text = graph_json(
        "bt",
        nodes,
        [conn("bt", 0, 1)],
        {},
        {"repeat": True, "$type": "NodeCanvas.BehaviourTrees.BehaviourTree+DerivedSerializationData"},
    )
    lines = [
        "%YAML 1.1",
        "%TAG !u! tag:unity3d.com,2011:",
        "--- !u!1 &1001",
        "GameObject:",
        "  m_ObjectHideFlags: 0",
        "  serializedVersion: 6",
        "  m_Component:",
        "  - component: {fileID: 1002}",
        "  - component: {fileID: 1003}",
        "  m_Layer: 0",
        "  m_Name: Sentry",
        "  m_TagString: Untagged",
        "  m_IsActive: 1",
        "--- !u!4 &1002",
        "Transform:",
        "  m_ObjectHideFlags: 0",
        "  m_GameObject: {fileID: 1001}",
        "--- !u!114 &1003",
        "MonoBehaviour:",
        "  m_ObjectHideFlags: 0",
        "  m_GameObject: {fileID: 1001}",
        "  m_Enabled: 1",
        f"  m_Script: {{fileID: 11500000, guid: {SCRIPT_GUIDS['owner']}, type: 3}}",
        "  m_Name: ",
        "  m_EditorClassIdentifier: NodeCanvas::NodeCanvas.BehaviourTrees.BehaviourTreeOwner",
        "  _serializedExposedParameters: []",
        "  _boundGraphSerialization: '" + "x" + "'",
    ]
    lines = lines[:-1] + encode_single("_boundGraphSerialization", 2, text)
    lines += [
        "  _boundGraphObjectReferences: []",
        "  _boundGraphSource:",
        "    _version: 3.33",
        "    _category: ",
        "    _comments: ",
        "    _translation: {x: 0, y: 0}",
        "    _zoomFactor: 1",
        "  _graph: {fileID: 0}",
        "  _blackboard: {fileID: 0}",
        "  _firstActivation: 0",
        "",
    ]
    path = PROJECT / "Assets/Prefabs/Sentry.prefab"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes("\n".join(lines).encode())
    (PROJECT / "Assets/Prefabs/Sentry.prefab.meta").write_text(
        f"fileFormatVersion: 2\nguid: {guid('Assets/Prefabs/Sentry.prefab')}\n"
        "PrefabImporter:\n  externalObjects: {}\n"
    )


BB_SCRIPT = "9a1b2c3d4e5f60718293a4b5c6d7e8f9"
GLOBAL_SCRIPT = "8a1b2c3d4e5f60718293a4b5c6d7e8f9"
ASSET_BB_SCRIPT = "7a1b2c3d4e5f60718293a4b5c6d7e8f9"


def patrol_bt():
    variables = {
        "Patience": var("Patience", V["patience"], f"System.Single, {SYS}", value=2.0, public=True),
        "Spare": var("Spare", V["spare"], f"System.Single, {SYS}", public=True),
    }
    nodes = [
        {"_position": {"x": 0.0, "y": 0.0}, "$type": "NodeCanvas.BehaviourTrees.Sequencer"},
        {
            "_condition": {
                "valueA": ref("Global/Alarm", V["alarm"]),
                "valueB": {},
                "$type": "NodeCanvas.Tasks.Conditions.CheckBoolean",
            },
            "_position": {"x": -300.0, "y": 200.0},
            "$type": "NodeCanvas.BehaviourTrees.ConditionNode",
        },
        {
            "_action": {
                "valueA": ref("Home dist", V["home"]),
                "valueB": ref("Patience", V["patience"]),
                "$type": "NodeCanvas.Tasks.Actions.SetFloat",
            },
            "_position": {"x": 0.0, "y": 200.0},
            "$type": "NodeCanvas.BehaviourTrees.ActionNode",
        },
        {
            "_action": {"boolVariable": ref("World/Panic", V["panic"]), "$type": "NodeCanvas.Tasks.Actions.SetBoolean"},
            "_position": {"x": 300.0, "y": 200.0},
            "$type": "NodeCanvas.BehaviourTrees.ActionNode",
        },
        {
            "_action": {
                "valueA": ref("Reach", V["reach"]),
                "valueB": {"_value": 1.0},
                "$type": "NodeCanvas.Tasks.Actions.SetFloat",
            },
            "_position": {"x": 600.0, "y": 200.0},
            "$type": "NodeCanvas.BehaviourTrees.ActionNode",
        },
    ]
    conns = [conn("bt", 0, 1), conn("bt", 0, 2), conn("bt", 0, 3), conn("bt", 0, 4)]
    write_asset(
        "Assets/Graphs/Patrol_BT.asset",
        "Patrol_BT",
        "bt",
        graph_json(
            "bt",
            nodes,
            conns,
            variables,
            {"repeat": True, "$type": "NodeCanvas.BehaviourTrees.BehaviourTree+DerivedSerializationData"},
        ),
    )


def pair_lines(key, items):
    """A ``SerializationPair[]`` field: ``- _json: '...'`` + ``_references``, the way Unity writes it."""
    if not items:
        return [f"  {key}: []"]
    out = [f"  {key}:"]
    for text, refs in items:
        js = encode_single("_json", 4, text)
        js[0] = "  - " + js[0][4:]
        out += js
        out += ["    _references:"] + [f"    - {r}" for r in refs] if refs else ["    _references: []"]
    return out


def bb_lines(variables):
    return encode_single("_serializedBlackboard", 2, fsjson.dumps({"_variables": variables}))


def write_unity(rel, lines, importer="PrefabImporter"):
    path = PROJECT / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes("\n".join(lines + [""]).encode())
    (PROJECT / (rel + ".meta")).write_text(
        f"fileFormatVersion: 2\nguid: {guid(rel)}\n{importer}:\n  externalObjects: {{}}\n"
    )


def component(fid, go, script, ident):
    return [
        f"--- !u!114 &{fid}",
        "MonoBehaviour:",
        "  m_ObjectHideFlags: 0",
        "  m_CorrespondingSourceObject: {fileID: 0}",
        "  m_PrefabInstance: {fileID: 0}",
        "  m_PrefabAsset: {fileID: 0}",
        f"  m_GameObject: {{fileID: {go}}}",
        "  m_Enabled: 1",
        "  m_EditorHideFlags: 0",
        f"  m_Script: {{fileID: 11500000, guid: {script}, type: 3}}",
        "  m_Name: ",
        f"  m_EditorClassIdentifier: {ident}",
    ]


def game_object(fid, name, comps):
    return (
        [f"--- !u!1 &{fid}", "GameObject:", "  m_ObjectHideFlags: 0", "  serializedVersion: 6", "  m_Component:"]
        + [f"  - component: {{fileID: {c}}}" for c in comps]
        + ["  m_Layer: 0", f"  m_Name: {name}", "  m_IsActive: 1"]
    )


def owner_fields(graph_ref, bb_ref, exposed):
    return pair_lines("_serializedExposedParameters", exposed) + [
        "  _boundGraphSerialization: ",
        "  _boundGraphObjectReferences: []",
        "  _boundGraphSource:",
        "    _version: 0",
        "    _category: ",
        "    _comments: ",
        "    _translation: {x: 0, y: 0}",
        "    _zoomFactor: 1",
        "  _firstActivation: 0",
        "  _enableAction: 0",
        "  _disableAction: 0",
        "  _lockBoundGraphPrefabOverrides: 1",
        "  _preInitializeSubGraphs: 0",
        "  _updateMode: 0",
        f"  _graph: {graph_ref}",
        f"  _blackboard: {bb_ref}",
    ]


def exposed(vid, qualified, value):
    return fsjson.dumps(
        {
            "_targetVariableID": vid,
            "_value": value,
            "$type": "NodeCanvas.Framework.ExposedParameter`1[[" + qualified + "]]",
        }
    )


def guard_prefab():
    owner_cs = PROJECT / "Assets/Game/GuardOwner.cs"
    owner_cs.write_text(
        "namespace Demo.Game\n{\n    public class GuardOwner : NodeCanvas.BehaviourTrees.BehaviourTreeOwner { }\n}\n"
    )
    lines = ["%YAML 1.1", "%TAG !u! tag:unity3d.com,2011:"]
    lines += game_object(1001, "Guard", [1002, 1003, 1004])
    lines += ["--- !u!4 &1002", "Transform:", "  m_ObjectHideFlags: 0", "  m_GameObject: {fileID: 1001}"]
    lines += component(1003, 1001, guid("Assets/Game/GuardOwner.cs"), "Demo.Game::Demo.Game.GuardOwner")
    lines += owner_fields(
        asset_ref("Assets/Graphs/Patrol_BT.asset"),
        "{fileID: 1004}",
        [
            (exposed(V["patience"], f"System.Int32, {SYS}", 3), []),
            (exposed(V["spare"], f"System.Single, {SYS}", 1.5), []),
            (exposed(V["home"] + "x", f"Demo.Game.Bot, {GAME}", 1), ["{fileID: 0}", "{fileID: 1003}"]),
        ],
    )
    lines += component(1004, 1001, BB_SCRIPT, "ParadoxNotion::NodeCanvas.Framework.Blackboard")
    lines += [f"  _parentBlackboard: {asset_ref('Assets/Blackboards/Shared.asset')}"]
    lines += bb_lines({"Home dist": var("Home dist", V["home"], f"System.Single, {SYS}", value=4.0)})
    lines += ["  _objectReferences: []", "  _serializedVariables: []"]
    write_unity("Assets/Prefabs/Guard.prefab", lines)


def shared_asset():
    lines = ["%YAML 1.1", "%TAG !u! tag:unity3d.com,2011:"] + component(
        11400000, 0, ASSET_BB_SCRIPT, "ParadoxNotion::NodeCanvas.Framework.AssetBlackboard"
    )
    lines = [line if not line.startswith("  m_Name:") else "  m_Name: Shared" for line in lines]
    lines += bb_lines({"Reach": var("Reach", V["reach"], f"System.Single, {SYS}", value=2.5)})
    lines += ["  _objectReferences: []", "  _UID: shared-uid"]
    write_unity("Assets/Blackboards/Shared.asset", lines, "NativeFormatImporter")


def world_scene():
    alarm = var("Alarm", V["alarm"], f"System.Boolean, {SYS}")
    lines = ["%YAML 1.1", "%TAG !u! tag:unity3d.com,2011:"]
    lines += game_object(2001, "Globals", [2003])
    lines += component(2003, 2001, GLOBAL_SCRIPT, "ParadoxNotion::NodeCanvas.Framework.GlobalBlackboard")
    lines += ["  _parentBlackboard: {fileID: 0}"] + bb_lines({"Alarm": alarm}) + ["  _objectReferences: []"]
    lines += pair_lines("_serializedVariables", [(fsjson.dumps(alarm), [])])
    lines += ["  _UID: global-uid", "  _identifier: Global", "  _singletonMode: 1", "  _dontDestroyOnLoad: 1"]
    lines += game_object(2101, "Runner", [2103])
    lines += component(2103, 2101, SCRIPT_GUIDS["owner"], "NodeCanvas::NodeCanvas.StateMachines.FSMOwner")
    lines += owner_fields("{fileID: 0}", "{fileID: 0}", [])
    lines += [
        "--- !u!1001 &3001",
        "PrefabInstance:",
        "  m_ObjectHideFlags: 0",
        "  serializedVersion: 2",
        "  m_Modification:",
        "    serializedVersion: 3",
        "    m_TransformParent: {fileID: 0}",
        "    m_Modifications:",
        f"    - target: {{fileID: 1003, guid: {guid('Assets/Prefabs/Guard.prefab')}, type: 3}}",
        "      propertyPath: _graph",
        "      value: ",
        f"      objectReference: {asset_ref('Assets/Graphs/Bot_FSM.asset')}",
        f"    - target: {{fileID: 1001, guid: {guid('Assets/Prefabs/Guard.prefab')}, type: 3}}",
        "      propertyPath: m_Name",
        "      value: Guard (2)",
        "      objectReference: {fileID: 0}",
        "    m_RemovedComponents: []",
        f"  m_SourcePrefab: {{fileID: 100100000, guid: {guid('Assets/Prefabs/Guard.prefab')}, type: 3}}",
    ]
    write_unity("Assets/Scenes/World.unity", lines, "DefaultImporter")


def main():
    sub_bt()
    bot_bt()
    bot_fsm()
    talk_dt()
    broken_bt()
    owner_prefab()
    patrol_bt()
    guard_prefab()
    shared_asset()
    world_scene()
    for d in ("Assets/Stubs", "Assets/Game"):
        for cs in (PROJECT / d).glob("*.cs"):
            meta = cs.with_suffix(".cs.meta")
            if not meta.exists():
                meta.write_text(f"fileFormatVersion: 2\nguid: {guid(str(cs.relative_to(PROJECT)))}\n")
    print("fixtures written to", PROJECT)


if __name__ == "__main__":
    main()
