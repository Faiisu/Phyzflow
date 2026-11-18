# Remote programming through a Workspace agent on EdgeAI

## Outcome

An AI coding agent can select an EdgeAI device, inspect its Workspace project, create or edit Python programs, flows, nodes, and dashboards, manage project dependencies, validate and test its changes, and inspect execution results without an interactive remote desktop or shell session. A person can inspect the same source and flow results in the existing Workspace Web UI.

## Architecture and ownership

The Workspace agent on each Edge device owns project access, environment selection, command execution, process lifecycle, jobs, logs, and flow execution. It enforces its local permissions even when requests arrive through an authenticated central service. The AI coding agent supplies the reasoning and uses tools to perform development work.

The central service exposes MCP tools, authenticates clients and devices, authorizes device access, and routes requests and responses. It does not own Edge execution or require source repositories, Python environments, or authoritative job state to move to the central server. The Edge agent connects outward using an authenticated encrypted connection, allowing operation behind NAT without exposing the local Web UI to the internet. The MCP interface and router may share one service initially.

The agent also supports a local client so its complete development workflow can be implemented and demonstrated before remote routing or MCP exists. Reuse existing validation, source revision checks, settings, Runner events, and history through narrow shared operations. Extract only the code needed by each ticket; preserve current UI and CLI behavior.

## Command and job behavior

Requests identify a permitted project and carry a request identifier. Remote requests additionally identify a device. File reads return revisions and edits supply the expected revision. Errors identify their stage and source location when available. Long operations return an Edge-owned job identifier rather than holding the caller open until completion. Clients can inspect status, read bounded output pages using a cursor, and cancel a job.

Clients can disconnect without terminating work. Repeating a mutation with the same request identifier must not apply it twice or start another process. Reusing that identifier with different content is rejected. Completed job results survive an agent restart; interrupted work is reported accurately rather than silently restarted or reported as successful. A central restart must not interrupt work already accepted on an Edge device.

The local agent accepts requests only from permitted local clients. Remote callers must pass central authorization and agent-side checks. Project file access, subprocess working directories, and inherited environment access must respect the locally configured development permissions. Command output is an explicit development artifact and may contain sensitive text; restrict access and retention. Preserve the existing default exclusion of raw payloads and request-derived exception messages from flow history.

## Flow and UI consistency

Flow source remains the graph definition. Validation uses the selected project interpreter and remains free of node execution, model initialization, and network calls during flow loading. The Web UI and agent inspect and run the same project source and expose consistent flow diagnostics and results. Edits honor source revisions, unsaved browser drafts, and the existing rule that source reload waits for active runs. Tests must demonstrate this coordination rather than starting a second incompatible execution path.

Initial demonstrations use a designated development project. Production version promotion, fleet rollout, rollback orchestration, separate model deployment, and GPU worker management are outside this effort. Project authorization must let operators keep development requests away from serving projects.

## Delivery order

1. Inspect an Edge workspace through a local agent.
2. Create and edit flow source through the agent.
3. Validate and run an edited flow through the agent.
4. Run development commands in the project environment.
5. Keep the agent available across client disconnects.
6. Route an authenticated coding request to an Edge device.
7. Edit and run remotely through reconnecting connections.
8. Build and debug an Edge flow through MCP.

Each numbered item has a corresponding implementation ticket. Dependencies form the approved linear sequence. Only the first ticket is initially unblocked; every ticket has status ready-for-agent as a specification readiness label, not a claim that its prerequisites are complete.

## Completion evidence

Demonstrate the local edit, validate, run, inspect, and repair loop first. Then demonstrate two separately identified Edge agents through the central service, including disconnection and reconnection, correct device routing, rejected unauthorized requests, and duplicate request handling. Finally connect an actual MCP client with an AI coding agent, create or change a flow on one selected Edge, repair an intentional error using returned diagnostics, and inspect the resulting source and completed run in the Workspace Web UI. Record reproducible commands and observed results with the implementing tickets.
