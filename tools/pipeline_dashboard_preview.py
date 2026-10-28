"""Isolated UI preview, synthetic events only. Does not contact Fog/Cloud or touch live jobs."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'tools'))
import pipeline_dashboard as dashboard
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
import os
import time

app = FastAPI(title="Synthetic Fog dashboard preview")

def scenario(name):
    global preview_store
    store = dashboard.Store()
    rid = 'preview-'+name
    def fog(event, status='active', payload=None, **kwargs):
        store.accept(dashboard.Event(request_id=rid, stage='fog', status=status, sender='fog',
          fog=dashboard.FogObservation(event=event, layer='python', **kwargs), payload=payload or {}))
    intent = {'hit':'cache','miss':'miss','queue':'queue','replay':'queue','bypass':'cloud'}[name]
    fog('received', payload={'demo_scenario':intent,'image_count':0 if name=='hit' else 1})
    if name=='hit':
        fog('cache_hit', cache_status='HIT')
        fog('cache_returned','done',cache_status='HIT',payload={'cached':True,'result':{'product_name':'合成快取案例（非真實商品）','health_score':0}})
    else:
        store.accept(dashboard.Event(request_id=rid,stage='image',status='done',sender='app'))
        fog('cache_bypass' if name=='bypass' else 'cache_miss',cache_status='BYPASS' if name=='bypass' else 'MISS')
        fog('cloud_forwarded')
        if name in ('queue','replay'):
            fog('cloud_unavailable')
            fog('queued',cache_status='QUEUED',queue_id='a'*32)
            if name=='replay': fog('replay_started',queue_id='a'*32,attempt=1)
        if name in ('miss','bypass','replay'):
            for stage in ('vision','routing','gemini'):
                store.accept(dashboard.Event(request_id=rid,stage=stage,status='done' if name=='replay' or stage!='gemini' else 'active',sender='cloud'))
            if name=='replay': fog('replay_completed','done',queue_id='a'*32,attempt=1,payload={'result':{'product_name':'合成補送案例（非真實商品）','health_score':0}})
    for job in store.jobs.values(): job['mode']='simulation'
    preview_store=store

@app.get('/preview/{name}')
def preview(name: str):
    if name not in ('hit','miss','queue','replay','bypass'):
        raise dashboard.HTTPException(status_code=404,detail='Unknown preview')
    scenario(name)
    return FileResponse(dashboard.ROOT/'web/pipeline/index.html')

@app.get('/api/state')
def state():
    return {'job': preview_store.latest(), 'server_time': time.time()}

@app.get('/api/grounding/config')
def grounding_config():
    return {'model': '合成預覽停用 API', 'configured': False}

@app.post('/api/grounding/search')
def no_paid_api():
    raise HTTPException(status_code=403, detail='合成預覽不呼叫付費 API。')

app.mount('/', StaticFiles(directory=dashboard.ROOT/'web/pipeline', html=True), name='static')
scenario('hit')
if __name__=='__main__':
    import uvicorn
    uvicorn.run(app,host='127.0.0.1',port=int(os.getenv('PIPELINE_PREVIEW_PORT','8788')))
