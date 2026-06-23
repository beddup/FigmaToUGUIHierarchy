#!/usr/bin/env python3
"""
Compute rendering-area overlaps between sibling children in a prefab hierarchy.

Takes a simplified Figma content file (flatNodes) and a prefab hierarchy JSON.
For every parent in the hierarchy, checks each pair of its children to determine
whether their actual rendered pixels overlap on screen.

Output: a JSON report listing only the overlapping pairs (must preserve Figma
z-order). Pairs not listed can be reordered freely.
"""

import json
import sys
import argparse
from typing import Dict, List, Any, Optional


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
    """Build a lookup by node id."""
    return {n['id']: n for n in flat_nodes}


def rectangles_overlap(a: Dict, b: Dict) -> bool:
    """Return True if two bounding rectangles overlap in both axes."""
    ax1, ay1 = a['x'], a['y']
    ax2, ay2 = a['x'] + a['width'], a['y'] + a['height']
    bx1, by1 = b['x'], b['y']
    bx2, by2 = b['x'] + b['width'], b['y'] + b['height']

    x_overlap = ax1 < bx2 and ax2 > bx1
    y_overlap = ay1 < by2 and ay2 > by1
    return x_overlap and y_overlap


def collect_rendering_areas_from_figma(
    node_id: str,
    figma_index: Dict[str, Dict],
    flat_nodes: List[Dict]
) -> List[Dict]:
    """
    Walk the figma subtree of `node_id` and return bounds of every node
    that actually produces pixels (TEXT with content, or any node with fills).
    """
    if node_id not in figma_index:
        return []

    node = figma_index[node_id]
    areas = []
    start = node['index']
    end = node['subtreeEndIndex']

    for i in range(start, end + 1):
        n = flat_nodes[i]
        # TEXT with content
        if n.get('type') == 'TEXT' and n.get('text'):
            areas.append(n['bounds'])
        # Any node with fills (works for leaf and container nodes)
        elif n.get('fills') and len(n['fills']) > 0:
            areas.append(n['bounds'])

    return areas


def collect_rendering_areas(
    hierarchy_node: Dict,
    figma_index: Dict[str, Dict],
    flat_nodes: List[Dict]
) -> List[Dict]:
    """
    Collect all rendering areas for a hierarchy node.

    If the node has a figma nodeId → walk its figma subtree.
    If the node is synthetic (no figma entry) → recursively collect from
    hierarchy children.
    """
    node_id = hierarchy_node.get('nodeId', '')

    if node_id and node_id in figma_index:
        return collect_rendering_areas_from_figma(node_id, figma_index, flat_nodes)

    # Synthetic container — aggregate from hierarchy children
    areas = []
    for child in hierarchy_node.get('children', []):
        areas.extend(collect_rendering_areas(child, figma_index, flat_nodes))
    return areas


def get_figma_sibling_index(node: Dict, figma_index: Dict[str, Dict]) -> Optional[int]:
    """Return the figma siblingIndex for a hierarchy node, or None if synthetic."""
    node_id = node.get('nodeId', '')
    if node_id and node_id in figma_index:
        return figma_index[node_id].get('siblingIndex')
    return None


def compute_overlaps(
    hierarchy: Dict,
    figma_index: Dict[str, Dict],
    flat_nodes: List[Dict]
) -> List[Dict]:
    """
    Walk the hierarchy tree. For each parent, check every sibling pair
    for rendering overlap. Return only pairs that overlap.
    """
    overlapping_pairs = []

    def walk(node: Dict, parent_id: str = '', parent_name: str = '', depth: int = 0):
        children = node.get('children', [])
        if not children or len(children) < 2:
            # Need at least 2 children for a pair
            for child in children:
                walk(child, node.get('nodeId', ''), node.get('gameObjectName', ''), depth + 1)
            return

        # Pre-compute rendering areas and sibling indices for all children
        child_data = []
        for child in children:
            areas = collect_rendering_areas(child, figma_index, flat_nodes)
            si = get_figma_sibling_index(child, figma_index)
            child_data.append({
                'node': child,
                'areas': areas,
                'figmaSiblingIndex': si
            })

        # Check every pair
        n = len(children)
        for i in range(n):
            for j in range(i + 1, n):
                a = child_data[i]
                b = child_data[j]
                if has_any_overlap(a['areas'], b['areas']):
                    overlapping_pairs.append({
                        'parentId': node.get('nodeId', ''),
                        'parentName': node.get('gameObjectName', ''),
                        'childA': {
                            'nodeId': a['node'].get('nodeId', ''),
                            'name': a['node'].get('gameObjectName', ''),
                            'figmaSiblingIndex': a['figmaSiblingIndex']
                        },
                        'childB': {
                            'nodeId': b['node'].get('nodeId', ''),
                            'name': b['node'].get('gameObjectName', ''),
                            'figmaSiblingIndex': b['figmaSiblingIndex']
                        }
                    })

        # Recurse into children
        for child in children:
            walk(child, node.get('nodeId', ''), node.get('gameObjectName', ''), depth + 1)

    walk(hierarchy)
    return overlapping_pairs


def has_any_overlap(areas_a: List[Dict], areas_b: List[Dict]) -> bool:
    """Return True if any rectangle in areas_a overlaps any rectangle in areas_b."""
    if not areas_a or not areas_b:
        return False
    for ra in areas_a:
        for rb in areas_b:
            if rectangles_overlap(ra, rb):
                return True
    return False


def main():
    parser = argparse.ArgumentParser(
        description='Compute rendering-area overlaps between sibling children in a prefab hierarchy.'
    )
    parser.add_argument(
        'hierarchy',
        help='Path to the prefab hierarchy JSON file'
    )
    parser.add_argument(
        '-f', '--figma',
        required=True,
        help='Path to the simplified Figma content JSON file (flatNodes)'
    )
    parser.add_argument(
        '-o', '--output',
        help='Path to save the overlap report JSON (default: stdout as JSON)'
    )

    args = parser.parse_args()

    # Load inputs
    hierarchy = load_json(args.hierarchy)
    figma_content = load_json(args.figma)

    # Extract flatNodes from simplified Figma content
    figma_root = find_figma_root(figma_content)
    if figma_root is None or not is_flat_doc(figma_root):
        print("ERROR: Figma content is not in expected flat format", file=sys.stderr)
        sys.exit(1)

    flat_nodes = figma_root['flatNodes']
    figma_index = build_figma_index(flat_nodes)

    # Compute overlaps
    overlapping_pairs = compute_overlaps(hierarchy, figma_index, flat_nodes)

    # Count statistics
    parent_count = 0
    total_pairs = 0

    def count_pairs(node):
        nonlocal parent_count, total_pairs
        children = node.get('children', [])
        if children:
            parent_count += 1
            n = len(children)
            total_pairs += n * (n - 1) // 2
        for child in children:
            count_pairs(child)

    count_pairs(hierarchy)

    report = {
        'overlapConstraints': overlapping_pairs,
        'summary': {
            'parentsWithChildren': parent_count,
            'totalSiblingPairs': total_pairs,
            'overlappingPairs': len(overlapping_pairs)
        }
    }

    if args.output:
        with open(args.output, 'w', encoding='utf-8') as f:
            json.dump(report, f, indent=2, ensure_ascii=False)
        print(f"Overlap report saved to: {args.output}")
    else:
        json.dump(report, sys.stdout, indent=2, ensure_ascii=False)
        print()


if __name__ == '__main__':
    main()
