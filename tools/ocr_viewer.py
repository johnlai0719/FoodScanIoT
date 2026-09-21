"""Read-only local viewer for saved EasyOCR output; no model inference."""
import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

from easyocr_baseline import resolve_image


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--results', type=Path, required=True)
    parser.add_argument('--images', type=Path, required=True)
    parser.add_argument('--port', type=int, default=8765)
    args = parser.parse_args()
    cases = [json.loads(p.read_text(encoding='utf-8')) for p in sorted(args.results.glob('c*.json'))]
    paths = []
    for case in cases:
        for image in case.get('images', []):
            image['viewer_id'] = len(paths)
            paths.append(resolve_image(args.images, image['path']).resolve())
    root = args.images.resolve()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            url = urlparse(self.path)
            if url.path == '/':
                data = Path(__file__).with_name('ocr_viewer.html').read_bytes()
                mime = 'text/html; charset=utf-8'
            elif url.path == '/api/cases':
                data = json.dumps(cases, ensure_ascii=False).encode()
                mime = 'application/json; charset=utf-8'
            elif url.path == '/image':
                try:
                    index = int(parse_qs(url.query)['id'][0])
                    if index < 0:
                        raise ValueError()
                    path = paths[index]
                    if not path.is_relative_to(root):
                        raise ValueError()
                    data = path.read_bytes()
                    mime = 'image/png' if path.suffix.lower() == '.png' else 'image/jpeg'
                except (KeyError, ValueError, IndexError, OSError):
                    self.send_error(404)
                    return
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header('Content-Type', mime)
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    print(f'EasyOCR viewer: http://127.0.0.1:{args.port}', flush=True)
    ThreadingHTTPServer(('127.0.0.1', args.port), Handler).serve_forever()


if __name__ == '__main__':
    main()
