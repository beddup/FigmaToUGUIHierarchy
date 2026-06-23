
The prefab hierarchy files are generate from different parts of the same figma content.
This skill will tell how to combine them into a large and complete one, which matches the whole figma content.


## RULES to keep

1. elements in different prefab hierarchies are the same if their `nodeId` are the same. when combine, use first one as the element of the final large hierarchy.

2. in the final hierarchy, any element's `nodeId` is unique and no element else has the same `nodeId`

3. the children order in the final hierarchy should keep the same with in the figma content file. you can use `nodeId` to find the figma node

4. don't lose any nodes: if there is element with nodeId "xxx" in some input hierarchy, then there must be a element with nodeID "xxx" in the final output hierarchy, too.


## Example

Hierarchy 1:

```
Element A (nodeId:1)
    |-----Element B (nodeId:2)
    |-----Element C (nodeId:3)
        |-----Element D (nodeId:4)
```


Hierarchy 2:

```
Element E (nodeId:1)
    |-----Element F (nodeId:3)
        |-----Element G (nodeId:5)
    |-----Element H (nodeId:6)
```

The combined hierarchy should be :

```
Element A (nodeId:1)
    |-----Element B (nodeId:2)
    |-----Element C (nodeId:3)
        |-----Element D (nodeId:4)
        |-----Element G (nodeId:5)
    |-----Element H (nodeId:6)



```
