#!/usr/bin/env python3
"""
Package the final prefab-hierarchy result into Assets/FigmaData.

Replaces the manual Step 6: creates the output folder, copies referenced files,
and writes the result JSON.

Usage:
  python3 package_result.py                   \
    --figma-url       "<url>"                 \
    --file-key        "<key>"                 \
    --node-id         "<id>"                  \
    --node-name       "<name>"                \
    --summary         "<short description>"   \
    --working-dir     "<working_dir_path>"    \
    --raw-content     "<raw_content_path>"    \
    --prefab-hierarchy "<prefab_hierarchy_path>"
"""

import argparse
import json
import os
import shutil
import sys


ASSETS_ROOT = "Assets/FigmaData"


def sanitize_filename_component(s, max_len=100):
    """Make a string safe for use as a filename/directory component."""
    import re
    safe = re.sub(r'[^\w\-]', '_', s)
    safe = safe.strip('_.')
    safe = re.sub(r'_{2,}', '_', safe)
    if not safe:
        safe = 'unnamed'
    if len(safe) > max_len:
        safe = safe[:max_len].rstrip('_.')
    return safe


def main():
    parser = argparse.ArgumentParser(
        description="Package Figma → prefab hierarchy result into Assets/FigmaData"
    )
    parser.add_argument("--figma-url", required=True, help="The input Figma URL")
    parser.add_argument("--file-key", required=True, help="Figma file key")
    parser.add_argument("--node-id", required=True, help="Figma node id")
    parser.add_argument("--node-name", required=True, help="Figma node name")
    parser.add_argument("--summary", required=True, help="Short description (under 50 words)")
    parser.add_argument("--working-dir", required=True, help="Working directory path from Step 1")
    parser.add_argument("--raw-content", required=True, help="Path to raw Figma content JSON")
    parser.add_argument("--prefab-hierarchy", required=True, help="Path to refined prefab hierarchy JSON")

    args = parser.parse_args()

    # ── derive names ──────────────────────────────────────────────────
    working_dir_name = os.path.basename(os.path.normpath(args.working_dir))

    root_gameobject_name = os.path.splitext(os.path.basename(args.prefab_hierarchy))[0]
    # strip trailing "_hierarchy" if present
    if root_gameobject_name.endswith("_hierarchy"):
        root_gameobject_name = root_gameobject_name[:-len("_hierarchy")]

    safe_root = sanitize_filename_component(root_gameobject_name)
    safe_working = sanitize_filename_component(working_dir_name)

    folder_name = f"{safe_root}_{safe_working}"
    dest_dir = os.path.join(ASSETS_ROOT, folder_name)

    # ── create destination folder ─────────────────────────────────────
    os.makedirs(dest_dir, exist_ok=True)
    print(f"Created: {dest_dir}", file=sys.stderr)

    # ── copy raw content ──────────────────────────────────────────────
    if not os.path.isfile(args.raw_content):
        print(f"Error: raw content file not found: {args.raw_content}", file=sys.stderr)
        sys.exit(1)

    raw_basename = os.path.basename(args.raw_content)
    raw_dest = os.path.join(dest_dir, raw_basename)
    shutil.copy2(args.raw_content, raw_dest)
    raw_rel = os.path.join("Assets", "FigmaData", folder_name, raw_basename)
    print(f"Copied: {args.raw_content} → {raw_dest}", file=sys.stderr)

    # ── copy prefab hierarchy ─────────────────────────────────────────
    if not os.path.isfile(args.prefab_hierarchy):
        print(f"Error: prefab hierarchy file not found: {args.prefab_hierarchy}", file=sys.stderr)
        sys.exit(1)

    hierarchy_basename = os.path.basename(args.prefab_hierarchy)
    hierarchy_dest = os.path.join(dest_dir, hierarchy_basename)
    shutil.copy2(args.prefab_hierarchy, hierarchy_dest)
    hierarchy_rel = os.path.join("Assets", "FigmaData", folder_name, hierarchy_basename)
    print(f"Copied: {args.prefab_hierarchy} → {hierarchy_dest}", file=sys.stderr)

    # ── write result JSON ─────────────────────────────────────────────
    result_basename = f"{folder_name}_hierarchy_result.json"
    result_path = os.path.join(dest_dir, result_basename)

    result = {
        "figma_url": args.figma_url,
        "file_key": args.file_key,
        "node_id": args.node_id,
        "node_name": args.node_name,
        "summary": args.summary,
        "raw_content_path": raw_rel,
        "prefab_hierarchy_path": hierarchy_rel,
    }

    with open(result_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    print(f"Result written: {result_path}", file=sys.stderr)

    # ── stdout: the result path ───────────────────────────────────────
    print(result_path)


if __name__ == "__main__":
    main()
