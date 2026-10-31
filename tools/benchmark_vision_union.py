"""One Vision text envelope per photo -> Gemini, using frozen cached OCR.

Fresh Gemini calls only. Never calls Cloud Vision, Fog, DB, or dashboard.
"""
import argparse
import base64
import hashlib
import io
import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image, ImageOps
from benchmark_vision_pipeline import configure_credentials, save, digest
from easyocr_union_gemini import text_envelope
from vision_auto_regions import extract_words
from gemini_compare import PROMPT, FoodLabel, request, score

ROOT = Path(__file__).resolve().parents[1]


def envelope(response, size):
    boxes = []
    for word in extract_words(response):
        x1,y1,x2,y2 = word['rect']
        boxes.append([[x1,y1],[x2,y1],[x2,y2],[x1,y2]])
    rect, fallback = text_envelope(boxes, size)
    return rect, fallback, len(boxes)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-run', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--workers', type=int, choices=(1,2), default=2)
    parser.add_argument('--audit-only', action='store_true')
    args = parser.parse_args()
    source = json.loads((args.source_run/'manifest.json').read_text(encoding='utf-8'))
    assert source['A_prompt'] == PROMPT
    assert source['schema_sha256'] == digest(json.dumps(FoodLabel.model_json_schema(),sort_keys=True).encode())
    rows = source['inventory']
    vision_hashes = {}
    for row in rows:
        assert digest(Path(row['gt']).read_bytes()) == row['gt_sha256']
        for index, image in enumerate(row['images']):
            assert digest(Path(image['path']).read_bytes()) == image['sha256']
            path = args.source_run/'vision'/row['case_id']/(str(index)+'.json')
            vision_hashes[str(path.resolve())] = digest(path.read_bytes())
    manifest = {'design':'C: all Vision word boxes -> one envelope per photo, padding 4%/minimum16px, >=90% area fallback; crops ONLY, max edge2048; no extra original.',
                'model':source['model'], 'prompt':PROMPT,'inventory':rows,
                'source_run':str(args.source_run.resolve()),
                'source_manifest_sha256':digest((args.source_run/'manifest.json').read_bytes()),
                'vision_hashes':vision_hashes,'workers':args.workers,'retries':0,
                'source_sha256':{name:digest((ROOT/'tools'/name).read_bytes()) for name in
                    ('benchmark_vision_union.py','benchmark_vision_pipeline.py','easyocr_union_gemini.py',
                     'vision_auto_regions.py','gemini_compare.py','structured_pipeline.py')},
                'timing_scope':'Measured cached-OCR reader excludes Vision. Fresh-path estimate adds the same case prior B Vision stage, not a live end-to-end measurement.',
                'scope':'Historical development dataset, one new Gemini call per case, no GT supplied to inference.'}
    dest = args.output/'manifest.json'
    if dest.exists():
        if json.loads(dest.read_text(encoding='utf-8')) != manifest:
            raise RuntimeError('Frozen manifest mismatch')
    else:
        save(dest,manifest)
    print(json.dumps({'cases':len(rows),'cached_vision_responses':len(vision_hashes),'audit_only':args.audit_only}),flush=True)
    if args.audit_only:
        return
    configure_credentials()
    from google import genai
    from google.genai import types
    stop = threading.Event()

    def progress():
        records=[json.loads(p.read_text(encoding='utf-8')) for p in (args.output/'cases').glob('*.json')]
        counts={status:sum(r.get('status')==status for r in records) for status in ('completed','failed','running')}
        save(args.output/'progress.json',{'records':len(records),**counts})
        return counts

    def run_case(row):
        if stop.is_set():
            return
        cid=row['case_id'];dest=args.output/'cases'/(cid+'.json')
        if dest.exists():
            # An interrupted running arm is not silently billed again either.
            return
        encoded=[base64.b64encode(Path(im['path']).read_bytes()).decode() for im in row['images']]
        ocr=[json.loads((args.source_run/'vision'/cid/(str(i)+'.json')).read_text(encoding='utf-8'))
             for i in range(len(encoded))]
        record={'case_id':cid,'status':'running','started_utc':datetime.now(timezone.utc).isoformat()}
        save(dest,record)
        started=time.perf_counter()
        try:
            images=[]
            for encoded_image in encoded:
                with Image.open(io.BytesIO(base64.b64decode(encoded_image))) as original:
                    images.append(ImageOps.exif_transpose(original).convert('RGB'))
            crop_started=time.perf_counter();inputs=[];regions=[]
            for image,response in zip(images,ocr):
                rect,fallback,words=envelope(response,image.size)
                crop=image.crop(rect);crop.thumbnail((2048,2048));inputs.append(crop)
                regions.append({'rect':list(rect),'fallback':fallback,'word_boxes':words,
                                'retained_area':(rect[2]-rect[0])*(rect[3]-rect[1])/(image.width*image.height),
                                'input_size':list(crop.size)})
            crop_seconds=time.perf_counter()-crop_started
            client=genai.Client(api_key=os.environ['GEMINI_API_KEY'],http_options=types.HttpOptions(
                timeout=90000,retry_options=types.HttpRetryOptions(attempts=1)))
            try:
                parsed,meta=request(client,manifest['model'],PROMPT,inputs,FoodLabel)
            finally:
                client.close()
            elapsed=time.perf_counter()-started
            # GT is first loaded after the Gemini call is over.
            gt=json.loads(Path(row['gt']).read_text(encoding='utf-8'))
            prior=json.loads((args.source_run/'cases'/(cid+'.json')).read_text(encoding='utf-8'))
            vision_seconds=prior.get('B',{}).get('stage_secs',{}).get('vision')
            record.update(status='completed',prediction=parsed,call=meta,score=score(parsed,gt),
                          cached_reader_wall_s=round(elapsed,3),crop_s=round(crop_seconds,3),
                          prior_vision_s=vision_seconds,
                          estimated_fresh_wall_s=round(elapsed+vision_seconds,3) if vision_seconds is not None else None,
                          input_count=len(inputs),regions=regions)
        except Exception as exc:
            code=getattr(exc,'code',None)
            record.update(status='failed',elapsed_s=round(time.perf_counter()-started,3),
                          error={'type':type(exc).__name__,'code':code})
            if code in (400,401,403,404,429):
                stop.set()
        save(dest,record)
        print(json.dumps({'case_id':cid,'status':record['status'],
                          'seconds':record.get('cached_reader_wall_s',record.get('elapsed_s'))}),flush=True)

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures=[pool.submit(run_case,row) for row in rows]
        for future in as_completed(futures):
            future.result();print(json.dumps(progress()),flush=True)
    print(json.dumps(progress()),flush=True)


if __name__=='__main__':
    main()
