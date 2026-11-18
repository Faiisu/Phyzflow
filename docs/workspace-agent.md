# Workspace development agent

The local agent provides authenticated project inspection, revision-checked source editing, and flow validation/execution without a browser. General development commands, persistent jobs, central routing, and MCP are delivered by later tickets.

## Start and connect

Use the Python environment where the Workspace SDK is installed. Create a private token with Python's `secrets.token_urlsafe(32)`, save it in a file owned by the account running the agent, and set its permissions to `0600`. Never put the token in a command-line argument, source control, or a browser URL.

Create an operator-owned JSON configuration outside the project:

```json
{
  "token_file": "/absolute/private/agent-token",
  "projects": {
    "development": {
      "root": "/absolute/path/to/workspace",
      "python": "/absolute/path/to/workspace/.venv/bin/python"
    }
  }
}
```

The configured Python executable must exist and be executable. It identifies the environment chosen by the operator; inspection does not import or run project code. Configuration paths should be absolute. Protect the configuration from untrusted modification.

Start the agent:

```sh
python -m workspace_sdk.agent --config /absolute/private/agent.json --port 0
```

The process prints `Workspace agent: http://127.0.0.1:<port>` after binding. It accepts only loopback connections and requires a bearer token for every request. A browser `Origin` is rejected. Stop it with Ctrl+C. Startup errors exit nonzero with a diagnostic. This initial foreground launcher is later extended with background-service lifecycle support.

Connect using the installed SDK:

```python
from pathlib import Path
from workspace_sdk.agent import AgentClient

client = AgentClient("http://127.0.0.1:18081", Path("/absolute/private/agent-token").read_text().strip())
response = client.request("inspection-1", "development", "inspect_project")
```

Use the actual printed port. The SDK client sends HTTP directly without environment proxy configuration.

## Request contract

POST `/request` with `Content-Type: application/json` and `Authorization: Bearer <token>`. Maximum request size is 64 KiB. Each request has exactly these fields:

```json
{
  "request_id": "inspection-1",
  "project_id": "development",
  "operation": "read_file",
  "arguments": {"path": "flow.py"}
}
```

`request_id` contains 1–128 characters. Successful responses return `request_id`, `ok: true`, and `result`. Operation errors return `request_id`, `ok: false`, and an `error` with `code`, `stage`, and `message`. Transport or authentication failures use non-200 HTTP status and may omit the request identifier because the request was not accepted. The SDK returns these error objects to callers rather than hiding them behind HTTP exceptions. Source mutations require expected revisions. Durable request deduplication is introduced with job persistence; retry a lost edit acknowledgement by reading the current source and revision rather than blindly submitting it again.

| Operation | Arguments | Result |
| --- | --- | --- |
| `inspect_project` | Empty object | Configured Python executable and discovered sheets with project-relative entry points |
| `list_files` | Optional relative `path`, default `.` | Immediate directory entries with path/kind and `truncated` |
| `read_file` | Relative `path` | UTF-8 preview, full-file SHA-256 revision when complete, and `truncated` |
| `search_code` | Literal `query`, 1–512 characters | Matches with path, one-based line, text preview, and `truncated` |
| `write_file` | Relative `path`, expected `revision`, full `source` | New revision, path, created flag, bounded unified diff |
| `apply_patch` | Relative `path`, expected `revision`, `old_text`, `new_text` | New revision, path, created flag, bounded unified diff |
| `validate_flow` | Optional sheet name `sheet`, default `Main` | `job_id` and initial `queued` status |
| `run_flow` | Optional sheet name `sheet`, default `Main`; optional `input` object | `job_id` and initial `queued` status |
| `get_job` | `job_id` belonging to this project | Current status and completed result or diagnostic |
| `cancel_job` | `job_id` belonging to this project | Current status after requesting cancellation |

Read previews consume at most 64 KiB of file bytes. Invalid UTF-8 bytes are replaced for display. Truncated reads have a null revision because a partial digest must not authorize a full-file edit. Directory listings return at most 500 entries. Search scans at most 2,000 directory entries and returns at most 100 matches, limiting each matching line preview to 1,024 characters. Search is literal and case-sensitive, covering Python, JSON, TOML, JavaScript, HTML, CSS, Markdown, and text files. A truncated result is incomplete; narrow the search or inspect a relevant file directly.

Listings and searches skip Git internals, virtual environments, agent/runtime state, and Python caches. Direct file inspection remains authorized within the configured project, so do not grant access to projects containing secrets the client must not read. Symbolic links and parent/absolute path escapes are rejected. Read-only transport is not a security sandbox against another process already running with the same operating-system account; run the agent under an appropriately restricted account.

## Editing source

`write_file` creates or replaces one source file. Supply the revision returned by `read_file` for an existing file, or the literal `missing` when creating a new file. New parent directories are created as needed. `apply_patch` replaces one exact, nonempty `old_text` occurrence in an existing file; absent or ambiguous matches are rejected. Both operations require a revision and reject a stale revision with `error.code: conflict`.

Editable extensions are Python, JSON, TOML, HTML, CSS, JavaScript, Markdown, and text. Hidden path components and Python caches are excluded from writes, including agent state, Git metadata, and virtual environments. The shared source editor preserves existing Python encoding declarations and CRLF newlines, checks for conflicting changes before replacement, and writes atomically. Source files have a 128 KiB editor limit; the HTTP request size limit still applies. A read truncated at 64 KiB supplies no revision and must not authorize an edit based on a partial preview.

Successful writes return a unified `diff` bounded to 16,384 characters and `diff_truncated`, along with `path`, `revision`, and `created`. Review the full current file when the diff is truncated. Syntax-invalid Python can be saved so an agent can repair incomplete work; validation happens through the normal Workspace validation/reload path.

```python
read = client.request("read-flow", "development", "read_file", {"path": "flow.py"})
result = client.request("rename-node", "development", "apply_patch", {
    "path": "flow.py",
    "revision": read["result"]["revision"],
    "old_text": "Original",
    "new_text": "Updated",
})
```

Inspection and edits use the same project files as the existing CLI and Web UI. Open idle sheets detect Python edits through the existing automatic reload behavior. Active runs keep executing their loaded source until completion; unsaved browser drafts defer reload. The agent reports concurrent file conflicts rather than overwriting another editor's save. Edits to dashboard assets are visible on Reload dashboard.


## Validate and run flows

Flow jobs currently require Linux or macOS. Install Workspace SDK in each configured project interpreter. Each job starts a fresh process in the selected sheet directory with that interpreter, so project imports cannot leak between jobs. Validation uses the same syntax, import, graph, and settings checks as the CLI and UI and does not execute nodes. Both validation and execution return immediately with an agent-owned job identifier; poll `get_job` until `succeeded`, `failed`, or `cancelled` (intermediate states are `queued` and `running`).

```python
import time

started = client.request("run-1", "development", "run_flow", {
    "sheet": "Main", "input": {"text": "example"},
})
job_id = started["result"]["job_id"]
while True:
    job = client.request("poll-1", "development", "get_job", {"job_id": job_id})["result"]
    if job["status"] not in ("queued", "running"):
        break
    time.sleep(0.1)
```

Completed flow results include `run_id`, `source_version`, JSON-compatible `output`, and safe per-step history diagnostics, including failed and skipped nodes. Source versions describe the source loaded for execution, including uncommitted edits. A failed validation/import returns `error.stage`, exception type, message, and traceback with source locations. These authenticated development results remain in agent memory; they may contain sensitive text. Results are bounded to 128 KiB; non-JSON or larger results return a result-stage diagnostic. Raw worker stdout/stderr is not an output/log contract in this ticket.

Execution uses the UI's Runner, node settings, and history recording. History excludes request values and request-derived exception text by default; explicit per-node sample capture keeps its existing behavior. If history cannot be written, successful output still returns with `history_error` and its source version. An already-open UI discovers agent runs, disables Run while active, and refreshes its shared completed history. External live state contains bounded status/timing/source fields only (at most 256 event entries and 128 KiB), without raw inputs, outputs, previews, or traceback text. Final output is available from `get_job`; the UI inspects the saved metadata and diagnostic locations.

A per-sheet operating-system lock coordinates agent execution, UI execution, and UI reload. A conflicting start or explicit reload returns a conflict; automatic source reload waits for completion. Other sheets remain independent. Cancellation terminates the job's process group, waits up to two seconds, and kills an unresponsive worker before releasing its reservation. Poll until `cancelled` before starting another job. Interrupted runs do not receive a fabricated successful history record.

Job state and raw results currently live only in agent memory and do not survive restart. Request deduplication, durable recovery, output paging, and service lifecycle belong to the persistence tickets. Do not blindly retry a start whose acknowledgement was lost.
