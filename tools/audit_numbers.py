#!/usr/bin/env python3
"""把報告裡的每一個數字回溯到來源，查不到的標出來。

為什麼需要這支
------------
2026-09-14 協助查核成果報告時，發現錯誤有一個共同形狀：**從 vault 裡抓到一個
「看起來相關」的數字，套到另一個指標上**。

    74.7  原本是「添加物層 F1（改善前）」→ 被寫成「App 影像壓縮頻寬節省 74.7%」
    77.7  原本是「現行最大切分的覆蓋率」→ 被寫成「添加物 F1 77.7」
    0.745 原本是**被否決**的逐行分類器實驗 → 被寫成現行架構的效能
    0.86  原本是「鈉的 F1」→ 被寫成「PP-OCR 耗時 0.86 秒」

這不是筆誤，是**沒有確認數字的主詞**。79 KB 的文件人工核不完，但機器可以
先篩出「這個數字在來源裡出現時，旁邊的字是什麼」，讓人一眼看出主詞對不對。

⚠ 這支**不判斷對錯**，只做三件事：
   1. 抽出報告中的數字
   2. 到 vault 與程式碼裡找同樣的數字，附上它在來源的上下文
   3. 標出「查無來源」的

判斷仍然是人的事——但有了上下文，判斷只要幾秒鐘。

用法：
    python tools/audit_numbers.py --report "G:/.../報告.md" --vault "G:/.../專案管理"
"""
import argparse
import io
import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 排版與書目雜訊。第一版沒濾，40 個「查無來源」裡有 21 個是圖片尺寸
# （width="3.89in"）與參考文獻頁碼（pp. 2861–2875）——看起來像捏造的量測表，
# 其實無害。濾掉之後才看得到真正的問題。
NOISE = ("image", "width=", "height=", "pp.", "vol.", "no.", "arXiv", "doi",
         "RAM", "RTX", "Ryzen", "Ubuntu", "http", "計畫編號")

# 太常見的數字不值得查（年份、章節號、百分比的 100、小整數）
SKIP = {"0", "1", "2", "3", "4", "5", "6", "7", "8", "9", "10", "100", "1000",
        "2023", "2024", "2025", "2026", "12", "24", "11", "20", "30", "50"}
NUM = re.compile(r"\d+(?:[.,]\d+)*%?")


def ctx(line, num, width=46):
    """數字在該行的上下文。看主詞對不對全靠這個。"""
    i = line.find(num)
    if i < 0:
        return line.strip()[:width * 2]
    return line[max(0, i - width):i + len(num) + width].strip()


def collect_sources(dirs):
    """回傳 {數字: [(來源檔, 行號, 上下文), ...]}"""
    src = {}
    for d in dirs:
        if not os.path.isdir(d):
            continue
        for base, _, fs in os.walk(d):
            if any(x in base for x in ("node_modules", "__pycache__", ".git",
                                       "out\\", "out/")):
                continue
            for f in fs:
                if not f.endswith((".md", ".py", ".ts", ".tsx", ".json")):
                    continue
                p = os.path.join(base, f)
                try:
                    text = io.open(p, encoding="utf-8", errors="replace").read()
                except OSError:
                    continue
                for n, line in enumerate(text.split("\n"), 1):
                    for m in NUM.findall(line):
                        key = m.rstrip("%")
                        if key in SKIP or len(key.replace(".", "").replace(",", "")) < 2:
                            continue
                        src.setdefault(key, []).append(
                            (os.path.relpath(p, os.path.dirname(d)), n, ctx(line, m)))
    return src


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", required=True)
    ap.add_argument("--vault", required=True, help="Obsidian 專案管理資料夾")
    ap.add_argument("--max-ctx", type=int, default=2, help="每個數字列幾條來源")
    a = ap.parse_args()

    report = io.open(a.report, encoding="utf-8", errors="replace").read()
    sources = collect_sources([a.vault, os.path.join(ROOT, "server"),
                               os.path.join(ROOT, "fog"),
                               os.path.join(ROOT, "APP", "src"),
                               os.path.join(ROOT, "tests")])

    # 報告裡的數字（去重，記第一次出現的行）
    seen = {}
    for n, line in enumerate(report.split("\n"), 1):
        if line.strip().startswith(("|---", "```")):
            continue
        for m in NUM.findall(line):
            key = m.rstrip("%")
            if key in SKIP or len(key.replace(".", "").replace(",", "")) < 2:
                continue
            seen.setdefault(key, []).append((n, ctx(line, m)))

    missing, found = [], []
    for key, uses in sorted(seen.items(), key=lambda kv: -len(kv[1])):
        # 逗號寫法（6,084）與純數字（6084）要一起找
        alts = {key, key.replace(",", "")}
        hits = []
        for alt in alts:
            hits += sources.get(alt, [])
        if not hits and all(any(n in c for n in NOISE) for _, c in uses):
            continue          # 排版／書目，不是量測值
        (found if hits else missing).append((key, uses, hits))

    print("# 報告數字稽核")
    print()
    print("報告：`%s`" % a.report)
    print("出現 %d 個待查數字；**查無來源 %d 個**。" % (len(seen), len(missing)))
    print()
    print("⚠ 本表不判斷對錯，只提供「這個數字在來源裡的上下文」。")
    print("　 主詞對不對要人看——過去的錯誤全部是主詞錯，不是數字錯。")
    print()

    print("## 查無來源（優先處理：報告有、vault 與程式碼都沒有）")
    print()
    if not missing:
        print("（無）")
    for key, uses, _ in missing:
        print("### `%s`　報告中 %d 處" % (key, len(uses)))
        for ln, c in uses[:3]:
            print("- 第 %d 行：%s" % (ln, c))
        print()

    print("## 有來源（請核對主詞是否一致）")
    print()
    for key, uses, hits in found:
        print("### `%s`　報告 %d 處 ／ 來源 %d 處" % (key, len(uses), len(hits)))
        print("**報告裡：**")
        for ln, c in uses[:2]:
            print("- 第 %d 行：%s" % (ln, c))
        print("**來源裡：**")
        for p, ln, c in hits[:a.max_ctx]:
            print("- `%s:%d`：%s" % (p, ln, c))
        print()


if __name__ == "__main__":
    main()
