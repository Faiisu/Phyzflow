"""Agent-owned asynchronous jobs in the configured project interpreter."""

from http.client import HTTPConnection, HTTPException
import json
import os
import signal
import subprocess
import sys
from pathlib import Path
from time import monotonic, sleep
from threading import Lock, Thread
from uuid import uuid4

from .execution import ExternalRun, FlowBusy, sheet_lock


def project_environment(project):
    """Start every project process without the agent account's ambient secrets."""
    if os.name == 'posix':
        return {'PATH': str(Path(project['python']).parent) + os.pathsep + os.defpath,
                'PYTHONNOUSERSITE': '1', 'PYTHONUNBUFFERED': '1'}
    system_root = os.environ.get('SYSTEMROOT', r'C:\Windows')
    environment = {
        'PATH': os.pathsep.join([str(Path(project['python']).parent), os.path.join(system_root, 'System32'), os.defpath]),
        'PYTHONNOUSERSITE': '1',
        'PYTHONUNBUFFERED': '1',
    }
    for key in ('SYSTEMDRIVE', 'SYSTEMROOT', 'COMSPEC', 'PATHEXT', 'TEMP', 'TMP', 'USERPROFILE', 'APPDATA', 'LOCALAPPDATA'):
        if key in os.environ:
            environment[key] = os.environ[key]
    return environment


class AgentJobs:
    def __init__(self, state=None):
        self.lock = Lock()
        self.state = state
        self.jobs = state.load_jobs() if state else {}
        self.collectors = []
        self.ui_controller = None

    def attach_ui(self, controller):
        """Route run_flow/cancel through the active Workspace UI FlowSheet worker."""
        self.ui_controller = controller

    def save(self, item, request_key=None):
        if self.state:
            self.state.save_job(item, request_key)

    def spawn(self, argv, *, inherited=-1, **kwargs):
        if os.name != 'posix':
            return subprocess.Popen(argv, creationflags=getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0), **kwargs)
        if not self.state:
            return subprocess.Popen(argv, start_new_session=True,
                                    pass_fds=() if inherited < 0 else (inherited,), **kwargs)
        reader, writer = os.pipe()
        owner = self.state.owner.fileno()
        try:
            process = subprocess.Popen([sys.executable, '-I', str(Path(__file__).with_name('agent_supervisor.py')),
                                        str(reader), str(owner), str(inherited), *argv],
                                       start_new_session=True,
                                       pass_fds=(reader, owner) + (() if inherited < 0 else (inherited,)), **kwargs)
        except BaseException:
            os.close(writer)
            raise
        finally:
            os.close(reader)
        process.agent_lifeline = writer
        return process

    def finish_process(self, process):
        with self.lock:
            writer = getattr(process, 'agent_lifeline', None)
            if writer is not None:
                process.agent_lifeline = None
                os.close(writer)

    def launch_collector(self, collect):
        thread = Thread(target=collect, daemon=True)
        self.collectors.append(thread)
        thread.start()

    def close(self):
        with self.lock:
            active = [(item['project'], identity) for identity, item in self.jobs.items()
                      if item['job']['status'] in ('queued', 'running')]
        for root, identity in active:
            self.get({'root': root}, identity, cancel=True)
        for thread in self.collectors:
            thread.join()

    def _start_ui_run(self, project, sheet, inputs, request_key=None):
        flow_sheet, worker_url = self.ui_controller.ensure_sheet_worker(sheet)
        job_id = uuid4().hex
        job = {'job_id': job_id, 'status': 'queued', 'operation': 'run_flow'}
        item = {'project': project['root'], 'job': job, 'cancelled': False, 'ui_sheet': flow_sheet}
        body = json.dumps(inputs if isinstance(inputs, dict) else {}).encode()
        conn = HTTPConnection(worker_url.removeprefix('http://'), timeout=5)
        try:
            conn.request('POST', '/run', body=body, headers={'Content-Type': 'application/json'})
            response = conn.getresponse()
            if response.status == 409:
                raise FlowBusy(response.reason or 'Another flow run is active on this sheet.')
            if response.status != 200:
                raise RuntimeError(f'Flow worker returned HTTP {response.status}.')
            if conn.sock is not None:
                conn.sock.settimeout(None)
        except BaseException:
            conn.close()
            raise

        with self.lock:
            item['ui_connection'] = conn
            self.jobs[job_id] = item
            job['status'] = 'running'
            self.save(item, request_key)

        def collect():
            run_id = None
            final_result = None
            final_error = None
            has_result = False
            try:
                while True:
                    raw = response.readline()
                    if not raw:
                        break
                    try:
                        message = json.loads(raw)
                    except ValueError:
                        continue
                    if isinstance(message, dict):
                        run_id = message.get('run_id', run_id)
                        if 'result' in message:
                            has_result = True
                            final_result = message['result']
                        if 'error' in message:
                            final_error = message['error']
            except (OSError, HTTPException):
                pass
            finally:
                try:
                    response.close()
                except OSError:
                    pass
                conn.close()

            with self.lock:
                if item['cancelled']:
                    job['status'] = 'cancelled'
                elif final_error is not None:
                    job.update(
                        status='failed',
                        error=final_error if isinstance(final_error, dict) else {'stage': 'run', 'message': str(final_error)},
                    )
                    if run_id:
                        job['result'] = {'run_id': run_id}
                elif has_result:
                    job.update(status='succeeded', result={'run_id': run_id, 'output': final_result})
                else:
                    job.update(status='failed', error={'stage': 'worker', 'message': 'Flow stream ended without a result.'})
                self.save(item)

        self.launch_collector(collect)
        return {'job_id': job_id, 'status': 'queued'}

    def start(self, project, operation, sheet, inputs, request_key=None):
        if operation == 'run_flow' and self.ui_controller is not None:
            return self._start_ui_run(project, sheet, inputs, request_key)
        reservation = sheet_lock(sheet)
        descriptor = reservation.__enter__()
        job_id = uuid4().hex
        job = {'job_id': job_id, 'status': 'queued', 'operation': operation}
        item = {'project': project['root'], 'job': job, 'cancelled': False}
        with self.lock:
            self.jobs[job_id] = item
            self.save(item, request_key)
        try:
            if operation == 'run_flow':
                ExternalRun(sheet, job_id)
            environment = project_environment(project)
            environment.update(project.get('inference_environment', {}))
            process = self.spawn([project['python'], '-m', 'workspace_sdk.agent_worker'], cwd=sheet, env=environment,
                                 stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                 inherited=-1 if descriptor is None else descriptor.fileno())
            process.stdin.write((json.dumps({'job_id': job_id, 'operation': operation, 'input': inputs}) + '\n').encode())
            process.stdin.close()
        except BaseException:
            if 'process' in locals():
                if hasattr(process, 'agent_lifeline'):
                    self.finish_process(process)
                elif os.name == 'posix':
                    os.killpg(process.pid, signal.SIGKILL)
                else:
                    process.kill()
                process.wait()
            reservation.__exit__(None, None, None)
            with self.lock:
                job.update(status='failed', error={'stage': 'worker', 'message': 'Could not start project worker.'})
                self.save(item)
            raise
        with self.lock:
            item['process'] = process
        def collect():
            with self.lock:
                job['status'] = 'running'
            data = process.stdout.read(128 * 1024 + 1)
            process.stdout.close()
            process.wait()
            self.finish_process(process)
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
                self.save(item)
        self.launch_collector(collect)
        return {'job_id': job_id, 'status': 'queued'}

    def start_command(self, project, program, argv, cwd, request_key=None):
        python = Path(project['python'])
        if not python.is_file() or not os.access(python, os.X_OK):
            raise ValueError('Configured project Python environment is inaccessible; restore or reconfigure it.')
        executable = str(python) if program == 'python' else program
        # Do not inherit caller credentials, Python import paths or environment overrides.
        environment = project_environment(project)
        if not Path(executable).is_file() or not os.access(executable, os.X_OK):
            raise ValueError(f'Cannot start executable {executable}: missing or not executable. Check the configured command and environment.')
        job_id = uuid4().hex
        job = {'job_id': job_id, 'status': 'queued', 'operation': 'run_command'}
        with self.lock:
            item = {'project': project['root'], 'job': job, 'cancelled': False,
                    'output': {name: {'data': bytearray(), 'total_bytes': 0} for name in ('stdout', 'stderr')}}
            self.jobs[job_id] = item
            self.save(item, request_key)
            try:
                process = self.spawn([executable, *argv], cwd=cwd, env=environment,
                                     stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            except OSError as error:
                job.update(status='failed', error={'stage': 'command', 'message': 'Could not start executable.'})
                self.save(item)
                raise ValueError(f'Cannot start executable {executable}: {error.strerror}. Check the configured command and environment.') from error
            item['process'] = process
            job['status'] = 'running'
            self.save(item)

        def drain(name, stream):
            with stream:
                while chunk := stream.read1(16384):
                    with self.lock:
                        output = item['output'][name]
                        output['total_bytes'] += len(chunk)
                        output['data'].extend(chunk[:max(0, 128 * 1024 - len(output['data']))])
                        self.save(item)

        def collect():
            readers = [Thread(target=drain, args=(name, stream), daemon=True)
                       for name, stream in (('stdout', process.stdout), ('stderr', process.stderr))]
            for reader in readers:
                reader.start()
            exit_code = process.wait()
            self.finish_process(process)
            for reader in readers:
                reader.join()
            with self.lock:
                job.update(exit_code=exit_code, status='cancelled' if item['cancelled'] else
                           ('succeeded' if exit_code == 0 else 'failed'))
                self.save(item)

        self.launch_collector(collect)
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
        if cancelling and 'ui_sheet' in item:
            conn = item.get('ui_connection')
            if conn is not None:
                try:
                    conn.close()
                except OSError:
                    pass
            self.ui_controller.stop_sheet_run(item['ui_sheet'])
            with self.lock:
                item['job']['status'] = 'cancelled'
                self.save(item)
                return json.loads(json.dumps(item['job']))
        if cancelling and hasattr(item['process'], 'agent_lifeline'):
            # The supervisor owns a live group leader and escalation; never signal
            # a guardian PID that another collector may already have reaped.
            self.finish_process(item['process'])
            item['process'].wait()
        elif cancelling and os.name == 'posix':
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
        elif cancelling:
            subprocess.run(['taskkill', '/F', '/T', '/PID', str(item['process'].pid)],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            item['process'].wait()
        with self.lock:
            return json.loads(json.dumps(item['job']))
