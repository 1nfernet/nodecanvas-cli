"""Node placement for edited graphs.

Only nodes an edit created or moved are positioned; existing nodes move only when they have to
make room (siblings to the right of an insertion, branches that would overlap). Rules:

* every coordinate set here lies on the editor grid (``Layout.grid``);
* a Behaviour Tree child sits ``level_gap`` below its parent;
* nodes on one row are at least ``sibling_gap`` apart, and siblings keep a left-to-right order that
  matches their child order (the NodeCanvas editor re-sorts children by x when a node is dragged).
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional, Set

from .config import Layout
from .model import Graph

MAX_PASSES = 200


class Layouter:
    def __init__(self, g: Graph, cfg: Layout):
        self.g = g
        self.cfg = cfg

    # ------------------------------------------------------------------ helpers

    def snap(self, v: float) -> float:
        return float(round(v / self.cfg.grid) * self.cfg.grid)

    def snap_up(self, v: float) -> float:
        return float(math.ceil(v / self.cfg.grid - 1e-9) * self.cfg.grid)

    @staticmethod
    def pos(n: dict) -> Optional[tuple]:
        p = n.get("_position")
        if not isinstance(p, dict):
            return None
        return float(p.get("x", 0.0)), float(p.get("y", 0.0))

    def set(self, n: dict, x: float, y: float) -> None:
        from .ops import set_position

        set_position(n, x, y)

    def subtree(self, n: dict) -> List[dict]:
        out, seen, stack = [], set(), [n]
        while stack:
            m = stack.pop()
            if id(m) in seen:
                continue
            seen.add(id(m))
            out.append(m)
            stack.extend(self.g.children(m))
        return out

    def translate(self, roots: List[dict], dx: float, dy: float, keep: Set[int] = frozenset()) -> None:
        """Shift the subtrees of ``roots`` by (dx, dy); nodes whose id is in ``keep`` stay put."""
        seen: Set[int] = set(keep)
        for r in roots:
            for m in self.subtree(r):
                if id(m) in seen:
                    continue
                seen.add(id(m))
                p = self.pos(m) or (0.0, 0.0)
                self.set(m, p[0] + dx, p[1] + dy)

    def parent(self, n: dict) -> Optional[dict]:
        ps = self.g.parents(n)
        return ps[0] if ps else None

    def ancestors(self, n: dict) -> List[dict]:
        out, cur, guard = [], self.parent(n), 0
        while cur is not None and guard < 10000:
            out.append(cur)
            cur = self.parent(cur)
            guard += 1
        return out

    # ------------------------------------------------------------------ BT

    def slot_parent(self, n: dict, positioned: Set[int]) -> Optional[dict]:
        """The parent ``n`` gets its slot under: with several parents (a Merge), the one where it has
        the most already positioned siblings, so its x satisfies the strictest child order."""
        ps = [p for p in self.g.parents(n) if self.pos(p) is not None] or self.g.parents(n)
        if len(ps) < 2:
            return ps[0] if ps else None

        def siblings(p: dict) -> int:
            return sum(1 for k in self.g.children(p) if k is not n and id(k) in positioned)

        return max(ps, key=siblings)  # max() keeps the first on ties

    def place(self, n: dict, positioned: Set[int]) -> None:
        """Put ``n`` (and its subtree, keeping its internal layout) into its slot under its parent.

        A node with several parents goes ``level_gap`` below the lowest of them."""
        gap, p = self.cfg.sibling_gap, self.slot_parent(n, positioned)
        if p is None:
            if self.pos(n) is None:
                self.set(n, 0.0, 0.0)
                self.place_new_children(n)
            return
        px, py = self.pos(p) or (0.0, 0.0)
        kids = self.g.children(p)
        i = next(k for k, c in enumerate(kids) if c is n)
        left = next(
            (self.pos(kids[j])[0] for j in range(i - 1, -1, -1) if id(kids[j]) in positioned and self.pos(kids[j])),
            None,
        )
        right = next(
            (self.pos(kids[j])[0] for j in range(i + 1, len(kids)) if id(kids[j]) in positioned and self.pos(kids[j])),
            None,
        )
        if left is not None and right is not None and right - left >= 2 * gap:
            x = (left + right) / 2
        elif left is not None:
            x = left + gap
        elif right is not None:
            x = right - gap
        else:
            x = px - gap * (len(kids) - 1) / 2 + i * gap
        py = max([py] + [q[1] for q in map(self.pos, self.g.parents(n)) if q is not None])
        x, y = self.snap(x), self.snap(py + self.cfg.level_gap)
        cur = self.pos(n)
        if cur is None:
            self.set(n, x, y)
            self.place_new_children(n)
        else:
            self.translate([n], x - cur[0], y - cur[1])
        positioned.add(id(n))
        self.keep_sibling_order(p)

    def place_new_children(self, n: dict) -> None:
        kids = [k for k in self.g.children(n) if self.pos(k) is None]
        if not kids:
            return
        x0, y0 = self.pos(n)
        start = x0 - self.cfg.sibling_gap * (len(kids) - 1) / 2
        for i, k in enumerate(kids):
            self.set(k, self.snap(start + i * self.cfg.sibling_gap), self.snap(y0 + self.cfg.level_gap))
            self.place_new_children(k)

    def keep_sibling_order(self, parent: dict) -> None:
        kids = self.g.children(parent)
        for i in range(1, len(kids)):
            a, b = self.pos(kids[i - 1]), self.pos(kids[i])
            if a is None or b is None:
                continue
            need = a[0] + self.cfg.sibling_gap - b[0]
            if need > 0:
                self.translate(kids[i:], self.snap_up(need), 0.0)

    def resolve(self, n: dict) -> None:
        """Push branches apart until no node of ``n``'s subtree overlaps a node outside it."""
        gap, half = self.cfg.sibling_gap, self.cfg.level_gap / 2
        for _ in range(MAX_PASSES):
            inside = {id(m) for m in self.subtree(n)}
            hit = None
            for m in self.subtree(n):
                mp = self.pos(m)
                if mp is None:
                    continue
                for o in self.g.nodes:
                    if id(o) in inside:
                        continue
                    op_ = self.pos(o)
                    if op_ and abs(op_[1] - mp[1]) < half and abs(op_[0] - mp[0]) < gap:
                        hit = (m, o, mp, op_)
                        break
                if hit:
                    break
            if hit is None:
                return
            m, o, mp, op_ = hit
            self.separate(n, o, mp, op_)

    def separate(self, n: dict, other: dict, mp: tuple, op_: tuple) -> None:
        """Shift one side of the overlap right. With shared children (Merge) a branch can reach the
        other side too, so the moved set always excludes the side that stays: every call makes progress."""
        gap = self.cfg.sibling_gap
        mine = {id(m) for m in self.subtree(n)}
        n_path = [n] + self.ancestors(n)
        n_ids = {id(x): k for k, x in enumerate(n_path)}
        branch_o, cur = other, self.parent(other)
        while cur is not None and id(cur) not in n_ids:
            branch_o, cur = cur, self.parent(cur)
        if cur is None:
            # unrelated tree (another root or an orphan): move our own top branch right
            self.move_mine([n_path[-1]], n, mine, other, self.snap_up(op_[0] + gap - mp[0]))
            return
        common = cur
        branch_n = n_path[n_ids[id(common)] - 1]
        kids = self.g.children(common)
        i_n = next(k for k, c in enumerate(kids) if c is branch_n)
        i_o = next(k for k, c in enumerate(kids) if c is branch_o)
        if i_o > i_n:
            self.translate(kids[i_o:], self.snap_up(mp[0] + gap - op_[0]), 0.0, keep=mine)
        else:
            self.move_mine(kids[i_n:], n, mine, branch_o, self.snap_up(op_[0] + gap - mp[0]))

    def move_mine(self, roots: List[dict], n: dict, mine: Set[int], stay: dict, dx: float) -> None:
        """Shift ``roots`` and ``n``'s whole subtree by ``dx``, leaving ``stay``'s subtree in place."""
        keep = {id(m) for m in self.subtree(stay)} - mine
        self.translate(roots + [n], dx, 0.0, keep=keep)

    def relayout(self, root: dict) -> None:
        """Explicit tidy layout of ``root``'s subtree: leaves get consecutive slots, parents centre.

        The root itself is snapped to the grid and, when it has a parent, put ``level_gap`` below it.
        """
        rp = self.pos(root) or (0.0, 0.0)
        parent = self.parent(root)
        pp = self.pos(parent) if parent is not None else None
        rp = (self.snap(rp[0]), self.snap(pp[1] + self.cfg.level_gap) if pp else self.snap(rp[1]))
        slots: Dict[int, float] = {}
        counter = [0]
        seen: Set[int] = set()

        def visit(n: dict, depth: int) -> float:
            seen.add(id(n))
            kids = [k for k in self.g.children(n) if id(k) not in seen]
            if not kids:
                x = counter[0] * self.cfg.sibling_gap
                counter[0] += 1
            else:
                xs = [visit(k, depth + 1) for k in kids]
                x = (xs[0] + xs[-1]) / 2
            slots[id(n)] = x
            self.set(n, x, rp[1] + depth * self.cfg.level_gap)
            return x

        visit(root, 0)
        shift = rp[0] - slots[id(root)]
        for m in self.subtree(root):
            p = self.pos(m)
            self.set(m, self.snap(p[0] + shift), self.snap(p[1]))
        self.resolve(root)

    # ------------------------------------------------------------------ FSM / DT

    def place_flat(self, n: dict) -> None:
        gap, half = self.cfg.sibling_gap, self.cfg.level_gap / 2
        src = next((self.pos(p) for p in self.g.parents(n) if self.pos(p)), None)
        others = [self.pos(o) for o in self.g.nodes if o is not n and self.pos(o)]
        if src is not None:
            x, y = src[0] + gap, src[1]
        elif others:
            x, y = max(o[0] for o in others) + gap, min(o[1] for o in others)
        else:
            x, y = 0.0, 0.0
        x, y = self.snap(x), self.snap(y)
        for _ in range(MAX_PASSES):
            if not any(abs(o[0] - x) < gap and abs(o[1] - y) < half for o in others):
                break
            y += self.cfg.level_gap
        self.set(n, x, y)
