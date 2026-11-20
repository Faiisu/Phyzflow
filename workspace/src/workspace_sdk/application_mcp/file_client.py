import argparse
from base64 import b64decode, b64encode
from contextlib import suppress
from hashlib import file_digest, sha256
import json
import os
from pathlib import Path
import sys
from tempfile import NamedTemporaryFile

import anyio
import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.exceptions import MCPError

from .file_paths import atomic_move


async def call_tool(client: Client, name: str, *, retry: bool = False, **arguments) -> dict:
    try:
        result = await client.call_tool(name, arguments)
    except (MCPError, httpx2.RequestError):
        if not retry:
            raise
        result = await client.call_tool(name, arguments)
    if result.is_error:
        raise ValueError(str(result.content[0].text) if result.content else "The MCP file tool failed.")
    return result.structured_content if result.structured_content is not None else json.loads(result.content[0].text)


async def upload(client: Client, args) -> dict:
    with args.local_path.open("rb") as stream:
        size = os.fstat(stream.fileno()).st_size
        checksum = file_digest(stream, "sha256").hexdigest()
        stream.seek(0)
        started = await call_tool(
            client, "begin_workspace_transfer", workspace_id=args.workspace_id, path=args.remote_path,
            direction="upload", revision=args.revision, size=size, sha256=checksum,
        )
        identity = started["transfer_id"]
        offset = 0
        finished = False
        try:
            while chunk := stream.read(started["chunk_bytes"]):
                result = await call_tool(
                    client, "write_workspace_chunk", retry=True, transfer_id=identity, offset=offset,
                    data=b64encode(chunk).decode("ascii"),
                )
                if result["next_offset"] != offset + len(chunk):
                    raise ValueError("The server reported an unexpected upload offset.")
                offset = result["next_offset"]
            result = await call_tool(
                client, "finish_workspace_transfer", retry=True, transfer_id=identity,
                session_ids=dict(args.session),
            )
            finished = True
            if result["sha256"] != checksum or result["size"] != size:
                raise ValueError("The server reported a different size or SHA-256.")
            return result
        finally:
            if not finished:
                with suppress(ValueError, MCPError, httpx2.RequestError):
                    await call_tool(client, "cancel_workspace_transfer", transfer_id=identity)


async def download(client: Client, args) -> dict:
    destination = args.local_path
    if os.path.lexists(destination):
        raise FileExistsError("The local destination already exists.")
    started = await call_tool(
        client, "begin_workspace_transfer", workspace_id=args.workspace_id, path=args.remote_path,
        direction="download", revision=args.revision,
    )
    identity = started["transfer_id"]
    stage = None
    finished = False
    try:
        checksum = sha256()
        offset = 0
        with NamedTemporaryFile(dir=destination.parent, prefix=".phyzflow-download-", delete=False) as stream:
            stage = Path(stream.name)
            while offset < started["size"]:
                chunk = await call_tool(
                    client, "read_workspace_chunk", retry=True, transfer_id=identity,
                    offset=offset, count=started["chunk_bytes"],
                )
                data = b64decode(chunk["data"], validate=True)
                if not data or chunk["next_offset"] != offset + len(data) or chunk["next_offset"] > started["size"]:
                    raise ValueError("The server returned an unexpected download offset.")
                stream.write(data)
                checksum.update(data)
                offset = chunk["next_offset"]
        result = await call_tool(client, "finish_workspace_transfer", retry=True, transfer_id=identity)
        finished = True
        if result["sha256"] != checksum.hexdigest() or result["size"] != offset:
            raise ValueError("The downloaded size or SHA-256 does not match.")
        atomic_move(stage, destination)
        return result
    finally:
        if stage is not None:
            stage.unlink(missing_ok=True)
        if not finished:
            with suppress(ValueError, MCPError, httpx2.RequestError):
                await call_tool(client, "cancel_workspace_transfer", transfer_id=identity)


async def transfer(args, token: str) -> dict:
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx2.AsyncClient(headers=headers) as http:
        endpoint = args.url.rstrip("/") + "/mcp"
        async with Client(streamable_http_client(endpoint, http_client=http), cache=None) as client:
            try:
                return await args.operation(client, args)
            except (OSError, ValueError, MCPError, httpx2.HTTPError) as error:
                failure = error
    raise failure


def session_argument(value: str) -> tuple[str, str]:
    sheet, separator, identity = value.rpartition("=")
    if not separator or not sheet or not identity:
        raise argparse.ArgumentTypeError("Expected a Sheet path and session ID in SHEET=ID form.")
    return sheet, identity


def main() -> int:
    parser = argparse.ArgumentParser(description="Stream a local file over Phyzflow MCP.")
    parser.add_argument("--url", required=True, help="Phyzflow base URL.")
    parser.add_argument("--workspace-id", required=True)
    commands = parser.add_subparsers(required=True)
    put = commands.add_parser("upload")
    put.add_argument("local_path", type=Path)
    put.add_argument("remote_path")
    put.add_argument("--revision", default="missing")
    put.add_argument("--session", type=session_argument, action="append", default=[])
    put.set_defaults(operation=upload)
    get = commands.add_parser("download")
    get.add_argument("remote_path")
    get.add_argument("local_path", type=Path)
    get.add_argument("--revision")
    get.set_defaults(operation=download)
    args = parser.parse_args()
    token = os.environ.get("PHYZFLOW_MCP_TOKEN")
    if not token:
        parser.error("Set PHYZFLOW_MCP_TOKEN to the MCP bearer token.")
    status = 0
    try:
        print(json.dumps(anyio.run(transfer, args, token)))
    except* (OSError, ValueError, MCPError, httpx2.HTTPError) as error:
        cause = error
        while isinstance(cause, BaseExceptionGroup):
            cause = cause.exceptions[0]
        print(f"Transfer failed: {cause or type(cause).__name__}", file=sys.stderr)
        status = 1
    return status


if __name__ == "__main__":
    raise SystemExit(main())
