#!/usr/bin/env python3
# 合成模糊掃描：對同一批照片施加遞增的模糊，找出模型讀不出來的閾值。
#
# 為何要有這支：現有 47 案的銳利度與成分分數相關係數僅 −0.151，看不到模糊
# 傷害辨識的訊號——但這不表示模糊無害，只表示**手機正常拍攝的照片都還在可讀
# 範圍內**，那條線在現有樣本之外。要知道 App 的重拍提示該設在哪，就必須看到
# 模型真正讀不出來的那一端；而那需要比現有照片更糊的輸入。
#
# 合成而非重拍的理由：同一件商品、同一份人工正解完全適用，**不必重拍也不必
# 重寫正解**，只花 API 費用。且模糊程度精確可控，重拍做不到這件事。
#
# 這是「找閾值」的實驗，不是「證明模糊有害」的實驗——合成模糊與手震、失焦的
# 光學性質不完全相同，故結論限於「影像高頻成分低到何種程度時模型開始失敗」，
# 不宜逕稱等同於真實的手震照片。
#
# 半徑的定義：以 1280px 寬為基準，實際套用時依影像寬度等比放大。故同一個
# --radii 值在原圖與壓縮圖上造成的視覺模糊程度相當，跨來源可比。
#
# 預設半徑 0.25/0.5/0.75/1/1.5：實測這個範圍才有判讀價值——現有照片的銳利度
# 中位為 1284，半徑 0.5 約降至四成、半徑 1 約降至一成五、半徑 2 以上一律低於 15
# （幾乎全白），再往上測只是浪費 API 費用。
#
# 用法：
#   python blur_sweep.py                              # 全部案例，預設五級
#   python blur_sweep.py --radii=0.5,1                # 指定強度
#   python blur_sweep.py c01_柳橙綠茶 c18_蝦味先原味      # 只做幾案（省 API 費用）
#   python blur_sweep.py --src=images_1280q80/images   # 改對壓縮後的圖做（較貼近實際送出）
#
# 產生後逐級量測：
#   EVAL_IMAGE_ROOT=images_blur2 python run_eval.py --tag=blur2
#   python score_eval.py
import json
import os
import sys

from PIL import Image, ImageFilter

import casetool
from image_metrics import measure

# Windows 主控台預設 cp950，案例名稱裡有它編不出來的字（「塩」「菓」…）。
# 沒有這兩行的後果不是印出亂碼，而是**整批任務在中途拋 UnicodeEncodeError 死掉**
# ——2026-09-07 run_eval 炸在第 10 案的「塩」、intake 炸在第 11 案的「菓」，
# 後者還留下半套用狀態（照片已搬、cases.json 沒存）。
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.stderr.reconfigure(encoding='utf-8', errors='replace')

HERE = os.path.dirname(os.path.abspath(__file__))
REF_WIDTH = 1280


def build(src_root, out_dir, radius, rels):
    rows = []
    for rel in rels:
        sp = os.path.join(HERE, src_root, rel[len('images/'):]) \
            if rel.startswith('images/') else os.path.join(HERE, src_root, rel)
        if not os.path.exists(sp):
            print(f"  [略過] 找不到 {sp}")
            continue
        # 輸出必須是 <out>/images/<rel>：run_eval 的路徑是 EVAL_IMAGE_ROOT 加上
        # cases.json 的相對路徑，而後者本來就含 images/ 這一層。少一層會使案例
        # 全部被略過而工具仍正常結束（compress_images.py 檔頭記過同一個坑）。
        dp = os.path.join(out_dir, rel)
        os.makedirs(os.path.dirname(dp), exist_ok=True)
        with Image.open(sp) as im:
            im = im.convert('RGB')
            sigma = radius * im.width / REF_WIDTH      # 依寬度等比，跨來源可比
            im.filter(ImageFilter.GaussianBlur(sigma)).save(dp, 'JPEG', quality=95)
        s, _ = measure(dp)
        rows.append({'file': rel, 'sigma_px': round(sigma, 2), 'sharpness': round(s, 1)})
    return rows


def main():
    radii = [0.25, 0.5, 0.75, 1, 1.5]
    src_root = 'images'
    want = []
    for a in sys.argv[1:]:
        if a.startswith('--radii='):
            radii = [float(x) for x in a.split('=', 1)[1].split(',') if x]
        elif a.startswith('--src='):
            src_root = a.split('=', 1)[1]
        elif a.startswith('--'):
            sys.exit(f"看不懂的參數：{a}")
        else:
            want.append(a)

    cases = json.load(open(casetool.CASES, encoding='utf-8'))['cases']
    if want:
        by = {c['case_id']: c for c in cases}
        missing = [w for w in want if w not in by]
        if missing:
            sys.exit(f"cases.json 沒有：{missing}")
        cases = [by[w] for w in want]
    rels = [r for c in cases for r in c['images']]
    if not rels:
        sys.exit("沒有可處理的照片。")

    print(f"來源 {src_root}／{len(cases)} 案、{len(rels)} 張，半徑 {radii}")
    base = {}
    for c in cases:                                     # 先量原始銳利度作為對照
        for rel in c['images']:
            p = os.path.join(HERE, rel)
            if os.path.exists(p):
                base[rel] = measure(p)[0]
    if base:
        vals = sorted(base.values())
        print(f"  原始銳利度 中位 {vals[len(vals)//2]:.0f}"
              f"（{vals[0]:.0f} – {vals[-1]:.0f}）")

    for r in radii:
        name = f"images_blur{r:g}"
        out_dir = os.path.join(HERE, name)
        rows = build(src_root, out_dir, r, rels)
        if not rows:
            continue
        sh = sorted(x['sharpness'] for x in rows)
        with open(os.path.join(out_dir, '_blur_report.json'), 'w', encoding='utf-8') as f:
            json.dump({'source': src_root, 'radius_at_1280px': r,
                       'ref_width': REF_WIDTH, 'n_files': len(rows),
                       'sharpness_median': sh[len(sh) // 2], 'files': rows}, f,
                      ensure_ascii=False, indent=2)
        print(f"  {name}/  {len(rows)} 張  銳利度中位 {sh[len(sh)//2]:.0f}"
              f"（{sh[0]:.0f} – {sh[-1]:.0f}）")

    n_calls = len(cases) * len(radii)
    print(f"\n逐級量測（共 {n_calls} 次 API 呼叫，{len(cases)} 案 × {len(radii)} 級）：")
    for r in radii:
        print(f"  EVAL_IMAGE_ROOT=images_blur{r:g} python run_eval.py --tag=blur{r:g}")
    print("  python score_eval.py")
    print("\n判讀：把各級的成分 F1 對銳利度中位數畫出來，分數開始明顯下滑處"
          "即為 App 重拍提示的閾值候選。")


if __name__ == '__main__':
    main()
