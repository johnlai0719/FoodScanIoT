#!/usr/bin/env python3
# 抽取層的天花板：餵**完美的文字**（GT 的 ingredients_raw）給同一批抽取器，
# 看它們能到多少。
#
# 為什麼要量這個：現在整條鏈是「OCR 讀字 → 排行序 → 切成清單」。
# score_ocr.py 說 OCR 把 91.8% 的成分讀進文字裡了，bench_ingredients.py 說
# 只有 62.5% 進得了清單。中間掉的三十個百分點是「行序」還是「切分」，
# 這兩件事的修法完全不同——前者是工程（座標排序），後者要靠語言知識
# （「多磷酸鈉」是一項、「食鹽、水」是兩項，幾何規則不可能知道）。
#
# 把 OCR 與行序的變因整個拿掉，剩下的就是切分本身的上限。
# 如果餵完美文字還是只有七成，那再怎麼改 OCR 或行序都沒用，缺的是模型。
#
# 用法：python bench_ceiling.py
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import score_ocr as S          # noqa: E402
import bench_ingredients as B  # noqa: E402

# 只挑不依賴框座標的抽取器——boxseg / boxsep / block 都要 box，
# 餵純文字時它們無法運作。
TEXT_ONLY = ["naive", "bracket", "segment", "union", "filter", "hybrid", "dict"]


def fake_doc(raw):
    """把 GT 的 ingredients_raw 包成抽取器吃得下的形狀（單行、無框）。"""
    return {"images": [{"lines": [{"text": raw, "score": None, "box": None}]}]}


def load(preset):
    """preset=None 用 GT 的完美文字；否則讀 out/<preset>/ 的 OCR 輸出。"""
    HERE = os.path.dirname(os.path.abspath(__file__))
    cases = json.load(open(os.path.join(S.EVAL_ROOT, "cases.json"),
                           encoding="utf-8"))["cases"]
    data = []
    for c in cases:
        cid = c["case_id"]
        gt = S.load_gt(cid, c.get("category") or "")
        if not gt or not gt.get("ingredients_list"):
            continue
        if preset is None:
            if not gt.get("ingredients_raw"):
                continue
            d = fake_doc(gt["ingredients_raw"])
        else:
            p = os.path.join(HERE, "out", preset, f"{cid}.json")
            if not os.path.exists(p):
                continue
            d = json.load(open(p, encoding="utf-8"))
        data.append((cid, d, gt["ingredients_list"]))
    return data


def main():
    preset = sys.argv[1] if len(sys.argv) > 1 else None
    data = load(preset)
    src = ("GT 的 ingredients_raw（完美文字，無 OCR 誤差、無行序問題）"
           if preset is None else f"out/{preset}/ 的 OCR 文字")

    gtn = sum(len(g) for _, _, g in data)
    print(f"案例 {len(data)}｜GT 成分共 {gtn} 項｜輸入＝{src}\n")
    print(f"{'抽取器':<14}{'命中':>6}{'GT':>6}{'抽出':>6}{'召回':>8}{'精確':>8}{'F1':>8}")
    for name in TEXT_ONLY:
        fn = getattr(B, f"p_{name}")
        hit = got = 0
        for cid, d, gl in data:
            try:
                items = fn(cid, d, {"ingredients_list": gl}) or []
            except Exception:
                items = []
            h, _, n, _, _ = B.score_one(items, gl)
            hit += h
            got += n
        rec = hit / gtn if gtn else 0
        pre = hit / got if got else 0
        f1 = 2 * rec * pre / (rec + pre) if rec + pre else 0
        print(f"{name:<14}{hit:>6}{gtn:>6}{got:>6}{rec:>8.1%}{pre:>8.1%}{f1:>8.1%}")

    print("\n對照：同一批抽取器吃 PP-OCR 文字時，最好的是 dict F1 67.6%"
          "（見 bench_ingredients.py）")


if __name__ == "__main__":
    main()
