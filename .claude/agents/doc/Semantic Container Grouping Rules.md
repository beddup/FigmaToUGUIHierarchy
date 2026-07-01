
## Semantic Container Grouping Rules

The prefab hierarchy is **NOT** a mirror of the Figma node tree. A human Unity developer introduces intermediate `container` gameobjects to organize sibling children that are semantically, logically, or spatially related

You MUST introduce synthetic grouping containers where appropriate, following the patterns below.

### General principle

For any set of sibling children that share a common semantic purpose, spatial region, or logical function, wrap them in a new `container` gameobject.
The container becomes a direct child of the original parent, and the grouped children become its children, preserving their relative order.
When Figma hierarchy, naming, or bounds do not make the relationship clear, inspect the screenshot and infer how the visible UI would be perceived by a user. The screenshot is the tie-breaker for deciding whether nearby elements belong to the same semantic group.

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

### Functional areas inside complex modules

A complex module may need internal functional grouping even when the original Figma tree is flat or only partially grouped. Do not rely only on existing Figma parent groups. Use spatial and semantic evidence to group siblings that together represent one functional area, information unit, visual state, row column, card section, control cluster, or repeated item.

In this context, a complex module is a single perceived UI unit that contains several different responsibilities inside it. It is not just a broad screen region, and it is not a simple leaf visual. 

A container likely represents a complex module when several of these signals are present:

- It has many direct children, especially 6 or more non-container renderable children.
- It contains a background or frame plus multiple foreground elements.
- It mixes different semantic roles such as image, text, icon, badge, value, decoration, and state indicator.
- It has multiple spatial zones or columns inside one perceived unit.
- It contains more than one information unit, such as identity plus score, reward plus value, title plus status, or icon plus label plus action.
- Its direct children would be awkward for a Unity author to scan, select, animate, hide, or update as one flat list.

If a container has many direct children, actively look for meaningful subgroups that would make the Unity prefab easier to author and maintain. A good grouping should reflect how a user or UI developer understands the module, not merely how Figma happened to store the layers.

Common evidence includes:

- Multiple sibling elements with similar size, shape, naming pattern, spacing, or alignment.
- Nearby labels, icons, badges, shadows, highlights, or state visuals that visually attach to the same area.
- Text values or labels that correspond to a specific background, slot, tab, step, card section, row column, or cell.
- A selected, active, disabled, locked, completed, or highlighted state visual that belongs to one logical unit.
- Functional columns or sections such as rank, identity, avatar/name, reward, score, action, title, description, status, and controls.

When a parent contains visual elements and corresponding text/icon/state siblings, wrap each logical area into a synthetic `container`. Each area container should contain all visual and textual elements that describe that area, while preserving the required render order inside the area.

If the mapping is ambiguous from node names or bounds alone, use the screenshot to understand which text, icon, highlight, shadow, badge, or state visual is visually attached to which functional area.

Do not leave a complex module as a long flat list of backgrounds, icons, labels, values, badges, and state visuals when those children actually form smaller functional areas.

### Container nodeId convention

A synthetic container introduced by this step does not correspond to any single Figma node.
Use a derived nodeId with the pattern `<parentNodeId>:group_<GameObjectName>`. The `parentNodeId` is the id of the node that was the original common parent of the grouped children.
- Example: parent `"11:22"`, gameObjectName `"TopBar"` → `nodeId: "11:22:group_TopBar"`

The synthetic container's `nodeName` should equal its `nodeId`.
