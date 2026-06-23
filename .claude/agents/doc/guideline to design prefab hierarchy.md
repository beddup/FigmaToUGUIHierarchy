This guideline help you to design unity prefab hierarchy from figma content, in a Unity UGUI developer perspective.

## Concept

### node
- basic element in figma content data, stored in the `flatNodes` array in **DFS pre-order**;
- each node has: `id`, `name`, `index` (array position), `subtreeEndIndex` (where its subtree ends), `depth`, `parentId` (parent's id), `siblingIndex` (z-order), `type`, `bounds`, `fills`, `text`;
- a node's **children** = all nodes with matching `parentId` in the range `[index+1, subtreeEndIndex]`;
- a node's **subtree** = the contiguous block `[index, subtreeEndIndex]` in the array — skipping a subtree means skipping all nodes in that range.

### gameobject
- basic element in prefab hierarchy;
- defines its category, children, and the corresponding figma node id.

There are 4 categories:
- `image` : a logical, meaningful, complete and independent visual design element;
- `text` : text;
- `color` : a rectangle filled with color;
- `container` : render nothing, a container grouping its children.

## Create gameobject

walk through the flatNodes array in DFS order, for each node, find where it is in the screenshot, figure out what it is.

**MUST FOLLOW** the following rules when creating gameobject:(if rules conflict for node, the front rule takes advantage)

- if the node is TEXT type, create a `text` category gameobject for it;
- if the node is COMPONENT type, create a `image` category gameobject for it;
- if the node is RECTANGLE type and any of its fill is SOLID, create a `color` category gameobject for it;
- if the node is INSTANCE type, create a gameobject for it, and the category depends on:
  1. if its visual content represents a single design element, its category MUST be `image`
  2. otherwise, its category is `container`
- for an `image` category gameobject, any descendant node in its subtree range `[index+1, subtreeEndIndex]` MUST NOT become a separate gameobject.
- if a node and its descendants together represent a single design element, create an `image` category gameobject for it, and skip all nodes in its subtree range `[index+1, subtreeEndIndex]`

Review your gameobjects:
- make sure all gameobject are all independent, meaningful and reusable UI element
- `text`, `image`, and `color` category gameobject must have a valid nodeId from figma content

## Semantic Container Grouping Rules

**MANDATORY**: Your prefab hierarchy is not a mirror of the figma node tree, but don't mess the front-back order (preserve `siblingIndex` z-order)

A human Unity developer introduces intermediate `container` gameobjects to organize sibling children that are semantically, logically, or spatially related — this keeps the hierarchy clean, readable, and maintainable for future authors.

When a text node is semantically or logically related to another image gameobject, it MUST be a child of that gameobject
Review you hierarchy based on the screenshot, not the node tree, util it match the screenshot

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


## Naming
- Use PascalCase for gameObject names
- Give gameObject a meaningful descriptive name.


## Common Patterns:

### Button with text

The text on button should be a child of the button gameobject

### Panel background with different pieces or parts

Node A : background group
|---- Node B : represent a part of the background
|---- Node C : represent a part of the background
|---- Node D : represent a part of the background

Node A represent a background image. it should be a image category gameobject. and Node B,C,D are ignored

### visual elements at top

if they are parts of top area in the design whatever the target screen size, you should create a `container` gameobject to group them

### visual elements at bottom

if they are parts of bottom area in the design whatever the target screen size, you should create a `container` gameobject to group them