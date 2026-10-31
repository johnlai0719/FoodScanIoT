"""Explicit barcode preparation and reversible reset for local competition demos."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'server'))
from rehearsal_store import journal
from fog_demo_control import authorize_fog

UPLOADS = ROOT/'server/uploads'
ARCHIVES = ROOT/'.artifacts/rehearsal-archives'
router = APIRouter()


class NextMiss:
    def __init__(self):
        self.lock = threading.Lock()
        self.enabled = False
        self.busy = False
        self.deadline = 0
        self.result = None
        self.request_id = None

    def view(self):
        with self.lock:
            return {'armed': self.enabled and time.monotonic() < self.deadline,
                    'busy': self.busy, 'remaining_seconds': max(0, self.deadline-time.monotonic()) if self.enabled else 0,
                    'result': self.result}


next_miss = NextMiss()


def authorize(request):
    if request.client and request.client.host not in ('127.0.0.1', '::1', 'testclient'):
        raise HTTPException(403, '請透過本機或既有 Tailscale 儀表板操作')
    origin = request.headers.get('origin')
    if request.headers.get('sec-fetch-site') == 'cross-site' or (origin and urlsplit(origin).netloc != request.headers.get('host')):
        raise HTTPException(403, 'Cross-origin control is not allowed')


@contextmanager
def database():
    import psycopg2
    # Same existing container as the one-click competition Cloud launcher.
    name = os.getenv('COMPETITION_DB_CONTAINER', 'app-sync-server-db-1')
    result = subprocess.run(['docker', 'inspect', name], capture_output=True, text=True,
                            encoding='utf-8', timeout=8, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if result.returncode:
        raise ValueError('無法取得既有比賽資料庫，請先啟動 Docker 與 Cloud。')
    config = json.loads(result.stdout)[0]
    env = dict(item.split('=', 1) for item in config['Config']['Env'] if '=' in item)
    connection = psycopg2.connect(host='127.0.0.1', port=5432, dbname=env.get('POSTGRES_DB', 'product_db'),
                                 user=env.get('POSTGRES_USER', 'postgres'), password=env.get('POSTGRES_PASSWORD', ''), connect_timeout=3)
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def photos(barcode):
    # Barcode is restricted to digits, filenames to the exact existing upload convention.
    pattern = re.compile(r'scan_\d+_' + re.escape(barcode) + r'_\d+\.jpg')
    return sorted(p.name for p in UPLOADS.iterdir() if p.is_file() and not p.is_symlink() and pattern.fullmatch(p.name)) if UPLOADS.exists() else []


def fog_clear(barcode):
    base = os.getenv('REHEARSAL_FOG_URL', 'http://100.86.249.39:3001').rstrip('/')
    # Fail closed if queued work can later recreate a reset product/cache.
    # Python port can be configured independently for deployments using a proxy.
    python_base = os.getenv('REHEARSAL_FOG_PYTHON_URL', 'http://100.86.249.39:3002').rstrip('/')
    session = requests.Session()
    session.trust_env = False
    try:
        r = session.get(python_base+'/health', timeout=5)
        r.raise_for_status()
        health = r.json()
        queue = health.get('queue')
        if health.get('status') != 'ok' or not isinstance(queue, dict):
            raise ValueError('Fog 佇列狀態無法確認，未清除快取。')
        if queue.get('pending', 0) or queue.get('processing', 0):
            raise ValueError('Fog 還有待補送／處理中的任務，請先完成補送再準備或重設。')
        r = session.delete(base+'/cache/'+barcode, timeout=5)
        r.raise_for_status()
        if r.json().get('status') != 'success':
            raise ValueError('Fog 未確認快取清除成功。')
    finally:
        session.close()


class Prepare(BaseModel):
    barcode: str = Field(pattern=r'^\d{8,14}$')


class Task(BaseModel):
    task_id: str = Field(pattern=r'^[a-f0-9]{32}$')


class Batch(BaseModel):
    task_ids: list[str] = Field(min_length=1, max_length=20)


class Arm(BaseModel):
    enabled: bool


class Claim(Prepare):
    request_id: str = Field(pattern=r'^[A-Za-z0-9_.:-]{1,64}$')
    fog_id: str = Field(default='foodscan-pi', max_length=64)


class Applied(Claim):
    task_id: str = Field(pattern=r'^[a-f0-9]{32}$')
    applied: bool


def safe_path(root, name):
    root = root.resolve()
    if (root/name).is_symlink():
        raise ValueError('圖片路徑不可是符號連結。')
    path = (root/name).resolve()
    if path.parent != root or path.is_symlink():
        raise ValueError('圖片路徑不符合展演資料範圍。')
    return path


@router.get('/api/rehearsal')
def listing():
    rows = []
    for row in journal.rows():
        current = set(photos(row['barcode']))-set(json.loads(row['baseline']))
        rows.append({k: row[k] for k in ('id', 'barcode', 'existed', 'state', 'active', 'created')}
                    | {'new_images': len(current), 'images': sorted(current)})
    return {'tasks': rows, 'next_miss': next_miss.view(), 'note': '下一筆由手機觸發；此面板不會自行呼叫辨識 API。'}


@router.post('/api/rehearsal/next-miss')
def arm(body: Arm, request: Request):
    authorize(request)
    from fog_demo_control import token
    if not token():
        raise HTTPException(503, '尚未設定 Fog 展演控制金鑰。')
    with next_miss.lock:
        if next_miss.busy:
            raise HTTPException(409, 'Fog 正在套用此任務，請等待完成。')
        next_miss.enabled = body.enabled
        next_miss.deadline = time.monotonic()+300 if body.enabled else 0
        next_miss.result = None
        next_miss.request_id = None
    return {'ok': True, 'message': '已預備：下一筆有效條碼掃描會清除該條碼快取，再走實際 Miss；等待新版 Fog 領取，最長 5 分鐘。' if body.enabled else '已取消下一筆 Miss 設定。'}


@router.post('/api/rehearsal/claim')
def claim(body: Claim, request: Request):
    authorize_fog(request)
    if body.fog_id != os.getenv('FOG_DEMO_NODE_ID', 'foodscan-pi'):
        raise HTTPException(403, 'Unknown Fog node')
    with next_miss.lock:
        if next_miss.request_id == body.request_id and next_miss.result:
            return next_miss.result
        if next_miss.busy or not next_miss.enabled or time.monotonic() >= next_miss.deadline:
            return {'applied': False}
        next_miss.busy = True
    try:
        # Reuse exact barcode preparation; do not spoof a cache_miss event.
        # request is a token-authenticated Fog call, not an operator browser.
        result = prepare_barcode(body.barcode, clear=False)
        response = {'applied': False, 'pending': True, 'barcode': body.barcode, 'task_id': result['task_id']}
        with next_miss.lock:
            next_miss.enabled = False
            next_miss.result = response
            next_miss.request_id = body.request_id
        return {**response, 'clear_cache': True}
    except Exception:
        with next_miss.lock:
            next_miss.result = {'applied': False, 'error': 'Fog 未完成指定快取清除；設定尚未消耗。'}
        raise HTTPException(409, '指定快取清除未完成，下一筆設定仍保留。')
    finally:
        with next_miss.lock:
            next_miss.busy = False


@router.post('/api/rehearsal/applied')
def applied(body: Applied, request: Request):
    authorize_fog(request)
    if body.fog_id != os.getenv('FOG_DEMO_NODE_ID', 'foodscan-pi'):
        raise HTTPException(403, 'Unknown Fog node')
    with next_miss.lock:
        current = next_miss.result or {}
        if next_miss.request_id != body.request_id or current.get('task_id') != body.task_id or current.get('barcode') != body.barcode:
            raise HTTPException(409, 'Stale rehearsal acknowledgement')
        next_miss.result = {'applied': body.applied, 'pending': False, 'barcode': body.barcode, 'task_id': body.task_id}
    return {'ok': True}


@router.post('/api/rehearsal/prepare')
def prepare(body: Prepare, request: Request):
    authorize(request)
    return prepare_barcode(body.barcode)


def prepare_barcode(barcode, clear=True):
    task_id = None
    existing = None
    try:
        with database() as db, db.cursor() as cursor:
            cursor.execute('SELECT 1 FROM products WHERE barcode=%s', (barcode,))
            existed = cursor.fetchone() is not None
        # A later repeat may reuse this rehearsal registration, but clear cache once again.
        existing = next((row for row in journal.rows() if row['barcode'] == barcode and row['state'] == 'ready' and not row['active']), None)
        task_id = existing['id'] if existing else journal.create(barcode, existed, photos(barcode))
        if existing:
            journal.reserve(task_id, 'ready', 'preparing')
        if clear:
            fog_clear(barcode)
        journal.update(task_id, 'ready')
        return {'ok': True, 'task_id': task_id, 'message': '已清除指定條碼的 Fog 快取。請用手機掃描此條碼；這是清除快取後的 Miss，尚未執行辨識。'}
    except Exception as exc:
        if task_id:
            journal.update(task_id, 'ready' if existing else 'failed')
        raise HTTPException(409, str(exc) if isinstance(exc, ValueError) else '準備未完成，請檢查 Fog、Docker 與本地設定。')


@router.post('/api/rehearsal/reset')
def reset(body: Task, request: Request):
    authorize(request)
    row = None
    moved = []
    committed = False
    try:
        row = journal.reserve(body.task_id, 'ready', 'resetting')
        fog_clear(row['barcode'])
        # Pre-existing products and their images are never archived by this feature.
        candidates = [] if row['existed'] else sorted(set(photos(row['barcode']))-set(json.loads(row['baseline'])))
        archive = ARCHIVES/row['id']
        archive.mkdir(parents=True, exist_ok=True)
        archive_barcode = 'R'+row['id'][:12]
        with database() as db, db.cursor() as cursor:
            if not row['existed']:
                cursor.execute('UPDATE products SET barcode=%s WHERE barcode=%s RETURNING id', (archive_barcode, row['barcode']))
                renamed = cursor.fetchone() is not None
            else:
                renamed = False
            for name in candidates:
                source, destination = safe_path(UPLOADS, name), safe_path(archive, name)
                if destination.exists():
                    raise ValueError('封存位置已有同名圖片，未覆寫。')
                shutil.move(str(source), str(destination))
                moved.append(name)
            # Persist recovery manifest before committing database changes.
            journal.update(row['id'], 'resetting', archive_barcode if renamed else None, moved)
        committed = True
        journal.update(row['id'], 'archived', archive_barcode if renamed else None, moved)
        return {'ok': True, 'message': '展演資料已重設並保留可還原封存；下次請先重新準備此條碼。', 'archived_images': len(moved), 'preserved_existing': bool(row['existed'])}
    except Exception as exc:
        if committed:
            raise HTTPException(409, '封存已完成，但狀態紀錄未完成；請保留封存與日誌，暫勿重試，需本地復原狀態。')
        if row:
            for name in reversed(moved):
                shutil.move(str(safe_path(ARCHIVES/row['id'], name)), str(safe_path(UPLOADS, name)))
            journal.update(row['id'], 'ready')
        raise HTTPException(409, str(exc) if isinstance(exc, ValueError) else '重設未完成；請檢查服務與本地封存紀錄。')


@router.post('/api/rehearsal/restore')
def restore(body: Task, request: Request):
    authorize(request)
    row = None
    moved = []
    committed = False
    try:
        row = journal.reserve(body.task_id, 'archived', 'restoring')
        names = json.loads(row['moved'])
        for name in names:
            if safe_path(UPLOADS, name).exists():
                raise ValueError('原位置已有圖片，未覆寫；請先處理新一輪展演資料。')
        with database() as db, db.cursor() as cursor:
            if row['archive_barcode']:
                cursor.execute('SELECT 1 FROM products WHERE barcode=%s', (row['barcode'],))
                if cursor.fetchone():
                    raise ValueError('原條碼已有新資料，未覆寫；請先處理新一輪展演資料。')
                cursor.execute('UPDATE products SET barcode=%s WHERE barcode=%s', (row['barcode'], row['archive_barcode']))
                if cursor.rowcount != 1:
                    raise ValueError('找不到原封存商品，未還原。')
            for name in names:
                shutil.move(str(safe_path(ARCHIVES/row['id'], name)), str(safe_path(UPLOADS, name)))
                moved.append(name)
        committed = True
        journal.update(row['id'], 'restored', row['archive_barcode'], names)
        return {'ok': True, 'message': '商品與圖片已還原；Fog 快取保持清空，可由正常查詢重新建立。'}
    except Exception as exc:
        if committed:
            raise HTTPException(409, '資料已還原，但狀態紀錄未完成；請暫勿重試，需本地復原狀態。')
        if row:
            for name in reversed(moved):
                shutil.move(str(safe_path(UPLOADS, name)), str(safe_path(ARCHIVES/row['id'], name)))
            journal.update(row['id'], 'archived', row['archive_barcode'], json.loads(row['moved']))
        raise HTTPException(409, str(exc) if isinstance(exc, ValueError) else '還原未完成，原封存仍保留。')


@router.post('/api/rehearsal/reset-new')
def reset_new(body: Batch, request: Request):
    authorize(request)
    results = []
    for task_id in dict.fromkeys(body.task_ids):
        row = journal.get(task_id)
        if not row or row['existed'] or row['state'] != 'ready':
            results.append({'task_id': task_id, 'ok': False, 'message': '非可重設的本輪新增資料，已略過。'})
            continue
        try:
            reset(Task(task_id=task_id), request)
            results.append({'task_id': task_id, 'ok': True})
        except HTTPException as exc:
            results.append({'task_id': task_id, 'ok': False, 'message': str(exc.detail)})
    completed = sum(r['ok'] for r in results)
    failed = [r['message'] for r in results if not r['ok']]
    return {'ok': not failed, 'results': results, 'message': f'已封存 {completed} 筆新增展演資料。'+(' 未完成：'+'；'.join(failed) if failed else '可逐筆還原。')}
