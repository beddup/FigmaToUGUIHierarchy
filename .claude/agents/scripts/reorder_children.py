#!/usr/bin/env python3
"""
Reorder children in a prefab hierarchy for Unity readability while preserving
rendering order for overlapping siblings.

Principles:
1. Don't move children across parents — only reorder within each parent's children array.
2. Container visual bounds = union of all image/text/color descendant bounds.
   For each descendant, renderable bounds come from figma flatNodes: TEXT with content,
   or any node with fills.
3. Overlap between sibling children → dependency constraint that must be preserved.
4. Overlapping siblings keep their Figma relative order (lower effectiveSiblingIndex first).
5. Non-overlapping siblings are sorted by position:
   - parent width > height  → X-first (left-to-right), then Y (top-to-bottom)
   - parent height >= width  → Y-first (top-to-bottom), then X (left-to-right)
6. Effective figmaSiblingIndex for any node (real or synthetic) =
   min( siblingIndex across all image/text/color descendants in figma flatNodes ).
7. Topological sort with a priority heap; all elements are guaranteed to appear exactly once.

Output: the hierarchy with every children array reordered in-place.
"""

import json
import sys
import argparse
from typing import Dict, List, Any, Optional, Tuple
from collections import defaultdict
import heapq


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def load_json(path: str) -> Any:
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


def is_flat_doc(doc: Any) -> bool:
    return isinstance(doc, dict) and doc.get('type') == 'flat'


def find_figma_root(doc: Any) -> Optional[Dict]:
    """Return the root document node from simplified Figma content."""
    if isinstance(doc.get('nodes'), dict):
        for node_content in doc['nodes'].values():
            if isinstance(node_content, dict) and isinstance(node_content.get('document'), dict):
                return node_content['document']
    return None


def build_figma_index(flat_nodes: List[Dict]) -> Dict[str, Dict]:
    return {n['id']: n for n in flat_nodes}


# ---------------------------------------------------------------------------
# Visual area collection
# ---------------------------------------------------------------------------

def collect_visual_areas_from_figma_subtree(
    node_id: str,
    figma_index: Dict[str, Dict],
    flat_nodes: List[Dict]
) -> List[Dict]:
    """
    Walk the figma flatNodes subtree of `node_id` and return bounds of every
    node that produces visual pixels (TEXT with content, or any node with fills).
    """
    if node_id not in figma_index:
        return []

    node = figma_index[node_id]
    areas: List[Dict] = []
    start = node['index']
    end = node['subtreeEndIndex']

    for i in range(start, end + 1):
        n = flat_nodes[i]
        if n.get('type') == 'TEXT' and n.get('text'):
            bounds = n.get('bounds')
            if bounds:
                areas.append(bounds)
        elif n.get('fills') and len(n['fills']) > 0:
            bounds = n.get('bounds')
            if bounds:
                areas.append(bounds)

    return areas


def collect_visual_areas(
    hierarchy_node: Dict,
    figma_index: Dict[str, Dict],
    flat_nodes: List[Dict]
) -> List[Dict]:
    """
    Collect bounds of all visual-pixel-producing nodes belonging to this
    hierarchy node.

    Strategy:
    - Hierarchy leaf (no children) with a figma nodeId → walk the figma subtree
      to capture fills/text from all figma descendants (handles INSTANCE/FRAME
      whose fill-producing children may not have individual hierarchy entries).
    - Hierarchy parent (has children, or synthetic container) → recurse into
      hierarchy children.
    - Additionally check the node's own figma entry for direct fills/text.
    """
    node_id = hierarchy_node.get('nodeId', '')
    hierarchy_children = hierarchy_node.get('children', [])

    if not hierarchy_children and node_id and node_id in figma_index:
        # Leaf hierarchy node — walk figma subtree for complete visual coverage
        return collect_visual_areas_from_figma_subtree(
            node_id, figma_index, flat_nodes
        )

    # Non-leaf or synthetic — walk hierarchy children
    areas: List[Dict] = []

    # Also include the node's own figma data if it's a renderable leaf
    if node_id and node_id in figma_index:
        fm = figma_index[node_id]
        if fm.get('type') == 'TEXT' and fm.get('text'):
            bounds = fm.get('bounds')
            if bounds:
                areas.append(bounds)
        elif fm.get('fills') and len(fm['fills']) > 0:
            bounds = fm.get('bounds')
            if bounds:
                areas.append(bounds)

    for child in hierarchy_children:
        areas.extend(collect_visual_areas(child, figma_index, flat_nodes))

    return areas


def union_bounds(bounds_list: List[Dict]) -> Optional[Dict]:
    """Compute the axis-aligned bounding box of a list of bounds rectangles."""
    if not bounds_list:
        return None
    min_x = min(b['x'] for b in bounds_list)
    min_y = min(b['y'] for b in bounds_list)
    max_x = max(b['x'] + b['width'] for b in bounds_list)
    max_y = max(b['y'] + b['height'] for b in bounds_list)
    return {'x': min_x, 'y': min_y, 'width': max_x - min_x, 'height': max_y - min_y}


def centroid(bounds: Dict) -> Tuple[float, float]:
    """Return (cx, cy) centroid of a bounds rectangle."""
    return (bounds['x'] + bounds['width'] / 2, bounds['y'] + bounds['height'] / 2)


# ---------------------------------------------------------------------------
# Effective figma siblingIndex
# ---------------------------------------------------------------------------

def is_synthetic(node_id: str) -> bool:
    """Return True if the nodeId follows the synthetic container naming pattern."""
    return ':group_' in (node_id or '')


def collect_effective_sibling_index(
    hierarchy_node: Dict,
    figma_index: Dict[str, Dict]
) -> Optional[int]:
    """
    Compute the effective figma siblingIndex used for overlap ordering.

    - Real figma node (nodeId in figma_index, not synthetic):
        → use its own siblingIndex from figma flatNodes.
    - Synthetic container (nodeId like '<parent>:group_<Name>'):
        → min() of effectiveSiblingIndex across all hierarchy children.
    - Neither:
        → min() of hierarchy children (fallback).

    Returns None when no figma siblingIndex can be determined.
    """
    node_id = hierarchy_node.get('nodeId', '')

    # Real figma node — use its own siblingIndex directly
    if node_id and not is_synthetic(node_id) and node_id in figma_index:
        si = figma_index[node_id].get('siblingIndex')
        if si is not None:
            return si

    # Synthetic or unknown — aggregate from hierarchy children
    best: Optional[int] = None
    for child in hierarchy_node.get('children', []):
        child_si = collect_effective_sibling_index(child, figma_index)
        if child_si is not None:
            if best is None or child_si < best:
                best = child_si

    return best


# ---------------------------------------------------------------------------
# Overlap check
# ---------------------------------------------------------------------------

def rectangles_overlap(a: Dict, b: Dict) -> bool:
    """True if two axis-aligned rectangles overlap in both axes."""
    ax1, ay1 = a['x'], a['y']
    ax2, ay2 = a['x'] + a['width'], a['y'] + a['height']
    bx1, by1 = b['x'], b['y']
    bx2, by2 = b['x'] + b['width'], b['y'] + b['height']
    return ax1 < bx2 and ax2 > bx1 and ay1 < by2 and ay2 > by1


def has_any_overlap(areas_a: List[Dict], areas_b: List[Dict]) -> bool:
    """True if any rectangle in areas_a overlaps any rectangle in areas_b."""
    if not areas_a or not areas_b:
        return False
    for ra in areas_a:
        for rb in areas_b:
            if rectangles_overlap(ra, rb):
                return True
    return False


# ---------------------------------------------------------------------------
# Main reorder logic
# ---------------------------------------------------------------------------

def reorder_children(hierarchy: Dict, figma_index: Dict[str, Dict], flat_nodes: List[Dict]) -> Dict:
    """
    Reorder every children array in the hierarchy tree.

    Walks the tree depth-first.  For each node with >= 2 children:
      - Compute visual areas and effective siblingIndex for each child.
      - Determine which sibling pairs overlap → build dependency edges.
      - Topological sort with position-based heap priority.

    The hierarchy is mutated in place and also returned.
    """

    # ------------------------------------------------------------------
    # Pre-compute per-node data (walk the hierarchy tree once)
    # ------------------------------------------------------------------
    # key = id(node) (Python object identity — hierarchy nodes are unique objects)
    node_cache: Dict[int, Dict] = {}

    def precompute(node: Dict) -> None:
        nid = id(node)
        node_cache[nid] = {
            'visualAreas': collect_visual_areas(node, figma_index, flat_nodes),
            'visualBounds': None,          # filled below
            'effectiveSiblingIndex': collect_effective_sibling_index(node, figma_index),
            'nodeId': node.get('nodeId', ''),
            'name': node.get('gameObjectName', node.get('nodeName', '')),
        }
        node_cache[nid]['visualBounds'] = union_bounds(
            node_cache[nid]['visualAreas']
        )
        for child in node.get('children', []):
            precompute(child)

    precompute(hierarchy)

    # ------------------------------------------------------------------
    # Reorder children of a single node (recursive)
    # ------------------------------------------------------------------

    def reorder_one(node: Dict) -> None:
        children: List[Dict] = node.get('children', [])
        if len(children) < 2:
            # Nothing to reorder, but recurse into the single child
            for child in children:
                reorder_one(child)
            return

        # -- Parent visual bounds (for aspect-ratio based sort axis) --
        parent_vb = node_cache[id(node)]['visualBounds']

        if parent_vb and parent_vb['width'] > parent_vb['height']:
            x_first = True   # wide parent → sort left-to-right
        else:
            x_first = False  # tall or square parent → sort top-to-bottom

        # -- Build dependency graph from overlapping sibling pairs --
        # edges[src_id] = {dep_id, ...}  means src must appear before dep
        edges: Dict[int, set] = defaultdict(set)
        indegree: Dict[int, int] = defaultdict(int)

        for i in range(len(children)):
            for j in range(i + 1, len(children)):
                ci = children[i]
                cj = children[j]
                ci_data = node_cache.get(id(ci), {})
                cj_data = node_cache.get(id(cj), {})

                if has_any_overlap(
                    ci_data.get('visualAreas', []),
                    cj_data.get('visualAreas', []),
                ):
                    # Overlap detected — must preserve Figma relative order
                    si_i = ci_data.get('effectiveSiblingIndex')
                    si_j = cj_data.get('effectiveSiblingIndex')

                    if si_i is not None and si_j is not None:
                        if si_i < si_j:
                            edges[id(ci)].add(id(cj))
                            indegree[id(cj)] += 1
                        elif si_j < si_i:
                            edges[id(cj)].add(id(ci))
                            indegree[id(ci)] += 1
                        # si_i == si_j: rare, no constraint
                    elif si_i is not None:
                        # Only ci has index — treat cj as later (synthetic container)
                        edges[id(ci)].add(id(cj))
                        indegree[id(cj)] += 1
                    elif si_j is not None:
                        edges[id(cj)].add(id(ci))
                        indegree[id(ci)] += 1
                    else:
                        # Both None — keep original order, emit warning
                        print(
                            f"WARNING: overlapping siblings with no figmaSiblingIndex: "
                            f"'{ci_data.get('name','?')}' and '{cj_data.get('name','?')}' "
                            f"under parent '{node_cache[id(node)].get('name','?')}' "
                            f"— keeping original relative order.",
                            file=sys.stderr,
                        )
                        edges[id(ci)].add(id(cj))
                        indegree[id(cj)] += 1

        # -- Build a stable sort key from spatial position --
        def sort_key(child: Dict) -> Tuple[float, float]:
            vb = node_cache.get(id(child), {}).get('visualBounds')
            if vb:
                cx, cy = centroid(vb)
                if x_first:
                    return (cx, cy)
                else:
                    return (cy, cx)
            # No visual bounds → push to end
            return (float('inf'), float('inf'))

        # -- Topological sort via priority heap --
        child_by_oid: Dict[int, Dict] = {id(c): c for c in children}

        heap: List[Tuple[Tuple[float, float], int, Dict]] = []
        for c in children:
            if indegree[id(c)] == 0:
                heapq.heappush(heap, (sort_key(c), id(c), c))

        ordered: List[Dict] = []
        while heap:
            _, _, child = heapq.heappop(heap)
            ordered.append(child)
            for dep_oid in edges.get(id(child), set()):
                indegree[dep_oid] -= 1
                if indegree[dep_oid] == 0:
                    dep_child = child_by_oid[dep_oid]
                    heapq.heappush(heap, (sort_key(dep_child), dep_oid, dep_child))

        # -- Safety check --
        if len(ordered) != len(children):
            print(
                f"ERROR: reorder lost children under parent "
                f"'{node_cache[id(node)].get('name','?')}' "
                f"({node_cache[id(node)].get('nodeId','?')}): "
                f"{len(ordered)} != {len(children)}. "
                f"Leaving original order unchanged.",
                file=sys.stderr,
            )
            # Do NOT update children — keep original order as safe fallback
        else:
            node['children'] = ordered

        # -- Recurse into (now ordered) children --
        for child in node.get('children', []):
            reorder_one(child)

    reorder_one(hierarchy)
    return hierarchy


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            'Reorder children in a prefab hierarchy for Unity readability '
            'while preserving rendering order for overlapping siblings.'
        )
    )
    parser.add_argument(
        'hierarchy',
        help='Path to the prefab hierarchy JSON file to reorder',
    )
    parser.add_argument(
        '-f', '--figma',
        required=True,
        help='Path to the simplified Figma content flat JSON file',
    )
    parser.add_argument(
        '-o', '--output',
        required=True,
        help='Path to save the reordered hierarchy JSON',
    )
    args = parser.parse_args()

    # Load
    hierarchy = load_json(args.hierarchy)
    figma_content = load_json(args.figma)

    figma_root = find_figma_root(figma_content)
    if figma_root is None or not is_flat_doc(figma_root):
        print(
            "ERROR: Figma content is not in expected flat format "
            "(root must have type='flat').",
            file=sys.stderr,
        )
        sys.exit(1)

    flat_nodes = figma_root['flatNodes']
    figma_index = build_figma_index(flat_nodes)

    # Reorder
    reorder_children(hierarchy, figma_index, flat_nodes)

    # Save
    with open(args.output, 'w', encoding='utf-8') as f:
        json.dump(hierarchy, f, indent=2, ensure_ascii=False)
    print(f"Reordered hierarchy saved to: {args.output}")


if __name__ == '__main__':
    main()
