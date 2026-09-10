#!/usr/bin/env python3
# HunyuanOCR-1.5 跑量化測試集，輸出對齊 out/<preset>/ 的既有格式。
#
# 為什麼試它（2026-08-31 的量測結果決定的）：
#   換讀取器對主指標（父子配對）的影響遠大於預期——
#     PP-OCR   端到端召回 11.6%（100/860 條邊）
#     vlcrop   端到端召回 28.4%（244/860）  ← 2.4 倍
#   階層要的是**巢狀結構完整**，VL 吐的是閱讀順序、括號成對的文字；
#   PP-OCR 切成幾十個框再拼回去，括號就散了。
#
# 選這一個的三個理由：
#   1. 騰訊出品，語言明列 zh，`text-spotting` 是偵測＋辨識帶框
#   2. **官方直接支援 llama.cpp + GGUF + OpenAI 相容 llama-server**
#      （Surya 那次得跟 vLLM/Docker 後端搏鬥，這個不用）
#   3. BF16 才 1.08 GB，不必量化 —— 少一個變因
#
# ⚠ 已知風險：**它沒有信心值**。今天試過的讀取器裡只有 Surya 有，而 Surya
#   在洋芋片上讀出「藥丸專治諸般虛損」。靜默編造這件事本模型沒有免疫，
#   要靠 PP-OCR 文字交叉查核當防線（既有實測：59/65 的編造攔得下來）。
#
# 前置：llama-server 需在 PATH（WinGet 版即可）。本腳本自己起 server、跑完自己關。
#
# 用法：
#   python run_hunyuan.py --cases c13 c18 c01 c20
#   python run_hunyuan.py                      # 全跑
import argparse
import atexit
import base64
import io
import json
import os
import re
import signal
import subprocess
import sys
import time

import requests
from PIL import Image

sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

EVAL_ROOT = os.path.abspath(os.path.join(
    HERE, "..", "測試", "量化測試"))
LMS = r"C:/Users/johnl/.lmstudio/models"
PORT = 8177

# 每個模型用**它自己官方的提示詞**。提示詞不是自由發揮的地方——PaddleOCR-VL
# 那次 off-label 用法（整頁餵 + 自訂指令）讓四個案例吃滿 max_tokens。
MODELS = {
    "hunyuan": {
        "model": LMS + "/tencent/HunyuanOCR-1.5-GGUF/HunyuanOCR-1.5.gguf",
        "mmproj": LMS + "/tencent/HunyuanOCR-1.5-GGUF/mmproj-HunyuanOCR-1.5.gguf",
        "prompt": "請將圖片中的文字完整轉錄為 Markdown，保留表格結構。",
    },
    "unlimited": {
        # baidu/Unlimited-OCR 的官方提示詞（模型卡 README）
        "model": LMS + "/baidu/Unlimited-OCR-GGUF/Unlimited-OCR.gguf",
        "mmproj": LMS + "/baidu/Unlimited-OCR-GGUF/mmproj-Unlimited-OCR.gguf",
        # 實測（c01，同圖同設定）：
        #   <image>document parsing.  漢字   2  ← <image> 是 transformers 包裝器
        #                                         插的 token，走 OpenAI API 會干擾
        #   document parsing.         漢字 538
        #   Free OCR.                 漢字 665  ← 用這個
        "prompt": "Free OCR.",
    },
}
MODEL = MMPROJ = PROMPT = None       # 由 --model 在 main() 設定

_proc = None


def start_server():
    global _proc
    for f in (MODEL, MMPROJ):
        if not os.path.exists(f):
            sys.exit("缺檔案：%s" % f)
    cmd = ["llama-server", "-m", MODEL, "--mmproj", MMPROJ,
           "-ngl", "99", "--host", "127.0.0.1", "--port", str(PORT),
           "--ctx-size", "16384", "-fa", "on"]
    print("啟動 llama-server …")
    _proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                             stderr=subprocess.STDOUT)
    atexit.register(stop_server)
    url = "http://127.0.0.1:%d/v1/models" % PORT
    for _ in range(180):
        try:
            if requests.get(url, timeout=2).status_code == 200:
                print("server 就緒")
                return
        except Exception:
            pass
        if _proc.poll() is not None:
            sys.exit("llama-server 意外結束（exit %s）" % _proc.returncode)
        time.sleep(1)
    sys.exit("llama-server 啟動逾時")


def stop_server():
    global _proc
    if _proc and _proc.poll() is None:
        try:
            _proc.terminate()
            _proc.wait(timeout=10)
        except Exception:
            _proc.kill()
        _proc = None


def encode(path, maxside):
    im = Image.open(path).convert("RGB")
    w, h = im.size
    s = maxside / max(w, h)
    if s < 1:
        im = im.resize((round(w * s), round(h * s)), Image.LANCZOS)
    b = io.BytesIO()
    im.save(b, "JPEG", quality=92)
    return base64.b64encode(b.getvalue()).decode(), im.size


def ask(b64, max_tokens, timeout):
    body = {
        "model": "hunyuan",
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": PROMPT},
            {"type": "image_url",
             "image_url": {"url": "data:image/jpeg;base64," + b64}}]}],
        # temperature 0 與 repeat_penalty 都是必要的，不是調參：
        # run_vlm.py:47 記著 —— 不加的話模型在營養表那種高度重複的版面
        # 會鎖進無限迴圈直到吃滿 max_tokens。
        "temperature": 0,
        "max_tokens": max_tokens,
        "repeat_penalty": 1.15,
    }
    r = requests.post("http://127.0.0.1:%d/v1/chat/completions" % PORT,
                      json=body, timeout=timeout)
    j = r.json()
    ch = (j.get("choices") or [{}])[0]
    msg = ch.get("message") or {}
    txt = msg.get("content") or msg.get("reasoning_content") or ""
    return txt, ch.get("finish_reason"), j.get("usage") or {}


def to_lines(md):
    """把 markdown 拆成「行」，對齊既有 preset 的形狀。

    表格列用空白接（不是直接去掉分隔線）——相鄰儲存格的數字黏成一串
    （4.2 / 10.5 → 4.210.5）正是營養層最怕的錯，Surya 那支也踩過。
    """
    out = []
    for ln in md.splitlines():
        s = ln.strip()
        if not s or re.fullmatch(r"[|\-: ]+", s):
            continue
        if s.startswith("|"):
            s = " ".join(c.strip() for c in s.strip("|").split("|") if c.strip())
        s = re.sub(r"^#+\s*|\*\*|`", "", s).strip()
        if s:
            out.append(s)
    return out


def cases(only):
    p = json.load(open(os.path.join(EVAL_ROOT, "cases.json"), encoding="utf-8"))
    rows = []
    for c in p["cases"]:
        cid = c["case_id"]
        if only and not any(cid.startswith(o) for o in only):
            continue
        rows.append((cid, c.get("category") or "", c.get("images") or [],
                     c.get("set_version")))
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="hunyuan", choices=sorted(MODELS))
    ap.add_argument("--out", default=None, help="預設同 --model")
    ap.add_argument("--maxside", type=int, default=2048)
    ap.add_argument("--max-tokens", type=int, default=3072)
    ap.add_argument("--timeout", type=int, default=300)
    ap.add_argument("--cases", nargs="*", default=None)
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    global MODEL, MMPROJ, PROMPT
    cfg = MODELS[a.model]
    MODEL, MMPROJ, PROMPT = cfg["model"], cfg["mmproj"], cfg["prompt"]
    a.out = a.out or a.model

    outdir = os.path.join(HERE, "out", a.out)
    os.makedirs(outdir, exist_ok=True)
    start_server()

    rows = cases(a.cases)
    print("共 %d 案 -> out/%s/" % (len(rows), a.out))
    for i, (cid, cat, imgs, sv) in enumerate(rows, 1):
        dst = os.path.join(outdir, cid + ".json")
        if os.path.exists(dst) and not a.force:
            print("[%d/%d] %-26s 已存在，略過" % (i, len(rows), cid))
            continue
        recs, note = [], []
        for rel in imgs:
            p = os.path.join(EVAL_ROOT, rel.replace("/", os.sep))
            if not os.path.exists(p):
                continue
            b64, size = encode(p, a.maxside)
            t0 = time.time()
            try:
                txt, fin, usage = ask(b64, a.max_tokens, a.timeout)
            except Exception as e:
                txt, fin, usage = "", "error:%s" % type(e).__name__, {}
            dt = time.time() - t0
            lines = to_lines(txt)
            if fin != "stop":
                note.append(fin)
            recs.append({"path": rel, "elapsed_s": round(dt, 2),
                         "n_lines": len(lines),
                         "lines": [{"text": t, "score": None, "box": None}
                                   for t in lines],
                         "vlm": {"finish": fin, "usage": usage,
                                 "sent_size": list(size), "raw_chars": len(txt)}})
        json.dump({"case_id": cid, "preset": a.out, "set_version": sv,
                   "category": cat, "images": recs},
                  open(dst, "w", encoding="utf-8"), ensure_ascii=False)
        n = sum(r["n_lines"] for r in recs)
        s = sum(r["elapsed_s"] for r in recs)
        cjk = sum(len(re.findall(r"[\u4e00-\u9fff]", l["text"]))
                  for r in recs for l in r["lines"])
        print("[%d/%d] %-26s %5.1fs %3d 行 %4d 漢字 %s"
              % (i, len(rows), cid, s, n, cjk, "｜".join(note)))
    stop_server()


if __name__ == "__main__":
    main()
