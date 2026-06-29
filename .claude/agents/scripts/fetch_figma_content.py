#!/usr/bin/env python3
"""
Fetch figma design content and its screenshot.

Usage:
  # Get node metadata only (no files saved, outputs JSON to stdout)
  python fetch_figma_content.py --name-only <figma_url> [output_dir]

  # Fetch content and screenshot to explicit paths (no stdout output)
  python fetch_figma_content.py <figma_url> --raw-output <path> --screenshot-output <path>

The Figma API token is read from FIGMA_API_TOKEN.
All progress/log messages go to stderr.
"""

import sys
import json
import urllib.request
import urllib.parse
import ssl
import re
import os


# --- Figma API ---

def create_ssl_context():
    """Create SSL context that works on macOS."""
    context = ssl.create_default_context()
    try:
        import certifi
        context.load_verify_locations(certifi.where())
    except ImportError:
        pass
    return context


def parse_figma_url(url):
    """Extract file key and node ID from Figma URL."""
    design_pattern = r'/design/([a-zA-Z0-9_-]+)(?:/[^?]*)?\?.*node-id=([0-9-]+)'
    file_pattern = r'/file/([a-zA-Z0-9_-]+)(?:/[^?]*)?\?.*node-id=([0-9-]+)'

    for pattern in [design_pattern, file_pattern]:
        match = re.search(pattern, url)
        if match:
            return match.group(1), match.group(2)
    return None, None


def fetch_figma_node(file_key, node_id, token):
    """Fetch a specific node from a Figma file using the REST API."""
    encoded_node_id = node_id.replace('-', ':')
    url = f"https://api.figma.com/v1/files/{file_key}/nodes?ids={encoded_node_id}"
    headers = {
        "X-Figma-Token": token,
        "Content-Type": "application/json"
    }
    req = urllib.request.Request(url, headers=headers)
    try:
        context = create_ssl_context()
        with urllib.request.urlopen(req, context=context) as response:
            return json.loads(response.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        body = e.read().decode('utf-8')
        print(f"HTTP Error: {e.code} - {e.reason}\nResponse: {body}", file=sys.stderr)
        return None
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return None


def fetch_figma_node_screenshot(file_key, node_id, token, scale=2):
    """Fetch a screenshot of a specific node from Figma using the REST API."""
    encoded_node_id = node_id.replace('-', ':')
    url = f"https://api.figma.com/v1/images/{file_key}?ids={encoded_node_id}&scale={scale}&format=png"
    headers = {
        "X-Figma-Token": token,
        "Content-Type": "application/json"
    }
    req = urllib.request.Request(url, headers=headers)
    try:
        context = create_ssl_context()
        with urllib.request.urlopen(req, context=context) as response:
            data = json.loads(response.read().decode('utf-8'))
            if encoded_node_id in data.get('images', {}):
                image_url = data['images'][encoded_node_id]
                if image_url:
                    with urllib.request.urlopen(image_url, context=context) as img_response:
                        return img_response.read()
        return None
    except urllib.error.HTTPError as e:
        body = e.read().decode('utf-8')
        print(f"HTTP Error: {e.code} - {e.reason}\nResponse: {body}", file=sys.stderr)
        return None
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return None


# --- Sanitize ---

def sanitize_filename_component(s, max_len=100):
    """Make a string safe for use as a filename/directory component across platforms.

    Keeps only alphanumeric characters, underscores, and hyphens.
    Handles edge cases: empty result, reserved Windows names, excessive length.
    """
    safe = re.sub(r'[^\w\-]', '_', s)
    safe = safe.strip('_.')
    safe = re.sub(r'_{2,}', '_', safe)
    if not safe:
        safe = 'unnamed'
    if len(safe) > max_len:
        safe = safe[:max_len].rstrip('_.')
    return safe


# --- Main ---

def get_figma_token():
    token = os.environ.get("FIGMA_API_TOKEN")
    if not token:
        print("Error: FIGMA_API_TOKEN environment variable is not set.", file=sys.stderr)
        sys.exit(1)
    return token


def main():
    name_only = '--name-only' in sys.argv
    figma_token = get_figma_token()

    if name_only:
        # ── --name-only mode: just get node metadata, no files ──
        argv = [a for a in sys.argv[1:] if a != '--name-only']
        if len(argv) < 1:
            print("Usage: python fetch_figma_content.py --name-only <figma_url> [output_dir]", file=sys.stderr)
            sys.exit(1)
        figma_url = argv[0]
        output_dir = argv[1] if len(argv) >= 2 else "Library/FigmaToUGUI"

        file_key, node_id = parse_figma_url(figma_url)
        if not file_key or not node_id:
            print("Error: Could not parse Figma URL.", file=sys.stderr)
            sys.exit(1)

        print(f"Fetching node name for {file_key}/{node_id}...", file=sys.stderr)
        node_data = fetch_figma_node(file_key, node_id, figma_token)
        if not node_data:
            print("Failed to fetch node data.", file=sys.stderr)
            sys.exit(1)

        doc = None
        for nid, node_info in node_data.get("nodes", {}).items():
            if "document" in node_info:
                doc = node_info["document"]
                break

        if not doc:
            print("No document node found in fetched data.", file=sys.stderr)
            sys.exit(1)

        root_node_id = doc.get("id", node_id).replace(":", "-")
        root_node_name = doc.get("name", "unknown")
        safe_name = sanitize_filename_component(root_node_name)
        safe_node_id = sanitize_filename_component(root_node_id)
        working_dir = os.path.join(output_dir, f"{safe_name}_{safe_node_id}")

        result = {
            "file_key": file_key,
            "node_id": node_id,
            "node_name": root_node_name,
            "working_dir_path": working_dir,
        }
        print(json.dumps(result, ensure_ascii=False))
        return

    # ── Normal mode: fetch content + screenshot to explicit paths ──
    parse_mode = '--raw-output' in sys.argv and '--screenshot-output' in sys.argv
    if not parse_mode:
        print("Error: --raw-output and --screenshot-output are required", file=sys.stderr)
        print("Usage: python fetch_figma_content.py <figma_url> --raw-output <path> --screenshot-output <path>", file=sys.stderr)
        sys.exit(1)

    # Parse arguments
    raw_output = None
    screenshot_output = None
    positional = []
    i = 1
    while i < len(sys.argv):
        a = sys.argv[i]
        if a == '--raw-output' and i + 1 < len(sys.argv):
            raw_output = sys.argv[i + 1]
            i += 2
        elif a == '--screenshot-output' and i + 1 < len(sys.argv):
            screenshot_output = sys.argv[i + 1]
            i += 2
        else:
            positional.append(a)
            i += 1

    if len(positional) < 1:
        print("Usage: python fetch_figma_content.py <figma_url> --raw-output <path> --screenshot-output <path>", file=sys.stderr)
        sys.exit(1)

    figma_url = positional[0]

    # Parse URL
    file_key, node_id = parse_figma_url(figma_url)
    if not file_key or not node_id:
        print("Error: Could not parse Figma URL.", file=sys.stderr)
        sys.exit(1)

    print(f"File Key: {file_key}", file=sys.stderr)
    print(f"Node ID: {node_id}", file=sys.stderr)

    # Fetch node data
    print("Fetching node data...", file=sys.stderr)
    node_data = fetch_figma_node(file_key, node_id, figma_token)
    if not node_data:
        print("Failed to fetch node data.", file=sys.stderr)
        sys.exit(1)

    doc = None
    root_node_id = None
    for nid, node_info in node_data.get("nodes", {}).items():
        if "document" in node_info:
            doc = node_info["document"]
            root_node_id = doc.get("id", node_id).replace(":", "-")
            break

    if not doc:
        print("No document node found in fetched data.", file=sys.stderr)
        sys.exit(1)

    root_node_name = doc.get("name", "unknown")

    # Ensure parent directory exists
    os.makedirs(os.path.dirname(raw_output) or ".", exist_ok=True)

    # Save raw content
    with open(raw_output, 'w', encoding='utf-8') as f:
        json.dump(node_data, f, indent=2, ensure_ascii=False)
    print(f"Raw data saved to: {raw_output}", file=sys.stderr)

    # Fetch and save screenshot
    print("Fetching node screenshot...", file=sys.stderr)
    screenshot_data = fetch_figma_node_screenshot(file_key, node_id, figma_token, scale=2)
    if screenshot_data:
        os.makedirs(os.path.dirname(screenshot_output) or ".", exist_ok=True)
        with open(screenshot_output, 'wb') as f:
            f.write(screenshot_data)
        print(f"Screenshot saved to: {screenshot_output}", file=sys.stderr)
    else:
        print("Warning: Failed to fetch screenshot", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
