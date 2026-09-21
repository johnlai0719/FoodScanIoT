"""Explicit manual crops on c01, compared with full-image Gemma transcription."""
import base64
import json
import time
from pathlib import Path
from urllib.request import Request, urlopen

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
SOURCE = Path('D:/FoodScanIot/APP-sync-server/測試/量化測試/images/beverage/c01_柳橙綠茶/nutrition_en.5.400.jpg')
OUT = ROOT / '.artifacts/gemma-crop-c01'
REGIONS = {
    'name': ((260, 415, 665, 470), 'Read the product name in the image. Answer only the name in Chinese.'),
    'ingredients': ((255, 480, 1000, 583), '逐字抄錄圖片中的原料或成分，保留標點和括號。不要補字、解釋、改寫或推測。只輸出讀到的文字。'),
    'nutrition': ((290, 1150, 655, 1445), '逐行抄錄圖片中的營養標示，包括份量、欄位標頭、每份與每100毫升數值及單位。保留小數點，不換算，不補值，讀不清請寫[不清楚]。只輸出讀到的表格文字。'),
}


def infer(path, prompt):
    encoded = base64.b64encode(path.read_bytes()).decode()
    body = {
        'model': 'foodscan-gemma4-e4b',
        'messages': [{'role': 'user', 'content': [
            {'type': 'image_url', 'image_url': {'url': 'data:image/jpeg;base64,' + encoded}},
            {'type': 'text', 'text': prompt},
        ]}],
        'chat_template_kwargs': {'enable_thinking': False},
        'temperature': 0, 'max_tokens': 700, 'stream': False,
    }
    start = time.perf_counter()
    req = Request('http://127.0.0.1:8766/v1/chat/completions',
                  data=json.dumps(body, ensure_ascii=False).encode(),
                  headers={'Content-Type': 'application/json'})
    with urlopen(req, timeout=180) as response:
        result = json.load(response)
    return {'text': result['choices'][0]['message'].get('content'),
            'finish_reason': result['choices'][0]['finish_reason'],
            'elapsed_s': round(time.perf_counter() - start, 3)}


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    image = Image.open(SOURCE)
    results = {'source': str(SOURCE), 'source_size': list(image.size),
               'crop_method': 'manual rectangles chosen after visual inspection; no automatic localization',
               'regions': {}}
    for name, (rectangle, prompt) in REGIONS.items():
        path = OUT / f'{name}.jpg'
        image.crop(rectangle).save(path, quality=95)
        pair = {'rectangle': rectangle, 'prompt': prompt, 'crop_path': str(path)}
        for mode, target in [('full', SOURCE), ('crop', path)]:
            try:
                pair[mode] = infer(target, prompt)
            except Exception as exc:
                pair[mode] = {'error': str(exc)}
            print(json.dumps({'region': name, 'mode': mode, **pair[mode]}, ensure_ascii=False), flush=True)
        results['regions'][name] = pair
        (OUT / 'results.json').write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
