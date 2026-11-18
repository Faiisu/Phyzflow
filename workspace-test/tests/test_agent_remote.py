from contextlib import ExitStack
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread, Event
import subprocess
import sys
import time
import unittest

from workspace_sdk.agent import AgentClient, create_agent_server
from workspace_sdk.agent_remote import RemoteClient, EdgeConnector, create_router_server


class RemoteAgentTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(TemporaryDirectory()))
        self.cert, self.key = self.root / 'cert.pem', self.root / 'key.pem'
        subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes',
                        '-keyout', str(self.key), '-out', str(self.cert), '-days', '1',
                        '-subj', '/CN=localhost', '-addext', 'subjectAltName=DNS:localhost'],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.config = {'cert_file': str(self.cert), 'key_file': str(self.key),
                       'devices': {}, 'clients': {}}
        for name in ('a', 'b'):
            self.config['devices'][name] = {'token_file': self.token('device-' + name)}
        self.config['clients']['alice'] = {'token_file': self.token('client-alice'), 'devices': ['a', 'b']}
        self.config['clients']['bob'] = {'token_file': self.token('client-bob'), 'devices': ['a']}
        self.router = self.stack.enter_context(create_router_server(self.config, port=0))
        self.serve(self.router)
        self.url = f'https://localhost:{self.router.server_port}'
        self.client = RemoteClient(self.url, 'client-alice-token-value', cafile=self.cert)

    def token(self, name):
        path = self.root / name
        path.write_text(name + '-token-value')
        path.chmod(0o600)
        return str(path)

    def serve(self, server):
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.stack.callback(thread.join)
        self.stack.callback(server.shutdown)

    def connect(self, name):
        project = self.root / ('project-' + name)
        project.mkdir()
        (project / 'flow.py').write_text(f'answer = "{name}"\n')
        local = self.stack.enter_context(create_agent_server({'token_file': self.token('local-' + name),
            'projects': {'demo': {'root': str(project), 'python': sys.executable}}}, port=0))
        self.serve(local)
        connector = EdgeConnector(self.url, name, 'device-' + name + '-token-value',
            AgentClient(f'http://127.0.0.1:{local.server_port}', 'local-' + name + '-token-value'), cafile=self.cert)
        stop = Event()
        thread = Thread(target=connector.run, args=(stop,), daemon=True)
        thread.start()
        self.stack.callback(thread.join, 5)
        self.stack.callback(stop.set)
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            devices = self.client.list_devices()['result']['devices']
            if any(d['device_id'] == name and d['status'] == 'connected' for d in devices):
                return stop
            time.sleep(.02)
        self.fail('Edge did not connect')

    def test_two_outbound_agents_route_distinct_project_contents(self):
        self.connect('a')
        self.connect('b')
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor() as pool:
            results = {name: pool.submit(self.client.request, 'same-id', name, 'demo', 'read_file', {'path': 'flow.py'})
                       for name in ('a', 'b')}
            for name, future in results.items():
                result = future.result()
                self.assertTrue(result['ok'], result)
                self.assertEqual(result['request_id'], 'same-id')
                self.assertEqual(result['result']['content'], f'answer = "{name}"\n')
                self.assertEqual(self.client.request('inspect', name, 'demo', 'inspect_project')['result']['sheets'],
                                 [{'name': 'Main', 'file': 'flow.py'}])
                self.assertEqual(self.client.request('files', name, 'demo', 'list_files')['result']['entries'],
                                 [{'path': 'flow.py', 'kind': 'file'}])
                self.assertEqual(self.client.request('search', name, 'demo', 'search_code', {'query': name})['result']['matches'],
                                 [{'path': 'flow.py', 'line': 1, 'text': f'answer = "{name}"'}])

    def test_client_and_device_authentication_and_local_project_permissions(self):
        self.connect('a')
        bob = RemoteClient(self.url, 'client-bob-token-value', cafile=self.cert)
        self.assertEqual(bob.list_devices()['result']['devices'], [{'device_id': 'a', 'status': 'connected'}])
        self.assertEqual(bob.request('denied', 'b', 'demo', 'inspect_project')['error']['code'], 'denied')
        bad = RemoteClient(self.url, 'bad-token', cafile=self.cert)
        self.assertEqual(bad.list_devices()['error']['code'], 'unauthorized')
        device = RemoteClient(self.url, 'device-a-token-value', cafile=self.cert)
        self.assertEqual(device.list_devices()['error']['code'], 'unauthorized')
        self.assertEqual(self.client.post('/edge/register', {'device_id': 'a'})['error']['code'], 'unauthorized')
        self.assertEqual(device.post('/edge/register', {'device_id': 'b'})['error']['code'], 'unauthorized')
        self.assertEqual(self.client.request('scope', 'a', 'production', 'inspect_project')['error']['code'], 'unknown_project')
        self.assertEqual(self.client.request('escape', 'a', 'demo', 'read_file', {'path': '../secret'})['error']['code'], 'denied')
        self.assertEqual(self.client.request('write', 'a', 'demo', 'write_file', {})['error']['code'], 'unsupported_operation')

    def test_tls_requires_trusted_certificate_and_matching_hostname(self):
        from urllib.error import URLError
        with self.assertRaises(ValueError):
            RemoteClient(self.url.replace('https:', 'http:'), 'token')
        with self.assertRaises(URLError):
            RemoteClient(self.url, 'client-alice-token-value').list_devices()
        with self.assertRaises(URLError):
            RemoteClient(self.url.replace('localhost', '127.0.0.1'), 'client-alice-token-value', cafile=self.cert).list_devices()

    def test_unavailable_and_disconnected_devices_return_explicit_errors(self):
        self.assertEqual(self.client.request('offline', 'b', 'demo', 'inspect_project')['error']['code'], 'unavailable')
        stop = self.connect('a')
        stop.set()
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            if self.client.list_devices()['result']['devices'][0]['status'] == 'unavailable':
                break
            time.sleep(.02)
        self.assertEqual(self.client.request('gone', 'a', 'demo', 'inspect_project')['error']['code'], 'unavailable')

    def test_replaced_session_cannot_poll_or_reply_for_new_device_connection(self):
        device = RemoteClient(self.url, 'device-a-token-value', cafile=self.cert)
        old = device.post('/edge/register', {'device_id': 'a'})['result']['session']
        new = device.post('/edge/register', {'device_id': 'a'})['result']['session']
        identity = {'device_id': 'a', 'session': old}
        self.assertEqual(device.post('/edge/poll', identity)['error']['code'], 'stale_session')
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor() as pool:
            result = pool.submit(self.client.request, 'original', 'a', 'demo', 'inspect_project')
            polled = device.post('/edge/poll', {'device_id': 'a', 'session': new})['result']['delivery']
            self.assertEqual(polled['request']['request_id'], 'original')
            reply = {**identity, 'delivery_id': polled['delivery_id'],
                     'response': {'request_id': 'original', 'ok': True, 'result': {'fake': True}}}
            self.assertEqual(device.post('/edge/reply', reply)['error']['code'], 'stale_session')
            reply['session'] = new
            reply['response']['request_id'] = 'another-request'
            self.assertEqual(device.post('/edge/reply', reply)['error']['code'], 'invalid_request')
            reply['response']['request_id'] = 'original'
            self.assertTrue(device.post('/edge/reply', reply)['ok'])
            self.assertTrue(result.result()['ok'])

    def test_expired_queued_reads_are_removed_and_delivered_timeout_is_uncertain(self):
        from concurrent.futures import ThreadPoolExecutor
        device = RemoteClient(self.url, 'device-a-token-value', cafile=self.cert)
        session = device.post('/edge/register', {'device_id': 'a'})['result']['session']
        identity = {'device_id': 'a', 'session': session}
        expired = self.client.request('not-fetched', 'a', 'demo', 'inspect_project')
        self.assertEqual(expired['error']['code'], 'unavailable')
        self.assertIsNone(device.post('/edge/poll', identity)['result']['delivery'])
        with ThreadPoolExecutor() as pool:
            result = pool.submit(self.client.request, 'fetched', 'a', 'demo', 'inspect_project')
            delivery = device.post('/edge/poll', identity)['result']['delivery']
            self.assertEqual(result.result()['error']['code'], 'delivery_unknown')
            self.assertEqual(device.post('/edge/reply', {**identity, 'delivery_id': delivery['delivery_id'],
                'response': {'request_id': 'fetched', 'ok': True, 'result': {}}})['error']['code'], 'expired_delivery')

    def test_central_and_connector_module_launchers_use_private_configuration(self):
        import json
        import select
        config = self.root / 'central.json'
        config.write_text(json.dumps(self.config))
        central = subprocess.Popen([sys.executable, '-m', 'workspace_sdk.agent_remote', 'central', '--config', str(config)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.stack.callback(self.stop_process, central)
        self.assertTrue(select.select([central.stdout], [], [], 5)[0], 'Central did not announce readiness')
        url = central.stdout.readline().strip().removeprefix('Workspace central: ').replace('127.0.0.1', 'localhost')
        client = RemoteClient(url, 'client-alice-token-value', cafile=self.cert)
        project = self.root / 'cli-project'
        project.mkdir()
        (project / 'flow.py').write_text('cli_answer = 42\n')
        token_file = self.token('cli-local')
        local = self.stack.enter_context(create_agent_server({'token_file': token_file,
            'projects': {'demo': {'root': str(project), 'python': sys.executable}}}, port=0))
        self.serve(local)
        connector_config = self.root / 'connector.json'
        connector_config.write_text(json.dumps({'central_url': url, 'device_id': 'a',
            'device_token_file': self.config['devices']['a']['token_file'], 'ca_file': str(self.cert),
            'local_url': f'http://127.0.0.1:{local.server_port}', 'local_token_file': token_file}))
        connector = subprocess.Popen([sys.executable, '-m', 'workspace_sdk.agent_remote', 'connect', '--config', str(connector_config)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.stack.callback(self.stop_process, connector)
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            if client.list_devices()['result']['devices'][0]['status'] == 'connected':
                break
            time.sleep(.02)
        result = client.request('cli-read', 'a', 'demo', 'read_file', {'path': 'flow.py'})
        self.assertEqual(result['result']['content'], 'cli_answer = 42\n')
        self.stop_process(connector)
        self.assertEqual(connector.returncode, 0)
        self.assertEqual(client.list_devices()['result']['devices'][0]['status'], 'unavailable')

    @staticmethod
    def stop_process(process):
        if process.poll() is None:
            process.terminate()
        process.communicate(timeout=15)
