---
name: figma_to_ugui_hierarchy
description: This is a workflow for creating prefab hierarchy from figma url.
allowed-tools: Read, Grep, Glob, Write, Bash, Edit, Skill
---

You are provided a Figma content URL .

Ensure the Figma API token is available before starting:

1. Use the `FIGMA_API_TOKEN` environment variable when it is available.
2. If `FIGMA_API_TOKEN` is not set, ask the user to provide it before continuing.

Follow the steps to create unity prefab hierarchy.

## Step 1 : get the figma content

Run the python script `.agents/skills/figma_to_ugui_hierarchy/scripts/fetch_all_figma.py` from the project root. The script reads `FIGMA_API_TOKEN` from the environment.

```bash
python3 .agents/skills/figma_to_ugui_hierarchy/scripts/fetch_all_figma.py "<figma_url>"
```

The script outputs a JSON object to stdout:

```json
{
  "figma_url":"the input figma url",
  "file_key": "<figma_file_key>",
  "node_id": "<node_id>",
  "node_name": "<node_name>", 
  "working_dir_path": "<working_dir_path>",
  "raw_content_path": "<raw_figma_content_file_path>",
  "content_screen_path": "<figma_content_screenshot_path>",
  "simplified_content_path": "<simplified_figma_content_file_path>"
}
```

## Step 2: divide the single figma content node tree to multiple semantic subtrees

Start subagent `figma_subtree_split_planner`, pass:
- `simplified_content_path` from Step 1
- `content_screen_path` from Step 1
- `working_dir_path` from Step 1 (all subtree files must be created here)

The subagent owns the complete divide pipeline: it creates a plan template, plans semantic groups, validates coverage (mandatory retry-until-pass), and extracts exact subtree JSON files into `working_dir_path`.

you get a json data which contains all subtree file paths as follows

```json
{
  "subtrees":[
    "<working_dir_path>/subtree_000.json",
    "<working_dir_path>/subtree_001.json",
    "<working_dir_path>/subtree_002.json"
  ]
}
```

## Step 3: convert all subtrees to prefab hierarchy

start a subagent `figma_content_to_prefab_hierarchy` for each node subtree from step 2, Pass:
- the specific figma content subtree file path from step 2
- `content_screen_path` and `working_dir_path` from Step 1.

you get each subtree's prefab hierarchy file path.

when you get all the node subtree prefab hierarchy, you go to step 4

## Step 4: combine subtree prefab hierarchy to a large complete one

Now you have all the subtree prefab hierarchy, you combine them into a large and complete one, which matches the whole figma content.

run the python script `.agents/skills/figma_to_ugui_hierarchy/scripts/combine_hierarchy.py` from the project root to combine multiple prefab hierarchy files:

```bash
python3 .agents/skills/figma_to_ugui_hierarchy/scripts/combine_hierarchy.py \
  <hierarchy_file_1.json> <hierarchy_file_2.json> ... \
  -f <simplified_content_path> \
  -o "<working_dir_path>/prefab_hierarchy_combined.json"
```

- `hierarchy_files`: One or more prefab hierarchy JSON files to combine (required)
- `-f, --figma`: simplified_content_path from step 1 for ordering children (required)

when you get the combined prefab hierarchy, you go to step 5.

## Step 5: refine hierarchy order and add layout alignment

Start a subagent `figma_prefab_hierarchy_layout_refiner`, pass:

- `simplified_content_path` from Step 1
- `content_screen_path` from Step 1
- the combined `prefab_hierarchy_path` from Step 4
- working directory path (`working_dir_path`) from step 1 

The subagent refines the combined hierarchy for Unity prefab authoring, including child order, responsive layout alignment metadata, and meaningful root naming.

Output only the refined hierarchy file path, the file name should be `<root gameObjectName>_hierarchy.json`


## Step 6: Create the result json file

Write a short summary about the figma content (under 50 words), then run the python script `.agents/skills/figma_to_ugui_hierarchy/scripts/package_result.py` from the project root:

```bash
python3 .agents/skills/figma_to_ugui_hierarchy/scripts/package_result.py \
  --figma-url "<figma_url from Step 1>" \
  --file-key "<file_key from Step 1>" \
  --node-id "<node_id from Step 1>" \
  --node-name "<node_name from Step 1>" \
  --summary "<short description>" \
  --working-dir "<working_dir_path from Step 1>" \
  --raw-content "<raw_content_path from Step 1>" \
  --prefab-hierarchy "<prefab_hierarchy_path from Step 5>"
```

The script creates the output folder `Assets/FigmaData/<rootName>_<workingDirName>/`, copies the referenced files into it, writes the result JSON, and prints the result path to stdout.
