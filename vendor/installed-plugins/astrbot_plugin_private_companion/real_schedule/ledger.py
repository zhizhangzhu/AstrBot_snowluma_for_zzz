"""Durable scoped activities. Plans and clock passage never constitute evidence."""
from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
import time
import uuid

TERMINAL = {'succeeded', 'failed', 'cancelled', 'uncertain', 'skipped'}
PENDING = {'planned', 'queued', 'paused', 'deferred'}


def dumps(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


class Ledger:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS activities(
                  id TEXT PRIMARY KEY, scope TEXT NOT NULL, day TEXT NOT NULL,
                  capability TEXT NOT NULL, intent_key TEXT NOT NULL, title TEXT NOT NULL,
                  state TEXT NOT NULL, earliest REAL NOT NULL, deadline REAL NOT NULL,
                  priority INTEGER NOT NULL, effect TEXT NOT NULL,
                  created REAL NOT NULL, updated REAL NOT NULL, lease TEXT DEFAULT '',
                  lease_until REAL DEFAULT 0, attempts INTEGER DEFAULT 0,
                  note TEXT DEFAULT '', payload TEXT NOT NULL DEFAULT '{}',
                  UNIQUE(scope,intent_key));
                CREATE INDEX IF NOT EXISTS activities_scope ON activities(scope,day,state);
                CREATE UNIQUE INDEX IF NOT EXISTS one_foreground
                  ON activities(scope) WHERE state='running';
                CREATE TABLE IF NOT EXISTS receipts(
                  id TEXT PRIMARY KEY, scope TEXT NOT NULL, activity_id TEXT NOT NULL,
                  source_key TEXT NOT NULL, created REAL NOT NULL, body TEXT NOT NULL,
                  UNIQUE(scope,source_key));
                CREATE TABLE IF NOT EXISTS settings_state(
                  scope TEXT NOT NULL,key TEXT NOT NULL,body TEXT NOT NULL,
                  PRIMARY KEY(scope,key));
                CREATE TABLE IF NOT EXISTS consumer_offsets(
                  scope TEXT NOT NULL,consumer TEXT NOT NULL,receipt_id TEXT NOT NULL,
                  PRIMARY KEY(scope,consumer,receipt_id));
            ''')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=8)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def decode(row):
        if row is None:
            return None
        item = dict(row)
        item['payload'] = json.loads(item['payload'])
        return item

    def get(self, scope, key, default=None):
        with self.connect() as db:
            row = db.execute('SELECT body FROM settings_state WHERE scope=? AND key=?', (scope,key)).fetchone()
        return json.loads(row[0]) if row else default

    def put(self, scope, key, value):
        with self.connect() as db:
            db.execute('INSERT OR REPLACE INTO settings_state VALUES(?,?,?)', (scope,key,dumps(value)))

    def enqueue(self, scope, day, capability, title, intent_key, *, earliest=0, deadline=0,
                priority=50, effect='read', payload=None, now=None):
        now = time.time() if now is None else now
        if effect not in ('read','local_write','social_write','execution'):
            raise ValueError('invalid_activity_effect')
        activity_id = 'act_' + uuid.uuid4().hex
        with self.connect() as db:
            db.execute('INSERT OR IGNORE INTO activities(id,scope,day,capability,intent_key,title,state,earliest,deadline,priority,effect,created,updated,payload) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                (activity_id,scope,day,capability,intent_key,title[:200],'planned',earliest or now,deadline,priority,effect,now,now,dumps(payload or {})))
            row = db.execute('SELECT * FROM activities WHERE scope=? AND intent_key=?', (scope,intent_key)).fetchone()
        return self.decode(row)

    def list(self, scope, *, day='', limit=100):
        sql = 'SELECT * FROM activities WHERE scope=?'
        args = [scope]
        if day:
            sql += ' AND day=?'; args.append(day)
        sql += ' ORDER BY created DESC,id DESC LIMIT ?';args.append(max(1,min(500,limit)))
        with self.connect() as db:
            return [self.decode(r) for r in db.execute(sql,args)]

    def activity(self, scope, activity_id):
        with self.connect() as db:
            return self.decode(db.execute('SELECT * FROM activities WHERE scope=? AND id=?',(scope,activity_id)).fetchone())

    def mark_external_write(self, scope, activity_id):
        with self.connect() as db:
            row=db.execute("SELECT payload FROM activities WHERE scope=? AND id=? AND state='running'",(scope,activity_id)).fetchone()
            if row is None:return
            payload=json.loads(row[0]);payload['external_write_started']=True
            db.execute("UPDATE activities SET effect='social_write',payload=? WHERE scope=? AND id=? AND state='running'",(dumps(payload),scope,activity_id))

    def checkpoint(self, scope, activity_id, lease, checkpoint):
        """Only the current execution lease may advance a resumable cursor."""
        if len(dumps(checkpoint))>16000:raise ValueError('checkpoint_too_large')
        with self.connect() as db:
            row=db.execute("SELECT payload FROM activities WHERE scope=? AND id=? AND state='running' AND lease=?",(scope,activity_id,lease)).fetchone()
            if row is None:return False
            payload=json.loads(row[0]);payload['checkpoint']=checkpoint
            db.execute('UPDATE activities SET payload=?,updated=? WHERE scope=? AND id=?',(dumps(payload),time.time(),scope,activity_id))
            return True

    def claim(self, scope, activity_id, *, now=None, lease_seconds=240):
        now = time.time() if now is None else now
        lease = uuid.uuid4().hex
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute("SELECT 1 FROM activities WHERE scope=? AND state='running'",(scope,)).fetchone():
                return None
            row = db.execute('SELECT * FROM activities WHERE scope=? AND id=?',(scope,activity_id)).fetchone()
            if not row or row['state'] not in PENDING or row['earliest']>now:
                return None
            if row['deadline'] and now>row['deadline']:
                db.execute("UPDATE activities SET state='cancelled',note='超过执行窗口',updated=? WHERE id=?",(now,activity_id))
                return None
            db.execute("UPDATE activities SET state='running',lease=?,lease_until=?,attempts=attempts+1,updated=? WHERE id=?",
                (lease,now+lease_seconds,now,activity_id))
            return self.decode(db.execute('SELECT * FROM activities WHERE id=?',(activity_id,)).fetchone())

    def finish(self, scope, activity_id, lease, state, note='', *, now=None, retry_at=0):
        if state not in TERMINAL | {'paused','deferred'}:
            raise ValueError('invalid_finish_state')
        now = time.time() if now is None else now
        with self.connect() as db:
            completed=any(json.loads(r[0]).get('partial') is not True for r in db.execute('SELECT body FROM receipts WHERE scope=? AND activity_id=?',(scope,activity_id)))
            if state == 'succeeded' and not completed:
                raise ValueError('success_requires_execution_receipt')
            cur = db.execute("UPDATE activities SET state=?,note=?,updated=?,earliest=?,lease='',lease_until=0 WHERE scope=? AND id=? AND state='running' AND lease=?",
                (state,note[:600],now,retry_at or now,scope,activity_id,lease))
            return cur.rowcount == 1

    def reschedule(self, scope, activity_id, *, earliest, note, cancel=False):
        with self.connect() as db:
            row = db.execute('SELECT state FROM activities WHERE scope=? AND id=?',(scope,activity_id)).fetchone()
            if not row or row[0] not in PENDING:
                return False
            db.execute('UPDATE activities SET earliest=?,state=?,note=?,updated=? WHERE scope=? AND id=?',
                (earliest,'cancelled' if cancel else 'deferred',note[:600],time.time(),scope,activity_id))
        return True

    def annotate(self, scope, activity_id, changes):
        with self.connect() as db:
            row=db.execute('SELECT payload FROM activities WHERE scope=? AND id=?',(scope,activity_id)).fetchone()
            if row is None:return False
            body=json.loads(row[0]);body.update(changes)
            db.execute('UPDATE activities SET payload=? WHERE scope=? AND id=?',(dumps(body),scope,activity_id))
            return True

    def resolve_pending(self, scope, activity_id, state, note, *, next_at=0):
        if state not in ('skipped','deferred'):raise ValueError('invalid_pending_resolution')
        with self.connect() as db:
            row=db.execute('SELECT state FROM activities WHERE scope=? AND id=?',(scope,activity_id)).fetchone()
            if row is None or row[0] not in PENDING:return False
            db.execute("UPDATE activities SET state=?,earliest=?,note=?,updated=? WHERE scope=? AND id=?",
                (state,next_at or time.time(),note[:600],time.time(),scope,activity_id))
            return True

    def has_receipt(self, scope, source_key):
        with self.connect() as db:
            return db.execute('SELECT 1 FROM receipts WHERE scope=? AND source_key=?',(scope,source_key)).fetchone() is not None

    def receipt(self, scope, activity_id, source_key, body, *, now=None):
        """Called only by host observers/adapters, never with a model proposal."""
        if not isinstance(body,dict) or body.get('verified') is not True:
            raise ValueError('unverified_receipt')
        if not body.get('evidence_kind') or not body.get('summary'):
            raise ValueError('receipt_missing_evidence')
        now = time.time() if now is None else now
        receipt_id = 'rcpt_' + uuid.uuid4().hex
        with self.connect() as db:
            db.execute('INSERT OR IGNORE INTO receipts VALUES(?,?,?,?,?,?)',
                (receipt_id,scope,activity_id,source_key,now,dumps(body)))
            row = db.execute('SELECT id FROM receipts WHERE scope=? AND source_key=?',(scope,source_key)).fetchone()
        return row[0]

    def receipts(self, scope, *, activity_id='', since=0, limit=40):
        sql='SELECT * FROM receipts WHERE scope=? AND created>=?';args=[scope,since]
        if activity_id:
            sql+=' AND activity_id=?';args.append(activity_id)
        sql+=' ORDER BY created DESC LIMIT ?';args.append(max(1,min(500,limit)))
        with self.connect() as db:
            return [{**dict(r),'body':json.loads(r['body'])} for r in db.execute(sql,args)]

    def diary_receipts(self, scope, start, end):
        # Exclude polling/transport noise before LIMIT; confirmed replies are
        # valid diary evidence even though ordinary experience recall omits them.
        with self.connect() as db:
            rows = db.execute("""SELECT * FROM receipts WHERE scope=? AND created>=? AND created<?
                AND json_extract(body,'$.verified')=1
                AND json_extract(body,'$.evidence_kind') NOT IN ('transport_read','qq_action_ack')
                ORDER BY created DESC LIMIT 2000""", (scope,start,end)).fetchall()
        return [{**dict(row), 'body':json.loads(row['body'])} for row in rows]

    def experiences(self, scope, *, since=0, limit=12, session_id=''):
        """Filter audit noise before LIMIT so technical traffic cannot starve recall."""
        from .experience import TECHNICAL_ACTIONS, AUDIT_ONLY_KINDS, select
        sql = '''SELECT * FROM receipts WHERE scope=? AND created>=?
            AND json_extract(body,'$.evidence_kind') NOT IN (?,?)
            AND json_extract(body,'$.evidence_kind') <> 'saved_chat_presence'
            AND coalesce(json_extract(body,'$.source'),'') NOT IN ('''
        sql += ','.join('?' for _ in TECHNICAL_ACTIONS) + ')'
        args = [scope, since, *AUDIT_ONLY_KINDS, *TECHNICAL_ACTIONS]
        for field in ('session_id', 'origin_session_id'):
            sql += f" AND coalesce(json_extract(body,'$.{field}'),'') IN ('',?)"
            args.append(session_id)
        sql += ' ORDER BY created DESC LIMIT 500'
        with self.connect() as db:
            rows = [{**dict(r), 'body': json.loads(r['body'])} for r in db.execute(sql, args)]
        return select(rows, limit=max(0, min(80, limit)), session_id=session_id)

    def recover(self, scope, *, now=None):
        now = time.time() if now is None else now
        count=0
        with self.connect() as db:
            for row in db.execute("SELECT * FROM activities WHERE scope=? AND state='running' AND lease_until<?",(scope,now)).fetchall():
                completed=any(json.loads(r[0]).get('partial') is not True for r in db.execute('SELECT body FROM receipts WHERE scope=? AND activity_id=?',(scope,row['id'])))
                if completed:
                    state,note='succeeded','重启后从实际成果恢复完成状态'
                elif row['effect'] in ('social_write','execution'):
                    state,note='uncertain','执行回执不确定，禁止自动重复对外动作'
                else:
                    state,note='paused','执行中断，保留原意图待空闲恢复'
                db.execute("UPDATE activities SET state=?,note=?,updated=?,lease='',lease_until=0 WHERE id=?",(state,note,now,row['id']))
                count+=1
        return count

    def cancel_day(self, scope, day, reason):
        """Hard limit never needs a model call and never erases receipts."""
        now=time.time()
        with self.connect() as db:
            cur=db.execute("UPDATE activities SET state='cancelled',note=?,updated=? WHERE scope=? AND day=? AND state IN ('planned','queued','paused','deferred')",(reason,now,scope,day))
        self.put(scope,'halt:'+day,{'at':now,'reason':reason})
        return cur.rowcount

    def rollover(self, scope, day):
        with self.connect() as db:
            db.execute("UPDATE activities SET state='cancelled',note='跨日重新考虑，旧意图不当作已完成',updated=? WHERE scope=? AND day<>? AND state IN ('planned','queued','paused','deferred')",(time.time(),scope,day))

    def consume(self, scope, consumer, receipt_id):
        with self.connect() as db:
            cur=db.execute('INSERT OR IGNORE INTO consumer_offsets VALUES(?,?,?)',(scope,consumer,receipt_id))
            return cur.rowcount == 1

    def credit(self, scope, skill_id, receipt, gain):
        """Receipt consumption and authoritative XP update share one transaction."""
        consumer='skill:'+skill_id;key='verified_skill:'+skill_id
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT body FROM settings_state WHERE scope=? AND key=?',(scope,key)).fetchone()
            state=json.loads(row[0]) if row else {'exp':0,'count':0,'logs':[]}
            inserted=db.execute('INSERT OR IGNORE INTO consumer_offsets VALUES(?,?,?)',(scope,consumer,receipt['id'])).rowcount
            if inserted:
                state['exp']=round(state['exp']+gain,2);state['count']+=1
                state['last_at']=receipt['created']
                state['logs']=(state['logs']+[{'ts':receipt['created'],'source':'real_execution',
                    'receipt_id':receipt['id'],'activity':receipt['body']['summary'][:120],'exp':gain,'level_up':False}])[-20:]
                db.execute('INSERT OR REPLACE INTO settings_state VALUES(?,?,?)',(scope,key,dumps(state)))
        return state
