#!/usr/bin/env python3
# 用「正確設定」的 PP-OCRv5 預先產生 PPOCRLabel 的 Label.txt，讓人只做修正。
#
# 為什麼不直接用 PPOCRLabel 內建的自動標註：它的設定寫死且不理想
# （PPOCRLabel.py 的 params）：
#   - `text_det_limit_side_len` 整份原始碼沒有出現 → 沿用 PaddleOCR 預設 960/max，
#     4284x5712 的照片進偵測前會被縮到 720x960
#   - `use_textline_orientation: False` → 文字行方向分類關閉，直排文字受影響
#
# 實測那個 960 的差別（out/v5_default vs out/v5_hires，同一批 67 張圖）：
#   總框數只差 +49，但個別案例是災難級的——
#   c39_米香 0→50（960 時整張找不到字）、c29_可口可樂 7→98、c36_滿漢大餐 1→51。
# 也就是說「自動標註很爛」多半不是模型不行，是設定把圖縮壞了。
#
# 但要誠實：曲面那類（c58_拉麵道 99→102）解析度拉高幾乎沒幫助，
# 那是模型的真實限制，只能靠人工補框——而那正是要標註的價值所在。
#
# 產生後在 PPOCRLabel「打开目录」選同一個資料夾，它會自動載入這份 Label.txt。
# **不要再按 PPOCRLabel 的「自动标注」**，那會用它自己的爛設定覆蓋掉。
#
# 用法：
#   python prelabel_det.py ../FoodScanData/train_det/batch1
#   python prelabel_det.py ../FoodScanData/train_det/batch1 --limit-side-len=2560
import argparse
import json
import os
import sys
import time

sys.stdout.reconfigure(encoding='utf-8')

EXTS = ('.jpg', '.jpeg', '.png', '.bmp', '.webp')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('folder')
    ap.add_argument('--limit-side-len', type=int, default=2048)
    ap.add_argument('--device', default='gpu')
    ap.add_argument('--overwrite', action='store_true',
                    help='已有 Label.txt 時覆蓋（預設拒絕，避免蓋掉人工修正）')
    ap.add_argument('--only-new', action='store_true',
                    help='只預標 Label.txt 裡還沒有的影像，既有的框原封不動保留。'
                         '分批補照片時用這個，不要用 --overwrite')
    a = ap.parse_args()

    folder = os.path.abspath(a.folder)
    if not os.path.isdir(folder):
        sys.exit(f'找不到資料夾：{folder}')
    imgs = sorted(f for f in os.listdir(folder) if f.lower().endswith(EXTS))
    if not imgs:
        sys.exit(f'{folder} 裡沒有影像')

    # 預標寫進 Cache.cach，不是 Label.txt。這是 PPOCRLabel 的分工，踩過一次：
    #   PPOCRLabel.py:3638 savePPlabel() —— Label.txt 只寫 fileStatedict 裡的圖，
    #   也就是**人工按過「确认」的**，其餘整列靜默丟棄。預標放 Label.txt 的話，
    #   使用者存檔一次，還沒確認的那些就全沒了。
    #   PPOCRLabel.py:2631-2634 開資料夾時 `PPlabel = dict(Cachelabel, **PPlabel)`，
    #   兩份會合併顯示，且 Label.txt 優先——所以人工改過的永遠蓋得過預標。
    # 附帶好處：make_det_dataset.py 讀 Label.txt，等於自動只拿人工驗過的框去訓練，
    # 不會把 det 自己的錯誤當成正解餵回去。
    lp = os.path.join(folder, 'Label.txt')
    cp = os.path.join(folder, 'Cache.cach')
    parent = os.path.basename(folder)   # key = PPOCRLabel getImglabelidx()：路徑最後兩段
    keep = []                           # Cache.cach 既有的行
    if os.path.exists(cp) or os.path.exists(lp):
        if a.only_new:
            have = set()
            for path, is_cache in ((lp, False), (cp, True)):
                if not os.path.exists(path):
                    continue
                for line in open(path, encoding='utf-8'):
                    line = line.rstrip('\n')
                    if '\t' not in line:
                        continue
                    have.add(os.path.basename(line.split('\t', 1)[0]))
                    if is_cache:
                        keep.append(line)
            imgs = [f for f in imgs if f not in have]
            print(f'--only-new：已有標註/預標 {len(have)} 張，本次預標 {len(imgs)} 張')
            if not imgs:
                sys.exit('沒有新影像需要預標。')
        elif not a.overwrite:
            sys.exit(f'{cp} 或 {lp} 已存在。分批補照片請加 --only-new（保留既有的）；'
                     f'\n真的要整份重來才加 --overwrite（會蓋掉人工修正過的框）')

    print(f'{folder}\n{len(imgs)} 張影像，limit_side_len={a.limit_side_len}')

    from paddleocr import PaddleOCR
    # 用 run_baseline.py 的 v6_best 設定（2026-08-15 參數掃描的最佳組合）。
    # 與 v5 相比：完全命中 587→630、沒讀到 90→65、繁簡 236→31，而且更快。
    ocr = PaddleOCR(
        lang='chinese_cht',
        ocr_version='PP-OCRv6',
        use_doc_orientation_classify=False,
        use_doc_unwarping=False,
        # PPOCRLabel 內建關掉了這個，直排文字（泡麵杯）會受影響
        use_textline_orientation=True,
        device=a.device,
    )

    rows, total, t0 = [], 0, time.time()
    for i, fn in enumerate(imgs, 1):
        path = os.path.join(folder, fn)
        try:
            results = ocr.predict(path,
                                  text_det_limit_side_len=a.limit_side_len,
                                  text_det_limit_type='max',
                                  text_det_box_thresh=0.4)
        except Exception as e:
            print(f'[{i}/{len(imgs)}] {fn}  失敗：{e!r}')
            continue
        shapes = []
        for res in results:
            d = res.json['res'] if hasattr(res, 'json') else res
            texts = d.get('rec_texts', [])
            polys = d.get('rec_polys') or d.get('dt_polys') or []
            for j, t in enumerate(texts):
                if j >= len(polys):
                    continue
                pts = polys[j]
                if not isinstance(pts, list):
                    pts = pts.tolist()
                shapes.append({
                    'transcription': t,
                    'points': [[int(round(x)), int(round(y))] for x, y in pts],
                    'difficult': False,
                })
        total += len(shapes)
        rows.append(f'{parent}/{fn}\t{json.dumps(shapes, ensure_ascii=False)}')
        print(f'[{i}/{len(imgs)}] {fn}  {len(shapes)} 框')

    if os.path.exists(cp):          # 備份再寫，標註資料不容許單向操作
        import shutil
        shutil.copy2(cp, cp + '.bak')
    with open(cp, 'w', encoding='utf-8', newline='\n') as f:
        f.write('\n'.join(keep + rows) + '\n')

    print(f'\n新增 {total} 框（每張平均 {total / max(1, len(rows)):.0f}）'
          f'，{time.time() - t0:.0f}s → {cp}')
    print('Label.txt 未更動——預標屬於未確認狀態，確認過的才會進 Label.txt')
    print('\n接下來：')
    print(f'  1. PPOCRLabel 開這個資料夾（文件 → 打开目录）')
    print(f'  2. **不要按「自动标注」**——會用它的預設設定覆蓋掉這份')
    print(f'  3. 逐張修正，每張按「确认」')
    print(f'  4. 文件 → 导出标记结果')
    print(f"  5. python make_det_dataset.py check {folder}")


if __name__ == '__main__':
    main()
