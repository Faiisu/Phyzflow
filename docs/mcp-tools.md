# MCP tool contract

Phyzflow exposes two MCP servers. The application server is served over Streamable HTTP at `/mcp` and covers Workspace flow development, as described in [Workspace](../workspace/README.md). The Edge development frontend runs over stdio and routes requests to Workspace agents, as described in [Workspace agent](workspace-agent.md). Both servers follow the same tool contract. Their tool schemas are part of every MCP client's context, so each tool is written to be selected correctly from its listing alone.

## Annotations

Every tool declares a `title` and sets all four annotation hints explicitly. Hosts use these hints to decide which calls they can approve automatically and which ones need confirmation.

| Hint | Set to `true` when |
|---|---|
| `readOnlyHint` | The tool changes no project file, setting, lock, run, or published transfer. Starting a validation job or opening a download stream counts as read-only. |
| `destructiveHint` | The tool can replace, remove, or interrupt existing state. Examples: writing or patching a file, moving or trashing a file, deleting a sheet, stopping a run, shortening log retention, or changing installed packages. |
| `idempotentHint` | Repeating the call with the same arguments has no further effect. On the Edge server, every tool is idempotent because a reused `request_id` replays the original outcome. |
| `openWorldHint` | The tool reaches beyond the Workspace. `run_flow` and `run_all_flows` run flow code with the host account's access, and `install_dependencies` downloads from the package index. |

Read-only tools are always non-destructive. The MCP command tests check every listed tool's hints against its expected classification.

## Separating reads from writes

A single tool never both reads and changes state. Where an earlier tool did both depending on an argument, it was split:

| Earlier tool | Read-only tool | Mutating tool |
|---|---|---|
| `open_sheet` with `mode` | `inspect_sheet` | `open_sheet` (acquires the edit lock) |
| `begin_workspace_transfer` with `direction` | `begin_workspace_file_download` | `begin_workspace_file_upload` |
| `finish_workspace_transfer` | `finish_workspace_download` | `finish_workspace_upload` |

Calling a finish tool with the other direction's transfer fails, and the error names the correct tool. `write_workspace_chunk` and `read_workspace_chunk` already handled one direction each. `cancel_workspace_transfer` handles both directions because it only discards staged data.

## Descriptions and parameters

A description says what the tool does, what it returns, and its limits. When a similar tool exists, the description names it, for example `read_file` versus `read_workspace_text`. Descriptions don't tell the client how to behave. Workflow guidance, such as validating after an edit, goes in the server `instructions`.

Every parameter has a description. Shared parameters use the definitions in `application_mcp/tool_contract.py`, including Workspace IDs, sheet names, edit sessions, revisions, paths, and transfer fields, so the same argument reads the same way across tools. Value checks stay in the shared feature logic, which keeps validation messages identical across the CLI, UI, and MCP.

## Results and errors

Tools return a JSON object as `structuredContent`, declared by an object `outputSchema`, along with the same JSON as a text block for hosts that read only text. Failures are returned as MCP tool errors with `isError`. Where another tool resolves the failure, the error message names it. For example, an unknown sheet points to `inspect_workspace`, and a stale revision points to `read_file`.

## Tool count

The application server lists 44 tools. That is more than the roughly 30 tools at which a search-and-execute pair usually pays off. Phyzflow keeps one tool per operation because the tools form a single development workflow that a client uses together. Hiding them behind a catalog would add a discovery round trip to almost every step. [MCP-06](application-spec.md) also names several of these tools as the contract. Revisit this choice if the tool listing grows enough to crowd client context.
