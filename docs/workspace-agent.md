# Workspace development agent

The local agent provides authenticated project inspection, revision-checked source editing, and flow validation/execution, and development command jobs without a browser. Jobs, results, bounded command output, and mutation identities persist locally across restart. Authenticated central routing supports the same development operations and automatic connector reconnection below; An optional stdio MCP frontend routes the same operations through central HTTPS, as described below.

## Start and connect

Use the Python environment where the Workspace SDK is installed. Create a private token with Python's `secrets.token_urlsafe(32)`, save it in a file owned by the account running the agent, and set its permissions to `0600`. Never put the token in a command-line argument, source control, or a browser URL.

Create an operator-owned JSON configuration outside the project:

```json
{
  "token_file": "/absolute/private/agent-token",
  "state_dir": "/absolute/private/agent-state",
  "projects": {
    "development": {
      "root": "/absolute/path/to/workspace",
      "python": "/absolute/path/to/workspace/.venv/bin/python",
      "commands": ["python"],
      "inference_environment": {"HF_HOME": "/absolute/model-cache"}
    }
  }
}
```

The configured Python executable must exist and be executable. It identifies the environment chosen by the operator; inspection does not import or run project code. Configuration paths should be absolute. Protect the configuration from untrusted modification.

Start the agent:

```sh
python -m workspace_sdk.agent --config /absolute/private/agent.json --port 0
```

The process prints `Workspace agent: http://127.0.0.1:<port>` after binding. It accepts only loopback connections and requires a bearer token for every request. A browser `Origin` is rejected. Stop it with Ctrl+C or SIGTERM; managed jobs are cancelled and their groups cleaned up before exit. Startup errors exit nonzero with a diagnostic. A background service configuration is described below.

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

`request_id` contains 1–128 characters. Successful responses return `request_id`, `ok: true`, and `result`. Operation errors return `request_id`, `ok: false`, and an `error` with `code`, `stage`, and `message`. Transport or authentication failures use non-200 HTTP status and may omit the request identifier because the request was not accepted. The SDK returns these error objects to callers rather than hiding them behind HTTP exceptions. Source mutations require expected revisions. For mutations (`write_file`, `apply_patch`, `validate_flow`, `run_flow`, `run_command`, and `cancel_job`), retry a lost acknowledgement with the **same original request_id and identical content**. The local ledger returns the original response, including its job identifier or error. Reusing the identity with changed content returns `request_conflict`; use a new identity for genuinely new work. Read-only request identities may be reused.

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
| `run_command` | `program`, string list `argv`, optional project-relative `cwd` (default `.`) | `job_id` and initial `queued` status |
| `read_job_output` | Command `job_id`; optional `stream` (`stdout`/`stderr`), byte `cursor` (default `0`), `limit` (1–16,384 bytes) | UTF-8 `content`, `next_cursor`, `total_bytes`, `truncated`, `complete` |
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

Flow jobs currently require Linux or macOS. Install Workspace SDK in each configured project interpreter. Each job starts a fresh process in the selected sheet directory with that interpreter, so project imports cannot leak between jobs. Validation uses the same syntax, import, graph, and settings checks as the CLI and UI and does not execute nodes. Both validation and execution return immediately with an agent-owned job identifier; poll `get_job` until `succeeded`, `failed`, `cancelled`, or `interrupted` (intermediate states are `queued` and `running`).

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

Validation and execution receive the same minimal base environment as commands: `PATH` starts with the configured interpreter directory, followed by the operating-system default executable path; `PYTHONNOUSERSITE=1` and `PYTHONUNBUFFERED=1` are set. Ambient agent credentials, `PYTHONPATH`, and other Python environment overrides are excluded. The configured executable path is preserved, including a virtual environment's Python symlink; install the SDK and project dependencies normally into that environment rather than relying on the agent's import path.

Operators can explicitly supply string values in the project's `inference_environment` configuration for model/cache locations or required inference credentials. These values are available to flow imports during validation and to execution nodes, so grant them only to trusted development projects. Names must be identifiers; `PATH`, names beginning with `PYTHON`, `LD_`, or `DYLD_` (case-insensitive), and values containing NUL are rejected to preserve the selected interpreter and loader configuration. The default is empty. Clients cannot override this configuration through requests, and generic development commands never receive these additional values. Configuration is read at startup; restart the agent to change it.

Completed flow results include `run_id`, `source_version`, JSON-compatible `output`, and safe per-step history diagnostics, including failed and skipped nodes. Source versions describe the source loaded for execution, including uncommitted edits. A failed validation/import returns `error.stage`, exception type, message, and traceback with source locations. These authenticated development results persist in the private agent state; they may contain sensitive text. Results are bounded to 128 KiB; non-JSON or larger results return a result-stage diagnostic. Flow worker stdout/stderr is not exposed as command output.

Execution uses the UI's Runner, node settings, and history recording. History excludes request values and request-derived exception text by default; explicit per-node sample capture keeps its existing behavior. If history cannot be written, successful output still returns with `history_error` and its source version. An already-open UI discovers agent runs, disables Run while active, and refreshes its shared completed history. External live state contains bounded status/timing/source fields only (at most 256 event entries and 128 KiB), without raw inputs, outputs, previews, or traceback text. Final output is available from `get_job`; the UI inspects the saved metadata and diagnostic locations.

A per-sheet operating-system lock coordinates agent execution, UI execution, and UI reload. A conflicting start or explicit reload returns a conflict; automatic source reload waits for completion. Other sheets remain independent. Cancellation terminates the job's process group, waits up to two seconds, and kills an unresponsive worker before releasing its reservation. Poll until `cancelled` before starting another job. Interrupted runs do not receive a fabricated successful history record.

Completed job state and raw results survive restart in the configured state directory. A job active when the agent crashes becomes `interrupted`, with a recovery diagnostic; it is never silently retried or reported as successful. Retained output is a durable prefix and can end before the last bytes produced during a crash. If a non-job mutation was accepted but its outcome was not committed, retries return `error.code: interrupted`: it **may already have taken effect**. Inspect current files or external effects before deciding whether to issue new work with a new request identifier. There is no promise of an atomic transaction between a source edit or external command effect and SQLite.


## Development commands and output

Command jobs require Linux or macOS. The operator explicitly enables commands per project with `commands`; omission or an empty list denies all commands. The special program `python` invokes that project's configured interpreter, preserving a virtual environment's executable path. Other entries must be absolute executable paths and match the request exactly. Requests use an argument list, never a shell string. `cwd` stays within the configured project; absolute paths, parent escapes, and symbolic links are rejected. Missing executables and environments report a command-stage error before a job starts.

A command receives only `PATH` (the selected interpreter's directory followed by the operating system default executable path), `PYTHONNOUSERSITE=1`, and `PYTHONUNBUFFERED=1`. Ambient credentials, `PYTHONPATH`, and Python environment overrides are not inherited. Requests cannot supply environment variables or choose another interpreter. Commands can run arbitrary code within the agent account's operating-system permissions: authorizing Python or another executable does not sandbox its arguments or child processes. Grant development command access only to trusted clients and projects under an appropriately restricted account.

For example, install a local wheel without contacting an index and then run the project's tests in the same environment:

```python
install = client.request("install-1", "development", "run_command", {
    "program": "python",
    "argv": ["-m", "pip", "install", "--no-index", "sample_dependency-1.0-py3-none-any.whl"],
    "cwd": ".",
})
# Poll get_job as above until completion before running the tests.
tests = client.request("tests-1", "development", "run_command", {
    "program": "python", "argv": ["-m", "unittest", "discover", "-v"], "cwd": ".",
})
page = client.request("output-1", "development", "read_job_output", {
    "job_id": tests["result"]["job_id"], "stream": "stderr", "cursor": 0, "limit": 4096,
})
```

Command results use the existing `queued`, `running`, `succeeded`, `failed`, and `cancelled` statuses. Completed command jobs include `exit_code`; zero succeeds, nonzero fails, and a negative value reports the terminating POSIX signal. An assertion failure retains its test diagnostic in stderr. Jobs continue when the requesting HTTP client disconnects. Cancel through `cancel_job` and poll until terminal: the agent signals the entire managed process group and escalates to SIGKILL after one second even if the group leader already exited. Children must remain in the managed process group; the agent is not a sandbox against programs intentionally escaping the session.

Output access requires the same bearer token and project authorization as job access. Each stream retains its first 128 KiB in memory and private SQLite state and drains/discards subsequent bytes; concurrent readers prevent stdout/stderr pipe deadlocks. Each page reads at most 16 KiB of retained bytes. `next_cursor` is a byte offset; use it unchanged to resume, including while a command runs. `total_bytes` counts all drained bytes and `truncated` indicates discarded bytes. `complete` becomes true at the retained stream's end after the job finishes; an empty live page requires polling again. Content decodes UTF-8 with replacement, so a page boundary inside a multibyte character can show replacement characters. There is no implicit raw output in flow history or the Web UI.

Each command job retains at most 256 KiB of output across both streams (128 KiB per stream) without a 32-command lifetime cap. Restart preserves existing jobs and output. Mutation identities and their responses are never pruned: deleting an old identity could execute a delayed duplicate again. Raw development output, flow results, saved mutation responses, backups, and service logs can contain sensitive data; restrict token, account, directory, backup, and journal access accordingly.


## Durable local instance and recovery

`state_dir` is an absolute path to a local filesystem directory, outside every exposed project. If omitted it defaults to `<token_file>.state`. The agent creates it with `0700`, requires current-user ownership/private permissions, and stores its SQLite database and lock with `0600`. Existing insecure directories are rejected. Use one stable state directory per device-local agent instance; do not share it over a network filesystem, run concurrent replicas, or copy it between devices. Keep the same directory and project identity across process restarts. Request identity is scoped by configured project ID within this instance; the fingerprint also includes the configured project root and Python path, so reconfiguring a project cannot return an old outcome for a different root/environment. Arguments are fingerprinted as canonical JSON; raw request payloads are not stored in the ledger.

SQLite commits acceptance before a mutation can take effect. Job admission commits its identifier and queued record before launching the process. A live supervisor owns each job group, and only the agent owns the control pipe writer. Client socket closure has no effect on that pipe. Agent death closes it, and the supervisor sends SIGTERM followed by SIGKILL after one second to the entire group, including children ignoring SIGTERM. Normal command completion also cleans remaining descendants. The supervisor holds the instance lock until cleanup completes, so an immediate replacement can exit with `Another agent is already using this state directory`; retry startup after cleanup. Recovery never signals saved numeric PIDs and cannot kill an unrelated process that reused one. Programs intentionally escaping their process group remain outside this mechanism; use the service cgroup boundary and a restricted account. Request/output cursors and identifiers do not change on restart.

Graceful shutdown stops accepting requests, finishes already accepted handlers, cancels active jobs, joins output collectors, closes SQLite, then releases the instance lock. SIGKILL or power loss instead leaves active jobs `interrupted`; the persisted output may be incomplete. Stop before archiving/copying state. The command cap bounds retained raw command output to 8 MiB (SQLite JSON/base64 encoding adds disk overhead); request identity/response bookkeeping and bounded-per-job flow results grow with accepted work. Monitor available disk space; persistence failure cannot safely promise an outcome and must be investigated before retrying with a new ID.

## Ubuntu 24.04 LTS background service

The following is an operator-run installation template for one Linux Edge machine with Python 3.12 and systemd. Replace paths/account names for the designated development project. It needs no browser or logged-in client. Keep the SDK checkout at `/opt/phyzflow`; ensure the dedicated account can read it and owns its designated project. Do not give this account unrelated serving projects or secrets.

```sh
sudo apt-get update
sudo apt-get install -y python3.12-venv
sudo useradd --system --create-home --home-dir /var/lib/workspace-agent --shell /usr/sbin/nologin workspace-agent
sudo install -d -o workspace-agent -g workspace-agent -m 0700 /var/lib/workspace-agent/state
sudo python3.12 -m venv /opt/workspace-agent-venv
sudo /opt/workspace-agent-venv/bin/python -m pip install /opt/phyzflow/workspace
sudo install -d -o root -g workspace-agent -m 0750 /etc/workspace-agent
sudo -u workspace-agent /opt/workspace-agent-venv/bin/python -c 'import os,secrets; fd=os.open("/var/lib/workspace-agent/token",os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600); os.write(fd,(secrets.token_urlsafe(32)+"\n").encode()); os.close(fd)'
```

Install the SDK in the selected project Python environment too. Create `/etc/workspace-agent/agent.json` as root, group `workspace-agent`, mode `0640`, using the configuration above with `token_file: /var/lib/workspace-agent/token`, `state_dir: /var/lib/workspace-agent/state`, and the actual absolute development root/interpreter. Validate in the foreground first as `workspace-agent`; require the printed listening address and an authenticated `inspect_project` response, then stop with Ctrl+C. A configured allowlist such as `["python"]` grants arbitrary Python code execution under this account.

Copy [workspace-agent.service](workspace-agent.service) to `/etc/systemd/system/workspace-agent.service` and run:

```sh
sudo systemd-analyze verify /etc/systemd/system/workspace-agent.service
sudo systemctl daemon-reload
sudo systemctl enable --now workspace-agent.service
sudo systemctl status workspace-agent.service --no-pager
sudo journalctl -u workspace-agent.service -n 50 --no-pager
# Stop (also stops active managed jobs), then start or restart explicitly:
sudo systemctl stop workspace-agent.service
sudo systemctl start workspace-agent.service
sudo systemctl restart workspace-agent.service
# Disable boot startup and stop:
sudo systemctl disable --now workspace-agent.service
```

The fixed loopback port is 18081; it is not an Internet endpoint. `Type=exec` confirms the interpreter exec, not HTTP readiness: require the printed address plus an authenticated request. `Restart=on-failure` retries unexpected exits after three seconds; explicit stops remain stopped. `KillMode=mixed` lets the agent cancel jobs first and makes systemd SIGKILL all remaining cgroup processes after the shutdown timeout. See the authoritative [service options](https://www.freedesktop.org/software/systemd/man/latest/systemd.service.html) and [termination behavior](https://www.freedesktop.org/software/systemd/man/latest/systemd.kill.html).

For startup failure, inspect the journal for `Agent startup failed`, fix config/token ownership, missing interpreter/project, private state permissions, instance-lock conflict, or occupied loopback port; then `sudo systemctl reset-failed workspace-agent.service` and start again. Test invalid config in the foreground with the same account and interpreter to see a nonzero exit and its diagnostic. Do not delete the database to repair an instance-lock conflict. Restrict system journal access and journal retention because development diagnostics can be sensitive.

Verification boundary: real module startup, HTTP disconnect/retry, SIGTERM shutdown, SIGKILL cleanup, and restart recovery are covered by `test_agent_persistence.py` on macOS/Python 3.12.11. The Ubuntu/systemd template has not been executed on a Linux device or container (Docker daemon unavailable on the implementation host); verify install/start/stop/boot behavior on the target before deployment. No host service is registered by the test suite.

## Develop on an Edge through the central router

The central service holds client/device credentials, device grants, and short-lived routing state. Project files, environments, jobs, and mutation records stay with the existing local agent. An independent connector process uses that agent's authenticated loopback API; it never creates another agent or state-directory owner. No inbound public Edge port is needed. The remote allowlist includes all operations in the request contract above: inspection, revision-checked edits, validation, flow and command jobs, bounded command output, job status, and cancellation. Execution remains on Edge; An optional stdio MCP frontend routes the same operations through central HTTPS, as described below.

Provision a stable device ID and a different random private token for **each** device and client. Reuse the same device ID/config across connector restarts. Tokens must be at least 16 non-whitespace ASCII characters in account-owned `0600` files; use `secrets.token_urlsafe(32)`. Keep configs and the TLS private key private and outside project/source control. Copy each device token securely only to its matching Edge; give client tokens only to authorized callers. Never pass tokens as command arguments or URL components.

On the central host, create `/absolute/private/central.json`:

```json
{
  "cert_file": "/absolute/private/fullchain.pem",
  "key_file": "/absolute/private/privkey.pem",
  "devices": {
    "edge-a": {"token_file": "/absolute/private/edge-a-token"},
    "edge-b": {"token_file": "/absolute/private/edge-b-token"}
  },
  "clients": {
    "developer": {
      "token_file": "/absolute/private/developer-token",
      "devices": ["edge-a", "edge-b"]
    }
  }
}
```

Use a certificate trusted by the clients, whose subject alternative name matches the central hostname. Start central with the installed Workspace interpreter:

```sh
python -m workspace_sdk.agent_remote central --config /absolute/private/central.json --host 0.0.0.0 --port 18443
```

The default bind address is loopback; choose the network interface explicitly. The printed binding address is diagnostic: configure clients with the real certificate hostname, for example `https://central.example.com:18443`. TLS is mandatory, supports TLS 1.2 or later, and uses normal CA and hostname validation. For a private CA, distribute its public certificate securely and set `ca_file`. There is no verification bypass. This small standard-library server is intended for a controlled development network; assess hardened ingress, network limits, and service management before public deployment. No public deployment has been verified.

On each Edge, start the existing local agent using its normal private config and fixed loopback port (for example the service above). Create a separate connector config `/absolute/private/connector.json`:

```json
{
  "central_url": "https://central.example.com:18443",
  "device_id": "edge-a",
  "device_token_file": "/absolute/private/edge-a-token",
  "local_url": "http://127.0.0.1:18081",
  "local_token_file": "/absolute/private/agent-token",
  "ca_file": "/absolute/private/central-ca.pem"
}
```

Omit `ca_file` for a publicly trusted certificate. For the second Edge use `edge-b` and its own token, local agent, and project configuration. The loopback token is distinct from central device/client credentials. Start the outbound connector:

```sh
python -m workspace_sdk.agent_remote connect --config /absolute/private/connector.json
```

The connector automatically retries network and truncated-response failures with bounded backoff (0.25 seconds rising to two seconds), retaining its session when central still recognizes it. After central restarts and reports `session_lost`, it registers again with the same authenticated device ID. Invalid authentication or `stale_session` from a replacement connector exits nonzero; stale connectors never repeatedly replace their successors. Ctrl+C or SIGTERM disconnects after the current bounded request, without stopping the local agent or local jobs. Stopping central leaves Edge files/jobs/state untouched. Heartbeats every half second retain the three-second lease while an accepted local request is awaiting acknowledgement. Heartbeats, polls, replies, and disconnects require the current session generation.

Connect from the coding client:

```python
from pathlib import Path
from workspace_sdk.agent_remote import RemoteClient

client = RemoteClient(
    "https://central.example.com:18443",
    Path("/absolute/private/developer-token").read_text().strip(),
    cafile="/absolute/private/central-ca.pem",  # omit for public trust
)
print(client.list_devices())
print(client.request("inspect-a-1", "edge-a", "development", "inspect_project"))
print(client.request("read-b-1", "edge-b", "development", "read_file", {"path": "flow.py"}))
```

Clients see only granted devices, each with `connected` or `unavailable` status. Status has a three-second lease, so a crashed connector becomes unavailable after at most that lease; it is not a guarantee of successful delivery. Client credentials cannot register devices; device credentials cannot call client endpoints or impersonate another device. Central grants authorize devices; the local agent independently authorizes project IDs, paths, and operations. Identical request IDs in concurrent calls are isolated by opaque delivery IDs; the original inner request and ID reach the local agent unchanged.

### Relay contract and bounds

Every endpoint is a JSON POST with a bearer token and no browser `Origin`. Client endpoints are `/devices` (empty object) and `/request` (`device_id` plus the unchanged local `request` object). Device endpoints are `/edge/register` (`device_id`), `/edge/poll` (`device_id`, `session`), `/edge/reply` (those plus `delivery_id`, `response`), `/edge/heartbeat` (`device_id`, `session`), and `/edge/disconnect` (`device_id`, `session`). Registration returns `result.session`; polling returns `result.delivery` as null or an object containing `delivery_id` and `request`. Replies must match the original request ID and the current session/device/delivery. Transport authentication failures use HTTP 401; denied grants 403; stale session/reply 409. Agent results retain their normal response shape. Client TLS/network failures raise transport errors; no fallback device is selected.

Central accepts at most 32 pending routes and 64 simultaneous connections. Inner requests retain the local 64 KiB limit; relay envelopes/replies are capped at 1 MiB. Polls wait at most one second; routes wait at most three seconds. Pending routes/replies are discarded after completion or expiry and are never durable. Before delivery, expiry/disconnection returns `unavailable` and removes the queued request so it cannot be executed later. After a poll fetched a request, loss of its reply returns `delivery_unknown`: execution may have happened. This is not confirmation of cancellation. The connector never automatically replays a delivered operation. A slow local acknowledgement can exceed the three-second routing deadline while the device remains connected; this also returns `delivery_unknown`. A local acknowledgement lost after submission is uncertain, never a claim that local execution failed. Retry the exact original mutation ID, project, operation, and arguments to recover the Edge ledger response. A new ID authorizes new work. Device and project scope isolate identical IDs. A queued offline cancellation returns `unavailable` and is removed; explicitly retry it after reconnect and inspect the original job to observe termination. No payload/token access logs are emitted.

Verification: `test_agent_remote` exercises remote edit/validate/run and execution errors, stale revisions, duplicate mutation/command retries after actual delayed HTTP acknowledgements, per-device/project identity and job/output scope, offline cancellation with explicit retry, central restart during a real long-running command, continued original-job output/cancellation after automatic reconnect, and replacement-connector termination without takeover storms. It uses temporary OpenSSL certificates with a localhost SAN, actual verified HTTPS requests, two independent local agent owners and outbound connectors, scoped authentication, local project/path checks, stale sessions, expired/delivered request errors, and real central/connector module processes. It rejects HTTP, untrusted CAs, and mismatched hostnames. This demonstrates the network topology on one macOS host, not physical Edge hardware or deployment across an untrusted public network.


## MCP frontend (stdio to central HTTPS)

Install the optional extra in the coding client's Python environment:

```sh
python -m pip install '/absolute/path/to/workspace[mcp]'
```

The extra pins `mcp==2.3.0`. Plain SDK installation and local/remote agent modules
have no MCP dependency. Edge interpreters need only the SDK and their project dependencies.

This release provides a **stdio MCP server**, launched by the coding client. It is
an authenticated HTTPS bridge to the existing central router, not a public HTTP
MCP endpoint. Central authenticates the configured client credential and its
device grants; Edge independently validates project, operation, path, revisions,
and jobs. The frontend never executes project code or owns job state. No OAuth or
new internet-facing MCP service is included.

Create an operator-owned private configuration outside the project and source
control (protect its directory and set the configuration to `0600`):

```json
{
  "central_url": "https://central.example.com:18443",
  "client_token_file": "/absolute/private/developer-token",
  "ca_file": "/absolute/private/central-ca.pem"
}
```

Use the separately scoped central client token described above. The token file
must be owned by the current user, regular, and inaccessible to group/others.
Omit `ca_file` for a public CA. TLS certificate and hostname verification remain
mandatory. Never put tokens in tool arguments, process arguments, URLs, or logs.

Configure an MCP-capable coding client with the equivalent stdio command:

```json
{
  "mcpServers": {
    "workspace-edge": {
      "command": "/absolute/client-venv/bin/python",
      "args": ["-m", "workspace_sdk.agent_mcp", "--config", "/absolute/private/mcp.json"]
    }
  }
}
```

The exact client settings wrapper may differ; the executable and argument list
are the interface. Stdout carries only MCP messages. The executable also supports
`--help`. Each process has one configured central identity, so clients needing
different device grants must use different configurations/processes.

Discover `list_devices`, `inspect_project`, `list_files`, `read_file`,
`search_code`, `write_file`, `apply_patch`, `validate_flow`, `run_flow`,
`run_command`, `get_job`, `read_job_output`, and `cancel_job`. Tool schemas expose
explicit `request_id`, `device_id`, and `project_id` on every project operation,
and mandatory `revision` on edits. `list_devices` needs no project/request ID.
Tool names, operation arguments, bounds, results, and diagnostics follow the
request contract above; omit `operation` and the nested `arguments` envelope
when supplying tool arguments. For example:

```json
{"request_id":"inspect-1","device_id":"edge-a","project_id":"development"}
```

Call that object with `inspect_project`. Mutations retain the exact request ID
and content through central to Edge. Retry identical requests after uncertain
acknowledgements; changed content needs a new ID. Validation, flow runs, commands,
and cancellation use the existing Edge jobs. Poll `get_job`, page command output
with `next_cursor`, and wait for a terminal cancellation status. Unavailable
and `delivery_unknown` responses retain their existing meaning. MCP argument
validation errors use MCP `isError`; accepted routing/Edge responses preserve
`ok` and `error` inside the returned JSON text block. Do not interpret MCP
`isError: false` as successful project execution: inspect `ok` and the job status.

A real interactive client, demo topology fixture, UI inspector, transcript and
reproduction steps are recorded in
[the ticket 08 evidence](../.scratch/workspace-edge-agent/evidence/08/README.md).
