"""Explicit six-request exploratory probe of the local event-search endpoint."""
import argparse
import hashlib
import json
import time
from pathlib import Path

import requests

CASES = [
    ('01_義美小泡芙', '義美食品股份有限公司', '義美', '小泡芙'),
    ('02_味全林鳳營', '味全食品工業股份有限公司', '林鳳營', '林鳳營鮮乳'),
    ('03_統一肉燥麵', '統一企業股份有限公司', '統一', '肉燥麵'),
    ('04_統一超商茶葉蛋', '統一超商股份有限公司', '7-ELEVEN', '茶葉蛋'),
    ('05_台糖梅花肉', '台灣糖業股份有限公司', '台糖', '安心豚梅花肉片'),
    ('06_義美重複查詢', '義美食品股份有限公司', '義美', '小泡芙'),
]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    manifest = []
    for case_id, manufacturer, brand, product in CASES:
        body = {'mode': 'vendor_events', 'manufacturer': manufacturer, 'brand': brand,
                'product': product, 'query': ''}
        started = time.perf_counter()
        try:
            response = requests.post('http://127.0.0.1:8787/api/grounding/search', json=body, timeout=75)
            result = response.json()
            status = response.status_code
        except (requests.RequestException, ValueError) as exc:
            result, status = {'transport_error_type': type(exc).__name__}, None
        record = {'case_id': case_id, 'input': body, 'http_status': status,
                  'wall_seconds': round(time.perf_counter()-started, 3), 'result': result}
        path = output/(case_id+'.json')
        path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
        summary = {'case_id': case_id, 'http_status': status, 'wall_seconds': record['wall_seconds'],
                   'answer_chars': len(result.get('answer', '')), 'sources': len(result.get('sources', [])),
                   'supports': len(result.get('supports', [])), 'has_grounding': result.get('has_grounding', False),
                   'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
        manifest.append(summary)
        (output/'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(summary, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
