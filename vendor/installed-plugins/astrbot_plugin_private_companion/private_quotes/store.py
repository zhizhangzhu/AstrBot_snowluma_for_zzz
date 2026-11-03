"""Bounded QQ message anchors; never a substitute for long-term memories."""
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import time


def key(binding):
    return json.dumps([binding[k] for k in ('platform','bot','persona','umo','cid')],ensure_ascii=False,separators=(',',':'))


def base(binding):
    return json.dumps([binding[k] for k in ('platform','bot','umo')],ensure_ascii=False,separators=(',',':'))


class QuoteStore:
    def __init__(self,path):
        self.path=Path(path);self.path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        with closing(self.connect()) as db,db:
            db.executescript('''CREATE TABLE IF NOT EXISTS anchors(
                scope TEXT,base TEXT,mid TEXT,actor TEXT,stamp REAL,text TEXT,kind TEXT,
                PRIMARY KEY(scope,mid));
                CREATE INDEX IF NOT EXISTS quote_recent ON anchors(scope,stamp);
                CREATE TABLE IF NOT EXISTS withdrawn(base TEXT,mid TEXT,stamp REAL,PRIMARY KEY(base,mid));''')
        self.path.chmod(0o600)

    def connect(self):return sqlite3.connect(self.path,timeout=3)

    def save(self,binding,mid,actor,text,*,kind='text',stamp=None):
        if not str(mid) or actor not in (binding['bot'],binding['user']):return
        now=time.time()
        with closing(self.connect()) as db,db:
            db.execute('INSERT OR IGNORE INTO anchors VALUES(?,?,?,?,?,?,?)',
                (key(binding),base(binding),str(mid),actor,now if stamp is None else stamp,str(text)[:700],kind))
            if kind=='quoted':
                db.execute('UPDATE anchors SET stamp=?,kind=? WHERE scope=? AND mid=? AND actor=?',
                    (now,'quoted',key(binding),str(mid),actor))
            db.execute('DELETE FROM anchors WHERE stamp<?',(now-72*3600,))
            db.execute('DELETE FROM withdrawn WHERE stamp<?',(now-72*3600,))
            db.execute('DELETE FROM anchors WHERE rowid IN (SELECT rowid FROM anchors ORDER BY stamp DESC LIMIT -1 OFFSET 6000)')

    def recent(self,binding,*,limit=12,hours=24):
        with closing(self.connect()) as db:
            rows=db.execute('''SELECT mid,actor,stamp,text,kind FROM anchors a WHERE scope=? AND stamp>=?
                AND NOT EXISTS(SELECT 1 FROM withdrawn w WHERE w.base=a.base AND w.mid=a.mid)
                ORDER BY stamp DESC,rowid DESC LIMIT ?''',(key(binding),time.time()-hours*3600,limit)).fetchall()
        return [dict(zip(('mid','actor','stamp','text','kind'),r)) for r in rows][::-1]

    def get(self,binding,mid,*,hours=24):
        return next((r for r in self.recent(binding,limit=300,hours=hours) if r['mid']==str(mid)),None)

    def withdraw(self,binding,mid):
        with closing(self.connect()) as db,db:
            db.execute('INSERT OR REPLACE INTO withdrawn VALUES(?,?,?)',(base(binding),str(mid),time.time()))

    def is_withdrawn(self,binding,mid):
        with closing(self.connect()) as db:
            return db.execute('SELECT 1 FROM withdrawn WHERE base=? AND mid=?',(base(binding),str(mid))).fetchone() is not None


def store(host):
    value=getattr(host,'_private_quote_store',None)
    if value is None:
        value=QuoteStore(Path(host.data_dir)/'private_quotes/context.db');host._private_quote_store=value
    return value
