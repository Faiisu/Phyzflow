"""Local, authenticated development access to explicitly configured projects."""

import argparse
from hashlib import sha256
from hmac import compare_digest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import stat
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

from .sheets import discover_flows

MAX_READ_BYTES = 64 * 1024
MAX_ENTRIES = 500
MAX_SCAN_FILES = 2000
MAX_SEARCH_MATCHES = 100
SKIP_DIRECTORIES = {'.git', '.venv', '.workspace', '__pycache__'}


class AgentError(ValueError):
    def __init__(self, code, message, stage='request'):
        super().__init__(message)
        self.code, self.stage = code, stage


class AgentClient:
    def __init__(self, url: str, token: str):
        self.url, self.token = url.rstrip('/'), token
        self.http = build_opener(ProxyHandler({}))

    def request(self, request_id, project_id, operation, arguments=None):
        request = Request(self.url + '/request', data=json.dumps({
            'request_id': request_id, 'project_id': project_id,
            'operation': operation, 'arguments': arguments or {},
        }).encode(), headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + self.token})
        try:
            response = self.http.open(request, timeout=10)
        except HTTPError as error:
            response = error
        with response:
            return json.load(response)


def project_path(root: Path, value: object) -> Path:
    if not isinstance(value, str) or '\x00' in value:
        raise AgentError('invalid_path', 'Expected a relative project path.', 'filesystem')
    path = Path(value)
    if path.is_absolute() or '..' in path.parts:
        raise AgentError('denied', 'Path must stay inside the project.', 'filesystem')
    candidate = root / path
    # Reject all symlinks, including in-project links, for a stable edit boundary.
    current = root
    for part in path.parts:
        current = current / part
        if current.is_symlink():
            raise AgentError('denied', 'Symbolic links are not accessible.', 'filesystem')
    if not candidate.resolve().is_relative_to(root):
        raise AgentError('denied', 'Path must stay inside the project.', 'filesystem')
    return candidate


class WorkspaceAgent:
    def __init__(self, projects):
        if not isinstance(projects, dict) or not projects:
            raise ValueError('Configure at least one project.')
        self.projects = {}
        for name, project in projects.items():
            root = Path(project['root']).expanduser().resolve(strict=True)
            python = Path(project['python']).expanduser().absolute()
            if not root.is_dir() or not python.is_file() or not os.access(python, os.X_OK):
                raise ValueError('Project root and executable Python interpreter are required.')
            self.projects[name] = {'root': root, 'python': str(python)}

    def dispatch(self, request):
        request_id = request.get('request_id') if isinstance(request, dict) else None
        try:
            if not isinstance(request, dict) or set(request) != {'request_id', 'project_id', 'operation', 'arguments'}:
                raise AgentError('invalid_request', 'Expected request_id, project_id, operation, and arguments.')
            if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
                raise AgentError('invalid_request', 'request_id must contain 1 to 128 characters.')
            project_id, operation, arguments = (request[k] for k in ('project_id', 'operation', 'arguments'))
            if not isinstance(project_id, str) or project_id not in self.projects:
                raise AgentError('unknown_project', 'Project is not configured.', 'authorization')
            if not isinstance(operation, str) or not isinstance(arguments, dict):
                raise AgentError('invalid_request', 'Expected an operation name and arguments object.')
            result = self.execute(self.projects[project_id], operation, arguments)
            return {'request_id': request_id, 'ok': True, 'result': result}
        except AgentError as error:
            return {'request_id': request_id, 'ok': False, 'error': {'code': error.code, 'stage': error.stage, 'message': str(error)}}
        except (OSError, UnicodeError, ValueError, RuntimeError):
            return {'request_id': request_id, 'ok': False, 'error': {'code': 'unavailable', 'stage': 'filesystem', 'message': 'Could not access the requested project resource.'}}

    def execute(self, project, operation, arguments):
        root = project['root']
        allowed = {'inspect_project': set(), 'list_files': {'path'}, 'read_file': {'path'}, 'search_code': {'query'}}
        if operation not in allowed:
            raise AgentError('unsupported_operation', 'Unsupported operation.')
        if set(arguments) - allowed[operation]:
            raise AgentError('invalid_arguments', 'Unexpected operation arguments.')
        if operation == 'inspect_project':
            sheets = [{'name': name, 'file': (path / 'flow.py').relative_to(root).as_posix()}
                      for name, path in discover_flows(root)
                      if not path.is_symlink() and not (path / 'flow.py').is_symlink() and path.resolve().is_relative_to(root)]
            return {'python': project['python'], 'sheets': sheets}
        if operation == 'list_files':
            path = project_path(root, arguments.get('path', '.'))
            entries = []
            with os.scandir(path) as children:
                for child in children:
                    if child.name in SKIP_DIRECTORIES or child.is_symlink():
                        continue
                    if len(entries) == MAX_ENTRIES:
                        return {'entries': sorted(entries, key=lambda item: item['path']), 'truncated': True}
                    entries.append({'path': (path / child.name).relative_to(root).as_posix(),
                                    'kind': 'directory' if child.is_dir(follow_symlinks=False) else 'file'})
            return {'entries': sorted(entries, key=lambda item: item['path']), 'truncated': False}
        if operation == 'read_file':
            path = project_path(root, arguments.get('path'))
            if not path.is_file():
                raise AgentError('unavailable', 'Only regular files can be read.', 'filesystem')
            with path.open('rb') as stream:
                if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                    raise AgentError('denied', 'Only regular files can be read.', 'filesystem')
                data = stream.read(MAX_READ_BYTES + 1)
            truncated = len(data) > MAX_READ_BYTES
            return {'content': data[:MAX_READ_BYTES].decode('utf-8', errors='replace'),
                    'revision': None if truncated else sha256(data).hexdigest(), 'truncated': truncated}
        query = arguments.get('query')
        if not isinstance(query, str) or not 1 <= len(query) <= 512:
            raise AgentError('invalid_arguments', 'query must contain 1 to 512 characters.')
        matches, scanned = [], 0
        pending = [root]
        while pending:
            with os.scandir(pending.pop()) as children:
                for child in children:
                    if scanned >= MAX_SCAN_FILES:
                        return {'matches': matches, 'truncated': True}
                    scanned += 1
                    if child.is_symlink() or child.name in SKIP_DIRECTORIES:
                        continue
                    path = Path(child.path)
                    if child.is_dir(follow_symlinks=False):
                        pending.append(path)
                        continue
                    if not child.is_file(follow_symlinks=False) or path.suffix not in {'.py', '.json', '.toml', '.js', '.html', '.css', '.md', '.txt'}:
                        continue
                    relative = path.relative_to(root).as_posix()
                    read = self.execute(project, 'read_file', {'path': relative})
                    line_truncated = False
                    for line, text in enumerate(read['content'].splitlines(), 1):
                        if query in text:
                            line_truncated |= len(text) > 1024
                            matches.append({'path': relative, 'line': line, 'text': text[:1024]})
                            if len(matches) >= MAX_SEARCH_MATCHES:
                                return {'matches': matches, 'truncated': True}
                    if read['truncated'] or line_truncated:
                        return {'matches': matches, 'truncated': True}
        return {'matches': matches, 'truncated': False}


def create_agent_server(config: dict, port=0):
    token_path = Path(config['token_file']).expanduser()
    info = token_path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or info.st_uid != os.getuid():
        raise ValueError('The token file must be owned by the current user and accessible only to that user.')
    token = token_path.read_text().strip()
    if len(token) < 16:
        raise ValueError('The agent token must contain at least 16 characters.')
    agent = WorkspaceAgent(config['projects'])

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def reply(self, data, status=200):
            payload = json.dumps(data).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(payload)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(payload)

        def do_POST(self):
            if not compare_digest(self.headers.get('Authorization', '').encode(), ('Bearer ' + token).encode()):
                self.reply({'ok': False, 'error': {'code': 'unauthorized', 'stage': 'authorization', 'message': 'Authentication required.'}}, 401)
                return
            if self.path != '/request' or self.headers.get('Origin') is not None:
                self.reply({'ok': False, 'error': {'code': 'denied', 'stage': 'transport', 'message': 'Unsupported request.'}}, 403)
                return
            try:
                size = int(self.headers.get('Content-Length', '0'))
                if not 0 < size <= 65536 or self.headers.get_content_type() != 'application/json':
                    raise ValueError
                data = json.loads(self.rfile.read(size))
            except (ValueError, UnicodeError):
                self.reply({'ok': False, 'error': {'code': 'invalid_request', 'stage': 'transport', 'message': 'Send a JSON request up to 64 KiB.'}}, 400)
                return
            self.reply(agent.dispatch(data))

    return ThreadingHTTPServer(('127.0.0.1', port), Handler)


def main():
    parser = argparse.ArgumentParser(description='Serve local Workspace development requests.')
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--port', type=int, default=0)
    args = parser.parse_args()
    try:
        config = json.loads(args.config.read_text())
        with create_agent_server(config, args.port) as server:
            print(f'Workspace agent: http://127.0.0.1:{server.server_port}', flush=True)
            server.serve_forever()
    except KeyboardInterrupt:
        return 0
    except (OSError, ValueError, KeyError, TypeError) as error:
        parser.exit(1, f'Agent startup failed: {error}\n')


if __name__ == '__main__':
    main()
