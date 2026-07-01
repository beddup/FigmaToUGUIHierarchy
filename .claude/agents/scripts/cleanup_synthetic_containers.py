#!/usr/bin/env python3
"""
Clean up synthetic containers in a prefab hierarchy that have zero or one child.

A node is "synthetic" when it was NOT part of the original Figma design — it was
introduced by the hierarchy-generation pipeline.  Two criteria (OR):
  - nodeId contains the literal ":group_"
  - nodeId is not found anywhere in the simplified Figma content

Cleanup rules (applied iteratively until the tree is stable):
  - 0 children → delete the synthetic container
  - 1 child   → promote that single child into the synthetic container's parent,
                 replacing the synthetic container
  - 2+ children → untouched (the grouping is meaningful)

Only ``gameObjectCategory == "container"`` nodes are candidates for cleanup.

Usage:
  python3 cleanup_synthetic_containers.py \\
    --hierarchy <prefab_hierarchy.json> \\
    --figma     <simplified_figma_content.json> \\
    --output    <output_path.json>
"""

import argparse
import json
import sys
import os
from typing import Any, Dict, List, Optional, Set


# ═══════════════════════════════════════════════════════════════════════════════
#  Helpers
# ═══════════════════════════════════════════════════════════════════════════════

def load_json(path: str) -> Any:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path: str, data: Any) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def collect_figma_node_ids(figma_data: Dict) -> Set[str]:
    """Extract every nodeId from a simplified (flat) Figma content document."""
    ids: Set[str] = set()
    nodes_section = figma_data.get("nodes", {})
    for node_content in nodes_section.values():
        doc = node_content.get("document", {})
        flat_nodes = doc.get("flatNodes", [])
        for fn in flat_nodes:
            if isinstance(fn, dict) and "id" in fn:
                ids.add(str(fn["id"]))
    return ids


def is_synthetic(node_id: str, figma_ids: Set[str]) -> bool:
    """Return True when *node_id* does not originate from the Figma source."""
    if ":group_" in node_id:
        return True
    if node_id not in figma_ids:
        return True
    return False


# ═══════════════════════════════════════════════════════════════════════════════
#  Core cleanup
# ═══════════════════════════════════════════════════════════════════════════════

def cleanup_node(node: Dict, figma_ids: Set[str]) -> tuple:
    """
    Recursively clean up synthetic containers below *node*.

    Returns (cleaned_node, changed) where *changed* is True if any
    descendant was removed or restructured.
    """
    changed = False
    if "children" not in node:
        return node, changed

    new_children: List[Dict] = []

    for child in node["children"]:
        # Recursively process the child first (bottom-up)
        child, child_changed = cleanup_node(child, figma_ids)
        if child_changed:
            changed = True

        # Only container nodes can be synthetic cleanup targets
        if child.get("gameObjectCategory") != "container":
            new_children.append(child)
            continue

        child_id = str(child.get("nodeId", ""))
        if not is_synthetic(child_id, figma_ids):
            new_children.append(child)
            continue

        # ── synthetic container ──
        grandchildren = child.get("children", [])

        if len(grandchildren) == 0:
            # Remove empty synthetic container
            changed = True
            continue

        elif len(grandchildren) == 1:
            # Promote the single child, removing the pointless wrapper
            changed = True
            new_children.append(grandchildren[0])
            continue

        else:
            # Meaningful grouping — keep it
            new_children.append(child)

    node["children"] = new_children
    return node, changed


def cleanup_loop(root: Dict, figma_ids: Set[str]) -> int:
    """
    Run cleanup_node repeatedly until the tree stabilises.
    Returns the number of iterations performed.
    """
    iteration = 0
    while True:
        root, changed = cleanup_node(root, figma_ids)
        iteration += 1
        if not changed:
            break
    return iteration


# ═══════════════════════════════════════════════════════════════════════════════
#  Main
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="Remove / collapse synthetic containers with ≤1 child"
    )
    parser.add_argument("--hierarchy", required=True,
                        help="Path to the prefab hierarchy JSON")
    parser.add_argument("--figma", required=True,
                        help="Path to the simplified Figma content JSON")
    parser.add_argument("--output", required=True,
                        help="Output path for the cleaned hierarchy JSON")
    args = parser.parse_args()

    # ── load ──────────────────────────────────────────────────────────
    hierarchy = load_json(args.hierarchy)
    figma_data = load_json(args.figma)

    figma_ids = collect_figma_node_ids(figma_data)
    print(f"Collected {len(figma_ids)} node IDs from Figma content.", file=sys.stderr)

    # ── count synthetic containers before cleanup ─────────────────────
    def count_nodes(n: Dict) -> int:
        return 1 + sum(count_nodes(c) for c in n.get("children", []))
    total_before = count_nodes(hierarchy)
    print(f"Nodes before cleanup: {total_before}", file=sys.stderr)

    # ── clean ─────────────────────────────────────────────────────────
    iterations = cleanup_loop(hierarchy, figma_ids)
    total_after = count_nodes(hierarchy)

    removed = total_before - total_after
    print(f"Nodes after  cleanup: {total_after}  (removed {removed})", file=sys.stderr)
    print(f"Iterations until stable: {iterations}", file=sys.stderr)

    # ── save ──────────────────────────────────────────────────────────
    save_json(args.output, hierarchy)
    print(f"Saved: {args.output}", file=sys.stderr)


if __name__ == "__main__":
    main()
