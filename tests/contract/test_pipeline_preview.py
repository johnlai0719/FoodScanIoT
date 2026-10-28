import sys
from pathlib import Path
from unittest.mock import Mock
from fastapi.testclient import TestClient
import pytest
sys.path.insert(0, str(Path(__file__).parents[2]/'tools'))
import pipeline_dashboard_preview as preview


@pytest.mark.parametrize('name,outcome', [('hit','cache_returned'),('miss','cloud_forwarded'),
    ('queue','queued'),('replay','replay_completed'),('bypass','cloud_forwarded')])
def test_synthetic_preview_is_explicit_and_never_calls_external_services(monkeypatch, name, outcome):
    post = Mock()
    monkeypatch.setattr(preview.dashboard.grounding_lab, 'search', post)
    client = TestClient(preview.app)
    assert client.get('/preview/'+name).status_code == 200
    job = client.get('/api/state').json()['job']
    assert job['mode'] == 'simulation'
    assert job['payload']['fog_latest']['event'] == outcome
    assert not job['phone_received']
    assert client.post('/api/grounding/search', json={'query':'example'}).status_code == 403
    post.assert_not_called()
