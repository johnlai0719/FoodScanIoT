"""Opt-in, expiring simulation of Fog->Cloud failure; never touches cache or LAN."""
import os
import threading
import time

import requests


class DemoFault:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.lock = threading.RLock()
        self.generation = ''
        self.deadline = self.lease = 0
        self.release_at = None
        self.queue_id = None
        self.auto = None
        self.enabled = False

    def apply(self, command):
        with self.lock:
            now = self.clock()
            if command['generation'] != self.generation:
                self.generation = command['generation']
                self.deadline = now+min(120, max(0, command['expires_in']))
                self.enabled = bool(command['offline'])
                self.auto = command.get('auto_restore_seconds')
                self.queue_id = self.release_at = None
            if not command['offline']:
                self.enabled = False
            self.deadline = min(self.deadline, now+max(0, command['expires_in']))
            self.lease = now+min(4, max(0, command['lease_seconds']))

    def blocked(self):
        with self.lock:
            now = self.clock()
            if now >= min(self.deadline, self.lease) or (self.release_at is not None and now >= self.release_at):
                self.enabled = False
            return self.enabled

    def queued(self, job_id):
        with self.lock:
            if self.blocked() and self.queue_id is None:
                self.queue_id = job_id
                if self.auto is not None:
                    self.release_at = self.clock()+self.auto

    def snapshot(self):
        with self.lock:
            blocked = self.blocked()
            return {'generation': self.generation, 'blocked': blocked, 'queue_id': self.queue_id,
                    'remaining_seconds': max(0, min(self.release_at, self.deadline)-self.clock()) if blocked and self.release_at is not None else None}


fault = DemoFault()


def configured():
    return (os.getenv('FOG_DEMO_CONTROL_ENABLED') == '1'
            and bool(os.getenv('FOG_DEMO_CONTROL_TOKEN')) and bool(os.getenv('PIPELINE_DASHBOARD_URL')))


def worker(stop):
    while not stop.is_set():
        if configured():
            try:
                base = os.environ['PIPELINE_DASHBOARD_URL'].rstrip('/')
                node_id = os.getenv('FOG_DEMO_NODE_ID', 'foodscan-pi')
                headers = {'X-Fog-Control-Key': os.environ['FOG_DEMO_CONTROL_TOKEN']}
                r = requests.get(base+'/api/fog-control', params={'fog_id': node_id}, headers=headers, timeout=1)
                r.raise_for_status()
                fault.apply(r.json())
                requests.post(base+'/api/fog-control/ack', json={'fog_id': node_id, **fault.snapshot()},
                              headers=headers, timeout=1).raise_for_status()
            except (requests.RequestException, ValueError, KeyError, TypeError):
                fault.blocked()  # Missing heartbeat expires the fault, never locks Cloud offline.
        stop.wait(1)
