# Deepen Workspace HTTP Transport, Canvas Layout, Sheet Lifecycle, and Project Settings

Status: ready-for-agent

## Problem Statement

As Phyzflow evolved from a single-flow local runner into a multi-sheet Workspace with auto-reload, in-browser source editing, and multi-panel dashboards, several responsibilities split across shallow modules and leaky seams:

1. Every Flow View HTTP route and transport rule is defined or manually mirrored in both the per-flow HTTP server and the multi-sheet Workspace proxy server, so adding or modifying routes (such as multi-panel dashboard assets) requires updating multiple regexes and forwarding guards in lockstep.
2. Dashboard panel geometry, 20px grid snapping, non-overlapping placement, and saved panel bounds leak across the seam between the graph canvas controller and the multi-panel dashboard state controller.
3. Sheet filesystem operations—discovering sheets, creating starter flows, archiving deleted flows, computing working-tree source stamps for auto-reload, and reading or saving `flow.py`—are scattered across three separate modules.
4. Project configuration (`setting.json`) reading and schema validation for UI networking, zoom physics, and deployment are fragmented across three shallow parsers instead of sharing one validation pass.

## Solution

Deepen the four affected architectural areas so each concept lives behind a single, narrow module interface:

1. **Unified Flow View Routing and HTTP Transport**: Consolidate GET/POST Flow View route matching, local-host and `Origin` verification, JSON payload size limits, and structured error mapping into one deep transport and routing module shared by both the per-flow server and the Workspace proxy server.
2. **Canvas Layout and Panel Geometry**: Isolate 20px workspace grid snapping, non-overlapping multi-panel placement, pointer drag/resize capture, keyboard stepping, and saved per-panel bounds inside one deep canvas layout module behind a minimal panel-identity interface.
3. **Sheet Lifecycle and Source Tree**: Consolidate sheet discovery, starter flow creation, trash archiving, source-tree stamping for auto-reload, and entrypoint source editing into one deep sheet lifecycle module, leaving the Workspace server to supervise worker processes and revisions.
4. **Unified Project Settings**: Consolidate `setting.json` reading and full schema validation (`port`, `ipv4`, `zoom`, and `deployment`) into one deep settings module used uniformly by the CLI, Web UI, and deployment paths.

## User Stories

1. As a Phyzflow maintainer, I want a single Flow View route definition shared by the per-flow server and the Workspace proxy, so that adding a dashboard or node route never silently fails behind the proxy with a 404.
2. As a Phyzflow maintainer, I want shared HTTP request framing, local-host enforcement, `Origin` checking, and JSON error responses in one module, so that security and payload limits remain identical across servers.
3. As a Phyzflow user, I want every dashboard panel and node card to snap to a consistent 20px workspace grid when dragged, so that I can align my graph and visualizations cleanly.
4. As a Phyzflow user, I want newly opened dashboard panels to be placed in clear space without overlapping existing panels, so that multi-panel dashboards (`2D Graph`, `3D Graph`, `3D Model`) are all immediately visible.
5. As a Phyzflow user, I want resizing any dashboard panel via pointer or keyboard to snap to 20px steps with a live dimension indicator, so that I can match sizes across panels effortlessly.
6. As a Phyzflow user, I want closing and reopening a dashboard panel from the Visualize panel to restore its last moved position and resized dimensions, so that my workspace layout persists during a session.
7. As a Phyzflow user, I want an **Open all** and **Close all** action in the Visualize panel, so that I can reveal or clear all workflow and node panels in one click.
8. As a Phyzflow user, I want all Workspace panels (`Toolbar`, `Flow outline`, `Visualize`, `Inspector`, `Run`, `History`, `Zoom controls`) enabled by default with a minimal chrome layout, so that I have direct access to controls without clutter.
9. As a Phyzflow developer, I want dashboard state management to stay decoupled from DOM positioning and collision math, so that I can change panel layout algorithms without touching snapshot streaming or settings logic.
10. As a Phyzflow developer, I want the graph canvas module to expose a narrow panel-placement interface, so that node dragging and panel placement share one grid-snapping implementation.
11. As a Phyzflow user, I want creating, editing, auto-reloading, and deleting a sheet to operate consistently on the sheet folder, so that working-tree changes are detected reliably without touching unrelated sheets.
12. As a Phyzflow maintainer, I want sheet discovery, starter creation, deletion archiving, source stamping, and `flow.py` editing to live in one sheet lifecycle module, so that filesystem rules and symlink protections are enforced in one place.
13. As a Phyzflow maintainer, I want the Workspace process manager to focus strictly on worker process supervision and revision tracking, so that worker lifecycle logic is not interleaved with directory traversal.
14. As a Phyzflow user, I want invalid `setting.json` fields (for UI port, IPv4 bind address, zoom physics, or deployment configuration) to fail early with consistent messages across CLI, UI, and deployment, so that misconfigurations are caught before serving.
15. As a Phyzflow maintainer, I want one project settings module to read and validate the entire `setting.json` schema in a single pass, so that validation logic is never duplicated across CLI, UI, and deployment modules.
16. As a Phyzflow test author, I want high-level HTTP and browser seams to verify routing, multi-panel placement, and auto-reload behavior end-to-end, so that internal module deepening never breaks user-facing contracts.
17. As a Phyzflow user, I want existing single-panel dashboards (`index.html` at the scope root) to continue opening alongside multi-panel `panels/` directories, so that existing flows require zero migration.
18. As a Phyzflow user, I want clicking a node link inside a workflow dashboard to navigate that panel to the target node while preserving the selected run, so that saved and live run inspection stays synchronized.
19. As a Phyzflow user, I want node settings saved or applied live from any dashboard panel to synchronize across all open panels for the same flow, so that no panel displays stale operational values.
20. As a Phyzflow maintainer, I want zero leakage of internal geometry, route strings, or filesystem traversal rules across module seams, so that future changes remain localized to one module.

## Implementation Decisions

- **HTTP Transport & Flow View Routing Module**:
  - Extract a deep HTTP transport and routing module that centralizes route matching (GET and POST Flow View routes including dashboard panels, dashboard assets, node sources, node files, run execution, and history), local-host and `Origin` guards, content-length bounds, JSON parsing, and standardized error responses.
  - Both the per-flow HTTP server and the multi-sheet Workspace proxy server become thin adapters delegating Flow View route validation and HTTP framing to this shared module, eliminating duplicated route regexes across the proxy seam.
- **Canvas Layout & Panel Geometry Module**:
  - Extract a deep canvas layout module responsible for 20px grid snapping, non-overlapping multi-panel placement, collision detection, drag/resize pointer capture, keyboard movement/resizing, live dimension display, and per-panel saved layout bounds.
  - The dashboard state module interacts with canvas layout strictly through a narrow interface (`reveal`, `focus`, `release`) keyed by panel identity (`scope` + `panelId`), ending inline geometry (`style.left/top/width/height`) leakage across the seam.
- **Sheet Lifecycle & Source Tree Module**:
  - Deepen the sheets module so it owns the complete on-disk sheet lifecycle: discovering flows, creating starter sheets, safely archiving deleted sheets, computing working-tree source stamps for auto-reload, and editing `flow.py` with revision conflict detection.
  - The Workspace UI supervisor communicates across a single sheet-lifecycle seam rather than walking directories or invoking source-edit internals directly.
- **Unified Project Settings Module**:
  - Deepen the project settings module to read `setting.json` once and validate UI settings (`port`, `ipv4`, `zoom`) and deployment settings under a single schema contract, exposing typed configuration objects to CLI, Web UI, and deployment callers.
  - Preserve existing backward compatibility defaults when keys are omitted in `setting.json`.

## Testing Decisions

- **Testing philosophy**: Test only external behavior through high-level interfaces and existing seams; avoid testing private helpers or internal DOM/CSS mechanics directly.
- **Primary Testing Seams**:
  1. **Workspace HTTP & Browser UI Seam (End-to-End Integration)**:
     - Uses the existing Playwright browser and live local Workspace server harness (matching the prior art in the dashboard, web UI, auto-reload, and workspace sheet test suites).
     - Exercises unified Flow View route forwarding (`dashboard-panels`, `dashboard-assets`, `dashboard-settings`, `node-source`, `history`, `run`), multi-panel opening/toggling from the Visualize panel, non-overlapping initial placement, 20px grid snapping during drag/resize, position/size persistence across close/reopen, and `Open all` / `Close all`.
  2. **Sheet Lifecycle & Project Validation Seam (In-Process / CLI Contract)**:
     - Uses the existing project validation and CLI test seam (matching the prior art in the validation, deployment settings, and workspace sheet test suites).
     - Exercises sheet discovery, creation, archiving, source-tree stamping, `flow.py` revision editing, and unified `setting.json` validation errors across CLI, UI, and deployment callers.

## Out of Scope

- Changing the Python-first `Flow`, `Step`, and `Runner` in-process execution model or introducing cross-process serialization between nodes.
- Changing the default diagnostic redaction rules that exclude raw request payloads and exception messages from stored run history.
- Adding multi-target deployment, remote GPU worker orchestration, or persistent cross-restart layout storage on disk.
- Altering the CSP sandbox restrictions on dashboard iframes.

## Further Notes

- All deepening steps preserve full backward compatibility with existing single-panel dashboards, `default-workspace` sheets, and `docs/development-plan.md` milestone acceptance criteria.
- Each candidate area can be implemented and verified incrementally against the existing test suite without blocking other milestones.
