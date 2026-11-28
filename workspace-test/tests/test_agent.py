import sys
import unittest
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread

from workspace_sdk.agent import AgentClient, create_agent_server


class AgentInspectionTests(unittest.TestCase):
    @contextmanager
    def agent(self):
        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            project = root / 'project'
            project.mkdir()
            (project / 'flow.py').write_text('answer = 42\n')
            (project / 'pyproject.toml').write_text('[project]\nname="sample"\nversion="1"\n')
            token = root / 'token'
            token.write_text('test-only-agent-token')
            token.chmod(0o600)
            config = {'token_file': str(token), 'projects': {'demo': {'root': str(project), 'python': sys.executable}}}
            with create_agent_server(config, port=0) as server:
                thread = Thread(target=server.serve_forever, daemon=True)
                thread.start()
                try:
                    yield AgentClient(f'http://127.0.0.1:{server.server_port}', token.read_text()), project
                finally:
                    server.shutdown()
                    thread.join()

    def test_client_inspects_project_and_reads_revision(self):
        with self.agent() as (client, _project):
            result = client.request('inspect-1', 'demo', 'inspect_project')
            self.assertTrue(result['ok'])
            self.assertEqual(result['request_id'], 'inspect-1')
            self.assertEqual(result['result']['sheets'], [{'name': 'Main', 'file': 'flow.py'}])
            self.assertEqual(result['result']['python'], sys.executable)
            files = client.request('files', 'demo', 'list_files')['result']
            self.assertIn({'path': 'flow.py', 'kind': 'file'}, files['entries'])
            read = client.request('read', 'demo', 'read_file', {'path': 'flow.py'})['result']
            self.assertEqual(read['content'], 'answer = 42\n')
            self.assertEqual(len(read['revision']), 64)
            self.assertFalse(read['truncated'])
            search = client.request('search', 'demo', 'search_code', {'query': '42'})['result']
            self.assertEqual(search['matches'], [{'path': 'flow.py', 'line': 1, 'text': 'answer = 42'}])

    def test_denies_unconfigured_projects_escapes_and_bad_authentication(self):
        with self.agent() as (client, project):
            denied = AgentClient(client.url, 'wrong-token').request('bad', 'demo', 'inspect_project')
            self.assertEqual(denied['error']['code'], 'unauthorized')
            self.assertEqual(client.request('unknown', 'other', 'inspect_project')['error']['code'], 'unknown_project')
            (project / 'escape.py').symlink_to(project.parent / 'secret.py')
            (project.parent / 'secret.py').write_text('secret')
            for path in ('../secret.py', str(project.parent / 'secret.py'), 'escape.py'):
                with self.subTest(path=path):
                    response = client.request('escape', 'demo', 'read_file', {'path': path})
                    self.assertFalse(response['ok'])
                    self.assertEqual(response['error']['code'], 'denied')
            self.assertEqual(client.request('unsupported', 'demo', 'delete_everything')['error']['code'], 'unsupported_operation')
            self.assertEqual(client.request('invalid', 'demo', 'read_file', {'path': 5})['error']['code'], 'invalid_path')
            self.assertEqual(client.request('', 'demo', 'inspect_project')['error']['code'], 'invalid_request')
            listing = client.request('list', 'demo', 'list_files')['result']
            self.assertNotIn('escape.py', [entry['path'] for entry in listing['entries']])

    def test_file_listing_read_and_search_report_limits(self):
        with self.agent() as (client, project):
            (project / 'large.py').write_text('x' * 100000)
            read = client.request('large', 'demo', 'read_file', {'path': 'large.py'})['result']
            self.assertTrue(read['truncated'])
            self.assertLessEqual(len(read['content'].encode()), 65536)
            self.assertIsNone(read['revision'])
            (project / 'large.py').unlink()
            (project / 'many.py').write_text('needle\n' * 150)
            search = client.request('many', 'demo', 'search_code', {'query': 'needle'})['result']
            self.assertEqual(len(search['matches']), 100)
            self.assertTrue(search['truncated'])
            for number in range(510):
                (project / f'file-{number}').touch()
            listing = client.request('many-files', 'demo', 'list_files')['result']
            self.assertEqual(len(listing['entries']), 500)
            self.assertTrue(listing['truncated'])

    def test_search_bounds_empty_directory_traversal(self):
        with self.agent() as (client, project):
            for number in range(2010):
                (project / f'dir-{number}').mkdir()
            result = client.request('scan', 'demo', 'search_code', {'query': 'absent'})['result']
            self.assertTrue(result['truncated'])

    def test_module_starts_with_config_and_inspects_installed_sample(self):
        import json
        import select
        import signal
        import subprocess
        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            token = root / 'token'
            token.write_text('test-only-agent-token')
            token.chmod(0o600)
            sample = Path(__file__).resolve().parents[1]
            config = root / 'config.json'
            config.write_text(json.dumps({'token_file': str(token), 'projects': {
                'sample': {'root': str(sample), 'python': sys.executable},
            }}))
            process = subprocess.Popen([sys.executable, '-m', 'workspace_sdk.agent', '--config', str(config)],
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            try:
                self.assertTrue(select.select([process.stdout], [], [], 5)[0], 'agent did not announce readiness')
                line = process.stdout.readline().strip()
                self.assertTrue(line.startswith('Workspace agent: '), line)
                client = AgentClient(line.removeprefix('Workspace agent: '), token.read_text())
                response = client.request('sample', 'sample', 'inspect_project')
                self.assertTrue(response['ok'], response)
                self.assertIn({'name': 'Main', 'file': 'flow.py'}, response['result']['sheets'])
                cli = subprocess.run([sys.executable, '-c', 'from workspace_sdk.cli import main; raise SystemExit(main())',
                                      '--project', str(sample), 'sheets'], capture_output=True, text=True, check=True)
                self.assertEqual([sheet['name'] for sheet in response['result']['sheets']],
                                 [sheet['name'] for sheet in json.loads(cli.stdout)])
                content = client.request('source', 'sample', 'read_file', {'path': 'flow.py'})['result']['content']
                self.assertIn('flow = Flow(', content)
            finally:
                process.send_signal(signal.SIGINT)
                process.communicate(timeout=5)

    def test_rejects_public_token_file_and_invalid_transport(self):
        import json
        from urllib.error import HTTPError
        from urllib.request import Request, urlopen
        with self.agent() as (client, project):
            for body in (b'not json', json.dumps({'request_id': 'x'}).encode()):
                request = Request(client.url + '/request', data=body, headers={
                    'Authorization': 'Bearer ' + client.token, 'Content-Type': 'application/json',
                })
                try:
                    response = urlopen(request, timeout=2)
                except HTTPError as error:
                    response = error
                with response:
                    self.assertFalse(json.load(response)['ok'])
            token = project.parent / 'public-token'
            token.write_text('test-only-agent-token')
            token.chmod(0o644)
            with self.assertRaises(ValueError):
                create_agent_server({'token_file': str(token), 'projects': {'demo': {'root': str(project), 'python': sys.executable}}})
