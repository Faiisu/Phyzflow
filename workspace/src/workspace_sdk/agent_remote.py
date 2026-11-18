"""TLS routing only; project access and durable state stay in the local agent."""

import argparse
from hmac import compare_digest
from http.client import HTTPException
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import signal
import ssl
import stat
from threading import Condition, Event, Semaphore, Thread
import time
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, ProxyHandler, Request, build_opener

from .agent import AgentClient

REMOTE_OPERATIONS = {'inspect_project', 'list_files', 'read_file', 'search_code',
                     'write_file', 'apply_patch', 'validate_flow', 'run_flow',
                     'run_command', 'get_job', 'read_job_output', 'cancel_job'}
POLL_SECONDS = 1
LEASE_SECONDS = 3
ROUTE_SECONDS = 3
MAX_PENDING = 32
MAX_MESSAGE = 1024 * 1024


def failure(code, message, request_id=None):
    return {'request_id': request_id, 'ok': False,
            'error': {'code': code, 'stage': 'routing', 'message': message}}


def private_token(filename):
    path = Path(filename)
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or info.st_uid != os.getuid():
        raise ValueError('Token files must be private and owned by the current user.')
    token = path.read_text().strip()
    if len(token) < 16 or not token.isascii() or any(c.isspace() for c in token):
        raise ValueError('Tokens must contain at least 16 non-whitespace ASCII characters.')
    return token


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *_):
        # Bearer credentials must never follow redirects to another origin or HTTP.
        return None


class RemoteClient:
    def __init__(self, url, token, *, cafile=None):
        parsed = urlsplit(url)
        if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ('', '/'):
            raise ValueError('Central URL must be an HTTPS origin without credentials.')
        self.url, self.token = url.rstrip('/'), token
        self.http = build_opener(ProxyHandler({}), NoRedirect(), HTTPSHandler(context=ssl.create_default_context(cafile=cafile)))

    def post(self, path, body):
        payload = json.dumps(body).encode()
        if len(payload) > MAX_MESSAGE:
            raise ValueError('Message exceeds the relay size limit.')
        request = Request(self.url + path, data=payload, headers={
            'Content-Type': 'application/json', 'Authorization': 'Bearer ' + self.token})
        try:
            response = self.http.open(request, timeout=12)
        except HTTPError as error:
            response = error
        with response:
            data = response.read(MAX_MESSAGE + 1)
            if len(data) > MAX_MESSAGE:
                raise ValueError('Relay response exceeds the size limit.')
            return json.loads(data)

    def list_devices(self):
        return self.post('/devices', {})

    def request(self, request_id, device_id, project_id, operation, arguments=None):
        return self.post('/request', {'device_id': device_id, 'request': {
            'request_id': request_id, 'project_id': project_id,
            'operation': operation, 'arguments': {} if arguments is None else arguments}})


class EdgeConnector:
    """Reconnect outbound routing to the authenticated, sole local owner."""
    def __init__(self, url, device_id, token, local_client, *, cafile=None):
        parsed = urlsplit(local_client.url)
        if (parsed.scheme != 'http' or parsed.hostname not in ('127.0.0.1', 'localhost', '::1') or
                parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ('', '/')):
            raise ValueError('Local agent must use a loopback HTTP address.')
        self.remote = RemoteClient(url, token, cafile=cafile)
        self.device_id, self.local = device_id, local_client

    def run(self, stop):
        identity = None
        delay = .25
        finished = Event()

        def heartbeat():
            while not finished.wait(.5):
                current = identity
                if current is not None:
                    try:
                        self.remote.post('/edge/heartbeat', current)
                    except (OSError, HTTPException, ValueError):
                        pass

        keeper = Thread(target=heartbeat)
        keeper.start()
        try:
            while not stop.is_set():
                try:
                    if identity is None:
                        registered = self.remote.post('/edge/register', {'device_id': self.device_id})
                        if not registered.get('ok'):
                            raise ValueError('Device registration rejected.')
                        identity = {'device_id': self.device_id, 'session': registered['result']['session']}
                    polled = self.remote.post('/edge/poll', identity)
                    if not polled.get('ok'):
                        if polled.get('error', {}).get('code') == 'session_lost':
                            identity = None
                            continue
                        # A replaced connector must not retake routing from its successor.
                        raise ValueError('Device session rejected.')
                    delay = .25
                    delivery = polled['result'].get('delivery')
                    if delivery is None:
                        continue
                    request = delivery['request']
                    if request['operation'] not in REMOTE_OPERATIONS:
                        response = failure('unsupported_operation', 'Remote operation is not enabled.', request['request_id'])
                    else:
                        try:
                            response = self.local.request(**request)
                        except (OSError, HTTPException, ValueError):
                            response = failure('delivery_unknown', 'Local acknowledgement lost; execution may have occurred.', request['request_id'])
                    # No automatic request replay: only callers may retry the original identity.
                    self.remote.post('/edge/reply', {**identity, 'delivery_id': delivery['delivery_id'], 'response': response})
                except (OSError, HTTPException):
                    if stop.wait(delay):
                        break
                    delay = min(delay * 2, 2)
        finally:
            finished.set()
            keeper.join()
            if identity is not None:
                try:
                    self.remote.post('/edge/disconnect', identity)
                except (OSError, HTTPException, ValueError):
                    pass


def create_router_server(config, port=0, host='127.0.0.1'):
    devices = {name: {'token': private_token(value['token_file']), 'session': None, 'seen': 0, 'polling': False}
               for name, value in config['devices'].items()}
    clients = {name: {'token': private_token(value['token_file']), 'devices': set(value['devices'])}
               for name, value in config['clients'].items()}
    tokens = [value['token'] for value in [*devices.values(), *clients.values()]]
    if len(tokens) != len(set(tokens)) or any(value['devices'] - devices.keys() for value in clients.values()):
        raise ValueError('Use distinct client/device tokens and configured device grants.')
    condition = Condition()
    pending = {}

    def invalidate(device_id):
        for item in pending.values():
            if item['device_id'] == device_id and item['response'] is None:
                code = 'delivery_unknown' if item['delivered'] else 'unavailable'
                item['response'] = failure(code, 'Device disconnected; delivery may have occurred.' if item['delivered'] else 'Device is unavailable.', item['request']['request_id'])
        condition.notify_all()

    def route(path, body, auth):
        with condition:
            if path.startswith('/edge/'):
                device_id = body.get('device_id')
                if not isinstance(device_id, str) or device_id not in devices or not compare_digest(auth, ('Bearer ' + devices[device_id]['token']).encode()):
                    return failure('unauthorized', 'Device authentication required.'), 401
                device = devices[device_id]
                if path == '/edge/register':
                    invalidate(device_id)
                    device.update(session=secrets.token_urlsafe(32), seen=time.monotonic(), polling=False)
                    return {'ok': True, 'result': {'session': device['session']}}, 200
                session = body.get('session')
                if not device['session']:
                    return failure('session_lost', 'Device session lost; register again.'), 409
                if not isinstance(session, str) or not compare_digest(session, device['session']):
                    return failure('stale_session', 'Register a new device session.'), 409
                if path == '/edge/heartbeat':
                    device['seen'] = time.monotonic()
                    return {'ok': True, 'result': {}}, 200
                if path == '/edge/disconnect':
                    device.update(session=None, seen=0)
                    invalidate(device_id)
                    return {'ok': True, 'result': {}}, 200
                if path == '/edge/poll':
                    if device['polling']:
                        return failure('session_busy', 'Only one poll per device session is allowed.'), 409
                    device['polling'] = True
                    deadline = time.monotonic() + POLL_SECONDS
                    try:
                        while True:
                            if device['session'] != session:
                                return failure('stale_session', 'Device session replaced.'), 409
                            device['seen'] = time.monotonic()
                            for key, item in pending.items():
                                if (item['device_id'] == device_id and item['response'] is None and
                                        not item['delivered'] and item['deadline'] > time.monotonic()):
                                    item['delivered'] = True
                                    return {'ok': True, 'result': {'delivery': {'delivery_id': key, 'request': item['request']}}}, 200
                            remaining = deadline - time.monotonic()
                            if remaining <= 0:
                                return {'ok': True, 'result': {'delivery': None}}, 200
                            condition.wait(remaining)
                    finally:
                        if device['session'] == session:
                            device['polling'] = False
                if path == '/edge/reply':
                    key, response = body.get('delivery_id'), body.get('response')
                    item = pending.get(key) if isinstance(key, str) else None
                    if (item is None or item['device_id'] != device_id or item['session'] != session or
                            not item['delivered'] or item['response'] is not None or item['deadline'] <= time.monotonic()):
                        return failure('expired_delivery', 'Delivery is no longer awaiting a reply.'), 409
                    if not isinstance(response, dict) or response.get('request_id') != item['request']['request_id'] or type(response.get('ok')) is not bool:
                        return failure('invalid_request', 'Reply must match the originating request.'), 400
                    item['response'] = response
                    device['seen'] = time.monotonic()
                    condition.notify_all()
                    return {'ok': True, 'result': {}}, 200
                return failure('invalid_request', 'Unknown endpoint.'), 404

            client = next((value for value in clients.values() if compare_digest(auth, ('Bearer ' + value['token']).encode())), None)
            if client is None:
                return failure('unauthorized', 'Client authentication required.'), 401
            if path == '/devices':
                return {'ok': True, 'result': {'devices': [
                    {'device_id': name, 'status': 'connected' if devices[name]['session'] and time.monotonic() - devices[name]['seen'] < LEASE_SECONDS else 'unavailable'}
                    for name in sorted(client['devices'])]}}, 200
            if path != '/request':
                return failure('invalid_request', 'Unknown endpoint.'), 404
            device_id, request = body.get('device_id'), body.get('request')
            if not isinstance(device_id, str) or device_id not in client['devices']:
                return failure('denied', 'Device access is not authorized.'), 403
            if (not isinstance(request, dict) or set(request) != {'request_id', 'project_id', 'operation', 'arguments'} or
                    not isinstance(request['request_id'], str) or not 1 <= len(request['request_id']) <= 128 or
                    not isinstance(request['project_id'], str) or not isinstance(request['arguments'], dict) or
                    not isinstance(request['operation'], str) or len(json.dumps(request).encode()) > 65536):
                return failure('invalid_request', 'Expected a bounded agent request.'), 400
            request_id = request['request_id']
            if request['operation'] not in REMOTE_OPERATIONS:
                return failure('unsupported_operation', 'Remote operation is not enabled.', request_id), 400
            device = devices[device_id]
            if not device['session'] or time.monotonic() - device['seen'] >= LEASE_SECONDS:
                return failure('unavailable', 'Device is unavailable.', request_id), 200
            if len(pending) >= MAX_PENDING:
                return failure('busy', 'Router request limit reached.', request_id), 503
            key = secrets.token_urlsafe(24)
            item = {'device_id': device_id, 'session': device['session'], 'request': request,
                    'deadline': time.monotonic() + ROUTE_SECONDS, 'delivered': False, 'response': None}
            pending[key] = item
            condition.notify_all()
            try:
                while item['response'] is None:
                    remaining = item['deadline'] - time.monotonic()
                    if remaining <= 0:
                        code = 'delivery_unknown' if item['delivered'] else 'unavailable'
                        return failure(code, 'No reply received; delivery may have occurred.' if item['delivered'] else 'Device did not accept the request.', request_id), 200
                    condition.wait(remaining)
                return item['response'], 200
            finally:
                del pending[key]

    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            self.request.settimeout(12)
            super().setup()

        def log_message(self, *_):
            pass

        def handle(self):
            try:
                super().handle()
            except OSError:
                # Failed TLS handshakes/disconnects carry no accepted agent result.
                pass

        def do_POST(self):
            try:
                size = int(self.headers.get('Content-Length', '0'))
                if (self.headers.get('Origin') is not None or self.headers.get_content_type() != 'application/json' or
                        not 0 < size <= MAX_MESSAGE):
                    raise ValueError
                body = json.loads(self.rfile.read(size))
                if not isinstance(body, dict):
                    raise ValueError
                result, status = route(self.path, body, self.headers.get('Authorization', '').encode())
            except (ValueError, UnicodeError):
                result, status = failure('invalid_request', 'Send a bounded JSON object.'), 400
            payload = json.dumps(result).encode()
            if len(payload) > MAX_MESSAGE:
                payload = json.dumps(failure('response_too_large', 'Device response exceeded the relay limit.')).encode()
                status = 502
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(payload)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            try:
                self.wfile.write(payload)
            except OSError:
                pass

    class RouterServer(ThreadingHTTPServer):
        daemon_threads = False
        request_queue_size = 64
        slots = Semaphore(64)

        def process_request(self, request, address):
            if not self.slots.acquire(blocking=False):
                self.shutdown_request(request)
                return
            try:
                super().process_request(request, address)
            except BaseException:
                self.slots.release()
                raise

        def process_request_thread(self, request, address):
            try:
                super().process_request_thread(request, address)
            finally:
                self.slots.release()

    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(config['cert_file'], config['key_file'])
    server = RouterServer((host, port), Handler)
    try:
        server.socket = context.wrap_socket(server.socket, server_side=True, do_handshake_on_connect=False)
        return server
    except BaseException:
        server.server_close()
        raise


def main():
    parser = argparse.ArgumentParser(description='Route Workspace requests over authenticated TLS.')
    parser.add_argument('mode', choices=('central', 'connect'))
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=0)
    args = parser.parse_args()
    stop = Event()
    previous = signal.signal(signal.SIGTERM, lambda *_: stop.set())
    try:
        config = json.loads(args.config.read_text())
        if args.mode == 'central':
            with create_router_server(config, args.port, args.host) as server:
                thread = Thread(target=server.serve_forever)
                thread.start()
                print(f'Workspace central: https://{args.host}:{server.server_port}', flush=True)
                try:
                    stop.wait()
                finally:
                    server.shutdown()
                    thread.join()
        else:
            local = AgentClient(config['local_url'], private_token(config['local_token_file']))
            connector = EdgeConnector(config['central_url'], config['device_id'], private_token(config['device_token_file']), local, cafile=config.get('ca_file'))
            connector.run(stop)
    except KeyboardInterrupt:
        stop.set()
    except (OSError, ValueError, KeyError, TypeError) as error:
        parser.exit(1, f'Remote agent failed: {type(error).__name__}. Check private configuration and connectivity.\n')
    finally:
        signal.signal(signal.SIGTERM, previous)


if __name__ == '__main__':
    main()
