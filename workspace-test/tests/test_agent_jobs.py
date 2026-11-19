from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
from time import monotonic, sleep
import sys
import unittest

from workspace_sdk.agent import AgentClient, create_agent_server


@contextmanager
def running_agent(root, python=None, extra_projects=None, commands=None, inference_environment=None):
    token = root.parent / ('token-' + root.name)
    token.write_text('test-only-agent-jobs-token')
    token.chmod(0o600)
    config = {'token_file': str(token), 'projects': {'demo': {'root': str(root), 'python': python or sys.executable}}}
    config['projects']['demo']['commands'] = commands or []
    config['projects']['demo']['inference_environment'] = inference_environment or {}
    config['projects'].update(extra_projects or {})
    with create_agent_server(config) as server:
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield AgentClient(f'http://127.0.0.1:{server.server_port}', token.read_text())
        finally:
            server.shutdown()
            thread.join()


def wait_job(client, job_id, project="demo"):
    deadline = monotonic() + 10
    while monotonic() < deadline:
        response = client.request('poll', project, 'get_job', {'job_id': job_id})
        if not response['ok']:
            raise AssertionError(response)
        job = response['result']
        if job['status'] not in ('queued', 'running'):
            return job
        sleep(.02)
    raise AssertionError('Job did not finish')


class AgentJobTests(unittest.TestCase):
    def test_edit_validate_and_run_with_selected_interpreter(self):
        with TemporaryDirectory() as temp:
            root = Path(temp).resolve() / 'project'
            root.mkdir()
            wrapper = root.parent / 'python'
            wrapper.write_text('#!/bin/sh\nexport AGENT_TEST_ENV=selected\nexec ' + sys.executable + ' "$@"\n')
            wrapper.chmod(0o700)
            with running_agent(root, str(wrapper)) as client:
                source = 'from workspace_sdk import Flow, Output, Step\nimport os\ndef work(): return os.environ["AGENT_TEST_ENV"]\nflow = Flow((), (Step("Work", work, {}),), Output("Work"))\n'
                self.assertTrue(client.request('write', 'demo', 'write_file', {'path': 'flow.py', 'revision': 'missing', 'source': source})['ok'])
                started = client.request('validate', 'demo', 'validate_flow')
                self.assertTrue(started['ok'], started)
                checked = wait_job(client, started['result']['job_id'])
                self.assertEqual(checked['status'], 'succeeded', checked)
                started = client.request('run', 'demo', 'run_flow', {'input': {}})
                self.assertTrue(started['ok'], started)
                job = wait_job(client, started['result']['job_id'])
                self.assertEqual(job['status'], 'succeeded', job)
                self.assertEqual(job['result']['output'], 'selected')
                self.assertEqual(len(job['result']['source_version']), 64)
                self.assertEqual(len(job['result']['run_id']), 32)

    def test_validation_and_run_use_only_operator_permitted_inference_environment(self):
        import os
        from unittest.mock import patch
        with TemporaryDirectory() as temp:
            root = Path(temp).resolve() / 'project'
            root.mkdir()
            ambient = root.parent / 'ambient'
            ambient.mkdir()
            (ambient / 'ambient_probe.py').write_text('secret = "ambient import"\n')
            (root / 'flow.py').write_text(
                'from workspace_sdk import Flow,Output,Step\n'
                'import os, sys, importlib.util\n'
                'assert "AGENT_AMBIENT_SECRET" not in os.environ, "ambient credential inherited"\n'
                'assert importlib.util.find_spec("ambient_probe") is None, "ambient import path inherited"\n'
                'assert "PYTHONPATH" not in os.environ and "PYTHONHOME" not in os.environ\n'
                'def work(): return {"model": os.environ.get("MODEL_LOCATION"), '
                '"secret": os.environ.get("AGENT_AMBIENT_SECRET"), "python": sys.executable}\n'
                'flow=Flow((),(Step("Work",work,{}),),Output("Work"))\n')
            with patch.dict(os.environ, {'AGENT_AMBIENT_SECRET': 'private-sentinel', 'PYTHONPATH': str(ambient), 'PYTHONHOME': '/invalid/ambient/python'}):
                with running_agent(root, commands=['python'], inference_environment={'MODEL_LOCATION': '/models/approved'}) as client:
                    for operation in ('validate_flow', 'run_flow'):
                        started = client.request(operation, 'demo', operation)
                        self.assertTrue(started['ok'], started)
                        job = wait_job(client, started['result']['job_id'])
                        self.assertEqual(job['status'], 'succeeded', job)
                        if operation == 'run_flow':
                            self.assertEqual(job['result']['output'], {'model': '/models/approved', 'secret': None, 'python': sys.executable})
                    rejected = client.request('client-env', 'demo', 'run_flow', {'environment': {'MODEL_LOCATION': 'client override'}})
                    self.assertFalse(rejected['ok'], rejected)
                    command = client.request('command-env', 'demo', 'run_command', {'program': 'python', 'argv': ['-c', 'import os; print(os.environ.get("MODEL_LOCATION")); print(os.environ.get("AGENT_AMBIENT_SECRET")); print(os.environ.get("PYTHONPATH"))']})
                    self.assertEqual(wait_job(client, command['result']['job_id'])['status'], 'succeeded')
                    output = client.request('command-output', 'demo', 'read_job_output', {'job_id': command['result']['job_id']})
                    self.assertEqual(output['result']['content'], 'None\nNone\nNone\n')

    def test_nested_validation_and_failure_diagnostics_are_safe_in_history(self):
        from browser_support import local_ui
        import json
        from urllib.request import urlopen
        with TemporaryDirectory() as temp:
            root = Path(temp).resolve() / 'project'
            root.mkdir()
            (root / 'pyproject.toml').write_text('[tool.workspace]\nsources=["helper.py"]\n')
            (root / 'helper.py').write_text('def predict(value):\n    raise RuntimeError(value)\n')
            (root / 'flow.py').write_text('from workspace_sdk import Flow, Input, Output, Step\nfrom helper import predict\ndef finish(value): return value\nflow=Flow(("value",), (Step("Predict", predict, {"value":Input("value")}), Step("Finish", finish, {"value":Output("Predict")})), Output("Finish"))\n')
            with running_agent(root) as client:
                bad = (root / 'helper.py').read_text()
                (root / 'helper.py').write_text('def broken(\n')
                job = client.request('bad', 'demo', 'validate_flow')['result']
                checked = wait_job(client, job['job_id'])
                self.assertEqual(checked['status'], 'failed')
                self.assertEqual(checked['error']['stage'], 'validation')
                self.assertIn('helper.py:1', checked['error']['message'])
                (root / 'helper.py').write_text(bad)
                started = client.request('run', 'demo', 'run_flow', {'input': {'value':'private-request-value'}})['result']
                job = wait_job(client, started['job_id'])
                self.assertEqual(job['status'], 'failed', job)
                steps = {step['id']:step for step in job['result']['steps']}
                self.assertEqual(steps['Predict']['status'], 'failed')
                self.assertEqual(steps['Finish']['status'], 'skipped')
                self.assertEqual(steps['Predict']['error_type'], 'RuntimeError')
                with local_ui(root, single_flow=True) as (url, _):
                    with urlopen(url + '/history/' + job['result']['run_id']) as response:
                        history = json.load(response)
                    self.assertEqual(history['source_version'], job['result']['source_version'])
                    self.assertNotIn('private-request-value', json.dumps(history))
                    with urlopen(url + '/live-run') as response:
                        self.assertNotIn('private-request-value', response.read().decode())

    def test_open_browser_follows_agent_run_and_defers_reload(self):
        from browser_support import local_ui, browser_page
        from playwright.sync_api import expect
        with TemporaryDirectory() as temp, browser_page() as page:
            root = Path(temp).resolve() / 'project'
            root.mkdir()
            source = 'from workspace_sdk import Flow, Output, Step\nfrom pathlib import Path\nfrom time import sleep\ndef work():\n    Path("started").touch()\n    while not Path("release").exists(): sleep(.01)\n    return "original"\nflow=Flow((), (Step("Work",work,{}),), Output("Work"))\n'
            (root / 'flow.py').write_text(source)
            with running_agent(root) as client, local_ui(root) as (url, _):
                page.goto(url)
                frame = page.locator('iframe[title="Main flow"]')
                view = frame.content_frame
                expect(page.get_by_role('button', name='Run flow')).to_be_enabled()
                started = client.request('agent-run', 'demo', 'run_flow')['result']
                expect(page.get_by_role('tab', name='Main')).to_contain_text('Running')
                expect(page.get_by_role('button', name='Run flow')).to_be_disabled()
                page.reload()
                expect(page.get_by_role('button', name='Run flow')).to_be_disabled()
                expect(view.locator('#status')).to_have_text('Running')
                conflict = page.request.post(url + '/flows/0/view/run', data={})
                self.assertEqual(conflict.status, 409)
                conflict = page.request.post(url + '/flows/0/reload', data={})
                self.assertEqual(conflict.status, 409)
                (root / 'flow.py').write_text(source.replace('original', 'updated'))
                expect(page.locator('#reload-flow')).to_be_disabled()
                expect(frame).to_have_attribute('src', '/flows/0/view/')
                (root / 'release').touch()
                job = wait_job(client, started['job_id'])
                self.assertEqual(job['result']['output'], 'original')
                expect(frame).to_have_attribute('src', '/flows/0/revision/1/view/')
                history = page.request.get(url + '/flows/0/revision/1/view/history/' + job['result']['run_id']).json()
                self.assertEqual(history['source_version'], job['result']['source_version'])
                expect(page.get_by_role('button', name='Run flow')).to_be_enabled()

    def test_frontend_blocks_editing_while_running_and_hides_history_panel(self):
        from browser_support import local_ui, browser_page
        from playwright.sync_api import expect
        with TemporaryDirectory() as temp, browser_page() as page:
            root = Path(temp).resolve() / 'project'
            root.mkdir()
            (root / 'flow.py').write_text(
                'from workspace_sdk import Flow, Output, Step\n'
                'from pathlib import Path\n'
                'from time import sleep\n'
                'def work():\n'
                '    while not Path("release").exists(): sleep(.01)\n'
                '    return 42\n'
                'flow=Flow((),(Step("Work",work,{}),),Output("Work"))\n'
            )
            with local_ui(root) as (url, _):
                page.goto(url)
                frame = page.locator('iframe[title="Main flow"]')
                view = frame.content_frame
                expect(view.locator('#panel-history')).to_have_count(0)
                expect(view.locator('#dashboard-run')).to_have_count(0)
                page.get_by_role('button', name='Run flow').click()
                expect(page.get_by_role('tab', name='Main')).to_contain_text('Running')

                page.get_by_role('button', name='Edit flow.py').click()
                dialog = page.get_by_role('dialog', name='Edit flow.py')
                expect(dialog.get_by_role('textbox', name='Source code')).not_to_be_editable()
                expect(dialog.get_by_role('button', name='Save', exact=True)).to_be_disabled()
                dialog.get_by_role('button', name='Close', exact=True).click()

                src_resp = page.request.get(url + '/flows/0/source').json()
                blocked = page.request.post(
                    url + '/flows/0/source',
                    data={'source': src_resp['source'], 'revision': src_resp['revision']},
                )
                self.assertEqual(blocked.status, 409)

                (root / 'release').touch()
                expect(page.get_by_role('button', name='Run flow')).to_be_enabled()
                page.get_by_role('button', name='Edit flow.py').click()
                expect(dialog.get_by_role('textbox', name='Source code')).to_be_editable()
                dialog.get_by_role('button', name='Close', exact=True).click()

    def test_ui_run_blocks_agent_start_on_same_sheet(self):
        from browser_support import local_ui, browser_page
        from playwright.sync_api import expect
        with TemporaryDirectory() as temp, browser_page() as page:
            root = Path(temp).resolve() / 'project'
            root.mkdir()
            (root / 'flow.py').write_text('from workspace_sdk import Flow, Output, Step\nfrom pathlib import Path\nfrom time import sleep\ndef work():\n    while not Path("release").exists(): sleep(.01)\n    return 7\nflow=Flow((),(Step("Work",work,{}),),Output("Work"))\n')
            with running_agent(root) as client, local_ui(root, single_flow=True) as (url, _):
                page.goto(url)
                page.get_by_role('button', name='Run flow').click()
                try:
                    expect(page.locator('#status')).to_have_text('Running')
                    conflict = client.request('agent-conflict', 'demo', 'run_flow')
                    self.assertEqual(conflict['error']['code'], 'conflict', conflict)
                finally:
                    (root / 'release').touch()
                expect(page.locator('#result pre')).to_have_text('7')
                started = client.request('after-ui', 'demo', 'run_flow')
                self.assertTrue(started['ok'], started)
                self.assertEqual(wait_job(client, started['result']['job_id'])['status'], 'succeeded')

    def test_separate_projects_isolate_imports_and_job_access(self):
        with TemporaryDirectory() as temp:
            root = Path(temp).resolve() / 'first'
            second = root.parent / 'second'
            for project, value in ((root, 11), (second, 22)):
                project.mkdir()
                (project / 'helper.py').write_text(f'def work(): return {value}\n')
                (project / 'flow.py').write_text('from workspace_sdk import Flow,Output,Step\nfrom helper import work\nflow=Flow((),(Step("Work",work,{}),),Output("Work"))\n')
            with running_agent(root, extra_projects={'other': {'root': str(second), 'python': sys.executable}}) as client:
                for project, value in (('demo', 11), ('other', 22)):
                    started = client.request('start-' + project, project, 'run_flow')['result']
                    job = wait_job(client, started['job_id'], project)
                    self.assertEqual(job['result']['output'], value)
                isolated = client.request('foreign-job', 'demo', 'get_job', {'job_id': started['job_id']})
                self.assertFalse(isolated['ok'])

    def test_history_write_failure_preserves_successful_output(self):
        with TemporaryDirectory() as temp:
            root = Path(temp).resolve() / 'project'
            root.mkdir()
            (root / 'flow.py').write_text('from workspace_sdk import Flow,Output,Step\ndef work(): return 42\nflow=Flow((),(Step("Work",work,{}),),Output("Work"))\n')
            history = root / '.workspace' / 'runs'
            history.mkdir(parents=True)
            history.chmod(0o500)
            try:
                with running_agent(root) as client:
                    started = client.request('no-history', 'demo', 'run_flow')['result']
                    job = wait_job(client, started['job_id'])
                    self.assertEqual(job['status'], 'succeeded', job)
                    self.assertEqual(job['result']['output'], 42)
                    self.assertIn('history_error', job['result'])
            finally:
                history.chmod(0o700)

    def test_cancel_releases_sheet_and_fresh_processes_isolate_imports(self):
        with TemporaryDirectory() as temp:
            root = Path(temp).resolve() / 'project'
            root.mkdir()
            (root / 'flow.py').write_text('from workspace_sdk import Flow, Output, Step\nfrom pathlib import Path\nfrom time import sleep\ndef work():\n    Path("started").touch()\n    while not Path("release").exists(): sleep(.01)\n    return 7\nflow=Flow((), (Step("Work",work,{}),), Output("Work"))\n')
            other = root / 'flows' / 'Other'
            other.mkdir(parents=True)
            (other / 'flow.py').write_text('from workspace_sdk import Flow,Output,Step\nfrom pathlib import Path\ndef work(): return 99\nflow=Flow((),(Step("Other",work,{}),),Output("Other"))\n')
            with running_agent(root) as client:
                started = client.request('start', 'demo', 'run_flow')['result']
                deadline = monotonic() + 5
                while not (root / 'started').exists() and monotonic() < deadline: sleep(.01)
                self.assertTrue((root / 'started').exists())
                conflict = client.request('duplicate', 'demo', 'run_flow')
                self.assertEqual(conflict['error']['code'], 'conflict')
                independent = client.request('independent', 'demo', 'run_flow', {'sheet': 'Other'})
                self.assertTrue(independent['ok'], independent)
                self.assertEqual(wait_job(client, independent['result']['job_id'])['result']['output'], 99)
                client.request('cancel', 'demo', 'cancel_job', {'job_id':started['job_id']})
                self.assertEqual(wait_job(client, started['job_id'])['status'], 'cancelled')
                (root / 'release').touch()
                again = client.request('again', 'demo', 'run_flow')
                self.assertTrue(again['ok'], again)
                self.assertEqual(wait_job(client, again['result']['job_id'])['result']['output'], 7)
                (root / 'helper.py').write_text('value=11\n')
                (root / 'flow.py').write_text('from workspace_sdk import Flow, Output, Step\nfrom helper import value\ndef work(): return value\nflow=Flow((),(Step("Work",work,{}),),Output("Work"))\n')
                first = client.request('first', 'demo', 'run_flow')['result']
                self.assertEqual(wait_job(client, first['job_id'])['result']['output'], 11)
                (root / 'helper.py').write_text('value=22\n')
                second = client.request('second', 'demo', 'run_flow')['result']
                self.assertEqual(wait_job(client, second['job_id'])['result']['output'], 22)

    def test_unified_ui_controller_runs_and_cancels_in_same_worker(self):
        import json
        from urllib.request import urlopen
        from workspace_sdk.agent_jobs import AgentJobs
        from workspace_sdk.execution import FlowBusy
        from workspace_sdk.ui_settings import ZoomSettings
        from workspace_sdk.workspace_ui import FlowSheet, WorkspaceController

        with TemporaryDirectory() as temp:
            root = Path(temp).resolve() / 'project'
            root.mkdir()
            (root / 'flow.py').write_text(
                'from workspace_sdk import Flow, Output, Step\n'
                'from pathlib import Path\n'
                'from time import sleep\n'
                'def work():\n'
                '    Path("started").touch()\n'
                '    while not Path("release").exists(): sleep(.01)\n'
                '    return "unified"\n'
                'flow=Flow((), (Step("Work",work,{}),), Output("Work"))\n'
            )
            sheet = FlowSheet('Main', root, ZoomSettings(scale=1.0, x=0.0, y=0.0), main=True)
            controller = WorkspaceController(lambda path: sheet)
            jobs = AgentJobs()
            jobs.attach_ui(controller)
            project = {'root': str(root), 'python': sys.executable, 'inference_environment': {}}
            try:
                started = jobs.start(project, 'run_flow', root, {})
                deadline = monotonic() + 5
                while not (root / 'started').exists() and monotonic() < deadline:
                    sleep(0.02)
                self.assertTrue((root / 'started').exists())
                self.assertEqual(sheet.status(), 'running')
                with urlopen(f"{sheet.worker.url}/live-run", timeout=2) as response:
                    snapshot = json.loads(response.read())
                self.assertTrue(snapshot['running'])
                with self.assertRaises(FlowBusy):
                    jobs.start(project, 'run_flow', root, {})

                cancelled = jobs.get(project, started['job_id'], cancel=True)
                self.assertEqual(cancelled['status'], 'cancelled')
                self.assertEqual(sheet.status(), 'ready')

                (root / 'release').touch()
                second = jobs.start(project, 'run_flow', root, {})
                deadline = monotonic() + 10
                while monotonic() < deadline:
                    state = jobs.get(project, second['job_id'])
                    if state['status'] not in ('queued', 'running'):
                        break
                    sleep(0.05)
                self.assertEqual(state['status'], 'succeeded', state)
                self.assertEqual(state['result']['output'], 'unified')
            finally:
                sheet.stop(shutdown=True)

    def test_update_workspace_and_restart_workspace_operations(self):
        import os
        import subprocess
        from threading import Event
        from unittest.mock import patch
        from workspace_sdk.agent import WorkspaceAgent
        from workspace_sdk.agent_mcp import create_mcp_server
        from workspace_sdk.agent_remote import REMOTE_OPERATIONS

        self.assertIn('update_workspace', REMOTE_OPERATIONS)
        self.assertIn('restart_workspace', REMOTE_OPERATIONS)

        server = create_mcp_server(object())
        tools = {tool.name for tool in server._tool_manager.list_tools()}
        self.assertIn('update_workspace', tools)
        self.assertIn('restart_workspace', tools)

        with TemporaryDirectory() as temp:
            base = Path(temp)
            upstream = base / 'upstream.git'
            repo = base / 'repo'
            subprocess.run(['git', 'init', '--bare', str(upstream)], check=True, capture_output=True)
            subprocess.run(['git', 'init', str(repo)], check=True, capture_output=True)
            subprocess.run(['git', 'remote', 'add', 'origin', str(upstream)], cwd=repo, check=True)
            (repo / 'README.md').write_text('v1\n')
            project = repo / 'default-workspace'
            project.mkdir(parents=True)
            (project / 'flow.py').write_text('from workspace_sdk import Flow, Output, Step\nflow = Flow((), (Step("S", lambda: 1, {}),), Output("S"))\n')
            subprocess.run(['git', 'config', 'user.email', 'test@example.com'], cwd=repo, check=True)
            subprocess.run(['git', 'config', 'user.name', 'Test'], cwd=repo, check=True)
            subprocess.run(['git', 'add', '.'], cwd=repo, check=True)
            subprocess.run(['git', 'commit', '-m', 'init'], cwd=repo, check=True, capture_output=True)
            subprocess.run(['git', 'push', '-u', 'origin', 'HEAD'], cwd=repo, check=True, capture_output=True)

            # Create a second clone to push v2 upstream
            pusher = base / 'pusher'
            subprocess.run(['git', 'clone', str(upstream), str(pusher)], check=True, capture_output=True)
            subprocess.run(['git', 'config', 'user.email', 'test@example.com'], cwd=pusher, check=True)
            subprocess.run(['git', 'config', 'user.name', 'Test'], cwd=pusher, check=True)
            (pusher / 'README.md').write_text('v2\n')
            subprocess.run(['git', 'add', 'README.md'], cwd=pusher, check=True)
            subprocess.run(['git', 'commit', '-m', 'v2'], cwd=pusher, check=True, capture_output=True)
            subprocess.run(['git', 'push'], cwd=pusher, check=True, capture_output=True)

            restarted = Event()
            agent = WorkspaceAgent({'development': {'root': str(project), 'python': sys.executable}})
            agent.attach_lifecycle(repo_dir=repo, on_restart=restarted.set)

            with patch.dict(os.environ, {'PHYZFLOW_RESTART_WRAPPER': '1'}):
                res = agent.dispatch({
                    'request_id': 'upd-1',
                    'project_id': 'development',
                    'operation': 'update_workspace',
                    'arguments': {'restart': True},
                })
            self.assertTrue(res['ok'], res)
            self.assertTrue(res['result']['updated'])
            self.assertTrue(res['result']['restarting'])
            self.assertNotEqual(res['result']['previous_commit'], res['result']['current_commit'])
            self.assertEqual((repo / 'README.md').read_text(), 'v2\n')
            self.assertTrue(restarted.wait(2.0))

            restarted.clear()
            res2 = agent.dispatch({
                'request_id': 'rst-1',
                'project_id': 'development',
                'operation': 'restart_workspace',
                'arguments': {},
            })
            self.assertTrue(res2['ok'], res2)
            self.assertEqual(res2['result']['status'], 'restarting')
            self.assertTrue(restarted.wait(2.0))


if __name__ == '__main__':
    unittest.main()
