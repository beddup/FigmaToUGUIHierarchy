#!/usr/bin/env python3
"""
Reorder children in a prefab hierarchy for Unity readability while preserving
Figma rendering order whenever sibling GameObjects visually overlap.

Principles:
1. Reorder only inside each parent's children array; never move a child across
   hierarchy parents.
2. A child container's visual bounds are the union of all renderable hierarchy
   descendants. A hierarchy GameObject is renderable when its
   gameObjectCategory is text, image, or color, and its own Figma node bounds
   are used as the visual area.
3. Non-overlapping sibling children are free to move and are sorted by position:
   - parent visual width > height -> X-first (left-to-right), then Y
   - parent visual height >= width -> Y-first (top-to-bottom), then X
4. Overlapping sibling children create dependency constraints. For each
   overlapping pair, collect only the render nodes that participate in the
   overlap, find their nearest common Figma ancestor, project them to direct
   child branches under that ancestor, then use those branches' Figma sibling
   order to decide which hierarchy child must come first.
5. If overlapping nodes cannot be compared through a common ancestor branch
   order, keep their original relative hierarchy order and emit a warning.
6. Apply the constraints with a topological sort using a spatial priority heap;
   original child index is the final tie-breaker for deterministic output.
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
#
# Each visual area is a dict:
#   {
#       'nodeId': str,
#       'bounds': {'x','y','width','height'},
#       'parentId': str | None,
#       'siblingIndex': int | None
#   }
#
# parentId and siblingIndex refer to the specific flatNodes entry that produced
# the bounds.  siblingIndex values are only comparable when parentId matches.
# ---------------------------------------------------------------------------

def collect_visual_areas(
    hierarchy_node: Dict,
    figma_index: Dict[str, Dict],
    flat_nodes: List[Dict]
) -> List[Dict]:
    """
    Collect visual-area entries for this hierarchy node.

    Strategy:
    - If this hierarchy node is renderable (text/image/color), include its own
      Figma bounds.
    - Recurse through hierarchy children and include every renderable descendant.
    - Do not infer rendering areas from raw Figma subtree fills/text that do not
      have a corresponding renderable hierarchy GameObject.
    """
    node_id = hierarchy_node.get('nodeId', '')
    hierarchy_children = hierarchy_node.get('children', [])
    areas: List[Dict] = []

    if node_id and node_id in figma_index:
        fm = figma_index[node_id]
        bounds = fm.get('bounds')
        if bounds and hierarchy_node.get('gameObjectCategory') in {'text', 'image', 'color'}:
            areas.append({
                'nodeId': node_id,
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
        collect all render nodes involved in the overlap.  Find their nearest
        common Figma ancestor, scan that ancestor's children from back to front,
        and let the first involved branch determine which GameObject comes first.
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

    children_by_parent: Dict[str, List[Dict]] = defaultdict(list)
    for figma_node in figma_index.values():
        parent_id = figma_node.get('parentId')
        if parent_id:
            children_by_parent[parent_id].append(figma_node)
    for siblings in children_by_parent.values():
        siblings.sort(key=lambda n: n.get('siblingIndex', 0))

    def ancestor_chain(node_id: str) -> List[str]:
        """Return node_id followed by each Figma ancestor up to the root."""
        chain: List[str] = []
        current_id = node_id
        seen = set()
        while current_id and current_id in figma_index and current_id not in seen:
            seen.add(current_id)
            chain.append(current_id)
            current_id = figma_index[current_id].get('parentId')
        return chain

    def nearest_common_ancestor(node_ids: List[str]) -> Optional[str]:
        """Return the nearest common Figma ancestor for all node_ids."""
        chains = [ancestor_chain(node_id) for node_id in node_ids if node_id in figma_index]
        if len(chains) != len(node_ids) or not chains:
            return None
        common = set(chains[0])
        for chain in chains[1:]:
            common &= set(chain)
        for node_id in chains[0]:
            if node_id in common:
                return node_id
        return None

    def project_to_child_under(node_id: str, ancestor_id: str) -> Optional[str]:
        """Return the direct child branch of ancestor_id that contains node_id."""
        if node_id == ancestor_id:
            return None
        current_id = node_id
        while current_id in figma_index:
            parent_id = figma_index[current_id].get('parentId')
            if parent_id == ancestor_id:
                return current_id
            current_id = parent_id
        return None

    def overlap_direction_from_common_ancestor(
        i_area_ids: set,
        j_area_ids: set
    ) -> Optional[str]:
        """Determine GameObject order by scanning involved branches under their LCA."""
        involved_ids = sorted(i_area_ids | j_area_ids)
        ancestor_id = nearest_common_ancestor(involved_ids)
        if not ancestor_id:
            return None

        branch_owner: Dict[str, str] = {}
        for node_id in i_area_ids:
            branch_id = project_to_child_under(node_id, ancestor_id)
            if not branch_id:
                return None
            branch_owner.setdefault(branch_id, 'i')
            if branch_owner[branch_id] != 'i':
                return None
        for node_id in j_area_ids:
            branch_id = project_to_child_under(node_id, ancestor_id)
            if not branch_id:
                return None
            branch_owner.setdefault(branch_id, 'j')
            if branch_owner[branch_id] != 'j':
                return None

        for child in children_by_parent.get(ancestor_id, []):
            owner = branch_owner.get(child.get('id'))
            if owner == 'i':
                return 'i_before_j'
            if owner == 'j':
                return 'j_before_i'
        return None

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
        for idx, c in enumerate(children):
            child_by_idx[idx] = c

        for i in range(len(children)):
            for j in range(i + 1, len(children)):
                ci = children[i]
                cj = children[j]
                ci_areas = node_cache.get(id(ci), {}).get('visualAreas', [])
                cj_areas = node_cache.get(id(cj), {}).get('visualAreas', [])

                # Check each area pair for overlap and collect the render nodes
                # that actually participate in this sibling-pair overlap.
                direction: Optional[str] = None
                any_overlap = False
                i_overlap_area_ids = set()
                j_overlap_area_ids = set()

                for a_i in ci_areas:
                    for a_j in cj_areas:
                        if not rectangles_overlap(a_i['bounds'], a_j['bounds']):
                            continue
                        any_overlap = True
                        if a_i.get('nodeId'):
                            i_overlap_area_ids.add(a_i['nodeId'])
                        if a_j.get('nodeId'):
                            j_overlap_area_ids.add(a_j['nodeId'])

                if not any_overlap:
                    continue  # No overlap → free to reorder

                direction = overlap_direction_from_common_ancestor(
                    i_overlap_area_ids,
                    j_overlap_area_ids,
                )

                if direction:
                    if direction == 'i_before_j':
                        edges[i].add(j)
                        indegree[j] += 1
                    else:
                        edges[j].add(i)
                        indegree[i] += 1
                else:
                    # Overlapping areas exist, but no common-ancestor branch
                    # order can be compared. Keep original order and warn.
                    ci_name = node_cache.get(id(ci), {}).get('name', '?')
                    cj_name = node_cache.get(id(cj), {}).get('name', '?')
                    parent_name = node_cache[id(node)].get('name', '?')
                    print(
                        f"WARNING: overlapping siblings with no comparable "
                        f"Figma sibling order: "
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
