# How to simplify figma content


## Trim unnecessary information

### 1. Cascading removal for invisible nodes

A node should be removed if **BOTH** conditions are true:
- It has **no visual properties** (no fills, strokes, background, effects)
- **AND** (either):
  - It has no children, OR
  - **All its children are also removed** (cascading removal)

**Examples:**
```
Parent (no fills)           → REMOVED (no fills + all children removed)
  ├── Child1 (no fills)     → REMOVED
  └── Child2 (no fills)     → REMOVED

Parent (no fills)           → KEPT (has visible child)
  ├── Child1 (has fills)    → KEPT
  └── Child2 (no fills)     → REMOVED
```

### 2. Remove explicitly invisible nodes

- Check `visible=false` on node
- Check `opacity=0` on node
- Check `visible=false` or `opacity=0` on fills

### 3. Keep only essential fields

For each node, only keep:
- `id`
- `name`
- `type`
- `children`
- `absoluteBoundingBox`
- `fills`
- `characters`

### 4. Simplify fills

Only keep:
- `type`
- `color`

### 5. Round floats

Keep at most 2 decimal places for all float numbers.


## Output the simplified content

Do not change the original nodes order and parent-child relation.
