#!/usr/bin/env python3
"""
Semantic Figma subtree splitter.

This script keeps the LLM out of the raw JSON copying path:
1. `check-split` decides whether the flat source needs semantic splitting.
2. A planner agent reads the flat source and screenshot, then writes semantic groups of node ids.
3. `analyze-plan` finds missed source subtrees before any output files are created.
4. `apply-plan` extracts exact JSON subtrees only from a complete plan.
"""

import argparse
import copy
import json
import os
import re
from typing import Any, Dict, List, Optional, Set, Tuple


DEFAULT_MAX_NODES_BEFORE_SPLIT = 160
TARGET_SUBTREE_NODES = 80
PREFERRED_MIN_SUBTREE_NODES = 60
PREFERRED_MAX_SUBTREE_NODES = 120
SOFT_MAX_SUBTREE_NODES = 140


def load_json(path: str) -> Any:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path: str, data: Any) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def sanitize_name(name: str) -> str:
    name = re.sub(r"[^\w\u4e00-\u9fff-]+", "_", name.strip())
    name = re.sub(r"_+", "_", name).strip("_")
    return name or "Group"


def find_document_roots(data: Dict[str, Any]) -> List[Tuple[str, Dict[str, Any]]]:
    if isinstance(data.get("nodes"), dict):
        roots = []
        for node_id, node_content in data["nodes"].items():
            if isinstance(node_content, dict) and isinstance(node_content.get("document"), dict):
                roots.append((node_id, node_content["document"]))
        if roots:
            return roots
    if isinstance(data, dict):
        return [(str(data.get("id", "root")), data)]
    return []


def is_flat_document(doc: Any) -> bool:
    """Return True if doc is a flat-format document dict."""
    return isinstance(doc, dict) and doc.get("type") == "flat" and isinstance(doc.get("flatNodes"), list)


def get_flat_root_info(document: Dict[str, Any]) -> Dict[str, Any]:
    """Return the root node from a flat document (node without parentId)."""
    flat_nodes = document.get("flatNodes", [])
    for fn in flat_nodes:
        if "parentId" not in fn:
            return fn
    return flat_nodes[0] if flat_nodes else {}


def build_children_index(flat_nodes: List[Dict[str, Any]]) -> Dict[str, List[str]]:
    """Return source-order child ids for every parent."""
    children_of: Dict[str, List[str]] = {}
    for node in flat_nodes:
        parent_id = node.get("parentId")
        if parent_id:
            children_of.setdefault(parent_id, []).append(node["id"])
    return children_of


def build_source_index(flat_nodes: List[Dict[str, Any]]) -> Dict[str, int]:
    """Return each node's authoritative position in the source DFS list."""
    return {
        str(node["id"]): int(node.get("index", index))
        for index, node in enumerate(flat_nodes)
    }


def subtree_range(node: Dict[str, Any], fallback_index: int) -> Tuple[int, int]:
    """Return the inclusive DFS range occupied by a source node's subtree."""
    start = int(node.get("index", fallback_index))
    end = int(node.get("subtreeEndIndex", start))
    return start, max(start, end)


def subtree_nodes(
        flat_nodes: List[Dict[str, Any]],
        node: Dict[str, Any],
        source_index: Dict[str, int],
) -> List[Dict[str, Any]]:
    """Return a source node and all descendants using its DFS interval."""
    fallback = source_index[str(node["id"])]
    start, end = subtree_range(node, fallback)
    return flat_nodes[start:end + 1]


def selected_source_ids(
        flat_nodes: List[Dict[str, Any]],
        selected: Set[str],
        force_keep_root: bool,
) -> Set[str]:
    """Expand selected boundary ids to exact source subtree intervals."""
    node_by_id = {str(node["id"]): node for node in flat_nodes}
    source_index = build_source_index(flat_nodes)
    covered: Set[str] = set()

    for node_id in selected:
        node = node_by_id.get(node_id)
        if node is None:
            continue
        covered.update(
            str(item["id"])
            for item in subtree_nodes(flat_nodes, node, source_index)
        )

    if force_keep_root:
        root = next((node for node in flat_nodes if not node.get("parentId")), None)
        if root is not None:
            covered.add(str(root["id"]))

    return covered


def finalize_normalized_flat_nodes(flat_nodes: List[Dict[str, Any]]) -> None:
    """Recompute local index, depth, siblingIndex, and subtreeEndIndex in place."""
    if not flat_nodes:
        return

    node_by_id = {str(node["id"]): node for node in flat_nodes}
    children_of = build_children_index(flat_nodes)

    for index, node in enumerate(flat_nodes):
        node["index"] = index

    roots = [node for node in flat_nodes if not node.get("parentId")]
    stack: List[Tuple[str, int]] = [
        (str(node["id"]), 0) for node in reversed(roots)
    ]
    while stack:
        node_id, depth = stack.pop()
        node = node_by_id[node_id]
        node["depth"] = depth
        child_ids = children_of.get(node_id, [])
        for sibling_index, child_id in enumerate(child_ids):
            node_by_id[child_id]["siblingIndex"] = sibling_index
        for child_id in reversed(child_ids):
            stack.append((child_id, depth + 1))

    for index in range(len(flat_nodes) - 1, -1, -1):
        node = flat_nodes[index]
        child_ids = children_of.get(str(node["id"]), [])
        node["subtreeEndIndex"] = max(
            (node_by_id[child_id]["subtreeEndIndex"] for child_id in child_ids),
            default=index,
        )


def normalize_subtree(
        source_document: Dict[str, Any],
        included_ids: Set[str],
) -> Dict[str, Any]:
    """Create a standalone, valid flat subtree from source node ids.

    Nodes remain in source DFS order. Any included node whose original parent is
    absent is reparented to the screen root and records its original parent in
    sourceParentId.
    """
    source_nodes = source_document.get("flatNodes", [])
    root = get_flat_root_info(source_document)
    root_id = str(root.get("id", ""))
    included = set(included_ids)
    if root_id:
        included.add(root_id)

    normalized = [
        copy.deepcopy(node)
        for node in source_nodes
        if str(node.get("id", "")) in included
    ]

    for node in normalized:
        node.pop("sourceParentId", None)
        node_id = str(node.get("id", ""))
        if node_id == root_id:
            node.pop("parentId", None)
            node.pop("siblingIndex", None)
            continue

        parent_id = node.get("parentId")
        if parent_id not in included:
            if parent_id:
                node["sourceParentId"] = parent_id
            node["parentId"] = root_id

    finalize_normalized_flat_nodes(normalized)
    return {
        "type": "flat",
        "flatNodes": normalized,
    }


def subtree_size_status(node_count: int) -> str:
    if PREFERRED_MIN_SUBTREE_NODES <= node_count <= PREFERRED_MAX_SUBTREE_NODES:
        return "preferred"
    if node_count > SOFT_MAX_SUBTREE_NODES:
        return "above_soft_max"
    if node_count < PREFERRED_MIN_SUBTREE_NODES:
        return "small"
    return "acceptable"


def build_node_index(data: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """Build a flat id→node index from flat-format data."""
    index: Dict[str, Dict[str, Any]] = {}
    for _root_key, root_document in find_document_roots(data):
        if is_flat_document(root_document):
            for fn in root_document.get("flatNodes", []):
                node_id = fn.get("id")
                if node_id is not None:
                    index[str(node_id)] = fn
    return index


def build_parent_map(data: Dict[str, Any]) -> Dict[str, str]:
    """Return a mapping from every node_id to its parent_id."""
    parent_map: Dict[str, str] = {}
    for _root_key, root_document in find_document_roots(data):
        if is_flat_document(root_document):
            for fn in root_document.get("flatNodes", []):
                node_id = fn.get("id")
                pid = fn.get("parentId")
                if node_id and pid:
                    parent_map[str(node_id)] = pid
    return parent_map


def collect_node_ids(root_document_or_node: Any) -> List[str]:
    """Collect all node ids from a flat document, a flat node list, or a nested node tree."""
    ids: List[str] = []
    if isinstance(root_document_or_node, dict):
        if is_flat_document(root_document_or_node):
            for fn in root_document_or_node.get("flatNodes", []):
                nid = fn.get("id")
                if nid:
                    ids.append(str(nid))
            return ids
        # Nested: walk children recursively
        node_id = root_document_or_node.get("id")
        if node_id is not None:
            ids.append(str(node_id))
        for child in root_document_or_node.get("children", []) or []:
            if isinstance(child, dict):
                ids.extend(collect_node_ids(child))
    return ids


def filter_tree(document: Dict[str, Any], selected: Set[str], force_keep_root: bool = False) -> Optional[Dict[str, Any]]:
    """Extract selected source subtrees as a normalized standalone flat document."""
    if not is_flat_document(document):
        return None

    flat_nodes = document.get("flatNodes", [])
    covered = selected_source_ids(flat_nodes, selected, force_keep_root)
    return normalize_subtree(document, covered)


def ancestors_of(node_id: str, parent_map: Dict[str, str]) -> Set[str]:
    """Return the set of all ancestor ids for a node (inclusive: includes self)."""
    result = {node_id}
    current = node_id
    while current in parent_map:
        current = parent_map[current]
        result.add(current)
    return result


def is_ancestor_of_any_boundary(
        node_id: str,
        all_boundary_ids: Set[str],
        parent_map: Dict[str, str],
) -> bool:
    """Return True if node_id is an ancestor of any selected boundary node.

    A is an ancestor of B if walking up B's parent chain reaches A.
    """
    for boundary_id in all_boundary_ids:
        anc = ancestors_of(boundary_id, parent_map)
        if node_id in anc:
            return True
    return False


def check_boundary_disjointness(
        flat_nodes: List[Dict[str, Any]],
        plan: Dict[str, Any],
        parent_map: Dict[str, str],
        root_id: str,
) -> List[Dict[str, Any]]:
    """Check that no two groups select boundary nodes with ancestor/descendant relationship.

    Returns a list of overlap violations (empty if plan is disjoint).
    """
    node_by_id = {n['id']: n for n in flat_nodes}

    # Collect raw selected nodeIds per group (before subtree expansion)
    selected_per_group: List[Set[str]] = []
    group_names: List[str] = []
    for group in plan.get('groups', []):
        node_ids = {str(nid) for nid in group.get('nodeIds', []) if str(nid) in node_by_id}
        selected_per_group.append(node_ids)
        group_names.append(sanitize_name(str(group.get('name', f'Group{len(group_names):03d}'))))

    violations: List[Dict[str, Any]] = []
    for i in range(len(selected_per_group)):
        for j in range(i + 1, len(selected_per_group)):
            for nid_a in selected_per_group[i]:
                ancestors_a = ancestors_of(nid_a, parent_map)
                for nid_b in selected_per_group[j]:
                    if nid_b in ancestors_a and nid_b != root_id:
                        # A is ancestor of B (or same node)
                        violations.append({
                            'type': 'ancestor_descendant',
                            'ancestor': nid_a,
                            'ancestorGroupIndex': i,
                            'ancestorGroupName': group_names[i],
                            'descendant': nid_b,
                            'descendantGroupIndex': j,
                            'descendantGroupName': group_names[j],
                        })
                    else:
                        ancestors_b = ancestors_of(nid_b, parent_map)
                        if nid_a in ancestors_b and nid_a != root_id:
                            # B is ancestor of A
                            violations.append({
                                'type': 'ancestor_descendant',
                                'ancestor': nid_b,
                                'ancestorGroupIndex': j,
                                'ancestorGroupName': group_names[j],
                                'descendant': nid_a,
                                'descendantGroupIndex': i,
                                'descendantGroupName': group_names[i],
                            })

    return violations


def validate_plan_coverage(data: Dict[str, Any], plan: Dict[str, Any]) -> Dict[str, Any]:
    """Check that every node in the original data is covered by the plan."""
    node_index = build_node_index(data)
    roots = find_document_roots(data)
    all_node_ids = set(node_index.keys())
    covered_node_ids: Set[str] = set()
    referenced_node_ids: List[str] = []
    missing: Dict[str, List[str]] = {}

    for i, group in enumerate(plan.get("groups", [])):
        group_name = sanitize_name(str(group.get("name", f"Group{i:03d}")))
        node_ids = [str(node_id) for node_id in group.get("nodeIds", [])]
        referenced_node_ids.extend(node_ids)
        selected = {node_id for node_id in node_ids if node_id in node_index}
        missing_ids = [node_id for node_id in node_ids if node_id not in node_index]
        if missing_ids:
            missing[group_name] = missing_ids

        for _root_key, root_document in roots:
            filtered = filter_tree(root_document, selected, force_keep_root=True)
            if filtered is not None:
                covered_node_ids.update(collect_node_ids(filtered))

    uncovered = sorted(all_node_ids - covered_node_ids)
    duplicate_references = sorted(
        node_id for node_id in set(referenced_node_ids) if referenced_node_ids.count(node_id) > 1
    )

    result: Dict[str, Any] = {
        "valid": not uncovered and not missing,
        "totalNodeCount": len(all_node_ids),
        "coveredNodeCount": len(covered_node_ids),
        "uncoveredNodeCount": len(uncovered),
        "uncoveredNodeIds": uncovered,
    }
    if missing:
        result["missingNodeIds"] = missing
    if duplicate_references:
        result["duplicateReferencedNodeIds"] = duplicate_references
    return result


def analyze_plan(data: Dict[str, Any], plan: Dict[str, Any]) -> Dict[str, Any]:
    """Return disjointness violations and genuinely missed subtree roots.

    Validates that boundary nodes are disjoint (no ancestor/descendant pairs across groups)
    and finds genuinely missed nodes (uncovered AND not an ancestor of any selected boundary).

    Output shape:
    {
      "planValidation": {"valid": bool, "overlaps": [...]},
      "missedRoots": [
        {"nodeId": str, "nodeName": str, "nodeType": str, "validTargetGroups": [int, ...]}
      ]
    }
    """
    node_index = build_node_index(data)
    parent_map = build_parent_map(data)
    roots = find_document_roots(data)
    all_node_ids = set(node_index.keys())

    # Find the root id for disjointness exclusion
    root_id = ""
    flat_nodes_ref: List[Dict[str, Any]] = []
    for _root_key, root_document in roots:
        if is_flat_document(root_document):
            ri = get_flat_root_info(root_document)
            if ri:
                root_id = ri.get("id", "")
            flat_nodes_ref = root_document.get("flatNodes", [])
            break

    # --- Disjointness pre-check ---
    raw_overlaps = check_boundary_disjointness(flat_nodes_ref, plan, parent_map, root_id)
    overlaps: List[Dict[str, Any]] = [
        {
            "ancestor": o["ancestor"],
            "ancestorGroup": o["ancestorGroupIndex"],
            "descendant": o["descendant"],
            "descendantGroup": o["descendantGroupIndex"],
        }
        for o in raw_overlaps
    ]

    # --- Collect all selected boundary nodeIds (for extended missed-root logic) ---
    all_boundary_ids: Set[str] = set()
    for group in plan.get("groups", []):
        for nid in group.get("nodeIds", []):
            if str(nid) in node_index:
                all_boundary_ids.add(str(nid))

    covered_ids: Set[str] = set()
    group_covered: List[Set[str]] = []

    for index, group in enumerate(plan.get("groups", [])):
        node_ids = [str(node_id) for node_id in group.get("nodeIds", [])]
        selected = {node_id for node_id in node_ids if node_id in node_index}
        current_covered: Set[str] = set()

        for _root_key, root_document in roots:
            filtered = filter_tree(root_document, selected, force_keep_root=True)
            if filtered is not None:
                current_covered.update(collect_node_ids(filtered))

        covered_ids.update(current_covered)
        group_covered.append(current_covered)

    uncovered_ids = all_node_ids - covered_ids

    # Extended missed-root detection:
    # A node is a "genuine missed root" if it is uncovered AND either:
    #   (a) its parent IS in the covered set (existing rule), or
    #   (b) its parent is an ancestor of some selected boundary node
    # BUT the node itself must NOT be an ancestor of any selected boundary node
    # (ancestors of selected boundaries are "explained" misses — not genuine).
    missed_root_ids: Set[str] = set()
    for node_id in uncovered_ids:
        if node_id in all_boundary_ids:
            continue
        # Exclude ancestors of selected boundary nodes
        if is_ancestor_of_any_boundary(node_id, all_boundary_ids, parent_map):
            continue
        parent_id = parent_map.get(node_id)
        if parent_id is None:
            # Root-level uncovered node: its parent must be the force-included root
            if root_id and root_id in covered_ids:
                missed_root_ids.add(node_id)
            continue
        # Rule (a): parent is in covered set
        if parent_id in covered_ids:
            missed_root_ids.add(node_id)
            continue
        # Rule (b): parent is an ancestor of some selected boundary node
        if is_ancestor_of_any_boundary(parent_id, all_boundary_ids, parent_map):
            missed_root_ids.add(node_id)
            continue

    missed_roots: List[Dict[str, Any]] = []
    for node_id in sorted(
            missed_root_ids,
            key=lambda current_id: int(node_index[current_id].get("index", 0)),
    ):
        node = node_index[node_id]
        parent_id = parent_map.get(node_id)

        # validTargetGroups: group indices whose coverage includes the parent
        valid_target_indices = [
            i
            for i, covered in enumerate(group_covered)
            if parent_id is None or parent_id in covered
        ]

        missed_roots.append({
            "nodeId": node_id,
            "nodeName": node.get("name", ""),
            "nodeType": node.get("type", ""),
            "validTargetGroups": valid_target_indices,
        })

    return {
        "planValidation": {
            "valid": not overlaps and not missed_roots,
            "overlaps": overlaps,
        },
        "missedRoots": missed_roots,
    }


def extract_groups(data: Dict[str, Any], plan: Dict[str, Any], output_dir: str) -> Dict[str, Any]:
    """Extract flat subtree documents from data according to the plan."""
    node_index = build_node_index(data)
    roots = find_document_roots(data)
    output_files: List[Dict[str, Any]] = []
    missing: Dict[str, List[str]] = {}

    os.makedirs(output_dir, exist_ok=True)

    for i, group in enumerate(plan.get("groups", [])):
        group_name = sanitize_name(str(group.get("name", f"Group{i:03d}")))
        node_ids = [str(node_id) for node_id in group.get("nodeIds", [])]
        selected = {node_id for node_id in node_ids if node_id in node_index}
        missing_ids = [node_id for node_id in node_ids if node_id not in node_index]
        if missing_ids:
            missing[group_name] = missing_ids
        if not selected:
            continue

        if isinstance(data.get("nodes"), dict):
            subtree_data = copy.deepcopy(data)
            subtree_data["nodes"] = {}
            for root_key, root_document in roots:
                filtered = filter_tree(root_document, selected, force_keep_root=True)
                if filtered is not None:
                    source_node = data["nodes"].get(root_key, {})
                    subtree_node = {k: copy.deepcopy(v) for k, v in source_node.items() if k != "document"}
                    subtree_node["document"] = filtered
                    subtree_data["nodes"][root_key] = subtree_node
        else:
            root_document = roots[0][1]
            subtree_data = filter_tree(root_document, selected, force_keep_root=True)

        output_file = os.path.join(output_dir, f"semantic_{i:03d}_{group_name}.json")
        write_json(output_file, subtree_data)
        output_node_count = len(build_node_index(subtree_data))
        output_files.append({
            "name": group_name,
            "description": group.get("description", ""),
            "nodeIds": node_ids,
            "path": output_file,
            "nodeCount": output_node_count,
            "sizeStatus": subtree_size_status(output_node_count),
        })

    result = {
        "subtrees": [item["path"] for item in output_files],
        "groups": output_files,
    }
    if missing:
        result["missingNodeIds"] = missing
    return result


def command_check_split(args: argparse.Namespace) -> None:
    data = load_json(args.input_file)
    input_nodes = sum(
        len(root_document.get("flatNodes", []))
        for _root_key, root_document in find_document_roots(data)
        if is_flat_document(root_document)
    )
    needs_split = input_nodes > args.max_nodes_before_split

    print(json.dumps({
        "needs_split": needs_split,
        "input_nodes": input_nodes,
        "max_nodes_before_split": args.max_nodes_before_split,
        "target_subtree_nodes": TARGET_SUBTREE_NODES,
        "preferred_subtree_node_range": [
            PREFERRED_MIN_SUBTREE_NODES,
            PREFERRED_MAX_SUBTREE_NODES,
        ],
        "soft_max_subtree_nodes": SOFT_MAX_SUBTREE_NODES,
        "subtrees": [] if needs_split else [args.input_file],
    }, ensure_ascii=False, indent=2))


def command_analyze_plan(args: argparse.Namespace) -> None:
    data = load_json(args.input_file)
    plan = load_json(args.plan_file)
    result = analyze_plan(data, plan)
    if args.output:
        write_json(args.output, result)
    print(json.dumps(result, ensure_ascii=False, indent=2))


def command_apply_plan(args: argparse.Namespace) -> None:
    data = load_json(args.input_file)
    plan = load_json(args.plan_file)

    # Full analysis: coverage + disjointness
    analysis = analyze_plan(data, plan)
    validation = analysis["planValidation"]

    if not validation["valid"]:
        errors: List[str] = []
        if validation.get("overlaps"):
            errors.append(f"{len(validation['overlaps'])} overlapping boundary pairs")
        if analysis.get("missedRoots"):
            errors.append(f"{len(analysis['missedRoots'])} missed roots")
        print(json.dumps({
            "error": "plan rejected — " + "; ".join(errors),
            "planValidation": validation,
            "missedRoots": analysis["missedRoots"],
        }, ensure_ascii=False, indent=2))
        raise SystemExit(1)

    output_dir = args.output_dir or f"{os.path.splitext(args.input_file)[0]}_semantic_subtrees"
    result = extract_groups(data, plan, output_dir)
    print(json.dumps(result, ensure_ascii=False, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description="Semantic Figma subtree split helper")
    subparsers = parser.add_subparsers(dest="command", required=True)

    check_split = subparsers.add_parser(
        "check-split",
        help="Decide whether a flat Figma source needs semantic splitting",
    )
    check_split.add_argument("input_file")
    check_split.add_argument(
        "--max-nodes-before-split",
        type=int,
        default=DEFAULT_MAX_NODES_BEFORE_SPLIT,
    )
    check_split.set_defaults(func=command_check_split)

    analyze_plan_parser = subparsers.add_parser(
        "analyze-plan",
        help="Analyze plan coverage and return complete missed source subtrees",
    )
    analyze_plan_parser.add_argument("input_file")
    analyze_plan_parser.add_argument("plan_file")
    analyze_plan_parser.add_argument("-o", "--output")
    analyze_plan_parser.set_defaults(func=command_analyze_plan)

    apply_plan = subparsers.add_parser("apply-plan", help="Extract exact subtrees from a filled plan")
    apply_plan.add_argument("input_file")
    apply_plan.add_argument("plan_file")
    apply_plan.add_argument("--output-dir")
    apply_plan.set_defaults(func=command_apply_plan)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
