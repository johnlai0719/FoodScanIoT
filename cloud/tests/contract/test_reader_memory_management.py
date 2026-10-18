"""Resource and lifecycle failure contracts; no real models or network calls."""
import asyncio
import base64
from contextlib import contextmanager
import io
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'reader'))
import input_limits as limits
import model_manager as mm
import pipeline as pipe
from request_limits import RequestLimits


def image64(size=(100, 100)):
    with Image.new('RGB', size) as image:
        b = io.BytesIO()
        image.save(b, 'JPEG')
    return base64.b64encode(b.getvalue()).decode()


def test_app_portrait_is_accepted_without_resizing():
    images = limits.decode_images([image64((1920, 2560))])
    assert images[0].size == (1920, 2560)
    images[0].close()


def test_original_rejected_before_rgb_allocation(monkeypatch):
    raw = image64((4284, 5712))
    monkeypatch.setattr(Image.Image, 'convert', lambda *a, **k: pytest.fail('RGB allocated before dimension check'))
    with pytest.raises(limits.InputRejected, match='像素'):
        limits.decode_images([raw])


@pytest.mark.parametrize('values', [[], ['a'] * 5, ['not base64!']])
def test_invalid_image_payloads(values):
    with pytest.raises(limits.InputRejected):
        limits.decode_images(values)


def test_total_pixels_and_bytes_are_bounded(monkeypatch):
    monkeypatch.setattr(limits, 'MAX_TOTAL_PIXELS', 15000)
    with pytest.raises(limits.InputRejected):
        limits.decode_images([image64(), image64()])
    monkeypatch.setattr(limits, 'MAX_IMAGE_BYTES', 20)
    with pytest.raises(limits.InputRejected):
        limits.decode_images([image64()])


class Process:
    def __init__(self, pid):
        self.pid = pid
        self.code = None
    def poll(self):
        return self.code


@pytest.fixture
def managed(monkeypatch):
    started, stopped = [], []
    def start(config):
        p = Process(len(started) + 1)
        started.append((config['name'], p))
        return p
    def stop(p):
        p.code = 0
        stopped.append(p.pid)
    monkeypatch.setattr(mm.start_models, 'alive', lambda port: any(
        p.poll() is None and name == ('hunyuan' if port == 8177 else 'qwen') for name, p in started))
    monkeypatch.setattr(mm.start_models, 'start', start)
    monkeypatch.setattr(mm.start_models, 'stop', stop)
    manager = mm.ModelManager([dict(name='hunyuan', port=8177), dict(name='qwen', port=8179)])
    return manager, started, stopped


def test_pause_waits_and_restores_after_stage_exception(managed):
    manager, started, stopped = managed
    manager.ensure_ready()
    original = manager.processes['hunyuan']
    with pytest.raises(ValueError, match='inference failed'):
        with manager.pause_hunyuan():
            assert original.poll() == 0
            assert manager.state == 'paused'
            raise ValueError('inference failed')
    assert manager.state == 'ready'
    assert manager.processes['hunyuan'] is not original
    assert manager.processes['qwen'] is started[1][1]
    manager.close()
    assert all(p.poll() == 0 for _, p in started)


def test_foreign_listener_never_stopped(managed, monkeypatch):
    manager, started, stopped = managed
    monkeypatch.setattr(mm.start_models, 'alive', lambda port: True)
    with pytest.raises(mm.ModelUnavailable, match='外部'):
        manager.ensure_ready()
    assert not started and not stopped


def test_partial_start_failure_releases_created_model(managed, monkeypatch):
    manager, started, stopped = managed
    original_start = mm.start_models.start
    def fail_qwen(config):
        if config['name'] == 'qwen':
            raise RuntimeError('missing model')
        return original_start(config)
    monkeypatch.setattr(mm.start_models, 'start', fail_qwen)
    with pytest.raises(mm.ModelUnavailable):
        manager.ensure_ready()
    assert stopped == [started[0][1].pid]


def test_reload_failure_can_recover_next_request(managed, monkeypatch):
    manager, started, stopped = managed
    manager.ensure_ready()
    start = mm.start_models.start
    with monkeypatch.context() as patch:
        patch.setattr(mm.start_models, 'start', lambda config: (_ for _ in ()).throw(RuntimeError('reload failed')))
        with pytest.raises(mm.ModelUnavailable):
            with manager.pause_hunyuan():
                pass
    assert manager.state == 'failed'
    manager.ensure_ready()
    assert manager.state == 'ready'
    manager.close()


@pytest.mark.parametrize('finish,lines', [('length', ['partial']), ('error: context overflow', []), ('stop', [])])
def test_region_failure_cannot_emit_normal_result(monkeypatch, tmp_path, finish, lines):
    import json
    boxes = tmp_path/'boxes.json'
    boxes.write_text(json.dumps({'images': [{'path':'x', 'lines':[]}]}))
    monkeypatch.setattr(pipe, '_out', lambda *a: boxes)
    monkeypatch.setattr(pipe, '_mods', {
        'RC': SimpleNamespace(find_regions=lambda _: {'ingredients': [0,0,10,10], 'nutrition': None}),
        'RV': SimpleNamespace(CFG={'ingredients':'prompt'}, read_region=lambda *a: {'finish':finish,'lines':lines})})
    with pytest.raises(pipe.ReaderInferenceError):
        pipe._vlcrop('case', ['x'], [object()])


def test_fallback_cleans_cache_before_model_reload(monkeypatch, tmp_path):
    import json
    vl, nutri = tmp_path/'vl.json', tmp_path/'nutri.json'
    vl.write_text(json.dumps({'images':[{'regions':{'nutrition':{'found':False}}}]}))
    events = []
    class Manager:
        @contextmanager
        def pause_hunyuan(self):
            events.append('stopped')
            try:
                yield
            finally:
                events.append('reloaded')
    def infer(*a, **kwargs):
        assert kwargs['strict'] is True
        events.append('inference')
        raise RuntimeError('OOM')
    monkeypatch.setattr(pipe, '_model_manager', Manager())
    monkeypatch.setattr(pipe, '_mods', {'N':SimpleNamespace(run=infer)})
    monkeypatch.setattr(pipe, '_out', lambda preset, cid: vl if preset == pipe.PRESET_VL else nutri)
    monkeypatch.setattr(pipe, '_release_paddle_cache', lambda: events.append('cache released'))
    with pytest.raises(pipe.ReaderInferenceError):
        pipe._nutrition('case')
    assert events == ['stopped','inference','cache released','reloaded']


def test_body_and_concurrency_limits_before_app():
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        async def app(scope, receive, send):
            await receive()
            entered.set()
            await release.wait()
        gate = RequestLimits(app, max_bytes=4, capacity=1)
        scope = {'type':'http','method':'POST','path':'/read','headers':[]}
        async def receive():
            return {'type':'http.request','body':b'1234'}
        sent = []
        async def send(message):
            sent.append(message)
        first = asyncio.create_task(gate(scope, receive, send))
        await entered.wait()
        await gate(scope, receive, send)
        assert sent[0]['status'] == 503
        release.set()
        await first
        sent.clear()
        async def too_big():
            return {'type':'http.request','body':b'12345'}
        await gate(scope, too_big, send)
        assert sent[0]['status'] == 413
        # Oversize rejection must return its slot.
        sent.clear()
        await gate(scope, receive, send)
        assert not sent
    asyncio.run(scenario())


@pytest.mark.parametrize('exc,status', [
    (limits.InputRejected('too large'), 413),
    (limits.InputRejected('bad image', 400), 400),
    (pipe.ReaderInferenceError('truncated'), 502),
    (mm.ModelUnavailable('reload failed'), 503),
])
def test_http_error_mapping_and_slot_recovery(monkeypatch, exc, status):
    import service
    from fastapi.testclient import TestClient
    monkeypatch.setattr(service, 'SECRET', 'unit-test')
    monkeypatch.setattr(service, 'manager', object())
    monkeypatch.setattr(pipe, 'is_ready', lambda: True)
    def fail(*args):
        raise exc
    monkeypatch.setattr(pipe, 'read', fail)
    # No context manager: do not run GPU/model startup hooks in this pure test.
    client = TestClient(service.app)
    for _ in range(3):
        response = client.post('/read', json={'label_images':['x']}, headers={'X-API-Key':'unit-test'})
        assert response.status_code == status
    assert client.post('/read', json={'label_images':['x']}).status_code == 401


def test_chunked_body_and_declared_oversize_never_reach_app():
    async def scenario():
        async def app(*args):
            pytest.fail('oversize request reached app')
        gate = RequestLimits(app, max_bytes=4)
        sent = []
        async def send(message):
            sent.append(message)
        scope = {'type':'http','method':'POST','path':'/read','headers':[(b'content-length',b'5')]}
        async def unread():
            pytest.fail('declared oversize body was read')
        await gate(scope, unread, send)
        assert sent[0]['status'] == 413
        sent.clear()
        chunks = iter([{'type':'http.request','body':b'123','more_body':True},
                       {'type':'http.request','body':b'45','more_body':False}])
        async def receive():
            return next(chunks)
        await gate({**scope,'headers':[]}, receive, send)
        assert sent[0]['status'] == 413
    asyncio.run(scenario())
