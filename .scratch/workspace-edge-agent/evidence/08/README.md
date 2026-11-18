# Ticket 08: actual MCP coding and debugging evidence

## Delivery and boundary

The optional `workspace-sdk[mcp]` extra pins MCP 2.3.0. The delivered interface is
stdio MCP to authenticated HTTPS central routing, not a public HTTP MCP endpoint.
It forwards to the existing `RemoteClient`; central and authenticated Edge remain
the authorization and execution owners. No second execution engine, central
project copy, HTTP MCP auth framework, or central job store was added.

The approved automated seam is actual subprocess MCP client → MCP frontend →
verified TLS central router → outbound connector → authenticated local Edge agent.
The real browser observes the same project source and history. Red: the first
subprocess discovery attempt failed with `No module named workspace_sdk.agent_mcp`
and MCP connection closed. Green: discovery/required targeting and revision schema,
missing arguments, stale revision, denied device/project, unknown device, duplicate
run ID, successful flow, bounded command output/cursor, cancellation and authorized
disconnected device returning `unavailable` all pass. Without the optional extra,
the MCP integration case explicitly skips rather than breaking plain SDK discovery.

## Live AI-directed session, 2026-11-18

The implementing Codex coding agent chose the calls interactively using the real
MCP client in `workspace-test/tools/mcp_client.py`. This was not a hardcoded
successful-edit script or an in-process MCP transport. Every response came through
the live TLS router, outbound connector and authenticated Edge. No external LLM
account was required: the implementing coding agent supplied the reasoning.

The complete protocol discovery and request/result pairs are in
[mcp-transcript.jsonl](mcp-transcript.jsonl). It contains no credentials. Absolute
paths identify the disposable demo, not a deployed Edge. Decisions made from the
actual results:

1. Discovered 13 typed tools; selected the sole connected granted `edge-demo` and
   inspected project `demo` and `flow.py`/`nested/worker.py` revisions.
2. Patched the flow node/output from `Work` to `Calculate` through MCP and validated
   it. An immediate run collided with active validation and returned a real sheet
   conflict. After observing validation success, used a new run ID.
3. The run failed at `Calculate`, with `RuntimeError` and an error frame at
   `nested/worker.py:2`. Re-read that file based on the returned location. It
   explicitly raised the intentional error; replaced that statement with
   `return 42`, supplying its observed full revision.
4. The repaired run succeeded with output **42**. Job:
   `6bd90b13c01949d09265ce3964807214`; run:
   `54c1971a325c4381bef6f5337528e1a4`; loaded source version:
   `cb66e6c67abab9e2fa41c684c6910f6de92c5fcbcd48cd4a8d8e5ac53012d4e0`.
5. Started `python -m pip install --no-index edge_demo_bonus-1.0-py3-none-any.whl`
   using `run_command`, polled success/exit 0, and read bounded output confirming
   `edge-demo-bonus-1.0` installed. Then started project unittest discovery through
   the same MCP tools. The test imports the installed bonus package and repaired
   node; `work() + bonus() == 43`. Exit 0 and one passing test were observed through
   `get_job` and `read_job_output` stderr.
6. Read final worker source again via MCP. In real Chromium, opened the same
   project's Workspace UI, selected its succeeded history entry, asserted the
   exact run ID/source version, and opened `View files`. The editor contains the
   same `def work(): return 42` implementation.

[UI source screenshot](ui-source.png), [UI history screenshot](ui-history.png),
and [observed browser text/source](ui-observation.json) preserve the result.
History deliberately displays output **type metadata** (`int`), not captured raw
42; the authenticated MCP job returns the actual output. Existing privacy defaults
were preserved.

## Reproduce from a clean checkout

Use Python 3.12+ and OpenSSL. From the repository root:

```sh
python3.12 -m venv /tmp/workspace-mcp-demo-env
/tmp/workspace-mcp-demo-env/bin/python -m pip install './workspace[mcp]' playwright
/tmp/workspace-mcp-demo-env/bin/python -m playwright install chromium
/tmp/workspace-mcp-demo-env/bin/python workspace-test/tools/mcp_demo.py --root /tmp/workspace-mcp-demo-session
```

Keep that process alive. It refuses to overwrite an existing project; choose a
new root on each reproduction. It generates private random credentials, a temporary
localhost CA/certificate, seeded nested failure, tiny offline wheel, and project
test. The configured Edge interpreter is the installed environment above. No
editable installation or worktree Python import is required. In another terminal:

```sh
/tmp/workspace-mcp-demo-env/bin/python workspace-test/tools/mcp_client.py /tmp/workspace-mcp-demo-env/bin/python -m workspace_sdk.agent_mcp --config /tmp/workspace-mcp-demo-session/mcp.json
```

The harness prints discovery, then accepts one JSON object per stdin line:

```json
{"name":"list_devices"}
{"name":"inspect_project","arguments":{"request_id":"inspect-1","device_id":"edge-demo","project_id":"demo"}}
{"name":"read_file","arguments":{"request_id":"read-1","device_id":"edge-demo","project_id":"demo","path":"flow.py"}}
```

Make interactive choices following the session above. Use newly observed revisions
and returned job IDs; do not replay stored revision/job identities into a new
fixture. Poll validation to completion before starting a flow. The transcript
records all exact edit, run, install, output and test arguments. Pipe stdout to a
private file if preserving another session; command output/source may contain
sensitive content in real projects. The shipped harness forwards only PATH and
SYSTEMROOT, never ambient credentials or PYTHONPATH. Exit with EOF.

Open UI separately:

```sh
/tmp/workspace-mcp-demo-env/bin/workspace --project /tmp/workspace-mcp-demo-session/project ui --port 18308
```

Visit `http://127.0.0.1:18308`, select the succeeded history entry and `View files`.
An automated real-browser inspection is also provided:

```sh
/tmp/workspace-mcp-demo-env/bin/python workspace-test/tools/mcp_ui_inspect.py --url http://127.0.0.1:18308 --run-id RETURNED_RUN_ID --source-version RETURNED_SOURCE_VERSION --output /tmp/workspace-mcp-observation
```

Use `--chromium /absolute/browser/executable` if using an existing Chromium.
Close MCP with EOF; stop UI and fixture with Ctrl+C or SIGTERM. The fixture waits
for connector shutdown and closes the real local/central servers. Remove only the
private demo roots/env that you created after stopping them.

## Verification commands and limits

```sh
PYTHONPATH="$PWD/workspace/src:$PWD/workspace-test/tests" /tmp/workspace-mcp-demo-env/bin/python -m unittest test_agent_mcp test_agent_remote -v
git diff --check
```

Live session environment was a noneditable installation at
`/Users/faiisu/projects.nosync/Phyzflow/edge-mcp-demo/venv`; fixture data in its
sibling `session/`. It contained only the optional extra, Playwright and tiny demo
wheel, not Torch. This directory is disposable and not part of the committed
artifact. Reproduction uses the scripts and a fresh installed environment above.
Evidence demonstrates real protocol/TLS/process/browser behavior on one macOS
host, not physical Edge hardware, Docker/Linux, an internet deployment, or a public
HTTP MCP endpoint. Those unavailable targets are not claimed as verified.
