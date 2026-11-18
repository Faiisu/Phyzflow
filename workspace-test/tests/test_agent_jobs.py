from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
from time import monotonic, sleep
import sys
import unittest

from workspace_sdk.agent import AgentClient, create_agent_server


@contextmanager
def running_agent(root, python=None, extra_projects=None, commands=None):
    token = root.parent / ('token-' + root.name)
    token.write_text('test-only-agent-jobs-token')
    token.chmod(0o600)
    config = {'token_file': str(token), 'projects': {'demo': {'root': str(root), 'python': python or sys.executable}}}
    config['projects']['demo']['commands'] = commands or []
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
                expect(view.get_by_role('button', name='Run flow')).to_be_enabled()
                started = client.request('agent-run', 'demo', 'run_flow')['result']
                expect(page.get_by_role('tab', name='Main')).to_contain_text('Running')
                expect(view.get_by_role('button', name='Run flow')).to_be_disabled()
                page.reload()
                expect(view.get_by_role('button', name='Run flow')).to_be_disabled()
                expect(view.locator('#status')).to_have_text('Running')
                conflict = page.request.post(url + '/flows/0/view/run', data={})
                self.assertEqual(conflict.status, 409)
                conflict = page.request.post(url + '/flows/0/reload', data={})
                self.assertEqual(conflict.status, 409)
                (root / 'flow.py').write_text(source.replace('original', 'updated'))
                expect(page.locator('#reload-message')).to_have_text('Source changed. Reload pending.')
                expect(frame).to_have_attribute('src', '/flows/0/view/')
                (root / 'release').touch()
                job = wait_job(client, started['job_id'])
                self.assertEqual(job['result']['output'], 'original')
                expect(frame).to_have_attribute('src', '/flows/0/revision/1/view/')
                history = page.request.get(url + '/flows/0/revision/1/view/history/' + job['result']['run_id']).json()
                self.assertEqual(history['source_version'], job['result']['source_version'])
                expect(view.get_by_role('button', name='Run flow')).to_be_enabled()

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


if __name__ == '__main__':
    unittest.main()
