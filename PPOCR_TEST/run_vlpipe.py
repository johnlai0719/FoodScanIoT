#!/usr/bin/env python3
# PaddleOCR **官方** PaddleOCRVL 管線（版面偵測 → 逐塊送 VL）在量化測試集上的推論。
#
# 跟 run_vlm.py 的差別就是「有沒有版面階段」：
#   run_vlm.py    整張圖 → LM Studio → 文字        （off-label 用法）
#   run_vlpipe.py 整張圖 → PP-DocLayoutV3 切塊 → 逐塊送 VL → markdown
# 兩者用的是**同一個 GGUF 模型、同一個 LM Studio server**，所以差異可以直接
# 歸因於版面階段，不會混進模型或量化的變因。
#
# 前置：
#   pip install "openai>=1.63"          （genai-client plugin，只需要這個）
#   lms load paddleocr-vl-1.6 --identifier=ocrvl --gpu=max ; lms server start
#
# 用法：
#   python run_vlpipe.py                      # 全跑，已存在的跳過
#   python run_vlpipe.py --cases c58 --force
#   python score_ocr.py --preset=vlpipe
import argparse
import json
import os
import re
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
EVAL_ROOT = os.path.abspath(
    os.path.join(HERE, "..", "測試", "量化測試")
)
CJK = re.compile(r"[一-鿿]")
TAG = re.compile(r"<[^>]+>")


def md_to_lines(md):
    """把管線輸出的 markdown 拆成行，供 score_ocr.py 使用。

    營養表是 HTML <table>，把標籤換成換行而不是直接刪掉——刪掉會讓
    「蛋白質4.2公克10.5公克」黏成一串，number_present 仍找得到，但人工看
    detail 時完全讀不出行列關係。
    """
    txt = str(md)
    txt = re.sub(r"</t[dhr]>", "\n", txt)
    txt = TAG.sub(" ", txt)
    lines = []
    for ln in txt.splitlines():
        s = ln.strip()
        if s and not s.startswith("!["):
            lines.append(s)
    return lines


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--preset", default="vlpipe")
    ap.add_argument("--cases", nargs="*", default=None)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()

    from paddleocr import PaddleOCRVL
    pipe = PaddleOCRVL(
        vl_rec_backend="llama-cpp-server",
        vl_rec_server_url="http://localhost:1234/v1",
        vl_rec_api_model_name="ocrvl",
        vl_rec_api_key="lm-studio",
        use_doc_orientation_classify=True,   # 取代 run_vlm.py 手刻的 vlm_orient.py
        use_doc_unwarping=False,             # 既有實測：unwarp 對這批照片是負向
        use_ocr_for_image_block=True,        # 整張被判成 image 時的退路（c58 需要）
    )

    cases = json.load(open(os.path.join(EVAL_ROOT, "cases.json"),
                           encoding="utf-8"))["cases"]
    if a.cases:
        cases = [c for c in cases
                 if any(c["case_id"].startswith(x) for x in a.cases)]
    if a.limit:
        cases = cases[:a.limit]

    outdir = os.path.join(HERE, "out", a.preset)
    os.makedirs(outdir, exist_ok=True)
    t0 = time.time()
    done = skipped = failed = 0

    for i, c in enumerate(cases, 1):
        cid = c["case_id"]
        dst = os.path.join(outdir, f"{cid}.json")
        if os.path.exists(dst) and not a.force:
            skipped += 1
            continue
        rec = {"case_id": cid, "preset": a.preset,
               "set_version": c.get("set_version"),
               "category": c.get("category"), "images": []}
        for rel in c["images"]:
            src = os.path.join(EVAL_ROOT, rel.replace("/", os.sep))
            t = time.time()
            try:
                out = list(pipe.predict(src))
            except Exception as e:
                print(f"[{i}/{len(cases)}] {cid:<28} 失敗 {type(e).__name__}: "
                      f"{str(e)[:70]}")
                rec["images"].append({"path": rel, "elapsed_s": 0, "n_lines": 0,
                                      "lines": [], "error": str(e)[:200]})
                failed += 1
                continue
            res = out[0]
            j = res.json["res"]
            boxes = (j.get("layout_det_res") or {}).get("boxes") or []
            md = res.markdown
            lines = md_to_lines(md.get("markdown_texts")
                                if isinstance(md, dict) else md)
            dt = time.time() - t
            from collections import Counter
            labels = dict(Counter(b["label"] for b in boxes))
            rec["images"].append({
                "path": rel, "elapsed_s": round(dt, 2), "n_lines": len(lines),
                "lines": [{"text": t, "score": None, "box": None} for t in lines],
                "layout": {"n_blocks": len(boxes), "labels": labels},
            })
            n = len(CJK.findall("\n".join(lines)))
            print(f"[{i}/{len(cases)}] {cid:<28}{dt:6.1f}s {len(boxes):>2}塊 "
                  f"{len(lines):>3}行 {n:>5}漢字  {labels}")
        json.dump(rec, open(dst, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
        done += 1

    print(f"\n完成 {done} 案（跳過 {skipped}、失敗 {failed}），"
          f"共 {time.time()-t0:.0f}s -> out/{a.preset}/")
    print(f"評分： python score_ocr.py --preset={a.preset}")


if __name__ == "__main__":
    main()
