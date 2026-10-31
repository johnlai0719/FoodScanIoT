"""Paired fresh API evaluation of full images and the current competition reader.

No Fog/cache/DB calls, no GT in inference, no dashboard events. API retries are
disabled. A run is resumable only with exactly the same code/data snapshot.
"""
import argparse
import base64
import contextlib
import hashlib
import io
import json
import os
import statistics
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image, ImageOps
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]


def digest(data):
    return hashlib.sha256(data).hexdigest()


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    tmp.replace(path)


def quantile(values, q):
    values = sorted(values)
    pos = (len(values)-1)*q
    lo = int(pos)
    return values[lo]+(values[min(lo+1, len(values)-1)]-values[lo])*(pos-lo)


def timing(values):
    return {'n': len(values), 'mean_s': statistics.mean(values),
            'p50_s': statistics.median(values), 'p95_s': quantile(values, .95)} if values else None


def summarize(output):
    records = [json.loads(p.read_text(encoding='utf-8')) for p in (output/'cases').glob('*.json')]
    paired = [r for r in records if all(r.get(m, {}).get('status') == 'completed' for m in ('A', 'B'))]
    result = {'recorded_cases': len(records), 'paired_completed': len(paired),
              'failed_or_partial': [r['case_id'] for r in records if r not in paired],
              'scope': 'One fresh call per arm; development dataset, exact ingredient multiset and nutrition numbers. Reader time excludes App/Fog/DB/observer and service queue wait.'}
    for mode in ('A', 'B'):
        scored = [r[mode]['score'] for r in paired]
        keys = ('ingredient_exact_hit', 'ingredient_predicted', 'ingredient_gt',
                'numeric_correct', 'numeric_wrong', 'numeric_missing', 'numeric_hardfill')
        totals = {key: sum(s[key] for s in scored) for key in keys}
        hit, pred, gt = (totals[k] for k in keys[:3])
        denom = sum(totals[k] for k in ('numeric_correct', 'numeric_wrong', 'numeric_missing'))
        totals.update(ingredient_precision=hit/pred if pred else None,
                      ingredient_recall=hit/gt if gt else None,
                      ingredient_micro_f1=2*hit/(pred+gt) if pred+gt else None,
                      nutrition_exact_accuracy=totals['numeric_correct']/denom if denom else None)
        result[mode] = {'scores': totals,
                        'reader_wall': timing([r[mode]['reader_wall_s'] for r in paired]),
                        'gemini': timing([r[mode]['call']['elapsed_s'] for r in paired]),
                        'total_tokens': sum(r[mode]['call']['usage'].get('total_token_count', 0) or 0 for r in paired)}
        if mode == 'B':
            for stage in ('vision', 'routing'):
                result[mode][stage] = timing([r[mode]['stage_secs'][stage] for r in paired])
    save(output/'summary.json', result)
    return result


def inventory(dataset):
    from easyocr_baseline import resolve_image
    rows = []
    for c in json.loads((dataset/'cases.json').read_text(encoding='utf-8'))['cases']:
        paths = [resolve_image(dataset/'images', p) for p in c['images']]
        gt = dataset/'ground_truth'/c['category']/(c['case_id']+'.json')
        rows.append({'case_id': c['case_id'],
                     'images': [{'path': str(p.resolve()), 'sha256': digest(p.read_bytes())} for p in paths],
                     'gt': str(gt.resolve()), 'gt_sha256': digest(gt.read_bytes())})
    return rows


def configure_credentials():
    # Match the existing competition service launcher; never serialize credentials.
    env = {k: v for k, v in dotenv_values('D:/FoodScanIot/APP-sync-server/server/.env').items() if v is not None}
    for p in (Path('D:/FoodScanIot/.env'), ROOT/'.env'):
        env.update({k: v for k, v in dotenv_values(p).items() if v is not None})
    raw = subprocess.check_output(['docker', 'inspect', 'app-sync-server-cloud-server-1'], text=True, encoding='utf-8')
    existing = dict(s.split('=', 1) for s in json.loads(raw)[0]['Config']['Env'] if '=' in s)
    for key in ('GEMINI_API_KEY', 'API_SHARED_SECRET'):
        if existing.get(key):
            env[key] = existing[key]
    assert env.get('GEMINI_API_KEY'), 'Missing Gemini credential'
    credentials = list(Path('D:/FoodScanIot/GoogleCloudVision').glob('*.json'))
    assert len(credentials) == 1, 'Expected one Vision credential file'
    for key in ('GEMINI_API_KEY', 'API_SHARED_SECRET'):
        if key in env:
            os.environ[key] = env[key]
    os.environ['GOOGLE_APPLICATION_CREDENTIALS'] = str(credentials[0])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset', type=Path, default=Path('D:/FoodScanIot/APP-sync-server/測試/量化測試'))
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--workers', type=int, choices=(1, 2, 3), default=2)
    parser.add_argument('--limit', type=int, default=0)
    parser.add_argument('--audit-only', action='store_true')
    parser.add_argument('--summary-only', action='store_true')
    args = parser.parse_args()
    if args.summary_only:
        print(json.dumps(summarize(args.output), ensure_ascii=True)); return
    from gemini_compare import PROMPT, FoodLabel
    rows = inventory(args.dataset)
    if args.limit:
        rows = rows[:args.limit]
    manifest = {'design': 'A full images resized to 2048; B exact current Vision+rules+originals+crops reader. B retains production crop-context prompt suffix; pipeline comparison, not crop-only causal ablation.',
                'model': 'gemini-2.5-flash', 'A_prompt': PROMPT,
                'B_prompt': PROMPT+'\n圖片包含原圖及其局部裁切，屬同一商品，不要重複列出成分。',
                'schema_sha256': digest(json.dumps(FoodLabel.model_json_schema(), sort_keys=True).encode()),
                'source_sha256': {name: digest((ROOT/'tools'/name).read_bytes()) for name in
                    ('benchmark_vision_pipeline.py', 'reader_vision_competition.py', 'vision_auto_regions.py', 'gemini_compare.py', 'structured_pipeline.py')},
                'cases_sha256': digest((args.dataset/'cases.json').read_bytes()), 'inventory': rows,
                'workers': args.workers, 'order': 'Case index parity alternates AB and BA',
                'retries': 0, 'scope': 'Historical development dataset; not independent acceptance data'}
    dest = args.output/'manifest.json'
    if dest.exists():
        if json.loads(dest.read_text(encoding='utf-8')) != manifest:
            raise RuntimeError('Frozen manifest mismatch; use a new output directory')
    else:
        save(dest, manifest)
    print(json.dumps({'audited_cases': len(rows), 'images': sum(len(r['images']) for r in rows), 'audit_only': args.audit_only}), flush=True)
    if args.audit_only:
        return
    configure_credentials()
    import reader_vision_competition as reader
    from gemini_compare import request, score
    from google import genai
    from google.genai import types
    reader.emit = lambda *a, **k: None
    # Isolated benchmark process only. Do not count artificial lock wait between workers.
    reader.lock = contextlib.nullcontext()
    local = threading.local()
    stop = threading.Event()
    original_vision = reader.vision
    original_gemini = reader.gemini_request

    def capture_error(exc):
        local.error = {'type': type(exc).__name__, 'code': getattr(exc, 'code', None)}
        if local.error['code'] in (400, 401, 403, 404, 429) or str(exc) == 'Cloud Vision request failed':
            stop.set()

    def observed_vision(image, credentials):
        try:
            data = original_vision(image, credentials)
            save(args.output/'vision'/local.cid/(str(local.image_index)+'.json'), data)
            local.image_index += 1
            return data
        except Exception as exc:
            capture_error(exc); raise

    def observed_gemini(*a, **k):
        try:
            value, meta = original_gemini(*a, **k)
            local.call = meta
            local.input_count = len(a[3])
            return value, meta
        except Exception as exc:
            capture_error(exc); raise

    reader.vision = observed_vision
    reader.gemini_request = observed_gemini

    def run_case(index, row):
        if stop.is_set():
            return
        cid = row['case_id']; path = args.output/'cases'/(cid+'.json')
        record = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {'case_id': cid, 'started_utc': datetime.now(timezone.utc).isoformat()}
        # Failed arms remain recorded; resume does not silently retry paid calls.
        if all(m in record for m in ('A', 'B')):
            return
        encoded = [base64.b64encode(Path(im['path']).read_bytes()).decode() for im in row['images']]
        for mode in (('A', 'B') if index % 2 == 0 else ('B', 'A')):
            if mode in record or stop.is_set():
                continue
            local.cid = cid; local.image_index = 0; local.error = None; local.call = None
            record[mode] = {'status': 'running'}; save(path, record)
            start = time.perf_counter()
            try:
                if mode == 'A':
                    images = []
                    for value in encoded:
                        with Image.open(io.BytesIO(base64.b64decode(value))) as im:
                            pic = ImageOps.exif_transpose(im).convert('RGB')
                        pic.thumbnail((2048, 2048)); images.append(pic)
                    client = genai.Client(api_key=os.environ['GEMINI_API_KEY'], http_options=types.HttpOptions(
                        timeout=90000, retry_options=types.HttpRetryOptions(attempts=1)))
                    try:
                        parsed, call = request(client, manifest['model'], PROMPT, images, FoodLabel)
                    finally:
                        client.close()
                    entry = {'prediction': parsed, 'call': call, 'input_count': len(images)}
                else:
                    parsed = reader.read(reader.ReadRequest(label_images=encoded, barcode='TEST'),
                        x_request_id='', x_api_key=os.getenv('API_SHARED_SECRET', ''))
                    meta = parsed.pop('_meta')
                    entry = {'prediction': parsed, 'call': local.call, 'input_count': local.input_count,
                             'stage_secs': meta['stage_secs'], 'routing_status': meta['routing_status']}
                elapsed = time.perf_counter()-start
                # GT is loaded only after inference has completed.
                gt = json.loads(Path(row['gt']).read_text(encoding='utf-8'))
                record[mode] = {**entry, 'status': 'completed', 'reader_wall_s': round(elapsed, 3), 'score': score(parsed, gt)}
            except Exception as exc:
                if local.error is None:
                    capture_error(exc)
                record[mode] = {'status': 'failed', 'elapsed_s': round(time.perf_counter()-start, 3),
                                'error': local.error, 'gemini_call_completed': local.call is not None}
            save(path, record)
            print(json.dumps({'case_id': cid, 'mode': mode, 'status': record[mode]['status'],
                              'seconds': record[mode].get('reader_wall_s', record[mode].get('elapsed_s'))}), flush=True)

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        pending = [pool.submit(run_case, i, row) for i, row in enumerate(rows)]
        for future in as_completed(pending):
            future.result()
            summary = summarize(args.output)
            print(json.dumps({'paired_completed': summary['paired_completed'], 'recorded_cases': summary['recorded_cases'], 'stopped_on_api_error': stop.is_set()}), flush=True)
    print(json.dumps(summarize(args.output), ensure_ascii=True), flush=True)


if __name__ == '__main__':
    main()
