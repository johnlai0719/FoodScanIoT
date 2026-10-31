"""Vision -> one padded text envelope per photo -> Gemini, with live events.

Implements the existing /read contract. Does not switch or stop any running reader.
"""
import base64
import io
import os
import sys
import threading
import time
from pathlib import Path

import requests
from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException
from google.auth.transport.requests import Request
from google.oauth2 import service_account
from google import genai
from google.genai import types
from PIL import Image, ImageOps
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'server'))
from pipeline_events import emit
from vision_auto_regions import extract_words
from vision_text_envelope import route, ROUTING_POLICY
from vision_quality_gate import assess, POLICY as QUALITY_POLICY
from gemini_compare import PROMPT, request as gemini_request
from structured_pipeline import FoodLabel
from module_b.nutrition_basis import serving_evidence

load_dotenv(ROOT/'.env')
app = FastAPI(title='Competition Vision reader')
lock = threading.Lock()
NAME = 'google_vision_rules_gemini'


class ReadRequest(BaseModel):
    label_images: list[str] = Field(min_length=1, max_length=6)
    barcode: str | None = None


def preview(image, edge=1000):
    im = image.copy()
    im.thumbnail((edge, edge))
    stream = io.BytesIO()
    im.save(stream, format='JPEG', quality=84)
    return 'data:image/jpeg;base64,'+base64.b64encode(stream.getvalue()).decode()


def vision(image, credentials):
    stream = io.BytesIO()
    image.save(stream, format='JPEG', quality=95)
    response = requests.post('https://vision.googleapis.com/v1/images:annotate',
        headers={'Authorization': 'Bearer '+credentials.token},
        json={'requests': [{'image': {'content': base64.b64encode(stream.getvalue()).decode()},
                            'features': [{'type': 'DOCUMENT_TEXT_DETECTION'}]}]}, timeout=60)
    data = response.json()
    first = (data.get('responses') or [{}])[0]
    if not response.ok or data.get('error') or first.get('error'):
        raise RuntimeError('Cloud Vision request failed')
    return data


@app.get('/health')
def health():
    return {'reader': NAME, 'credentials_configured': bool(os.getenv('GOOGLE_APPLICATION_CREDENTIALS')),
            'gemini_configured': bool(os.getenv('GEMINI_API_KEY')),
            'routing_policy': ROUTING_POLICY, 'gemini_input': 'one_crop_per_photo',
            'ocr_quality_policy': QUALITY_POLICY}


@app.post('/read')
def read(body: ReadRequest, x_request_id: str = Header(default=''), x_api_key: str = Header(default='')):
    secret = os.getenv('API_SHARED_SECRET', '').strip()
    if secret and x_api_key != secret:
        raise HTTPException(status_code=401, detail='bad api key')
    rid = x_request_id
    if not os.getenv('GOOGLE_APPLICATION_CREDENTIALS') or not os.getenv('GEMINI_API_KEY'):
        raise HTTPException(status_code=503, detail='Vision / Gemini credentials not configured')
    current_stage = 'vision'
    with lock:
        try:
            images = []
            for encoded in body.label_images:
                with Image.open(io.BytesIO(base64.b64decode(encoded.split(',', 1)[-1]))) as original:
                    images.append(ImageOps.exif_transpose(original).convert('RGB'))
            emit(rid, 'image', 'done', {'source_image': preview(images[0]), 'image_size': list(images[0].size),
                                        'image_count': len(images), 'routing_policy': ROUTING_POLICY})
            emit(rid, 'vision', 'active')
            started = time.perf_counter()
            credentials = service_account.Credentials.from_service_account_file(
                os.environ['GOOGLE_APPLICATION_CREDENTIALS'], scopes=['https://www.googleapis.com/auth/cloud-platform'])
            credentials.refresh(Request())
            responses = [vision(im, credentials) for im in images]
            durations = {'vision': round(time.perf_counter()-started, 3)}
            ocr_boxes = extract_words(responses[0])
            quality = assess(responses)
            emit(rid, 'vision', 'done', {
                'ocr_boxes': ocr_boxes, 'word_boxes': len(ocr_boxes),
                'ocr_text': responses[0]['responses'][0].get('fullTextAnnotation', {}).get('text', ''),
                'ocr_quality': quality,
            }, duration_ms=durations['vision']*1000)
            if quality['decision'] == 'retake':
                emit(rid, 'routing', 'failed', {'message': quality['reason'], 'ocr_quality': quality,
                     'gemini_skipped': True, 'gemini_input_count': 0})
                rejected = FoodLabel(is_food_label=False, reject_reason=quality['reason']).model_dump()
                rejected['_meta'] = {'reader': NAME, 'stage_secs': durations, 'ocr_quality': quality,
                                     'routing_policy': ROUTING_POLICY, 'gemini_input_count': 0}
                return rejected
            current_stage = 'routing'
            emit(rid, 'routing', 'active')
            started = time.perf_counter()
            inputs, routing, thumbs = [], [], []
            for image_index, (im, response) in enumerate(zip(images, responses)):
                regions = route(response, im.size)
                routing.append(regions)
                # Exactly one input per source photo; fallback replaces, never adds.
                for region in regions['regions']:
                    crop = im.crop(region['rect'])
                    crop.thumbnail((2048, 2048))
                    inputs.append(crop)
                    if image_index == 0:
                        thumbs.append({'label': region['label'], 'image': preview(crop, 400)})
            durations['routing'] = round(time.perf_counter()-started, 3)
            emit(rid, 'routing', 'done', {'regions': routing[0]['regions'], 'crops': thumbs,
                 'image_size': list(images[0].size), 'routing_status': routing[0]['status'],
                 'routing_policy': ROUTING_POLICY, 'gemini_input_count': len(inputs)},
                 duration_ms=durations['routing']*1000)
            current_stage = 'gemini'
            emit(rid, 'gemini', 'active')
            started = time.perf_counter()
            client = genai.Client(api_key=os.environ['GEMINI_API_KEY'],
                http_options=types.HttpOptions(timeout=90000, retry_options=types.HttpRetryOptions(attempts=1)))
            try:
                parsed, meta = gemini_request(client, os.getenv('COMPETITION_GEMINI_MODEL', 'gemini-2.5-flash'),
                                             PROMPT, inputs, FoodLabel)
            finally:
                client.close()
            durations['gemini'] = round(time.perf_counter()-started, 3)
            evidence = serving_evidence([
                r['responses'][0].get('fullTextAnnotation', {}).get('text', '')
                for r in responses], parsed.get('serving_size'))
            if evidence:
                parsed['serving_evidence'] = evidence
            emit(rid, 'gemini', 'done', {'gemini_output': parsed}, duration_ms=durations['gemini']*1000)
            parsed['_meta'] = {'reader': NAME, 'stage_secs': durations, 'model_version': meta.get('model_version'),
                               'routing_status': [r['status'] for r in routing],
                               'routing_policy': ROUTING_POLICY, 'gemini_input_count': len(inputs),
                               'ocr_quality': quality}
            return parsed
        except Exception:
            emit(rid, current_stage, 'failed', {'message': '辨識流程未完成，請查看 App 提示並重試。'})
            raise HTTPException(status_code=502, detail='Competition reader failed; check local service configuration')


if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host=os.getenv('READER_HOST', '127.0.0.1'), port=int(os.getenv('READER_PORT', '8181')))
