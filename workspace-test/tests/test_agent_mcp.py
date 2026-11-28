"""Public MCP subprocess -> verified TLS relay -> real authenticated Edge."""
import asyncio
import importlib.util
import json
import sys
import time
import unittest
from pathlib import Path

import test_agent_remote as remote_fixture


@unittest.skipUnless(importlib.util.find_spec('mcp'), 'requires optional workspace-sdk[mcp] extra')
class MCPAgentTests(unittest.TestCase):
    setUp = remote_fixture.RemoteAgentTests.setUp
    token = remote_fixture.RemoteAgentTests.token
    serve = remote_fixture.RemoteAgentTests.serve
    connect = remote_fixture.RemoteAgentTests.connect
    start_connector = remote_fixture.RemoteAgentTests.start_connector

    def test_discovery_edit_jobs_and_authorization_through_actual_mcp(self):
        self.stop_edge = self.connect('a')
        config = self.root / 'mcp.json'
        config.write_text(json.dumps({'central_url': self.url,
            'client_token_file': self.config['clients']['bob']['token_file'], 'ca_file': str(self.cert)}))
        asyncio.run(self.exercise(config))

    async def exercise(self, config):
        from mcp import Client, StdioServerParameters
        params = StdioServerParameters(command=sys.executable,
            args=['-m', 'workspace_sdk.agent_mcp', '--config', str(config)],
            env={'PYTHONPATH': str(Path(__file__).resolve().parents[2] / 'workspace' / 'src')})
        async with Client(params, raise_exceptions=False) as client:
            tools = {tool.name: tool for tool in (await client.list_tools()).tools}
            self.assertIn('write_file', tools)
            read_only = {'list_devices', 'inspect_project', 'list_files', 'read_file', 'search_code',
                         'validate_flow', 'get_job', 'read_job_output'}
            for name, tool in tools.items():
                self.assertTrue(tool.title, name)
                self.assertIs(tool.annotations.read_only_hint, name in read_only, name)
                self.assertIsNotNone(tool.annotations.destructive_hint, name)
                self.assertEqual(tool.output_schema['type'], 'object', name)
                for parameter, schema in tool.input_schema.get('properties', {}).items():
                    self.assertTrue(schema.get('description'), f'{name}.{parameter}')
            self.assertEqual(set(tools['apply_patch'].input_schema['required']),
                {'request_id', 'device_id', 'project_id', 'path', 'revision', 'old_text', 'new_text'})
            async def call(name, **args):
                result = await client.call_tool(name, args)
                self.assertFalse(result.is_error, result)
                return json.loads(result.content[0].text)
            malformed = await client.call_tool('read_file', {'device_id': 'a'})
            self.assertTrue(malformed.is_error)
            self.assertEqual((await call('list_devices'))['result']['devices'],
                [{'device_id': 'a', 'status': 'connected'}])
            target = {'device_id': 'a', 'project_id': 'demo'}
            before = (await call('read_file', request_id='read', **target, path='flow.py'))['result']
            edit = await call('write_file', request_id='edit', **target, path='flow.py',
                revision=before['revision'], source='from workspace_sdk import Flow, Output, Step\ndef work(): return 42\nflow=Flow((),(Step("Work",work,{}),),Output("Work"))\n')
            self.assertTrue(edit['ok'], edit)
            stale = await call('apply_patch', request_id='stale', **target, path='flow.py',
                revision=before['revision'], old_text='42', new_text='43')
            self.assertEqual(stale['error']['code'], 'conflict')
            denied = await call('inspect_project', request_id='denied', device_id='b', project_id='demo')
            self.assertEqual(denied['error']['code'], 'denied')
            denied = await call('inspect_project', request_id='scope', device_id='a', project_id='production')
            self.assertEqual(denied['error']['code'], 'unknown_project')
            offline = await call('inspect_project', request_id='offline', device_id='missing', project_id='demo')
            self.assertEqual(offline['error']['code'], 'denied')
            started = await call('run_flow', request_id='run', **target)
            repeated = await call('run_flow', request_id='run', **target)
            self.assertEqual(started, repeated)
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                job = await call('get_job', request_id='poll', **target, job_id=started['result']['job_id'])
                if job['result']['status'] not in ('queued', 'running'):
                    break
                await asyncio.sleep(.05)
            self.assertEqual(job['result']['result']['output'], 42)
            command = await call('run_command', request_id='command', **target,
                program='python', argv=['-u', '-c', 'import time; print("mcp-output", flush=True); time.sleep(60)'])
            command_id = command['result']['job_id']
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                output = await call('read_job_output', request_id='output', **target,
                    job_id=command_id, limit=4)
                if output['result']['content']:
                    break
                await asyncio.sleep(.05)
            self.assertEqual(output['result']['content'], 'mcp-')
            self.assertEqual(output['result']['next_cursor'], 4)
            cancelled = await call('cancel_job', request_id='cancel', **target, job_id=command_id)
            self.assertTrue(cancelled['ok'], cancelled)
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                ended = await call('get_job', request_id='cancel-poll', **target, job_id=command_id)
                if ended['result']['status'] == 'cancelled':
                    break
                await asyncio.sleep(.05)
            self.assertEqual(ended['result']['status'], 'cancelled')
            self.stop_edge.set()
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                devices = await call('list_devices')
                if devices['result']['devices'][0]['status'] == 'unavailable':
                    break
                await asyncio.sleep(.05)
            offline = await call('inspect_project', request_id='disconnected', **target)
            self.assertEqual(offline['error']['code'], 'unavailable')
