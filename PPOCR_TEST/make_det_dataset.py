#!/usr/bin/env python3
# 把 PPOCRLabel 的 Label.txt 整理成 PaddleOCR det 訓練用的資料集，並做品質檢查。
#
# PPOCRLabel 存出來的格式（`圖片路徑\t<JSON 陣列>`）其實已經就是 det 訓練格式，
# 所以這支的重點不是「轉換」，是**在訓練前把標註問題抓出來**。
# 標註是人工的、成本最高的一環，等訓練跑完才發現框歪了或漏標，代價太大。
#
# 檢查項目與理由：
#   - 退化框（面積過小、邊長為 0）  → 訓練時會產生 NaN 或無意義的監督訊號
#   - 超出影像邊界的座標            → 多半是縮放過的圖被套上原圖的框
#   - 每張圖的框數                  → 遠低於同類圖的平均，通常代表漏標了整片區域。
#                                     **漏標比標錯更糟**：沒框到的文字會被當成背景，
#                                     等於教模型「這裡沒有字」
#   - `###` 忽略框的比例            → 太高代表大部分區域沒真的標
#   - 與評估集重疊                  → 紅線。訓練圖若混進 v2.2/v3.0 的照片，
#                                     之後所有前後對照都失去意義
#
# 用法：
#   python make_det_dataset.py check  ../FoodScanData/train_det
#   python make_det_dataset.py build  ../FoodScanData/train_det --val-frac=0.15
import argparse
import hashlib
import json
import os
import random
import sys
from collections import Counter

sys.stdout.reconfigure(encoding='utf-8')

HERE = os.path.dirname(os.path.abspath(__file__))
EVAL_IMAGES = os.path.abspath(
    os.path.join(HERE, '..', '測試', '量化測試', 'images'))


def load_labels(root):
    """收集 root 底下所有 Label.txt。PPOCRLabel 一個資料夾存一份。"""
    entries = []
    for dirpath, _, files in os.walk(root):
        if 'Label.txt' not in files:
            continue
        lp = os.path.join(dirpath, 'Label.txt')
        for line in open(lp, encoding='utf-8'):
            line = line.rstrip('\n')
            if '\t' not in line:
                continue
            key, js = line.split('\t', 1)
            try:
                shapes = json.loads(js)
            except json.JSONDecodeError as e:
                print(f'[跳過] {lp} 的 {key}：JSON 解析失敗 {e}')
                continue
            # key 是相對於 Label.txt 所在目錄的父層
            img = os.path.normpath(os.path.join(os.path.dirname(dirpath), key))
            entries.append((img, shapes, lp))
    return entries


def eval_image_hashes():
    """評估集影像的內容雜湊，用來擋住「訓練圖其實是評估圖」。

    比對內容而非檔名——同一張照片改個名字複製過來，檔名比對抓不到。
    """
    out = {}
    if not os.path.isdir(EVAL_IMAGES):
        return out
    for dp, _, fs in os.walk(EVAL_IMAGES):
        for fn in fs:
            if fn.lower().endswith(('.jpg', '.jpeg', '.png', '.webp')):
                p = os.path.join(dp, fn)
                with open(p, 'rb') as f:
                    out[hashlib.md5(f.read()).hexdigest()] = os.path.relpath(p, EVAL_IMAGES)
    return out


def check(root):
    entries = load_labels(root)
    if not entries:
        sys.exit(f'{root} 底下找不到任何 Label.txt。'
                 f'\n用 PPOCRLabel 標註並按「檔案 → 匯出標記結果」後再跑。')
    print(f'找到 {len(entries)} 張已標註影像\n')

    ev = eval_image_hashes()
    problems, box_counts, ignore_ratio = [], [], []
    total_boxes = tot_ignore = 0

    for img, shapes, lp in entries:
        name = os.path.basename(img)
        if not os.path.exists(img):
            problems.append(f'[缺圖] {img}（Label.txt: {lp}）')
            continue
        if ev:
            with open(img, 'rb') as f:
                h = hashlib.md5(f.read()).hexdigest()
            if h in ev:
                problems.append(f'[紅線] {name} 與評估集影像相同：{ev[h]}')

        try:
            from PIL import Image
            W, H = Image.open(img).size
        except Exception as e:
            problems.append(f'[讀不到] {name}：{e}')
            continue

        n_ig = 0
        for s in shapes:
            pts = s.get('points') or []
            if len(pts) < 4:
                problems.append(f'[框點數不足] {name}：{len(pts)} 點')
                continue
            xs = [p[0] for p in pts]
            ys = [p[1] for p in pts]
            w, h = max(xs) - min(xs), max(ys) - min(ys)
            if w < 2 or h < 2:
                problems.append(f'[退化框] {name}：{w:.0f}x{h:.0f}')
            if min(xs) < -2 or min(ys) < -2 or max(xs) > W + 2 or max(ys) > H + 2:
                problems.append(f'[超出邊界] {name}：影像 {W}x{H}，框到 '
                                f'({min(xs):.0f},{min(ys):.0f})-({max(xs):.0f},{max(ys):.0f})')
            if (s.get('transcription') or '') in ('###', '*'):
                n_ig += 1
        total_boxes += len(shapes)
        tot_ignore += n_ig
        box_counts.append((len(shapes), name))
        ignore_ratio.append(n_ig / max(1, len(shapes)))

    box_counts.sort()
    nums = [n for n, _ in box_counts]
    if nums:
        med = nums[len(nums) // 2]
        print(f'框數：共 {total_boxes}｜每張 中位數 {med}、最少 {nums[0]}、最多 {nums[-1]}')
        print(f'忽略框(###)：{tot_ignore}（{tot_ignore / max(1, total_boxes) * 100:.0f}%）')
        # 框數異常少 = 很可能漏標整片區域
        thin = [(n, f) for n, f in box_counts if n < max(3, med * 0.35)]
        if thin:
            print(f'\n[注意] 框數明顯偏少的 {len(thin)} 張（中位數 {med}），'
                  f'確認是不是漏標整片：')
            for n, f in thin[:10]:
                print(f'   {n:>3} 框  {f}')

    print(f'\n問題 {len(problems)} 項')
    for m in problems[:30]:
        print('  ', m)
    if len(problems) > 30:
        print(f'   …另有 {len(problems) - 30} 項')
    return 1 if any(m.startswith('[紅線]') for m in problems) else 0


def build(root, val_frac, seed):
    if check(root):
        sys.exit('\n有紅線問題（訓練圖與評估集重疊），未產生資料集。')
    entries = load_labels(root)
    root = os.path.abspath(root)
    rows = []
    for img, shapes, _ in entries:
        if not os.path.exists(img):
            continue
        rel = os.path.relpath(img, root).replace('\\', '/')
        rows.append(f'{rel}\t{json.dumps(shapes, ensure_ascii=False)}')

    random.Random(seed).shuffle(rows)
    nval = max(1, int(len(rows) * val_frac))
    for name, part in (('val_list.txt', rows[:nval]), ('train_list.txt', rows[nval:])):
        with open(os.path.join(root, name), 'w', encoding='utf-8', newline='\n') as f:
            f.write('\n'.join(part) + '\n')
    print(f'\n已寫入 train_list.txt {len(rows) - nval} 筆 / val_list.txt {nval} 筆')
    print(f'\n訓練 config 片段：')
    print(f'  Train.dataset.data_dir: {root}')
    print(f'  Train.dataset.label_file_list: [{root}/train_list.txt]')


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest='cmd', required=True)
    c = sub.add_parser('check')
    c.add_argument('root')
    b = sub.add_parser('build')
    b.add_argument('root')
    b.add_argument('--val-frac', type=float, default=0.15)
    b.add_argument('--seed', type=int, default=20260815)
    a = ap.parse_args()
    if a.cmd == 'check':
        sys.exit(check(a.root))
    else:
        build(a.root, a.val_frac, a.seed)


if __name__ == '__main__':
    main()
