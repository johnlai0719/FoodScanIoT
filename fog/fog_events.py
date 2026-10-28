"""Ordered, bounded, best-effort observer delivery; never delay analysis or outbox writes."""
import os
import queue
import threading
import requests

_events = queue.Queue(maxsize=256)
_lock = threading.Lock()
_worker = None


def _deliver():
    while True:
        url, body = _events.get()
        try:
            requests.post(url, json=body, timeout=.7)
        except Exception:
            pass
        finally:
            _events.task_done()


def emit(request_id, event, status='active', detail=None, payload=None):
    global _worker
    base = os.getenv('PIPELINE_DASHBOARD_URL', '').rstrip('/')
    if not base or not request_id:
        return
    try:
        with _lock:
            if _worker is None or not _worker.is_alive():
                _worker = threading.Thread(target=_deliver, name='fog-observer', daemon=True)
                _worker.start()
        _events.put_nowait((base+'/api/events', {
            'request_id': request_id, 'stage': 'fog', 'sender': 'fog', 'status': status,
            'fog': {'event': event, 'layer': 'python', **(detail or {})}, 'payload': payload or {},
        }))
    except Exception:
        pass


def result_summary(result):
    if result.get('status') != 'success':
        return {}
    return {'result': {'product_name': (result.get('product_info') or {}).get('name'),
                       'health_score': result.get('health_score')}}
