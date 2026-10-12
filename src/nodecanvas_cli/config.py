"""Optional per-project settings from ``nodecanvas-cli.json`` in the Unity project root."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

CONFIG_NAME = "nodecanvas-cli.json"


class ConfigError(Exception):
    pass


@dataclass
class Layout:
    grid: float = 20.0  # NodeCanvas editor grid (GraphEditor.GRID_SIZE)
    level_gap: float = 200.0  # parent -> child, 10 grid cells
    sibling_gap: float = 300.0  # minimum horizontal distance between nodes on one row


@dataclass
class Scaffold:
    comments: bool = True  # TODO comments in generated method bodies
    indent: str = ""  # "" = detect from neighbouring .cs files (default 4 spaces)


@dataclass
class Config:
    layout: Layout
    scaffold: Scaffold = None


def load(root: Path) -> Config:
    path = root / CONFIG_NAME
    layout = Layout()
    scaffold = Scaffold()
    if path.exists():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except ValueError as e:
            raise ConfigError(f"{path}: invalid JSON: {e}") from e
        for k, v in (raw.get("layout") or {}).items():
            if not hasattr(layout, k):
                raise ConfigError(f"{path}: unknown layout setting {k!r}")
            if not isinstance(v, (int, float)) or v <= 0:
                raise ConfigError(f"{path}: layout.{k} must be a positive number")
            setattr(layout, k, float(v))
        for k, v in (raw.get("scaffold") or {}).items():
            if k == "comments" and isinstance(v, bool):
                scaffold.comments = v
            elif k == "indent" and isinstance(v, (int, str)):
                scaffold.indent = " " * v if isinstance(v, int) else v
            else:
                raise ConfigError(f"{path}: bad scaffold setting {k!r}")
    return Config(layout, scaffold)
