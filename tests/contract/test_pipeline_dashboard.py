"""Live observer semantics; synthetic events are isolated from the running service."""
import sys
import json
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from PIL import Image

sys.path.insert(0, str(Path(__file__).parents[2]/'tools'))
from pipeline_dashboard import Store, Event, app
import pipeline_dashboard


def make(stage, status, sender='cloud', rid='scan-1', payload=None):
    return Event(request_id=rid, stage=stage, status=status, sender=sender, payload=payload or {})


def test_cloud_ready_is_not_a_phone_receipt():
    store = Store()
    store.accept(make('sync', 'active'))
    assert not store.latest()['phone_received']
    with pytest.raises(ValueError):
        store.accept(make('sync', 'done'))
    store.accept(make('sync', 'done', sender='app'))
    assert store.latest()['phone_received']


def test_late_events_do_not_regress_completed_stage():
    store = Store()
    store.accept(make('sync', 'done', sender='app'))
    store.accept(make('sync', 'active'))
    assert store.latest()['stages']['sync']['status'] == 'done'


def test_requests_do_not_mix_or_invent_skipped_processing():
    store = Store()
    store.accept(make('vision', 'done'))
    store.accept(make('sync', 'done', sender='app', rid='cached-2'))
    assert store.latest()['request_id'] == 'cached-2'
    assert store.latest()['stages']['vision']['status'] == 'pending'
    store.accept(make('gemini', 'done'))
    assert store.latest()['request_id'] == 'cached-2'


def test_reject_invalid_image_without_creating_job():
    store = Store()
    with pytest.raises(ValueError):
        store.accept(make('image', 'done', payload={'source_image': 'not base64!'}))
    assert store.latest() is None


def test_failure_marks_active_stage_and_never_acknowledges_phone():
    store = Store()
    store.accept(make('gemini', 'active'))
    store.accept(make('gemini', 'failed', payload={'message': 'test failure'}))
    assert store.latest()['stages']['gemini']['status'] == 'failed'
    assert store.latest()['failed']
    assert not store.latest()['phone_received']


def test_static_site_and_schema():
    client = TestClient(app)
    assert client.get('/').status_code == 200
    assert 'FoodScan' in client.get('/').text
    assert client.get('/api/state').status_code == 200
    assert client.post('/api/events', json={'request_id': '../invalid'}).status_code == 422


def test_sample_never_claims_gemini_or_phone_completion(tmp_path, monkeypatch):
    # Synthetic fixture validates the endpoint without requiring private demo photos.
    fixture = tmp_path/'共用素材/Vision單張測試/c99_20261027'
    (fixture/'自動兩類區域').mkdir(parents=True)
    source = tmp_path/'sample.png'
    Image.new('RGB', (40, 30), 'white').save(source)
    (fixture/'metrics.json').write_text(json.dumps({'source': str(source), 'image_size': [40, 30],
        'word_boxes': 1, 'vision_request_seconds': .1}), encoding='utf-8')
    (fixture/'自動兩類區域/auto_groups.json').write_text(json.dumps({'regions': []}), encoding='utf-8')
    (fixture/'vision_response.json').write_text(json.dumps({'responses': [{'fullTextAnnotation': {
        'text': '測', 'pages': [{'blocks': [{'paragraphs': [{'words': [{'symbols': [{'text': '測'}],
        'boundingBox': {'vertices': [{}, {'x': 10}, {'x': 10, 'y': 20}, {'y': 20}]}}]}]}]}]}}]}), encoding='utf-8')
    monkeypatch.setattr(pipeline_dashboard, 'ROOT', tmp_path/'專案')
    client = TestClient(app)
    response = client.get('/api/sample')
    assert response.status_code == 200
    data = response.json()
    assert data['mode'] == 'sample'
    assert data['stages']['gemini']['status'] == 'pending'
    assert not data['phone_received']
    assert data['payload']['ocr_boxes']
    assert len(data['payload']['ocr_boxes']) == data['payload']['word_boxes']
    assert data['payload']['ocr_text']
    assert 'gemini_output' not in data['payload']
