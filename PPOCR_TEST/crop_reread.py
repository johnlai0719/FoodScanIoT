#!/usr/bin/env python3
# 第二遍：把營養表那一小塊裁出來、放大、單獨再讀一次。
#
# 為什麼這樣有機會贏過整張圖：
#   v6_best 把 4284x5712 的照片縮到 2048/max 才進偵測，縮放比約 0.36。
#   營養表在包裝上通常只佔一小塊，縮完之後字高剩下 8-12 px——rec 在這個
#   尺度上開始掉小數點（c02_濃豆漿 把 19.1 讀成 191、7.9 讀成 79，整批掉）。
#   成分表的字大得多，所以整張圖那一遍對成分是夠的，對營養表不夠。
#   裁出來單獨讀，同樣的 2048 預算全部給那一小塊，字高回到 40-60 px。
#
# 這不是影像前處理（那九種全部無效，見 sweep_preprocess.py），也不是訓練。
# 它改變的是**模型看到多少像素**，跟當初 limit_side_len 960→2048 同一類，
# 而那次是所有嘗試裡增益最大的一個。
#
# 區域怎麼找：用第一遍已經讀到的文字。營養表的欄位名（熱量／蛋白質／碳水…）
# 就算讀錯幾個字也還在那一區，把命中關鍵詞的框圈起來取外接矩形即可。
# 不需要版面偵測模型，也不會因為表格線沒偵測到而失敗。
#
# 用法：
#   python crop_reread.py                    # 全部 58 案
#   python crop_reread.py --cases c58 c02
#   python crop_reread.py --dump out/crops   # 順便存出裁切圖，肉眼檢查用
import argparse
import json
import os
import re
import sys
import time

import cv2
import numpy as np

sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import run_baseline as RB   # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, 'out', 'v6_hires__boxth0.4')
OUT = os.path.join(HERE, 'out', 'nutrition_crop')

# 關鍵詞分兩級。這個區分是必要的：「糖」「鈉」「公克」在成分表裡滿地都是
# （c58_拉麵道 的成分表有十幾個「鈉」），拿它們當種子會把整張圖圈進來——
# 實測那樣做 c58 圈出 2282x4301，縮放比 0.48，比原本那遍還糟。
STRONG = re.compile(r'營養標示|营养标示|營養標|每一份量|每份量|每\s*100|熱量|热量|大卡')
WEAK = re.compile(
    r'營養|营养|標示|标示|每份|蛋白|脂肪|飽和|饱和|反式|碳水|化合物|'
    r'膳食|纖維|纤维|糖|鈉|钠|纳|毫克|公克|大卡')


def _box_rect(b):
    xs = [p[0] for p in b]
    ys = [p[1] for p in b]
    return min(xs), min(ys), max(xs), max(ys)


def _grow(seeds, cand, gap):
    """從種子往外長：把距離現有區域 gap 以內的框併進來，直到長不動為止。

    營養表是連續的一塊，成分表是另一塊，兩者中間通常隔著明顯空白。
    用「鄰近才併」而不是「全部取外接矩形」，兩塊就分得開。
    """
    x0, y0, x1, y1 = seeds
    used = [False] * len(cand)
    for _ in range(20):
        moved = False
        for i, (a0, b0, a1, b1) in enumerate(cand):
            if used[i]:
                continue
            if a0 > x1 + gap or a1 < x0 - gap or b0 > y1 + gap or b1 < y0 - gap:
                continue
            x0, y0 = min(x0, a0), min(y0, b0)
            x1, y1 = max(x1, a1), max(y1, b1)
            used[i] = True
            moved = True
        if not moved:
            break
    return x0, y0, x1, y1


def _region(lines, w, h):
    """找營養表所在的矩形。找不到（或裁了沒好處）回 None。"""
    boxes = [(l, _box_rect(l['box'])) for l in lines if l.get('box')]
    strong = [r for l, r in boxes if STRONG.search(l.get('text') or '')]
    if len(strong) < 2:
        return None
    # 試過「小圖整張放大不裁」（max(w,h)*1.5 <= 2048 就回整張）：694→692。
    # 動機是 c02_濃豆漿 原圖只有 254x300，區域演算法上下各少圈 9px，
    # 剛好把鈉那一列切掉。但整張放大會把成分表的雜訊一起放大丟進候選池，
    # 別案的損失蓋過 c02 的收益。裁切區域的邊界仍是這支最脆弱的地方。
    weak = [r for l, r in boxes if WEAK.search(l.get('text') or '')]
    # 種子取強關鍵詞的外接矩形；行高當作「鄰近」的尺度
    sx0 = min(r[0] for r in strong); sy0 = min(r[1] for r in strong)
    sx1 = max(r[2] for r in strong); sy1 = max(r[3] for r in strong)
    hh = sorted(r[3] - r[1] for l, r in boxes) or [20]
    gap = max(30, hh[len(hh) // 2] * 3)
    x0, y0, x1, y1 = _grow((sx0, sy0, sx1, sy1), weak, gap)

    mx, my = (x1 - x0) * 0.06 + 15, (y1 - y0) * 0.06 + 15
    x0, y0 = max(0, int(x0 - mx)), max(0, int(y0 - my))
    x1, y1 = min(w, int(x1 + mx)), min(h, int(y1 + my))
    if x1 - x0 < 60 or y1 - y0 < 60:
        return None
    # 這裡不用面積當條件。第二遍真正的增益是**字變高**，而字高取決於
    # 放大倍率，不取決於裁掉多少。踩過一次：先前寫「面積超過 45% 就放棄」，
    # 結果把 c02_濃豆漿 擋掉了——它原圖只有 254x300、營養表佔滿整張，
    # 但正是那張靠 2.5 倍放大從「小數點整批掉」變成幾乎全對。
    # 該擋的是「裁完還是太大、放不了大」的情況，交給呼叫端用倍率判斷。
    return x0, y0, x1, y1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--cases', nargs='*', default=None)
    ap.add_argument('--device', default='gpu')
    ap.add_argument('--side', type=int, default=2048)
    ap.add_argument('--dump', default=None, help='把裁切圖存到這個資料夾')
    a = ap.parse_args()

    files = sorted(f for f in os.listdir(SRC) if f.endswith('.json'))
    if a.cases:
        files = [f for f in files if any(f.startswith(c) for c in a.cases)]
    os.makedirs(OUT, exist_ok=True)
    if a.dump:
        os.makedirs(a.dump, exist_ok=True)

    from paddleocr import PaddleOCR
    base = RB.PRESETS['v6_best']
    ocr = PaddleOCR(device=a.device, **base['init'])

    nc = ns = 0
    t0 = time.time()
    for n, f in enumerate(files, 1):
        d = json.load(open(os.path.join(SRC, f), encoding='utf-8'))
        cid = d['case_id']
        rec = {'case_id': cid, 'preset': 'nutrition_crop',
               'category': d.get('category'), 'images': []}
        for im in d['images']:
            rel = im['path']
            p = os.path.join(RB.EVAL_ROOT, *rel.split('/'))
            if not os.path.exists(p) or not im.get('lines'):
                continue
            # cv2.imread 讀不了中文路徑
            img = cv2.imdecode(np.fromfile(p, dtype=np.uint8), cv2.IMREAD_COLOR)
            if img is None:
                continue
            h, w = img.shape[:2]
            box = _region(im['lines'], w, h)
            if not box:
                ns += 1
                continue
            x0, y0, x1, y1 = box
            crop = img[y0:y1, x0:x1]
            # 裁完通常遠小於 2048，放大讓 rec 拿到夠高的字。
            # 上限 2.5 倍——再放大只是插值出來的假細節，不會多出資訊。
            scale = min(2.5, a.side / max(crop.shape[:2]))
            # 放不了大就沒有做第二遍的理由：字高不會變，只是重跑一次。
            # c58_拉麵道 的區域圈到 2282x4301，倍率 0.48（反而縮小），
            # 讀出來整段是成分表——這種要擋掉，省一次推論也避免污染候選池。
            if scale < 1.15:
                ns += 1
                continue
            crop = cv2.resize(crop, None, fx=scale, fy=scale,
                              interpolation=cv2.INTER_CUBIC)
            if a.dump:
                cv2.imencode('.jpg', crop)[1].tofile(
                    os.path.join(a.dump, f'{cid}_{os.path.basename(rel)}'))
            try:
                results = ocr.predict(crop, **base['predict'])
            except Exception as e:
                print(f'   {cid} 裁切後推論失敗：{e!r}')
                continue
            lines = []
            for r in results:
                lines.extend(RB.extract_lines(r))
            nc += 1
            rec['images'].append({'path': rel, 'crop': [x0, y0, x1, y1],
                                  'scale': round(scale, 2),
                                  'n_lines': len(lines), 'lines': lines})
        with open(os.path.join(OUT, f'{cid}.json'), 'w',
                  encoding='utf-8', newline='\n') as fh:
            json.dump(rec, fh, ensure_ascii=False, indent=1)
        print(f'[{n}/{len(files)}] {cid:<34}裁出 {len(rec["images"])} 張')

    print(f'\n裁切成功 {nc} 張｜找不到營養區 {ns} 張｜{time.time() - t0:.0f}s')
    print(f'→ {OUT}\n下一步：python bench_parse.py（會自動把它當成一個視角）')


if __name__ == '__main__':
    main()
