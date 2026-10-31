"""Local observer with an explicit, isolated Google Search grounding test."""
import base64
import copy
import io
import json
import os
import re
import threading
import time
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from PIL import Image, ImageOps
from vision_auto_regions import extract_words, extract_paragraphs, detect
import grounding_search_lab as grounding_lab
from fog_demo_control import router as demo_router
from rehearsal_api import router as rehearsal_router

ROOT = Path(__file__).resolve().parents[1]
STAGES = ('image', 'fog', 'vision', 'routing', 'gemini', 'sync')


class FogObservation(BaseModel):
    event: Literal['received', 'cache_hit', 'cache_miss', 'cache_bypass', 'cache_excluded',
                   'cache_returned', 'manager_forwarded', 'manager_returned', 'cloud_forwarded',
                   'cloud_returned', 'cloud_unavailable', 'queued', 'replay_started', 'replay_retry',
                   'replay_completed', 'replay_failed', 'storage_failed', 'unavailable']
    layer: Literal['node', 'python']
    cache_status: Literal['HIT', 'HIT-STALE', 'HIT-RAW', 'MISS', 'BYPASS', 'EXCLUDED', 'QUEUED'] | None = None
    queue_id: str | None = Field(default=None, pattern=r'^[a-f0-9]{32}$')
    attempt: int | None = Field(default=None, ge=1)
    http_status: int | None = Field(default=None, ge=100, le=599)


class Event(BaseModel):
    request_id: str = Field(min_length=1, max_length=96, pattern=r'^[A-Za-z0-9_.:-]+$')
    stage: Literal['image', 'fog', 'vision', 'routing', 'gemini', 'sync', 'error']
    status: Literal['active', 'done', 'failed']
    sender: Literal['app', 'cloud', 'fog']
    fog: FogObservation | None = None
    duration_ms: float | None = Field(default=None, ge=0)
    payload: dict = Field(default_factory=dict)


def thumbnail(image, edge=1100):
    im = ImageOps.exif_transpose(image).convert('RGB')
    im.thumbnail((edge, edge))
    out = io.BytesIO()
    im.save(out, format='JPEG', quality=84)
    return 'data:image/jpeg;base64,'+base64.b64encode(out.getvalue()).decode()


class Store:
    def __init__(self):
        self.jobs = OrderedDict()
        self.aliases = {}
        self.lock = threading.Lock()

    def accept(self, event):
        if event.stage == 'fog' and (event.sender != 'fog' or event.fog is None):
            raise ValueError('Fog observations must come from the Fog emitter')
        if event.stage != 'fog' and event.fog is not None:
            raise ValueError('Fog observations require the Fog stage')
        if event.stage == 'sync' and event.status == 'done' and event.sender != 'app':
            raise ValueError('Only App acknowledgement completes synchronization')
        payload = copy.deepcopy(event.payload)
        # Fog history and observed outcome are managed here, never taken from App payloads.
        for key in ('fog_history', 'fog_latest'):
            payload.pop(key, None)
        if payload.get('demo_scenario') not in ('cloud', 'miss', 'cache', 'queue'):
            payload.pop('demo_scenario', None)
        canonical = payload.pop('canonical_request_id', None)
        if len(json.dumps(payload)) > 7_000_000:
            raise ValueError('Event payload too large')
        if payload.get('source_image'):
            encoded = payload['source_image'].split(',', 1)[-1]
            with Image.open(io.BytesIO(base64.b64decode(encoded, validate=True))) as im:
                payload['image_size'] = list(ImageOps.exif_transpose(im).size)
                payload['source_image'] = thumbnail(im)
        with self.lock:
            rid = self.aliases.get(event.request_id, event.request_id)
            if event.stage == 'fog' and isinstance(canonical, str) and re.fullmatch(r'[A-Za-z0-9_.:-]{1,96}', canonical) and canonical != rid:
                self.aliases[canonical] = rid
            if rid not in self.jobs:
                self.jobs[rid] = {
                    'request_id': rid, 'mode': 'live', 'created': time.time(),
                    'stages': {s: {'status': 'pending'} for s in STAGES},
                    'payload': {}, 'events': [], 'failed': False, 'phone_received': False}
                while len(self.jobs) > 8:
                    self.jobs.popitem(last=False)
                self.aliases = {key: value for key, value in self.aliases.items() if value in self.jobs}
            job = self.jobs[rid]
            if event.stage == 'fog':
                observation = event.fog.model_dump(exclude_none=True)
                if observation['event'] in ('queued', 'replay_started', 'replay_retry', 'replay_completed') and not job['phone_received']:
                    job['failed'] = False
                    job.pop('error', None)
                if observation['event'] == 'replay_started' and not job['phone_received']:
                    for stage in ('vision', 'routing', 'gemini', 'sync'):
                        job['stages'][stage] = {'status': 'pending'}
                    for key in ('ocr_boxes', 'ocr_text', 'regions', 'crops', 'gemini_output', 'result'):
                        job['payload'].pop(key, None)
                history = job['payload'].setdefault('fog_history', [])
                history.append({**observation, 'at': time.time()})
                del history[:-40]
                previous = job['payload'].get('fog_latest', {})
                late_submission = previous.get('event', '').startswith('replay_') and not observation['event'].startswith('replay_')
                if not late_submission and (job['stages']['fog']['status'] != 'done' or event.status in ('done', 'failed')):
                    job['payload']['fog_latest'] = observation
                if observation['event'] in ('cache_hit', 'cache_returned'):
                    for skipped in ('vision', 'routing', 'gemini'):
                        if job['stages'][skipped]['status'] == 'pending':
                            job['stages'][skipped] = {'status': 'skipped', 'reason': 'Fog 快取回傳，本次未重新辨識'}
                if payload.get('image_count') == 0 and job['stages']['image']['status'] == 'pending':
                    job['stages']['image'] = {'status': 'skipped', 'reason': '本次僅查詢條碼，未上傳照片'}
                if payload.get('result') and job['stages']['sync']['status'] == 'pending':
                    job['stages']['sync']['status'] = 'active'
            if event.stage == 'error' or event.status == 'failed':
                job['failed'] = True
                job['error'] = str(payload.get('message', '處理未完成'))[:180]
                if event.stage in STAGES:
                    job['stages'][event.stage]['status'] = 'failed'
            else:
                old = job['stages'][event.stage]
                if old['status'] != 'done':
                    old['status'] = event.status
                    if event.duration_ms is not None:
                        old['duration_ms'] = event.duration_ms
                if event.stage == 'sync' and event.status == 'done':
                    job['phone_received'] = True
            job['payload'].update(payload)
            job['updated'] = time.time()
            job['events'].append({'stage': event.stage, 'status': event.status,
                                  'sender': event.sender, 'at': time.time()})
            job['events'] = job['events'][-40:]
            return {'ok': True}

    def latest(self):
        with self.lock:
            return copy.deepcopy(next(reversed(self.jobs.values()))) if self.jobs else None


store = Store()
app = FastAPI(title='FoodScan live pipeline observer')
app.include_router(demo_router)
app.include_router(rehearsal_router)


@app.get('/api/state')
def state():
    return {'job': store.latest(), 'server_time': time.time()}


@app.post('/api/events')
def event(body: Event):
    try:
        return store.accept(body)
    except (ValueError, OSError):
        raise HTTPException(status_code=400, detail='Invalid event or image')


@app.get('/api/sample')
def sample():
    fixture = ROOT.parent/'共用素材/Vision單張測試/c99_20261027'
    try:
        metrics = json.loads((fixture/'metrics.json').read_text(encoding='utf-8'))
        ocr = json.loads((fixture/'vision_response.json').read_text(encoding='utf-8'))
        paragraphs, unit = extract_paragraphs(ocr)
        groups = detect(paragraphs, unit, metrics['image_size'])
        with Image.open(metrics['source']) as im:
            picture = thumbnail(im)
            crops = [{'label': r['label'], 'image': thumbnail(im.crop(r['rect']), 400)}
                     for r in groups['regions']]
    except (OSError, ValueError, KeyError):
        raise HTTPException(status_code=404, detail='Saved sample not available')
    return {'request_id': 'c99-saved-vision', 'mode': 'sample', 'phone_received': False,
            'created': None, 'failed': False,
            'stages': {s: {'status': 'done' if s in ('image', 'vision', 'routing') else 'pending',
                           **({'duration_ms': metrics['vision_request_seconds']*1000} if s == 'vision' else {})}
                       for s in STAGES},
            'payload': {'source_image': picture, 'image_size': metrics['image_size'],
                        'regions': groups['regions'], 'crops': crops,
                        'word_boxes': metrics['word_boxes'], 'source_name': '海苔薄切洋芋片',
                        'ocr_boxes': extract_words(ocr),
                        'ocr_text': ocr['responses'][0]['fullTextAnnotation'].get('text', ''),
                        'note': '已存單張 Vision 紀錄，以目前文字密集區塊規則重新分區；尚未執行 Gemini，未連動手機。'}}


class GroundingQuery(BaseModel):
    query: str = Field(default='', max_length=1500)
    mode: Literal['general', 'vendor_events'] = 'general'
    manufacturer: str = Field(default='', max_length=200)
    brand: str = Field(default='', max_length=200)
    product: str = Field(default='', max_length=300)


@app.get('/api/grounding/config')
def grounding_config():
    return {'configured': bool(grounding_lab.credential()), 'model': grounding_lab.model()}


@app.post('/api/grounding/search')
def grounding_search(body: GroundingQuery, request: Request):
    # The LAN observer can receive phone events; only local operators initiate paid tests.
    if request.client and request.client.host not in ('127.0.0.1', '::1', 'testclient'):
        raise HTTPException(status_code=403, detail='請在儀表板電腦上開啟 localhost 執行搜尋測試。')
    query = body.query.strip()
    if body.mode == 'vendor_events':
        try:
            query = grounding_lab.event_prompt(body.manufacturer, body.brand, body.product, query)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
    elif len(query) < 3:
        raise HTTPException(status_code=422, detail='請輸入至少三個字的搜尋問題。')
    key = grounding_lab.credential()
    if not key:
        raise HTTPException(status_code=503, detail='尚未設定後端 GEMINI_API_KEY。')
    if not grounding_lab.LOCK.acquire(blocking=False):
        raise HTTPException(status_code=429, detail='已有搜尋測試進行中，請稍後再試。')
    try:
        result = grounding_lab.search(query, key)
        result['mode'] = body.mode
        result['subject'] = {'manufacturer': body.manufacturer.strip(), 'brand': body.brand.strip(),
                             'product': body.product.strip()}
        result['queried_at'] = datetime.now(timezone.utc).isoformat()
        result['review_status'] = 'unreviewed' if body.mode == 'vendor_events' else 'not_applicable'
        return result
    except Exception as exc:
        code = getattr(exc, 'code', None)
        suffix = f'（API {code}）' if isinstance(code, int) else ''
        raise HTTPException(status_code=502, detail='搜尋測試未完成'+suffix+'，請檢查模型、額度與網路設定。')
    finally:
        grounding_lab.LOCK.release()


app.mount('/', StaticFiles(directory=ROOT/'web/pipeline', html=True), name='dashboard')


if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host=os.getenv('PIPELINE_HOST', '127.0.0.1'),
                port=int(os.getenv('PIPELINE_PORT', '8787')))
