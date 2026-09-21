#!/usr/bin/env python3
# 第二遍：把**成分區**裁出來、放大、單獨再讀一次。
#
# `crop_reread.py` 已經對營養表證明過這招（58 案漏 61 → 42，零訓練），
# 但從來沒用在成分區上。而證據正好指向它：
#
#   `gap_vs_vision.py` 以 Vision 當標尺量測，PP-OCR 落後的成分裡
#   **det 沒框到是 0 項**（門檻 0.05–0.7 皆同），19 項全部是 rec 認錯字。
#   ——不是沒看到，是沒看清。那正是「裁出來放大重讀」要解的問題。
#
# 區域用 `region_crop.py` 找（成分區 57/57 都找得到），不用另寫一套關鍵詞。
#
# **輸出是「基準 ∪ 重讀」的聯集**，與 nutrition_pipeline.py 同樣的理由：
# 兩遍互有勝負，取聯集而非二選一。下游的抽取器本來就會過濾雜訊。
#
# 驗收不看 F1，看**筆數**：`miss_ocr_check.py` 會問「那 18 個指名的、
# 因 OCR 而漏掉的添加物，字有沒有出現在文字裡了」。
# 添加物層 F1 在 57 案上分辨不出 8 點以下的差異（見 boot_compare.py），
# 用它判斷這種改動只會白做。
#
# 用法：
#   python crop_reread_ing.py
#   python crop_reread_ing.py --cases c11 c23 --dump out/crops_ing
import argparse
import json
import os
import sys
import time

import cv2
import numpy as np

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import region_crop as RC   # noqa: E402
import run_baseline as RB  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "out", "v6_hires__boxth0.4")
OUT = os.path.join(HERE, "out", "ing_crop")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", nargs="*", default=None)
    ap.add_argument("--device", default="gpu")
    ap.add_argument("--side", type=int, default=2048)
    ap.add_argument("--max-scale", type=float, default=2.5)
    ap.add_argument("--min-scale", type=float, default=1.15)
    ap.add_argument("--dump", default=None)
    ap.add_argument("--src", default=os.path.basename(SRC),
                    help="out/ 底下的整頁 PP-OCR 輸出目錄")
    ap.add_argument("--out", default=os.path.basename(OUT),
                    help="out/ 底下的輸出目錄")
    ap.add_argument("--crop-only", action="store_true",
                    help="只保留裁切重讀文字；用於隔離是否裁切的消融")
    a = ap.parse_args()

    src_dir = os.path.join(HERE, "out", a.src)
    out_dir = os.path.join(HERE, "out", a.out)
    files = sorted(f for f in os.listdir(src_dir) if f.endswith(".json"))
    if a.cases:
        files = [f for f in files if any(f.startswith(c) for c in a.cases)]
    os.makedirs(out_dir, exist_ok=True)
    if a.dump:
        os.makedirs(a.dump, exist_ok=True)

    from paddleocr import PaddleOCR
    base = RB.PRESETS["v6_best"]
    ocr = PaddleOCR(device=a.device, **base["init"])

    n_crop = n_skip = 0
    t0 = time.time()
    for n, f in enumerate(files, 1):
        d = json.load(open(os.path.join(src_dir, f), encoding="utf-8"))
        cid = d["case_id"]
        rec = {"case_id": cid, "preset": "ing_crop",
               "set_version": d.get("set_version"),
               "category": d.get("category"), "images": []}
        for im in d["images"]:
            rel = im["path"]
            lines = im.get("lines") or []
            out_lines = [] if a.crop_only else list(lines)
            p = os.path.join(RB.EVAL_ROOT, *rel.split("/"))
            if not os.path.exists(p) or not lines:
                rec["images"].append({**im, "lines": out_lines,
                                      "n_lines": len(out_lines)})
                continue
            box = RC.find_regions(lines)["ingredients"]
            img = cv2.imdecode(np.fromfile(p, dtype=np.uint8),
                               cv2.IMREAD_COLOR)
            if img is None or box is None:
                n_skip += 1
                rec["images"].append({**im, "lines": out_lines,
                                      "n_lines": len(out_lines)})
                continue
            h, w = img.shape[:2]
            mx = (box[2] - box[0]) * 0.06 + 15
            my = (box[3] - box[1]) * 0.06 + 15
            x0 = max(0, int(box[0] - mx)); y0 = max(0, int(box[1] - my))
            x1 = min(w, int(box[2] + mx)); y1 = min(h, int(box[3] + my))
            crop = img[y0:y1, x0:x1]
            if crop.size == 0:
                n_skip += 1
                rec["images"].append({**im, "lines": out_lines,
                                      "n_lines": len(out_lines)})
                continue
            # 放不了大就沒有做第二遍的理由：字高不會變，只是重跑一次。
            scale = min(a.max_scale, a.side / max(crop.shape[:2]))
            if scale < a.min_scale:
                n_skip += 1
                rec["images"].append({**im, "lines": out_lines,
                                      "n_lines": len(out_lines)})
                continue
            crop = cv2.resize(crop, None, fx=scale, fy=scale,
                              interpolation=cv2.INTER_CUBIC)
            if a.dump:
                cv2.imencode(".jpg", crop)[1].tofile(
                    os.path.join(a.dump, "%s_%s" % (cid,
                                                    os.path.basename(rel))))
            try:
                results = ocr.predict(crop, **base["predict"])
            except Exception as e:
                print("   %s 裁切後推論失敗：%r" % (cid, e))
                rec["images"].append({**im, "lines": out_lines,
                                      "n_lines": len(out_lines)})
                continue
            add = []
            for r in results:
                add.extend(RB.extract_lines(r))
            # 重讀的框座標是裁切後的，換不回原圖也沒關係——下游只用文字。
            # 但標記來源，方便事後追哪一行是第二遍撿到的。
            for l in add:
                l["src"] = "crop"
            out_lines.extend(add)
            n_crop += 1
            print("[%d/%d] %-28s 裁 %dx%d ×%.2f  基準 %d 行 + 重讀 %d 行"
                  % (n, len(files), cid, x1 - x0, y1 - y0, scale,
                     len(lines), len(add)))
            rec["images"].append({**im, "lines": out_lines,
                                  "n_lines": len(out_lines)})
        rec["preset"] = a.out
        rec["crop_only"] = a.crop_only
        json.dump(rec, open(os.path.join(out_dir, f), "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)

    print("\n重讀 %d 張、跳過 %d 張，共 %.0fs -> out/%s/"
          % (n_crop, n_skip, time.time() - t0, a.out))
    print("驗收：python score_items.py %s --boxes %s" % (a.out, a.src))


if __name__ == "__main__":
    main()
