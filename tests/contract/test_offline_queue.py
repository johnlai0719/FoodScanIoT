"""Durable offline delivery: FIFO, retries, restart recovery and result caching."""
import json
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from offline_queue import OfflineQueue


@pytest.fixture
def store(tmp_path):
    path = tmp_path / 'fog.db'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE cache (barcode TEXT PRIMARY KEY,result_json TEXT,last_updated INTEGER,ttl INTEGER)')
    return OfflineQueue(path)


def test_duplicate_uploads_are_atomic_and_survive_restart(store):
    payload = {'barcode': '471', 'label_images': ['image']}
    with ThreadPoolExecutor(max_workers=4) as pool:
        ids = list(pool.map(lambda _: store.enqueue(payload), range(8)))
    assert len(set(ids)) == 1
    assert OfflineQueue(store.db_path).claim()['payload'] == payload


def test_fifo_retry_blocks_later_upload_and_survives_expired_lease(store):
    first = store.enqueue({'barcode': '1'})
    second = store.enqueue({'barcode': '2'})
    job = store.claim()
    assert job['id'] == first
    assert store.claim() is None
    with store.connect() as db:
        db.execute('UPDATE analysis_queue SET lease_until=0 WHERE id=?', (first,))
    recovered = OfflineQueue(store.db_path).claim()
    assert recovered['id'] == first
    store.retry(recovered, 'offline')
    assert store.claim() is None
    with store.connect() as db:
        db.execute('UPDATE analysis_queue SET next_attempt=0 WHERE id=?', (first,))
    store.complete(store.claim(), {'status': 'success'})
    assert store.claim()['id'] == second


def test_completion_caches_atomically_and_releases_uploaded_images(store):
    job_id = store.enqueue({'barcode': '471', 'label_images': ['sensitive-image']}, 'app-original')
    result = {'status': 'success', 'health_score': 80}
    store.complete(store.claim(), result)
    status = store.get(job_id)
    assert status['state'] == 'completed'
    assert status['request_id'] == 'app-original'
    assert status['result'] == result
    assert 'payload_json' not in status
    with store.connect() as db:
        assert db.execute('SELECT payload_json FROM analysis_queue').fetchone()[0] is None
        assert json.loads(db.execute('SELECT result_json FROM cache').fetchone()[0]) == result


def test_test_barcode_results_stay_per_job_and_do_not_pollute_cache(store):
    store.enqueue({'barcode': 'TEST', 'label_images': ['first']})
    store.complete(store.claim(), {'status': 'success', 'health_score': 80})
    with store.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM cache').fetchone()[0] == 0


def test_permanent_failure_does_not_block_next_job(store):
    first = store.enqueue({'barcode': '1'})
    second = store.enqueue({'barcode': '2'})
    store.fail(store.claim(), 'HTTP 400')
    assert store.get(first)['state'] == 'failed'
    assert store.claim()['id'] == second
