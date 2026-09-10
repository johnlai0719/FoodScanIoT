#!/usr/bin/env python3
# 分流讀取器：成分區用 Google Vision 的文字，營養區維持 PP-OCR。
#
# 為什麼分流（都是量出來的，見 README）：
#   成分  Vision 沒讀到 34 vs PP-OCR 65；添加物層 F1 72.3 vs 70.0
#   營養  PP-OCR 702/878 vs Vision 588/878（Vision 會把並排的兩欄黏成一行，
#         解析器的「欄位名後接數值」樣板一碰到就崩）
# 這不是折衷：成分是一整段連續文字流，Vision 的整頁閱讀順序是優勢；
# 營養是多欄表格，逐框輸出才保得住欄的邊界。
#
# 這支只產生**成分側**的輸出。營養側不需要改任何東西——維持
# nutrition_pipeline.py 現況（fill 702/878），這正是分流的意義。
#
# 不需要額外的 API 呼叫：全圖的 Vision 結果已經有了（out/gvision/），
# 這裡只是用 region_crop 的成分區框把它濾一遍，把營養表、地址、
# 行銷文案排除在抽取器的輸入之外。
#
# 用法：
#   python run_split.py
#   python bench_ceiling.py split          # 清單層
#   python sim_match_preset.py v6_hires__boxth0.4 gvision split   # 添加物層
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import region_crop as RC   # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
PPOCR = "v6_hires__boxth0.4"
VISION = "gvision"
OUT = "split"


def center_in(box, region):
    b = RC.bbox(box)
    cx, cy = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
    return region[0] <= cx <= region[2] and region[1] <= cy <= region[3]


def main():
    outdir = os.path.join(HERE, "out", OUT)
    os.makedirs(outdir, exist_ok=True)
    pdir = os.path.join(HERE, "out", PPOCR)
    vdir = os.path.join(HERE, "out", VISION)

    done = kept = dropped = nobox = 0
    for fn in sorted(os.listdir(vdir)):
        if not fn.endswith(".json"):
            continue
        vp, pp = os.path.join(vdir, fn), os.path.join(pdir, fn)
        if not os.path.exists(pp):
            continue
        vrec = json.load(open(vp, encoding="utf-8"))
        prec = json.load(open(pp, encoding="utf-8"))
        pimgs = {im["path"]: im for im in prec["images"]}

        out = {"case_id": vrec["case_id"], "preset": OUT,
               "set_version": vrec.get("set_version"),
               "category": vrec.get("category"), "images": []}
        for vim in vrec["images"]:
            vlines = vim.get("lines") or []
            pim = pimgs.get(vim["path"])
            # 版面階段用 PP-OCR 的框：它反正都要跑（營養側要用），
            # 而且 region_crop 的門檻是照它的框調出來的。
            region = None
            if pim:
                region = RC.find_regions(pim.get("lines") or [])["ingredients"]
            if region is None:
                nobox += 1
                keep = vlines            # 抓不到成分區就整張都留，寧可多不可少
            else:
                keep = [l for l in vlines
                        if l.get("box") and center_in(l["box"], region)]
                dropped += len(vlines) - len(keep)
            kept += len(keep)
            out["images"].append({
                "path": vim["path"], "elapsed_s": vim.get("elapsed_s", 0),
                "n_lines": len(keep), "lines": keep,
                "region": [int(v) for v in region] if region else None,
            })
        json.dump(out, open(os.path.join(outdir, fn), "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
        done += 1

    print(f"完成 {done} 案 -> out/{OUT}/")
    print(f"  Vision 的行保留 {kept}、因不在成分區而濾掉 {dropped}"
          f"｜{nobox} 張圖抓不到成分區（整張保留）")
    print(f"\n清單層：  python bench_ceiling.py {OUT}")
    print(f"添加物層：python sim_match_preset.py {PPOCR} {VISION} {OUT}")


if __name__ == "__main__":
    main()
