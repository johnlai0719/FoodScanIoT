#!/usr/bin/env python3
# 用 Google Vision 當**標尺**，把 PP-OCR 的成分漏失歸因到 det 還是 rec。
#
# Vision 在這裡不是要採用的元件，是參照組：它讀到而 PP-OCR 沒讀到的成分，
# 就是 PP-OCR 可改善的上限。而「怎麼改」取決於失分在哪一段：
#
#   det 沒框到  → 那塊區域 PP-OCR 根本沒有框 → 要動偵測（或前處理／解析度）
#   rec 認錯字  → 有框、位置對得上，但字讀錯了 → 要動辨識（字典約束、fine-tune）
#
# 這個區分沒有 Vision 就做不出來：以前只能說「這項沒讀到」，
# 不知道是沒看到還是看錯。既有基準線用肉眼抽查得到「det 沒有明顯在漏」，
# 這支是把那句話換成數字。
#
# 判法：取 Vision 讀到該成分的那些行的外接框，看 PP-OCR 有沒有框與它重疊。
# 用 IoU 太嚴（切行方式不同），改用「PP-OCR 的框中心落在 Vision 框內」
# 或「兩框有實質重疊」。
#
# 用法：
#   python gap_vs_vision.py
#   python gap_vs_vision.py --detail c58
import argparse
import json
import os
import sys
from collections import Counter

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import score_ocr as S   # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
PPOCR = "v6_hires__boxth0.4"
VISION = "gvision"


def bbox(box):
    xs = [p[0] for p in box]
    ys = [p[1] for p in box]
    return min(xs), min(ys), max(xs), max(ys)


def overlap(a, b):
    """兩框的重疊面積佔較小者的比例。"""
    ix = max(0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    if inter <= 0:
        return 0.0
    sa = (a[2] - a[0]) * (a[3] - a[1])
    sb = (b[2] - b[0]) * (b[3] - b[1])
    return inter / max(1, min(sa, sb))


def find_in(lines, key):
    """回傳含有 key（正規化後）的那些行的外接框。

    巢狀配方會橫跨很多行（`漢堡{麵粉, 水, 糖, …}` 在版面上是一整段），
    整串比對一定找不到。所以逐步退到用**前綴**定位——只要知道這個成分
    「從哪裡開始」就足以判斷 PP-OCR 有沒有框到那個位置。
    """
    ordered = [l for l in lines if l.get("box") and l.get("text")]
    for probe in (key, key[:16], key[:8]):
        if len(probe) < 4:
            break
        hits = [bbox(l["box"]) for l in ordered
                if probe in S.normalize(l["text"], fold_variants=True)]
        if hits:
            return hits
        for i in range(len(ordered) - 1):
            for j in (2, 3):
                grp = ordered[i:i + j]
                joined = S.normalize("".join(x["text"] for x in grp),
                                     fold_variants=True)
                if probe in joined:
                    bs = [bbox(x["box"]) for x in grp]
                    return [(min(b[0] for b in bs), min(b[1] for b in bs),
                             max(b[2] for b in bs), max(b[3] for b in bs))]
    return []


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--detail", default=None)
    ap.add_argument("--thresh", type=float, default=0.15,
                    help="重疊比例門檻，超過就算 det 有框到")
    a = ap.parse_args()

    cases = json.load(open(os.path.join(S.EVAL_ROOT, "cases.json"),
                           encoding="utf-8"))["cases"]
    tally = Counter()
    rows = []
    for c in cases:
        cid = c["case_id"]
        if a.detail and not cid.startswith(a.detail):
            continue
        gt = S.load_gt(cid, c.get("category") or "")
        if not gt or not gt.get("ingredients_list"):
            continue
        pp = os.path.join(HERE, "out", PPOCR, f"{cid}.json")
        gv = os.path.join(HERE, "out", VISION, f"{cid}.json")
        if not (os.path.exists(pp) and os.path.exists(gv)):
            continue
        prec = json.load(open(pp, encoding="utf-8"))
        vrec = json.load(open(gv, encoding="utf-8"))
        plines = [l for im in prec["images"] for l in im.get("lines", [])]
        vlines = [l for im in vrec["images"] for l in im.get("lines", [])]
        ptext = S.normalize("\n".join(l["text"] for l in plines), True)
        vtext = S.normalize("\n".join(l["text"] for l in vlines), True)

        for name in gt["ingredients_list"]:
            key = S.normalize(name, fold_variants=True)
            if not key:
                continue
            pe, pf, pm = S.ingredient_hits([name], ptext)
            ve, vf, vm = S.ingredient_hits([name], vtext)
            if not pm or vm:
                continue                    # 只看「Vision 讀到、PP-OCR 沒讀到」
            vboxes = find_in(vlines, key)
            if not vboxes:
                tally["定位不到"] += 1
                rows.append(("定位不到", cid, name))
                continue
            best = 0.0
            for vb in vboxes:
                for l in plines:
                    if l.get("box"):
                        best = max(best, overlap(vb, bbox(l["box"])))
            lay = "rec 認錯字" if best >= a.thresh else "det 沒框到"
            tally[lay] += 1
            rows.append((lay, cid, name, round(best, 2)))

    tot = sum(tally.values())
    print(f"Vision 讀到、PP-OCR 沒讀到的成分共 {tot} 項\n")
    for k in ("rec 認錯字", "det 沒框到", "定位不到"):
        if tally[k]:
            print(f"  {k:<10}{tally[k]:>4} 項")
    print()
    for k in ("det 沒框到", "rec 認錯字", "定位不到"):
        sel = [r for r in rows if r[0] == k]
        if not sel:
            continue
        print(f"── {k}（{len(sel)} 項）──")
        by = {}
        for r in sel:
            by.setdefault(r[1], []).append(r[2])
        for cid in sorted(by, key=lambda x: -len(by[x])):
            names = "、".join(n[:24] for n in by[cid][:5])
            more = " …" if len(by[cid]) > 5 else ""
            print(f"   {cid:<28}{len(by[cid]):>2} 項  {names}{more}")
        print()


if __name__ == "__main__":
    main()
