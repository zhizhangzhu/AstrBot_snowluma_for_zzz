"""Small, isolated operational records; never store tool arguments or secrets."""
import json
import sqlite3
import threading
import time
from contextlib import closing


class Records:
    def __init__(self, path):
        self.path = path
        self.lock = threading.RLock()
        with self.lock, closing(self.connect()) as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS actions (
              id TEXT PRIMARY KEY, scope TEXT NOT NULL, umo TEXT NOT NULL,
              ts REAL NOT NULL, kind TEXT NOT NULL, label TEXT NOT NULL, outcome TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS actions_scope ON actions(scope,umo,ts);
            CREATE TABLE IF NOT EXISTS health (
              scope TEXT NOT NULL, capability TEXT NOT NULL, ts REAL NOT NULL,
              status TEXT NOT NULL, reason TEXT NOT NULL, PRIMARY KEY(scope,capability));
            CREATE TABLE IF NOT EXISTS snapshots (
              scope TEXT NOT NULL, umo TEXT NOT NULL, ts REAL NOT NULL, body TEXT NOT NULL,
              PRIMARY KEY(scope,umo));
            ''')
            db.commit()
        path.chmod(0o600)

    def connect(self):
        return sqlite3.connect(self.path, timeout=3)

    def action(self, scope, umo, identity, kind, label, outcome, ts=None):
        with self.lock, closing(self.connect()) as db:
            db.execute('INSERT OR IGNORE INTO actions VALUES (?,?,?,?,?,?,?)',
                       (identity, scope, umo, ts or time.time(), kind, label[:100], outcome[:40]))
            db.execute('DELETE FROM actions WHERE ts < ?', (time.time()-7*86400,))
            db.commit()

    def recent(self, scope, umo, limit=8):
        with self.lock, closing(self.connect()) as db:
            db.row_factory = sqlite3.Row
            return [dict(x) for x in db.execute(
                'SELECT ts,kind,label,outcome FROM actions WHERE scope=? AND umo=? ORDER BY ts DESC LIMIT ?',
                (scope, umo, min(30,max(1,int(limit)))))]

    def health(self, scope, capability, status=None, reason=''):
        with self.lock, closing(self.connect()) as db:
            if status is not None:
                db.execute('INSERT OR REPLACE INTO health VALUES (?,?,?,?,?)',
                           (scope,capability,time.time(),status,reason[:200]))
                db.commit()
            row=db.execute('SELECT ts,status,reason FROM health WHERE scope=? AND capability=?',
                           (scope,capability)).fetchone()
            return dict(zip(('checked_at','status','reason'),row)) if row else None

    def snapshot(self, scope, umo, body=None):
        with self.lock, closing(self.connect()) as db:
            if body is not None:
                db.execute('INSERT OR REPLACE INTO snapshots VALUES (?,?,?,?)',
                           (scope,umo,time.time(),json.dumps(body,ensure_ascii=False)))
                db.commit()
            row=db.execute('SELECT body FROM snapshots WHERE scope=? AND umo=?',(scope,umo)).fetchone()
            return json.loads(row[0]) if row else None
