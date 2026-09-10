#!/usr/bin/env python3
# 用 run_baseline.py 的同一組設定跑推論，差別是解碼時加上純繁體遮罩＋字典約束。
# 所有替換都記到 out/_dict_fixes.json，可以逐筆檢查改了什麼——
# 這是必要的：純繁體那次的教訓是「指標沒動不等於沒有傷害」。
#
# 用法：
#   python run_baseline_dict.py
#   python score_ocr.py --preset=v6_best_dict
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import dict_decode   # noqa: E402

PRESET = "v6_best"
OUT_NAME = "v6_best_dict"

if __name__ == "__main__":
    log = []
    dict_decode.install(with_traditional=True, log=log)
    import run_baseline as RB   # noqa: E402  必須在 install 之後

    if not any(a.startswith("--preset") for a in sys.argv):
        sys.argv += [f"--preset={PRESET}"]
    if not any(a.startswith("--out-name") for a in sys.argv):
        sys.argv += [f"--out-name={OUT_NAME}"]
    try:
        RB.main()
    finally:
        HERE = os.path.dirname(os.path.abspath(__file__))
        p = os.path.join(HERE, "out", "_dict_fixes.json")
        json.dump(log, open(p, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
        print(f"\n字典替換共 {len(log)} 筆 -> {p}")
