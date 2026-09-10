#!/usr/bin/env python3
# Surya OCR 跑量化測試集，輸出對齊 out/<preset>/ 的既有格式。
#
# 為什麼試 Surya：既有結論指出瓶頸不在 det/rec 而在**行序**
# （ppocr-baseline-finding），而 Surya 的輸出自帶 `reading_order`，
# 是目前試過的讀取器裡唯一把閱讀順序當成一等公民的。
# 另外兩個 PP-OCR 沒有、VLM 也沒有的東西：
#   confidence  逐區塊信心值 —— 讀不出來時可以擋，這是 VLM 缺的那道防線
#   html        區塊內保留結構（營養表會是 <table>），對最弱的數字層有針對性
#
# ⚠ 必須用 .venv_torch 跑。torch 與 paddlepaddle 在同一環境會衝突
#   （見 ocr-layer-exhausted：paddlex 無條件 import modelscope → torch → cuDNN 相衝）：
#     .venv_torch/Scripts/python.exe run_surya.py
#
# 輸出格式刻意與 v6_hires__boxth0.4 / gvision 一致（case_id/preset/images/lines），
# 這樣 bench_ingredients、sim_match_preset、run_gemini_text --src 都能直接吃。
# Surya 特有的欄位（reading_order/label/confidence/html）額外掛在每個 line 上，
# 既有工具會忽略，但不丟掉資訊。
#
# 用法：
#   .venv_torch/Scripts/python.exe run_surya.py --cases c13 c18
#   .venv_torch/Scripts/python.exe run_surya.py                # 全跑
import argparse
import html as _html
import json
import os
import re
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

EVAL_ROOT = os.path.abspath(os.path.join(
    HERE, "..", "測試", "量化測試"))
PRESET = "surya"


def strip_html(s):
    """把區塊的 html 還原成純文字。

    表格的 <td> 之間補空白，否則相鄰儲存格的數字會黏成一串
    （「4.2」「10.5」→「4.210.5」），那正是營養層最怕的錯誤。
    """
    s = re.sub(r"<(br|/td|/th|/tr|/p|/div)[^>]*>", " ", s or "")
    s = re.sub(r"<[^>]+>", "", s)
    return re.sub(r"\s+", " ", _html.unescape(s)).strip()


def quad(poly):
    """Surya 給的多邊形轉成既有格式的四點框（左上順時針）。"""
    if not poly:
        return None
    xs = [p[0] for p in poly]
    ys = [p[1] for p in poly]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    return [[round(x0), round(y0)], [round(x1), round(y0)],
            [round(x1), round(y1)], [round(x0), round(y1)]]


def cases(only):
    p = json.load(open(os.path.join(EVAL_ROOT, "cases.json"), encoding="utf-8"))
    out = []
    for c in p["cases"]:
        cid = c["case_id"]
        if only and not any(cid.startswith(o) for o in only):
            continue
        out.append((cid, c.get("category") or "", c.get("images") or [],
                    c.get("set_version")))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=PRESET)
    ap.add_argument("--cases", nargs="*", default=None)
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()

    from PIL import Image
    from surya.detection import DetectionPredictor
    from surya.layout import LayoutPredictor
    from surya.recognition import RecognitionPredictor

    outdir = os.path.join(HERE, "out", a.out)
    os.makedirs(outdir, exist_ok=True)

    print("載入模型…")
    det = DetectionPredictor()
    lay = LayoutPredictor()
    rec = RecognitionPredictor()

    rows = cases(a.cases)
    print("共 %d 案 -> out/%s/" % (len(rows), a.out))
    for i, (cid, cat, imgs, sv) in enumerate(rows, 1):
        dst = os.path.join(outdir, cid + ".json")
        if os.path.exists(dst) and not a.force:
            print("[%d/%d] %-28s 已存在，略過" % (i, len(rows), cid))
            continue
        recs = []
        for rel in imgs:
            p = os.path.join(EVAL_ROOT, rel.replace("/", os.sep))
            if not os.path.exists(p):
                print("  !! 找不到", p)
                continue
            im = Image.open(p).convert("RGB")
            t0 = time.time()
            lr = lay([im])
            pr = rec([im], layout_results=lr)
            dt = time.time() - t0

            lines = []
            blocks = sorted(pr[0].blocks,
                            key=lambda b: (b.reading_order if
                                           b.reading_order is not None else 1e9))
            for b in blocks:
                txt = strip_html(b.html)
                if not txt:
                    continue
                lines.append({
                    "text": txt,
                    "score": round(float(b.confidence or 0), 4),
                    "box": quad(b.polygon),
                    # Surya 特有 —— 既有工具會忽略，但這三個才是試它的理由
                    "reading_order": b.reading_order,
                    "label": b.label,
                    "html": b.html,
                })
            recs.append({"path": rel, "elapsed_s": round(dt, 2),
                         "n_lines": len(lines), "lines": lines})

        doc = {"case_id": cid, "preset": a.out, "set_version": sv,
               "category": cat, "images": recs}
        with open(dst, "w", encoding="utf-8") as f:
            json.dump(doc, f, ensure_ascii=False)
        n = sum(r["n_lines"] for r in recs)
        s = sum(r["elapsed_s"] for r in recs)
        labs = {}
        for r in recs:
            for ln in r["lines"]:
                labs[ln["label"]] = labs.get(ln["label"], 0) + 1
        print("[%d/%d] %-28s %5.1fs %3d 區塊  %s"
              % (i, len(rows), cid, s, n,
                 "、".join("%s×%d" % kv for kv in sorted(labs.items()))[:60]))


if __name__ == "__main__":
    main()
