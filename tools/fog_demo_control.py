"""Bounded demo commands polled by one opt-in Fog; no Cloud/network settings changed."""
import hmac
import os
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field


def token():
    value = os.getenv('FOG_DEMO_CONTROL_TOKEN', '').strip()
    if not value:
        path = Path(__file__).resolve().parents[1]/'.artifacts/fog-demo-control.token'
        if path.is_file():
            value = path.read_text(encoding='utf-8').strip()
    return value


class Command(BaseModel):
    offline: bool
    auto_restore_seconds: int | None = Field(default=5, ge=3, le=30)


class Ack(BaseModel):
    fog_id: str = Field(max_length=64)
    generation: str = Field(max_length=40)
    blocked: bool
    remaining_seconds: float | None = Field(default=None, ge=0, le=120)
    queue_id: str | None = Field(default=None, pattern=r'^[a-f0-9]{32}$')


class Control:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.lock = threading.Lock()
        self.command = {'generation': uuid.uuid4().hex, 'offline': False, 'auto_restore_seconds': None}
        self.deadline = 0
        self.ack = None
        self.ack_at = None

    def set(self, offline, auto):
        with self.lock:
            self.command = {'generation': uuid.uuid4().hex, 'offline': offline,
                            'auto_restore_seconds': auto if offline else None}
            self.deadline = self.clock()+120 if offline else 0
            self.ack = None
            self.ack_at = None

    def view(self):
        with self.lock:
            remaining = max(0, self.deadline-self.clock())
            return {**self.command, 'offline': self.command['offline'] and remaining > 0,
                    'expires_in': remaining, 'lease_seconds': 4,
                    'fog_id': os.getenv('FOG_DEMO_NODE_ID', 'foodscan-pi'),
                    'ack': self.ack, 'ack_age_seconds': None if self.ack_at is None else self.clock()-self.ack_at}

    def receive(self, ack):
        with self.lock:
            if ack.generation != self.command['generation']:
                return
            self.ack = ack.model_dump()
            self.ack_at = self.clock()
            # Once Fog releases an auto countdown, do not re-arm its next poll.
            if not ack.blocked:
                self.command['offline'] = False


control = Control()
router = APIRouter()


def authorize_fog(request):
    expected = token()
    if not expected or not hmac.compare_digest(request.headers.get('X-Fog-Control-Key', ''), expected):
        raise HTTPException(401, 'Invalid Fog control token')


@router.get('/api/demo/cloud')
def state():
    return {'configured': bool(token()), **control.view()}


@router.post('/api/demo/cloud')
def command(body: Command, request: Request):
    # Local browser and existing localhost Tailscale proxy; reject drive-by sites.
    if request.client and request.client.host not in ('127.0.0.1', '::1', 'testclient'):
        raise HTTPException(403, '請透過本機或既有 Tailscale 儀表板操作')
    origin = request.headers.get('origin')
    if request.headers.get('sec-fetch-site') == 'cross-site' or (origin and urlsplit(origin).netloc != request.headers.get('host')):
        raise HTTPException(403, 'Cross-origin control is not allowed')
    if not token():
        raise HTTPException(503, '尚未設定 Fog 展示控制金鑰')
    control.set(body.offline, body.auto_restore_seconds)
    return state()


@router.get('/api/fog-control')
def poll(request: Request, fog_id: str):
    authorize_fog(request)
    view = control.view()
    if fog_id != view['fog_id']:
        raise HTTPException(403, 'Unknown Fog node')
    return {k:v for k,v in view.items() if k not in ('ack', 'ack_age_seconds')}


@router.post('/api/fog-control/ack')
def acknowledge(body: Ack, request: Request):
    authorize_fog(request)
    if body.fog_id != os.getenv('FOG_DEMO_NODE_ID', 'foodscan-pi'):
        raise HTTPException(403, 'Unknown Fog node')
    control.receive(body)
    return {'ok': True}
