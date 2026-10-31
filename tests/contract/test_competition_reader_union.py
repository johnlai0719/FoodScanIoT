"""Exercise the real reader route with network dependencies mocked."""
import base64
import io
import sys
from pathlib import Path
from unittest.mock import Mock

from fastapi.testclient import TestClient
from PIL import Image
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]/'tools'))
import reader_vision_competition as reader
from benchmark_vision_union import envelope
from vision_text_envelope import route, ROUTING_POLICY
from gemini_compare import PROMPT


def photo():
    stream = io.BytesIO()
    Image.new('RGB', (1000, 1000), 'white').save(stream, format='PNG')
    return base64.b64encode(stream.getvalue()).decode()


def ocr():
    return {'responses': [{'fullTextAnnotation': {'text': '成分表', 'pages': [{'blocks': [{'paragraphs': [
        {'words': [{'symbols': [{'text': '成分表'}], 'boundingBox': {'vertices': [
            {'x': 200, 'y': 200}, {'x': 600, 'y': 200}, {'x': 600, 'y': 600}, {'x': 200, 'y': 600}]}}]}]}]}]}}]}


@pytest.mark.parametrize('response', [ocr(), {'responses': [{}]}])
def test_production_geometry_matches_experiment(response):
    plan = route(response, (1000, 1000))
    rect, fallback, count = envelope(response, (1000, 1000))
    assert len(plan['regions']) == 1
    assert plan['regions'][0]['rect'] == list(rect)
    assert plan['regions'][0]['fallback'] == fallback
    assert plan['regions'][0]['word_box_count'] == count


@pytest.mark.parametrize('response,expected_size', [(ocr(), (480, 480)), ({'responses': [{'fullTextAnnotation': {'text': 'ingredients'}}]}, (1000, 1000))])
def test_reader_sends_one_crop_per_photo_without_extra_original(monkeypatch, response, expected_size):
    monkeypatch.setenv('GOOGLE_APPLICATION_CREDENTIALS', 'mock-only')
    monkeypatch.setenv('GEMINI_API_KEY', 'mock-only')
    monkeypatch.setenv('API_SHARED_SECRET', 'mock-secret')
    monkeypatch.setattr(reader.service_account.Credentials, 'from_service_account_file', Mock(return_value=Mock()))
    monkeypatch.setattr(reader, 'vision', Mock(return_value=response))
    emitted = Mock(); monkeypatch.setattr(reader, 'emit', emitted)
    client = Mock(); monkeypatch.setattr(reader.genai, 'Client', Mock(return_value=client))
    def inspect_inputs(client_arg, model, prompt, images, schema):
        assert prompt == PROMPT
        assert len(images) == 2
        assert [im.size for im in images] == [expected_size, expected_size]
        return {'name': 'synthetic-test', 'ingredients_list': []}, {'model_version': 'mock'}
    monkeypatch.setattr(reader, 'gemini_request', inspect_inputs)
    api = TestClient(reader.app)
    result = api.post('/read', json={'label_images': [photo(), photo()], 'barcode': 'TEST'},
                      headers={'X-API-Key': 'mock-secret', 'X-Request-Id': 'test-union-only'})
    assert result.status_code == 200
    assert result.json()['_meta']['routing_policy'] == ROUTING_POLICY
    assert result.json()['_meta']['gemini_input_count'] == 2
    event = next(c for c in emitted.call_args_list if c.args[1:3] == ('routing', 'done'))
    assert len(event.args[3]['regions']) == len(event.args[3]['crops']) == 1
    assert event.args[3]['routing_policy'] == ROUTING_POLICY
    client.close.assert_called_once()


def test_reader_health_and_auth_guard(monkeypatch):
    monkeypatch.setenv('API_SHARED_SECRET', 'mock-secret')
    vision = Mock(); monkeypatch.setattr(reader, 'vision', vision)
    client = TestClient(reader.app)
    assert client.get('/health').json()['routing_policy'] == ROUTING_POLICY
    assert client.post('/read', json={'label_images': [photo()]}).status_code == 401
    vision.assert_not_called()
