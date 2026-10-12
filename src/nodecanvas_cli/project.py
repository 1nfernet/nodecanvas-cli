"""Unity project discovery: root lookup, GUID map, graph container scan, mtime cache."""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from . import __version__

GRAPH_MARKERS = (b"_serializedGraph: '", b"_boundGraphSerialization: '")
CONTAINER_SUFFIXES = (".asset", ".prefab", ".unity")
_GUID_RE = re.compile(rb"^guid: ([0-9a-f]{32})", re.M)


class ProjectError(Exception):
    pass


def find_project_root(start: Optional[Path] = None) -> Path:
    cur = (start or Path.cwd()).resolve()
    for p in [cur, *cur.parents]:
        if (p / "Assets").is_dir() and (p / "ProjectSettings").is_dir():
            return p
    raise ProjectError(f"no Unity project (Assets/ + ProjectSettings/) found at or above {cur}")


class Cache:
    """Per-project JSON cache keyed by file path, invalidated by (mtime, size)."""

    def __init__(self, root: Path, name: str):
        base = os.environ.get("NODECANVAS_CLI_CACHE_DIR") or os.path.join(
            os.environ.get("XDG_CACHE_HOME") or os.path.join(Path.home(), ".cache"), "nodecanvas-cli"
        )
        digest = hashlib.sha1(str(root).encode()).hexdigest()[:16]
        self.path = Path(base) / digest / f"{name}.json"
        self.data: Dict[str, list] = {}
        self.dirty = False
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if raw.get("version") == __version__:
                self.data = raw.get("entries", {})
        except (OSError, ValueError):
            pass

    def get(self, key: str, stat: os.stat_result):
        entry = self.data.get(key)
        if entry and entry[0] == stat.st_mtime_ns and entry[1] == stat.st_size:
            return entry[2]
        return None

    def put(self, key: str, stat: os.stat_result, value) -> None:
        self.data[key] = [stat.st_mtime_ns, stat.st_size, value]
        self.dirty = True

    def prune(self, keep: Iterable[str]) -> None:
        keep = set(keep)
        for k in list(self.data):
            if k not in keep:
                del self.data[k]
                self.dirty = True

    def save(self) -> None:
        if not self.dirty or os.environ.get("NODECANVAS_CLI_NO_CACHE"):
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"version": __version__, "entries": self.data}), encoding="utf-8")
            tmp.replace(self.path)
        except OSError:
            pass


def _walk(root: Path, top: str, suffixes) -> Iterable[Path]:
    base = root / top
    if not base.is_dir():
        return
    for dirpath, dirnames, filenames in os.walk(base, followlinks=True):
        dirnames[:] = [d for d in dirnames if not d.startswith(".") and not d.endswith("~")]
        for f in filenames:
            if f.endswith(suffixes):
                yield Path(dirpath) / f


def _search_tops(root: Path) -> List[str]:
    tops = ["Assets"]
    pk = root / "Packages"
    if pk.is_dir():
        tops += [f"Packages/{d.name}" for d in sorted(pk.iterdir()) if d.is_dir() and not d.name.startswith(".")]
    return tops


class Project:
    def __init__(self, root: Path):
        self.root = root
        self._guid_to_path: Optional[Dict[str, str]] = None
        self._containers: Optional[List[str]] = None

    def rel(self, p: Path) -> str:
        try:
            return p.resolve().relative_to(self.root).as_posix()
        except ValueError:
            return p.as_posix()

    def abs(self, rel: str) -> Path:
        return self.root / rel

    def iter_files(self, suffixes) -> Iterable[Path]:
        for top in _search_tops(self.root):
            yield from _walk(self.root, top, suffixes)

    @property
    def guid_to_path(self) -> Dict[str, str]:
        if self._guid_to_path is None:
            cache = Cache(self.root, "guids")
            result: Dict[str, str] = {}
            seen = []
            for meta in self.iter_files((".meta",)):
                key = self.rel(meta)
                seen.append(key)
                st = meta.stat()
                guid = cache.get(key, st)
                if guid is None:
                    with open(meta, "rb") as fh:
                        m = _GUID_RE.search(fh.read(512))
                    guid = m.group(1).decode() if m else ""
                    cache.put(key, st, guid)
                if guid:
                    result[guid] = key[:-5]
            cache.prune(seen)
            cache.save()
            self._guid_to_path = result
        return self._guid_to_path

    def path_to_guid(self, rel: str) -> Optional[str]:
        meta = self.abs(rel + ".meta")
        try:
            m = _GUID_RE.search(meta.read_bytes()[:512])
        except OSError:
            return None
        return m.group(1).decode() if m else None

    @property
    def containers(self) -> List[str]:
        """Project-relative paths of files that hold at least one serialized graph."""
        if self._containers is None:
            cache = Cache(self.root, "containers")
            found, seen = [], []
            for f in self.iter_files(CONTAINER_SUFFIXES):
                key = self.rel(f)
                seen.append(key)
                st = f.stat()
                has = cache.get(key, st)
                if has is None:
                    with open(f, "rb") as fh:
                        data = fh.read()
                    has = any(m in data for m in GRAPH_MARKERS)
                    cache.put(key, st, has)
                if has:
                    found.append(key)
            cache.prune(seen)
            cache.save()
            self._containers = sorted(found)
        return self._containers
