"""Exercise Fog endpoints with isolated SQLite and a simulated Cloud."""
import json
import sqlite3
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from transforms import CloudUnavailable


@pytest.fixture
def fog(tmp_path, monkeypatch):
    from conftest import load_fog_main
    mod = load_fog_main()
    monkeypatch.setattr(mod, 'DB_PATH', str(tmp_path / 'fog.db'))
    monkeypatch.setattr(mod, '_queue', None)
    monkeypatch.setattr(mod, '_cloud_retry_after', 0)
    mod.init_db()
    return mod, TestClient(mod.app)


def offline(*args):
    raise CloudUnavailable('offline')


def test_real_queue_transitions_emit_metadata_without_photos_or_profile(fog, monkeypatch):
    mod, client = fog
    events = []
    monkeypatch.setattr(mod, 'emit_fog', lambda *args, **kwargs: events.append((args, kwargs)))
    monkeypatch.setattr(mod, 'forward_cloud', offline)
    response = client.post('/query', json={'barcode':'471','label_images':['private-photo'], 'user_conditions':{'group':'child'}},
                           headers={'X-Request-Id':'observed-job','X-Demo-Scenario':'queue'})
    assert response.status_code == 202
    assert [args[1] for args, kwargs in events] == ['received','cache_miss','cloud_forwarded','cloud_unavailable','queued']
    monkeypatch.setattr(mod, 'forward_cloud', lambda *args: (SimpleNamespace(ok=True,status_code=200,headers={}), {'status':'success','health_score':0}))
    mod.sync_once()
    assert [args[1] for args, kwargs in events][-2:] == ['replay_started', 'replay_completed']
    assert 'private-photo' not in repr(events) and 'user_conditions' not in repr(events)
    assert events[-1][0][0] == 'observed-job'


def test_offline_upload_is_durable_sanitized_and_not_an_analysis(fog, monkeypatch):
    mod, client = fog
    monkeypatch.setattr(mod, 'forward_cloud', offline)
    payload = {'barcode': '471', 'label_images': ['image'], 'user_conditions': {'group': 'child'}, 'device_id': 'secret'}
    r = client.post('/query', json=payload, headers={'X-Request-Id': 'app-original'})
    assert r.status_code == 202
    body = r.json()
    assert body['status'] == 'queued'
    assert body['request_id'] == 'app-original'
    assert 'health_score' not in body
    assert r.headers['X-Cache'] == 'QUEUED'
    duplicate = client.post('/query', json=payload, headers={'X-Request-Id': 'app-duplicate'}).json()
    assert duplicate['queue_id'] == body['queue_id']
    assert duplicate['request_id'] == 'app-original'
    job = mod.queue_store().claim()
    assert job['payload'] == {'barcode': '471', 'label_images': ['image']}
    status = client.get(body['status_url']).json()
    assert status['state'] == 'processing'
    assert status['request_id'] == 'app-original'
    assert 'payload_json' not in status


def test_cache_hit_with_photo_never_calls_cloud_or_queue(fog, monkeypatch):
    mod, client = fog
    with sqlite3.connect(mod.DB_PATH) as db:
        db.execute('INSERT INTO cache VALUES (?,?,?,?)', ('471', json.dumps({'status': 'success','health_score': 80}), 0, 3600))
    def unexpected(*args):
        pytest.fail('cache hit should not upload')
    monkeypatch.setattr(mod, 'forward_cloud', unexpected)
    r = client.post('/query', json={'barcode': '471', 'label_images': ['image']})
    assert r.status_code == 200
    assert r.json()['cached'] is True
    assert r.headers['X-Cache'] == 'HIT-STALE'
    assert mod.queue_store().stats() == {}


def test_worker_completes_job_and_later_query_uses_cache(fog, monkeypatch):
    mod, client = fog
    monkeypatch.setattr(mod, 'forward_cloud', offline)
    queued = client.post('/query', json={'barcode': '471', 'label_images': ['image']}).json()
    def online(*args):
        return SimpleNamespace(ok=True,status_code=200,headers={}), {'status': 'success','health_score': 80}
    monkeypatch.setattr(mod, 'forward_cloud', online)
    assert mod.sync_once()
    done = client.get(queued['status_url']).json()
    assert done['state'] == 'completed'
    assert done['result']['health_score'] == 80
    r = client.post('/query', json={'barcode': '471'})
    assert r.headers['X-Cache'] == 'HIT'


def test_online_miss_stays_synchronous(fog, monkeypatch):
    mod, client = fog
    monkeypatch.setattr(mod, 'forward_cloud', lambda *args: (SimpleNamespace(ok=True,status_code=200,headers={}), {'status':'success','health_score':80}))
    assert client.post('/query', json={'barcode':'471'}).status_code == 200
    assert mod.queue_store().stats() == {}


def test_local_only_opt_out_is_never_queued_or_uploaded(fog, monkeypatch):
    mod, client = fog
    monkeypatch.setattr(mod, 'forward_cloud', lambda *args: pytest.fail('privacy opt-out uploaded'))
    r = client.post('/query', json={'barcode':'471','label_images':['image']}, headers={'X-Local-Only':'1'})
    assert r.status_code == 409
    assert mod.queue_store().stats() == {}


def test_storage_failure_does_not_claim_upload_was_saved(fog, monkeypatch):
    mod, client = fog
    monkeypatch.setattr(mod, 'forward_cloud', offline)
    q = mod.queue_store()
    def full(*args):
        raise sqlite3.OperationalError('disk full')
    monkeypatch.setattr(q, 'enqueue', full)
    r = client.post('/query', json={'barcode':'471'})
    assert r.status_code == 503
    assert r.json()['queued'] is False


@pytest.mark.parametrize('code', [408, 429, 500, 503, 530])
def test_transient_cloud_http_status_is_queued(fog, monkeypatch, code):
    mod, client = fog
    monkeypatch.setattr(mod, 'CLOUD_URL', 'https://cloud.invalid/api/analyze')
    monkeypatch.setattr(mod.requests, 'post', lambda *args, **kwargs: SimpleNamespace(status_code=code))
    assert client.post('/query', json={'barcode':'471'}).status_code == 202
    assert mod.queue_store().stats() == {'pending': 1}


def test_permanent_cloud_error_is_not_queued_even_with_html_body(fog, monkeypatch):
    mod, client = fog
    monkeypatch.setattr(mod, 'CLOUD_URL', 'https://cloud.invalid/api/analyze')
    def invalid_json():
        raise ValueError('html')
    monkeypatch.setattr(mod.requests, 'post', lambda *args, **kwargs: SimpleNamespace(ok=False,status_code=401,json=invalid_json,headers={}))
    assert client.post('/query', json={'barcode':'471'}).status_code == 401
    assert mod.queue_store().stats() == {}


def test_pending_job_is_not_uploaded_again_on_resubmission(fog, monkeypatch):
    mod, client = fog
    monkeypatch.setattr(mod, 'forward_cloud', offline)
    payload = {'barcode':'471', 'label_images':['image']}
    first = client.post('/query', json=payload).json()
    monkeypatch.setattr(mod, 'forward_cloud', lambda *args: pytest.fail('duplicate upload'))
    assert client.post('/query', json=payload).json()['queue_id'] == first['queue_id']


def test_lifespan_worker_automatically_drains_queue(fog, monkeypatch):
    import time
    mod, _ = fog
    ids = [mod.queue_store().enqueue({'barcode': code}) for code in ['111', '222']]
    calls = []
    def online(payload, rid):
        calls.append(payload['barcode'])
        return SimpleNamespace(ok=True,status_code=200,headers={}), {'status':'success','health_score':80}
    monkeypatch.setattr(mod, 'forward_cloud', online)
    with TestClient(mod.app) as client:
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            if client.get('/queue/' + ids[-1]).json()['state'] == 'completed':
                break
            time.sleep(0.02)
        assert calls == ['111', '222']
        assert client.get('/queue/' + ids[-1]).json()['state'] == 'completed'
