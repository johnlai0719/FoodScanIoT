#!/usr/bin/env python3
# 影像的客觀品質量值：銳利度與高光溢出比例。
#
# 為何要有這支：`difficulty` 的模糊標註是人看出來的，而人眼的「糊」與機器判讀
# 的「糊」不是同一件事——2026-08-11 實測，人工標為模糊者的銳利度落在未標註者
# 的範圍之內，最糊的一張反而沒被標到。更關鍵的是，人眼標註**無法寫進 App**：
# 手機端要判斷「這張要不要請使用者重拍」，靠的必然是某個算得出來的數值。
# 本工具量的就是那個數值，故其閾值可直接轉為 App 的判準。
#
# **不取代人工標註，與之並存**：兩者量的東西不同——
#   人工 difficulty  標示區域看起來如何（規則是「只算影響到標示區域的問題」）
#   本工具的量值      整張影像的高頻成分，未區分標示區與背景
# 背景銳利而標示區失焦的照片，兩者會給出相反的結果，這不是誰錯，是量的不是
# 同一件事。兩者的不一致本身即為資訊（使用者覺得糊、但模型讀得出來的落差，
# 正是 App 提示重拍時要處理的問題）。故本工具寫入獨立的 sidecar 檔，
# 不動 cases.json 的 difficulty 欄位。
#
# 量值說明：
#   sharpness  拉普拉斯變異數。數值越低越糊。**比較前必須統一解析度**，
#              否則高解析度照片天生數值較高，比的是相機不是清晰度，
#              故一律縮至 NORM_WIDTH 再算。
#   clipped    亮度 >= 250 的像素比例。原意是偵測反光，**但目前不可用於此**：
#              食品標示底色多為白色，乾淨的白底標示與反光斑在此指標上分不開。
#              先記錄，待日後能框出標示區域再談。
#
# 用法：
#   python image_metrics.py             # 計算並寫入 image_metrics.json
#   python image_metrics.py --report    # 併 results/per_case.csv 看量值與分數的關係
import csv
import json
import os
import statistics as st
import sys

import numpy as np
from PIL import Image

import casetool

# Windows 主控台預設 cp950，案例名稱裡有它編不出來的字（「塩」「菓」…）。
# 沒有這兩行的後果不是印出亂碼，而是**整批任務在中途拋 UnicodeEncodeError 死掉**
# ——2026-09-07 run_eval 炸在第 10 案的「塩」、intake 炸在第 11 案的「菓」，
# 後者還留下半套用狀態（照片已搬、cases.json 沒存）。
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.stderr.reconfigure(encoding='utf-8', errors='replace')

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, 'image_metrics.json')
PER_CASE = os.path.join(HERE, 'results', 'per_case.csv')
NORM_WIDTH = 1200


def measure(path):
    """回傳 (sharpness, clipped)。統一縮放後計算，使跨照片可比。"""
    with Image.open(path) as im:
        im = im.convert('L')
        if im.width > NORM_WIDTH:
            im = im.resize((NORM_WIDTH, round(im.height * NORM_WIDTH / im.width)),
                           Image.LANCZOS)
        a = np.asarray(im, dtype=np.float64)
    # 四鄰域拉普拉斯；取變異數作為銳利度
    lap = (a[1:-1, 1:-1] * -4 + a[:-2, 1:-1] + a[2:, 1:-1]
           + a[1:-1, :-2] + a[1:-1, 2:])
    return float(lap.var()), float((a >= 250).mean())


def compute():
    cases = json.load(open(casetool.CASES, encoding='utf-8'))['cases']
    out = {}
    for c in cases:
        per_img = {}
        for rel in c['images']:
            p = os.path.join(HERE, rel)
            if not os.path.exists(p):
                print(f"  [略過] 找不到 {rel}")
                continue
            s, cl = measure(p)
            per_img[os.path.basename(rel)] = {'sharpness': round(s, 1),
                                              'clipped': round(cl, 4)}
        if not per_img:
            continue
        # 案例層級取最大值：所有照片都會送給模型，故以最清楚的那張代表
        # 「這一案有沒有可讀的影像」。取平均會讓一張補拍的糊照拉低整案。
        out[c['case_id']] = {
            'sharpness_max': max(v['sharpness'] for v in per_img.values()),
            'clipped_max': max(v['clipped'] for v in per_img.values()),
            'n_images': len(per_img),
            'images': per_img,
        }
    with open(OUT, 'w', encoding='utf-8') as f:
        json.dump({'_說明': '客觀影像量值，與人工 difficulty 並存而非取代；'
                            f'銳利度於統一縮至 {NORM_WIDTH}px 後計算，'
                            'clipped 目前不可用於偵測反光（見檔頭）。',
                   'norm_width': NORM_WIDTH, 'cases': out}, f,
                  ensure_ascii=False, indent=2)
    vals = [v['sharpness_max'] for v in out.values()]
    print(f"已寫入 {os.path.basename(OUT)}（{len(out)} 案）")
    print(f"  銳利度 最低 {min(vals):.0f} / 中位 {st.median(vals):.0f} / 最高 {max(vals):.0f}")
    return out


def report():
    if not os.path.exists(OUT):
        sys.exit(f"請先執行 python image_metrics.py 產生 {os.path.basename(OUT)}")
    data = json.load(open(OUT, encoding='utf-8'))['cases']
    if not os.path.exists(PER_CASE):
        sys.exit(f"找不到 {PER_CASE}，請先跑過 score_eval.py")
    rows = list(csv.DictReader(open(PER_CASE, encoding='utf-8')))
    key = list(rows[0].keys())[0]          # 首欄可能帶 BOM
    f1 = {r[key]: float(r['value']) for r in rows
          if r['field'] == 'ingredients_flat' and r['metric'] == 'f1'}
    cases = {c['case_id']: c for c in json.load(open(casetool.CASES, encoding='utf-8'))['cases']}

    pairs = [(cid, data[cid]['sharpness_max'], f1[cid],
              'blurry' in (cases[cid].get('difficulty') or []))
             for cid in data if cid in f1 and cid in cases]
    if len(pairs) < 3:
        sys.exit("可比對的案例太少。")
    pairs.sort(key=lambda x: x[1])

    print(f"{'案例':24}{'銳利度':>9}{'成分F1':>8}   人工標模糊")
    for cid, s, v, b in pairs[:12]:
        print(f"{cid.split('_', 1)[-1][:22]:24}{s:9.0f}{v:8.2f}   {'✓' if b else ''}")
    print(f"   …（依銳利度由低至高，共 {len(pairs)} 案，只列最糊的 12 案）\n")

    sh = [p[1] for p in pairs]
    sc = [p[2] for p in pairs]
    r = float(np.corrcoef(sh, sc)[0, 1])
    med = st.median(sh)
    lo = [p[2] for p in pairs if p[1] <= med]
    hi = [p[2] for p in pairs if p[1] > med]
    print(f"銳利度與成分 F1 的相關係數 = {r:+.3f}")
    print(f"  較糊的一半 n={len(lo)} F1 平均 {st.mean(lo):.3f}"
          f" ／ 較清楚的一半 n={len(hi)} F1 平均 {st.mean(hi):.3f}")

    tagged = [p[1] for p in pairs if p[3]]
    untag = [p[1] for p in pairs if not p[3]]
    if tagged:
        print(f"\n人工標「模糊」者的銳利度：{[round(x) for x in tagged]}")
        print(f"未標註者的範圍：{min(untag):.0f} – {max(untag):.0f}")
        if min(untag) < min(tagged):
            print("  → 有未標註的照片比標註者更糊。人眼的「糊」與本量值並非同一件事，"
                  "屬預期（見檔頭），兩者並存即為此。")
    print("\n判讀提醒：相關係數接近 0 不代表模糊無害，只代表**現有照片的模糊範圍內**"
          "看不到訊號。要找出模型讀不出來的閾值，請用 blur_sweep.py 合成更糊的輸入。")


def main():
    args = sys.argv[1:]
    if not args:
        compute()
        print("\n接著看量值與辨識分數的關係：python image_metrics.py --report")
    elif args == ['--report']:
        report()
    else:
        sys.exit("用法：image_metrics.py [--report]")


if __name__ == '__main__':
    main()
