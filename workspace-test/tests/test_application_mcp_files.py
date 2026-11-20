from base64 import b64decode, b64encode
from contextlib import contextmanager
from hashlib import file_digest, sha256
from io import BytesIO
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from threading import Event, Thread
from time import monotonic
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from uuid import uuid4
from zipfile import ZipFile

from anyio.from_thread import start_blocking_portal
import httpx2

from test_application import application
from test_application_mcp import mcp_client, output
from workspace_sdk.application_mcp.access import McpTokens, WorkspaceAccess, request_identity
from workspace_sdk.application_mcp.file_transfers import CHUNK_BYTES, FileTransfers


@contextmanager
def installed_workspace(*, processes=None, empty_project=False):
    with TemporaryDirectory() as temp, start_blocking_portal() as portal:
        parent = Path(temp).resolve()
        state = parent / "state"
        root = parent / "Workspace"
        with application(state, processes=processes) as url, httpx2.Client(
            base_url=url, headers={"Origin": url},
        ) as admin:
            password = (state / "admin" / "initial-password").read_text().strip()
            response = admin.post("/login", data={"username": "admin", "password": password})
            assert response.is_redirect, response.text
            action = "add" if empty_project else "create"
            if empty_project:
                root.mkdir()
            response = admin.post("/workspaces", json={"path": str(root), "action": action})
            assert response.status_code == 201, response.text
            workspace_id = response.json()["id"]
            response = admin.post("/mcp-tokens", json={"name": "File client", "workspace_ids": [workspace_id]})
            assert response.status_code == 201, response.text
            token_data = response.json()
            with portal.wrap_async_context_manager(mcp_client(url, token_data["token"])) as client:
                def call(name, /, **arguments):
                    return portal.call(client.call_tool, name, arguments)

                def revoke():
                    response = admin.post(f"/mcp-tokens/{token_data['id']}", json={"workspace_ids": []})
                    response.raise_for_status()

                yield parent, state, root, workspace_id, url, token_data["token"], call, revoke


class ApplicationMcpFilesTests(unittest.TestCase):
    def ok(self, call, name, /, **arguments):
        result = call(name, **arguments)
        self.assertFalse(result.is_error, result.content)
        return output(result)

    def failed(self, call, name, message, /, **arguments):
        result = call(name, **arguments)
        self.assertTrue(result.is_error, result.content)
        self.assertIn(message, result.content[0].text)

    def test_installed_file_operations_and_sheet_leases(self):
        with installed_workspace() as (_, _, root, workspace_id, _, _, call, _):
            self.ok(call, "create_sheet", workspace_id=workspace_id, name="Secondary")
            main = self.ok(call, "open_sheet", workspace_id=workspace_id)["session_id"]
            secondary = self.ok(call, "open_sheet", workspace_id=workspace_id, sheet="Secondary")["session_id"]
            sessions = {".": main, "flows/Secondary": secondary}
            self.failed(
                call, "write_workspace_text", "locked", workspace_id=workspace_id,
                path=".github/workflows/demo.yml", text="name: demo\n", revision="missing",
            )
            written = self.ok(
                call, "write_workspace_text", workspace_id=workspace_id, path=".github/workflows/demo.yml",
                text="name: demo\n", revision="missing", session_ids={".": main},
            )
            read = self.ok(call, "read_workspace_text", workspace_id=workspace_id, path=written["path"])
            self.assertEqual(read["text"], "name: demo\n")
            self.assertEqual(read["revision"], written["revision"])
            self.failed(call, "write_workspace_text", "revision changed", workspace_id=workspace_id,
                        path=written["path"], text="stale", revision="missing", session_ids={".": main})
            self.assertIn(".github", {
                entry["name"] for entry in self.ok(call, "list_workspace_files", workspace_id=workspace_id)["entries"]
            })
            moved = self.ok(
                call, "move_workspace_file", workspace_id=workspace_id, source_path=written["path"],
                destination_path="renamed", revision=written["revision"], session_ids={".": main},
            )
            self.failed(
                call, "move_workspace_file", "locked", workspace_id=workspace_id, source_path="renamed",
                destination_path="flows/Secondary/notes", revision=moved["revision"], session_ids={".": main},
            )
            moved = self.ok(
                call, "move_workspace_file", workspace_id=workspace_id, source_path="renamed",
                destination_path="flows/Secondary/notes", revision=moved["revision"], session_ids=sessions,
            )
            self.assertFalse((root / "renamed").exists())
            moved = self.ok(
                call, "move_workspace_file", workspace_id=workspace_id, source_path=moved["path"],
                destination_path="flows/Secondary/Notes", revision=moved["revision"],
                session_ids={"flows/Secondary": secondary},
            )
            self.assertIn("Notes", {path.name for path in (root / "flows/Secondary").iterdir()})

            self.assertEqual((root / moved["path"]).read_text(), "name: demo\n")
            self.ok(call, "write_workspace_text", workspace_id=workspace_id, path="taken", text="keep",
                    revision="missing", session_ids={".": main})
            self.failed(call, "move_workspace_file", "already exists", workspace_id=workspace_id,
                        source_path=moved["path"], destination_path="taken", revision=moved["revision"], session_ids=sessions)
            self.failed(call, "delete_workspace_file", "entrypoints", workspace_id=workspace_id,
                        path="flows/Secondary/flow.py", revision="missing", session_ids=sessions)
            for protected in (".workspace/edit-lock.json", ".git/config", ".venv/pyvenv.cfg", "../outside", "/outside"):
                self.failed(call, "stat_workspace_file", "Workspace-relative", workspace_id=workspace_id, path=protected)
            if os.name != "nt":
                (root / "alias").symlink_to(root / "taken")
                self.failed(call, "stat_workspace_file", "Symbolic links", workspace_id=workspace_id, path="alias")
                os.link(root / "taken", root / "hardlink")
                self.failed(call, "stat_workspace_file", "file aliases", workspace_id=workspace_id, path="hardlink")
            name = f"phyzflow-file-test-{uuid4().hex}"
            trash_target = self.ok(
                call, "write_workspace_text", workspace_id=workspace_id, path=name, text="recoverable",
                revision="missing", session_ids={".": main},
            )
            self.assertTrue(self.ok(call, "delete_workspace_file", workspace_id=workspace_id, path=name,
                                    revision=trash_target["revision"], session_ids={".": main})["trashed"])
            self.assertFalse((root / name).exists())
            if sys.platform in {"darwin", "linux"}:
                trashed = Path.home() / ".Trash" / name if sys.platform == "darwin" else (
                    Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "Trash" / "files" / name
                )
                trashed.rename(root / name)
                self.assertEqual((root / name).read_text(), "recoverable")
                if sys.platform == "linux":
                    (trashed.parents[1] / "info" / f"{name}.trashinfo").unlink()


    def test_installed_binary_transfers_and_failure_recovery(self):
        with installed_workspace() as (parent, _, root, workspace_id, url, token, call, revoke):
            archive = BytesIO()
            with ZipFile(archive, "w") as zip_file:
                zip_file.writestr("data.bin", b"\xff\x00" * (CHUNK_BYTES // 2) + b"tail")
            expected = archive.getvalue()
            digest = sha256(expected).hexdigest()
            params = {"workspace_id": workspace_id, "path": "assets/binary.zip", "direction": "upload",
                      "revision": "missing", "size": len(expected), "sha256": digest}
            self.failed(call, "begin_workspace_transfer", "insufficient", **{**params, "size": 10**18})
            identity = self.ok(call, "begin_workspace_transfer", **params)["transfer_id"]
            chunk = b64encode(expected[:CHUNK_BYTES]).decode()
            self.failed(call, "write_workspace_chunk", "Expected byte offset", transfer_id=identity, offset=1, data=chunk)
            self.failed(call, "write_workspace_chunk", "Base64", transfer_id=identity, offset=0, data="!!!")
            first = self.ok(call, "write_workspace_chunk", transfer_id=identity, offset=0, data=chunk)
            self.assertEqual(first, self.ok(call, "write_workspace_chunk", transfer_id=identity, offset=0, data=chunk))
            self.assertFalse((root / params["path"]).exists())
            self.failed(call, "finish_workspace_transfer", "incomplete", transfer_id=identity)
            self.ok(call, "write_workspace_chunk", transfer_id=identity, offset=CHUNK_BYTES,
                    data=b64encode(expected[CHUNK_BYTES:]).decode())
            finished = self.ok(call, "finish_workspace_transfer", transfer_id=identity)
            self.assertEqual(finished, self.ok(call, "finish_workspace_transfer", transfer_id=identity))
            self.assertEqual(finished["sha256"], digest)
            self.assertEqual((root / params["path"]).read_bytes(), expected)

            wrong = self.ok(call, "begin_workspace_transfer", **{
                **params, "sha256": "0" * 64, "revision": finished["revision"], "size": 1,
            })["transfer_id"]
            self.ok(call, "write_workspace_chunk", transfer_id=wrong, offset=0, data="YQ==")
            self.failed(call, "finish_workspace_transfer", "SHA-256 does not match", transfer_id=wrong)
            self.assertEqual((root / params["path"]).read_bytes(), expected)

            path = "a-new-target"
            pending_data = b"new upload"
            pending = self.ok(call, "begin_workspace_transfer", **{
                **params, "path": path, "sha256": sha256(pending_data).hexdigest(), "size": len(pending_data),
            })["transfer_id"]
            self.ok(call, "write_workspace_chunk", transfer_id=pending, offset=0,
                    data=b64encode(pending_data).decode())
            (root / path).write_text("created while uploading")
            self.failed(call, "finish_workspace_transfer", "revision changed", transfer_id=pending)
            self.assertEqual((root / path).read_text(), "created while uploading")
            self.ok(call, "cancel_workspace_transfer", transfer_id=pending)

            read_args = {"workspace_id": workspace_id, "path": params["path"], "direction": "download"}
            download = self.ok(call, "begin_workspace_transfer", **read_args)["transfer_id"]
            first = self.ok(call, "read_workspace_chunk", transfer_id=download, offset=0)
            self.assertEqual(first, self.ok(call, "read_workspace_chunk", transfer_id=download, offset=0))
            last = self.ok(call, "read_workspace_chunk", transfer_id=download, offset=first["next_offset"])
            self.assertEqual(b64decode(first["data"]) + b64decode(last["data"]), expected)
            self.assertEqual(self.ok(call, "finish_workspace_transfer", transfer_id=download)["sha256"], digest)

            download = self.ok(call, "begin_workspace_transfer", **read_args)["transfer_id"]
            with (root / params["path"]).open("ab") as stream:
                stream.write(b"new")
            self.failed(call, "read_workspace_chunk", "changed during download", transfer_id=download, offset=0)

            command = [sys.executable, "-m", "workspace_sdk.application_mcp.file_client",
                       "--url", url, "--workspace-id", workspace_id]
            for name, content in {
                "example.png": b"\x89PNG\r\n\x1a\n\x00", "example.pdf": b"%PDF-1.7\n\xff",
                ".gitignore": b"__pycache__/\n", "empty": b"",
            }.items():
                source, destination = parent / name, parent / f"received-{name}"
                source.write_bytes(content)
                for args in (["upload", str(source), name], ["download", name, str(destination)]):
                    result = subprocess.run([*command, *args], env={**os.environ, "PHYZFLOW_MCP_TOKEN": token},
                                            capture_output=True, text=True)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(json.loads(result.stdout)["sha256"], sha256(content).hexdigest())
                self.assertEqual(destination.read_bytes(), content)

            pending = self.ok(call, "begin_workspace_transfer", **{**params, "path": "pending"})["transfer_id"]
            self.assertTrue(self.ok(call, "cancel_workspace_transfer", transfer_id=pending)["cancelled"])
            pending = self.ok(call, "begin_workspace_transfer", **{**params, "path": "pending"})["transfer_id"]
            revoke()
            self.failed(call, "write_workspace_chunk", "Workspace access denied", transfer_id=pending,
                        offset=0, data="YQ==")
            self.assertFalse((root / "pending").exists())

    def test_edit_files_in_registered_workspace_without_a_main_sheet(self):
        with installed_workspace(empty_project=True) as (_, _, root, workspace_id, _, _, call, _):
            self.assertEqual(self.ok(call, "inspect_workspace", workspace_id=workspace_id)["sheets"], [])
            written = self.ok(
                call, "write_workspace_text", workspace_id=workspace_id, path=".gitignore",
                text="assets/\n", revision="missing",
            )
            self.assertEqual((root / ".gitignore").read_text(), "assets/\n")
            self.assertEqual(self.ok(call, "stat_workspace_file", workspace_id=workspace_id,
                                    path=".gitignore"), written)

    def test_repair_project_configuration_without_mounting_its_flow(self):
        with installed_workspace() as (_, _, root, workspace_id, _, _, call, _):
            (root / "pyproject.toml").write_text("malformed.toml = [")
            metadata = self.ok(call, "stat_workspace_file", workspace_id=workspace_id, path="pyproject.toml")
            self.ok(
                call, "write_workspace_text", workspace_id=workspace_id, path="pyproject.toml",
                text="[tool.workspace]\nformat_version = 1\n", revision=metadata["revision"],
            )
            self.assertEqual(self.ok(call, "inspect_workspace", workspace_id=workspace_id)["sheets"],
                             [{"name": "Main", "entrypoint": "flow.py"}])

    @unittest.skipUnless(os.environ.get("PHYZFLOW_MCP_LARGE_FILE_TEST"), "Opt in to the 1 GiB transfer benchmark.")
    def test_installed_gib_round_trip(self):
        processes = []
        with installed_workspace(processes=processes) as (parent, _, root, workspace_id, url, token, _call, _revoke):
            source, destination = parent / "source.zip", parent / "received.zip"
            with source.open("wb") as stream:
                stream.truncate(1024**3)
            command = [sys.executable, "-m", "workspace_sdk.application_mcp.file_client",
                       "--url", url, "--workspace-id", workspace_id]
            peak = {"server_rss_kib": 0, "staged_upload_bytes": 0, "staged_download_bytes": 0}
            stopped = Event()

            def sample():
                while not stopped.wait(0.05):
                    if os.name == "posix":
                        rss = subprocess.check_output(["ps", "-o", "rss=", "-p", str(processes[0].pid)], text=True)
                        peak["server_rss_kib"] = max(peak["server_rss_kib"], int(rss.strip()))
                    for directory, prefix, label in (
                        (root, ".phyzflow-transfer-", "staged_upload_bytes"),
                        (parent, ".phyzflow-download-", "staged_download_bytes"),
                    ):
                        for stage in directory.glob(prefix + "*"):
                            try:
                                peak[label] = max(peak[label], stage.stat().st_size)
                            except FileNotFoundError:
                                pass

            monitoring = Thread(target=sample)
            monitoring.start()
            try:
                for arguments in (["upload", str(source), "large.zip"], ["download", "large.zip", str(destination)]):
                    start = monotonic()
                    timer = ["/usr/bin/time", "-l"] if sys.platform == "darwin" else []
                    result = subprocess.run([*timer, *command, *arguments],
                                            env={**os.environ, "PHYZFLOW_MCP_TOKEN": token}, capture_output=True, text=True)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    print(f"{arguments[0]} elapsed: {monotonic() - start:.2f}s", flush=True)
                    if timer:
                        for line in result.stderr.splitlines():
                            if "maximum resident set size" in line:
                                print(line.strip(), flush=True)
            finally:
                stopped.set()
                monitoring.join()
            print(json.dumps(peak), flush=True)
            checksums = []
            for path in (source, root / "large.zip", destination):
                with path.open("rb") as stream:
                    checksums.append(file_digest(stream, "sha256").hexdigest())
            self.assertEqual(len(set(checksums)), 1)
            print(f"SHA-256: {checksums[0]}", flush=True)


class FileTransferCleanupTests(unittest.TestCase):
    def test_expiry_and_restart_remove_staged_uploads(self):
        with TemporaryDirectory() as temp:
            directory = Path(temp).resolve()
            root = directory / "project"
            root.mkdir()
            tokens = McpTokens(directory)
            principal = tokens.issue("test", ["allowed"])["id"]
            registry = SimpleNamespace(get=lambda _: root)
            access = WorkspaceAccess(tokens, registry, None)
            current = request_identity.set(principal)
            transfers = FileTransfers(access, directory / "staging")
            try:
                started = transfers.begin("allowed", "file", "upload", "missing", 1, sha256(b"a").hexdigest())
                identity = started["transfer_id"]
                stage = transfers.transfers[identity].stage
                with patch("workspace_sdk.application_mcp.file_transfers.monotonic", return_value=monotonic() + 901):
                    transfers.expire()
                self.assertFalse(stage.exists())
                self.assertFalse((transfers.directory / identity).exists())
                leftover = uuid4().hex
                path = f".phyzflow-transfer-{leftover}"
                (root / path).write_bytes(b"staged")
                (transfers.directory / leftover).write_text(json.dumps({
                    "workspace_id": "allowed", "staging_path": path,
                }))
                transfers.recover()
                self.assertFalse((root / path).exists())
                self.assertFalse((transfers.directory / leftover).exists())
            finally:
                transfers.close()
                request_identity.reset(current)
