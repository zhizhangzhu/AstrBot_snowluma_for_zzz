"""Private durable governance records. Monetary values use integer microdollars."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
from decimal import Decimal, ROUND_CEILING, InvalidOperation
import json
import os
from pathlib import Path
import sqlite3
import threading
import time
import uuid
from zoneinfo import ZoneInfo

ZONE = ZoneInfo('Asia/Shanghai')


def dumps(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def micros(value):
    try:
        result = Decimal(str(value)) * 1_000_000
    except InvalidOperation as exc:
        raise ValueError('费用必须为有效数字') from exc
    if not result.is_finite() or result < 0:
        raise ValueError('费用必须为有限非负数')
    return int(result.to_integral_value(rounding=ROUND_CEILING))


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.conn = sqlite3.connect(self.path, timeout=15, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        os.chmod(self.path, 0o600)
        self.conn.executescript('''
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT NOT NULL,revision INTEGER NOT NULL DEFAULT 1);
        CREATE TABLE IF NOT EXISTS audit(id TEXT PRIMARY KEY,at REAL NOT NULL,kind TEXT NOT NULL,actor TEXT NOT NULL,body TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS requests(id TEXT PRIMARY KEY,started REAL NOT NULL,ended REAL,day TEXT NOT NULL,period TEXT NOT NULL,
          plugin TEXT NOT NULL,feature TEXT NOT NULL,bot TEXT NOT NULL,persona TEXT NOT NULL,task TEXT NOT NULL,
          provider TEXT NOT NULL,model TEXT NOT NULL,effort TEXT NOT NULL,state TEXT NOT NULL,
          reserved INTEGER NOT NULL,cost INTEGER,input INTEGER,cached INTEGER,output INTEGER,reasoning INTEGER,
          quality TEXT NOT NULL,price_version TEXT,detail TEXT NOT NULL DEFAULT '{}');
        CREATE INDEX IF NOT EXISTS requests_period ON requests(period,state);
        CREATE INDEX IF NOT EXISTS requests_day ON requests(day,plugin,bot);
        CREATE TABLE IF NOT EXISTS tasks(id TEXT PRIMARY KEY,created REAL NOT NULL,updated REAL NOT NULL,revision INTEGER NOT NULL,
          state TEXT NOT NULL,goal TEXT NOT NULL,identity TEXT NOT NULL,payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS approvals(id TEXT PRIMARY KEY,task TEXT NOT NULL,revision INTEGER NOT NULL,digest TEXT NOT NULL,
          stage TEXT NOT NULL,actor TEXT NOT NULL,created REAL NOT NULL,expires REAL NOT NULL,state TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS config_changes(id TEXT PRIMARY KEY,plugin TEXT NOT NULL,persona TEXT NOT NULL,key TEXT NOT NULL,
          before_value TEXT NOT NULL,after_value TEXT NOT NULL,reason TEXT NOT NULL,at REAL NOT NULL,state TEXT NOT NULL,
          UNIQUE(id,persona));
        CREATE TABLE IF NOT EXISTS documents(id TEXT PRIMARY KEY,kb TEXT NOT NULL,scope TEXT NOT NULL,url TEXT NOT NULL,
          digest TEXT NOT NULL,created REAL NOT NULL,updated REAL NOT NULL,state TEXT NOT NULL,body TEXT NOT NULL,
          UNIQUE(kb,scope,url,digest));
        CREATE TABLE IF NOT EXISTS leases(name TEXT PRIMARY KEY,owner TEXT NOT NULL,expires REAL NOT NULL);
        ''')

    @contextmanager
    def transaction(self):
        with self.lock:
            self.conn.execute('BEGIN IMMEDIATE')
            try:
                yield self.conn
                self.conn.commit()
            except BaseException:
                self.conn.rollback()
                raise

    def rows(self, sql, args=()):
        with self.lock:
            return [dict(r) for r in self.conn.execute(sql, args)]

    def setting(self, key, default=None):
        rows = self.rows('SELECT value,revision FROM settings WHERE key=?', (key,))
        return (json.loads(rows[0]['value']), rows[0]['revision']) if rows else (default, 0)

    def set_setting(self, key, value, expected_revision=None):
        with self.transaction() as db:
            row = db.execute('SELECT revision FROM settings WHERE key=?', (key,)).fetchone()
            revision = row[0] if row else 0
            if expected_revision is not None and revision != expected_revision:
                raise ValueError('配置已变化，请刷新后重试')
            db.execute('INSERT OR REPLACE INTO settings VALUES(?,?,?)', (key, dumps(value), revision + 1))
        return revision + 1

    def audit(self, kind, actor, body):
        with self.transaction() as db:
            db.execute('INSERT INTO audit VALUES(?,?,?,?,?)', (uuid.uuid4().hex, time.time(), kind, actor, dumps(body)))

    def lease(self, name, owner, seconds=300):
        with self.transaction() as db:
            row = db.execute('SELECT owner,expires FROM leases WHERE name=?', (name,)).fetchone()
            if row and row['expires'] > time.time() and row['owner'] != owner:
                return False
            db.execute('INSERT OR REPLACE INTO leases VALUES(?,?,?)', (name, owner, time.time() + seconds))
        return True

    def release(self, name, owner):
        with self.transaction() as db:
            db.execute('DELETE FROM leases WHERE name=? AND owner=?', (name, owner))

    def close(self):
        with self.lock:
            self.conn.close()
