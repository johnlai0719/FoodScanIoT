#!/usr/bin/env python3
"""在裁切階段以傳統影像特徵偵測「文字面板反光」，供上傳前提示重拍。

⚠ **偵測不是去除。** 專案已實測「去高光前處理」對辨識**顯著有害**
（[[06-裁切後前處理對 vlcrop 的影響]]：−3.5 點，CI −6.5 ～ −0.7）。
本檔只讀不改像素。

**目標不是「反光」，是「曲面＋反光」。** 實測（添加物層 F1 對其餘案例）：
    反光但不曲面   16 案   +6.9   CI  −7.3 ～ +15.9   跨 0，**沒問題**
    曲面但不反光   13 案   −0.4   CI −21.2 ～ +17.4   跨 0，**沒問題**
    曲面＋反光     12 案  −31.3   CI −47.6 ～ −15.0   **顯著崩壞**
純反光偵測器會誤擋那 16 案。

**免費的對照基準**：PP-OCR 的貼邊行信心。曲面＋反光 0.884，其餘三組 ≥0.97。
像素特徵要有價值，必須贏過或補足它——後者的理由是**信心只存在於偵測到
文字的地方**，反光把一整塊洗掉時信心給不出值，像素看得到。
"""
import collections
import json
import os
import re
import statistics
import sys

import cv2
import numpy as np

sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import region_crop as RC   # noqa: E402
import score_ocr as S      # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))


def edge_center_ratio(bgr, horiz=True, edge=0.20):
    """**同一塊面板內**：邊緣帶的對比 ÷ 中央帶的對比。

    為什麼用比值而不是絕對值：絕對門檻要在資料上校準，12 個正例校出來的門檻
    必然過擬合（2026-09-10 第一版就是這樣，精確 50% 是在同一批案例上挑的）。
    比值是**自我參照**的——拿區域自己的中央當基準，換相機、換光線、換包裝
    都不需要重新校準。

    這也正是量到的機制的像素版：曲面＋反光的貼邊行信心 0.884、正中 0.960，
    其餘三組全平。反光洗掉的是**字與底的對比**，不是把畫面變亮
    （實測該組的鏡面高光像素反而是四組最少）。
    """
    g = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    H, W = g.shape[:2]
    n = W if horiz else H
    k = max(8, int(n * edge))
    if n < 3 * k:
        return None
    def con(a):
        if a.size < 64:
            return None
        return float(cv2.Laplacian(a, cv2.CV_64F).var())
    if horiz:
        e = [con(g[:, :k]), con(g[:, -k:])]
        c = con(g[:, k:-k])
    else:
        e = [con(g[:k, :]), con(g[-k:, :])]
        c = con(g[k:-k, :])
    e = [x for x in e if x]
    if not e or not c:
        return None
    return min(e) / c          # 最差的那一側


def feats(bgr):
    """文字面板的高光特徵。全部只讀不改。"""
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    Hh, Sx, Vv = cv2.split(hsv)
    n = Vv.size
    # 鏡面反射的典型樣態：**亮且不飽和**。單看亮度會把白底誤判成反光。
    spec = ((Vv > 240) & (Sx < 40))
    spec2 = ((Vv > 225) & (Sx < 50))
    # 高光是否連成一片（散開的亮點多半是白底或雜訊，成片才是反射帶）
    m = (spec2.astype(np.uint8) * 255)
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
    nlab, _, stats, _ = cv2.connectedComponentsWithStats(m, 8)
    blob = (stats[1:, cv2.CC_STAT_AREA].max() / n) if nlab > 1 else 0.0
    # 局部對比：反光把字與底的差距洗掉
    g = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    lap = cv2.Laplacian(g, cv2.CV_64F).var()
    return {
        'spec': float(spec.mean()),
        'spec2': float(spec2.mean()),
        'blob': float(blob),
        'vstd': float(Vv.std()),
        'lap': float(lap),
        'vp99': float(np.percentile(Vv, 99)),
    }


def main():
    cases = json.load(open(os.path.join(S.EVAL_ROOT, 'cases.json'),
                           encoding='utf-8'))['cases']
    diff = {c['case_id']: set(c.get('difficulty') or []) for c in cases}
    out = {}
    for c in cases:
        cid = c['case_id']
        p = os.path.join(HERE, 'out', 'v6_best', cid + '.json')
        if not os.path.exists(p):
            continue
        rec = json.load(open(p, encoding='utf-8'))
        best = None
        for im in rec['images']:
            lines = [l for l in (im.get('lines') or []) if l.get('box')]
            if not lines:
                continue
            r = RC.find_regions(lines)
            box = r['ingredients']
            if not box:
                continue
            f = os.path.join(S.EVAL_ROOT, im['path'].replace('/', os.sep))
            if not os.path.exists(f):
                alt = os.path.splitext(f)[0] + '.jpg'
                if not os.path.exists(alt):
                    continue
                f = alt
            # ⚠ cv2.imread 在 Windows 讀不了含中文的路徑（回 None 且不報錯）。
            # 專案的 case_id 一律含中文，必須走 fromfile + imdecode。
            img = cv2.imdecode(np.fromfile(f, dtype=np.uint8), cv2.IMREAD_COLOR)
            if img is None:
                continue
            H, W = img.shape[:2]
            x0, y0 = max(0, int(box[0])), max(0, int(box[1]))
            x1, y1 = min(W, int(box[2])), min(H, int(box[3]))
            if x1 - x0 < 20 or y1 - y0 < 20:
                continue
            sub = img[y0:y1, x0:x1]
            v = feats(sub)
            v['ecr'] = edge_center_ratio(sub, horiz=(x1 - x0) >= (y1 - y0)) or 1.0
            # 一案多圖時取高光最嚴重的那張——提示重拍是逐張給的
            if best is None or v['spec2'] > best['spec2']:
                best = v
        if best:
            best['d'] = sorted(diff.get(cid, ()))
            out[cid] = best
    json.dump(out, open(os.path.join(HERE, 'out', '_glare_feats.json'),
                        'w', encoding='utf-8'), ensure_ascii=False)
    print('算出 %d 案 → out/_glare_feats.json' % len(out))


if __name__ == '__main__':
    main()
