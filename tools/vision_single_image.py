"""Single-image Cloud Vision diagnostic; credentials are never saved in outputs."""
import argparse
import ast
import base64
import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
from google.auth.transport.requests import Request
from google.oauth2 import service_account
from PIL import Image, ImageDraw, ImageOps


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', type=Path, required=True)
    parser.add_argument('--credentials', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    source = args.image.read_bytes()
    with args.credentials.open(encoding='utf-8') as stream:
        info = json.load(stream)
    if info.get('token_uri') != 'https://oauth2.googleapis.com/token':
        raise RuntimeError('Unexpected OAuth token endpoint')
    credentials = service_account.Credentials.from_service_account_info(
        info, scopes=['https://www.googleapis.com/auth/cloud-platform'])
    credentials.refresh(Request())
    started = time.perf_counter()
    response = requests.post('https://vision.googleapis.com/v1/images:annotate',
        headers={'Authorization': 'Bearer ' + credentials.token},
        json={'requests': [{'image': {'content': base64.b64encode(source).decode('ascii')},
                            'features': [{'type': 'DOCUMENT_TEXT_DETECTION'}]}]}, timeout=90)
    elapsed = time.perf_counter() - started
    data = response.json()
    item = (data.get('responses') or [{}])[0]
    if not response.ok or data.get('error') or item.get('error'):
        error = data.get('error') or item.get('error') or {}
        raise RuntimeError('Vision request failed: HTTP %s, code %s' %
                           (response.status_code, error.get('code')))
    (args.output / 'vision_response.json').write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    annotation = item.get('fullTextAnnotation', {})
    text = annotation.get('text', '')
    (args.output / 'ocr.txt').write_text(text, encoding='utf-8')
    boxes = []
    block_count = 0
    for page in annotation.get('pages', []):
        for block in page.get('blocks', []):
            block_count += 1
            for paragraph in block.get('paragraphs', []):
                for word in paragraph.get('words', []):
                    points = [[v.get('x', 0), v.get('y', 0)]
                              for v in word.get('boundingBox', {}).get('vertices', [])]
                    if points:
                        boxes.append(points)
    with Image.open(args.image) as original:
        image = ImageOps.exif_transpose(original).convert('RGB')
    # Execute only the existing pure crop function, without importing inference modules.
    crop_script = Path(__file__).with_name('easyocr_union_gemini.py')
    tree = ast.parse(crop_script.read_text(encoding='utf-8'))
    function = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
                    and n.name == 'text_envelope')
    namespace = {}
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(crop_script), 'exec'), namespace)
    rect, fallback = namespace['text_envelope'](boxes, image.size)
    image.crop(rect).save(args.output / 'crop.jpg', quality=95)
    preview = image.copy()
    preview.thumbnail((1500, 1800))
    sx, sy = preview.width / image.width, preview.height / image.height
    draw = ImageDraw.Draw(preview)
    for box in boxes:
        points = [(round(x*sx), round(y*sy)) for x, y in box]
        draw.line(points + points[:1], fill='#00e5ff', width=2)
    draw.rectangle((round(rect[0]*sx), round(rect[1]*sy),
                    round(rect[2]*sx)-1, round(rect[3]*sy)-1), outline='#ff3333', width=4)
    preview.save(args.output / 'boxes_preview.jpg', quality=92)
    metrics = {
        'at_utc': datetime.now(timezone.utc).isoformat(),
        'source': str(args.image.resolve()), 'source_sha256': hashlib.sha256(source).hexdigest(),
        'feature': 'DOCUMENT_TEXT_DETECTION', 'image_size': list(image.size),
        'source_bytes': len(source), 'http_status': response.status_code,
        'vision_request_seconds': round(elapsed, 3),
        'timing_scope': 'JSON/base64 preparation + HTTP request/response; excludes authentication and crop/render',
        'blocks': block_count, 'word_boxes': len(boxes), 'text_characters': len(text),
        'crop_rect': list(rect), 'crop_fallback': fallback,
        'crop_area_ratio': round((rect[2]-rect[0])*(rect[3]-rect[1])/(image.width*image.height), 4),
        'crop_function_sha256': hashlib.sha256(ast.get_source_segment(
            crop_script.read_text(encoding='utf-8'), function).encode('utf-8')).hexdigest(),
        'scope': 'One exploratory image; no Gemini call; no accuracy benchmark.'}
    (args.output / 'metrics.json').write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(metrics, ensure_ascii=False))


if __name__ == '__main__':
    main()
