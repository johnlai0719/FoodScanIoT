import json
import sqlite3
import sys
from pathlib import Path
from unittest.mock import Mock
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from cloud_availability import Availability, install


def cloud_app(store):
    app = FastAPI()
    calls = Mock()
    @app.post('/query')
    @app.post('/analyze')
    @app.post('/api/analyze')
    def analyze():
        calls()
        return {'status': 'success', 'health_score': 80, 'product_info': {'name': 'isolated example'}}
    @app.get('/health')
    def health(): return {'status': 'ok'}
    install(app, store)
    return TestClient(app), calls


def test_503_blocks_every_analysis_alias_before_inference_and_expires(tmp_path):
    now = [1000]
    path = tmp_path/'switch.sqlite3'
    store = Availability(path, lambda: now[0])
    other_process = Availability(path, lambda: now[0])
    client, calls = cloud_app(other_process)
    store.set(True)
    for route in ['/query', '/analyze', '/api/analyze', '/api/analyze/']:
        result = client.post(route, json={})
        assert result.status_code == 503 and result.json()['demo'] is True
    assert client.get('/health').status_code == 503
    calls.assert_not_called()
    assert store.view()['rejected_requests'] == 5
    now[0] += 180
    assert client.post('/api/analyze', json={}).status_code == 200
    calls.assert_called_once()
    assert not store.view()['unavailable']


def test_dashboard_command_does_not_require_fog_key_and_rejects_cross_origin(tmp_path, monkeypatch):
    sys.path.insert(0, str(Path(__file__).parents[2]/'tools'))
    import cloud_availability_api as api
    store = Availability(tmp_path/'switch.sqlite3')
    monkeypatch.setattr(api, 'availability', store)
    monkeypatch.delenv('FOG_DEMO_CONTROL_TOKEN', raising=False)
    app = FastAPI(); app.include_router(api.router)
    client = TestClient(app)
    assert client.post('/api/demo/cloud-availability', json={'unavailable': True}, headers={'Origin': 'http://evil.test'}).status_code == 403
    assert client.post('/api/demo/cloud-availability', json={'unavailable': True}).status_code == 200
    assert store.view()['unavailable']
    assert client.post('/api/demo/cloud-availability', json={'unavailable': False}).status_code == 200
    assert not store.view()['unavailable']


def test_real_fog_logic_hit_miss_queue_and_replay_with_cloud_503(tmp_path, monkeypatch):
    from conftest import load_fog_main
    mod = load_fog_main()
    import fog_demo
    monkeypatch.setattr(fog_demo, 'fault', fog_demo.DemoFault())  # No Fog-side fault.
    monkeypatch.setattr(mod, 'DB_PATH', str(tmp_path/'fog.sqlite3'))
    monkeypatch.setattr(mod, '_queue', None)
    monkeypatch.setattr(mod, '_cloud_retry_after', 0)
    monkeypatch.setattr(mod, 'CLOUD_URL', 'http://isolated/api/analyze')
    monkeypatch.setattr(mod, 'emit_fog', Mock())
    store = Availability(tmp_path/'switch.sqlite3')
    cloud, inference = cloud_app(store)
    def forward(url, data, headers, timeout):
        response = cloud.post('/api/analyze', content=data, headers=headers)
        return SimpleNamespace(ok=response.status_code < 400, status_code=response.status_code,
                               headers=response.headers, json=response.json)
    monkeypatch.setattr(mod.requests, 'post', forward)
    mod.init_db()
    fog = TestClient(mod.app)
    # A first visit is normal, second visit uses actual SQLite cache.
    first = fog.post('/query', json={'barcode': '4710000000001', 'label_images': ['isolated-photo']})
    assert first.status_code == 200
    store.set(True)
    hit = fog.post('/query', json={'barcode': '4710000000001'})
    assert hit.json()['cached'] is True
    assert store.view()['rejected_requests'] == 0  # Cached A never reaches Cloud.
    # New B gets a genuine 503 and a durable queue acknowledgement.
    queued = fog.post('/query', json={'barcode': '4710000000002', 'label_images': ['isolated-photo']})
    assert queued.status_code == 202
    assert store.view()['rejected_requests'] == 1
    inference.assert_called_once()
    store.set(False)
    assert mod.sync_once()
    job = fog.get(queued.json()['status_url']).json()
    assert job['state'] == 'completed' and job['result']['health_score'] == 80
    assert inference.call_count == 2
    assert fog.post('/query', json={'barcode': '4710000000002'}).json()['cached'] is True
