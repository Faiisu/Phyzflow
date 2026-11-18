"""Durable operations through the installed agent process and HTTP client."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import json
from http.client import HTTPException
from urllib.error import URLError
from pathlib import Path
import socket
import subprocess
import sys
from tempfile import TemporaryDirectory
from time import monotonic, sleep
import unittest

from workspace_sdk.agent import AgentClient
from test_agent_jobs import wait_job


@contextmanager
def agent_process(root, state, port=0):
    token = root.parent / 'token'
    token.write_text('persistent-development-token')
    token.chmod(0o600)
    config = root.parent / 'agent.json'
    config.write_text(json.dumps({'token_file': str(token), 'state_dir': str(state), 'projects': {
        'demo': {'root': str(root), 'python': sys.executable, 'commands': ['python']}}}))
    process = subprocess.Popen([sys.executable, '-m', 'workspace_sdk.agent', '--config', str(config), '--port', str(port)],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        line = process.stdout.readline()
        if not line.startswith('Workspace agent: '):
            raise AssertionError(process.stderr.read())
        yield AgentClient(line.strip().split(' ')[-1], token.read_text()), process
    finally:
        if process.poll() is None:
            process.terminate()
        try:
            process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        process.stdout.close()
        process.stderr.close()


class AgentPersistenceTests(unittest.TestCase):
    def test_duplicate_command_and_patch_survive_restart(self):
        with TemporaryDirectory() as temp:
            root = Path(temp) / 'project'
            root.mkdir()
            state = Path(temp) / 'state'
            arguments = {'program': 'python', 'argv': ['-c', 'print("durable output")']}
            with agent_process(root, state) as (client, _):
                original = client.request('command-1', 'demo', 'run_command', arguments)
                completed = wait_job(client, original['result']['job_id'])
                self.assertEqual(completed['status'], 'succeeded', completed)
                self.assertEqual(client.request('command-1', 'demo', 'run_command', arguments), original)
                edited = client.request('edit-1', 'demo', 'write_file', {'path': 'hello.py', 'revision': 'missing', 'source': 'hello = 1\n'})
                self.assertTrue(edited['ok'], edited)
            with agent_process(root, state) as (client, _):
                self.assertEqual(client.request('command-1', 'demo', 'run_command', arguments), original)
                self.assertEqual(client.request('read', 'demo', 'get_job', {'job_id': completed['job_id']})['result'], completed)
                self.assertEqual(client.request('output', 'demo', 'read_job_output', {'job_id': completed['job_id']})['result']['content'], 'durable output\n')
                self.assertEqual(client.request('edit-1', 'demo', 'write_file', {'path': 'hello.py', 'revision': 'missing', 'source': 'hello = 1\n'}), edited)
                conflict = client.request('command-1', 'demo', 'run_command', {'program': 'python', 'argv': ['-c', 'print("different")']})
                self.assertEqual(conflict['error']['code'], 'request_conflict')

    def test_lost_acknowledgement_and_concurrent_retry_launch_once(self):
        with TemporaryDirectory() as temp:
            root = Path(temp) / 'project'
            root.mkdir()
            arguments = {'program': 'python', 'argv': ['-c', 'import time; open("launches.txt","a").write("once\\n"); print("first",flush=True); time.sleep(.5); print("second")']}
            with agent_process(root, Path(temp) / 'state') as (client, _):
                from urllib.parse import urlsplit
                address = urlsplit(client.url)
                payload = json.dumps({'request_id': 'lost-start', 'project_id': 'demo', 'operation': 'run_command', 'arguments': arguments}).encode()
                connection = socket.create_connection((address.hostname, address.port))
                connection.sendall(('POST /request HTTP/1.0\r\nAuthorization: Bearer persistent-development-token\r\nContent-Type: application/json\r\nContent-Length: ' + str(len(payload)) + '\r\n\r\n').encode() + payload)
                connection.shutdown(socket.SHUT_WR)
                connection.close()  # Drop the actual acknowledgement before reading any bytes.
                with ThreadPoolExecutor(max_workers=6) as pool:
                    responses = list(pool.map(lambda _: client.request('lost-start', 'demo', 'run_command', arguments), range(6)))
                self.assertTrue(responses[0]['ok'], responses)
                self.assertTrue(all(response == responses[0] for response in responses))
                job_id = responses[0]['result']['job_id']
                fresh = AgentClient(client.url, 'persistent-development-token')
                deadline = monotonic() + 5
                cursor = 0
                text = ''
                while monotonic() < deadline:
                    page = fresh.request('output', 'demo', 'read_job_output', {'job_id': job_id, 'cursor': cursor})['result']
                    text += page['content']
                    cursor = page['next_cursor']
                    if page['complete']:
                        break
                    sleep(.02)
                self.assertEqual(text, 'first\nsecond\n')
                self.assertEqual(fresh.request('launch-count', 'demo', 'read_file', {'path': 'launches.txt'})['result']['content'], 'once\n')
                self.assertEqual(wait_job(fresh, job_id)['status'], 'succeeded')
                write = fresh.request('write', 'demo', 'write_file', {'path': 'value.py', 'revision': 'missing', 'source': 'value = 1\n'})
                patch = {'path': 'value.py', 'revision': write['result']['revision'], 'old_text': '1', 'new_text': '11'}
                first = fresh.request('patch', 'demo', 'apply_patch', patch)
                self.assertEqual(fresh.request('patch', 'demo', 'apply_patch', patch), first)
                self.assertEqual(fresh.request('read', 'demo', 'read_file', {'path': 'value.py'})['result']['content'], 'value = 11\n')

    def test_sigkill_cleans_stubborn_child_before_restart_and_marks_interrupted(self):
        with TemporaryDirectory() as temp:
            root = Path(temp) / 'project'
            root.mkdir()
            state = Path(temp) / 'state'
            child = 'import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); time.sleep(2); open("orphan.txt","w").write("orphan")'
            parent = 'import subprocess,sys,time; subprocess.Popen([sys.executable,"-c",' + repr(child) + ']); print("started",flush=True); time.sleep(60)'
            arguments = {'program': 'python', 'argv': ['-c', parent]}
            with agent_process(root, state) as (client, process):
                original = client.request('start', 'demo', 'run_command', arguments)
                job_id = original['result']['job_id']
                deadline = monotonic() + 5
                while monotonic() < deadline:
                    if client.request('output', 'demo', 'read_job_output', {'job_id': job_id})['result']['content']:
                        break
                    sleep(.02)
                else:
                    self.fail('Command did not start')
                process.kill()
                process.wait()
                # The previous watchdog keeps the instance lock until cleanup finishes.
                with self.assertRaisesRegex(AssertionError, 'already using'):
                    with agent_process(root, state):
                        pass
                sleep(1.3)
            with agent_process(root, state) as (client, _):
                job = client.request('get', 'demo', 'get_job', {'job_id': job_id})['result']
                self.assertEqual(job['status'], 'interrupted')
                self.assertEqual(client.request('start', 'demo', 'run_command', arguments), original)
                self.assertEqual(client.request('output', 'demo', 'read_job_output', {'job_id': job_id})['result']['content'], 'started\n')
                sleep(1)
                self.assertFalse((root / 'orphan.txt').exists(), 'Managed child survived agent crash')
                self.assertEqual(state.stat().st_mode & 0o777, 0o700)
                self.assertTrue(all(path.stat().st_mode & 0o077 == 0 for path in state.iterdir()))

    def test_term_stops_job_tree_and_startup_failure_is_actionable(self):
        with TemporaryDirectory() as temp:
            root = Path(temp) / 'project'
            root.mkdir()
            state = Path(temp) / 'state'
            with agent_process(root, state) as (client, process):
                with self.assertRaisesRegex(AssertionError, 'already using'):
                    with agent_process(root, state):
                        pass
                started = client.request('start', 'demo', 'run_command', {'program': 'python', 'argv': ['-c', 'import time; print("ready",flush=True); time.sleep(2); open("survived.txt","w").write("bad")']})['result']
                sleep(.1)
                process.terminate()
                self.assertEqual(process.wait(timeout=8), 0)
            with agent_process(root, state) as (client, _):
                self.assertEqual(client.request('get', 'demo', 'get_job', {'job_id': started['job_id']})['result']['status'], 'cancelled')
                sleep(1)
                self.assertFalse((root / 'survived.txt').exists())

    def test_crash_during_accepted_mutation_returns_uncertain_without_replay(self):
        with TemporaryDirectory() as temp:
            root = Path(temp) / 'project'
            root.mkdir()
            state = Path(temp) / 'state'
            script = 'import signal,time; signal.signal(signal.SIGTERM,lambda *_: open("stopping.txt","w").write("stopping")); print("ready",flush=True); time.sleep(60)'
            with agent_process(root, state) as (client, process):
                started = client.request('start', 'demo', 'run_command', {'program': 'python', 'argv': ['-c', script]})['result']
                deadline = monotonic() + 5
                while monotonic() < deadline:
                    if client.request('out', 'demo', 'read_job_output', {'job_id': started['job_id']})['result']['content']:
                        break
                    sleep(.02)
                with ThreadPoolExecutor(max_workers=1) as pool:
                    pending = pool.submit(client.request, 'cancel', 'demo', 'cancel_job', {'job_id': started['job_id']})
                    deadline = monotonic() + 3
                    while monotonic() < deadline:
                        read = client.request('stopping', 'demo', 'read_file', {'path': 'stopping.txt'})
                        if read['ok']:
                            break
                        sleep(.01)
                    else:
                        self.fail('Cancellation did not reach the process')
                    self.assertFalse(pending.done())
                    process.kill()
                    process.wait()
                    with self.assertRaises((HTTPException, URLError, ConnectionError)):
                        pending.result()
                sleep(1.2)
            with agent_process(root, state) as (client, _):
                uncertain = client.request('cancel', 'demo', 'cancel_job', {'job_id': started['job_id']})
                self.assertEqual(uncertain['error']['code'], 'interrupted', uncertain)
                self.assertIn('may have taken effect', uncertain['error']['message'])
                self.assertEqual(client.request('cancel', 'demo', 'cancel_job', {'job_id': started['job_id']}), uncertain)
                self.assertEqual(client.request('job', 'demo', 'get_job', {'job_id': started['job_id']})['result']['status'], 'interrupted')

    def test_malformed_operation_returns_structured_error(self):
        with TemporaryDirectory() as temp:
            root = Path(temp) / 'project'
            root.mkdir()
            with agent_process(root, Path(temp) / 'state') as (client, _):
                for operation in ({}, []):
                    response = client.request('malformed', 'demo', operation)
                    self.assertEqual(response['error']['code'], 'invalid_request')

    def test_reconfigured_project_cannot_replay_another_roots_outcome(self):
        with TemporaryDirectory() as temp:
            root = Path(temp) / 'first'
            root.mkdir()
            state = Path(temp) / 'state'
            arguments = {'path': 'value.py', 'revision': 'missing', 'source': 'value = 1\n'}
            with agent_process(root, state) as (client, _):
                self.assertTrue(client.request('write', 'demo', 'write_file', arguments)['ok'])
            other = Path(temp) / 'second'
            other.mkdir()
            with agent_process(other, state) as (client, _):
                replay = client.request('write', 'demo', 'write_file', arguments)
                self.assertEqual(replay['error']['code'], 'request_conflict')
                self.assertFalse(client.request('read', 'demo', 'read_file', {'path': 'value.py'})['ok'])
            with self.assertRaisesRegex(AssertionError, 'outside configured projects'):
                with agent_process(other, other / 'state'):
                    pass
