
## Semantic Container Grouping Rules

The prefab hierarchy is **NOT** a mirror of the Figma node tree. A human Unity developer introduces intermediate `container` gameobjects to organize sibling children that are semantically, logically, or spatially related

You MUST introduce synthetic grouping containers where appropriate, following the patterns below.

### General principle

For any set of sibling children that share a common semantic purpose, spatial region, or logical function, wrap them in a new `container` gameobject.
The container becomes a direct child of the original parent, and the grouped children become its children, preserving their relative order.

### Spatial region patterns

- **Top region**: controls, titles, indicators, or decorative elements anchored to the top edge of the screen or parent → group into a top-bar container.
- **Bottom region**: action bars, navigation controls, footer frames, or persistent bottom UI anchored to the bottom edge → group into a bottom-bar container.
- **Side regions**: left-aligned or right-aligned panels, drawers, or control clusters that form a coherent side area → group into a side container.
- **Center region**: the main content body that fills the middle of the screen → group into a content container.

### Other grouping triggers

- **Functional cohesion**: a label, icon, and state indicator that together express one piece of information.
- **Repeated structure**: multiple peer instances that share the same internal composition — the grouping container signals the repetition boundary.
- **Interactive cluster**: a button and its adjacent supplementary controls that form one interactive region.
- **Visibility or state unit**: elements that show, hide, or change together as a group.

### Container nodeId convention

A synthetic container introduced by this step does not correspond to any single Figma node.
Use a derived nodeId with the pattern `<parentNodeId>:group_<GameObjectName>`. The `parentNodeId` is the id of the node that was the original common parent of the grouped children.
- Example: parent `"11:22"`, gameObjectName `"TopBar"` → `nodeId: "11:22:group_TopBar"`

The synthetic container's `nodeName` should equal its `nodeId`.
