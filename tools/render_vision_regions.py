"""Render semantic region groups from saved Vision OCR; no API calls."""
import argparse
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps


def render(result_dir, groups_path, output):
    metrics = json.loads((result_dir / 'metrics.json').read_text(encoding='utf-8'))
    response = json.loads((result_dir / 'vision_response.json').read_text(encoding='utf-8'))
    groups = json.loads(groups_path.read_text(encoding='utf-8'))
    blocks = [b for page in response['responses'][0]['fullTextAnnotation']['pages']
              for b in page.get('blocks', [])]
    with Image.open(metrics['source']) as source:
        image = ImageOps.exif_transpose(source).convert('RGB')
    output.mkdir(parents=True, exist_ok=True)
    (output / 'crops').mkdir(exist_ok=True)
    preview = image.copy()
    preview.thumbnail((1500, 2000))
    sx, sy = preview.width / image.width, preview.height / image.height
    font = ImageFont.truetype('C:/Windows/Fonts/msjhbd.ttc', 24)
    layer = Image.new('RGBA', preview.size)
    tint = ImageDraw.Draw(layer)
    annotations = []
    for index, group in enumerate(groups['regions']):
        if 'rect' in group:
            rect = group['rect']
        else:
            points = [v for i in group['block_ids'] for v in blocks[i]['boundingBox']['vertices']]
            pad = group.get('padding', 24)
            rect = [max(0, min(v.get('x', 0) for v in points)-pad),
                    max(0, min(v.get('y', 0) for v in points)-pad),
                    min(image.width, max(v.get('x', 0) for v in points)+pad),
                    min(image.height, max(v.get('y', 0) for v in points)+pad)]
        color = group['color']
        box = [round(rect[0]*sx), round(rect[1]*sy), round(rect[2]*sx), round(rect[3]*sy)]
        rgb = tuple(int(color[i:i+2], 16) for i in (1, 3, 5))
        tint.rectangle(box, fill=rgb+(22,))
        stem = '%02d_%s' % (index+1, group['class'])
        image.crop(rect).save(output / 'crops' / (stem+'.jpg'), quality=95)
        annotations.append({**group, 'rect_xyxy': rect, 'crop': 'crops/'+stem+'.jpg',
            'display_rect': box, 'normalized_xywh': [
                (rect[0]+rect[2])/2/image.width, (rect[1]+rect[3])/2/image.height,
                (rect[2]-rect[0])/image.width, (rect[3]-rect[1])/image.height]})
    preview = Image.alpha_composite(preview.convert('RGBA'), layer).convert('RGB')
    draw = ImageDraw.Draw(preview)
    occupied = []
    def overlap(a, b):
        return a[0] < b[2] and a[2] > b[0] and a[1] < b[3] and a[3] > b[1]
    for region in annotations:
        box = region['display_rect']
        draw.rectangle(box, outline=region['color'], width=4)
        label = region['label']
        bounds = draw.textbbox((0, 0), label, font=font)
        width, height = bounds[2]-bounds[0]+16, bounds[3]-bounds[1]+12
        x = max(0, min(box[0], preview.width-width))
        candidates = [[x, max(0, box[1]-height), x+width, max(0, box[1]-height)+height],
                      [x, box[1], x+width, box[1]+height],
                      [x, min(box[3], preview.height-height), x+width,
                       min(box[3], preview.height-height)+height]]
        tag = next((c for c in candidates if not any(overlap(c, p) for p in occupied)), candidates[0])
        occupied.append(tag)
        draw.rectangle(tag, fill=region['color'])
        draw.text((tag[0]+8, tag[1]+6-bounds[1]), label, fill='white', font=font)
    preview.save(output / 'regions_annotated.jpg', quality=95)
    manifest = {'method': groups.get('method', 'Vision OCR coordinates + human-reviewed semantic block groups'),
                'source': metrics['source'], 'source_sha256': metrics['source_sha256'],
                'source_size': list(image.size), 'regions': annotations,
                'scope': groups.get('scope', 'Single-image visualization prototype, not YOLO predictions; no confidence scores.')}
    (output / 'annotations.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'regions': len(annotations), 'preview': str(output/'regions_annotated.jpg')}, ensure_ascii=True))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--result-dir', type=Path, required=True)
    parser.add_argument('--groups', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    render(args.result_dir, args.groups, args.output)
