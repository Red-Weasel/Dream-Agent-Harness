---
name: understand-dashboard
description: "Use when the user wants to see the Understand-Anything map or dashboard -- 'open the map', 'show the knowledge graph dashboard', /understand-dashboard. In Dream the map is shown in the Understand dock, not a separate web page."
---

# Understand dashboard: the map is in Dream's Understand dock

Dream has the Understand-Anything dashboard built in. The **Understand ⌬** entry in the sidebar opens it as a dock beside the chat, showing this workspace's `.ua/` files. Nothing needs to be installed or started for it: do not start a server or a build, and do not open a browser.

1. list_dir `.ua` in the workspace. (When `.understand-anything/knowledge-graph.json` exists, the dock shows that older folder first.)
2. `.ua/knowledge-graph.json` is there: tell the user to open **Understand ⌬** in the sidebar. The map is on its Map tab: layers, files, functions and the guided tour. When `domain-graph.json` is there too, the dashboard's **Domain** view shows the business domains and flows. The dock's Changes tab lists this session's file edits.
3. No `knowledge-graph.json`: say there is no map yet. The user can open **Understand ⌬** and press **Ask for a map of this project**, or ask "map this repo" (the `understand` skill makes it). A `domain-graph.json` alone is not shown until the map exists.
4. Answer in two or three sentences. Do not make or change anything in `.ua/` for this request.
