"""Agent-owned asynchronous jobs in the configured project interpreter."""

import json
import os
import signal
import subprocess
from pathlib import Path
from time import monotonic, sleep
from threading import Lock, Thread
from uuid import uuid4

from .execution import ExternalRun, sheet_lock


class AgentJobs:
    def __init__(self):
        self.lock = Lock()
        self.jobs = {}

    def start(self, project, operation, sheet, inputs):
        if os.name != 'posix':
            raise ValueError('Agent flow jobs require Linux or macOS.')
        reservation = sheet_lock(sheet)
        descriptor = reservation.__enter__()
        job_id = uuid4().hex
        job = {'job_id': job_id, 'status': 'queued', 'operation': operation}
        try:
            if operation == 'run_flow':
                ExternalRun(sheet, job_id)
            process = subprocess.Popen([project['python'], '-m', 'workspace_sdk.agent_worker'], cwd=sheet,
                                       stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                       start_new_session=True, pass_fds=(descriptor.fileno(),))
            process.stdin.write((json.dumps({'job_id': job_id, 'operation': operation, 'input': inputs}) + '\n').encode())
            process.stdin.close()
        except BaseException:
            if 'process' in locals():
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
            reservation.__exit__(None, None, None)
            raise
        with self.lock:
            self.jobs[job_id] = {'project': project['root'], 'job': job, 'process': process, 'cancelled': False}
        def collect():
            with self.lock:
                job['status'] = 'running'
            data = process.stdout.read(128 * 1024 + 1)
            process.stdout.close()
            process.wait()
            try:
                completed = json.loads(data) if len(data) <= 128 * 1024 else {}
            except ValueError:
                completed = {}
            reservation.__exit__(None, None, None)
            with self.lock:
                if self.jobs[job_id]['cancelled']:
                    job['status'] = 'cancelled'
                elif completed.get('status') in ('succeeded', 'failed'):
                    job.update(completed)
                else:
                    job.update(status='failed', error={'stage': 'worker', 'message': 'Project worker exited without a result.'})
        Thread(target=collect, daemon=True).start()
        return {'job_id': job_id, 'status': 'queued'}

    def start_command(self, project, program, argv, cwd):
        if os.name != 'posix':
            raise ValueError('Agent command jobs require Linux or macOS.')
        python = Path(project['python'])
        if not python.is_file() or not os.access(python, os.X_OK):
            raise ValueError('Configured project Python environment is inaccessible; restore or reconfigure it.')
        executable = str(python) if program == 'python' else program
        # Do not inherit caller credentials, Python import paths or environment overrides.
        environment = {'PATH': str(python.parent) + os.pathsep + os.defpath,
                       'PYTHONNOUSERSITE': '1', 'PYTHONUNBUFFERED': '1'}
        job_id = uuid4().hex
        job = {'job_id': job_id, 'status': 'queued', 'operation': 'run_command'}
        with self.lock:
            if sum(item['job']['operation'] == 'run_command' for item in self.jobs.values()) >= 32:
                raise ValueError('Command job retention limit (32) reached; restart the agent to clear retained jobs.')
            try:
                process = subprocess.Popen([executable, *argv], cwd=cwd, env=environment,
                                           stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                           start_new_session=True)
            except OSError as error:
                raise ValueError(f'Cannot start executable {executable}: {error.strerror}. Check the configured command and environment.') from error
            item = {'project': project['root'], 'job': job, 'process': process, 'cancelled': False,
                    'output': {name: {'data': bytearray(), 'total_bytes': 0} for name in ('stdout', 'stderr')}}
            self.jobs[job_id] = item
            job['status'] = 'running'

        def drain(name, stream):
            with stream:
                while chunk := stream.read1(16384):
                    with self.lock:
                        output = item['output'][name]
                        output['total_bytes'] += len(chunk)
                        output['data'].extend(chunk[:max(0, 128 * 1024 - len(output['data']))])

        def collect():
            readers = [Thread(target=drain, args=(name, stream), daemon=True)
                       for name, stream in (('stdout', process.stdout), ('stderr', process.stderr))]
            for reader in readers:
                reader.start()
            exit_code = process.wait()
            for reader in readers:
                reader.join()
            with self.lock:
                job.update(exit_code=exit_code, status='cancelled' if item['cancelled'] else
                           ('succeeded' if exit_code == 0 else 'failed'))

        Thread(target=collect, daemon=True).start()
        return {'job_id': job_id, 'status': 'queued'}

    def output(self, project, job_id, stream='stdout', cursor=0, limit=16384):
        if stream not in ('stdout', 'stderr') or type(cursor) is not int or cursor < 0 or type(limit) is not int or not 1 <= limit <= 16384:
            raise ValueError('Use stdout/stderr, a nonnegative byte cursor, and a limit from 1 to 16384.')
        with self.lock:
            item = self.jobs.get(job_id) if isinstance(job_id, str) else None
            if item is None or item['project'] != project['root'] or 'output' not in item:
                raise ValueError('Unknown command job for this project.')
            output = item['output'][stream]
            if cursor > len(output['data']):
                raise ValueError('Cursor exceeds retained output.')
            data = bytes(output['data'][cursor:cursor + limit])
            return {'content': data.decode('utf-8', errors='replace'), 'next_cursor': cursor + len(data),
                    'total_bytes': output['total_bytes'], 'truncated': output['total_bytes'] > len(output['data']),
                    'complete': item['job']['status'] not in ('queued', 'running') and cursor + len(data) == len(output['data'])}

    def get(self, project, job_id, cancel=False):
        with self.lock:
            item = self.jobs.get(job_id)
            if item is None or item['project'] != project['root']:
                raise ValueError('Unknown job for this project.')
            cancelling = cancel and item['job']['status'] in ('queued', 'running')
            if cancelling:
                item['cancelled'] = True
        if cancelling:
            try:
                os.killpg(item['process'].pid, signal.SIGTERM)
                deadline = monotonic() + 2
                while monotonic() < deadline:
                    item['process'].poll()
                    try:
                        os.killpg(item['process'].pid, 0)
                    except ProcessLookupError:
                        break
                    sleep(.02)
                else:
                    os.killpg(item['process'].pid, signal.SIGKILL)
                item['process'].wait()
            except ProcessLookupError:
                pass
        with self.lock:
            return json.loads(json.dumps(item['job']))
