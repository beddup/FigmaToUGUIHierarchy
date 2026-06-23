# Figma2UGUI Hierarchy

A multi-agent workflow compatible with both Claude Code and Codex. It reads a
specific Figma node and its screenshot, interprets the design semantics, and
generates a prefab hierarchy JSON for downstream processing in the Unity Editor.

The generated artifact is not a Unity `.prefab` containing Unity components.
Instead, it is a Unity uGUI-oriented structural description containing
GameObject categories, meaningful names, button semantics, child ordering, and
responsive alignment metadata.

## Workflow

1. Fetch the node data and screenshot from the Figma API.
2. Convert the nested node tree into DFS pre-order `flatNodes`.
3. Split large designs into functional and spatial subtrees, validating complete
   coverage and mutually exclusive boundaries.
4. Convert the subtrees into prefab hierarchies in parallel.
5. Merge the partial hierarchies and restore their Figma ordering.
6. Refine containers, hierarchy, naming, and alignment based on the screenshot,
   actual rendering overlaps, and Unity uGUI conventions.
7. Write the file index and Figma token needed by the downstream Unity Editor
   step to `Assets/FigmaAssets/<working_dir_name>_result.json`.

## Requirements

- Python 3.9 or later.
- A Figma Personal Access Token with access to the target file.
- Claude Code or a Codex environment that supports project Agent and Skill
  configuration.

Provide the token through an environment variable:

```bash
export FIGMA_API_TOKEN="your-figma-token"
```

The workflow and fetch scripts prefer `FIGMA_API_TOKEN`. If it is unavailable,
the Agent should ask the user for a token. 

## Usage

Invoke `figma_to_ugui_hierarchy` in Claude Code or Codex and provide
a Figma URL containing a `node-id`, for example:

```text
Use skill figma_to_ugui_hierarchy to process:
https://www.figma.com/design/<file-key>/<name>?node-id=123-456
```

## Output Structure

Every GameObject in the hierarchy follows `prefab_hierarchy_schema.json`. Its
main fields are:

```json
{
  "nodeId": "123:456",
  "nodeName": "Figma node name",
  "gameObjectCategory": "container",
  "gameObjectName": "MeaningfulPascalCaseName",
  "isButton": false,
  "horizontal_alignment": "center",
  "vertical_alignment": "center",
  "children": []
}
```

Supported `gameObjectCategory` values are `container`, `image`, `text`, and
`color`.

## Security

- Never store tokens in Agent configuration, documentation, test fixtures, or
  commit history.
- `.claude/settings.local.json` is machine-specific and must not be committed.
- Result JSON files contain a token and should only be used in the local Unity
  project.
- If a token appears in version control or shared logs, revoke it in Figma and
  generate a replacement immediately.
