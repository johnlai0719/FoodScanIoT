"""Text-dense region proposals from Cloud Vision OCR paragraphs; no semantic classes."""
import argparse
import json
import re
import statistics
from pathlib import Path



def union(rects):
    return [min(r[0] for r in rects), min(r[1] for r in rects),
            max(r[2] for r in rects), max(r[3] for r in rects)]


def gap(a, b):
    return max(0, a[0]-b[2], b[0]-a[2], a[1]-b[3], b[1]-a[3])


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
    # Cluster every paragraph together, including numerical cells and table headings.
    # Larger generic neighborhoods favor complete text panels over small feature boxes.
    for group in clusters(items, 4*unit):
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
        regions.append({'class': 'dense_text', 'label': '文字密集區塊 '+str(len(regions)+1), 'rect': rect,
                        'color': '#E66A00', 'paragraph_ids': members,
                        'characters': chars, 'language_characters': letters, 'words': words,
                        'text_density_proxy': round(density, 4),
                        'basis': 'Spatial proximity + text amount + OCR-size density proxy'})
    pad = max(8, round(.5*unit))
    for r in regions:
        a = r['rect']
        r['rect'] = [max(0, a[0]-pad), max(0, a[1]-pad),
                     min(size[0], a[2]+pad), min(size[1], a[3]+pad)]
    return {'method': 'Vision OCR spatial text-density rules v2; no semantic classification',
            'scope': 'Heuristic region proposals; no model confidence or field-accuracy guarantee',
            'status': 'dense_regions_found' if regions else 'no_dense_regions',
            'preserve_original': True, 'ocr_scale': unit,
            'unassigned_paragraph_ids': [p['id'] for p in items if p['id'] not in covered],
            'parameters': {'dense_gap_units': 4, 'minimum_characters': 12,
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
