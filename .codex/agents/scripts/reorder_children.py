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
4. Overlapping siblings: compare the figma siblingIndex of the *specific* visual areas
   that overlap. If all overlapping area pairs agree on direction → apply it.
   If pairs disagree (one says A→B, another says B→A) → the grouping makes the
   render order impossible to satisfy with sibling reorder alone → warn and keep
   original order so the agent can revise the grouping.
5. Non-overlapping siblings are sorted by position:
   - parent width > height  → X-first (left-to-right), then Y (top-to-bottom)
   - parent height >= width  → Y-first (top-to-bottom), then X (left-to-right)
6. Topological sort with a priority heap, tie-broken by original child index for
   deterministic output.
"""

import json
import sys
import argparse
from typing import Dict, List, Any, Optional, Tuple, Union
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
#
# Each visual area is a dict:
#   {
#       'bounds': {'x','y','width','height'},
#       'parentId': str | None,
#       'siblingIndex': int | None
#   }
#
# parentId and siblingIndex refer to the specific flatNodes entry that produced
# the bounds.  siblingIndex values are only comparable when parentId matches.
# ---------------------------------------------------------------------------

def collect_visual_areas_from_figma_subtree(
    node_id: str,
    figma_index: Dict[str, Dict],
    flat_nodes: List[Dict]
) -> List[Dict]:
    """
    Walk the figma flatNodes subtree of `node_id` and return one area entry
    for every node that produces visual pixels (TEXT with content, or any node
    with fills).  Each entry carries the source node's figma siblingIndex.
    """
    if node_id not in figma_index:
        return []

    node = figma_index[node_id]
    areas: List[Dict] = []
    start = node['index']
    end = node['subtreeEndIndex']

    for i in range(start, end + 1):
        n = flat_nodes[i]
        bounds = n.get('bounds')
        if not bounds:
            continue
        parent_id = n.get('parentId')
        si = n.get('siblingIndex')

        if n.get('type') == 'TEXT' and n.get('text'):
            areas.append({'bounds': bounds, 'parentId': parent_id, 'siblingIndex': si})
        elif n.get('fills') and len(n['fills']) > 0:
            areas.append({'bounds': bounds, 'parentId': parent_id, 'siblingIndex': si})

    return areas


def collect_visual_areas(
    hierarchy_node: Dict,
    figma_index: Dict[str, Dict],
    flat_nodes: List[Dict]
) -> List[Dict]:
    """
    Collect visual-area entries for this hierarchy node.

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
    areas: List[Dict] = []

    if node_id and node_id in figma_index:
        fm = figma_index[node_id]
        bounds = fm.get('bounds')
        if bounds and hierarchy_node.get('gameObjectCategory') == 'image':
            areas.append({
                'bounds': bounds,
                'parentId': fm.get('parentId'),
                'siblingIndex': fm.get('siblingIndex'),
            })

    if not hierarchy_children and node_id and node_id in figma_index:
        areas.extend(collect_visual_areas_from_figma_subtree(
            node_id, figma_index, flat_nodes
        ))
        return areas

    if node_id and node_id in figma_index:
        fm = figma_index[node_id]
        bounds = fm.get('bounds')
        if bounds:
            if fm.get('type') == 'TEXT' and fm.get('text'):
                areas.append({
                    'bounds': bounds,
                    'parentId': fm.get('parentId'),
                    'siblingIndex': fm.get('siblingIndex'),
                })
            elif fm.get('fills') and len(fm['fills']) > 0:
                areas.append({
                    'bounds': bounds,
                    'parentId': fm.get('parentId'),
                    'siblingIndex': fm.get('siblingIndex'),
                })

    for child in hierarchy_children:
        areas.extend(collect_visual_areas(child, figma_index, flat_nodes))

    return areas


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------

def union_bounds(areas: List[Dict]) -> Optional[Dict]:
    """Compute the axis-aligned bounding box of a list of area entries."""
    bounds_list = [a['bounds'] for a in areas]
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


def rectangles_overlap(a: Dict, b: Dict) -> bool:
    """True if two axis-aligned rectangles overlap in both axes."""
    ax1, ay1 = a['x'], a['y']
    ax2, ay2 = a['x'] + a['width'], a['y'] + a['height']
    bx1, by1 = b['x'], b['y']
    bx2, by2 = b['x'] + b['width'], b['y'] + b['height']
    return ax1 < bx2 and ax2 > bx1 and ay1 < by2 and ay2 > by1


# ---------------------------------------------------------------------------
# Main reorder logic
# ---------------------------------------------------------------------------

def reorder_children(hierarchy: Dict, figma_index: Dict[str, Dict], flat_nodes: List[Dict]) -> Dict:
    """
    Reorder every children array in the hierarchy tree.

    Walks the tree depth-first.  For each node with >= 2 children:
      - Compute visual areas (each tagged with source figma parentId/siblingIndex).
      - Determine which sibling pairs overlap and, for each overlapping pair,
        first compare the direct Figma siblings.  Fall back to specific visual
        areas only when their parentId values match.
      - Build a dependency graph; topological sort with position-based heap
        priority and deterministic tie-breaking.
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
            'nodeId': node.get('nodeId', ''),
            'name': node.get('gameObjectName', node.get('nodeName', '')),
        }
        node_cache[nid]['visualBounds'] = union_bounds(
            node_cache[nid]['visualAreas']
        )
        for child in node.get('children', []):
            precompute(child)

    precompute(hierarchy)

    def figma_relative_direction(node_a: Dict, node_b: Dict) -> Optional[str]:
        """Return render order between two hierarchy nodes if Figma can compare them."""
        figma_a = figma_index.get(node_a.get('nodeId', ''))
        figma_b = figma_index.get(node_b.get('nodeId', ''))
        if not figma_a or not figma_b:
            return None
        if figma_a.get('parentId') != figma_b.get('parentId'):
            return None

        si_a = figma_a.get('siblingIndex')
        si_b = figma_b.get('siblingIndex')
        if si_a is None or si_b is None or si_a == si_b:
            return None
        return 'a_before_b' if si_a < si_b else 'b_before_a'

    # ------------------------------------------------------------------
    # Reorder children of a single node (recursive)
    # ------------------------------------------------------------------

    def reorder_one(node: Dict) -> None:
        children: List[Dict] = node.get('children', [])
        if len(children) < 2:
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
        # edges[src_idx] = {dep_idx, ...}  means src must appear before dep
        edges: Dict[int, set] = defaultdict(set)
        indegree: Dict[int, int] = defaultdict(int)
        # Map child index in the ORIGINAL children array → child dict
        child_by_idx: Dict[int, Dict] = {}
        idx_by_oid: Dict[int, int] = {}
        for idx, c in enumerate(children):
            child_by_idx[idx] = c
            idx_by_oid[id(c)] = idx

        for i in range(len(children)):
            for j in range(i + 1, len(children)):
                ci = children[i]
                cj = children[j]
                ci_areas = node_cache.get(id(ci), {}).get('visualAreas', [])
                cj_areas = node_cache.get(id(cj), {}).get('visualAreas', [])

                # Check each area pair for overlap and collect directional constraints
                directions: set = set()  # 'i_before_j' or 'j_before_i'
                any_overlap = False

                for a_i in ci_areas:
                    for a_j in cj_areas:
                        if not rectangles_overlap(a_i['bounds'], a_j['bounds']):
                            continue
                        any_overlap = True
                        # Area siblingIndex values are local to their Figma
                        # parent.  Do not compare descendant areas from
                        # different parents/instances; that produces false
                        # z-order directions.
                        if a_i.get('parentId') != a_j.get('parentId'):
                            continue

                        si_i = a_i.get('siblingIndex')
                        si_j = a_j.get('siblingIndex')

                        if si_i is not None and si_j is not None:
                            if si_i < si_j:
                                directions.add('i_before_j')
                            elif si_j < si_i:
                                directions.add('j_before_i')
                            # equal → no direction from this pair

                if not any_overlap:
                    continue  # No overlap → free to reorder

                direct_direction = figma_relative_direction(ci, cj)
                if direct_direction == 'a_before_b':
                    directions = {'i_before_j'}
                elif direct_direction == 'b_before_a':
                    directions = {'j_before_i'}

                if len(directions) == 1:
                    # All overlapping areas agree on direction
                    if 'i_before_j' in directions:
                        edges[i].add(j)
                        indegree[j] += 1
                    else:
                        edges[j].add(i)
                        indegree[i] += 1
                elif len(directions) == 2:
                    # Conflict: grouping made render order impossible via sibling
                    # reorder alone.  Keep original order and warn so the agent
                    # can revise the grouping.
                    ci_name = node_cache.get(id(ci), {}).get('name', '?')
                    cj_name = node_cache.get(id(cj), {}).get('name', '?')
                    parent_name = node_cache[id(node)].get('name', '?')
                    print(
                        f"WARNING: conflicting overlap constraints between "
                        f"'{ci_name}' and '{cj_name}' "
                        f"under parent '{parent_name}' — "
                        f"grouping may need revision; keeping original order.",
                        file=sys.stderr,
                    )
                    edges[i].add(j)
                    indegree[j] += 1
                else:
                    # directions empty: overlapping areas exist but none have
                    # siblingIndex info.  Keep original order, warn.
                    ci_name = node_cache.get(id(ci), {}).get('name', '?')
                    cj_name = node_cache.get(id(cj), {}).get('name', '?')
                    parent_name = node_cache[id(node)].get('name', '?')
                    print(
                        f"WARNING: overlapping siblings with no figmaSiblingIndex "
                        f"for any overlapping area: "
                        f"'{ci_name}' and '{cj_name}' "
                        f"under parent '{parent_name}' — "
                        f"keeping original relative order.",
                        file=sys.stderr,
                    )
                    edges[i].add(j)
                    indegree[j] += 1

        # -- Build a deterministic sort key from spatial position --
        # Tie-breaker: original child index for deterministic output.
        def sort_key(idx: int) -> Tuple[float, float, int]:
            child = child_by_idx[idx]
            vb = node_cache.get(id(child), {}).get('visualBounds')
            if vb:
                cx, cy = centroid(vb)
                if x_first:
                    return (cx, cy, idx)
                else:
                    return (cy, cx, idx)
            # No visual bounds → push to end, tie-broken by index
            return (float('inf'), float('inf'), idx)

        # -- Topological sort via priority heap --
        heap: List[Tuple[Tuple[float, float, int], int]] = []
        for idx in range(len(children)):
            if indegree[idx] == 0:
                heapq.heappush(heap, (sort_key(idx), idx))

        ordered: List[Dict] = []
        while heap:
            _, idx = heapq.heappop(heap)
            ordered.append(child_by_idx[idx])
            for dep_idx in edges.get(idx, set()):
                indegree[dep_idx] -= 1
                if indegree[dep_idx] == 0:
                    heapq.heappush(heap, (sort_key(dep_idx), dep_idx))

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
