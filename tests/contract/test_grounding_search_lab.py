"""Grounding provenance and explicit-call boundary; no network requests."""
import sys
from pathlib import Path
from types import SimpleNamespace as N
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parents[2]/'tools'))
import grounding_search_lab as lab
from pipeline_dashboard import app


def response(metadata=None):
    return N(candidates=[N(content=N(parts=[N(text='private thought', thought=True), N(text='回答')]),
                           grounding_metadata=metadata)], model_version='test-model')


def test_no_metadata_is_not_grounded():
    result = lab.normalize(response())
    assert result['answer'] == '回答'
    assert not result['has_grounding']
    assert result['sources'] == []


def test_source_indices_preserved_and_invalid_links_removed():
    metadata = N(grounding_chunks=[N(web=N(uri='javascript:alert(1)')),
                                  N(web=N(uri='https://example.org/doc', title='文件')),
                                  N(web=N(uri='https://user:secret@example.org'))],
                 grounding_supports=[N(segment=N(text='依據'), grounding_chunk_indices=[0, 1, 2, 99, -1, True])],
                 web_search_queries=['查詢'], search_entry_point=N(rendered_content='<div>Google</div>'))
    result = lab.normalize(response(metadata))
    assert result['sources'] == [{'id': 2, 'title': '文件', 'url': 'https://example.org/doc'}]
    assert result['supports'] == [{'text': '依據', 'source_ids': [2]}]
    assert result['has_grounding']
    assert result['search_suggestions_html'] == '<div>Google</div>'


def test_explicit_endpoint_and_configuration_hide_key(monkeypatch):
    monkeypatch.setattr(lab, 'credential', lambda: 'secret-value')
    calls = []
    monkeypatch.setattr(lab, 'search', lambda query, key: calls.append((query, key)) or {'answer': 'ok'})
    with TestClient(app) as client:
        config = client.get('/api/grounding/config')
        assert config.json()['configured']
        assert 'secret-value' not in config.text
        assert not calls
        assert client.post('/api/grounding/search', json={'query': '   '}).status_code == 422
        assert not calls
        assert client.post('/api/grounding/search', json={'query': '查詢官方文件'}).json()['answer'] == 'ok'
        assert calls == [('查詢官方文件', 'secret-value')]


def test_missing_key_busy_and_safe_error(monkeypatch):
    with TestClient(app) as client:
        monkeypatch.setattr(lab, 'credential', lambda: '')
        assert client.post('/api/grounding/search', json={'query': '官方文件'}).status_code == 503
        monkeypatch.setattr(lab, 'credential', lambda: 'secret-value')
        lab.LOCK.acquire()
        try:
            assert client.post('/api/grounding/search', json={'query': '官方文件'}).status_code == 429
        finally:
            lab.LOCK.release()
        def fail(*args):
            raise RuntimeError('secret-value')
        monkeypatch.setattr(lab, 'search', fail)
        result = client.post('/api/grounding/search', json={'query': '官方文件'})
        assert result.status_code == 502
        assert 'secret-value' not in result.text
        assert not lab.LOCK.locked()


def test_remote_observer_cannot_trigger_search(monkeypatch):
    monkeypatch.setattr(lab, 'credential', lambda: 'secret-value')
    with TestClient(app, client=('192.168.1.10', 50000)) as client:
        assert client.post('/api/grounding/search', json={'query': '官方文件'}).status_code == 403


def test_vendor_identity_is_required_before_search(monkeypatch):
    calls = []
    monkeypatch.setattr(lab, 'search', lambda *args: calls.append(args))
    with TestClient(app) as client:
        result = client.post('/api/grounding/search', json={'mode': 'vendor_events', 'manufacturer': '  ', 'product': '商品'})
        assert result.status_code == 422
        assert not calls


def test_brand_search_preserves_subject_and_unreviewed_state(monkeypatch):
    monkeypatch.setattr(lab, 'credential', lambda: 'secret-value')
    calls = []
    monkeypatch.setattr(lab, 'search', lambda query, key: calls.append(query) or {'answer': '候選'})
    with TestClient(app) as client:
        result = client.post('/api/grounding/search', json={'mode': 'vendor_events', 'brand': '測試品牌',
                                                         'product': '測試商品', 'query': '關注近年報導'})
    assert result.status_code == 200
    data = result.json()
    assert data['review_status'] == 'unreviewed'
    assert data['subject'] == {'manufacturer': '', 'brand': '測試品牌', 'product': '測試商品'}
    assert data['queried_at']
    assert '測試品牌' in calls[0] and '測試商品' in calls[0]
    assert '補充查詢方向' in calls[0]
