"""Shared flow recording and per-sheet execution exclusion for UI and agent."""

from contextlib import contextmanager
import os
import json
from time import perf_counter
from uuid import uuid4

from .history import RunHistory
from .node_settings import SettingsStore
from .project import relative_source
from .runner import run


class FlowBusy(RuntimeError):
    pass


@contextmanager
def sheet_lock(root):
    directory = root / '.workspace'
    directory.mkdir(mode=0o700 if os.name == 'posix' else 0o777, exist_ok=True)
    if directory.is_symlink():
        raise ValueError('Workspace state must not be a symbolic link.')
    path = directory / 'execution.lock'
    if path.is_symlink():
        raise ValueError('Workspace lock must not be a symbolic link.')
    with path.open('a+b') as stream:
        try:
            if os.name == 'posix':
                import fcntl

                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            else:
                import msvcrt

                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            raise FlowBusy('This sheet already has an active run or reload.') from None
        try:
            yield stream
        finally:
            if os.name == 'posix':
                fcntl.flock(stream, fcntl.LOCK_UN)
            else:
                try:
                    stream.seek(0)
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                except OSError:
                    pass


def sheet_busy(root):
    try:
        with sheet_lock(root):
            return False
    except FlowBusy:
        return True


def external_snapshot(root):
    path = root / '.workspace' / 'agent-run.json'
    try:
        if path.is_symlink():
            return None
        snapshot = json.loads(path.read_bytes())
    except (OSError, ValueError):
        return None
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get('events'), list):
        return None
    busy = sheet_busy(root)
    if snapshot.get('running') and not busy:
        snapshot = {**snapshot, 'running': False, 'events': [], 'status': 'stopped'}
    elif busy:
        # Completion is visible only after the worker has finished saving history.
        snapshot = {**snapshot, 'running': True}
    return snapshot


def clear_external_snapshot(root):
    (root / '.workspace' / 'agent-run.json').unlink(missing_ok=True)


class ExternalRun:
    """Persist only safe state needed for an independently open UI."""
    def __init__(self, root, job_id):
        self.root, self.job_id = root, job_id
        self.events = {}
        self.sequence = 0
        self.status = 'running'
        self.write()

    def record(self, event):
        if event.keys() & {'output_preview', 'live_frame', 'live_metrics', 'setting_change'}:
            return
        safe = {name: event[name] for name in ('run_id', 'step_id', 'status', 'time', 'duration_ms', 'blocked_by', 'iteration') if name in event}
        if event.get('file'):
            safe['file'] = relative_source(self.root, event['file'])
            safe['line'] = event.get('line')
        if 'diagnostic_error_type' in event:
            safe['error_type'] = event['diagnostic_error_type']
        # Shared state contains status only; request artifacts stay in agent memory.
        self.sequence += 1
        self.events[event.get('step_id'), event['status']] = (self.sequence, safe)
        if 'step_id' not in event:
            self.status = event['status']
        # Keep run boundaries and only the latest bounded set of node events.
        while len(self.events) > 256:
            oldest = next(key for key in self.events if key[0] is not None)
            del self.events[oldest]
        self.write()

    def write(self):
        data = {'job_id': self.job_id, 'status': self.status, 'running': self.status == 'running', 'events': sorted(self.events.values())}
        encoded = json.dumps(data, allow_nan=False).encode()
        while len(encoded) > 128 * 1024:
            index = next(i for i, (_, event) in enumerate(data['events']) if 'step_id' in event)
            del data['events'][index]
            encoded = json.dumps(data, allow_nan=False).encode()
        workspace_dir = self.root / '.workspace'
        workspace_dir.mkdir(parents=True, exist_ok=True)
        pending = workspace_dir / f'.agent-run.{uuid4().hex}.tmp'
        try:
            pending.write_bytes(encoded)
            pending.replace(workspace_dir / 'agent-run.json')
        finally:
            pending.unlink(missing_ok=True)


def run_recorded(flow, root, loaded_sources, observer, **options):
    events = []

    def record(event):
        if not event.keys() & {'output_preview', 'live_frame', 'live_metrics', 'setting_change'}:
            events.append(event)
        observer(event)

    started = perf_counter()
    result = run(flow, options.pop('inputs'), record, include_metadata=True,
                 settings=SettingsStore(root).snapshot(flow.steps), **options)
    try:
        RunHistory(root).save(events, loaded_sources, (perf_counter() - started) * 1000)
    except OSError:
        observer({'history_error': 'This run could not be saved to history.'})
    return result
