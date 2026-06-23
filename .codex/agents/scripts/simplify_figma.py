#!/usr/bin/env python3
"""
Simplify Figma content by keeping only necessary fields for UI hierarchy understanding.

Usage:
    python simplify_figma.py <input_file> [output_file] [--nested]

If output_file is not provided, defaults to <input_dir>/<basename>_simplified.json

All progress/log messages go to stderr.
"""

import json
import sys
import os
from typing import Any, Dict, List, Optional, Tuple


# Global list to track removed nodes
removed_nodes_log: List[Dict[str, Any]] = []

# Fields to keep for each node (whitelist approach)
KEEP_NODE_FIELDS = {'id', 'name', 'type', 'children', 'absoluteBoundingBox', 'fills', 'characters'}

# Fields to keep for fills
KEEP_FILL_FIELDS = {'type', 'color'}

# Fields to keep for bounding box
KEEP_BBOX_FIELDS = {'x', 'y', 'width', 'height'}



def is_node_visible(node: Dict[str, Any]) -> bool:
    """Check if a node is explicitly visible (not hidden).

    This only checks the node's own visibility properties, NOT its content.
    A node can be "visible" (not hidden) but have no visible content (invisible fills).
    """
    # Check explicit visibility - if visible field exists and is False, node is hidden
    visible = node.get('visible')
    if visible is False:
        return False

    # Check opacity - if opacity is 0, node is hidden
    opacity = node.get('opacity')
    if opacity is not None and opacity == 0:
        return False

    # NOTE: We do NOT check fills here.
    # A node with invisible fills is NOT the same as an invisible node.
    # A node can have invisible fills but still have visible children.
    # Whether a node has visible content (fills, strokes, etc.) is checked
    # separately by has_visual_properties().

    return True


def simplify_fill(fill: Dict[str, Any]) -> Optional[str]:
    """Simplify a fill object to a compact string representation.

    SOLID fills become "SOLID #RRGGBBAA" hex color strings.
    Other fill types become just the type name.
    """
    # Skip invisible fills
    if fill.get('visible') is False:
        return None

    fill_type = fill.get('type', '')
    if not fill_type:
        return None

    if fill_type == 'SOLID' and 'color' in fill:
        color = fill['color']
        r = int(round(color.get('r', 0) * 255))
        g = int(round(color.get('g', 0) * 255))
        b = int(round(color.get('b', 0) * 255))
        a = int(round(color.get('a', 1) * 255))
        return f"SOLID #{r:02X}{g:02X}{b:02X}{a:02X}"

    return fill_type


def simplify_bbox(bbox: Dict[str, Any]) -> Dict[str, Any]:
    """Simplify bounding box, rounding floats."""
    simplified = {}
    for key in KEEP_BBOX_FIELDS:
        if key in bbox:
            simplified[key] = round(bbox[key])
    return simplified


def has_visual_properties(node: Dict[str, Any]) -> bool:
    """Check if a node has visual properties that make it visible (fills, strokes, background, effects)."""
    # Check fills
    fills = node.get('fills', [])
    if fills:
        # Check if any fill is visible
        for fill in fills:
            if isinstance(fill, dict):
                if fill.get('visible') is not False and fill.get('opacity', 1) != 0:
                    return True

    # Check strokes
    strokes = node.get('strokes', [])
    if strokes:
        for stroke in strokes:
            if isinstance(stroke, dict):
                if stroke.get('visible') is not False:
                    return True

    # Check background
    background = node.get('background', [])
    if background:
        for bg in background:
            if isinstance(bg, dict):
                if bg.get('visible') is not False:
                    return True

    # Check effects (shadows, etc.)
    effects = node.get('effects', [])
    if effects:
        for effect in effects:
            if isinstance(effect, dict):
                if effect.get('visible') is not False:
                    return True

    return False


def simplify_node(node: Dict[str, Any], removed_log: List[Dict[str, Any]], reason: str = "") -> Optional[Dict[str, Any]]:
    """
    Simplify a Figma node, keeping only essential fields.
    Returns None if the node should be removed (invisible, empty container, etc.)
    """
    node_id = node.get('id', 'unknown')
    node_name = node.get('name', 'unnamed')
    node_type = node.get('type', 'unknown')

    # Skip invisible nodes
    if not is_node_visible(node):
        removed_log.append({
            'id': node_id,
            'name': node_name,
            'type': node_type,
            'reason': 'invisible (visible=false or opacity=0)'
        })
        return None

    # Check if node has visual properties
    node_has_visuals = has_visual_properties(node)

    simplified = {}

    # Keep essential node fields
    for key in KEEP_NODE_FIELDS:
        if key in node:
            if key == 'children':
                # Recursively simplify children
                children = node['children']
                simplified_children = []
                for child in children:
                    simplified_child = simplify_node(child, removed_log)
                    if simplified_child is not None:
                        simplified_children.append(simplified_child)
                if simplified_children:
                    simplified['children'] = simplified_children
            elif key == 'absoluteBoundingBox':
                simplified['absoluteBoundingBox'] = simplify_bbox(node[key])
            elif key == 'fills':
                # Simplify fills
                fills = node[key]
                simplified_fills = []
                for fill in fills:
                    simplified_fill = simplify_fill(fill)
                    if simplified_fill:
                        simplified_fills.append(simplified_fill)
                if simplified_fills:
                    simplified['fills'] = simplified_fills
            elif key == 'characters':
                # Keep text content as-is
                simplified['characters'] = node[key]
            else:
                simplified[key] = node[key]

    # Cascading removal: if node has no visual properties AND no children, remove it
    has_children = 'children' in simplified and simplified['children']
    if not node_has_visuals and not has_children:
        removed_log.append({
            'id': node_id,
            'name': node_name,
            'type': node_type,
            'reason': 'empty container (no visual properties, all children removed)'
        })
        return None

    return simplified if simplified else None


def process_nodes_section(nodes_data: Dict[str, Any], removed_log: List[Dict[str, Any]],
                          nested: bool = False) -> Dict[str, Any]:
    """Process the 'nodes' section of the Figma response."""
    result: Dict[str, Any] = {}

    for node_id, node_content in nodes_data.items():
        doc = node_content.get('document') if isinstance(node_content, dict) else None
        source_node = simplify_node(doc, removed_log) if isinstance(doc, dict) else simplify_node(node_content, removed_log)

        if source_node:
            if nested:
                result[node_id] = {'document': source_node}
            else:
                flat_nodes = finalize_flat_nodes(flatten_tree(source_node))
                result[node_id] = {
                    'document': {
                        'type': 'flat',
                        'flatNodes': flat_nodes,
                    }
                }

    return result


def flatten_tree(node: Dict[str, Any], parent_id: Optional[str] = None,
                 depth: int = 0, sibling_index: Optional[int] = None) -> List[Dict[str, Any]]:
    """Flatten a simplified nested node tree into a flat list (DFS pre-order).

    Field order: id, name, index, subtreeEndIndex, depth, parentId, siblingIndex,
    type, bounds, fills, text.
    index/subtreeEndIndex are placeholders filled by finalize_flat_nodes().
    """
    result: List[Dict[str, Any]] = []

    node_id = str(node.get("id", ""))

    flat: Dict[str, Any] = {
        "id": node_id,
        "name": node.get("name", ""),
        "index": -1,            # placeholder, set by finalize_flat_nodes()
        "subtreeEndIndex": -1,  # placeholder, set by finalize_flat_nodes()
        "depth": depth,
    }

    if parent_id is not None:
        flat["parentId"] = parent_id

    if sibling_index is not None:
        flat["siblingIndex"] = sibling_index

    flat["type"] = node.get("type", "")

    bbox = node.get("absoluteBoundingBox")
    if isinstance(bbox, dict):
        flat["bounds"] = bbox

    fills = node.get("fills")
    if fills:
        flat["fills"] = fills

    characters = node.get("characters")
    if characters:
        flat["text"] = str(characters).replace("\n", " ").strip()

    result.append(flat)

    children = node.get("children", [])
    for i, child in enumerate(children):
        if isinstance(child, dict):
            result.extend(flatten_tree(child, node_id, depth + 1, i))

    return result


def finalize_flat_nodes(flat_nodes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Set index and compute subtreeEndIndex for every node in a DFS-ordered flat list.

    In DFS pre-order, a node's subtree occupies a contiguous range [index, subtreeEndIndex].
    Leaf nodes have subtreeEndIndex == index.
    """
    if not flat_nodes:
        return flat_nodes

    children_of: Dict[str, List[int]] = {}
    for i, node in enumerate(flat_nodes):
        node["index"] = i
        pid = node.get("parentId")
        if pid:
            children_of.setdefault(pid, []).append(i)

    # Reverse pass: children appear after their parent, so they're already processed
    for i in range(len(flat_nodes) - 1, -1, -1):
        node = flat_nodes[i]
        child_indices = children_of.get(node["id"], [])
        if child_indices:
            node["subtreeEndIndex"] = max(
                flat_nodes[c]["subtreeEndIndex"] for c in child_indices
            )
        else:
            node["subtreeEndIndex"] = i

    return flat_nodes


def write_flat_json(path: str, data: Dict[str, Any]) -> None:
    """Write simplified Figma JSON with flatNodes serialized as compact single-line objects."""
    PLACEHOLDER = "☍FLAT_PLACEHOLDER☍"
    placeholders: Dict[str, str] = {}

    for node_id, node_content in data.get("nodes", {}).items():
        doc = node_content.get("document", {})
        flat_nodes = doc.get("flatNodes", [])
        if flat_nodes:
            compact_lines = []
            for i, fn in enumerate(flat_nodes):
                key = f"{PLACEHOLDER}{i}"
                placeholders[key] = json.dumps(fn, ensure_ascii=False)
                compact_lines.append(key)
            doc["flatNodes"] = compact_lines

    raw = json.dumps(data, ensure_ascii=False, indent=2)
    for key, compact_json in placeholders.items():
        raw = raw.replace(f'"{key}"', compact_json)

    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(raw)
        f.write("\n")


def simplify_figma_content(data: Dict[str, Any], removed_log: List[Dict[str, Any]],
                            nested: bool = False) -> Dict[str, Any]:
    """Main entry point to simplify Figma content."""
    result: Dict[str, Any] = {}

    # Keep top-level metadata
    metadata_fields = ['name', 'lastModified', 'thumbnailUrl', 'version', 'role']
    for field in metadata_fields:
        if field in data:
            result[field] = data[field]

    # Process nodes section
    if 'nodes' in data:
        result['nodes'] = process_nodes_section(data['nodes'], removed_log, nested=nested)

    return result


def main():
    if len(sys.argv) < 2:
        print("Usage: python simplify_figma.py <input_file> [output_file] [--nested]", file=sys.stderr)
        print("  input_file:   Path to raw Figma content JSON", file=sys.stderr)
        print("  output_file:  Optional. Defaults to <input>_simplified.json", file=sys.stderr)
        print("  --nested:     Output nested format instead of flat (default: flat)", file=sys.stderr)
        sys.exit(1)

    # Parse optional flags from argv
    nested = '--nested' in sys.argv
    positional = []
    i = 1  # skip script name
    while i < len(sys.argv):
        a = sys.argv[i]
        if a == '--nested':
            i += 1
        else:
            positional.append(a)
            i += 1
    input_file = positional[0]

    # Determine output file path
    if len(positional) >= 2:
        output_file = positional[1]
    else:
        base, ext = os.path.splitext(input_file)
        suffix = "_simplified_nested" if nested else "_simplified"
        output_file = f"{base}{suffix}{ext}"

    # Validate input file exists
    if not os.path.exists(input_file):
        print(f"Error: Input file not found: {input_file}", file=sys.stderr)
        sys.exit(1)

    # Read input
    print(f"Reading: {input_file}", file=sys.stderr)
    with open(input_file, 'r', encoding='utf-8') as f:
        data = json.load(f)

    # Process
    mode = "nested" if nested else "flat"
    print(f"Simplifying ({mode})...", file=sys.stderr)
    removed_log: List[Dict[str, Any]] = []
    simplified = simplify_figma_content(data, removed_log, nested=nested)

    # Write output
    print(f"Writing: {output_file}", file=sys.stderr)
    if nested:
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(simplified, f, indent=2, ensure_ascii=False)
    else:
        write_flat_json(output_file, simplified)

    # Write removed nodes log
    removed_log_file = f"{os.path.splitext(output_file)[0]}_removed_nodes.json"
    print(f"Writing removed nodes log: {removed_log_file}", file=sys.stderr)
    with open(removed_log_file, 'w', encoding='utf-8') as f:
        json.dump({
            'total_removed': len(removed_log),
            'removed_nodes': removed_log
        }, f, indent=2, ensure_ascii=False)

    # Report to stderr
    input_size = os.path.getsize(input_file)
    output_size = os.path.getsize(output_file)
    reduction = (1 - output_size / input_size) * 100

    print(f"Input size:  {input_size:,} bytes ({input_size/1024:.2f} KB)", file=sys.stderr)
    print(f"Output size: {output_size:,} bytes ({output_size/1024:.2f} KB)", file=sys.stderr)
    print(f"Reduction:   {reduction:.1f}%", file=sys.stderr)
    print(f"Total nodes removed: {len(removed_log)}", file=sys.stderr)


if __name__ == '__main__':
    main()
