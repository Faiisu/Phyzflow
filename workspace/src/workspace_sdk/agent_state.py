"""Private, instance-local durable request identities and bounded job artifacts."""
import base64
import fcntl
import json
import os
from pathlib import Path
import sqlite3
from threading import RLock


class AgentState:
    def __init__(self, directory):
        self.directory = Path(directory).expanduser().absolute()
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = self.directory.lstat()
        if self.directory.is_symlink() or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ValueError('Agent state directory must be private (0700) and owned by the current user.')
        self.owner = (self.directory / 'owner.lock').open('a+b')
        os.chmod(self.owner.name, 0o600)
        try:
            fcntl.flock(self.owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.owner.close()
            raise ValueError('Another agent is already using this state directory.') from None
        database = self.directory / 'state.sqlite3'
        fd = os.open(database, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        os.close(fd)
        os.chmod(database, 0o600)
        self.lock = RLock()
        self.db = sqlite3.connect(database, check_same_thread=False)
        # DELETE journals inherit the private database/directory permissions.
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.execute('CREATE TABLE IF NOT EXISTS requests (project TEXT, identity TEXT, fingerprint TEXT, response TEXT, PRIMARY KEY(project, identity))')
        self.db.execute('CREATE TABLE IF NOT EXISTS jobs (identity TEXT PRIMARY KEY, data TEXT)')
        self.db.commit()
        with self.db:
            for project, identity in self.db.execute('SELECT project, identity FROM requests WHERE response IS NULL').fetchall():
                response = {'request_id': identity, 'ok': False, 'error': {
                    'code': 'interrupted', 'stage': 'recovery',
                    'message': 'Agent stopped before recording the outcome. The mutation may have taken effect; inspect before issuing a new request.'}}
                self.db.execute('UPDATE requests SET response=? WHERE project=? AND identity=?', (json.dumps(response), project, identity))

    def lookup(self, project, identity, fingerprint):
        with self.lock:
            row = self.db.execute('SELECT fingerprint, response FROM requests WHERE project=? AND identity=?', (project, identity)).fetchone()
            if row:
                if row[0] != fingerprint:
                    return {'request_id': identity, 'ok': False, 'error': {'code': 'request_conflict', 'stage': 'request', 'message': 'request_id was already accepted with different content.'}}
                return json.loads(row[1]) if row[1] else {'request_id': identity, 'ok': False, 'error': {'code': 'interrupted', 'stage': 'recovery', 'message': 'Accepted mutation has an uncertain outcome; inspect before issuing a new request.'}}
            with self.db:
                self.db.execute('INSERT INTO requests VALUES (?, ?, ?, NULL)', (project, identity, fingerprint))
            return None

    def finish(self, key, response):
        with self.lock, self.db:
            self.db.execute('UPDATE requests SET response=? WHERE project=? AND identity=?', (json.dumps(response), *key))

    def save_job(self, item, key=None):
        data = {'project': str(item['project']), 'job': item['job']}
        if 'output' in item:
            data['output'] = {name: {'data': base64.b64encode(value['data']).decode('ascii'), 'total_bytes': value['total_bytes']}
                              for name, value in item['output'].items()}
        with self.lock, self.db:
            self.db.execute('INSERT OR REPLACE INTO jobs VALUES (?, ?)', (item['job']['job_id'], json.dumps(data)))
            if key:
                response = {'request_id': key[1], 'ok': True, 'result': {'job_id': item['job']['job_id'], 'status': 'queued'}}
                self.db.execute('UPDATE requests SET response=? WHERE project=? AND identity=?', (json.dumps(response), *key))

    def load_jobs(self):
        jobs = {}
        with self.lock:
            rows = self.db.execute('SELECT identity, data FROM jobs').fetchall()
        for identity, data in rows:
            item = json.loads(data)
            item['project'] = Path(item['project'])
            item['cancelled'] = False
            if 'output' in item:
                for output in item['output'].values():
                    output['data'] = bytearray(base64.b64decode(output['data']))
            if item['job']['status'] in ('queued', 'running'):
                item['job'].update(status='interrupted', error={'stage': 'recovery', 'message': 'Agent stopped during this job. Work was not retried; inspect any external effects.'})
                self.save_job(item)
            jobs[identity] = item
        return jobs

    def close(self):
        self.db.close()
        self.owner.close()
