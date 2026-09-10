#!/usr/bin/env python3
# PaddleOCR-VL 1.6 (GGUF) 在量化測試集上的推論，輸出格式對齊 run_baseline.py，
# 因此可以直接用 score_ocr.py / bench_ingredients.py 評分、與 PP-OCR 並排比較。
#
# 為什麼要試這條路：PPOCR_TEST/README.md 的結論是「det/rec 不是瓶頸，最大的
# 失分在行序與版面重建」，而那一段 fine-tune det 或 rec 都碰不到。VLM 是端到端
# 直接吐閱讀順序的文字，剛好打在那個缺口上。這支就是去量它到底補了多少。
#
# 跑之前要先在 LM Studio 載好模型並開 server：
#   lms load paddleocr-vl-1.6 --identifier=ocrvl --gpu=max
#   lms server start
# 模型與 mmproj 必須成對被 LM Studio 認出來——mmproj 檔名要是 `mmproj-*.gguf`
# 前綴，否則 LM Studio 會當成兩個獨立模型，送圖會回
# "does not support image inputs"。
#
# 用法：
#   python run_vlm.py                       # 全跑，已存在的跳過
#   python run_vlm.py --preset=vlm_2048 --maxside=2048
#   python run_vlm.py --cases c38 c58 --force
import argparse
import base64
import io
import json
import os
import re
import sys
import time

import requests
from PIL import Image

sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
EVAL_ROOT = os.path.abspath(
    os.path.join(HERE, "..", "測試", "量化測試")
)
URL = "http://localhost:1234/v1/chat/completions"
MODEL = "ocrvl"

# PaddleOCR-VL 的原生提示詞就是這幾個固定字串（OCR: / Table Recognition: /
# Formula Recognition: / Chart Recognition:），不是一般 VLM 的自由指令。
# 官方管線是先用版面模型切塊再逐塊給對應提示；這裡整張餵是 off-label 用法，
# 所以只用最泛用的 "OCR:"。
PROMPT = "OCR:"

# repeat_penalty 是必要的，不是調參。不加的話模型在營養表那種高度重複的版面
# 會鎖進「每份40公克 / 熱量195大卡 / …」的無限迴圈直到吃滿 max_tokens
# （c24 實測：1500 token 產出 187 行、只有 8 行是相異的）。
#
# 但它**修不乾淨**：c38 在 rot=90 用 1.15 正常收尾、1.10 爆迴圈；同一張圖
# rot=270 反過來，1.10 正常、1.15 爆迴圈。這不是還沒調到最佳值，是整頁餵給
# 一個 0.5B 的 VLM 本來就不穩。所以下面還有 dedup_lines() 收尾。
REPEAT_PENALTY = 1.15

CJK = re.compile(r"[一-鿿]")


def dedup_lines(lines):
    """砍掉退化迴圈重複出的行，回傳 (保留的行, 砍掉幾行)。

    只砍「同一行出現第 3 次以上」——雙欄營養表本來就會有兩行都是「0公克」，
    留前兩次才不會誤傷。順序不動，因為主指標是逐項比對，位置本來就不計分。
    """
    seen, keep = {}, []
    for t in lines:
        k = t.strip()
        seen[k] = seen.get(k, 0) + 1
        if seen[k] <= 2:
            keep.append(t)
    return keep, len(lines) - len(keep)


def load_orient():
    p = os.path.join(HERE, "out", "_vlm_orient.json")
    if not os.path.exists(p):
        sys.exit("缺 out/_vlm_orient.json，先跑 python vlm_orient.py")
    return json.load(open(p, encoding="utf-8"))


def encode(path, maxside, rot):
    im = Image.open(path).convert("RGB")
    orig = im.size
    if rot:
        im = im.rotate(-rot, expand=True)   # 正值 = 順時針
    w, h = im.size
    s = maxside / max(w, h)
    if s < 1:
        im = im.resize((round(w * s), round(h * s)), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=92)
    return base64.b64encode(buf.getvalue()).decode(), orig, im.size


def ask(b64, max_tokens):
    body = {
        "model": MODEL,
        "messages": [{"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + b64}},
            {"type": "text", "text": PROMPT},
        ]}],
        "temperature": 0,
        "max_tokens": max_tokens,
        "repeat_penalty": REPEAT_PENALTY,
    }
    t = time.time()
    r = requests.post(URL, json=body, timeout=1800)
    dt = time.time() - t
    d = r.json()
    if "error" in d:
        raise RuntimeError(d["error"])
    ch = d["choices"][0]
    return ch["message"]["content"], ch.get("finish_reason"), d.get("usage", {}), dt


def run_image(path, maxside, max_tokens, needs_rot):
    """側躺的圖 90/270 都跑，取「去重後」漢字多的那個。

    框的形狀分不出是順時針還逆時針，只能兩邊都試。比的是去重後的字數而不是
    原始字數——爆迴圈的那一邊原始字數一定比較多（c38 實測 1654 對 363），
    用原始字數選會穩定選到壞的那個。
    """
    angles = [90, 270] if needs_rot else [0]
    best, tried = None, []
    for a in angles:
        b64, orig, sent = encode(path, maxside, a)
        try:
            text, finish, usage, dt = ask(b64, max_tokens)
        except RuntimeError as e:
            # LM Studio 偶爾回 "output does not match the expected Content-only
            # format"——模型吐出它解不開的 token。整批跑不能因為一張圖就死掉，
            # 記成空結果讓評分看得到這一案是 0，不要靜靜跳過。
            print(f"    ! {os.path.basename(path)} rot={a} 失敗：{e}")
            tried.append({"rot": a, "n_cjk": 0, "error": str(e)[:200]})
            if best is None:
                best = {"rot": a, "lines": [], "dropped": 0, "finish": "error",
                        "usage": {}, "secs": 0.0, "n_cjk": 0,
                        "orig": orig, "sent": sent, "error": str(e)[:200]}
            continue
        raw = [t for t in text.splitlines() if t.strip()]
        kept, dropped = dedup_lines(raw)
        n_cjk = len(CJK.findall("\n".join(kept)))
        tried.append({"rot": a, "n_cjk": n_cjk, "dropped": dropped,
                      "secs": round(dt, 1), "finish": finish})
        if best is None or n_cjk > best["n_cjk"]:
            best = {"rot": a, "lines": kept, "dropped": dropped,
                    "finish": finish, "usage": usage, "secs": dt,
                    "n_cjk": n_cjk, "orig": orig, "sent": sent}
    best["tried"] = tried
    return best


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--preset", default="vlm_2048")
    ap.add_argument("--maxside", type=int, default=2048)
    ap.add_argument("--max-tokens", type=int, default=3072)
    ap.add_argument("--cases", nargs="*", default=None,
                    help="case_id 前綴，例如 c38 c58")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()

    orient = load_orient()
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
    done = skipped = 0
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
            needs = orient.get(rel, {}).get("rotate", False)
            r = run_image(src, a.maxside, a.max_tokens, needs)
            # 一行一 line，box 留 None：VLM 不給座標。score_ocr.py 只用 text，
            # 但 bench_ingredients.py 之類吃 box 的分析在這個 preset 上不適用。
            lines = [{"text": t, "score": None, "box": None} for t in r["lines"]]
            rec["images"].append({
                "path": rel,
                "elapsed_s": round(r["secs"], 2),
                "n_lines": len(lines),
                "lines": lines,
                "vlm": {"rot": r["rot"], "finish": r["finish"],
                        "dropped_repeat_lines": r["dropped"],
                        "usage": r["usage"], "orig_size": list(r["orig"]),
                        "sent_size": list(r["sent"]), "tried": r["tried"]},
            })
            flag = "" if r["finish"] == "stop" else f"  !{r['finish']}"
            dr = f"  -{r['dropped']}重複" if r["dropped"] else ""
            print(f"[{i}/{len(cases)}] {cid:<30} rot={r['rot']:<3} "
                  f"{r['sent'][0]}x{r['sent'][1]:<5} {r['secs']:5.1f}s "
                  f"{len(lines):3d}行 {r['n_cjk']:4d}漢字{flag}{dr}")

        json.dump(rec, open(dst, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
        done += 1

    print(f"\n完成 {done} 案（跳過 {skipped}），共 {time.time()-t0:.0f}s"
          f" -> out/{a.preset}/")
    print(f"評分： python score_ocr.py --preset={a.preset}")


if __name__ == "__main__":
    main()
