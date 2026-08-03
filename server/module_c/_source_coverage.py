"""
Module C — 切段後的來源覆蓋率檢查（離線，讀 _captures/）

切得多不代表可用：管線規定「沒有可追溯引用來源的段落一律不發布」，所以真正
該量的是「切出來的事件裡，有幾件拿得到來源」。若模型切段切得細但字元範圍
對不上引用位置，覆蓋率會下降，那樣切再多也是白切。

本檔對同一批原始回應，分別用正則與模型切段，計算：
  - 切出的段數／件數
  - 其中有至少一個引用來源的比例
  - 平均每件的來源數
"""
import json
import os
import sys

from module_c.grounding_discovery import _split_events as split_regex
from module_c._split_compare import split_llm

CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_captures")


def sources_for(text: str, start: int, end: int, supports: list[dict]) -> set:
    """
    回傳字元範圍內所有引用到的來源索引（與正式管線同一套重疊判定）。

    引用位置是 UTF-8 位元組編號，需先把字元範圍換算成位元組範圍，
    詳見 grounding_discovery._sources_for_span 的說明。
    """
    if start < 0:
        return set()
    b_start = len(text[:start].encode("utf-8"))
    b_end = len(text[:end].encode("utf-8"))
    out = set()
    for s in supports:
        if s["start"] < b_end and s["end"] > b_start:
            out.update(s["chunks"])
    return out


def main(only_producer: str | None):
    agg = {"regex": [0, 0, 0], "llm": [0, 0, 0]}  # [段數, 有來源數, 來源總數]
    for fn in sorted(f for f in os.listdir(CACHE_DIR) if f.endswith(".json")):
        with open(os.path.join(CACHE_DIR, fn), encoding="utf-8") as f:
            cap = json.load(f)
        if only_producer and cap["producer"] != only_producer:
            continue
        text, supports = cap["text"], cap["supports"]
        row = {}
        for label, segs in (("regex", split_regex(text)), ("llm", split_llm(text))):
            n = len(segs)
            withsrc = 0
            total = 0
            for s in segs:
                srcs = sources_for(text, s.get("start", -1), s.get("end", -1), supports)
                if srcs:
                    withsrc += 1
                total += len(srcs)
            row[label] = (n, withsrc, total)
            agg[label][0] += n
            agg[label][1] += withsrc
            agg[label][2] += total
        print(f"{cap['producer']:8s}/{cap['angle']:11s} "
              f"正則 {row['regex'][0]:2d}段 有來源{row['regex'][1]:2d} | "
              f"模型 {row['llm'][0]:2d}件 有來源{row['llm'][1]:2d}")

    print("\n=== 合計 ===")
    for label, name in (("regex", "正則切段"), ("llm", "模型切段")):
        n, w, t = agg[label]
        pct = (w / n * 100) if n else 0
        avg = (t / w) if w else 0
        print(f"{name}: {n} 件，其中 {w} 件有來源（{pct:.0f}%），平均每件 {avg:.1f} 個來源")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else None)
