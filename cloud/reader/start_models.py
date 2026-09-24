"""起 vlcrop 線上推論要用的兩個 llama-server，並等到它們就緒。

    HunyuanOCR-1.5  :8177   讀裁切區的文字與營養表
    Qwen3.5-2B      :8179   從 OCR 文字整理出品名與廠商

參數不是猜的，是從 `PPOCR_TEST/run_hunyuan.py:80` 抄過來的同一組
（`-ngl 99`、`--ctx-size 16384`、`-fa on`）——那是實驗時實際用的組態，
換了就不是同一個受測對象。

用法：
    python reader/start_models.py          # 前景，Ctrl-C 一起關掉
    python reader/start_models.py --check   # 只檢查有沒有在跑，不啟動

⚠ 8GB 的 4060 同時載這兩個會很緊。Hunyuan 先起、確認就緒再起 Qwen，
   失敗時至少知道是哪一個吃不下。
"""
import argparse
import os
import subprocess
import sys
import time

import requests

LMS = os.environ.get("LMS_MODELS") or os.path.join(
    os.path.expanduser("~"), ".lmstudio", "models")

SERVERS = [
    {
        "name": "hunyuan",
        "port": 8177,
        "model": os.path.join(LMS, "tencent", "HunyuanOCR-1.5-GGUF",
                              "HunyuanOCR-1.5.gguf"),
        "mmproj": os.path.join(LMS, "tencent", "HunyuanOCR-1.5-GGUF",
                               "mmproj-HunyuanOCR-1.5.gguf"),
        # ctx 從評估時的 16384 降到 8192：我們送的是**裁切區**不是整頁，
        # 而 KV cache 是 VRAM 大戶。8GB 卡上 HunyuanOCR 只要擠不下就會把層
        # 搬回 CPU，3.5 秒變 29 秒（2026-09-13 實測）。
        # ⚠ 太小會截斷輸出：run_vlcrop 的 max_tokens 是 3072，加上視覺 token，
        #   8192 是量過還沒撞到 finish_reason=length 的值。再降要重新驗。
        "ctx": "8192",
        # 成分區與營養區沒有先後依賴，兩槽並行。decode 是記憶體頻寬受限，
        # 兩條序列一起跑幾乎免費；串著跑的話 7.38 秒有一半是在等。
        # ⚠ 這裡的 8192 會被切成 2×4096。2026-09-14 量到裁切區實際只用
        #   636／502 個 token（成分區／營養區），4096 仍遠超過
        #   max_tokens 3072 ＋ prompt 約 350，所以**不必調高 ctx**。
        "parallel": 2,
    },
    {
        "name": "qwen",
        "port": 8179,
        "model": os.path.join(LMS, "lmstudio-community", "Qwen3.5-2B-GGUF",
                              "Qwen3.5-2B-Q4_K_M.gguf"),
        # **不掛 mmproj。** 品名／廠商是純文字任務（輸入是 OCR 文字，不是圖），
        # 而 2026-09-13 實測掛著它就算 -ngl 0 仍會吃掉約 2GB VRAM——
        # 視覺投影層是獨立offload 的，不受 -ngl 管。
        "mmproj": None,
        # ⚠ **跑 CPU（-ngl 0）、context 開小。** 2026-09-13 實測：四個模型同時
        # 常駐時 VRAM 用到 7824/8188 MiB（95.5%），PP-OCR 從 0.77 秒被拖到
        # 6.5 秒、HunyuanOCR 從 10.3 秒被拖到 28 秒。評估時看不到這件事，
        # 因為 run_hunyuan.py 是自己起 server、跑完自己關，一次只有一個模型。
        # 這支的工作是純文字（輸入是 OCR 文字不是圖）且只佔 0.69 秒，
        # 是四個裡面最該讓出 VRAM 的。
        "ngl": "0",
        "ctx": "8192",
    },
]


def alive(port):
    try:
        return requests.get("http://127.0.0.1:%d/v1/models" % port,
                            timeout=2).status_code == 200
    except Exception:
        return False


def start(s):
    if alive(s["port"]):
        print("[%s] 已在 :%d 執行，略過" % (s["name"], s["port"]))
        return None
    for f in (s["model"], s["mmproj"]):
        if f and not os.path.exists(f):
            sys.exit("[%s] 缺檔案：%s" % (s["name"], f))
    cmd = ["llama-server", "-m", s["model"],
           "-ngl", s.get("ngl", "99"), "--host", "127.0.0.1",
           "--port", str(s["port"]),
           "--ctx-size", s.get("ctx", "16384"), "-fa", "on",
           "--alias", s["name"]]
    # 平行槽。**必須明確指定**——這個 build 的預設是 4 槽，而 --ctx-size 是
    # 所有槽共用的總量：8192÷4 = 2048，比 max_tokens 3072 還小，長一點的
    # 成分區會被截斷而且不會報錯。指定 2 槽時每槽 4096，才放得下。
    # 2026-09-14 量到裁切區實際只用 636／502 個 token，4096 綽綽有餘。
    if s.get("parallel"):
        cmd += ["-np", str(s["parallel"])]
    if s["mmproj"]:
        cmd[3:3] = ["--mmproj", s["mmproj"]]
    print("[%s] 啟動中 :%d …" % (s["name"], s["port"]))
    p = subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                         stderr=subprocess.STDOUT)
    for _ in range(180):
        if alive(s["port"]):
            print("[%s] 就緒" % s["name"])
            return p
        if p.poll() is not None:
            sys.exit("[%s] llama-server 意外結束（exit %s）"
                     % (s["name"], p.returncode))
        time.sleep(1)
    sys.exit("[%s] 啟動逾時（180 秒）" % s["name"])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只檢查，不啟動")
    a = ap.parse_args()

    if a.check:
        for s in SERVERS:
            print("%-8s :%d  %s" % (s["name"], s["port"],
                                    "up" if alive(s["port"]) else "DOWN"))
        return

    procs = [p for p in (start(s) for s in SERVERS) if p]
    if not procs:
        print("兩個都已在執行，沒有新起任何行程。")
        return
    print("\n都起來了。Ctrl-C 關閉本腳本啟動的那些。")
    try:
        while True:
            time.sleep(5)
            for s, p in zip(SERVERS, procs):
                if p.poll() is not None:
                    print("[%s] 已結束（exit %s）" % (s["name"], p.returncode))
                    return
    except KeyboardInterrupt:
        pass
    finally:
        for p in procs:
            p.terminate()


if __name__ == "__main__":
    main()
