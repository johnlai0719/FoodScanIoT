#!/usr/bin/env python3
# 掃影像前處理：哪一種能把 58 案的辨識成績拉高。
#
# 為什麼值得做：這是零成本的變因，跟 sweep_params.py 同一個道理。
# 先前的教訓是「所有有效的改善都來自免費的東西」——解析度設定、版本升級、
# box_thresh。前處理是同一類，而且還沒試過。
#
# 但要先講一個預期：**傳統 OCR 前處理（二值化那類）對現代深度模型多半是反效果。**
# PP-OCR 訓練用的是自然影像，把圖二值化等於把它推出訓練分布。這裡仍然把
# 二值化放進來當對照組——如果它真的變差，那個負面結果本身就值得記錄，
# 免得日後有人再試一次。
#
# 每個變體只動一件事，效果才歸因得了。輸出照 run_baseline 的格式存到
# out/<preset>__<變體>/，score_ocr.py 可以直接讀。
#
# 用法：
#   python sweep_preprocess.py                      # 全部變體、58 案
#   python sweep_preprocess.py --only clahe unsharp
#   python sweep_preprocess.py --cases c58 c38 c02  # 只跑特定案例（快速試）
import argparse
import json
import os
import sys
import time

import cv2
import numpy as np

sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import run_baseline as RB  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))


# ─── 前處理 ──────────────────────────────────────────────────────────────────
# 每個函式吃 BGR ndarray、回 BGR ndarray。
def pp_none(img):
    return img


def pp_clahe(img):
    """對比受限的自適應直方圖等化，只作用在亮度通道。

    針對的是 c58 那種深藍底白字——全域對比很低，但局部有結構。
    在 LAB 的 L 通道做，避免直接對 BGR 做造成色偏。
    """
    lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB)
    l, a, b = cv2.split(lab)
    l = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(l)
    return cv2.cvtColor(cv2.merge([l, a, b]), cv2.COLOR_LAB2BGR)


def pp_clahe_strong(img):
    lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB)
    l, a, b = cv2.split(lab)
    l = cv2.createCLAHE(clipLimit=4.0, tileGridSize=(16, 16)).apply(l)
    return cv2.cvtColor(cv2.merge([l, a, b]), cv2.COLOR_LAB2BGR)


def pp_unsharp(img):
    """反遮罩銳化。針對縮到 2048 之後糊掉的小字。"""
    blur = cv2.GaussianBlur(img, (0, 0), 3)
    return cv2.addWeighted(img, 1.5, blur, -0.5, 0)


def pp_clahe_unsharp(img):
    return pp_unsharp(pp_clahe(img))


def pp_denoise(img):
    """雙邊濾波：抑制雜訊但保留邊緣。手機高 ISO 的貨架照可能受益。"""
    return cv2.bilateralFilter(img, 5, 50, 50)


def pp_gamma(img):
    """自動 gamma：把整體亮度拉到中性。偏暗的包裝（深藍杯）理論上受益。"""
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    mean = float(np.mean(g)) / 255.0
    if mean <= 0.01 or mean >= 0.99:
        return img
    gamma = np.log(0.5) / np.log(mean)
    gamma = float(np.clip(gamma, 0.5, 2.0))
    table = ((np.arange(256) / 255.0) ** (1.0 / gamma) * 255).astype(np.uint8)
    return cv2.LUT(img, table)


def pp_glare(img):
    """反光抑制：找出過曝的鏡面反光區，用周圍內容補起來。

    針對收縮膜的白色亮條紋——實測那些條紋會被 DB 當成文字連通區域，
    把好幾列黏成一條（c58）。
    """
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    _, mask = cv2.threshold(g, 245, 255, cv2.THRESH_BINARY)
    if cv2.countNonZero(mask) == 0:
        return img
    mask = cv2.dilate(mask, np.ones((5, 5), np.uint8), iterations=1)
    return cv2.inpaint(img, mask, 3, cv2.INPAINT_TELEA)


def pp_gray(img):
    """灰階（仍回三通道）。純粹當對照：色彩資訊對這個任務有沒有用。"""
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    return cv2.cvtColor(g, cv2.COLOR_GRAY2BGR)


def pp_binarize(img):
    """自適應二值化——**預期會變差**，放進來當負面對照。

    這是傳統 OCR 的標準前處理，但 PP-OCR 訓練用的是自然影像。
    把它列出來是為了留下記錄，免得日後有人再試一次。
    """
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    b = cv2.adaptiveThreshold(g, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                              cv2.THRESH_BINARY, 31, 10)
    return cv2.cvtColor(b, cv2.COLOR_GRAY2BGR)


VARIANTS = {
    'base': pp_none,
    'clahe': pp_clahe,
    'clahe_strong': pp_clahe_strong,
    'unsharp': pp_unsharp,
    'clahe_unsharp': pp_clahe_unsharp,
    'denoise': pp_denoise,
    'gamma': pp_gamma,
    'glare': pp_glare,
    'gray': pp_gray,
    'binarize': pp_binarize,
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--preset', default='v6_best')
    ap.add_argument('--only', nargs='*', default=None)
    ap.add_argument('--cases', nargs='*', default=None)
    ap.add_argument('--device', default='gpu')
    a = ap.parse_args()

    base = RB.PRESETS[a.preset]
    cases = RB.load_cases(a.cases, 0)
    names = [n for n in VARIANTS if not a.only or n in a.only]
    print(f'preset={a.preset}｜案例 {len(cases)}｜變體 {len(names)}\n')

    from paddleocr import PaddleOCR
    ocr = PaddleOCR(device=a.device, **base['init'])

    for name in names:
        fn_pp = VARIANTS[name]
        outdir = os.path.join(HERE, 'out', f'pp__{name}')
        os.makedirs(outdir, exist_ok=True)
        t0, nb = time.time(), 0
        for case in cases:
            rec = {'case_id': case['case_id'], 'preset': f'pp__{name}',
                   'set_version': case.get('set_version'),
                   'category': case.get('category'), 'images': []}
            for rel in case['images']:
                p = os.path.join(RB.EVAL_ROOT, rel)
                if not os.path.exists(p):
                    rec['images'].append({'path': rel, 'error': 'missing'})
                    continue
                # cv2.imread 讀不了中文路徑，用 imdecode 繞過
                img = cv2.imdecode(np.fromfile(p, dtype=np.uint8), cv2.IMREAD_COLOR)
                if img is None:
                    rec['images'].append({'path': rel, 'error': 'decode failed'})
                    continue
                t = time.time()
                try:
                    results = ocr.predict(fn_pp(img), **base['predict'])
                except Exception as e:
                    rec['images'].append({'path': rel, 'error': repr(e)})
                    continue
                lines = []
                for r in results:
                    lines.extend(RB.extract_lines(r))
                nb += len(lines)
                rec['images'].append({'path': rel, 'elapsed_s': round(time.time() - t, 2),
                                      'n_lines': len(lines), 'lines': lines})
            with open(os.path.join(outdir, f'{case["case_id"]}.json'), 'w',
                      encoding='utf-8') as f:
                json.dump(rec, f, ensure_ascii=False, indent=1)
        print(f'  {name:<16}{nb:>5} 框  {time.time() - t0:>5.0f}s')

    print('\n評分：python sweep_preprocess.py --score  或 score_ocr.py --preset=pp__<名稱>')


if __name__ == '__main__':
    main()
