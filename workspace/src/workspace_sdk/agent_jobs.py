"""Agent-owned asynchronous jobs in the configured project interpreter."""

import json
import os
import signal
import subprocess
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
                try:
                    item['process'].wait(timeout=2)
                except subprocess.TimeoutExpired:
                    os.killpg(item['process'].pid, signal.SIGKILL)
                    item['process'].wait()
            except ProcessLookupError:
                pass
        with self.lock:
            return json.loads(json.dumps(item['job']))
