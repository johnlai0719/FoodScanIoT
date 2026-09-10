#!/usr/bin/env python3
"""把檢視頁與照片透過 Tailscale 分享給組員（**唯讀**）。

為什麼不是把檔案同步到雲端硬碟：檢視頁引用的是**原圖**（222 張、824 MB），
放大磚塊全產更是 1.7 GB。同步一次要很久，而且每次重產都要再同步一次。
這支直接服務本機的檔案，組員看到的永遠是最新的。

**唯讀**：只處理 GET／HEAD，任何寫入方法一律 405。
資料的權威來源仍是這台機器上的 `ground_truth/`——
組員發現正解有問題請回報，不要各自改各自的（兩份正解分岔會毀掉跨期比較）。

**只綁 Tailscale 介面**，不綁 0.0.0.0：後者會讓同一個 Wi-Fi 上的任何人
（宿舍、咖啡廳、學校網路）都連得進來。Tailscale 是私有網路，只有 tailnet
成員連得到。

用法：
    python review_server.py                 # 綁 Tailscale IP，埠 8090
    python review_server.py --port=9000
    python review_server.py --any           # 綁 0.0.0.0（⚠ 同網段皆可存取）

開啟後把印出來的網址給組員即可。要停就 Ctrl-C。
"""
import http.server
import os
import socketserver
import subprocess
import sys

sys.stdout.reconfigure(encoding='utf-8')

HERE = os.path.dirname(os.path.abspath(__file__))
# 允許被瀏覽的東西。其餘一律 404——這台機器上還有 out/、_contaminated_* 等
# 不該外流的中間產物，白名單比黑名單安全。
ALLOW_DIRS = ('images', '_zoom', '_thumbs', 'ground_truth', '_croppreview')
# 裁切框檢視在 PPOCR_TEST 底下，不在本目錄——用 symlink 之外的方式接進來：
# 直接把它加進 EXTRA_MOUNT，由 translate_path 轉址。
EXTRA_MOUNT = {
    '_croppreview': os.path.normpath(os.path.join(
        HERE, '..', '..', 'PPOCR_TEST', '_croppreview')),
}
ALLOW_FILES = ('_review_gt.html', '_review_pred.html',
               '_difficulty_worksheet.html', '_material_worksheet.html',
               '_intake_viewer.html', 'cases.json')


def tailscale_ip():
    try:
        out = subprocess.run(['tailscale', 'ip', '-4'], capture_output=True,
                             text=True, timeout=10).stdout.strip().splitlines()
        return out[0].strip() if out else None
    except Exception:
        return None


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=HERE, **kw)

    def translate_path(self, path):
        """把 EXTRA_MOUNT 裡的前綴轉到別的實體目錄。"""
        import posixpath
        import urllib.parse
        rel = urllib.parse.unquote(path.split('?', 1)[0].split('#', 1)[0])
        rel = posixpath.normpath(rel).lstrip('/')
        top = rel.split('/', 1)[0]
        if top in EXTRA_MOUNT:
            tail = rel[len(top):].lstrip('/').replace('/', os.sep)
            # ⚠ normpath 之後再檢查一次，擋掉 ../ 逃逸
            full = os.path.normpath(os.path.join(EXTRA_MOUNT[top], tail))
            if not full.startswith(EXTRA_MOUNT[top]):
                return os.path.join(HERE, '__forbidden__')
            return full
        return super().translate_path(path)

    def _allowed(self, path):
        rel = path.lstrip('/').split('?')[0]
        if rel in ('', 'index.html'):
            return True
        rel = rel.replace('/', os.sep)
        if rel in ALLOW_FILES:
            return True
        top = rel.split(os.sep)[0]
        return top in ALLOW_DIRS

    def do_GET(self):
        import urllib.parse
        p = urllib.parse.unquote(self.path)
        if p.rstrip('/') in ('', '/'):
            return self._index()
        if not self._allowed(p):
            self.send_error(404)
            return
        return super().do_GET()

    def do_HEAD(self):
        import urllib.parse
        if not self._allowed(urllib.parse.unquote(self.path)):
            self.send_error(404)
            return
        return super().do_HEAD()

    # ⚠ 不能只寫 `do_POST = _blocked`——BaseHTTPRequestHandler 在讀完
    #    request line 之後才分派，未實作的方法會直接斷線（curl 看到的是
    #    HTTP 000 而不是 405）。要回明確的 405，得覆寫 handle_one_request
    #    之後的分派點；這裡用最簡單的做法：顯式定義每一個方法。
    def _blocked(self):
        try:
            n = int(self.headers.get('Content-Length') or 0)
            if n:
                self.rfile.read(n)          # 先把 body 讀掉，否則連線會卡住
        except Exception:
            pass
        self.send_error(405, 'read-only review server')

    def do_POST(self):
        self._blocked()

    def do_PUT(self):
        self._blocked()

    def do_DELETE(self):
        self._blocked()

    def do_PATCH(self):
        self._blocked()

    def _index(self):
        pages = [(f, os.path.getsize(os.path.join(HERE, f)))
                 for f in ALLOW_FILES if f.endswith('.html')
                 and os.path.exists(os.path.join(HERE, f))]
        zooms = sorted(os.listdir(os.path.join(HERE, '_zoom'))) \
            if os.path.isdir(os.path.join(HERE, '_zoom')) else []
        h = ['<!doctype html><meta charset="utf-8">',
             '<title>FoodScan 檢視</title>',
             '<style>body{font-family:system-ui;margin:2rem;max-width:56rem;'
             'line-height:1.7}a{display:block;padding:.4rem 0}'
             'code{background:#f4f4f4;padding:.1rem .3rem}'
             '.n{color:#666;font-size:.85em}</style>',
             '<h2>FoodScan 檢視站台（唯讀）</h2>',
             '<p class="n">資料的權威來源是這台機器。發現正解有問題請回報，'
             '不要各自修改——兩份正解分岔會讓跨期比較失效。</p>',
             '<h3>並排檢視</h3>']
        for f, sz in pages:
            note = {'_review_gt.html': '照片 × 正解 —— 正解和照片對不對得上',
                    '_review_pred.html': '照片 × 正解 × 各家預測 —— 模型錯在哪裡',
                    '_difficulty_worksheet.html': '拍攝條件標註（曲面／反光／皺摺／模糊）',
                    '_material_worksheet.html': '包裝形狀與反光材質',
                    }.get(f, '')
            h.append('<a href="/%s">%s <span class="n">%s · %.1f MB</span></a>'
                     % (f, f, note, sz / 1e6))
        cp = EXTRA_MOUNT.get('_croppreview')
        if cp and os.path.isdir(cp):
            found = [(f, lab) for f, lab in
                     (('index_good.html', '**正常運作**的案例（各品類抽樣）'),
                      ('index_zero.html', '**成分 0 命中**的案例'),
                      ('index.html', '全部案例'))
                     if os.path.exists(os.path.join(cp, f))]
            if found:
                h.append('<h3>裁切框檢視 <span class="n">'
                         '每案：整張圖＋框、實際送進模型的裁切圖、模型讀出來的文字。'
                         '<b>灰框在藍框外＝文字讀到了但沒被框進去</b>。'
                         '兩份要一起看——沒有正常的對照，看不出壞的壞在哪</span></h3>')
                for f, lab in found:
                    h.append('<a href="/_croppreview/%s">%s <span class="n">%s</span></a>'
                             % (f, f, lab))
        if zooms:
            h.append('<h3>放大磚塊（%d 案）<span class="n"> '
                     '原生解析度切塊＋銳化，供逐字核對正解</span></h3>' % len(zooms))
            for z in zooms:
                if os.path.exists(os.path.join(HERE, '_zoom', z, 'index.html')):
                    h.append('<a href="/_zoom/%s/index.html">%s</a>' % (z, z))
        body = '\n'.join(h).encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        print('   %s - %s' % (self.address_string(), fmt % args))


def main():
    port = 8090
    host = None
    for a in sys.argv[1:]:
        if a.startswith('--port='):
            port = int(a.split('=', 1)[1])
        elif a == '--any':
            host = '0.0.0.0'
        else:
            sys.exit('看不懂的參數：%s' % a)
    if host is None:
        host = tailscale_ip()
        if not host:
            sys.exit('取不到 Tailscale IP。確認 Tailscale 在執行，'
                     '或用 --any 綁全部介面（⚠ 同網段皆可存取）。')
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.ThreadingTCPServer((host, port), Handler) as srv:
        print('=== FoodScan 檢視站台（唯讀）===')
        print('   目錄   %s' % HERE)
        print('   綁定   %s:%d %s' % (host, port,
                                      '（Tailscale 私有網路）' if host != '0.0.0.0'
                                      else '（⚠ 同網段皆可存取）'))
        print('\n把這個網址給組員：')
        print('   http://%s:%d/' % (host, port))
        print('\nCtrl-C 停止。')
        try:
            srv.serve_forever()
        except KeyboardInterrupt:
            print('\n已停止。')


if __name__ == '__main__':
    main()
