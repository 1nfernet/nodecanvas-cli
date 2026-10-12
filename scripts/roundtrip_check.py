#!/usr/bin/env python3
"""Load and re-store every graph of a Unity project; report files that change."""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
from nodecanvas_cli.container import UnityFile
from nodecanvas_cli.project import Project, find_project_root

proj = Project(find_project_root(pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else None))
ok = total = 0
for rel in proj.containers:
    f = UnityFile(proj.abs(rel), rel)
    original = list(f.lines)
    slots = f.slots()
    for slot in slots:
        total += 1
        g = f.load(slot)
        f.store(g)
    if f.lines == original:
        ok += len(slots)
    else:
        for i, (a, b) in enumerate(zip(original, f.lines)):
            if a != b:
                print(rel, "line", i + 1, "\n O:", a[:150], "\n N:", b[:150])
                break
        else:
            print(rel, "line count", len(original), len(f.lines))
print(f"{ok}/{total} graphs round-trip identically")
