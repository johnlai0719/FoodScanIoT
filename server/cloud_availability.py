"""Shared local Cloud-only HTTP 503 switch; Fog needs no new commands or key."""
import os
from pathlib import Path
import sqlite3
import time
from contextlib import contextmanager

from fastapi.responses import JSONResponse


class Availability:
    def __init__(self, path=None, clock=time.time):
        self.path = Path(path or os.getenv('CLOUD_DEMO_STATE', Path(__file__).resolve().parents[1]/'.artifacts/cloud-availability.sqlite3'))
        self.clock = clock

    @contextmanager
    def connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        try:
            with db:
                db.execute('CREATE TABLE IF NOT EXISTS availability (id INTEGER PRIMARY KEY, until REAL NOT NULL, rejected INTEGER NOT NULL, last_rejected REAL)')
                db.execute('INSERT OR IGNORE INTO availability VALUES (1,0,0,NULL)')
                db.commit()
                yield db
        finally:
            db.close()

    def set(self, unavailable):
        with self.connect() as db:
            db.execute('UPDATE availability SET until=?,rejected=0,last_rejected=NULL WHERE id=1',
                       (self.clock()+180 if unavailable else 0,))
        return self.view()

    def view(self):
        with self.connect() as db:
            row = db.execute('SELECT * FROM availability WHERE id=1').fetchone()
        remaining = max(0, row['until']-self.clock())
        return {'unavailable': remaining > 0, 'remaining_seconds': remaining,
                'rejected_requests': row['rejected'], 'last_rejected_at': row['last_rejected']}

    def reject(self):
        if not self.path.exists():
            return False
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT until FROM availability WHERE id=1').fetchone()
            if row['until'] <= self.clock():
                return False
            db.execute('UPDATE availability SET rejected=rejected+1,last_rejected=? WHERE id=1', (self.clock(),))
            return True


availability = Availability()


def install(app, store=availability):
    @app.middleware('http')
    async def demo_cloud_response(request, call_next):
        path = request.url.path.rstrip('/')
        is_analysis = request.method == 'POST' and path in ('/query', '/analyze', '/api/analyze')
        is_health = request.method in ('GET', 'HEAD') and path == '/health'
        if (is_analysis or is_health) and store.reject():
            return JSONResponse(status_code=503, content={
                'status': 'unavailable', 'demo': True,
                'message': '展演設定：Cloud 暫時回應不可用，請由 Fog 保存任務並於恢復後補送。'},
                headers={'Retry-After': '5', 'Cache-Control': 'no-store'})
        return await call_next(request)
