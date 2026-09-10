#!/usr/bin/env python3
# 用 Google Vision 複製 nutrition_pipeline.py 的架構：全圖 ＋ 營養區裁切重讀，取聯集。
#
# 沿用既有結論裡那個被驗證有效的部分：PP-StructureV3 的價值**不是表格理解**
# （它把整張表塞進一個 <td>，行列沒還原），而是「裁出表格區域、以較高解析度
# 重讀」——那一步把全圖漏掉的數字撿回來，58 案漏 61 → 42。
#
# 這裡把兩端都換成 Vision：
#   base_lines   = Vision 讀全圖
#   struct_lines = Vision 讀 region_crop 切出來的營養區（裁切重讀）
# 版面階段用 region_crop.py 而不是 PP-DocLayoutV3——實測前者抓營養表
# 53/57、後者的 table 類別只有 43/58（文件訓練的模型在包裝照上分布外）。
#
# 輸出格式對齊 out/nutrition/，可直接餵 bench_parse.py 評分。
#
# 費用：每案多 1 unit（只裁出營養區的那一次），58 案約 53 units。
#
# 用法：
#   python nutrition_gv.py --cred=...\xxx.json
#   python bench_parse_gv.py
import argparse
import json
import os
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import score_ocr as S      # noqa: E402
import region_crop as RC   # noqa: E402
import run_gvision as GV   # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
EVAL_ROOT = S.EVAL_ROOT
SRC = "gvision"
OUT = os.path.join(HERE, "out", "nutrition_gv")
PAD = 0.02


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cred", default=None)
    ap.add_argument("--cases", nargs="*", default=None)
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    if a.cred:
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = a.cred

    from google.cloud import vision
    from PIL import Image
    client = vision.ImageAnnotatorClient()
    ctx = vision.ImageContext(language_hints=["zh-Hant"])
    os.makedirs(OUT, exist_ok=True)

    srcdir = os.path.join(HERE, "out", SRC)
    files = sorted(f for f in os.listdir(srcdir) if f.endswith(".json"))
    if a.cases:
        files = [f for f in files if any(f.startswith(x) for x in a.cases)]

    units = done = skipped = 0
    t0 = time.time()
    for i, fn in enumerate(files, 1):
        dst = os.path.join(OUT, fn)
        if os.path.exists(dst) and not a.force:
            skipped += 1
            continue
        rec = json.load(open(os.path.join(srcdir, fn), encoding="utf-8"))
        cid = rec["case_id"]
        base, struct = [], []
        for im in rec["images"]:
            lines = im.get("lines") or []
            base.extend(l["text"] for l in lines if l.get("text"))
            r = RC.find_regions(lines)
            box = r["nutrition"]
            if box is None:
                continue
            src = os.path.join(EVAL_ROOT, im["path"].replace("/", os.sep))
            img = Image.open(src).convert("RGB")
            W, H = img.size
            pad = PAD * max(W, H)
            crop = img.crop((max(0, box[0] - pad), max(0, box[1] - pad),
                             min(W, box[2] + pad), min(H, box[3] + pad)))
            import io
            buf = io.BytesIO(); crop.save(buf, "PNG")
            try:
                res = client.document_text_detection(
                    image=vision.Image(content=buf.getvalue()), image_context=ctx)
                units += 1
                if res.error.message:
                    raise RuntimeError(res.error.message)
                struct.extend(l["text"] for l in
                              GV.extract_lines(res.full_text_annotation))
            except Exception as e:
                print(f"  {cid} 裁切重讀失敗：{str(e)[:60]}")
        json.dump({"case_id": cid, "category": rec.get("category"),
                   "set_version": rec.get("set_version"),
                   "base_lines": base, "table_html_text": [],
                   "struct_lines": struct},
                  open(dst, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        done += 1
        print(f"[{i}/{len(files)}] {cid:<28} 全圖 {len(base):>3} 行"
              f"  裁切重讀 {len(struct):>3} 行")

    print(f"\n完成 {done} 案（跳過 {skipped}），{time.time()-t0:.0f}s，"
          f"用掉 {units} units -> out/nutrition_gv/")


if __name__ == "__main__":
    main()
