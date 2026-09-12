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
    },
    {
        "name": "qwen",
        "port": 8179,
        "model": os.path.join(LMS, "lmstudio-community", "Qwen3.5-2B-GGUF",
                              "Qwen3.5-2B-Q4_K_M.gguf"),
        # 品名／廠商是純文字任務（輸入是 OCR 文字，不是圖），
        # 但掛著 mmproj 不影響，留著讓這支也能當視覺對照組用。
        "mmproj": os.path.join(LMS, "lmstudio-community", "Qwen3.5-2B-GGUF",
                               "mmproj-Qwen3.5-2B-BF16.gguf"),
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
        if not os.path.exists(f):
            sys.exit("[%s] 缺檔案：%s" % (s["name"], f))
    cmd = ["llama-server", "-m", s["model"], "--mmproj", s["mmproj"],
           "-ngl", "99", "--host", "127.0.0.1", "--port", str(s["port"]),
           "--ctx-size", "16384", "-fa", "on", "--alias", s["name"]]
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
