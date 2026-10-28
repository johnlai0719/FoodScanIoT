import sys
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).parents[2]/'server'))
import vision_backend
import pipeline_events


def test_reader_preserves_request_id_and_response(monkeypatch):
    response = Mock(status_code=200)
    response.json.return_value = {'name': 'test', '_meta': {'reader': 'google_vision_rules_gemini'}}
    post = Mock(return_value=response)
    monkeypatch.setattr(vision_backend.requests, 'post', post)
    result = vision_backend._read_via_reader(['test-image'], 'TEST', 'scan-123')
    assert post.call_args.kwargs['headers']['X-Request-Id'] == 'scan-123'
    assert result == response.json.return_value


def test_disabled_observer_does_not_send_or_change_processing(monkeypatch):
    monkeypatch.delenv('PIPELINE_DASHBOARD_URL', raising=False)
    post = Mock()
    monkeypatch.setattr(pipeline_events.requests, 'post', post)
    pipeline_events.emit('scan-1', 'vision', 'active')
    post.assert_not_called()
