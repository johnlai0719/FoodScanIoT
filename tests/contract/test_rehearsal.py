from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
import sys
from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parents[2]/'tools'))
import rehearsal_api as api
from rehearsal_store import Journal


class Cursor:
    def __init__(self, db): self.cursor = db.cursor()
    def __enter__(self): return self
    def __exit__(self, *args): self.cursor.close()
    def execute(self, sql, args=()): self.cursor.execute(sql.replace('%s', '?'), args)
    def fetchone(self): return self.cursor.fetchone()
    @property
    def rowcount(self): return self.cursor.rowcount


class Connection:
    def __init__(self, db): self.db = db
    def cursor(self): return Cursor(self.db)


@pytest.fixture
def lab(tmp_path, monkeypatch):
    uploads = tmp_path/'uploads'; uploads.mkdir()
    monkeypatch.setattr(api, 'UPLOADS', uploads)
    monkeypatch.setattr(api, 'ARCHIVES', tmp_path/'archives')
    journal = Journal(tmp_path/'journal.sqlite3')
    monkeypatch.setattr(api, 'journal', journal)
    monkeypatch.setattr(api, 'next_miss', api.NextMiss())
    monkeypatch.setenv('FOG_DEMO_CONTROL_TOKEN', 'test-only')
    dbpath = tmp_path/'products.sqlite3'
    with sqlite3.connect(dbpath) as db:
        db.execute('CREATE TABLE products(barcode TEXT PRIMARY KEY, name TEXT)')
    @contextmanager
    def database():
        db = sqlite3.connect(dbpath)
        try:
            with db: yield Connection(db)
        finally: db.close()
    monkeypatch.setattr(api, 'database', database)
    clear = Mock(); monkeypatch.setattr(api, 'fog_clear', clear)
    app = FastAPI(); app.include_router(api.router)
    return TestClient(app), journal, uploads, dbpath, clear


def insert(dbpath, barcode, name='demo'):
    with sqlite3.connect(dbpath) as db:
        db.execute('INSERT INTO products(barcode,name) VALUES (?,?)', (barcode, name))


def present(dbpath, barcode):
    with sqlite3.connect(dbpath) as db:
        return db.execute('SELECT name FROM products WHERE barcode=?', (barcode,)).fetchone()


def test_new_data_roundtrip_preserves_original_data_and_refuses_active_reset(lab):
    client, journal, uploads, dbpath, clear = lab
    insert(dbpath, '4710000000001', 'existing')
    old = uploads/'scan_1_4710000000001_0.jpg'; old.write_bytes(b'original')
    response = client.post('/api/rehearsal/prepare', json={'barcode': '4710000000002'})
    assert response.status_code == 200
    task = response.json()['task_id']
    insert(dbpath, '4710000000002')
    photo = uploads/'scan_2_4710000000002_0.jpg'; photo.write_bytes(b'new')
    active = journal.begin('4710000000002')
    assert client.post('/api/rehearsal/reset', json={'task_id': task}).status_code == 409
    journal.end(active)
    assert client.post('/api/rehearsal/reset', json={'task_id': task}).status_code == 200
    assert not present(dbpath, '4710000000002') and not photo.exists()
    assert old.read_bytes() == b'original' and present(dbpath, '4710000000001') == ('existing',)
    assert client.post('/api/rehearsal/restore', json={'task_id': task}).status_code == 200
    assert present(dbpath, '4710000000002') and photo.read_bytes() == b'new'
    assert clear.call_count == 2


def test_existing_product_and_images_never_archived(lab):
    client, journal, uploads, dbpath, clear = lab
    barcode = '4710000000001'; insert(dbpath, barcode)
    response = client.post('/api/rehearsal/prepare', json={'barcode': barcode})
    photo = uploads/f'scan_2_{barcode}_0.jpg'; photo.write_bytes(b'keep')
    task = response.json()['task_id']
    result = client.post('/api/rehearsal/reset', json={'task_id': task})
    assert result.json()['preserved_existing'] is True
    assert present(dbpath, barcode) and photo.read_bytes() == b'keep'


def test_prepare_failure_and_restore_conflict_are_non_destructive(lab):
    client, journal, uploads, dbpath, clear = lab
    clear.side_effect = ValueError('Fog queue pending')
    assert client.post('/api/rehearsal/prepare', json={'barcode': '4710000000001'}).status_code == 409
    assert journal.rows()[0]['state'] == 'failed'
    clear.side_effect = None
    task = client.post('/api/rehearsal/prepare', json={'barcode': '4710000000001'}).json()['task_id']
    insert(dbpath, '4710000000001')
    client.post('/api/rehearsal/reset', json={'task_id': task})
    insert(dbpath, '4710000000001', 'second-round')
    assert client.post('/api/rehearsal/restore', json={'task_id': task}).status_code == 409
    assert present(dbpath, '4710000000001') == ('second-round',)
    assert journal.get(task)['state'] == 'archived'


def test_move_failure_rolls_back_database_and_prior_files(lab, monkeypatch):
    client, journal, uploads, dbpath, clear = lab
    barcode = '4710000000001'
    task = client.post('/api/rehearsal/prepare', json={'barcode': barcode}).json()['task_id']
    insert(dbpath, barcode)
    for i in range(2): (uploads/f'scan_2_{barcode}_{i}.jpg').write_bytes(b'new')
    original_move = api.shutil.move
    count = [0]
    def move(*args):
        count[0] += 1
        if count[0] == 2: raise OSError('test failure')
        return original_move(*args)
    monkeypatch.setattr(api.shutil, 'move', move)
    assert client.post('/api/rehearsal/reset', json={'task_id': task}).status_code == 409
    assert present(dbpath, barcode) and len(list(uploads.iterdir())) == 2
    assert journal.get(task)['state'] == 'ready'


def test_next_miss_is_authenticated_one_shot_and_requires_real_ack(lab):
    client, journal, uploads, dbpath, clear = lab
    assert client.post('/api/rehearsal/next-miss', json={'enabled': True}).status_code == 200
    body = {'barcode': '4710000000001', 'request_id': 'test-rid'}
    assert client.post('/api/rehearsal/claim', json=body).status_code == 401
    headers = {'X-Fog-Control-Key': 'test-only'}
    claim = client.post('/api/rehearsal/claim', json=body, headers=headers).json()
    assert claim['clear_cache'] and not claim['applied']
    clear.assert_not_called()  # Node does synchronous local SQLite delete.
    assert client.get('/api/rehearsal').json()['next_miss']['result']['pending']
    assert client.post('/api/rehearsal/claim', json={**body, 'request_id': 'second'}, headers=headers).json() == {'applied': False}
    ack = {**body, 'task_id': claim['task_id'], 'applied': True}
    assert client.post('/api/rehearsal/applied', json=ack, headers=headers).status_code == 200
    assert client.get('/api/rehearsal').json()['next_miss']['result']['applied']
    assert 'clear_cache' not in client.post('/api/rehearsal/claim', json=body, headers=headers).json()


def test_invalid_barcodes_cross_origin_and_existing_batch_protection(lab):
    client, journal, uploads, dbpath, clear = lab
    assert client.post('/api/rehearsal/prepare', json={'barcode': '../x'}).status_code == 422
    assert client.post('/api/rehearsal/next-miss', json={'enabled': True}, headers={'Origin': 'http://evil.test'}).status_code == 403
    insert(dbpath, '4710000000001')
    task = client.post('/api/rehearsal/prepare', json={'barcode': '4710000000001'}).json()['task_id']
    result = client.post('/api/rehearsal/reset-new', json={'task_ids': [task]}).json()
    assert not result['ok'] and journal.get(task)['state'] == 'ready'
    assert present(dbpath, '4710000000001')


def test_prepare_multiple_barcodes_deduplicates_and_tracks_each_case(lab):
    client, journal, uploads, dbpath, clear = lab
    codes = ['4710000000001', '00123456', '4710000000001']
    result = client.post('/api/rehearsal/prepare-many', json={'barcodes': codes})
    assert result.status_code == 200 and result.json()['ok']
    assert [r['barcode'] for r in result.json()['results']] == codes[:2]
    assert clear.call_count == 2
    assert {r['barcode'] for r in journal.rows()} == set(codes)


def test_invalid_batch_is_rejected_before_any_cache_clear(lab):
    client, journal, uploads, dbpath, clear = lab
    for codes in [[], ['4710000000001', '../x'], ['4710000000001']*21]:
        assert client.post('/api/rehearsal/prepare-many', json={'barcodes': codes}).status_code == 422
    clear.assert_not_called()
    assert not journal.rows()


def test_batch_reports_partial_failure_without_claiming_all_completed(lab):
    client, journal, uploads, dbpath, clear = lab
    clear.side_effect = [None, ValueError('Fog unavailable'), None]
    codes = ['4710000000001', '4710000000002', '4710000000003']
    result = client.post('/api/rehearsal/prepare-many', json={'barcodes': codes}).json()
    assert not result['ok'] and [r['ok'] for r in result['results']] == [True, False, True]
    assert '2／3' in result['message']
    assert {r['barcode'] for r in journal.rows() if r['state'] == 'ready'} == {codes[0], codes[2]}


def test_reset_barcode_archives_existing_cloud_data_and_can_restore(lab):
    client, journal, uploads, dbpath, clear = lab
    barcode = '4710000000001'
    insert(dbpath, barcode, 'original')
    photo = uploads/f'scan_2_{barcode}_0.jpg'
    photo.write_bytes(b'original-photo')
    result = client.post('/api/rehearsal/reset-barcodes', json={'barcodes': [barcode]})
    assert result.status_code == 200 and result.json()['ok']
    assert not present(dbpath, barcode) and not photo.exists()
    task_id = result.json()['results'][0]['task_id']
    assert journal.get(task_id)['state'] == 'archived'
    active = journal.begin(barcode)
    assert active
    result = client.post('/api/rehearsal/reset-barcodes', json={'barcodes': [barcode]})
    assert not result.json()['ok']
    journal.end(active)
    # Retire the empty next round before restoring the original.
    ready = next(row for row in journal.rows() if row['state'] == 'ready')
    assert client.post('/api/rehearsal/reset', json={'task_id': ready['id']}).status_code == 200
    assert client.post('/api/rehearsal/restore', json={'task_id': task_id}).status_code == 200
    assert present(dbpath, barcode) == ('original',)
    assert photo.read_bytes() == b'original-photo'


def test_reset_barcode_fog_failure_preserves_cloud_and_rejects_cross_origin(lab):
    client, journal, uploads, dbpath, clear = lab
    barcode = '4710000000001'
    insert(dbpath, barcode)
    clear.side_effect = ValueError('Fog queue pending')
    result = client.post('/api/rehearsal/reset-barcodes', json={'barcodes': [barcode]})
    assert not result.json()['ok'] and present(dbpath, barcode)
    assert client.post('/api/rehearsal/reset-barcodes', json={'barcodes': [barcode]},
                       headers={'Origin': 'http://evil.test'}).status_code == 403
