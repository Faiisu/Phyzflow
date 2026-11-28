"""Optional stdio MCP frontend for the authenticated HTTPS central router."""

import argparse
import json
from pathlib import Path
from typing import Annotated, Any, Literal

from .agent_remote import RemoteClient, private_token
from .mcp_flow_guidance import FLOW_AUTHORING_GUIDANCE


def create_mcp_server(client):
    # Keep the plain SDK independent of the optional MCP dependency.
    from mcp.server import MCPServer
    from mcp_types import ToolAnnotations
    from pydantic import Field

    RequestId = Annotated[str, Field(
        strict=True, min_length=1,
        description="Caller-chosen ID for this request. Reusing it with identical arguments replays the "
        "original outcome instead of repeating the action.",
    )]
    DeviceId = Annotated[str, Field(strict=True, min_length=1, description="Device ID returned by list_devices.")]
    ProjectId = Annotated[str, Field(strict=True, min_length=1, description="Project ID configured on the device.")]
    ProjectPath = Annotated[str, Field(
        strict=True, min_length=1, description="Forward-slash path relative to the project root.",
    )]
    JobId = Annotated[str, Field(
        strict=True, min_length=1, description="Job ID returned by validate_flow, run_flow, or run_command.",
    )]
    Revision = Annotated[str, Field(
        strict=True, min_length=1,
        description="Revision returned by read_file, or 'missing' to create a file that does not exist yet.",
    )]
    SheetName = Annotated[str, Field(description="Sheet name. 'Main' addresses the root sheet.")]

    def tool(title, *, read_only, destructive=False):
        return {
            "title": title,
            "annotations": ToolAnnotations(
                title=title,
                read_only_hint=read_only,
                destructive_hint=False if read_only else destructive,
                idempotent_hint=True,
                open_world_hint=False,
            ),
            "structured_output": True,
        }

    server = MCPServer(
        'Workspace Edge development',
        instructions=(
            f"{FLOW_AUTHORING_GUIDANCE}"
            "After editing, call validate_flow and wait for the job to succeed using get_job. "
            "Call run_flow with a representative input, then use get_job to verify the result "
            "and compare its steps with the planned graph. "
            "Every device call takes a request_id; reuse the same request_id and arguments to retry "
            "a call whose acknowledgement was lost."
        ),
    )

    @server.tool(**tool("List devices", read_only=True))
    def list_devices() -> dict[str, Any]:
        """List the Edge devices granted to this central identity and whether each is connected."""
        return client.list_devices()

    @server.tool(**tool("Inspect project", read_only=True))
    def inspect_project(request_id: RequestId, device_id: DeviceId, project_id: ProjectId) -> dict[str, Any]:
        """Return an Edge project's selected Python interpreter and its discovered sheets."""
        return client.request(request_id, device_id, project_id, 'inspect_project')

    @server.tool(**tool("List project files", read_only=True))
    def list_files(
        request_id: RequestId, device_id: DeviceId, project_id: ProjectId,
        path: Annotated[str, Field(description="Directory relative to the project root. '.' is the root.")] = '.',
    ) -> dict[str, Any]:
        """List a bounded page of immediate entries in a project directory."""
        return client.request(request_id, device_id, project_id, 'list_files', {'path': path})

    @server.tool(**tool("Read project file", read_only=True))
    def read_file(request_id: RequestId, device_id: DeviceId, project_id: ProjectId, path: ProjectPath) -> dict[str, Any]:
        """Read a source file and its revision. A truncated read returns no revision usable for editing."""
        return client.request(request_id, device_id, project_id, 'read_file', {'path': path})

    @server.tool(**tool("Search project code", read_only=True))
    def search_code(
        request_id: RequestId, device_id: DeviceId, project_id: ProjectId,
        query: Annotated[str, Field(strict=True, min_length=1, description="Literal text to find. Not a regular expression.")],
    ) -> dict[str, Any]:
        """Find lines containing literal text in project source. Long matching lines are truncated."""
        return client.request(request_id, device_id, project_id, 'search_code', {'query': query})

    @server.tool(**tool("Write project file", read_only=False, destructive=True))
    def write_file(
        request_id: RequestId, device_id: DeviceId, project_id: ProjectId, path: ProjectPath,
        revision: Revision,
        source: Annotated[str, Field(description="Complete new file text.")],
    ) -> dict[str, Any]:
        """Create or fully replace a source file. Fails with a conflict when the file changed since revision."""
        return client.request(request_id, device_id, project_id, 'write_file',
                              {'path': path, 'revision': revision, 'source': source})

    @server.tool(**tool("Patch project file", read_only=False, destructive=True))
    def apply_patch(
        request_id: RequestId, device_id: DeviceId, project_id: ProjectId, path: ProjectPath,
        revision: Annotated[str, Field(strict=True, min_length=1, description="Revision returned by read_file.")],
        old_text: Annotated[str, Field(strict=True, min_length=1, description="Exact text that occurs once in the file.")],
        new_text: Annotated[str, Field(description="Replacement text. May be empty.")],
    ) -> dict[str, Any]:
        """Replace the single exact occurrence of old_text. Fails with a conflict when the file changed since revision."""
        return client.request(request_id, device_id, project_id, 'apply_patch',
                              {'path': path, 'revision': revision, 'old_text': old_text, 'new_text': new_text})

    @server.tool(**tool("Validate flow", read_only=True))
    def validate_flow(
        request_id: RequestId, device_id: DeviceId, project_id: ProjectId, sheet: SheetName = 'Main',
    ) -> dict[str, Any]:
        """Start loading a sheet's flow without running any step. Returns a job_id whose result get_job reports."""
        return client.request(request_id, device_id, project_id, 'validate_flow', {'sheet': sheet})

    @server.tool(**tool("Run flow", read_only=False))
    def run_flow(
        request_id: RequestId, device_id: DeviceId, project_id: ProjectId, sheet: SheetName = 'Main',
        input: Annotated[dict | None, Field(description="JSON object passed to the flow. Omit for an empty input.")] = None,
    ) -> dict[str, Any]:
        """Start a flow run recorded in the device's run history. Returns a job_id whose progress get_job reports."""
        return client.request(request_id, device_id, project_id, 'run_flow',
                              {'sheet': sheet, 'input': {} if input is None else input})

    @server.tool(**tool("Run command", read_only=False, destructive=True))
    def run_command(
        request_id: RequestId, device_id: DeviceId, project_id: ProjectId,
        program: Annotated[str, Field(
            strict=True, min_length=1,
            description="Operator-allowed program name. 'python' selects the project interpreter.",
        )],
        argv: Annotated[list[Annotated[str, Field(strict=True)]], Field(
            description="Arguments passed to the program directly, without a shell.",
        )],
        cwd: Annotated[str, Field(description="Working directory relative to the project root.")] = '.',
    ) -> dict[str, Any]:
        """Start an operator-allowed development command, such as pip or a test runner, as a job.

        Arguments are passed without a shell. Returns a job_id immediately; read_job_output pages its output.
        """
        return client.request(request_id, device_id, project_id, 'run_command',
                              {'program': program, 'argv': argv, 'cwd': cwd})

    @server.tool(**tool("Get job", read_only=True))
    def get_job(request_id: RequestId, device_id: DeviceId, project_id: ProjectId, job_id: JobId) -> dict[str, Any]:
        """Return a job's status and, once complete, its result and diagnostics with source locations."""
        return client.request(request_id, device_id, project_id, 'get_job', {'job_id': job_id})

    @server.tool(**tool("Read job output", read_only=True))
    def read_job_output(
        request_id: RequestId, device_id: DeviceId, project_id: ProjectId, job_id: JobId,
        stream: Annotated[Literal['stdout', 'stderr'], Field(description="Output stream to read.")] = 'stdout',
        cursor: Annotated[int, Field(
            strict=True, ge=0, description="Byte cursor; next_cursor from the previous page.",
        )] = 0,
        limit: Annotated[int, Field(strict=True, ge=1, le=16384, description="Maximum bytes to return.")] = 4096,
    ) -> dict[str, Any]:
        """Read one bounded page of a command job's output. next_cursor continues until complete is true."""
        return client.request(request_id, device_id, project_id, 'read_job_output',
                              {'job_id': job_id, 'stream': stream, 'cursor': cursor, 'limit': limit})

    @server.tool(**tool("Cancel job", read_only=False, destructive=True))
    def cancel_job(request_id: RequestId, device_id: DeviceId, project_id: ProjectId, job_id: JobId) -> dict[str, Any]:
        """Request cancellation of a running job on the device.

        Delivery to an offline device is not confirmation; get_job reports the job's final status.
        """
        return client.request(request_id, device_id, project_id, 'cancel_job', {'job_id': job_id})

    return server


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True, type=Path, help='Private operator configuration JSON path')
    args = parser.parse_args()
    try:
        config = json.loads(args.config.read_text())
        client = RemoteClient(config['central_url'], private_token(config['client_token_file']),
                              cafile=config.get('ca_file'))
        server = create_mcp_server(client)
    except ImportError:
        parser.exit(1, 'Install workspace-sdk[mcp] to use the MCP frontend.\n')
    except (OSError, ValueError, KeyError, TypeError):
        parser.exit(1, 'Invalid MCP configuration; check HTTPS origin, CA and private token file.\n')
    server.run(transport='stdio')


if __name__ == '__main__':
    main()
