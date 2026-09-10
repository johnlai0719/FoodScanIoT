#!/usr/bin/env python3
# 用 run_baseline.py 的同一組設定跑推論，唯一差別是解碼時遮蔽簡體字位。
# 差異可直接歸因於 trad_decode，不會混進其他變因。
#
# 用法：
#   python run_baseline_zht.py                       # 預設 v6_hires__boxth0.4
#   python run_baseline_zht.py --cases c38 --limit=5
#   python score_ocr.py --compare v6_hires__boxth0.4 v6_hires__boxth0.4_zht
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import trad_decode   # noqa: E402

# v6_best 與 out/v6_hires__boxth0.4/ 是同一組設定（v6 + side 2048 + box_thresh 0.4），
# 後者是參數掃描時的 out-name。要跟現有基準線可比就得用這組。
PRESET = "v6_best"
OUT_NAME = "v6_best_zht"

if __name__ == "__main__":
    trad_decode.install()
    import run_baseline as RB   # noqa: E402  必須在 install 之後才 import

    if not any(a.startswith("--preset") for a in sys.argv):
        sys.argv += [f"--preset={PRESET}"]
    if not any(a.startswith("--out-name") for a in sys.argv):
        sys.argv += [f"--out-name={OUT_NAME}"]
    RB.main()
