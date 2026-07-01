
## Child Order Rules

For each GameObject's direct children, reorder them using these priorities:

1. Vertical position: top elements come before bottom elements.
2. Horizontal position: left elements come before right elements.
3. Semantic cohesion: keep a coherent control, label, icon, state group, or reusable module together.
4. Do not move a child outside its parent unless the current hierarchy is clearly wrong for Unity prefab authoring and the screenshot plus Figma content prove the better parent.

**IMPORTANT:** These rules only apply when sibling children do NOT visually overlap. When two children's actual rendered pixels overlap, you MUST follow the **Rendering Area Overlap Rule** below instead — it determines the correct order and overrides rules 1–4.

This order is intended for Unity hierarchy readability for non-overlapping siblings. Background/foreground draw order is handled by the overlap report, not by a separate visual-layer sorting rule.

## Rendering Area Overlap Rule

### Problem

Two sibling children A and B can be reordered freely in the Unity hierarchy *only if* their actual rendered pixels never overlap on screen.
A container's `bounds` (which enclose all its descendants) can span a huge area — so comparing `bounds` directly produces false positives: it marks two nodes as "overlapping" when their visible content sits in completely separate regions.

You must compare **real rendering areas**, not container bounds.

The resulting overlap report is json formatted as follows:

```json
{
  "overlapConstraints": [
    {
      "parentId": "4590:9919",
      "parentName": "parentNodeName",
      "childA": { "nodeId": "4590:9920", "name": "childANodeName", "figmaSiblingIndex": 0 },
      "childB": { "nodeId": "4590:9921", "name": "childBNodeName", "figmaSiblingIndex": 1 }
    }
  ],
  "summary": {
    "parentsWithChildren": 30,
    "totalSiblingPairs": 403,
    "overlappingPairs": 18
  }
}
```

Each entry in `overlapConstraints` means the two children's rendered pixels overlap — their relative order **MUST** be preserved.
Synthetic containers have `figmaSiblingIndex: null`. Use spatial position and the screenshot to resolve these rare cases.

### Apply the rule

| Condition | Rule                                                                                                                                                                                                                |
|-----------|---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| Pair is **NOT** in `overlapConstraints` | A and B can be ordered freely. Use Child Order Rules 1–4.                                                                                                                                                           |
| Pair **IS** in `overlapConstraints` | A and B **MUST** preserve their Figma relative order: the child with the **higher** `figmaSiblingIndex` must appear **later** in the Unity hierarchy children array. This rule overrides all Child Order Rules 1–4. |

If either child has a `null` `figmaSiblingIndex`, determine the correct stacking from the screenshot and the visible pixel order.

### What "must preserve Figma relative order" means

It means the **relative order** of A and B must match Figma's relative order — it does NOT mean their `siblingIndex` values must stay the same. Example:

- Figma: A.siblingIndex = 1, B.siblingIndex = 3 → A renders behind B visually
- Unity hierarchy: A must come **before** B in the children array (A at any array index, B at any later array index)
- Valid: children = `[..., A, ..., B, ...]`
- Valid (even with other nodes between them): children = `[A, X, Y, B]`
- Invalid: children = `[B, ..., A, ...]` (swaps relative order, breaks rendering)
