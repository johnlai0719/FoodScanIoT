"""Durable FIFO outbox. Each operation uses its own short SQLite transaction."""
import hashlib
import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from transforms import is_cacheable_result


class OfflineQueue:
    def __init__(self, db_path):
        self.db_path = str(db_path)
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS analysis_queue (
                    id TEXT PRIMARY KEY, payload_json TEXT, digest TEXT NOT NULL,
                    request_id TEXT, state TEXT NOT NULL DEFAULT 'pending',
                    created_at REAL NOT NULL, updated_at REAL NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0, next_attempt REAL NOT NULL DEFAULT 0,
                    lease_until REAL NOT NULL DEFAULT 0, result_json TEXT, error TEXT
                );
                CREATE UNIQUE INDEX IF NOT EXISTS queue_active_digest
                ON analysis_queue(digest) WHERE state IN ('pending', 'processing');
            ''')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.db_path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def enqueue(self, payload, request_id=''):
        encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        digest = hashlib.sha256(encoded.encode()).hexdigest()
        now = time.time()
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            existing = db.execute("SELECT id FROM analysis_queue WHERE digest=? AND state IN ('pending','processing')", (digest,)).fetchone()
            if existing:
                return existing['id']
            job_id = uuid.uuid4().hex
            db.execute('INSERT INTO analysis_queue (id,payload_json,digest,request_id,created_at,updated_at) VALUES (?,?,?,?,?,?)',
                       (job_id, encoded, digest, request_id, now, now))
            return job_id

    def find_active(self, payload):
        encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        digest = hashlib.sha256(encoded.encode()).hexdigest()
        with self.connect() as db:
            row = db.execute("SELECT id FROM analysis_queue WHERE digest=? AND state IN ('pending','processing')", (digest,)).fetchone()
            return row['id'] if row else None

    def claim(self):
        now = time.time()
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            # The oldest active task blocks later tasks, even during retry backoff.
            row = db.execute("SELECT * FROM analysis_queue WHERE state IN ('pending','processing') ORDER BY created_at, rowid LIMIT 1").fetchone()
            if not row or row['next_attempt'] > now or row['lease_until'] > now:
                return None
            db.execute("UPDATE analysis_queue SET state='processing', attempts=attempts+1, lease_until=?, updated_at=? WHERE id=?", (now + 300, now, row['id']))
            job = dict(row)
            job['attempts'] += 1
            job['payload'] = json.loads(job['payload_json'])
            return job

    def complete(self, job, result):
        now = time.time()
        with self.connect() as db:
            barcode = job['payload'].get('barcode')
            if is_cacheable_result(result) and barcode and barcode != 'TEST':
                db.execute('INSERT OR REPLACE INTO cache (barcode,result_json,last_updated,ttl) VALUES (?,?,?,?)',
                           (barcode, json.dumps(result, ensure_ascii=False), int(now), 86400))
            # Drop uploaded images after successful delivery; retain result for polling.
            db.execute("UPDATE analysis_queue SET state='completed',payload_json=NULL,result_json=?,error=NULL,lease_until=0,updated_at=? WHERE id=?",
                       (json.dumps(result, ensure_ascii=False), now, job['id']))

    def retry(self, job, error):
        delay = min(300, 5 * 2 ** min(job['attempts'] - 1, 6))
        with self.connect() as db:
            db.execute("UPDATE analysis_queue SET state='pending',lease_until=0,next_attempt=?,updated_at=?,error=? WHERE id=?",
                       (time.time() + delay, time.time(), str(error)[:300], job['id']))

    def fail(self, job, message):
        with self.connect() as db:
            db.execute("UPDATE analysis_queue SET state='failed',lease_until=0,updated_at=?,error=? WHERE id=?", (time.time(), str(message)[:300], job['id']))

    def get(self, job_id):
        with self.connect() as db:
            row = db.execute('SELECT id,state,request_id,created_at,updated_at,attempts,result_json,error FROM analysis_queue WHERE id=?', (job_id,)).fetchone()
            if not row:
                return None
            job = dict(row)
            result = job.pop('result_json')
            if result:
                job['result'] = json.loads(result)
            return job

    def stats(self):
        with self.connect() as db:
            return {row['state']: row['n'] for row in db.execute('SELECT state,COUNT(*) n FROM analysis_queue GROUP BY state')}
