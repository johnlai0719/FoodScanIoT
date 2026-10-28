"""Offline, two-class region proposals from Cloud Vision OCR paragraphs."""
import argparse
import json
import re
import statistics
from pathlib import Path

SEEDS = ('營養標示', '營養成分', '熱量', '蛋白質', '飽和脂肪',
         '反式脂肪', '碳水化合物', '每一份量', '本包裝含')
DATE = re.compile(r'^\d{4}[./-]\d{1,2}[./-]\d{1,2}')


def union(rects):
    return [min(r[0] for r in rects), min(r[1] for r in rects),
            max(r[2] for r in rects), max(r[3] for r in rects)]


def gap(a, b):
    return max(0, a[0]-b[2], b[0]-a[2], a[1]-b[3], b[1]-a[3])


def center_in(a, b):
    return b[0] <= (a[0]+a[2])/2 <= b[2] and b[1] <= (a[1]+a[3])/2 <= b[3]


def clusters(items, distance):
    remaining = set(range(len(items)))
    result = []
    while remaining:
        group = [min(remaining)]
        remaining.remove(group[0])
        cursor = 0
        while cursor < len(group):
            near = sorted(i for i in remaining
                          if gap(items[group[cursor]]['rect'], items[i]['rect']) <= distance)
            group.extend(near)
            remaining.difference_update(near)
            cursor += 1
        result.append([items[i] for i in group])
    return result


def extract_words(response):
    """Return actual OCR text boxes for the presentation inspector."""
    result = []
    for page in response.get('responses', [{}])[0].get('fullTextAnnotation', {}).get('pages', []):
        for block in page.get('blocks', []):
            for paragraph in block.get('paragraphs', []):
                for word in paragraph.get('words', []):
                    points = word.get('boundingBox', {}).get('vertices', [])
                    if not points:
                        continue
                    xs = [v.get('x', 0) for v in points]
                    ys = [v.get('y', 0) for v in points]
                    result.append({'text': ''.join(s.get('text', '') for s in word.get('symbols', [])),
                                   'rect': [min(xs), min(ys), max(xs), max(ys)]})
    return result


def extract_paragraphs(response):
    items, heights = [], []
    for page in response.get('responses', [{}])[0].get('fullTextAnnotation', {}).get('pages', []):
        for block in page.get('blocks', []):
            for paragraph in block.get('paragraphs', []):
                words = paragraph.get('words', [])
                text = ''.join(s.get('text', '') for w in words for s in w.get('symbols', []))
                vertices = paragraph.get('boundingBox', {}).get('vertices', [])
                if not vertices or not text:
                    continue
                xs, ys = [v.get('x', 0) for v in vertices], [v.get('y', 0) for v in vertices]
                rect = [min(xs), min(ys), max(xs), max(ys)]
                if rect[2] <= rect[0] or rect[3] <= rect[1]:
                    continue
                items.append({'id': len(items), 'text': text, 'rect': rect, 'words': len(words)})
                for word in words:
                    vs = word.get('boundingBox', {}).get('vertices', [])
                    if vs:
                        w = max(v.get('x', 0) for v in vs)-min(v.get('x', 0) for v in vs)
                        h = max(v.get('y', 0) for v in vs)-min(v.get('y', 0) for v in vs)
                        if min(w, h) > 0:
                            heights.append(min(w, h))
    return items, statistics.median(heights) if heights else 1


def detect(items, unit, size):
    """Pure rule engine. Parameters use OCR text size, never case-specific coordinates."""
    unit = max(1, unit)
    regions, covered = [], set()
    seeds = [p for p in items if any(k in p['text'] for k in SEEDS)]
    widths = sorted(p['rect'][2]-p['rect'][0] for p in items)
    long_threshold = .6*widths[int(.9*(len(widths)-1))] if widths else 0
    for group in clusters(seeds, 4*unit):
        signals = sorted({k for p in group for k in SEEDS if k in p['text']})
        if len(signals) < 3:
            continue
        rect = union([p['rect'] for p in group])
        members = {p['id'] for p in group}
        # Grow toward nearby numerical cells, not arbitrary neighboring prose.
        for _ in range(2):
            additions = []
            for p in items:
                text, box = p['text'], p['rect']
                if p['id'] in members or DATE.match(text):
                    continue
                short = box[2]-box[0] < long_threshold or len(text) <= 16
                cell = bool(re.search(r'\d|大卡|公克|毫克|毫升', text))
                if short and cell and gap(box, rect) <= 3*unit:
                    additions.append(p)
            if additions:
                members.update(p['id'] for p in additions)
                rect = union([rect]+[p['rect'] for p in additions])
        # Include nutrient labels within the numerical table envelope.
        members.update(p['id'] for p in items if center_in(p['rect'], rect))
        covered.update(members)
        regions.append({'class': 'nutrition', 'label': '營養標示', 'rect': rect,
                        'color': '#E53935', 'paragraph_ids': sorted(members),
                        'signals': signals, 'basis': 'At least three distinct nutrition seeds + nearby cells'})
    others = [p for p in items if p['id'] not in covered]
    for group in clusters(others, 2.5*unit):
        rect = union([p['rect'] for p in group])
        chars = sum(len(p['text']) for p in group)
        letters = sum(len(re.findall(r'[A-Za-z\u3400-\u9fff]', p['text'])) for p in group)
        words = sum(p['words'] for p in group)
        area = max(1, (rect[2]-rect[0])*(rect[3]-rect[1]))
        density = chars*unit*unit/area
        if chars < 12 or letters < 4 or words < 4 or density < .02:
            continue
        members = [p['id'] for p in group]
        covered.update(members)
        regions.append({'class': 'dense_text', 'label': '其他文字密集區塊', 'rect': rect,
                        'color': '#E66A00', 'paragraph_ids': members,
                        'characters': chars, 'language_characters': letters, 'words': words,
                        'text_density_proxy': round(density, 4),
                        'basis': 'Spatial proximity + text amount + OCR-size density proxy'})
    pad = max(8, round(.5*unit))
    for r in regions:
        a = r['rect']
        r['rect'] = [max(0, a[0]-pad), max(0, a[1]-pad),
                     min(size[0], a[2]+pad), min(size[1], a[3]+pad)]
    found = any(r['class'] == 'nutrition' for r in regions)
    return {'method': 'Automatic Vision OCR text and spatial rules v1; no manual region IDs',
            'scope': 'Offline prototype; no model confidence; not integrated into service',
            'status': 'nutrition_candidate_found' if found else 'nutrition_not_found',
            'preserve_original': True, 'ocr_scale': unit,
            'unassigned_paragraph_ids': [p['id'] for p in items if p['id'] not in covered],
            'parameters': {'minimum_distinct_seeds': 3, 'seed_gap_units': 4,
                           'numeric_growth_gap_units': 3, 'growth_rounds': 2,
                           'dense_gap_units': 2.5, 'minimum_characters': 12,
                           'minimum_words': 4, 'minimum_language_characters': 4,
                           'minimum_density_proxy': .02},
            'regions': regions}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--result-dir', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    response = json.loads((args.result_dir/'vision_response.json').read_text(encoding='utf-8'))
    metrics = json.loads((args.result_dir/'metrics.json').read_text(encoding='utf-8'))
    items, unit = extract_paragraphs(response)
    result = detect(items, unit, metrics['image_size'])
    result['source_sha256'] = metrics['source_sha256']
    args.output.mkdir(parents=True, exist_ok=True)
    groups = args.output/'auto_groups.json'
    groups.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    (args.output/'paragraphs.json').write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding='utf-8')
    from render_vision_regions import render
    render(args.result_dir, groups, args.output)
    print(json.dumps({'status': result['status'], 'classes': [r['class'] for r in result['regions']],
                      'unassigned_paragraphs': len(result['unassigned_paragraph_ids'])}))


if __name__ == '__main__':
    main()
