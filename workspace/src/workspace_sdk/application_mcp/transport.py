from concurrent.futures import TimeoutError as FuturesTimeoutError
from contextlib import asynccontextmanager, contextmanager, suppress
from threading import Event

from ..dashboard import DASHBOARD_RESIZE_GUIDANCE
from ..mcp_flow_guidance import FLOW_AUTHORING_GUIDANCE

from .access import WorkspaceAccess, request_identity
from .tools import register_workspace_tools


MCP_BODY_LIMIT = 1024 * 1024
MCP_REQUEST_HEADERS = {"accept", "content-type", "mcp-protocol-version", "mcp-session-id"}
MCP_RESPONSE_HEADERS = {"allow", "content-type", "mcp-protocol-version", "mcp-session-id"}


@contextmanager
def mcp_transport(access: WorkspaceAccess):
    try:
        from mcp.server import MCPServer
    except ModuleNotFoundError as error:
        if error.name != "mcp":
            raise
        raise ValueError("Install workspace-sdk[mcp] to run the Phyzflow application.") from None

    from anyio.from_thread import start_blocking_portal
    from httpx2 import ASGITransport, AsyncClient

    server = MCPServer(
        "Phyzflow",
        instructions=(
            f"{FLOW_AUTHORING_GUIDANCE}"
            "After editing, call validate_flow and compare its step IDs with the planned graph. "
            "Call run_flow with a representative input and verify the result with inspect_execution.\n\n"
            "Before creating or editing a dashboard panel, call inspect_dashboards for its layout and theme contract. "
            f"{DASHBOARD_RESIZE_GUIDANCE}"
        ),
        log_level="WARNING",
    )
    register_workspace_tools(server, access)
    app = server.streamable_http_app(
        streamable_http_path="/mcp", stateless_http=True, json_response=True, max_request_body_size=MCP_BODY_LIMIT,
    )

    @asynccontextmanager
    async def open_client():
        async with server.session_manager.run(), AsyncClient(
            base_url="http://127.0.0.1:8080", transport=ASGITransport(app=app, raise_app_exceptions=False),
        ) as client:
            yield client

    async def dispatch(client, identity, method, headers, body):
        credential = request_identity.set(identity)
        try:
            return await client.request(method, "/mcp", headers=headers, content=body)
        finally:
            request_identity.reset(credential)

    with start_blocking_portal() as portal, portal.wrap_async_context_manager(open_client()) as client:
        def send_mcp(identity, method, headers, body, *, disconnected=None):
            import anyio

            scope_holder = []
            ready = Event()
            done = Event()

            async def run_dispatch():
                with anyio.CancelScope() as scope:
                    scope_holder.append(scope)
                    ready.set()
                    try:
                        return await dispatch(client, identity, method, headers, body)
                    finally:
                        done.set()

            future = portal.start_task_soon(run_dispatch)
            while True:
                try:
                    return future.result(timeout=0.05)
                except FuturesTimeoutError:
                    if disconnected is not None and disconnected():
                        ready.wait(timeout=1)
                        if scope_holder:
                            with suppress(RuntimeError):
                                portal.call(scope_holder[0].cancel)
                        done.wait(timeout=5)
                        return None

        yield send_mcp
