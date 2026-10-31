"""Local competition rehearsal journal shared by Cloud and dashboard.

Only explicitly prepared barcodes are tracked. SQLite transactions prevent reset
while Cloud is handling that barcode. Archived products/files remain recoverable.
"""
import json
import os
from pathlib import Path
import sqlite3
import time
import uuid
from contextlib import contextmanager

ROOT = Path(__file__).resolve().parents[1]


class Journal:
    def __init__(self, path=None):
        self.path = Path(path or os.getenv('REHEARSAL_JOURNAL', ROOT/'.artifacts/rehearsal.sqlite3'))

    @contextmanager
    def connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute('''CREATE TABLE IF NOT EXISTS rehearsal (
            id TEXT PRIMARY KEY, barcode TEXT NOT NULL, existed INTEGER NOT NULL,
            baseline TEXT NOT NULL, state TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 0,
            created REAL NOT NULL, archive_barcode TEXT, moved TEXT NOT NULL DEFAULT '[]')''')
        db.commit()
        try:
            with db:
                yield db
        finally:
            db.close()

    def rows(self):
        with self.connect() as db:
            return [dict(r) for r in db.execute('SELECT * FROM rehearsal ORDER BY created DESC LIMIT 100')]

    def get(self, task_id):
        return next((r for r in self.rows() if r['id'] == task_id), None)

    def create(self, barcode, existed, baseline):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute("SELECT 1 FROM rehearsal WHERE barcode=? AND state NOT IN ('archived','restored','failed')", (barcode,)).fetchone():
                raise ValueError('這個條碼已有展演任務，請先完成重設。')
            task_id = uuid.uuid4().hex
            db.execute('INSERT INTO rehearsal(id,barcode,existed,baseline,state,created) VALUES (?,?,?,?,?,?)',
                       (task_id, barcode, int(existed), json.dumps(baseline), 'preparing', time.time()))
        return task_id

    def update(self, task_id, state, archive_barcode=None, moved=None):
        with self.connect() as db:
            db.execute('UPDATE rehearsal SET state=?,archive_barcode=?,moved=? WHERE id=?',
                       (state, archive_barcode, json.dumps(moved or []), task_id))

    def reserve(self, task_id, expected, target):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM rehearsal WHERE id=?', (task_id,)).fetchone()
            if not row or row['state'] != expected or row['active']:
                raise ValueError('任務狀態已改變，或仍有 Cloud 分析進行中；請等待完成再刷新清單。')
            db.execute('UPDATE rehearsal SET state=? WHERE id=?', (target, task_id))
            return dict(row)

    def begin(self, barcode):
        if not self.path.exists():
            return None
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute("SELECT * FROM rehearsal WHERE barcode=? AND state NOT IN ('archived','restored','failed') ORDER BY created DESC LIMIT 1", (barcode,)).fetchone()
            if not row:
                return None
            if row['state'] != 'ready':
                raise ValueError('展演資料正在準備或重設，請稍後重試。')
            db.execute('UPDATE rehearsal SET active=active+1 WHERE id=?', (row['id'],))
            return row['id']

    def end(self, task_id):
        if task_id:
            with self.connect() as db:
                db.execute('UPDATE rehearsal SET active=MAX(0,active-1) WHERE id=?', (task_id,))


journal = Journal()
