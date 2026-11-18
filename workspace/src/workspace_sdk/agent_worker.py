"""One project-interpreter process per validation or flow job."""

from contextlib import redirect_stdout
from hashlib import sha256
import json
from pathlib import Path
import sys
import traceback

from .execution import ExternalRun, run_recorded
from .history import RunHistory
from .project import load_project, ValidationError


def main():
    request = json.loads(sys.stdin.readline())
    channel = sys.stdout
    root = Path.cwd()
    state = ExternalRun(root, request['job_id']) if request['operation'] == 'run_flow' else None
    try:
        with redirect_stdout(sys.stderr):
            sources = {}
            with load_project(root, sources) as flow:
                if state is None:
                    result = {'checked': True}
                else:
                    warnings = {}
                    def observe(event):
                        if 'history_error' in event:
                            warnings.update(event)
                        else:
                            state.record(event)
                    execution = run_recorded(flow, root, sources, observe, inputs=request['input'])
                    record = RunHistory(root).read(execution.id) or {}
                    source_version = sha256(json.dumps(sorted((str(path.relative_to(root)), digest) for path, digest in sources.items())).encode()).hexdigest()
                    result = {'run_id': execution.id, 'source_version': record.get('source_version', source_version),
                              'output': execution.output, 'steps': record.get('steps', []), **warnings}
                    status = execution.status
        message = {'status': status if state is not None else 'succeeded', 'result': result}
    except Exception as error:
        message = {'status': 'failed', 'error': {'stage': 'validation' if isinstance(error, ValidationError) else 'import',
                    'type': type(error).__name__, 'message': str(error), 'traceback': traceback.format_exc()}}
        if state is not None:
            state.status = 'failed'
            state.write()
    try:
        encoded = json.dumps(message, allow_nan=False)
        if len(encoded.encode()) > 128 * 1024:
            raise ValueError('Flow result exceeds 128 KiB; publish a bounded result.')
    except (TypeError, ValueError) as error:
        encoded = json.dumps({'status': 'failed', 'error': {'stage': 'result', 'message': str(error)}})
    channel.write(encoded + '\n')
    channel.flush()


if __name__ == '__main__':
    main()
