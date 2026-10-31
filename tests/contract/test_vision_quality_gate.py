import base64
import io
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest
from PIL import Image
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'tools'))
sys.path.insert(0, str(ROOT/'server'))
from vision_quality_gate import assess
import reader_vision_competition as reader
from vision_backend import ocr_retake_response


def response(text):
    return {'responses': [{'fullTextAnnotation': {'text': text}}]}


@pytest.mark.parametrize('text,decision', [('', 'retake'), ('  !％…', 'retake'),
    ('A2', 'retake'), ('成分表', 'pass'), ('abc', 'pass'), ('123', 'pass'), ('原材料', 'pass')])
def test_only_almost_empty_text_is_blocked(text, decision):
    assert assess([response(text)])['decision'] == decision


def test_multiple_photos_and_missing_geometry_do_not_force_retake():
    assert assess([response(''), response('Ingredients')])['decision'] == 'pass'
    assert assess([response('成分'), response('水')])['character_count'] == 3
    assert assess([{'responses': [{'textAnnotations': [{'description': '原材料'}]}]}])['decision'] == 'pass'


def test_retake_skips_gemini_and_cloud_rejects_without_score(monkeypatch):
    monkeypatch.setenv('GOOGLE_APPLICATION_CREDENTIALS', 'mock-only')
    monkeypatch.setenv('GEMINI_API_KEY', 'mock-only')
    monkeypatch.setenv('API_SHARED_SECRET', 'mock-secret')
    monkeypatch.setattr(reader.service_account.Credentials, 'from_service_account_file', Mock(return_value=Mock()))
    monkeypatch.setattr(reader, 'vision', Mock(return_value=response('!')))
    events = Mock(); monkeypatch.setattr(reader, 'emit', events)
    constructor = Mock(); monkeypatch.setattr(reader.genai, 'Client', constructor)
    request = Mock(); monkeypatch.setattr(reader, 'gemini_request', request)
    crop = Mock(); monkeypatch.setattr(reader, 'route', crop)
    stream = io.BytesIO(); Image.new('RGB', (32, 32)).save(stream, format='PNG')
    result = TestClient(reader.app).post('/read', json={
        'label_images': [base64.b64encode(stream.getvalue()).decode()], 'barcode': '4710018123456'},
        headers={'X-API-Key': 'mock-secret'})
    assert result.status_code == 200
    data = result.json()
    assert data['_meta']['ocr_quality']['decision'] == 'retake'
    assert data['_meta']['gemini_input_count'] == 0
    constructor.assert_not_called(); request.assert_not_called(); crop.assert_not_called()
    assert not any(c.args[1] == 'gemini' for c in events.call_args_list)
    assert any(c.args[3].get('gemini_skipped') for c in events.call_args_list if len(c.args) > 3)
    rejected = ocr_retake_response(data)
    assert rejected['status'] == 'rejected' and '重新拍攝' in rejected['message']
    assert 'health_score' not in rejected


def test_existing_reader_results_are_unchanged_and_retake_precedes_db():
    assert ocr_retake_response({'is_food_label': False}) is None
    assert ocr_retake_response({'_meta': {'ocr_quality': {'policy': 'vision_min_text_v1', 'decision': 'pass'}}}) is None
    source = (ROOT/'server/main.py').read_text(encoding='utf-8')
    start = source.index('vision_data = await vision_backend.analyze(')
    assert source.index('return retake', start) < source.index('db = get_db_conn()', start)
