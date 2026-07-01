#!/usr/bin/env python3
"""
One-shot script: fetch Figma content, screenshot, and simplify.

End-to-end replacement for the fetch_figma_content agent.  Internally:
  1. Discover node identity from the Figma URL
  2. Fetch raw content JSON + 2x PNG screenshot
  3. Simplify the raw content (strip invisible nodes, flatten tree)

All progress / log messages go to stderr.  The final result JSON
(the same shape the agent used to return) goes to stdout.

Usage:
  python3 fetch_all_figma.py <figma_url>

Environment:
  FIGMA_API_TOKEN — required; the script exits with an error if unset.
"""

import json
import os
import re
import ssl
import sys
import urllib.request
import urllib.error
from typing import Any, Dict, List, Optional


# ═══════════════════════════════════════════════════════════════════════════════
#  SSL helper
# ═══════════════════════════════════════════════════════════════════════════════

def _create_ssl_context() -> ssl.SSLContext:
    context = ssl.create_default_context()
    try:
        import certifi
        context.load_verify_locations(certifi.where())
    except ImportError:
        pass
    return context


# ═══════════════════════════════════════════════════════════════════════════════
#  Figma API
# ═══════════════════════════════════════════════════════════════════════════════

def _parse_figma_url(url: str):
    """Extract file key and node ID from a Figma URL.  Returns (key, id) or (None, None)."""
    for pattern in [
            r'/design/([a-zA-Z0-9_-]+)(?:/[^?]*)?\?.*node-id=([0-9-]+)',
            r'/file/([a-zA-Z0-9_-]+)(?:/[^?]*)?\?.*node-id=([0-9-]+)',
    ]:
        m = re.search(pattern, url)
        if m:
            return m.group(1), m.group(2)
    return None, None


def _fetch_figma_node(file_key: str, node_id: str, token: str) -> Optional[Dict]:
    """Fetch a specific node from a Figma file (REST API)."""
    encoded = node_id.replace('-', ':')
    url = f"https://api.figma.com/v1/files/{file_key}/nodes?ids={encoded}"
    headers = {"X-Figma-Token": token, "Content-Type": "application/json"}
    req = urllib.request.Request(url, headers=headers)
    try:
        ctx = _create_ssl_context()
        with urllib.request.urlopen(req, context=ctx) as resp:
            return json.loads(resp.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        body = e.read().decode('utf-8')
        print(f"HTTP Error: {e.code} - {e.reason}\nResponse: {body}", file=sys.stderr)
        return None
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return None


def _fetch_figma_screenshot(file_key: str, node_id: str, token: str, scale: int = 2) -> Optional[bytes]:
    """Fetch a PNG screenshot of a Figma node (REST API)."""
    encoded = node_id.replace('-', ':')
    url = f"https://api.figma.com/v1/images/{file_key}?ids={encoded}&scale={scale}&format=png"
    headers = {"X-Figma-Token": token, "Content-Type": "application/json"}
    req = urllib.request.Request(url, headers=headers)
    try:
        ctx = _create_ssl_context()
        with urllib.request.urlopen(req, context=ctx) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            image_url = data.get('images', {}).get(encoded)
            if image_url:
                with urllib.request.urlopen(image_url, context=ctx) as img_resp:
                    return img_resp.read()
        return None
    except urllib.error.HTTPError as e:
        body = e.read().decode('utf-8')
        print(f"HTTP Error: {e.code} - {e.reason}\nResponse: {body}", file=sys.stderr)
        return None
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return None


def _get_figma_token() -> str:
    token = os.environ.get("FIGMA_API_TOKEN")
    if not token:
        print("Error: FIGMA_API_TOKEN environment variable is not set.", file=sys.stderr)
        sys.exit(1)
    return token


# ═══════════════════════════════════════════════════════════════════════════════
#  Simplify — keep only essential fields, strip invisible nodes, flatten tree
# ═══════════════════════════════════════════════════════════════════════════════

KEEP_NODE_FIELDS = {'id', 'name', 'type', 'children', 'absoluteBoundingBox', 'fills', 'characters'}


def _is_node_visible(node: Dict) -> bool:
    if node.get('visible') is False:
        return False
    opacity = node.get('opacity')
    if opacity is not None and opacity == 0:
        return False
    return True


def _simplify_fill(fill: Dict) -> Optional[str]:
    if fill.get('visible') is False:
        return None
    fill_type = fill.get('type', '')
    if not fill_type:
        return None
    if fill_type == 'SOLID' and 'color' in fill:
        c = fill['color']
        r = int(round(c.get('r', 0) * 255))
        g = int(round(c.get('g', 0) * 255))
        b = int(round(c.get('b', 0) * 255))
        a = int(round(c.get('a', 1) * 255))
        return f"SOLID #{r:02X}{g:02X}{b:02X}{a:02X}"
    return fill_type


def _simplify_bbox(bbox: Dict) -> Dict:
    return {k: round(bbox[k]) for k in ('x', 'y', 'width', 'height') if k in bbox}


def _has_visual_properties(node: Dict) -> bool:
    for key in ('fills', 'strokes', 'background', 'effects'):
        for item in node.get(key, []) or []:
            if isinstance(item, dict) and item.get('visible') is not False and item.get('opacity', 1) != 0:
                return True
    return False


def _simplify_node(node: Dict, removed_log: List[Dict]) -> Optional[Dict]:
    nid, name, ntype = node.get('id', 'unknown'), node.get('name', 'unnamed'), node.get('type', 'unknown')

    if not _is_node_visible(node):
        removed_log.append({'id': nid, 'name': name, 'type': ntype, 'reason': 'invisible'})
        return None

    node_has_visuals = _has_visual_properties(node)
    simplified: Dict = {}

    for key in KEEP_NODE_FIELDS:
        if key not in node:
            continue
        if key == 'children':
            simplified_children = []
            for child in node['children']:
                sc = _simplify_node(child, removed_log)
                if sc is not None:
                    simplified_children.append(sc)
            if simplified_children:
                simplified['children'] = simplified_children
        elif key == 'absoluteBoundingBox':
            simplified['absoluteBoundingBox'] = _simplify_bbox(node[key])
        elif key == 'fills':
            fills = [_simplify_fill(f) for f in node[key]]
            fills = [f for f in fills if f]
            if fills:
                simplified['fills'] = fills
        elif key == 'characters':
            simplified['characters'] = node[key]
        else:
            simplified[key] = node[key]

    has_children = bool(simplified.get('children'))
    if not node_has_visuals and not has_children:
        removed_log.append({'id': nid, 'name': name, 'type': ntype, 'reason': 'empty container'})
        return None

    return simplified or None


def _flatten_tree(node: Dict, parent_id: Optional[str] = None,
                  depth: int = 0, sibling_index: Optional[int] = None) -> List[Dict]:
    result: List[Dict] = []
    nid = str(node.get("id", ""))

    flat: Dict = {
        "id": nid,
        "name": node.get("name", ""),
        "index": -1,
        "subtreeEndIndex": -1,
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

    text = node.get("characters")
    if text:
        flat["text"] = str(text).replace("\n", " ").strip()

    result.append(flat)

    for i, child in enumerate(node.get("children", []) or []):
        if isinstance(child, dict):
            result.extend(_flatten_tree(child, nid, depth + 1, i))

    return result


def _finalize_flat_nodes(flat_nodes: List[Dict]) -> List[Dict]:
    if not flat_nodes:
        return flat_nodes

    children_of: Dict[str, List[int]] = {}
    for i, node in enumerate(flat_nodes):
        node["index"] = i
        pid = node.get("parentId")
        if pid:
            children_of.setdefault(pid, []).append(i)

    for i in range(len(flat_nodes) - 1, -1, -1):
        node = flat_nodes[i]
        child_indices = children_of.get(node["id"], [])
        if child_indices:
            node["subtreeEndIndex"] = max(flat_nodes[c]["subtreeEndIndex"] for c in child_indices)
        else:
            node["subtreeEndIndex"] = i

    return flat_nodes


def _write_flat_json(path: str, data: Dict) -> None:
    PLACEHOLDER = "☍FLAT_PLACEHOLDER☍"
    placeholders: Dict[str, str] = {}

    for node_content in data.get("nodes", {}).values():
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


def _simplify_figma_content(data: Dict, removed_log: List[Dict]) -> Dict:
    result: Dict = {}
    for field in ('name', 'lastModified', 'thumbnailUrl', 'version', 'role'):
        if field in data:
            result[field] = data[field]

    if 'nodes' in data:
        nodes_result: Dict = {}
        for nid, ncontent in data['nodes'].items():
            doc = ncontent.get('document') if isinstance(ncontent, dict) else None
            source_node = _simplify_node(doc, removed_log) if isinstance(doc, dict) else _simplify_node(ncontent, removed_log)
            if source_node:
                flat_nodes = _finalize_flat_nodes(_flatten_tree(source_node))
                nodes_result[nid] = {
                    'document': {'type': 'flat', 'flatNodes': flat_nodes}
                }
        if nodes_result:
            result['nodes'] = nodes_result

    return result


# ═══════════════════════════════════════════════════════════════════════════════
#  Sanitize
# ═══════════════════════════════════════════════════════════════════════════════

def _sanitize(s: str, max_len: int = 100) -> str:
    safe = re.sub(r'[^\w\-]', '_', s)
    safe = safe.strip('_.')
    safe = re.sub(r'_{2,}', '_', safe)
    if not safe:
        safe = 'unnamed'
    if len(safe) > max_len:
        safe = safe[:max_len].rstrip('_.')
    return safe


# ═══════════════════════════════════════════════════════════════════════════════
#  Main
# ═══════════════════════════════════════════════════════════════════════════════

DEFAULT_OUTPUT_DIR = "Library/FigmaToUGUI"


def main():
    if len(sys.argv) < 2:
        print("Usage: python fetch_all_figma.py <figma_url>", file=sys.stderr)
        sys.exit(1)

    figma_url = sys.argv[1]
    token = _get_figma_token()

    # ── Phase 1: discover node identity ───────────────────────────────
    print("Phase 1/3: Discovering node identity...", file=sys.stderr)

    file_key, node_id = _parse_figma_url(figma_url)
    if not file_key or not node_id:
        print("Error: Could not parse Figma URL.", file=sys.stderr)
        sys.exit(1)

    node_data = _fetch_figma_node(file_key, node_id, token)
    if not node_data:
        print("Error: Failed to fetch node data.", file=sys.stderr)
        sys.exit(1)

    doc = None
    for node_info in node_data.get("nodes", {}).values():
        if isinstance(node_info, dict) and "document" in node_info:
            doc = node_info["document"]
            break
    if not doc:
        print("Error: No document node found.", file=sys.stderr)
        sys.exit(1)

    node_name = doc.get("name", "unknown")
    safe_name = _sanitize(node_name)
    safe_node_id = _sanitize(node_id.replace(':', '-'))
    working_dir = os.path.join(DEFAULT_OUTPUT_DIR, f"{safe_name}_{safe_node_id}")

    print(f"  file_key={file_key}  node_id={node_id}  name={node_name}", file=sys.stderr)

    # ── Phase 2: fetch raw content + screenshot ───────────────────────
    print("Phase 2/3: Fetching content and screenshot...", file=sys.stderr)

    os.makedirs(working_dir, exist_ok=True)
    raw_path = os.path.join(working_dir, "raw.json")
    screenshot_path = os.path.join(working_dir, "screenshot.png")

    node_data_full = _fetch_figma_node(file_key, node_id, token)
    if not node_data_full:
        print("Error: Failed to fetch node data (full).", file=sys.stderr)
        sys.exit(1)

    with open(raw_path, 'w', encoding='utf-8') as f:
        json.dump(node_data_full, f, indent=2, ensure_ascii=False)
    print(f"  Raw data saved to: {raw_path}", file=sys.stderr)

    screenshot_data = _fetch_figma_screenshot(file_key, node_id, token, scale=2)
    if not screenshot_data:
        print("Error: Failed to fetch screenshot.", file=sys.stderr)
        sys.exit(1)

    with open(screenshot_path, 'wb') as f:
        f.write(screenshot_data)
    print(f"  Screenshot saved to: {screenshot_path}", file=sys.stderr)

    # ── Phase 3: simplify ─────────────────────────────────────────────
    print("Phase 3/3: Simplifying...", file=sys.stderr)

    simplified_path = os.path.join(working_dir, "simplified.json")

    with open(raw_path, 'r', encoding='utf-8') as f:
        raw_data = json.load(f)

    removed_log: List[Dict] = []
    simplified = _simplify_figma_content(raw_data, removed_log)
    _write_flat_json(simplified_path, simplified)

    removed_log_path = os.path.join(working_dir, "simplified_removed_nodes.json")
    with open(removed_log_path, 'w', encoding='utf-8') as f:
        json.dump({'total_removed': len(removed_log), 'removed_nodes': removed_log},
                    f, indent=2, ensure_ascii=False)

    raw_size = os.path.getsize(raw_path)
    simplified_size = os.path.getsize(simplified_path)
    reduction = (1 - simplified_size / raw_size) * 100 if raw_size > 0 else 0
    print(f"  Input size:  {raw_size:,} bytes ({raw_size/1024:.2f} KB)", file=sys.stderr)
    print(f"  Output size: {simplified_size:,} bytes ({simplified_size/1024:.2f} KB)", file=sys.stderr)
    print(f"  Reduction:   {reduction:.1f}%", file=sys.stderr)
    print(f"  Nodes removed: {len(removed_log)}", file=sys.stderr)

    # ── output result JSON ────────────────────────────────────────────
    result = {
        "figma_url": figma_url,
        "file_key": file_key,
        "node_id": node_id,
        "node_name": node_name,
        "working_dir_path": working_dir,
        "raw_content_path": raw_path,
        "content_screen_path": screenshot_path,
        "simplified_content_path": simplified_path,
    }
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
