#!/usr/bin/env python3
# 與 run_baseline_dict.py 同一個形狀，只是把逐位置替換換成**視窗重評分**。
# 差別見 lex_rescore.py 檔頭：後者能在一個視窗內同時改多個字，
# 前者一次只能改一個，兩字同時錯讀的詞救不到。
#
# 所有替換都記到 out/_lex_fixes.json——這是必要的，
# 純繁體那次的教訓是「指標沒動不等於沒有傷害」。
#
# 用法：
#   python run_baseline_lex.py                      # 預設 MAX_LOSS
#   python run_baseline_lex.py --loss=2.0           # 掃描旋鈕
#   python score_ocr.py --preset=v6_best_lex
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import lex_rescore   # noqa: E402

PRESET = "v6_best"

if __name__ == "__main__":
    loss = None
    argv = []
    for a in sys.argv:
        if a.startswith("--loss="):
            loss = float(a.split("=", 1)[1])
        else:
            argv.append(a)
    sys.argv = argv
    if loss is not None:
        lex_rescore.MAX_LOSS = loss
    tag = ("%g" % lex_rescore.MAX_LOSS).replace(".", "")
    out_name = "v6_best_lex%s" % tag

    log = []
    lex_rescore.install(with_traditional=True, log=log)
    import run_baseline as RB   # noqa: E402  必須在 install 之後

    if not any(a.startswith("--preset") for a in sys.argv):
        sys.argv += ["--preset=%s" % PRESET]
    if not any(a.startswith("--out-name") for a in sys.argv):
        sys.argv += ["--out-name=%s" % out_name]
    try:
        RB.main()
    finally:
        HERE = os.path.dirname(os.path.abspath(__file__))
        p = os.path.join(HERE, "out", "_lex_fixes_%s.json" % tag)
        json.dump(log, open(p, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
        print("\n詞典替換共 %d 筆 -> %s" % (len(log), p))
