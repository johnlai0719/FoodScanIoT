from fastapi import APIRouter, Request
from pydantic import BaseModel

from rehearsal_api import authorize
from cloud_availability import availability

router = APIRouter()


class Command(BaseModel):
    unavailable: bool


@router.get('/api/demo/cloud-availability')
def state():
    return availability.view()


@router.post('/api/demo/cloud-availability')
def command(body: Command, request: Request):
    authorize(request)
    return availability.set(body.unavailable)
