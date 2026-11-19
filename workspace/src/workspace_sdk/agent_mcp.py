"""Optional stdio MCP frontend for the authenticated HTTPS central router."""

import argparse
import json
from pathlib import Path
from typing import Annotated, Literal

from .agent_remote import RemoteClient, private_token


def create_mcp_server(client):
    # Keep the plain SDK independent of the optional MCP dependency.
    from mcp.server import MCPServer
    from pydantic import Field

    text = Annotated[str, Field(strict=True, min_length=1)]
    server = MCPServer('Workspace Edge development')

    @server.tool()
    def list_devices() -> dict:
        """List devices granted to this configured central identity and their availability."""
        return client.list_devices()

    @server.tool()
    def inspect_project(request_id: text, device_id: text, project_id: text) -> dict:
        """Inspect the selected Edge project's interpreter and discovered sheets."""
        return client.request(request_id, device_id, project_id, 'inspect_project')

    @server.tool()
    def list_files(request_id: text, device_id: text, project_id: text, path: str = '.') -> dict:
        """List bounded immediate entries in a project-relative directory."""
        return client.request(request_id, device_id, project_id, 'list_files', {'path': path})

    @server.tool()
    def read_file(request_id: text, device_id: text, project_id: text, path: text) -> dict:
        """Read source and its revision. A truncated read cannot authorize editing."""
        return client.request(request_id, device_id, project_id, 'read_file', {'path': path})

    @server.tool()
    def search_code(request_id: text, device_id: text, project_id: text, query: text) -> dict:
        """Search project source for literal text; inspect truncated matches separately."""
        return client.request(request_id, device_id, project_id, 'search_code', {'query': query})

    @server.tool()
    def write_file(request_id: text, device_id: text, project_id: text, path: text,
                   revision: text, source: str) -> dict:
        """Create (revision='missing') or replace source with an expected revision.

        Retry lost acknowledgements with the SAME request_id and identical arguments.
        """
        return client.request(request_id, device_id, project_id, 'write_file',
                              {'path': path, 'revision': revision, 'source': source})

    @server.tool()
    def apply_patch(request_id: text, device_id: text, project_id: text, path: text,
                    revision: text, old_text: text, new_text: str) -> dict:
        """Replace one exact source occurrence with revision protection. Retry identical IDs/content."""
        return client.request(request_id, device_id, project_id, 'apply_patch',
                              {'path': path, 'revision': revision, 'old_text': old_text, 'new_text': new_text})

    @server.tool()
    def validate_flow(request_id: text, device_id: text, project_id: text, sheet: str = 'Main') -> dict:
        """Start validation without executing nodes; returns an Edge job_id. Poll get_job."""
        return client.request(request_id, device_id, project_id, 'validate_flow', {'sheet': sheet})

    @server.tool()
    def run_flow(request_id: text, device_id: text, project_id: text, sheet: str = 'Main',
                 input: dict | None = None) -> dict:
        """Start a flow using the shared Runner/history. Poll job_id; retry identical IDs/content."""
        return client.request(request_id, device_id, project_id, 'run_flow',
                              {'sheet': sheet, 'input': {} if input is None else input})

    @server.tool()
    def run_command(request_id: text, device_id: text, project_id: text, program: text,
                    argv: list[Annotated[str, Field(strict=True)]], cwd: str = '.') -> dict:
        """Start an operator-allowed development command, never a shell string.

        Use python for the selected project interpreter; pip/tests are command jobs.
        Returns job_id immediately. Retry lost acknowledgements with identical IDs/content.
        """
        return client.request(request_id, device_id, project_id, 'run_command',
                              {'program': program, 'argv': argv, 'cwd': cwd})

    @server.tool()
    def get_job(request_id: text, device_id: text, project_id: text, job_id: text) -> dict:
        """Read Edge-owned job status and completed result/diagnostics, including source locations."""
        return client.request(request_id, device_id, project_id, 'get_job', {'job_id': job_id})

    @server.tool()
    def read_job_output(request_id: text, device_id: text, project_id: text, job_id: text,
                        stream: Literal['stdout', 'stderr'] = 'stdout',
                        cursor: Annotated[int, Field(strict=True, ge=0)] = 0,
                        limit: Annotated[int, Field(strict=True, ge=1, le=16384)] = 4096) -> dict:
        """Read a bounded command output page; continue from next_cursor until complete."""
        return client.request(request_id, device_id, project_id, 'read_job_output',
                              {'job_id': job_id, 'stream': stream, 'cursor': cursor, 'limit': limit})

    @server.tool()
    def cancel_job(request_id: text, device_id: text, project_id: text, job_id: text) -> dict:
        """Request Edge cancellation. Offline/unknown delivery is not confirmation; poll status.

        After reconnect retry the original request_id and identical content.
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
