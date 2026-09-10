#!/usr/bin/env python3
# Google Cloud Vision（DOCUMENT_TEXT_DETECTION）在量化測試集上的推論。
# 輸出格式對齊 run_baseline.py，可直接用 score_ocr.py / bench_ingredients.py /
# sim_match_preset.py 評分，與 PP-OCR、VL 各組並排。
#
# 為什麼值得測：它是這串比較裡唯一「商用文件 OCR」的參照點。
# 前面量到的三個痛點它都號稱有解——
#   行序：blocks → paragraphs → words → symbols 的階層，本身就是閱讀順序
#   方向：整頁旋轉自動處理（c38 側躺照直接吐正立文字，實測）
#   繁簡：language_hints=['zh-Hant'] 直接輸出繁體，不需要 OpenCC 後處理
#
# **行的切分用 symbol 的 detected_break**，不是用 paragraph。
# paragraph 太粗，會讓 p_boxsep（把框邊界當隱形頓號）失去作用——
# 那條是既有結論裡座標唯一真的有用的一條，不能弄丟。
#
# 費用：每張圖 1 unit，每月前 1000 units 免費，超過 $1.50/1000。
# 測試集 67 張圖 → 一次完整執行 67 units。
#
# 前置：
#   pip install google-cloud-vision
#   憑證走 --cred 或環境變數 GOOGLE_APPLICATION_CREDENTIALS
#
# 用法：
#   python run_gvision.py --cred=D:\FoodScanIot\GoogleCloudVision\xxx.json
#   python run_gvision.py --cases c38 c58 --force
#   python score_ocr.py --preset=gvision
import argparse
import json
import os
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
EVAL_ROOT = os.path.abspath(
    os.path.join(HERE, "..", "測試", "量化測試")
)

BREAK_EOL = None   # 延後初始化，避免沒裝套件時 import 就爆


def extract_lines(fta):
    """把 Vision 的階層攤成「行」，每行帶四點框。

    Vision 的層級是 block → paragraph → word → symbol，斷行資訊在 symbol 的
    `detected_break`（LINE_BREAK / EOL_SURE_SPACE）。依它切行，粒度才跟
    PP-OCR 的 text line 對得上，下游吃 box 的抽取器才能照常運作。
    """
    from google.cloud.vision import TextAnnotation
    B = TextAnnotation.DetectedBreak.BreakType
    eol = {B.LINE_BREAK, B.EOL_SURE_SPACE}

    lines = []
    buf, verts, confs = [], [], []

    def flush():
        if not buf:
            return
        xs = [v[0] for v in verts]
        ys = [v[1] for v in verts]
        lines.append({
            "text": "".join(buf),
            "score": round(sum(confs) / len(confs), 4) if confs else None,
            "box": [[min(xs), min(ys)], [max(xs), min(ys)],
                    [max(xs), max(ys)], [min(xs), max(ys)]],
        })
        buf.clear(); verts.clear(); confs.clear()

    for page in fta.pages:
        for block in page.blocks:
            for para in block.paragraphs:
                for word in para.words:
                    for v in word.bounding_box.vertices:
                        verts.append((v.x, v.y))
                    if word.confidence:
                        confs.append(word.confidence)
                    for sym in word.symbols:
                        buf.append(sym.text)
                        br = sym.property.detected_break
                        if br and br.type_ in eol:
                            flush()
                flush()
    flush()
    return lines


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--preset", default="gvision")
    ap.add_argument("--cred", default=None, help="service account JSON 路徑")
    ap.add_argument("--cases", nargs="*", default=None)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()

    if a.cred:
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = a.cred
    if not os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"):
        sys.exit("缺憑證：給 --cred 或設 GOOGLE_APPLICATION_CREDENTIALS")

    from google.cloud import vision
    client = vision.ImageAnnotatorClient()
    ctx = vision.ImageContext(language_hints=["zh-Hant"])

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
    done = skipped = failed = units = 0

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
                img = vision.Image(content=open(src, "rb").read())
                r = client.document_text_detection(image=img, image_context=ctx)
                units += 1
                if r.error.message:
                    raise RuntimeError(r.error.message)
                lines = extract_lines(r.full_text_annotation)
            except Exception as e:
                print(f"[{i}/{len(cases)}] {cid:<28} 失敗 {type(e).__name__}: "
                      f"{str(e)[:70]}")
                rec["images"].append({"path": rel, "elapsed_s": 0, "n_lines": 0,
                                      "lines": [], "error": str(e)[:200]})
                failed += 1
                continue
            dt = time.time() - t
            rec["images"].append({"path": rel, "elapsed_s": round(dt, 2),
                                  "n_lines": len(lines), "lines": lines})
            nch = sum(len(l["text"]) for l in lines)
            print(f"[{i}/{len(cases)}] {cid:<28}{dt:6.1f}s {len(lines):>4}行 "
                  f"{nch:>5}字")
        json.dump(rec, open(dst, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
        done += 1

    print(f"\n完成 {done} 案（跳過 {skipped}、失敗 {failed}），"
          f"共 {time.time()-t0:.0f}s，用掉 {units} units -> out/{a.preset}/")
    print(f"評分： python score_ocr.py --preset={a.preset}")


if __name__ == "__main__":
    main()
