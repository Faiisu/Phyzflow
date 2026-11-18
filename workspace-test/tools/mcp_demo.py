"""Prepare a disposable demo and serve TLS central, connector, and Edge until Ctrl+C.

Run with an installed workspace-sdk[mcp] interpreter. Never reuses or prints tokens.
"""
import argparse
from contextlib import ExitStack
import json
from pathlib import Path
import secrets
import signal
import subprocess
import sys
from threading import Event, Thread
import time
from zipfile import ZipFile

from workspace_sdk.agent import AgentClient, create_agent_server
from workspace_sdk.agent_remote import EdgeConnector, RemoteClient, create_router_server


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True, type=Path)
    args = parser.parse_args()
    root = args.root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    root.chmod(0o700)
    project = root / 'project'
    if project.exists():
        parser.error('Use a new root: existing demo project will not be overwritten.')
    project.mkdir()
    (project / 'nested').mkdir()
    (project / 'flow.py').write_text('from workspace_sdk import Flow, Output, Step\nfrom nested.worker import work\nflow = Flow((), (Step("Work", work, {}),), Output("Work"))\n')
    (project / 'nested' / '__init__.py').write_text('')
    (project / 'nested' / 'worker.py').write_text('def work():\n    raise RuntimeError("intentional nested failure")\n')
    (project / 'test_project.py').write_text('import unittest\nfrom nested.worker import work\nfrom edge_demo_bonus import bonus\nclass ProjectTests(unittest.TestCase):\n    def test_project_dependency_and_flow(self):\n        self.assertEqual(work() + bonus(), 43)\n')
    wheel = project / 'edge_demo_bonus-1.0-py3-none-any.whl'
    with ZipFile(wheel, 'w') as archive:
        for name, content in {
            'edge_demo_bonus.py': 'def bonus(): return 1\n',
            'edge_demo_bonus-1.0.dist-info/METADATA': 'Metadata-Version: 2.1\nName: edge-demo-bonus\nVersion: 1.0\n',
            'edge_demo_bonus-1.0.dist-info/WHEEL': 'Wheel-Version: 1.0\nGenerator: workspace-demo\nRoot-Is-Purelib: true\nTag: py3-none-any\n',
            'edge_demo_bonus-1.0.dist-info/RECORD': '',
        }.items():
            archive.writestr(name, content)
    def token(name):
        path = root / name
        path.write_text(secrets.token_urlsafe(32))
        path.chmod(0o600)
        return path
    local_token, device_token, client_token = [token(name) for name in ('local-token', 'device-token', 'client-token')]
    cert, key = root / 'cert.pem', root / 'key.pem'
    subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes',
                    '-keyout', str(key), '-out', str(cert), '-days', '2', '-subj', '/CN=localhost',
                    '-addext', 'subjectAltName=DNS:localhost'], check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    stop = Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    with ExitStack() as stack:
        agent = stack.enter_context(create_agent_server({'token_file': str(local_token), 'state_dir': str(root / 'state'),
            'projects': {'demo': {'root': str(project), 'python': sys.executable, 'commands': ['python']}}}, port=0))
        central = stack.enter_context(create_router_server({'cert_file': str(cert), 'key_file': str(key),
            'devices': {'edge-demo': {'token_file': str(device_token)}},
            'clients': {'developer': {'token_file': str(client_token), 'devices': ['edge-demo']}}}, port=0))
        for server in (agent, central):
            thread = Thread(target=server.serve_forever, daemon=True)
            thread.start()
            stack.callback(thread.join)
            stack.callback(server.shutdown)
        url = f'https://localhost:{central.server_port}'
        connector = EdgeConnector(url, 'edge-demo', device_token.read_text(),
            AgentClient(f'http://127.0.0.1:{agent.server_port}', local_token.read_text()), cafile=cert)
        thread = Thread(target=connector.run, args=(stop,), daemon=True)
        thread.start()
        stack.callback(thread.join, 5)
        stack.callback(stop.set)
        config = root / 'mcp.json'
        config.write_text(json.dumps({'central_url': url, 'client_token_file': str(client_token), 'ca_file': str(cert)}))
        config.chmod(0o600)
        client = RemoteClient(url, client_token.read_text(), cafile=cert)
        while not stop.is_set():
            if client.list_devices()['result']['devices'][0]['status'] == 'connected':
                break
            time.sleep(.05)
        print(json.dumps({'config': str(config), 'project': str(project)}), flush=True)
        stop.wait()


if __name__ == '__main__':
    main()
