"""
Tree model + the two in-memory views the router needs (spec §1).

  - flat INDEX  {node_id: Node}              -> O(1) payload fetch
  - flat ROUTING LIST [{node_id,title,summary,depth,parent_id}]  -> routing input only

`details`, `guide_link`, `cross_refs`, `children` are payload — never in the routing list.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional


@dataclass
class Node:
    node_id: str
    title: str
    summary: str
    details: Optional[str]
    guide_link: Optional[dict]
    parent_id: Optional[str]
    depth: int
    coming_soon: bool
    cross_refs: list[str] = field(default_factory=list)
    child_ids: list[str] = field(default_factory=list)

    @property
    def is_leaf(self) -> bool:
        return not self.child_ids


class GuideTree:
    def __init__(self, root_raw: dict) -> None:
        self.nodes: dict[str, Node] = {}
        self._root_id = root_raw["node_id"]
        self._index(root_raw, None)

    def _index(self, raw: dict, parent_id: Optional[str]) -> None:
        children = raw.get("children") or []
        node = Node(
            node_id=raw["node_id"],
            title=raw["title"],
            summary=raw.get("summary") or "",
            details=raw.get("details"),
            guide_link=raw.get("guide_link"),
            parent_id=raw.get("parent_id", parent_id),
            depth=int(raw.get("depth", 0)),
            coming_soon=bool(raw.get("coming_soon", False)),
            cross_refs=list(raw.get("cross_refs") or []),
            child_ids=[c["node_id"] for c in children],
        )
        if node.node_id in self.nodes:
            raise ValueError(f"Duplicate node_id: {node.node_id!r}")
        self.nodes[node.node_id] = node
        for c in children:
            self._index(c, node.node_id)

    @classmethod
    def from_file(cls, path: str | Path) -> "GuideTree":
        # utf-8-sig drops a BOM; lstrip removes stray zero-width spaces/whitespace
        # some editors paste before the first '{'.
        text = Path(path).read_text(encoding="utf-8-sig").lstrip("﻿​‌‍ \t\r\n")
        data = json.loads(text)
        root_raw = data["root"] if "root" in data and "node_id" not in data else data
        return cls(root_raw)

    # ── lookup ─────────────────────────────────────────────────────
    @property
    def root(self) -> Node:
        return self.nodes[self._root_id]

    def get(self, node_id: str) -> Optional[Node]:
        return self.nodes.get(node_id)

    def children(self, node_id: str) -> list[Node]:
        n = self.nodes.get(node_id)
        return [self.nodes[c] for c in (n.child_ids if n else []) if c in self.nodes]

    def resolve_cross_refs(self, node_id: str) -> list[Node]:
        n = self.nodes.get(node_id)
        return [self.nodes[r] for r in (n.cross_refs if n else []) if r in self.nodes]

    # ── views ──────────────────────────────────────────────────────
    def routing_list(self) -> list[dict]:
        """Flat routing input — 5 nav fields per node, every node included."""
        return [
            {
                "node_id": n.node_id,
                "title": n.title,
                "summary": n.summary,
                "depth": n.depth,
                "parent_id": n.parent_id,
            }
            for n in self.nodes.values()
        ]

    def breadcrumb(self, node_id: str) -> list[dict]:
        """Path from the top module down to this node (spec §4.1), as
        [{node_id, title}, ...] so the UI can render a clickable trail.
        Root is omitted (it has no parent)."""
        chain: list[dict] = []
        cur = self.nodes.get(node_id)
        while cur is not None and cur.parent_id is not None:
            chain.append({"node_id": cur.node_id, "title": cur.title})
            cur = self.nodes.get(cur.parent_id)
        chain.reverse()
        return chain

    # ── validation ─────────────────────────────────────────────────
    def validate(self) -> list[str]:
        problems: list[str] = []
        for nid, node in self.nodes.items():
            for ref in node.cross_refs:
                if ref not in self.nodes:
                    problems.append(f"{nid}: cross_ref -> unknown {ref!r}")
            if node.parent_id and node.parent_id not in self.nodes:
                problems.append(f"{nid}: parent_id -> unknown {node.parent_id!r}")
        return problems

    def __len__(self) -> int:
        return len(self.nodes)
