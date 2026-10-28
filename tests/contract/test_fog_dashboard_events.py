import sys
from pathlib import Path
import pytest
sys.path.insert(0, str(Path(__file__).parents[2]/'tools'))
from pipeline_dashboard import Store, Event, FogObservation


def observe(event, status='active', rid='scan', payload=None, **detail):
    return Event(request_id=rid, stage='fog', sender='fog', status=status,
                 fog=FogObservation(event=event, layer='python', **detail), payload=payload or {})


def test_demo_intent_does_not_become_observed_hit():
    store = Store()
    store.accept(Event(request_id='scan', stage='image', sender='app', status='done', payload={'demo_scenario': 'cache'}))
    store.accept(observe('cache_miss', cache_status='MISS'))
    job = store.latest()
    assert job['payload']['demo_scenario'] == 'cache'
    assert job['payload']['fog_latest']['cache_status'] == 'MISS'
    assert job['stages']['vision']['status'] == 'pending'


def test_hit_skips_inference_but_waits_for_actual_phone_ack():
    store = Store()
    store.accept(observe('received', payload={'image_count': 0}))
    store.accept(observe('cache_hit', cache_status='HIT'))
    store.accept(observe('cache_returned', 'done', cache_status='HIT', payload={'cached': True, 'result': {'health_score': 0}}))
    job = store.latest()
    assert all(job['stages'][stage]['status'] == 'skipped' for stage in ('image','vision','routing','gemini'))
    assert job['stages']['sync']['status'] == 'active'
    assert not job['phone_received']
    store.accept(Event(request_id='scan', stage='sync', sender='app', status='done'))
    assert store.latest()['phone_received']


def test_only_fog_can_report_observations_and_non_fog_cannot_inject_history():
    store = Store()
    with pytest.raises(ValueError):
        store.accept(Event(request_id='scan', stage='fog', sender='app', status='done'))
    store.accept(Event(request_id='scan', stage='image', sender='app', status='done',
                       payload={'fog_latest': {'event': 'cache_hit'}, 'fog_history': ['fake']}))
    assert 'fog_latest' not in store.latest()['payload']
    assert 'fog_history' not in store.latest()['payload']


def test_queue_replay_keeps_timeline_and_clears_a_failed_previous_attempt():
    store = Store()
    store.accept(observe('queued', queue_id='a'*32, cache_status='QUEUED'))
    store.accept(Event(request_id='scan', stage='gemini', sender='cloud', status='failed'))
    store.accept(observe('replay_started', attempt=2, queue_id='a'*32))
    store.accept(observe('queued', queue_id='a'*32))
    assert store.latest()['payload']['fog_latest']['event'] == 'replay_started'
    assert not store.latest()['failed']
    assert store.latest()['stages']['gemini']['status'] == 'pending'
    store.accept(observe('replay_completed', 'done', attempt=2, queue_id='a'*32, payload={'result': {'health_score': 0}}))
    store.accept(observe('queued', queue_id='a'*32))  # late acknowledgement must not regress completion
    job = store.latest()
    assert job['payload']['fog_latest']['event'] == 'replay_completed'
    assert job['stages']['fog']['status'] == 'done'
    assert not job['phone_received']


def test_deduplicated_submission_receives_original_queue_replay_events():
    store = Store()
    store.accept(observe('queued', rid='original'))
    store.accept(observe('received', rid='new-submit'))
    store.accept(observe('queued', rid='new-submit', payload={'canonical_request_id': 'original'}))
    store.accept(observe('replay_completed', 'done', rid='original'))
    assert store.latest()['request_id'] == 'new-submit'
    assert store.latest()['payload']['fog_latest']['event'] == 'replay_completed'


def test_observer_history_is_bounded_and_requests_stay_separate():
    store = Store()
    for i in range(60): store.accept(observe('received'))
    assert len(store.latest()['payload']['fog_history']) == 40
    store.accept(observe('cache_bypass', rid='another', cache_status='BYPASS'))
    assert len(store.latest()['payload']['fog_history']) == 1
