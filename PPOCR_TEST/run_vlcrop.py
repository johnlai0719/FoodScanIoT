#!/usr/bin/env python3
# 混合版：用 region_crop.py 的框切區域，再逐區送 VL。
#
# 為什麼是這個組合（三方實測後的結論，見 README）：
#   run_vlm.py    整張餵          → 營養漏 347（解碼器退化）
#   run_vlpipe.py 官方版面→逐塊餵 → 營養漏 156（好一半），但成分 109→130 退步，
#                                  因為 PP-DocLayoutV3 在包裝照上會整塊漏掉
#   region_crop.py 抓營養表 52/57，官方版面模型的 table 只有 43/58
# 所以版面階段要留，但用領域關鍵字那套，不要用文件訓練出來的版面模型。
#
# 提示詞照官方管線分：文字區 "OCR:"、營養表 "Table Recognition:"
# （見 paddlex/inference/pipelines/paddleocr_vl/pipeline.py:308-313）。
# 表格提示詞是關鍵——整頁餵時營養數值會掉小數點（4.2→42），用表格提示詞
# 讀出來的是行列對齊的 HTML，每份／每100克配對正確。
#
# 前置：lms load paddleocr-vl-1.6 --identifier=ocrvl ; lms server start
#
# 用法：
#   python run_vlcrop.py
#   python run_vlcrop.py --cases c58 c38 --force
#   python score_ocr.py --preset=vlcrop
import argparse
import base64
import io
import json
import os
import re
import statistics
import sys
import time

import requests
from PIL import Image

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import region_crop as RC   # noqa: E402
import score_ocr as S      # noqa: E402  （_linecls_probs 的過濾規則要用 normalize）

HERE = os.path.dirname(os.path.abspath(__file__))
EVAL_ROOT = os.path.abspath(
    os.path.join(HERE, "..", "測試", "量化測試")
)
# ⚠ 第六支寫死 OCR 目錄的腳本（2026-09-07）。前五支是 bench_ingredients／
# score_items／boot_compare／emit_json／region_crop。測試集擴充後全都會
# **靜默只處理舊案例**——這支還會逐案印「缺 xxx 輸出，跳過」，但 177 案
# 只跑了 58 案仍以 exit 0 結束，不看輸出就發現不了。
BOXES = os.environ.get("PPOCR_BOXES") or "v6_best"
# 成分框改用逐行分類器的機率。0 ＝ 關閉，行為與 2026-09-11 之前完全相同。
LINECLS_TH = float(os.environ.get("VL_LINECLS") or 0)
LINECLS_DIR = os.environ.get("VL_LINECLS_DIR") or "linecls_pred"


def _linecls_probs(cid, ppocr):
    """把折外預測接回 OCR 行序，回傳與所有行等長的機率序列。

    ⚠ `make_linecls.py` 丟掉「正規化後不足 2 字」的行，所以預測筆數比 OCR
       行數少（177 案：12383 vs 14098）。接回去必須**套同一條過濾規則**
       ——沒套的話 176 案裡有 172 案會對不齊（2026-09-11 實際踩過）。
       對不齊就回 None，讓呼叫端整案退回規則，不要用錯位的機率去框。
    """
    p = os.path.join(HERE, "out", LINECLS_DIR, f"{cid}.json")
    if not os.path.exists(p):
        return None
    pr = json.load(open(p, encoding="utf-8"))
    flat = [l for im in ppocr.get("images") or [] for l in (im.get("lines") or [])]
    ok = [l for l in flat
          if len(S.normalize((l.get("text") or "").strip())) >= 2]
    if len(ok) != len(pr):
        return None
    it = iter(pr)
    out = []
    for l in flat:
        if len(S.normalize((l.get("text") or "").strip())) >= 2:
            out.append(next(it)["p"])
        else:
            out.append(-1.0)
    return out


def _box_from(lines, ps, th):
    bs = [RC.bbox(l["box"]) for l, q in zip(lines, ps)
          if l.get("box") and q >= th]
    if not bs:
        return None
    return (min(b[0] for b in bs), min(b[1] for b in bs),
            max(b[2] for b in bs), max(b[3] for b in bs))
URL = "http://localhost:1234/v1/chat/completions"   # 由 --backend 覆寫

# 可換的讀字模型。**提示詞必須跟著模型換** —— `OCR:` / `Table Recognition:`
# 是 PaddleOCR-VL 的原生提示詞（見 paddlex pipeline.py:308-313），
# 對別的模型是無意義字串。
BACKENDS = {
    # 原始組態：PaddleOCR-VL 0.9B，經 LM Studio（預設埠 1234）
    "paddleocrvl": {
        # 2026-09-07 改走 llama-server（埠 8178），不再依賴 LM Studio——
        # 與 hunyuan 同一種起法，兩個後端才是對等的比較條件。
        "url": os.environ.get("PVL_URL") or "http://127.0.0.1:8178/v1/chat/completions",
        "model": "ocrvl",
        "ingredients": "OCR:",
        "nutrition": "Table Recognition:",
    },
    # HunyuanOCR-1.5：2026-08-31 實測，解析失敗 13→1（結構最好的一個），
    # 但整頁餵時會整塊靜默漏讀（OCR 失敗 18→42）。裁切後逐區餵正是為了
    # 消掉那個漏讀 —— 區域已經指定，它沒得跳過。
    "hunyuan": {
        "url": "http://127.0.0.1:8177/v1/chat/completions",
        "model": "hunyuan",
        "ingredients": "請完整轉錄圖中的文字，保留原有的頓號與括號。",
        "nutrition": "請將圖中的營養標示轉為表格，保留每一欄的對應關係。",
    },
}
CJK = re.compile(r"[一-鿿]")
TAG = re.compile(r"<[^>]+>")
REPEAT_PENALTY = 1.15   # 理由見 README：不加會鎖進迴圈，且修不乾淨
# ── 兩個「會改變送進 VLM 的圖」的旋鈕 ────────────────────────────────────
# 這兩個跟 region_crop 的四個不同：改了就**非重跑不可**（177 案 ≈ 30 分鐘），
# 代理指標看不到影響。2026-09-08 量到的現況：
#   成分區 222 塊，**158 塊（71%）最長邊卡在 2048 上限**（中位數就是 2048）
#   營養區 169 塊，只有 28 塊卡上限（中位 1509）
#   finish_reason=length（撞 token 上限）8 塊：成分 7、營養 1
# 所以 maxside 對成分區是**真的在綁**，對營養區大多不綁。
PAD = float(os.environ.get("VL_PAD") or 0.02)        # 裁切外擴，避免切到邊緣筆畫
MAXSIDE = int(os.environ.get("VL_MAXSIDE") or 2048)  # 送出去的圖最長邊


# 影像根目錄。壓縮實驗用 `EVAL_IMAGE_ROOT=images_1280q80` 切換。
# ⚠ 它是**前綴**不是替換：`compress_images.py` 產出的是
# `images_1280q80/images/beverage/...`，把 `images/` 換掉會找不到檔案。
# 與 `測試/量化測試/run_eval.py`（`os.path.join(IMAGE_ROOT, rel)`）的慣例一致。
# 2026-09-09 第一版寫成替換，PP-OCR 在 177 案上讀出 0 行、**exit 0 不報錯**。
IMAGE_ROOT = os.environ.get("EVAL_IMAGE_ROOT") or ""


def _img(rel):
    """把 cases.json 的相對路徑解到 IMAGE_ROOT 底下。

    ⚠ **副檔名不符時回退到 .jpg。** `compress_images.py` 一律存成 JPEG，
    而測試集裡有 3 張 .webp 與 1 張 .jpeg（c03／c06／c08／c17），
    壓縮後變成 .jpg。沿用 `run_eval.py` 的同一條回退規則。
    2026-09-09 沒有這條的後果：run_baseline 把那 4 張標成 `missing` 後
    **繼續跑完並回報成功**，run_vlcrop 則直接崩在 FileNotFoundError。
    """
    if not IMAGE_ROOT:
        return rel
    p = os.path.join(IMAGE_ROOT, rel)
    if not os.path.exists(os.path.join(EVAL_ROOT, p.replace("/", os.sep))):
        alt = os.path.splitext(p)[0] + ".jpg"
        if os.path.exists(os.path.join(EVAL_ROOT, alt.replace("/", os.sep))):
            return alt
    return p

def region_needs_rotation(lines, box):
    """這一區的文字是不是直排。逐區判而不是逐頁判——包裝上常常營養表橫排、
    成分直排，整頁一起轉會有一邊是錯的。"""
    r = []
    for l in lines:
        if not l.get("box"):
            continue
        b = RC.bbox(l["box"])
        cx, cy = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
        if not (box[0] <= cx <= box[2] and box[1] <= cy <= box[3]):
            continue
        w, h = b[2] - b[0], b[3] - b[1]
        if w > 3 and h > 3:
            r.append(h / w)
    return len(r) >= 3 and statistics.median(r) > 1.0


def crop(img, box, rot, maxside=None):
    if maxside is None:
        maxside = MAXSIDE
    W, H = img.size
    pad = PAD * max(W, H)
    c = img.crop((max(0, box[0] - pad), max(0, box[1] - pad),
                  min(W, box[2] + pad), min(H, box[3] + pad)))
    if rot:
        c = c.rotate(-rot, expand=True)
    w, h = c.size
    s = maxside / max(w, h)
    if s < 1:
        c = c.resize((round(w * s), round(h * s)), Image.LANCZOS)
    return c


# HunyuanOCR-1.5 的 **transformers 本地後端**（不經 HTTP）。
# 為什麼要有：LoRA 微調的權重在 transformers 這邊，而既有的兩個後端都是打
# llama-server 的 API。要拿微調模型跟原模型在**正式計分器**上比，就得讓它
# 能被同一支 run_vlcrop 呼叫——這樣所有既有的評分腳本直接可用，不必轉 GGUF。
# 用法：
#   python run_vlcrop.py --backend=hunyuan_hf --preset=xxx            # 原模型
#   VL_LORA=out/_contaminated_lora_ckpt_n81 python run_vlcrop.py ...  # 掛 LoRA
# ⚠ 必須用 .venv_torch 跑（torch 與 paddle 不能同環境，見「OCR 層已用盡」）。
# 通用視覺語言模型的對照組（[[08-專用與通用視覺模型的對照]]）。
# **提示詞刻意與 hunyuan 完全相同**——本實驗只換模型這一個變因。
BACKENDS["qwen"] = {
    "url": os.environ.get("QWEN_URL") or "http://127.0.0.1:8179/v1/chat/completions",
    "model": "qwen",
    "ingredients": "請完整轉錄圖中的文字，保留原有的頓號與括號。",
    "nutrition": "請將圖中的營養標示轉為表格，保留每一欄的對應關係。",
    "extra": {"chat_template_kwargs": {"enable_thinking": False}},
}
BACKENDS["hunyuan_hf"] = {
    "url": None, "local": True,
    "model": os.environ.get("HY_HF") or os.path.join(
        os.path.expanduser("~"), ".cache", "huggingface", "hub",
        "models--tencent--HunyuanOCR", "snapshots",
        "47644ecc4fc854efa4f505155158831f36773ee4"),
    "lora": os.environ.get("VL_LORA") or None,
    "ingredients": "請完整轉錄圖中的成分欄文字，保留原有的頓號與括號。",
    "nutrition": "請將圖中的營養標示轉為表格，保留每一欄的對應關係。",
}

# 提示詞模板不是猜的，是從 GGUF 檔頭的 tokenizer.chat_template 讀出來的。
# HF 版的 tokenizer_config.json **沒有** chat template，apply_chat_template 會報錯。
# 而它的慣例跟多數模型相反：轉場記號放在內容**後面**。猜錯的症狀是輸出一片
# `$ $ $ $`，看起來像模型壞了。詳見 train_ft_overfit.py 檔頭。
HF_IMG = ("<｜hy_place▁holder▁no▁100｜><｜hy_place▁holder▁no▁102｜>"
          "<｜hy_place▁holder▁no▁101｜>")
HF_BOS, HF_USER = "<｜hy_begin▁of▁sentence｜>", "<｜hy_User｜>"
_HF = {}


def _hf():
    """第一次呼叫才載模型。"""
    if _HF:
        return _HF
    import torch
    from transformers import AutoProcessor, AutoModelForImageTextToText
    _HF["torch"] = torch
    _HF["pr"] = AutoProcessor.from_pretrained(CFG["model"])
    m = AutoModelForImageTextToText.from_pretrained(
        CFG["model"], dtype=torch.bfloat16, device_map="cuda").eval()
    if CFG.get("lora"):
        from peft import PeftModel
        m = PeftModel.from_pretrained(m, CFG["lora"]).eval()
        print("已掛 LoRA：%s（**汙染權重**，只可用於未訓練過的案例）" % CFG["lora"])
    _HF["m"] = m
    return _HF


def ask_local(img, prompt, max_tokens):
    h = _hf()
    torch = h["torch"]
    inp = h["pr"](text=[HF_BOS + HF_IMG + prompt + HF_USER],
                  images=[img], return_tensors="pt").to(h["m"].device)
    t = time.time()
    with torch.no_grad():
        g = h["m"].generate(**inp, max_new_tokens=max_tokens, do_sample=False)
    txt = h["pr"].batch_decode(g[:, inp["input_ids"].shape[1]:],
                               skip_special_tokens=True)[0].strip()
    fin = "length" if g.shape[1] - inp["input_ids"].shape[1] >= max_tokens else "stop"
    return txt, fin, time.time() - t


CFG = BACKENDS["paddleocrvl"]      # 由 main() 依 --backend 設定


# ── 裁切後的前處理（VL_PREP）──────────────────────────────────────────────
# 專案 2026-08 量過「九種影像前處理對 PP-OCR 全部無效」，但那是**整張圖 → PP-OCR**。
# vlcrop 的情況不同：VLM 只看裁切區，而且裁切區會被縮放到 MAXSIDE
# （原圖 2510px→2048px 縮小；壓縮圖 833px→900px 放大）。**放大之後銳化**
# 是完全不同的作用點，舊結論不能沿用。
# 所以這裡把前處理掛在**送出前的最後一步**，作用對象是實際送進模型的那張圖。
# 手法沿用 sweep_preprocess.py 的定義，確保兩邊可比。
PREP = os.environ.get("VL_PREP") or ""
_PREPF = None


def _prep(img):
    """PIL RGB → 前處理 → PIL RGB。VL_PREP 未設時原樣回傳。"""
    global _PREPF
    if not PREP:
        return img
    if _PREPF is None:
        import numpy as np
        import cv2
        import sweep_preprocess as SP
        fn = SP.VARIANTS[PREP]

        def f(im):
            bgr = cv2.cvtColor(np.array(im), cv2.COLOR_RGB2BGR)
            return Image.fromarray(cv2.cvtColor(fn(bgr), cv2.COLOR_BGR2RGB))
        _PREPF = f
    return _PREPF(img)


def ask(img, prompt, max_tokens=3072):
    img = _prep(img)
    if CFG.get("local"):
        return ask_local(img, prompt, max_tokens)
    b = io.BytesIO()
    img.save(b, "JPEG", quality=92)
    body = {"model": CFG["model"], "messages": [{"role": "user", "content": [
        {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," +
         base64.b64encode(b.getvalue()).decode()}},
        {"type": "text", "text": prompt}]}],
        "temperature": 0, "max_tokens": max_tokens,
        "repeat_penalty": REPEAT_PENALTY}
    # 後端專屬的額外欄位。Qwen3.5 是推理模型，**不關思考會回空字串**——
    # reasoning_content 把 max_tokens 吃光，content 拿到的是 ""。
    # 專案 2026-09 在 local_fields.py 踩過同一個坑。
    body.update(CFG.get("extra") or {})
    t = time.time()
    for attempt in range(2):     # LM Studio 偶發 Content-only format 400
        d = requests.post(CFG["url"], json=body, timeout=1800).json()
        if "error" not in d:
            ch = d["choices"][0]
            return ch["message"]["content"], ch.get("finish_reason"), time.time() - t
        if attempt == 0:
            body["max_tokens"] = max_tokens // 2
    return None, f"error: {str(d['error'])[:80]}", time.time() - t


def to_lines(txt):
    """表格輸出是 HTML，把 </td></tr> 換成換行再去標籤，保住行列可讀性。"""
    s = re.sub(r"</t[dhr]>", "\n", txt)
    s = TAG.sub(" ", s)
    out, seen = [], {}
    for ln in s.splitlines():
        v = ln.strip()
        if not v:
            continue
        seen[v] = seen.get(v, 0) + 1
        if seen[v] <= 2:          # 同 run_vlm.py 的 dedup：砍退化迴圈
            out.append(v)
    return out


# ── 沿長邊分條（[[12-裁切區沿長邊分條]]）────────────────────────────────
# `crop()` 的縮放按長邊：s = MAXSIDE / max(w, h)。裁切區比 MAXSIDE 大就被縮小，
# 像素在送出前就丟掉。實測送出縮放中位：**曲面 0.73×**、非曲面 0.88×。
# 沿長邊切半之後兩者都回到 1.00×（曲面 +37%）。
#
# 為什麼是曲面受益最大：圓柱前縮讓貼邊的字只剩正中的 58% 寬
# （61 px vs 105 px，行信心 0.911 vs 0.968；非曲面完全沒有這個梯度）。
# 整塊一起縮放時，最需要像素的邊緣被正中的部分拖著一起縮。
#
# ⚠ 送更大的圖無效：HunyuanOCR 的 max_image_size 是 2048，
#    `vlcrop_hy_ms3072` 實測成分 F1 73.2 → 72.9。**只能送更小的區域。**
SLICE = int(os.environ.get("VL_SLICE") or 0)      # 0 = 不分條
OVERLAP = float(os.environ.get("VL_OVERLAP") or 0.18)   # 相鄰條的重疊比例


def _safe_cuts(lines, box, n):
    """把 box 沿長邊切成 n 條，**相鄰兩條重疊**。

    ⚠ 第一版要求「切點不得穿過任何行框」，結果幾乎每案都切不成——
    成分是橫貫整個標示面的長行，**任何垂直切線都會穿過某一行**
    （c70 框 2510×2215，628 px 的搜尋範圍內找不到一個乾淨切點）。
    而水平切不會縮短長邊，縮放比毫無改善。

    改用重疊分條：跨在切點上的項目至少會完整出現在其中一條裡，
    輸出端本來就有去重（`to_lines` 與 `_dedupe`）。
    重疊比例 `VL_OVERLAP`（預設 0.18）是條寬的比例。
    """
    x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0
    horiz = w >= h
    span = w if horiz else h
    if n < 2 or span < 400:
        return [box]
    lo = x0 if horiz else y0
    step = span / n
    ov = step * OVERLAP
    out = []
    for i in range(n):
        a = max(lo, lo + i * step - ov)
        b = min(lo + span, lo + (i + 1) * step + ov)
        if b - a < 80:
            return [box]
        out.append((a, y0, b, y1) if horiz else (x0, a, x1, b))
    return out


def read_region(img, lines, box, prompt, max_tokens):
    """側躺的區域 90/270 都試，取去重後漢字多的。

    `VL_SLICE=N` 時沿長邊切成 N 條分別送，輸出依閱讀順序串接。
    """
    if SLICE >= 2:
        parts = _safe_cuts(lines, box, SLICE)
        if len(parts) > 1:
            got, secs, sizes, fins = [], 0.0, [], []
            for sub in parts:
                r = _read_one(img, lines, sub, prompt, max_tokens)
                got += r["lines"]; secs += r["secs"]
                sizes.append(r["size"]); fins.append(r["finish"])
                rot = r["rot"]
            return {"rot": rot, "lines": got, "finish": ",".join(str(f) for f in fins),
                    "secs": secs, "n": len(CJK.findall(chr(10).join(got))),
                    "size": sizes[0], "slices": len(parts), "sizes": sizes}
    return _read_one(img, lines, box, prompt, max_tokens)


def _read_one(img, lines, box, prompt, max_tokens):
    angles = [90, 270] if region_needs_rotation(lines, box) else [0]
    best = None
    for a in angles:
        c = crop(img, box, a)
        txt, fin, dt = ask(c, prompt, max_tokens)
        if txt is None:
            cand = {"rot": a, "lines": [], "finish": fin, "secs": dt,
                    "n": 0, "size": c.size}
        else:
            ls = to_lines(txt)
            cand = {"rot": a, "lines": ls, "finish": fin, "secs": dt,
                    "n": len(CJK.findall("\n".join(ls))), "size": c.size}
        if best is None or cand["n"] > best["n"]:
            best = cand
    return best


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--backend", default="paddleocrvl",
                    choices=sorted(BACKENDS))
    ap.add_argument("--preset", default="vlcrop")
    ap.add_argument("--max-tokens", type=int, default=3072)
    ap.add_argument("--cases", nargs="*", default=None)
    # ⚠ 用檔案傳而不是走命令列：case_id 含中文與 `®`（c154_維力®麻醬麵），
    #    經過 shell 變數與 argv 在 Windows 上會被 cp950 咬掉。
    #    專案已經被同一個東西咬過三次（intake 的「菓」、run_eval 的「塩」）。
    ap.add_argument("--cases-file", default=None,
                    help="一行一個 case_id 的 UTF-8 檔案")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    global CFG
    CFG = BACKENDS[a.backend]

    cases = json.load(open(os.path.join(EVAL_ROOT, "cases.json"),
                           encoding="utf-8"))["cases"]
    if a.cases_file:
        with io.open(a.cases_file, encoding="utf-8") as f:
            a.cases = [l.strip() for l in f if l.strip()]
    if a.cases:
        cases = [c for c in cases
                 if any(c["case_id"].startswith(x) for x in a.cases)]
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
        src_json = os.path.join(HERE, "out", BOXES, f"{cid}.json")
        if not os.path.exists(src_json):
            print(f"[{i}/{len(cases)}] {cid}  缺 {BOXES} 輸出，跳過")
            continue
        ppocr = json.load(open(src_json, encoding="utf-8"))

        rec = {"case_id": cid, "preset": a.preset,
               "set_version": c.get("set_version"),
               "category": c.get("category"), "images": []}
        probs = _linecls_probs(cid, ppocr) if LINECLS_TH else None
        pi = 0
        for im in ppocr["images"]:
            lines = im.get("lines") or []
            r = RC.find_regions(lines)
            # ── 成分框改由逐行分類器決定（VL_LINECLS=<門檻> 啟用）──────────
            # 代理指標實測（`crop_linecls.py`，176 案 219 張圖，折外預測）：
            #   規則        成分留存 98.4%　面積 58%
            #   P(成分)≥0.10 成分留存 101.1%　面積 33%
            # 留存超過 100% 不是筆誤：移除中間夾雜的行會讓被打斷的成分字串
            # 重新接起來，模糊比對因此配得上（見 c101）。
            # ⚠ 分類器一行都沒選中時**退回規則**，否則那幾張圖會整個變空。
            if probs is not None:
                n = len(lines)
                ps = probs[pi:pi + n]
                pi += n
                nb = _box_from(lines, ps, LINECLS_TH)
                if nb is not None:
                    r = dict(r)
                    r["ingredients"] = nb
            img = Image.open(os.path.join(
                EVAL_ROOT, _img(im["path"]).replace("/", os.sep))).convert("RGB")

            all_lines, meta, secs = [], {}, 0.0
            for kind, prompt in (("ingredients", CFG["ingredients"]),
                                 ("nutrition", CFG["nutrition"])):
                box = r[kind]
                if box is None:
                    meta[kind] = {"found": False}
                    continue
                b = read_region(img, lines, box, prompt, a.max_tokens)
                all_lines.extend(b["lines"])
                secs += b["secs"]
                meta[kind] = {"found": True, "rot": b["rot"],
                              "finish": b["finish"], "n_lines": len(b["lines"]),
                              "n_cjk": b["n"], "sent_size": list(b["size"]),
                              "box": [int(v) for v in box]}
                if b.get("slices"):
                    meta[kind]["slices"] = b["slices"]
                    meta[kind]["slice_sizes"] = [list(x) for x in b["sizes"]]
            rec["images"].append({
                "path": im["path"], "elapsed_s": round(secs, 2),
                "n_lines": len(all_lines),
                "lines": [{"text": t, "score": None, "box": None}
                          for t in all_lines],
                "regions": meta,
            })
            tag = "+".join(k[:3] for k in meta if meta[k].get("found"))
            print(f"[{i}/{len(cases)}] {cid:<28}{secs:6.1f}s  {tag:<9}"
                  f"{len(all_lines):>3}行 "
                  f"{len(CJK.findall(chr(10).join(all_lines))):>5}漢字")
        json.dump(rec, open(dst, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
        done += 1

    print(f"\n完成 {done} 案（跳過 {skipped}），共 {time.time()-t0:.0f}s"
          f" -> out/{a.preset}/")
    print(f"評分： python score_ocr.py --preset={a.preset}")


if __name__ == "__main__":
    main()
