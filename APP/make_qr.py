#!/usr/bin/env python3
"""產生連到本機 Expo dev server 的 QR code（走 Tailscale）。

為什麼需要這支：遠端開發時不想每次都回主機看終端機的 QR。
而**那個網址是固定的**——不像 `expo start --tunnel` 每次換 ngrok URL，
Tailscale IP 不變，`exp://<ip>:8081` 就永遠一樣，所以 QR 產一次用到底。

⚠ **在本機產，不要用線上 QR 產生器。** 那等於把自己的 Tailscale IP
   （內網位址）送給第三方網站。

用法：
    python make_qr.py                    # 自動抓 Tailscale IP
    python make_qr.py --host johnlai-pc  # MagicDNS 名稱（換網路不用重產）
    python make_qr.py --port 8082

輸出 expo-qr.png，同時把網址印出來——Expo Go 有「Enter URL manually」，
不掃碼直接打那一串也行。
"""
import argparse
import os
import shutil
import subprocess
import sys

import qrcode

DEFAULT_PORT = 8081
# WinGet／官方安裝器的預設路徑。PATH 裡找不到時退回這裡。
TAILSCALE_EXE = r"C:\Program Files\Tailscale\tailscale.exe"


def tailscale_ip() -> str:
    """取本機的 Tailscale IPv4。取不到就回空字串，由呼叫端要求 --host。"""
    exe = shutil.which("tailscale") or (
        TAILSCALE_EXE if os.path.exists(TAILSCALE_EXE) else None)
    if not exe:
        return ""
    try:
        out = subprocess.run([exe, "ip", "-4"], capture_output=True,
                             text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return ""
    for line in out.stdout.splitlines():
        line = line.strip()
        # Tailscale 的位址一律在 100.64.0.0/10（CGNAT 保留段）。
        # 只取第一個：多個通常是 IPv6 或其他介面。
        if line.startswith("100."):
            return line
    return ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", help="Tailscale IP 或 MagicDNS 名稱；省略則自動偵測")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--out", default="expo-qr.png")
    a = ap.parse_args()

    host = a.host or tailscale_ip()
    if not host:
        sys.exit("抓不到 Tailscale IP。請確認 Tailscale 在執行，或用 --host 指定。")

    url = "exp://%s:%d" % (host, a.port)
    img = qrcode.make(url)
    img.save(a.out)

    print(url)
    print("→ %s" % os.path.abspath(a.out))
    print()
    print("手機那端：Tailscale 要連著同一個 tailnet，然後 Expo Go 掃這張圖，")
    print("或用「Enter URL manually」直接輸入上面那串——不掃碼也行。")
    print()
    print("主機這端要讓 Metro 對外報這個位址，否則 QR 會指到區網 IP：")
    print('    $env:REACT_NATIVE_PACKAGER_HOSTNAME = "%s"' % host)
    print("    npx expo start")


if __name__ == "__main__":
    main()
