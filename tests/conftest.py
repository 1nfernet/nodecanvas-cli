import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

FIXTURE_PROJECT = ROOT / "tests" / "fixtures" / "project"
SNAPSHOTS = ROOT / "tests" / "snapshots"


@pytest.fixture(autouse=True)
def _isolated_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("NODECANVAS_CLI_CACHE_DIR", str(tmp_path / "cache"))


@pytest.fixture
def project(tmp_path) -> Path:
    dst = tmp_path / "project"
    shutil.copytree(FIXTURE_PROJECT, dst)
    return dst


@pytest.fixture
def ncg(project, capsys, monkeypatch):
    """Run the CLI inside the copied fixture project; returns (exit code, stdout, stderr)."""
    from nodecanvas_cli.cli import main

    monkeypatch.chdir(project)

    def run(*args):
        code = main([str(a) for a in args])
        out = capsys.readouterr()
        return code, out.out, out.err

    return run
