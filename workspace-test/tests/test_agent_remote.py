import subprocess
import sys
import time
import unittest
from contextlib import ExitStack
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event, Thread

from workspace_sdk.agent import AgentClient, create_agent_server
from workspace_sdk.agent_remote import EdgeConnector, RemoteClient, create_router_server


class RemoteAgentTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(TemporaryDirectory()))
        self.locals = {}
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

    def connect(self, name, response_gate=None):
        project = self.root / ('project-' + name)
        project.mkdir()
        (project / 'flow.py').write_text(f'answer = "{name}"\n')
        other = self.root / ('other-' + name)
        other.mkdir()
        local = self.stack.enter_context(create_agent_server({'token_file': self.token('local-' + name),
            'projects': {key: {'root': str(root), 'python': sys.executable, 'commands': ['python']}
                         for key, root in (('demo', project), ('other', other))}}, port=0))
        self.serve(local)
        self.locals[name] = AgentClient(f'http://127.0.0.1:{local.server_port}', 'local-' + name + '-token-value')
        if response_gate is not None:
            # Delay acknowledgements at an actual HTTP boundary, leaving Edge execution real.
            from http.client import HTTPConnection
            from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
            port = local.server_port
            class DelayedResponse(BaseHTTPRequestHandler):
                def log_message(self, *_):
                    pass

                def do_POST(self):
                    body = self.rfile.read(int(self.headers['Content-Length']))
                    upstream = HTTPConnection('127.0.0.1', port, timeout=10)
                    try:
                        upstream.request('POST', self.path, body, dict(self.headers))
                        response = upstream.getresponse()
                        payload = response.read()
                        response_gate.wait(8)
                        self.send_response(response.status)
                        self.send_header('Content-Length', str(len(payload)))
                        self.end_headers()
                        self.wfile.write(payload)
                    finally:
                        upstream.close()
            proxy = self.stack.enter_context(ThreadingHTTPServer(('127.0.0.1', 0), DelayedResponse))
            self.serve(proxy)
            self.stack.callback(response_gate.set)
            self.locals[name] = AgentClient(f'http://127.0.0.1:{proxy.server_port}', 'local-' + name + '-token-value')
        return self.start_connector(name)

    def start_connector(self, name):
        connector = EdgeConnector(self.url, name, 'device-' + name + '-token-value',
            self.locals[name], cafile=self.cert)
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

    def wait_job(self, job_id, device='a', project='demo'):
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            response = self.client.request('poll', device, project, 'get_job', {'job_id': job_id})
            self.assertTrue(response['ok'], response)
            if response['result']['status'] not in ('queued', 'running'):
                return response['result']
            time.sleep(.03)
        self.fail('Remote job did not finish')

    def test_remote_edit_validate_run_and_failures_use_edge_jobs(self):
        self.connect('a')
        before = self.client.request('read', 'a', 'demo', 'read_file', {'path': 'flow.py'})['result']
        source = 'from workspace_sdk import Flow, Output, Step\ndef work(): return 42\nflow=Flow((),(Step("Work",work,{}),),Output("Work"))\n'
        edit = self.client.request('edit', 'a', 'demo', 'write_file',
            {'path': 'flow.py', 'revision': before['revision'], 'source': source})
        self.assertTrue(edit['ok'], edit)
        stale = self.client.request('stale', 'a', 'demo', 'apply_patch',
            {'path': 'flow.py', 'revision': before['revision'], 'old_text': '42', 'new_text': '43'})
        self.assertEqual(stale['error']['code'], 'conflict')
        for operation in ('validate_flow', 'run_flow'):
            started = self.client.request(operation, 'a', 'demo', operation)
            self.assertTrue(started['ok'], started)
            job = self.wait_job(started['result']['job_id'])
            self.assertEqual(job['status'], 'succeeded', job)
            if operation == 'run_flow':
                self.assertEqual(job['result']['output'], 42)
        patched = self.client.request('break', 'a', 'demo', 'apply_patch',
            {'path': 'flow.py', 'revision': edit['result']['revision'],
             'old_text': 'return 42', 'new_text': 'raise RuntimeError("failure")'})
        self.assertTrue(patched['ok'], patched)
        failed = self.client.request('failed-run', 'a', 'demo', 'run_flow')
        self.assertEqual(self.wait_job(failed['result']['job_id'])['status'], 'failed')
        broken = self.client.request('syntax', 'a', 'demo', 'write_file',
            {'path': 'flow.py', 'revision': patched['result']['revision'], 'source': 'def broken(\n'})
        self.assertTrue(broken['ok'], broken)
        invalid = self.client.request('invalid', 'a', 'demo', 'validate_flow')
        diagnostic = self.wait_job(invalid['result']['job_id'])
        self.assertEqual(diagnostic['status'], 'failed')
        self.assertEqual(diagnostic['error']['stage'], 'validation')

    def test_central_restart_reconnects_to_original_live_job_and_output(self):
        self.connect('a')
        started = self.client.request('long', 'a', 'demo', 'run_command',
            {'program': 'python', 'argv': ['-u', '-c',
             'import time; print("still-edge-owned", flush=True); time.sleep(60)']})
        self.assertTrue(started['ok'], started)
        job_id = started['result']['job_id']
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            output = self.client.request('output', 'a', 'demo', 'read_job_output', {'job_id': job_id})
            if 'still-edge-owned' in output['result']['content']:
                break
            time.sleep(.03)
        self.assertIn('still-edge-owned', output['result']['content'])
        port = self.router.server_port
        self.router.shutdown()
        self.router.server_close()
        # The connector must remain alive, retrying without a replacement process.
        time.sleep(.7)
        replacement = self.stack.enter_context(create_router_server(self.config, port=port))
        self.serve(replacement)
        deadline = time.monotonic() + 6
        while time.monotonic() < deadline:
            if self.client.list_devices()['result']['devices'][0]['status'] == 'connected':
                break
            time.sleep(.03)
        live = self.client.request('original-job', 'a', 'demo', 'get_job', {'job_id': job_id})
        self.assertTrue(live['ok'], live)
        self.assertEqual(live['result']['status'], 'running')
        output = self.client.request('output-again', 'a', 'demo', 'read_job_output', {'job_id': job_id})
        self.assertIn('still-edge-owned', output['result']['content'])
        cancelled = self.client.request('cancel-after-restart', 'a', 'demo', 'cancel_job', {'job_id': job_id})
        self.assertTrue(cancelled['ok'], cancelled)
        self.assertEqual(self.wait_job(job_id)['status'], 'cancelled')

    def test_slow_local_ack_keeps_device_live_and_retry_preserves_effect_identity(self):
        from concurrent.futures import ThreadPoolExecutor
        gate = Event()
        self.connect('a', response_gate=gate)
        arguments = {'path': 'created.py', 'revision': 'missing', 'source': 'value = 42\n'}
        with ThreadPoolExecutor() as pool:
            attempt = pool.submit(self.client.request, 'lost-edit', 'a', 'demo', 'write_file', arguments)
            uncertain = attempt.result()
            self.assertEqual(uncertain['error']['code'], 'delivery_unknown')
            time.sleep(.4)
            self.assertEqual(self.client.list_devices()['result']['devices'][0]['status'], 'connected')
            gate.set()
        retry = self.client.request('lost-edit', 'a', 'demo', 'write_file', arguments)
        self.assertTrue(retry['ok'], retry)
        self.assertEqual(self.client.request('lost-edit', 'a', 'demo', 'write_file', arguments), retry)
        changed = self.client.request('lost-edit', 'a', 'demo', 'write_file', {**arguments, 'source': 'changed\n'})
        self.assertEqual(changed['error']['code'], 'request_conflict')

    def test_lost_command_ack_retry_starts_once_and_ids_are_device_project_scoped(self):
        gate = Event()
        self.connect('a', response_gate=gate)
        self.connect('b')
        arguments = {'program': 'python', 'argv': ['-c', (
            'from pathlib import Path; p=Path("executions.txt"); '
            'p.open("a").write("started\\n"); print("one process")')]}
        uncertain = self.client.request('same-mutation', 'a', 'demo', 'run_command', arguments)
        self.assertEqual(uncertain['error']['code'], 'delivery_unknown')
        gate.set()
        accepted = self.client.request('same-mutation', 'a', 'demo', 'run_command', arguments)
        self.assertTrue(accepted['ok'], accepted)
        self.assertEqual(self.client.request('same-mutation', 'a', 'demo', 'run_command', arguments), accepted)
        original = accepted['result']['job_id']
        self.assertEqual(self.wait_job(original)['status'], 'succeeded')
        output = self.client.request('out', 'a', 'demo', 'read_job_output', {'job_id': original})
        self.assertEqual(output['result']['content'], 'one process\n')
        for device, project in (('a', 'other'), ('b', 'demo')):
            independent = self.client.request('same-mutation', device, project, 'run_command', arguments)
            self.assertTrue(independent['ok'], independent)
            self.assertNotEqual(independent['result']['job_id'], original)
            self.assertEqual(self.wait_job(independent['result']['job_id'], device, project)['status'], 'succeeded')
            denied = self.client.request('foreign', device, project, 'get_job', {'job_id': original})
            self.assertFalse(denied['ok'], denied)
        for device, project in (('a', 'demo'), ('a', 'other'), ('b', 'demo')):
            count = self.client.request('count', device, project, 'read_file', {'path': 'executions.txt'})
            self.assertEqual(count['result']['content'], 'started\n')
        denied = self.client.request('foreign-output', 'a', 'other', 'read_job_output', {'job_id': original})
        self.assertFalse(denied['ok'], denied)

    def test_disconnected_cancel_is_not_queued_and_explicit_retry_cancels_original(self):
        stop = self.connect('a')
        started = self.client.request('long', 'a', 'demo', 'run_command',
            {'program': 'python', 'argv': ['-c', 'import time; time.sleep(60)']})
        job_id = started['result']['job_id']
        stop.set()
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            if self.client.list_devices()['result']['devices'][0]['status'] == 'unavailable':
                break
            time.sleep(.03)
        arguments = {'job_id': job_id}
        offline = self.client.request('cancel-original', 'a', 'demo', 'cancel_job', arguments)
        self.assertEqual(offline['error']['code'], 'unavailable')
        self.start_connector('a')
        live = self.client.request('original', 'a', 'demo', 'get_job', arguments)
        self.assertEqual(live['result']['status'], 'running')
        result = self.client.request('cancel-original', 'a', 'demo', 'cancel_job', arguments)
        self.assertTrue(result['ok'], result)
        self.assertEqual(self.wait_job(job_id)['status'], 'cancelled')

    def test_replaced_connector_exits_without_reclaiming_new_session(self):
        from concurrent.futures import ThreadPoolExecutor
        stop = self.connect('a')
        stop.set()
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            if self.client.list_devices()['result']['devices'][0]['status'] == 'unavailable':
                break
            time.sleep(.03)
        connector = EdgeConnector(self.url, 'a', 'device-a-token-value', self.locals['a'], cafile=self.cert)
        stop_new = Event()
        with ThreadPoolExecutor() as pool:
            future = pool.submit(connector.run, stop_new)
            try:
                deadline = time.monotonic() + 4
                while time.monotonic() < deadline:
                    if self.client.list_devices()['result']['devices'][0]['status'] == 'connected':
                        break
                    time.sleep(.03)
                device = RemoteClient(self.url, 'device-a-token-value', cafile=self.cert)
                session = device.post('/edge/register', {'device_id': 'a'})['result']['session']
                with self.assertRaisesRegex(ValueError, 'session rejected'):
                    future.result(timeout=4)
                # The old run and its final disconnect cannot invalidate this successor.
                self.assertTrue(device.post('/edge/heartbeat', {'device_id': 'a', 'session': session})['ok'])
            finally:
                stop_new.set()

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
        self.assertEqual(self.client.request('write', 'a', 'demo', 'arbitrary_shell', {})['error']['code'], 'unsupported_operation')

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

    def test_expired_queued_mutations_are_removed_and_delivered_timeout_is_uncertain(self):
        from concurrent.futures import ThreadPoolExecutor
        device = RemoteClient(self.url, 'device-a-token-value', cafile=self.cert)
        session = device.post('/edge/register', {'device_id': 'a'})['result']['session']
        identity = {'device_id': 'a', 'session': session}
        expired = self.client.request('not-fetched', 'a', 'demo', 'write_file',
            {'path': 'never.py', 'revision': 'missing', 'source': 'not delivered\n'})
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
            'projects': {'demo': {'root': str(project), 'python': sys.executable, 'commands': ['python']}}}, port=0))
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
