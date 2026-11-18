from contextlib import asynccontextmanager
import json
from pathlib import Path
from secrets import token_urlsafe
from tempfile import TemporaryDirectory
import unittest

from anyio.from_thread import start_blocking_portal
import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.exceptions import MCPError
from playwright.sync_api import expect

from browser_support import browser_page
from test_application import application, sign_in


@asynccontextmanager
async def mcp_client(url, token):
    async with httpx2.AsyncClient(headers={"Authorization": f"Bearer {token}"}) as http:
        transport = streamable_http_client(f"{url}/mcp", http_client=http)
        async with Client(transport, cache=None, raise_exceptions=False) as client:
            yield client


def call(portal, client, name, **arguments):
    return portal.call(client.call_tool, name, arguments)


def output(result):
    return json.loads(result.content[0].text)


class ApplicationMcpTests(unittest.TestCase):
    def test_administrator_issues_changes_and_revokes_live_workspace_access(self):
        with TemporaryDirectory() as temp, browser_page() as page, start_blocking_portal() as portal:
            parent = Path(temp).resolve()
            state = parent / "state"
            with application(state) as url:
                sign_in(page, url, state)
                ids = {}
                for name in ("Weather", "Telemetry"):
                    response = page.request.post(
                        f"{url}/workspaces", headers={"Origin": url}, data={"path": str(parent / name)},
                    )
                    self.assertEqual(response.status, 201)
                    ids[name] = response.json()["id"]

                page.get_by_role("button", name="MCP access").click()
                page.get_by_label("Client name").fill("Development client")
                page.get_by_role("checkbox", name="Weather").check()
                page.get_by_role("button", name="Issue token").click()
                secret = page.get_by_label("MCP bearer token")
                expect(secret).to_be_visible()
                token = secret.input_value()
                self.assertGreaterEqual(len(token), 32)
                page.get_by_role("button", name="Close", exact=True).click()
                page.get_by_role("button", name="MCP access").click()
                expect(secret).to_be_hidden()

                guest = page.context.browser.new_context()
                try:
                    self.assertEqual(guest.request.get(f"{url}/mcp-tokens").status, 401)
                    self.assertEqual(guest.request.post(f"{url}/mcp-tokens", headers={"Origin": url}).status, 401)
                    headers = {"Authorization": f"Bearer {token}"}
                    self.assertEqual(guest.request.get(f"{url}/workspaces", headers=headers).status, 401)
                    self.assertEqual(guest.request.get(f"{url}/workspaces/{ids['Weather']}/", headers=headers).status, 401)
                    self.assertEqual(page.request.post(f"{url}/mcp", data={}).status, 401)
                    self.assertEqual(page.request.post(f"{url}/mcp-tokens", data={}).status, 403)

                    with portal.wrap_async_context_manager(mcp_client(url, token)) as client:
                        names = {tool.name for tool in portal.call(client.list_tools).tools}
                        self.assertLessEqual({"list_workspaces", "inspect_workspace"}, names)
                        self.assertTrue({"run_command", "update_workspace", "restart_workspace"}.isdisjoint(names))
                        self.assertEqual(output(call(portal, client, "list_workspaces")), {
                            "workspaces": [{"id": ids["Weather"], "name": "Weather"}],
                        })
                        available = call(portal, client, "inspect_workspace", workspace_id=ids["Weather"])
                        self.assertEqual(output(available)["sheets"], [{"name": "Main", "entrypoint": "flow.py"}])
                        denied = call(portal, client, "inspect_workspace", workspace_id=ids["Telemetry"])
                        self.assertTrue(denied.is_error)
                        self.assertIn("Workspace access denied", denied.content[0].text)

                        page.get_by_label("Token", exact=True).select_option(label="Development client")
                        page.get_by_role("checkbox", name="Weather").uncheck()
                        page.get_by_role("checkbox", name="Telemetry").check()
                        page.get_by_role("button", name="Save grants").click()
                        expect(page.get_by_role("status").filter(has_text="Grants saved.")).to_be_visible()
                        denied = call(portal, client, "inspect_workspace", workspace_id=ids["Weather"])
                        self.assertTrue(denied.is_error)
                        self.assertIn("Workspace access denied", denied.content[0].text)
                        available = call(portal, client, "inspect_workspace", workspace_id=ids["Telemetry"])
                        self.assertEqual(output(available)["name"], "Telemetry")

                        page.get_by_role("button", name="Revoke token").click()
                        expect(page.get_by_role("status").filter(has_text="Token revoked.")).to_be_visible()
                        with self.assertRaises(MCPError):
                            call(portal, client, "inspect_workspace", workspace_id=ids["Telemetry"])
                        self.assertEqual(guest.request.post(f"{url}/mcp", headers=headers, data={}).status, 401)

                    invalid_headers = {"Authorization": f"Bearer {token_urlsafe(32)}"}
                    for workspace_id in ids.values():
                        response = guest.request.post(
                            f"{url}/mcp", headers=invalid_headers,
                            data={"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
                                "name": "inspect_workspace", "arguments": {"workspace_id": workspace_id},
                            }},
                        )
                        self.assertEqual(response.status, 401)
                finally:
                    guest.close()
