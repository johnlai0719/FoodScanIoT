#!/usr/bin/env python3
"""把「裁切框落在哪裡」畫出來，逐案排成一頁。

**為什麼要有這支**：`region_crop` 是整條管線的單點故障——錨點找錯，
後面全錯而且**會安靜地錯**。但現有的一切都只看得到裁切**之後**的文字：
`found=True` 不告訴你框在哪，指標也數不出「框到別的地方」
（2026-09-09 試作三種「框內錨點」定義全部失敗，見 `box_audit.py` 檔頭）。

**人眼一秒就能看出來的事，我們花了一整天用指標去逼近。** 這支補上那一眼。

輸出 `_croppreview/`：每案一張標了框的原圖縮圖 ＋ 實際送進模型的裁切圖 ＋
模型讀出來的文字。可用 `測試/量化測試/review_server.py` 透過 Tailscale 分享。

用法：
    python crop_preview.py                    # 全部 177 案
    python crop_preview.py --only=c99,c44     # 指定案例
    python crop_preview.py --zero             # 只產「成分 0 命中」的案例
    python crop_preview.py --good             # 只產「正常運作」的案例（各品類抽樣）

⚠ `--zero` 與 `--good` 要**一起看**：沒有正常的對照，看不出壞的壞在哪。
   兩者寫成不同的 index 檔（`index_zero.html`／`index_good.html`），
   圖片共用同一個 `_croppreview/<case_id>/` 目錄。
"""
import html
import json
import os
import sys

import numpy as np
from PIL import Image, ImageDraw

sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import region_crop as RC          # noqa: E402
import score_ocr as S             # noqa: E402
import sim_match as SM            # noqa: E402
import bench_ingredients as BI    # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, '_croppreview')
BOXES = os.environ.get('PPOCR_BOXES') or 'v6_best'
READER = os.environ.get('CP_READER') or 'vlcrop_hy_v4'
OVERLAY_PX = 1000
CROP_PX = 1100
COLORS = {'ingredients': (0, 170, 255), 'nutrition': (255, 140, 0)}


def imread(p):
    """⚠ cv2/PIL 在 Windows 讀中文路徑各有雷；PIL 走 bytes 最穩。"""
    with open(p, 'rb') as f:
        import io as _io
        return Image.open(_io.BytesIO(f.read())).convert('RGB')


def find_image(rel):
    p = os.path.join(S.EVAL_ROOT, rel.replace('/', os.sep))
    if os.path.exists(p):
        return p
    alt = os.path.splitext(p)[0] + '.jpg'
    return alt if os.path.exists(alt) else None


def draw_case(cid):
    """回傳 [(第幾張圖, 疊框縮圖檔名, [(區域, 裁切圖檔名, 送出尺寸)]), ...]"""
    p = os.path.join(HERE, 'out', BOXES, cid + '.json')
    if not os.path.exists(p):
        return []
    rec = json.load(open(p, encoding='utf-8'))
    os.makedirs(os.path.join(OUT, cid), exist_ok=True)
    out = []
    for idx, im in enumerate(rec.get('images') or []):
        lines = [l for l in (im.get('lines') or []) if l.get('box')]
        if not lines:
            continue
        f = find_image(im['path'])
        if not f:
            continue
        img = imread(f)
        r = RC.find_regions(lines)
        ov = img.copy()
        d = ImageDraw.Draw(ov)
        # 先畫所有 PP-OCR 行框（淡），再畫區域框（粗）——
        # 這樣看得出「文字讀到了但沒被框進去」的情況
        for l in lines:
            b = RC.bbox(l['box'])
            d.rectangle(b, outline=(190, 190, 190), width=2)
        crops = []
        for kind in ('ingredients', 'nutrition'):
            box = r.get(kind)
            if not box:
                continue
            d.rectangle([int(v) for v in box], outline=COLORS[kind], width=9)
            c = None
            try:
                import run_vlcrop as V
                rot = V.region_needs_rotation(lines, box)
                c = V.crop(img, box, 90 if rot else 0)
            except Exception:
                x0, y0, x1, y1 = [int(v) for v in box]
                c = img.crop((x0, y0, x1, y1))
            sent = c.size
            c.thumbnail((CROP_PX, CROP_PX))
            cf = '%d_%s.jpg' % (idx, kind)
            c.save(os.path.join(OUT, cid, cf), quality=88)
            crops.append((kind, cf, sent))
        ov.thumbnail((OVERLAY_PX, OVERLAY_PX))
        of = '%d_overlay.jpg' % idx
        ov.save(os.path.join(OUT, cid, of), quality=85)
        out.append((idx, of, crops))
    return out


def reader_text(cid):
    p = os.path.join(HERE, 'out', READER, cid + '.json')
    if not os.path.exists(p):
        return []
    j = json.load(open(p, encoding='utf-8'))
    return [l.get('text', '') for im in (j.get('images') or [])
            for l in (im.get('lines') or [])]


def main():
    only = None
    mode = 'all'
    ngood = 12
    for a in sys.argv[1:]:
        if a.startswith('--only='):
            only = set(a.split('=', 1)[1].split(','))
        elif a == '--zero':
            mode = 'zero'
        elif a == '--good':
            mode = 'good'
        elif a.startswith('--good='):
            mode = 'good'
            ngood = int(a.split('=', 1)[1])
        else:
            sys.exit('看不懂的參數：%s' % a)
    adds, generic = SM.load_additives()
    BI.BOXES = READER
    cases = json.load(open(os.path.join(S.EVAL_ROOT, 'cases.json'),
                           encoding='utf-8'))['cases']
    os.makedirs(OUT, exist_ok=True)
    rows = []
    cand = []
    for c in cases:
        cid = c['case_id']
        if only and not any(cid.startswith(o) for o in only):
            continue
        gt = S.load_gt(cid, c.get('category') or '')
        if not gt or not gt.get('is_food_label', True):
            continue
        gl = gt.get('ingredients_list') or []
        jp = os.path.join(HERE, 'out', 'json', cid + '.json')
        items = (json.load(open(jp, encoding='utf-8')).get('ingredients_list')
                 if os.path.exists(jp) else []) or []
        hit = BI.score_one(items, gl)[0] if gl else 0
        if mode == 'zero' and not (gl and hit == 0):
            continue
        if mode == 'good' and not (gl and len(gl) >= 5 and hit >= 0.8 * len(gl)):
            continue
        cand.append((cid, c.get('category') or '', len(gl), hit, items))
    # `--good` 依品類分層抽樣——全收會太多，而且同一品類看三案就夠了
    if mode == 'good':
        import collections as _c
        by = _c.defaultdict(list)
        for x in cand:
            by[x[1]].append(x)
        pick = []
        i = 0
        while len(pick) < ngood and any(len(v) > i for v in by.values()):
            for k in sorted(by):
                if len(by[k]) > i and len(pick) < ngood:
                    pick.append(by[k][i])
            i += 1
        cand = pick
    for cid, cat, ngt, hit, items in cand:
        imgs = draw_case(cid)
        if not imgs:
            continue
        rows.append((cid, cat, ngt, hit, items, reader_text(cid), imgs))
        print('[%3d] %-30s 命中 %2d/%-3d' % (len(rows), cid, hit, ngt))
    # ── 頁面 ──
    e = html.escape
    h = ['<!doctype html><meta charset="utf-8"><title>裁切框檢視</title>',
         '<style>body{font-family:system-ui;margin:1.5rem;background:#fafafa}'
         '.c{background:#fff;border:1px solid #ddd;border-radius:8px;'
         'padding:1rem;margin-bottom:1.5rem}'
         'h3{margin:.2rem 0}.bad{color:#c00;font-weight:700}'
         '.row{display:flex;gap:1rem;flex-wrap:wrap;align-items:flex-start}'
         'img{max-width:100%;border:1px solid #ccc}'
         '.ov{max-width:420px}.cr{max-width:340px}'
         '.t{font-size:.82em;max-height:16rem;overflow:auto;background:#f6f6f6;'
         'padding:.5rem;white-space:pre-wrap;flex:1;min-width:18rem}'
         '.lg{font-size:.8em;color:#666}'
         'b.i{color:#0aa}b.n{color:#e80}</style>',
         '<h2>裁切框檢視（%s） <span class="lg">' % {
             'zero': '成分 0 命中', 'good': '正常運作', 'all': '全部'}[mode] +
         '<span class="lg">'
         '<b class="i">藍＝成分區</b>　<b class="n">橙＝營養區</b>　'
         '灰＝PP-OCR 的行框</span></h2>',
         '<p class="lg">灰框在藍框外，代表「文字讀到了但沒被框進去」。</p>']
    for cid, cat, ngt, hit, items, rtext, imgs in rows:
        cls = ' bad' if ngt and hit == 0 else ''
        h.append('<div class="c"><h3><span class="%s">%s</span> '
                 '<span class="lg">%s ｜ 成分命中 %d/%d</span></h3>'
                 % (cls.strip(), e(cid), e(cat), hit, ngt))
        for idx, ov, crops in imgs:
            h.append('<div class="row">')
            h.append('<div><div class="lg">整張圖 ＋ 框</div>'
                     '<img class="ov" src="%s/%s"></div>' % (e(cid), ov))
            for kind, cf, sent in crops:
                h.append('<div><div class="lg">%s　送出 %d×%d</div>'
                         '<img class="cr" src="%s/%s"></div>'
                         % (kind, sent[0], sent[1], e(cid), cf))
            h.append('</div>')
        h.append('<div class="row" style="margin-top:.6rem">')
        h.append('<div class="t"><b>模型讀出來的文字</b>\n%s</div>'
                 % e('\n'.join(rtext)[:2600]))
        h.append('<div class="t"><b>抽出的成分 %d 項</b>\n%s</div>'
                 % (len(items), e('\n'.join(items)[:1400])))
        h.append('</div></div>')
    name = {'zero': 'index_zero.html', 'good': 'index_good.html'}.get(mode, 'index.html')
    idx = os.path.join(OUT, name)
    open(idx, 'w', encoding='utf-8').write('\n'.join(h))
    print('\n%d 案 → %s' % (len(rows), idx))


if __name__ == '__main__':
    main()
