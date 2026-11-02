"""Device bindings and durable message identities; no model or QQ credentials."""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import sqlite3
import time
from pathlib import Path

from .protocol import Binding


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        os.chmod(path, 0o600)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS devices (
                device_id TEXT PRIMARY KEY, token_hash TEXT UNIQUE NOT NULL,
                persona_id TEXT NOT NULL, bot_id TEXT NOT NULL,
                user_id TEXT NOT NULL, qq_platform_id TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1);
            CREATE TABLE IF NOT EXISTS ledger (
                device_id TEXT NOT NULL, id TEXT NOT NULL, kind TEXT NOT NULL,
                state TEXT NOT NULL, payload TEXT NOT NULL, updated REAL NOT NULL,
                PRIMARY KEY(device_id,id));
            CREATE TABLE IF NOT EXISTS terminal_flags (
                device_id TEXT PRIMARY KEY, dnd INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS terminal_quiet (
                device_id TEXT PRIMARY KEY, quiet INTEGER NOT NULL DEFAULT 0);
        """)

    def pair(self, binding: Binding) -> str:
        token = secrets.token_urlsafe(32)
        # One primary device per persona/user. Re-pairing revokes the previous device.
        with self.db:
            self.db.execute("UPDATE devices SET enabled=0 WHERE persona_id=? AND bot_id=? AND user_id=?",
                            (binding.persona_id, binding.bot_id, binding.user_id))
            self.db.execute("INSERT OR REPLACE INTO devices VALUES (?,?,?,?,?,?,1)",
                            (binding.device_id, hashlib.sha256(token.encode()).hexdigest(), binding.persona_id,
                             binding.bot_id, binding.user_id, binding.qq_platform_id))
        return token

    def authenticate(self, token: str) -> Binding | None:
        row = self.db.execute("SELECT * FROM devices WHERE enabled=1 AND token_hash=?",
                              (hashlib.sha256(token.encode()).hexdigest(),)).fetchone()
        return Binding(**{key: row[key] for key in Binding.__dataclass_fields__}) if row else None

    def enabled(self, binding: Binding) -> bool:
        row = self.db.execute("SELECT * FROM devices WHERE device_id=? AND enabled=1", (binding.device_id,)).fetchone()
        return bool(row and all(row[key] == getattr(binding, key) for key in Binding.__dataclass_fields__))

    def revoke(self, device_id: str) -> None:
        with self.db:
            self.db.execute("UPDATE devices SET enabled=0 WHERE device_id=?", (device_id,))

    def claim(self, device: str, message_id: str, kind: str, payload: dict) -> bool:
        with self.db:
            cur = self.db.execute("INSERT OR IGNORE INTO ledger VALUES (?,?,?,?,?,?)",
                                  (device, message_id, kind, "accepted", json.dumps(payload, ensure_ascii=False), time.time()))
        return cur.rowcount == 1

    def update(self, device: str, message_id: str, state: str, payload: dict | None = None) -> None:
        with self.db:
            if payload is None:
                self.db.execute("UPDATE ledger SET state=?,updated=? WHERE device_id=? AND id=?",
                                (state, time.time(), device, message_id))
            else:
                self.db.execute("UPDATE ledger SET state=?,payload=?,updated=? WHERE device_id=? AND id=?",
                                (state, json.dumps(payload, ensure_ascii=False), time.time(), device, message_id))

    def get(self, device: str, message_id: str) -> dict | None:
        row = self.db.execute("SELECT * FROM ledger WHERE device_id=? AND id=?", (device, message_id)).fetchone()
        return {**dict(row), "payload": json.loads(row["payload"])} if row else None

    def outstanding(self, device: str) -> list[str]:
        return [row[0] for row in self.db.execute(
            "SELECT id FROM ledger WHERE device_id=? AND kind='call' AND state IN ('accepted','unknown') ORDER BY updated DESC LIMIT 100",
            (device,))]

    def bindings(self) -> list[Binding]:
        return [Binding(**{key: row[key] for key in Binding.__dataclass_fields__})
                for row in self.db.execute("SELECT * FROM devices WHERE enabled=1")]

    def set_dnd(self, device: str, enabled: bool) -> None:
        with self.db:
            self.db.execute("""INSERT INTO terminal_flags VALUES (?,?) ON CONFLICT(device_id)
                DO UPDATE SET dnd=excluded.dnd WHERE terminal_flags.dnd<>excluded.dnd""", (device, int(enabled)))

    def dnd(self, device: str) -> bool:
        row = self.db.execute("SELECT dnd FROM terminal_flags WHERE device_id=?", (device,)).fetchone()
        return bool(row and row[0])

    def set_quiet(self, device: str, enabled: bool) -> None:
        with self.db:
            self.db.execute("""INSERT INTO terminal_quiet VALUES (?,?) ON CONFLICT(device_id)
                DO UPDATE SET quiet=excluded.quiet WHERE terminal_quiet.quiet<>excluded.quiet""", (device, int(enabled)))

    def quiet(self, device: str) -> bool:
        row = self.db.execute("SELECT quiet FROM terminal_quiet WHERE device_id=?", (device,)).fetchone()
        return bool(row and row[0])

    def receipt(self, binding: Binding, request_id: str) -> dict | None:
        row = self.db.execute("""SELECT l.* FROM ledger l JOIN devices d ON d.device_id=l.device_id
            WHERE l.id=? AND l.kind='call' AND d.persona_id=? AND d.bot_id=? AND d.user_id=? AND d.qq_platform_id=?""",
            (request_id, binding.persona_id, binding.bot_id, binding.user_id, binding.qq_platform_id)).fetchone()
        return {**dict(row), "payload": json.loads(row["payload"])} if row else None

    def observations(self, binding: Binding, limit=8) -> list[dict]:
        return [json.loads(row[0]) for row in self.db.execute(
            "SELECT payload FROM ledger WHERE device_id=? AND kind='observation' ORDER BY updated DESC LIMIT ?",
            (binding.device_id, limit))][::-1]

    def queued_events(self, binding: Binding, limit=100) -> list[dict]:
        return [{'v':1,'type':'event','id':row['id'],'payload':json.loads(row['payload'])} for row in self.db.execute(
            "SELECT id,payload FROM ledger WHERE device_id=? AND kind='event' AND state IN ('accepted','queued') ORDER BY updated LIMIT ?",
            (binding.device_id,max(1,min(100,int(limit)))))]

    def usages(self, binding: Binding, limit=500) -> list[dict]:
        rows=self.db.execute("""SELECT l.payload FROM ledger l JOIN devices d ON d.device_id=l.device_id
            WHERE l.kind='usage' AND d.persona_id=? AND d.bot_id=? AND d.user_id=? AND d.qq_platform_id=?
            ORDER BY l.updated DESC LIMIT ?""",(binding.persona_id,binding.bot_id,binding.user_id,binding.qq_platform_id,max(1,min(500,int(limit)))))
        return [json.loads(row[0]) for row in rows][::-1]

    def close(self) -> None:
        self.db.close()
