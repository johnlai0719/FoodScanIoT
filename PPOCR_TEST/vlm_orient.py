#!/usr/bin/env python3
# 從既有的 PP-OCR 框輸出推每張圖要不要轉正。
#
# 為什麼需要這一支：PaddleOCR-VL 是端到端 VLM，沒有 PP-OCR 的 textline
# orientation 那一段，圖側躺時它讀得出來但會掉字（c38 實測：不轉正只讀到
# 營養表，轉正後成分整段都出來了）。而這批圖的 EXIF orientation 全是 1 或
# None——照片是「真的」存成側躺的，不是 EXIF 標記問題，所以只能從內容判斷。
#
# 判法：拿 out/v6_hires__boxth0.4/ 已經跑好的框，看文字框的長寬比中位數。
# 框比自己高 → 整頁側躺。不另外跑模型，因為那批框本來就存在。
#
# 方向（順時針 90 還是 270）分不出來——框的形狀對兩者一樣。所以只輸出
# 「要不要轉」，轉哪一邊交給 run_vlm.py 兩邊都跑、取中文字多的那個。
import json
import os
import statistics
import sys

sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
BOXES = "v6_hires__boxth0.4"


def box_wh(box):
    xs = [p[0] for p in box]
    ys = [p[1] for p in box]
    return max(xs) - min(xs), max(ys) - min(ys)


def image_needs_rotation(lines, min_lines=5):
    """回傳 (要不要轉, 直立框佔比, 納入判斷的框數)。"""
    ratios = []
    for ln in lines:
        b = ln.get("box")
        if not b:
            continue
        w, h = box_wh(b)
        if w < 4 or h < 4:
            continue
        ratios.append(h / w)
    if len(ratios) < min_lines:
        return False, 0.0, len(ratios)
    tall = sum(1 for r in ratios if r > 1.2)
    return statistics.median(ratios) > 1.0, tall / len(ratios), len(ratios)


def scan():
    out = {}
    d = os.path.join(HERE, "out", BOXES)
    for fn in sorted(os.listdir(d)):
        if not fn.endswith(".json"):
            continue
        rec = json.load(open(os.path.join(d, fn), encoding="utf-8"))
        for img in rec.get("images", []):
            need, frac, n = image_needs_rotation(img.get("lines", []))
            out[img["path"]] = {"rotate": need, "tall_frac": round(frac, 2),
                                "n_boxes": n, "case_id": rec["case_id"]}
    return out


if __name__ == "__main__":
    res = scan()
    p = os.path.join(HERE, "out", "_vlm_orient.json")
    json.dump(res, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    rot = [k for k, v in res.items() if v["rotate"]]
    print(f"{len(res)} 張圖，判定要轉正 {len(rot)} 張 -> {p}")
    for k in rot:
        v = res[k]
        print(f"  {v['case_id']:<32} 直立框 {v['tall_frac']:.0%}  ({v['n_boxes']} 框)")
