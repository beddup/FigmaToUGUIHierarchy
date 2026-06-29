#!/usr/bin/env python3
"""
Combine multiple prefab hierarchy files into a single large hierarchy.

Rules:
1. Elements with the same nodeId are considered the same - use first occurrence for node data
2. All nodeIds must be unique in the final hierarchy
3. Children order should match the figma content file order
4. ALL nodeIds from ALL input hierarchies must be present in the final hierarchy
   (When nodes share the same nodeId, their children are MERGED - union of all children)
"""

import json
import sys
import argparse
import re
from typing import Dict, List, Any, Set, Optional


def load_json(filepath: str) -> Any:
    """Load JSON file and return parsed data."""
    with open(filepath, 'r', encoding='utf-8') as f:
        return json.load(f)


def save_json(filepath: str, data: Any) -> None:
    """Save data to JSON file with pretty formatting."""
    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def collect_all_nodes_with_merged_children(hierarchies: List[Dict]) -> Dict[str, Dict]:
    """
    Collect all nodes from all hierarchies.
    When nodes share the same nodeId, merge their children (union).
    First occurrence wins for node data (Rule 1).

    Returns: Dict mapping nodeId -> node data with merged children
    """
    all_nodes: Dict[str, Dict] = {}

    def copy_node_data(node: Dict, current_children: List[Dict], child_ids: Set[str]) -> Dict:
        copied = {
            'nodeId': node.get('nodeId'),
            'nodeName': node.get('nodeName'),
            'gameObjectCategory': node.get('gameObjectCategory'),
            'gameObjectName': node.get('gameObjectName'),
            'isButton': node.get('isButton', False),
            'horizontal_alignment': node.get('horizontal_alignment', 'center'),
            'vertical_alignment': node.get('vertical_alignment', 'center'),
            'children': current_children,  # Store actual children list
            '_child_ids': child_ids  # Track child IDs for merging
        }
        if node.get('gameObjectCategory') == 'image' and node.get('image_type'):
            copied['image_type'] = node.get('image_type')
        return copied

    # First pass: collect all nodes and merge children
    for hierarchy in hierarchies:
        # Use stack to traverse
        stack = [hierarchy]
        while stack:
            node = stack.pop()
            if not node:
                continue

            node_id = node.get('nodeId')
            if not node_id:
                continue

            # Get children from current hierarchy
            current_children = node.get('children', [])
            child_ids = {c.get('nodeId') for c in current_children if c.get('nodeId')}

            if node_id not in all_nodes:
                # First occurrence - store node data and children
                all_nodes[node_id] = copy_node_data(node, current_children, child_ids)
            else:
                # Node already exists - MERGE children (Rule 4)
                existing = all_nodes[node_id]
                if (
                    existing.get('gameObjectCategory') == 'image'
                    and not existing.get('image_type')
                    and node.get('image_type')
                ):
                    existing['image_type'] = node.get('image_type')
                for child in current_children:
                    child_id = child.get('nodeId')
                    if child_id and child_id not in existing['_child_ids']:
                        existing['children'].append(child)
                        existing['_child_ids'].add(child_id)

            # Add children to stack for processing
            for child in current_children:
                if child:
                    stack.append(child)

    return all_nodes


def find_figma_document_root(data: Dict) -> Optional[Dict]:
    """Return the root document node from raw/simplified Figma content (flat or nested)."""
    if isinstance(data.get('nodes'), dict):
        for node_content in data['nodes'].values():
            if isinstance(node_content, dict) and isinstance(node_content.get('document'), dict):
                return node_content['document']
    if isinstance(data, dict) and data.get('id'):
        return data
    return None


def is_flat_doc(doc: Any) -> bool:
    """Return True if doc is a flat-format document dict."""
    return isinstance(doc, dict) and doc.get('type') == 'flat' and isinstance(doc.get('flatNodes'), list)


def get_flat_root_info(doc: Dict) -> Optional[Dict]:
    """Return the root node from a flat document (node without parentId)."""
    for fn in doc.get('flatNodes', []):
        if 'parentId' not in fn:
            return fn
    flat_nodes = doc.get('flatNodes', [])
    return flat_nodes[0] if flat_nodes else None


def build_tree(node_id: str, all_nodes: Dict[str, Dict],
               processed_ids: Set[str]) -> Optional[Dict]:
    """Build tree recursively starting from node_id."""
    if node_id in processed_ids:
        return None  # Cycle detected

    if node_id not in all_nodes:
        return None

    processed_ids.add(node_id)
    node = all_nodes[node_id]

    # Build result node
    result = {
        'nodeId': node['nodeId'],
        'nodeName': node['nodeName'],
        'gameObjectCategory': node['gameObjectCategory'],
        'gameObjectName': node['gameObjectName'],
        'isButton': node['isButton'],
        'horizontal_alignment': node.get('horizontal_alignment', 'center'),
        'vertical_alignment': node.get('vertical_alignment', 'center'),
        'children': []
    }
    if result['gameObjectCategory'] == 'image' and node.get('image_type'):
        result['image_type'] = node.get('image_type')

    # Recursively build children
    for child in node.get('children', []):
        child_id = child.get('nodeId')
        if child_id and child_id in all_nodes:
            child_tree = build_tree(child_id, all_nodes, processed_ids)
            if child_tree:
                result['children'].append(child_tree)

    return result


def count_nodes(node: Optional[Dict]) -> int:
    """Count total nodes in tree."""
    if not node:
        return 0
    return 1 + sum(count_nodes(c) for c in node.get('children', []))


def get_all_node_ids(node: Optional[Dict], ids: Set[str] = None) -> Set[str]:
    """Get all nodeIds from tree."""
    if ids is None:
        ids = set()
    if not node:
        return ids
    node_id = node.get('nodeId')
    if node_id:
        ids.add(node_id)
    for child in node.get('children', []):
        get_all_node_ids(child, ids)
    return ids


def create_root_gameobject(figma_root: Dict) -> Dict:
    """Create the canonical combined root from the Figma content root (flat or nested)."""
    if is_flat_doc(figma_root):
        ri = get_flat_root_info(figma_root) or {}
        root_name = str(ri.get('name', '')).strip()
        root_id = ri.get('id', '')
    else:
        root_name = str(figma_root.get('name') or '').strip()
        root_id = figma_root.get('id', '')
    safe_root_name = re.sub(r'[^\w\u4e00-\u9fff]+', '_', root_name).strip('_')
    return {
        'nodeId': root_id,
        'nodeName': root_name,
        'gameObjectCategory': 'container',
        'gameObjectName': safe_root_name or 'Root',
        'isButton': False,
        'horizontal_alignment': 'center',
        'vertical_alignment': 'center',
        'children': []
    }


def combine_hierarchies(hierarchies: List[Dict], figma_root: Optional[Dict] = None) -> Dict:
    """Main function to combine hierarchies following all 4 rules."""
    if not hierarchies:
        return {}

    print(f"\nCombining {len(hierarchies)} hierarchies...")

    if not figma_root:
        print("  ERROR: Figma root is required to combine subtree hierarchies")
        return {}

    root = create_root_gameobject(figma_root)
    root_id = root['nodeId']
    if not root_id:
        print("  ERROR: Figma root has no id")
        return {}

    # Step 1: Collect all nodes with merged children
    all_nodes = collect_all_nodes_with_merged_children(hierarchies)
    print(f"  Total unique input nodes: {len(all_nodes)}")

    # Step 2: Create one canonical root from the Figma content root.
    # If an input hierarchy already uses this root, merge its children into
    # the root. If it uses a semantic/local root, attach that whole hierarchy
    # under the root and preserve its children.
    root_children = []
    root_child_ids = set()

    for hierarchy in hierarchies:
        hierarchy_id = hierarchy.get('nodeId')
        if hierarchy_id == root_id:
            children_to_add = hierarchy.get('children', [])
        else:
            children_to_add = [hierarchy]

        for child in children_to_add:
            child_id = child.get('nodeId')
            if child_id and child_id != root_id and child_id not in root_child_ids:
                root_children.append(child)
                root_child_ids.add(child_id)

    all_nodes[root_id] = {
        **root,
        'children': root_children,
        '_child_ids': root_child_ids
    }

    # Step 3: Build the final tree from the canonical Figma root.
    processed = set()
    best_tree = build_tree(root_id, all_nodes, processed)
    if not best_tree:
        print("  ERROR: Failed to build tree from Figma root!")
        return {}

    # Step 4: Verify Rule 4 - all nodes should be in tree
    all_ids = set(all_nodes.keys())
    tree_ids = get_all_node_ids(best_tree)
    missing = all_ids - tree_ids

    if missing:
        print(f"  WARNING: {len(missing)} nodes missing, adding as root children")
        for miss_id in missing:
            miss_tree = build_tree(miss_id, all_nodes, tree_ids)
            if miss_tree:
                best_tree['children'].append(miss_tree)

    print(f"  Final tree: {count_nodes(best_tree)} nodes")
    return best_tree


def get_node_order_from_figma(figma_node: Dict) -> List[str]:
    """Extract nodeId order from figma content (flat or nested)."""
    order: List[str] = []

    root = find_figma_document_root(figma_node) or figma_node

    if is_flat_doc(root):
        flat_nodes = root.get('flatNodes', [])
        # Build children index and lookup by id
        children_of: Dict[str, List[str]] = {}
        node_by_id: Dict[str, Dict] = {}
        for fn in flat_nodes:
            nid = fn['id']
            node_by_id[nid] = fn
            pid = fn.get('parentId')
            if pid:
                children_of.setdefault(pid, []).append(nid)

        # DFS from root, sorting children by siblingIndex to preserve z-order
        root_info = get_flat_root_info(root)
        if root_info and root_info.get('id'):
            stack = [(root_info['id'], False)]
            while stack:
                nid, _visited = stack.pop()
                order.append(nid)
                child_ids = children_of.get(nid, [])
                # Sort by siblingIndex (0 = backmost, N-1 = frontmost)
                sorted_children = sorted(child_ids, key=lambda cid: node_by_id.get(cid, {}).get('siblingIndex', 0))
                for cid in reversed(sorted_children):
                    stack.append((cid, False))
    else:
        def extract(node: Dict) -> None:
            node_id = node.get('id')
            if node_id:
                order.append(node_id)
            for child in node.get('children', []):
                if isinstance(child, dict):
                    extract(child)
        extract(root)

    return order


def sort_by_figma_order(hierarchy: Dict, figma_order: List[str]) -> Dict:
    """Sort hierarchy children to match figma content order (Rule 3)."""
    if not figma_order:
        return hierarchy

    def get_order_index(node: Dict) -> int:
        node_id = node.get('nodeId', '')
        try:
            return figma_order.index(node_id)
        except ValueError:
            return len(figma_order)

    def sort_recursive(node: Dict) -> Dict:
        result = {**node}
        if 'children' in result and result['children']:
            result['children'] = sorted(result['children'], key=get_order_index)
            result['children'] = [sort_recursive(c) for c in result['children']]
        return result

    return sort_recursive(hierarchy)


def main():
    parser = argparse.ArgumentParser(
        description='Combine multiple prefab hierarchy files into one'
    )
    parser.add_argument(
        'hierarchy_files',
        nargs='+',
        help='Paths to hierarchy JSON files to combine'
    )
    parser.add_argument(
        '-f', '--figma',
        required=True,
        help='Path to figma content file for ordering reference'
    )
    parser.add_argument(
        '-o', '--output',
        required=True,
        help='Output file path for combined hierarchy'
    )

    args = parser.parse_args()

    # Load all hierarchy files
    hierarchies = []
    for filepath in args.hierarchy_files:
        try:
            data = load_json(filepath)
            hierarchies.append(data)
            print(f"Loaded: {filepath}")
        except Exception as e:
            print(f"Error loading {filepath}: {e}")
            sys.exit(1)

    # Load figma content if provided
    figma_order = []
    figma_root = None
    if args.figma:
        try:
            figma_content = load_json(args.figma)
            figma_root = find_figma_document_root(figma_content)
            figma_order = get_node_order_from_figma(figma_content)
            print(f"Loaded figma content: {args.figma} ({len(figma_order)} nodes)")
        except Exception as e:
            print(f"Warning: Could not load figma content: {e}")

    # Merge hierarchies
    combined = combine_hierarchies(hierarchies, figma_root)

    # Sort by figma order if available
    if figma_order and combined:
        combined = sort_by_figma_order(combined, figma_order)
        print("Sorted by figma content order")

    # Save combined hierarchy
    save_json(args.output, combined)
    print(f"\nCombined hierarchy saved to: {args.output}")


if __name__ == '__main__':
    main()
