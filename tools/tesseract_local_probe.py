"""Portable Linux CPU probe; reuses the baseline reader's preprocessing/TSV parser.

Run on a PC or Pi, but compare hardware measurements only on the actual device.
The manifest explicitly lists sources/configurations; no cloud API is called.
"""
import argparse
import hashlib
import json
import os
import platform
import subprocess
import time
from pathlib import Path

from PIL import Image, ImageDraw
from tesseract_baseline import prepare_image, parse_tsv


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--tessdata-dir', type=Path, help='Optional model directory, otherwise use system models')
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text(encoding='utf-8'))
    args.output.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, OMP_THREAD_LIMIT='1', LC_ALL='C.UTF-8')
    if args.tessdata_dir:
        env['TESSDATA_PREFIX'] = str(args.tessdata_dir.resolve())
    version = subprocess.run(['tesseract', '--version'], capture_output=True, text=True, check=True)
    metadata = {'platform': platform.platform(), 'architecture': platform.machine(),
                'tesseract': version.stdout+version.stderr, 'language': 'chi_tra+eng', 'oem': 1,
                'OMP_THREAD_LIMIT': 1, 'memory_scope': 'GNU time maximum RSS for OCR child only; excludes Python and other services',
                'prepared_image_scope': 'EXIF corrected; resize capped, never upscaled; RGB; no thresholding',
                'manifest_sha256': hashlib.sha256(args.manifest.read_bytes()).hexdigest(),
                'tessdata_dir': env.get('TESSDATA_PREFIX', 'system default'),
                'runs': []}
    for run in manifest['runs']:
        folder = args.output/run['id']
        folder.mkdir(parents=True, exist_ok=True)
        source = Path(run['source'])
        prepared = folder/'input.png'
        preparation_start = time.perf_counter()
        prepare_image(source, prepared, 'none', run['max_side'])
        prep_seconds = time.perf_counter()-preparation_start
        time_log = folder/'resource.txt'
        command = ['/usr/bin/time', '-f', 'max_rss_kb=%M\nuser_s=%U\nsystem_s=%S', '-o', str(time_log),
                   'tesseract', str(prepared), 'stdout', '-l', 'chi_tra+eng', '--oem', '1',
                   '--psm', str(run['psm']), 'tsv']
        started = time.perf_counter()
        try:
            completed = subprocess.run(command, capture_output=True, text=True, encoding='utf-8',
                                       env=env, timeout=60)
            ocr_seconds = time.perf_counter()-started
            (folder/'raw.tsv').write_text(completed.stdout, encoding='utf-8')
            (folder/'stderr.txt').write_text(completed.stderr, encoding='utf-8')
            lines = parse_tsv(completed.stdout) if completed.returncode == 0 else []
            resources = dict(line.split('=', 1) for line in time_log.read_text().splitlines() if '=' in line)
            image = Image.open(prepared).convert('RGB')
            size = image.size
            draw = ImageDraw.Draw(image)
            for index, line in enumerate(lines, 1):
                left, top = line['box'][0]
                right, bottom = line['box'][2]
                draw.rectangle((left, top, right, bottom), outline='#ff253a', width=2)
                draw.text((left, max(0, top-12)), str(index), fill='#ff253a', stroke_width=1, stroke_fill='white')
            image.thumbnail((1200, 1600))
            image.save(folder/'boxes.jpg', quality=90)
            result = {**run, 'returncode': completed.returncode, 'prepared_size': size,
                      'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
                      'preparation_seconds': round(prep_seconds, 3), 'ocr_seconds': round(ocr_seconds, 3),
                      'prep_plus_ocr_seconds': round(prep_seconds+ocr_seconds, 3),
                      'max_rss_mib': round(int(resources['max_rss_kb'])/1024, 2), 'n_lines': len(lines),
                      'n_characters': sum(len(line['text']) for line in lines), 'lines': lines}
            if completed.returncode == 0 and not completed.stdout.startswith('level\tpage_num\t'):
                result['error'] = 'Invalid TSV output; inspect stderr and model-directory configs'
            (folder/'text.txt').write_text('\n'.join(line['text'] for line in lines), encoding='utf-8')
        except subprocess.TimeoutExpired:
            result = {**run, 'error': 'OCR subprocess timeout after 60 seconds'}
        (folder/'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        metadata['runs'].append({key: value for key, value in result.items() if key != 'lines'})
        (args.output/'summary.json').write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(metadata['runs'][-1], ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
