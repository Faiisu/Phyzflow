import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from test_agent_jobs import running_agent, wait_job


class AgentCommandTests(unittest.TestCase):
    def test_command_uses_selected_python_cwd_and_reports_failure_output(self):
        with TemporaryDirectory() as temp:
            root = Path(temp).resolve() / 'project'
            root.mkdir()
            wrapper = root.parent / 'python'
            wrapper.write_text('#!/bin/sh\nexport SELECTED_ENV=yes\nexec ' + sys.executable + ' "$@"\n')
            wrapper.chmod(0o700)
            with running_agent(root, str(wrapper), commands=['python']) as client:
                started = client.request('start', 'demo', 'run_command', {
                    'program': 'python', 'argv': ['-c', 'import os,sys; print(os.getcwd()); print(os.environ["SELECTED_ENV"]); print("test failed",file=sys.stderr); sys.exit(3)'], 'cwd': '.',
                })
                self.assertTrue(started['ok'], started)
                job = wait_job(client, started['result']['job_id'])
                self.assertEqual((job['status'], job['exit_code']), ('failed', 3))
                for stream, expected in [('stdout', str(root) + '\nyes\n'), ('stderr', 'test failed\n')]:
                    page = client.request('output', 'demo', 'read_job_output', {'job_id': job['job_id'], 'stream': stream})
                    self.assertTrue(page['ok'], page)
                    self.assertEqual(page['result']['content'], expected)

    def test_output_is_bounded_and_resumable_without_inheriting_credentials(self):
        import os
        with TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            os.environ['AGENT_COMMAND_SECRET'] = 'must-not-leak'
            try:
                with running_agent(root, commands=['python']) as client:
                    started = client.request('large', 'demo', 'run_command', {'program': 'python', 'argv': ['-c', 'import os,sys; assert "AGENT_COMMAND_SECRET" not in os.environ; sys.stdout.write("x"*1048576); sys.stderr.write("y"*1048576)']})['result']
                    job = wait_job(client, started['job_id'])
                    self.assertEqual(job['status'], 'succeeded', job)
                    for stream, character in [('stdout', 'x'), ('stderr', 'y')]:
                        cursor, contents = 0, ''
                        while True:
                            page = client.request('page', 'demo', 'read_job_output', {'job_id': job['job_id'], 'stream': stream, 'cursor': cursor, 'limit': 4096})['result']
                            self.assertLessEqual(len(page['content']), 4096)
                            contents += page['content']
                            cursor = page['next_cursor']
                            if page['complete']: break
                        self.assertEqual(contents, character * 131072)
                        self.assertEqual(page['total_bytes'], 1048576)
                        self.assertTrue(page['truncated'])
                    invalid = client.request('bad-cursor', 'demo', 'read_job_output', {'job_id': job['job_id'], 'cursor': -1})
                    self.assertFalse(invalid['ok'])
            finally:
                del os.environ['AGENT_COMMAND_SECRET']

    def test_permissions_cwd_missing_executable_and_lost_environment_are_actionable(self):
        with TemporaryDirectory() as temp:
            root = Path(temp).resolve() / 'project'
            root.mkdir()
            python = root.parent / 'python'
            python.symlink_to(sys.executable)
            missing = str(root.parent / 'missing-executable')
            with running_agent(root, str(python), commands=['python', missing]) as client:
                denied = client.request('denied', 'demo', 'run_command', {'program': '/bin/sh', 'argv': ['-c', 'echo unsafe']})
                self.assertEqual(denied['error']['code'], 'denied')
                outside = client.request('escape', 'demo', 'run_command', {'program': 'python', 'argv': [], 'cwd': '..'})
                self.assertEqual(outside['error']['code'], 'denied')
                absent = client.request('missing', 'demo', 'run_command', {'program': missing, 'argv': []})
                self.assertEqual(absent['error']['stage'], 'command')
                self.assertIn('Cannot start executable', absent['error']['message'])
                python.unlink()
                unavailable = client.request('env', 'demo', 'run_command', {'program': 'python', 'argv': []})
                self.assertIn('environment is inaccessible', unavailable['error']['message'])
            with running_agent(root) as client:
                disabled = client.request('disabled', 'demo', 'run_command', {'program': 'python', 'argv': []})
                self.assertEqual(disabled['error']['code'], 'denied')

    def test_cancel_kills_child_ignoring_sigterm_after_parent_exits(self):
        import os
        from time import monotonic, sleep
        with TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            child = 'import os,signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); print(os.getpid(),flush=True); time.sleep(60)'
            parent = 'import subprocess,sys,time; subprocess.Popen([sys.executable,"-c",' + repr(child) + ']); time.sleep(60)'
            with running_agent(root, commands=['python']) as client:
                started = client.request('start', 'demo', 'run_command', {'program': 'python', 'argv': ['-c', parent]})['result']
                deadline = monotonic() + 5
                child_pid = None
                while monotonic() < deadline:
                    page = client.request('output', 'demo', 'read_job_output', {'job_id': started['job_id']})['result']
                    if page['content'].strip():
                        child_pid = int(page['content'])
                        break
                    sleep(.02)
                self.assertIsNotNone(child_pid)
                try:
                    client.request('cancel', 'demo', 'cancel_job', {'job_id': started['job_id']})
                    job = wait_job(client, started['job_id'])
                    self.assertEqual(job['status'], 'cancelled', job)
                    self.assertLess(job['exit_code'], 0)
                    deadline = monotonic() + 5
                    while monotonic() < deadline:
                        try: os.kill(child_pid, 0)
                        except ProcessLookupError: break
                        sleep(.02)
                    else: self.fail('Child survived command cancellation')
                finally:
                    try: os.kill(child_pid, 9)
                    except ProcessLookupError: pass

    def test_install_offline_dependency_and_run_project_test_in_only_selected_environment(self):
        import venv
        import zipfile
        with TemporaryDirectory() as temp:
            root = Path(temp).resolve() / 'selected'
            other = root.parent / 'other'
            for project in (root, other):
                project.mkdir()
                venv.EnvBuilder(with_pip=True, symlinks=True).create(project / '.venv')
                (project / 'test_failure.py').write_text('import unittest\nclass Failure(unittest.TestCase):\n    def test_failure(self): self.assertEqual(1, 2, "expected failure diagnostic")\n')
                (project / 'test_dependency.py').write_text('import unittest\nimport edge_sample_dependency\nclass DependencyTest(unittest.TestCase):\n    def test_answer(self): self.assertEqual(edge_sample_dependency.answer(), 42)\n')
            wheel = root / 'edge_sample_dependency-1.0-py3-none-any.whl'
            with zipfile.ZipFile(wheel, 'w') as archive:
                archive.writestr('edge_sample_dependency.py', 'def answer(): return 42\n')
                archive.writestr('edge_sample_dependency-1.0.dist-info/METADATA', 'Metadata-Version: 2.1\nName: edge-sample-dependency\nVersion: 1.0\n')
                archive.writestr('edge_sample_dependency-1.0.dist-info/WHEEL', 'Wheel-Version: 1.0\nGenerator: fixture\nRoot-Is-Purelib: true\nTag: py3-none-any\n')
                archive.writestr('edge_sample_dependency-1.0.dist-info/RECORD', '')
            with running_agent(root, str(root / '.venv/bin/python'), commands=['python'], extra_projects={
                'other': {'root': str(other), 'python': str(other / '.venv/bin/python'), 'commands': ['python']},
            }) as client:
                install = client.request('install', 'demo', 'run_command', {'program': 'python', 'argv': ['-m', 'pip', 'install', '--no-index', '--disable-pip-version-check', wheel.name], 'cwd': '.'})
                self.assertTrue(install['ok'], install)
                installed = wait_job(client, install['result']['job_id'])
                self.assertEqual((installed['status'], installed['exit_code']), ('succeeded', 0), installed)
                for project, status in [('demo', 'succeeded'), ('other', 'failed')]:
                    started = client.request('tests-' + project, project, 'run_command', {'program': 'python', 'argv': ['-m', 'unittest', 'test_dependency', '-v'], 'cwd': '.'})['result']
                    job = wait_job(client, started['job_id'], project)
                    self.assertEqual(job['status'], status, job)
                    output = client.request('diagnostic', project, 'read_job_output', {'job_id': job['job_id'], 'stream': 'stderr'})['result']['content']
                    self.assertIn('OK' if project == 'demo' else "No module named 'edge_sample_dependency'", output)
                    if project == 'other':
                        self.assertNotEqual(job['exit_code'], 0)
                        denied = client.request('foreign-output', 'demo', 'read_job_output', {'job_id': job['job_id']})
                        self.assertFalse(denied['ok'])

                failed = client.request('assertion-failure', 'demo', 'run_command', {'program': 'python', 'argv': ['-m', 'unittest', 'test_failure'], 'cwd': '.'})['result']
                job = wait_job(client, failed['job_id'])
                self.assertEqual((job['status'], job['exit_code']), ('failed', 1))
                diagnostic = client.request('failure-output', 'demo', 'read_job_output', {'job_id': job['job_id'], 'stream': 'stderr'})['result']['content']
                self.assertIn('AssertionError: 1 != 2 : expected failure diagnostic', diagnostic)

    def test_commands_exceed_previous_retention_limit_without_eviction(self):
        with TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            with running_agent(root, commands=['python']) as client:
                first = None
                for index in range(33):
                    started = client.request('job-' + str(index), 'demo', 'run_command', {'program': 'python', 'argv': ['-c', 'print("retained")']})
                    self.assertTrue(started['ok'], started)
                    first = first or started['result']['job_id']
                    job = wait_job(client, started['result']['job_id'])
                    diagnostic = client.request('retention-failure', 'demo', 'read_job_output',
                                                {'job_id': job['job_id'], 'stream': 'stderr'}) if job['exit_code'] else None
                    self.assertEqual(job['exit_code'], 0, {'job': job, 'stderr': diagnostic})
                retained = client.request('retained', 'demo', 'read_job_output', {'job_id': first})
                self.assertEqual(retained['result']['content'], 'retained\n')
