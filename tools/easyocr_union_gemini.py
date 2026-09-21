"""One padded text envelope per photo, no text recognition or semantic subdivision."""
import argparse
import json
from pathlib import Path
from PIL import Image
from gemini_compare import PROMPT, FoodLabel, request, save, score


def text_envelope(boxes, size):
    width, height = size
    points = [point for box in boxes for point in box]
    if not points:
        return (0, 0, width, height), 'no_boxes'
    px, py = max(16, width*.04), max(16, height*.04)
    rect = (max(0, int(min(p[0] for p in points)-px)),
            max(0, int(min(p[1] for p in points)-py)),
            min(width, int(max(p[0] for p in points)+px)),
            min(height, int(max(p[1] for p in points)+py)))
    area = (rect[2]-rect[0])*(rect[3]-rect[1])
    if area <= 0 or min(rect[2]-rect[0], rect[3]-rect[1]) < 16:
        return (0, 0, width, height), 'invalid_envelope'
    if area/(width*height) >= .9:
        return (0, 0, width, height), 'little_background_removed'
    return rect, None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--boxes', type=Path, required=True, help='Existing EasyOCR results; text/confidence ignored')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--limit', type=int, default=3)
    parser.add_argument('--infer', action='store_true')
    args = parser.parse_args()
    client = None
    if args.infer:
        from dotenv import dotenv_values
        from google import genai
        from google.genai import types
        key = dotenv_values(Path(__file__).resolve().parents[1]/'.env').get('GEMINI_API_KEY')
        if not key: raise RuntimeError('API key missing')
        client = genai.Client(api_key=key, http_options=types.HttpOptions(timeout=120000, retry_options=types.HttpRetryOptions(attempts=1)))
    paths = sorted(args.boxes.glob('c*.json'))
    paths = paths[:args.limit] if args.limit else paths
    save(args.output/'manifest.json', {'model':'gemini-2.5-flash', 'prompt':PROMPT,
        'design':'One union of all text boxes per photo; 4%/minimum16px padding; >=90% area falls back to original',
        'box_source':str(args.boxes.resolve()), 'requested':len(paths),
        'detection_rerun':all(json.loads(p.read_text(encoding='utf-8')).get('recognition') is False for p in paths)})
    for path in paths:
        cached = json.loads(path.read_text(encoding='utf-8'))
        cid = cached['case_id']
        result_path = args.output/'cases'/(cid+'.json')
        if result_path.exists():
            previous = json.loads(result_path.read_text(encoding='utf-8'))
            if 'C' in previous and 'score' in previous:
                continue
        if cached.get('missing') or not cached.get('images') or cached.get('status') == 'failed':
            save(result_path, {'case_id':cid, 'status':'incomplete_detection'})
            continue
        record = {'case_id': cid, 'design': 'union of ALL text boxes per photo, 4%/minimum16px margin, no recognition text used',
                  'box_source': str(path.resolve()), 'images': []}
        crops = []
        for i, item in enumerate(cached['images']):
            source = Path(item['source'])
            if not source.exists():
                from easyocr_baseline import resolve_image
                source = resolve_image(Path('D:/FoodScanIot/APP-sync-server/測試/量化測試/images'), item['path'])
            image = Image.open(source).convert('RGB')
            boxes = [line['box'] for line in item['lines']]
            rect, fallback = text_envelope(boxes, image.size)
            crop = image.crop(rect)
            dest = args.output/'crops'/cid/(str(i)+'.jpg')
            dest.parent.mkdir(parents=True, exist_ok=True)
            crop.save(dest, quality=95)
            crops.append(crop)
            record['images'].append({'source': str(source), 'crop': str(dest.resolve()), 'pixel_box': rect,
                                     'box_count': len(boxes), 'fallback': fallback,
                                     'retained_area': crop.width*crop.height/(image.width*image.height)})
        if client:
            try:
                record['C'], record['call'] = request(client, 'gemini-2.5-flash', PROMPT, crops, FoodLabel)
            except Exception as exc:
                code = getattr(exc,'code',None)
                record.update(status='failed',error={'type':type(exc).__name__,'code':code})
                save(result_path, record)
                print(json.dumps({'case_id':cid, 'status':'failed', 'code':code}), flush=True)
                if code in (400,401,403,404,429): break
                continue
            save(args.output/'cases'/(cid+'.json'), record)
            gt_root = Path('D:/FoodScanIot/APP-sync-server/測試/量化測試/ground_truth')
            gt = next(gt_root.glob('*/'+cid+'.json'), gt_root/'missing.json')
            if gt.exists(): record['score'] = score(record['C'], json.loads(gt.read_text(encoding='utf-8')))
        record['status'] = 'completed' if 'C' in record else 'crops_only'
        save(args.output/'cases'/(cid+'.json'), record)
        print(json.dumps({'case_id': cid, 'images':record['images'], 'score':record.get('score')}, ensure_ascii=True), flush=True)


if __name__ == '__main__': main()
