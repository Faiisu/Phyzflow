"""Start Workspace UI alongside the local Edge Agent, TLS router, and MCP setup."""

import argparse
from contextlib import ExitStack
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import signal
import socket
import subprocess
import sys
from threading import Event, Thread
import time

from workspace_sdk.sheets import discover_flows
from workspace_sdk.ui_settings import (
    load_ui_settings,
    prompt_ui_settings,
    validate_ipv4,
    validate_port,
)
from workspace_sdk.workspace_ui import serve_workspace


def ask_value(label, default, parse):
    """Prompt interactively on stderr with fallback to default on empty input or EOF."""
    while True:
        print(f"{label} [{default}]: ", end="", file=sys.stderr, flush=True)
        try:
            value = input().strip()
        except EOFError:
            print(file=sys.stderr)
            return default
        if not value:
            return default
        try:
            return parse(value)
        except ValueError as error:
            print(error, file=sys.stderr)


def resolve_runtime_dir(repo_dir: Path, project_dir: Path) -> Path:
    """Place runtime state and credentials outside the exposed Workspace project."""
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", project_dir.name).strip("-") or "workspace"
    candidate = repo_dir / ".workspace" / "mcp" / slug
    resolved_project = project_dir.resolve()
    resolved_candidate = candidate.resolve(strict=False)
    if resolved_candidate == resolved_project or resolved_candidate.is_relative_to(resolved_project):
        candidate = project_dir.parent / f".{slug}-mcp"
    return candidate


def detect_advertise_ipv4(bind_host: str) -> str:
    """Choose a connectable IPv4 address for TLS certificates and mcp.json."""
    if bind_host != "0.0.0.0":
        return bind_host
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"


def ensure_private_token(path: Path) -> str:
    """Create or reuse a 0600 private bearer token file."""
    if path.is_symlink():
        raise ValueError(f"Token path must not be a symbolic link: {path}")
    if not path.exists():
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            os.write(descriptor, (secrets.token_urlsafe(32) + "\n").encode("utf-8"))
        finally:
            os.close(descriptor)
    os.chmod(path, 0o600)
    return path.read_text(encoding="utf-8").strip()


def write_private_json(path: Path, payload: dict) -> None:
    """Write a JSON configuration file with private 0600 permissions."""
    if path.is_symlink():
        raise ValueError(f"Configuration path must not be a symbolic link: {path}")
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(descriptor, (json.dumps(payload, indent=2) + "\n").encode("utf-8"))
    finally:
        os.close(descriptor)
    os.chmod(path, 0o600)


def find_openssl() -> str:
    """Locate the openssl executable across POSIX and Windows Git installs."""
    found = shutil.which("openssl")
    if found:
        return found
    if os.name != "posix":
        candidates = [
            Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Git" / "usr" / "bin" / "openssl.exe",
            Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Git" / "mingw64" / "bin" / "openssl.exe",
            Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Git" / "usr" / "bin" / "openssl.exe",
            Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Git" / "mingw64" / "bin" / "openssl.exe",
            Path(os.environ.get("LocalAppData", "")) / "Programs" / "Git" / "usr" / "bin" / "openssl.exe",
            Path(os.environ.get("LocalAppData", "")) / "Programs" / "Git" / "mingw64" / "bin" / "openssl.exe",
        ]
        for candidate in candidates:
            if candidate.is_file():
                return str(candidate)
    raise RuntimeError(
        "Could not find 'openssl' on PATH"
        + (" or in Git for Windows." if os.name != "posix" else ".")
    )


def generate_tls_cert(cert_file: Path, key_file: Path, advertise_host: str) -> None:
    """Generate a localhost/IPv4 self-signed TLS certificate for the central router."""
    san_entries = ["DNS:localhost", "IP:127.0.0.1"]
    if advertise_host != "127.0.0.1":
        san_entries.append(f"IP:{advertise_host}")
    env = dict(os.environ)
    if os.name != "posix":
        # Prevent Git/MSYS2 openssl from rewriting '/CN=localhost' as a filesystem path.
        env["MSYS_NO_PATHCONV"] = "1"
        env["MSYS2_ARG_CONV_EXCL"] = "*"
    result = subprocess.run(
        [
            find_openssl(),
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-keyout",
            str(key_file),
            "-out",
            str(cert_file),
            "-days",
            "30",
            "-subj",
            "/CN=localhost",
            "-addext",
            "subjectAltName=" + ",".join(san_entries),
        ],
        capture_output=True,
        text=True,
        env=env,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or "openssl failed."
        raise RuntimeError(f"Could not generate TLS certificate: {detail}")
    os.chmod(key_file, 0o600)
    os.chmod(cert_file, 0o600)


def print_mcp_summary(
    *,
    device_id: str,
    project_id: str,
    central_url: str,
    mcp_config_path: Path,
    client_snippet: dict,
) -> None:
    """Print connection details and a ready-to-copy mcpServers configuration block."""
    print("=== Workspace MCP Ready ===", flush=True)
    print(f"Device ID  : {device_id}", flush=True)
    print(f"Project ID : {project_id}", flush=True)
    print(f"Central URL: {central_url}", flush=True)
    print(f"MCP Config : {mcp_config_path}", flush=True)
    print("MCP Client Config (mcpServers):", flush=True)
    print(json.dumps(client_snippet, indent=2), flush=True)
    print("===========================", flush=True)


def run_mcp_stack(args, repo_dir: Path, project_dir: Path, ui_settings) -> int:
    """Start the local Edge agent, central TLS router, connector, and Workspace UI."""
    from workspace_sdk.agent import AgentClient, create_agent_server
    from workspace_sdk.agent_mcp import create_mcp_server
    from workspace_sdk.agent_remote import (
        EdgeConnector,
        RemoteClient,
        create_router_server,
    )

    central_host = validate_ipv4(args.central_host, "Central MCP Router IP")
    central_port = validate_port(args.central_port, "Central MCP Router port")
    if not args.no_prompt:
        central_host = ask_value("Central MCP Router IP (IPv4)", central_host, validate_ipv4)
        central_port = ask_value(
            "Central MCP Router Port (0 for an available port)",
            central_port,
            lambda value: validate_port(int(value), "Central MCP Router port"),
        )

    runtime_dir = resolve_runtime_dir(repo_dir, project_dir)
    runtime_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(runtime_dir, 0o700)

    local_token_file = runtime_dir / "local-token"
    device_token_file = runtime_dir / "device-token"
    client_token_file = runtime_dir / "client-token"
    local_token = ensure_private_token(local_token_file)
    device_token = ensure_private_token(device_token_file)
    client_token = ensure_private_token(client_token_file)

    local_connect_host = "127.0.0.1" if central_host == "0.0.0.0" else central_host
    advertise_host = detect_advertise_ipv4(central_host)
    cert_file = runtime_dir / "cert.pem"
    key_file = runtime_dir / "key.pem"
    generate_tls_cert(cert_file, key_file, advertise_host)

    device_id = "edge-local"
    project_id = "development"
    state_dir = runtime_dir / "state"
    agent_config = {
        "token_file": str(local_token_file),
        "state_dir": str(state_dir),
        "projects": {
            project_id: {
                "root": str(project_dir),
                "python": sys.executable,
                "commands": ["python"],
            }
        },
    }
    central_config = {
        "cert_file": str(cert_file),
        "key_file": str(key_file),
        "devices": {device_id: {"token_file": str(device_token_file)}},
        "clients": {"developer": {"token_file": str(client_token_file), "devices": [device_id]}},
    }

    stop = Event()
    with ExitStack() as stack:
        agent = stack.enter_context(create_agent_server(agent_config, port=0))
        central = stack.enter_context(
            create_router_server(central_config, port=central_port, host=central_host)
        )
        for server in (agent, central):
            thread = Thread(target=server.serve_forever, daemon=True)
            thread.start()
            stack.callback(thread.join)
            stack.callback(server.shutdown)

        local_url = f"http://127.0.0.1:{agent.server_port}"
        connector_central_url = f"https://{local_connect_host}:{central.server_port}"
        client_central_url = f"https://{advertise_host}:{central.server_port}"

        connector_config = {
            "central_url": connector_central_url,
            "device_id": device_id,
            "device_token_file": str(device_token_file),
            "local_url": local_url,
            "local_token_file": str(local_token_file),
            "ca_file": str(cert_file),
        }
        mcp_config = {
            "central_url": client_central_url,
            "client_token_file": str(client_token_file),
            "ca_file": str(cert_file),
        }
        mcp_config_path = runtime_dir / "mcp.json"
        client_snippet = {
            "mcpServers": {
                "workspace-edge": {
                    "command": sys.executable,
                    "args": ["-m", "workspace_sdk.agent_mcp", "--config", str(mcp_config_path)],
                }
            }
        }
        write_private_json(runtime_dir / "agent.json", agent_config)
        write_private_json(runtime_dir / "central.json", central_config)
        write_private_json(runtime_dir / "connector.json", connector_config)
        write_private_json(mcp_config_path, mcp_config)
        write_private_json(runtime_dir / "mcp-client-config.json", client_snippet)

        connector = EdgeConnector(
            connector_central_url,
            device_id,
            device_token,
            AgentClient(local_url, local_token),
            cafile=str(cert_file),
        )
        connector_thread = Thread(target=connector.run, args=(stop,), daemon=True)
        connector_thread.start()
        stack.callback(connector_thread.join, 5)
        stack.callback(stop.set)

        client = RemoteClient(client_central_url, client_token, cafile=str(cert_file))
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            devices = client.list_devices().get("result", {}).get("devices", [])
            if any(item.get("device_id") == device_id and item.get("status") == "connected" for item in devices):
                break
            if not connector_thread.is_alive():
                raise RuntimeError("Edge connector exited before connecting to the central router.")
            time.sleep(0.05)
        else:
            raise RuntimeError("Timed out waiting for the local Edge connector to register.")

        print_mcp_summary(
            device_id=device_id,
            project_id=project_id,
            central_url=client_central_url,
            mcp_config_path=mcp_config_path,
            client_snippet=client_snippet,
        )

        if args.check_only:
            server = create_mcp_server(client)
            inspection = client.request("startup-check", device_id, project_id, "inspect_project")
            if server is None or not inspection.get("ok"):
                raise RuntimeError(f"MCP startup check failed: {inspection}")
            print("Startup check passed: edge-local connected and inspect_project succeeded.", flush=True)
            return 0

        def handle_sigterm(*_):
            raise KeyboardInterrupt

        previous_sigterm = signal.signal(signal.SIGTERM, handle_sigterm)
        try:
            os.chdir(project_dir)
            serve_workspace(project_dir, discover_flows(project_dir), ui_settings)
        finally:
            signal.signal(signal.SIGTERM, previous_sigterm)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, required=True, help="Workspace project directory")
    parser.add_argument("--ui-host", help="Override the UI IPv4 bind address")
    parser.add_argument("--ui-port", type=int, help="Override the UI bind port")
    parser.add_argument("--central-host", default="0.0.0.0", help="Central MCP router IPv4 bind address")
    parser.add_argument("--central-port", type=int, default=18443, help="Central MCP router bind port")
    parser.add_argument("--no-prompt", action="store_true", help="Use defaults without interactive prompts")
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="Start services, verify MCP readiness, print summary, and exit",
    )
    args = parser.parse_args()

    repo_dir = Path(__file__).resolve().parent.parent
    project_dir = args.project.expanduser().resolve()
    if not project_dir.is_dir():
        print(f"Workspace directory does not exist: {project_dir}", file=sys.stderr)
        return 1

    try:
        ui_settings = load_ui_settings(project_dir, args.ui_port, ipv4_override=args.ui_host)
        if not args.no_prompt:
            ui_settings = prompt_ui_settings(ui_settings)
        return run_mcp_stack(args, repo_dir, project_dir, ui_settings)
    except KeyboardInterrupt:
        print(file=sys.stderr)
        return 0
    except (OSError, RuntimeError, ValueError) as error:
        print(f"Workspace MCP startup failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
