"""Optional, best-effort demo telemetry. Never changes an analysis response."""
import os
import requests


def emit(request_id, stage, status, payload=None, duration_ms=None):
    base = os.getenv('PIPELINE_DASHBOARD_URL', '').rstrip('/')
    if not base or not request_id:
        return
    body = {'request_id': request_id, 'stage': stage, 'status': status,
            'sender': 'cloud', 'payload': payload or {}}
    if duration_ms is not None:
        body['duration_ms'] = duration_ms
    try:
        requests.post(base+'/api/events', json=body, timeout=.7)
    except requests.RequestException:
        pass
