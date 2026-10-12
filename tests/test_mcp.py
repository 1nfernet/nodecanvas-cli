import asyncio
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_notation_is_shared_with_the_skill():
    from nodecanvas_cli.mcp_server import INSTRUCTIONS, NOTATION

    assert NOTATION in (ROOT / "skills/nodecanvas-cli/SKILL.md").read_text(encoding="utf-8")
    assert NOTATION in INSTRUCTIONS


def test_run_cli_reports_exit_code(project):
    from nodecanvas_cli.mcp_server import run_cli

    assert "Bot_BT  [BT, repeat]" in run_cli(str(project), ["show", "Bot_BT"])
    out = run_cli(str(project), ["show", "nope"])
    assert "no graph matches" in out and "(exit code 1)" in out


def _text(result) -> str:
    return "".join(c.text for c in result.content if getattr(c, "type", "") == "text")


def test_mcp_tools_in_memory(project):
    pytest.importorskip("mcp")
    from mcp import Client

    from nodecanvas_cli.mcp_server import build_server

    server = build_server(str(project))
    before = (project / "Assets/Graphs/Bot_BT.asset").read_bytes()

    async def go():
        async with Client(server, raise_exceptions=True) as c:
            tools = {t.name for t in (await c.list_tools()).tools}
            assert {"list_graphs", "show", "apply", "lint", "refactor_rename_member", "why", "owners"} <= tools
            out = _text(await c.call_tool("list_graphs", {}))
            assert "Assets/Graphs/Bot_BT.asset" in out
            out = _text(await c.call_tool("show", {"graph": "Bot_BT"}))
            assert "#5 @Self.Attack($Target)" in out
            ops = [{"op": "set", "node": "9", "path": "_comment", "value": "via mcp"}]
            out = _text(await c.call_tool("apply", {"graph": "Bot_BT", "ops": ops}))
            assert "dry run: nothing written" in out and "via mcp" in out
            assert (project / "Assets/Graphs/Bot_BT.asset").read_bytes() == before
            out = _text(await c.call_tool("apply", {"graph": "Bot_BT", "ops": ops, "dry_run": False}))
            assert "saved Assets/Graphs/Bot_BT.asset" in out
            assert "via mcp" in _text(await c.call_tool("show", {"graph": "Bot_BT"}))

    asyncio.run(go())


def test_mcp_command_without_sdk(ncg, monkeypatch):
    import builtins

    real = builtins.__import__

    def fake(name, *a, **k):
        if name == "mcp.server" or name.startswith("mcp"):
            raise ImportError(name)
        return real(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", fake)
    code, _, err = ncg("mcp")
    assert code == 2 and "nodecanvas-cli[mcp]" in err
