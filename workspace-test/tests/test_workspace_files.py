from http.client import HTTPResponse
from pathlib import Path
import socket
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from urllib.parse import urlsplit

from playwright.sync_api import expect

from browser_support import browser_page, local_ui
from test_application import application, sign_in
from test_application_lifecycle import create_workspace


class WorkspaceFilesTests(unittest.TestCase):
    def test_signed_in_workspace_creates_folders_and_uploads_files(self):
        with TemporaryDirectory() as temp, browser_page() as page:
            parent = Path(temp).resolve()
            state, root = parent / "state", parent / "Example"
            content = bytes(range(256)) * 3073
            with application(state) as url:
                workspace = create_workspace(page, url, state, root)
                workspace_path = workspace.get_attribute("href").rstrip("/")
                anonymous = page.context.browser.new_context()
                try:
                    endpoint = f"{url}{workspace_path}/files"
                    self.assertEqual(anonymous.request.get(endpoint).status, 401)
                    self.assertEqual(anonymous.request.post(
                        f"{endpoint}/upload?name=blocked.bin", data=b"blocked",
                        headers={"Origin": url, "Content-Type": "application/octet-stream"},
                    ).status, 401)
                finally:
                    anonymous.close()
                self.assertEqual(page.request.post(
                    f"{endpoint}/folders", data={"name": "Blocked"}, headers={"Origin": "http://example.invalid"},
                ).status, 403)

                workspace.click()
                page.get_by_role("button", name="Files", exact=True).click()
                dialog = page.get_by_role("dialog", name="Workspace files")
                expect(dialog.get_by_text("This folder is empty.")).to_be_visible()
                self.assertTrue((root / "flow.py").is_file())
                folder_name = dialog.get_by_label("New folder name")
                create = dialog.get_by_role("button", name="Create folder")
                folder_name.fill("Input & samples")
                create.click()
                folder = dialog.get_by_role("button", name="Input & samples", exact=True)
                folder.drop({"files": {"name": "capture #1.bin", "mimeType": "application/octet-stream", "buffer": content}})
                expect(dialog.get_by_role("status")).to_have_text("1 file uploaded.")
                folder.click()
                expect(dialog.get_by_text("capture #1.bin", exact=True)).to_be_visible()
                saved = root / ".workspace/files/Input & samples/capture #1.bin"
                self.assertEqual(saved.read_bytes(), content)

                picker = dialog.locator('input[type="file"]')
                picker.set_input_files({"name": saved.name, "mimeType": "application/octet-stream", "buffer": b"duplicate"})
                expect(dialog.get_by_role("alert")).to_contain_text("already exists")
                self.assertEqual(saved.read_bytes(), content)
                folder_name.fill("Nested")
                create.click()
                dialog.get_by_role("button", name="Nested", exact=True).click()
                with page.expect_file_chooser() as chooser:
                    dialog.get_by_role("button", name="Choose files").click()
                chooser.value.set_files({"name": "empty.txt", "mimeType": "text/plain", "buffer": b""})
                expect(dialog.get_by_text("empty.txt", exact=True)).to_be_visible()
                self.assertEqual((saved.parent / "Nested/empty.txt").read_bytes(), b"")

            with application(state) as url:
                sign_in(page, url, state)
                page.get_by_role("link", name="Example").click()
                page.get_by_role("button", name="Files", exact=True).click()
                dialog.get_by_role("button", name="Input & samples", exact=True).click()
                expect(dialog.get_by_text("capture #1.bin", exact=True)).to_be_visible()
                self.assertEqual(saved.read_bytes(), content)

    def test_standalone_workspace_rejects_unsafe_and_interrupted_uploads(self):
        with TemporaryDirectory() as temp, browser_page() as page:
            parent = Path(temp).resolve()
            root = parent / "Example"
            root.mkdir()
            cli = Path(sys.executable).with_name("workspace")
            subprocess.run([cli, "init", "--with-main"], cwd=root, check=True, capture_output=True)
            storage = root / ".workspace/files"
            storage.mkdir(parents=True, exist_ok=True)
            if sys.platform != "win32":
                (storage / "linked").symlink_to(parent, target_is_directory=True)

            with local_ui(root) as (url, _):
                page.set_viewport_size({"width": 390, "height": 844})
                page.goto(url)
                page.get_by_role("button", name="Files", exact=True).click()
                dialog = page.get_by_role("dialog", name="Workspace files")
                expect(dialog.get_by_text("This folder is empty.")).to_be_visible()
                headers = {"Origin": url, "Content-Type": "application/octet-stream"}
                self.assertEqual(page.request.post(
                    f"{url}/files/upload?path=..%2F..&name=escaped.bin", data=b"blocked", headers=headers,
                ).status, 400)
                if sys.platform != "win32":
                    self.assertEqual(page.request.post(
                        f"{url}/files/upload?path=linked&name=escaped.bin", data=b"blocked", headers=headers,
                    ).status, 400)
                self.assertFalse((parent / "escaped.bin").exists())

                address = urlsplit(url)
                with socket.create_connection((address.hostname, address.port), timeout=5) as connection:
                    connection.sendall((
                        "POST /files/upload?name=partial.bin HTTP/1.1\r\n"
                        f"Host: {address.netloc}\r\nOrigin: {url}\r\n"
                        "Content-Type: application/octet-stream\r\nContent-Length: 100\r\n\r\npartial"
                    ).encode("ascii"))
                    connection.shutdown(socket.SHUT_WR)
                    with HTTPResponse(connection) as response:
                        response.begin()
                        self.assertEqual(response.status, 400)
                self.assertFalse((storage / "partial.bin").exists())
                self.assertEqual(list(storage.glob(".phyzflow-transfer-*")), [])

                dialog.get_by_role("region", name="Current folder").drop({
                    "files": {"name": "sample.txt", "mimeType": "text/plain", "buffer": b"ready"},
                })
                expect(dialog.get_by_text("sample.txt", exact=True)).to_be_visible()
                self.assertEqual((storage / "sample.txt").read_bytes(), b"ready")
