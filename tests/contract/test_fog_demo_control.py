import json
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parents[2]/'tools'))
sys.path.insert(0, str(Path(__file__).parents[2]/'fog'))
import fog_demo
import fog_demo_control as controller
from pipeline_dashboard import app


def command(**overrides):
    return {'generation': 'g1', 'offline': True, 'expires_in': 120,
            'lease_seconds': 4, 'auto_restore_seconds': 5, **overrides}


def test_countdown_only_starts_after_durable_queue_and_is_not_reset():
    now = [0]
    fault = fog_demo.DemoFault(lambda: now[0])
    fault.apply(command())
    now[0] = 3; fault.apply(command(expires_in=117))
    assert fault.snapshot()['remaining_seconds'] is None
    fault.queued('a'*32)
    now[0] = 5; fault.apply(command(expires_in=115)); fault.queued('b'*32)
    assert fault.snapshot()['remaining_seconds'] == 3
    assert fault.snapshot()['queue_id'] == 'a'*32
    now[0] = 8; fault.apply(command(expires_in=112))
    assert not fault.blocked()
    now[0] = 9; fault.apply(command(expires_in=111))
    assert not fault.blocked()


@pytest.mark.parametrize('restore', ['lease', 'manual', 'maximum'])
def test_fault_expires_and_never_changes_network(restore):
    now = [0]; fault = fog_demo.DemoFault(lambda: now[0]); fault.apply(command(auto_restore_seconds=None))
    if restore == 'lease': now[0] = 4
    elif restore == 'manual': fault.apply(command(generation='g2', offline=False, expires_in=0))
    else:
        for i in range(1,121):
            now[0] = i; fault.apply(command(expires_in=120-i, auto_restore_seconds=None))
    assert not fault.blocked()


def test_control_api_requires_token_and_same_origin(monkeypatch):
    monkeypatch.setenv('FOG_DEMO_CONTROL_TOKEN', 'test-only-secret')
    monkeypatch.setattr(controller, 'control', controller.Control())
    api = TestClient(app)
    assert api.get('/api/fog-control?fog_id=foodscan-pi').status_code == 401
    assert api.get('/api/fog-control?fog_id=wrong',headers={'X-Fog-Control-Key':'test-only-secret'}).status_code == 403
    assert api.post('/api/demo/cloud', json={'offline':True},headers={'Origin':'http://evil.example'}).status_code == 403
    assert api.post('/api/demo/cloud', json={'offline':True,'auto_restore_seconds':1}).status_code == 422
    r = api.post('/api/demo/cloud',json={'offline':True})
    assert r.status_code == 200 and r.json()['ack'] is None
    assert 'test-only-secret' not in r.text
    generation = r.json()['generation']
    ack = {'fog_id':'foodscan-pi','generation':generation,'blocked':True,'remaining_seconds':5,'queue_id':'a'*32}
    assert api.post('/api/fog-control/ack',json=ack,headers={'X-Fog-Control-Key':'test-only-secret'}).status_code == 200
    assert api.get('/api/demo/cloud').json()['ack']['blocked']
    ack['blocked']=False
    api.post('/api/fog-control/ack',json=ack,headers={'X-Fog-Control-Key':'test-only-secret'})
    assert not api.get('/api/demo/cloud').json()['offline']


def test_actual_fog_cache_miss_queue_countdown_restore_and_replay(tmp_path,monkeypatch):
    from conftest import load_fog_main
    mod = load_fog_main()
    now=[0]; fault=fog_demo.DemoFault(lambda:now[0]); fault.apply(command())
    monkeypatch.setattr(fog_demo,'fault',fault)
    monkeypatch.setattr(mod,'DB_PATH',str(tmp_path/'fog.db'))
    monkeypatch.setattr(mod,'_queue',None)
    monkeypatch.setattr(mod,'_cloud_retry_after',0)
    monkeypatch.setattr(mod,'CLOUD_URL','http://mock-cloud/api/analyze')
    network = Mock(return_value=SimpleNamespace(ok=True,status_code=200,headers={},json=lambda:{'status':'success','health_score':80}))
    monkeypatch.setattr(mod.requests,'post',network)
    monkeypatch.setattr(mod,'emit_fog',Mock())
    mod.init_db(); api=TestClient(mod.app)
    # A real cache hit still works during a simulated outage; it never starts a timer.
    with sqlite3.connect(mod.DB_PATH) as db:
        db.execute('INSERT INTO cache VALUES (?,?,?,?)',('cached',json.dumps({'status':'success','health_score':80}),0,3600))
    assert api.post('/query',json={'barcode':'cached'}).json()['cached']
    assert fault.snapshot()['remaining_seconds'] is None
    queued=api.post('/query',json={'barcode':'new','label_images':['photo']})
    assert queued.status_code==202
    assert fault.snapshot()['remaining_seconds']==5
    assert not mod.sync_once()
    network.assert_not_called()
    now[0]=3; fault.apply(command(expires_in=117))
    assert not mod.sync_once()
    now[0]=5; fault.apply(command(expires_in=115))
    assert mod.sync_once()
    result=api.get(queued.json()['status_url']).json()
    assert result['state']=='completed' and result['result']['health_score']==80
    network.assert_called_once()
    assert api.post('/query',json={'barcode':'new'}).json()['cached']
