"""
Module C — 全廠商 Grounding 建檔（一次性批次執行）

用途：清掉切段修正前產生的舊事件，再以新版管線（多角度提問＋模型切段＋
語意去重）重跑指定廠商。逐家提交，中途中斷不會賠掉已完成的部分。

為何要刪舊資料而非逐筆修補：舊版靠比對排版格式切段，格式對不上時整篇文章
會被當成單一段落，而來源是以字元位置對應回去的——位置切錯即會把文章其他
段落的引用一併掃進來（實例：統一企業「2026 中聯油脂苯駢芘超標事件」掛上
的 7 筆來源全部是 2013 年的報導）。這類記錄的來源歸屬無法逐筆修正，重跑
較乾淨，且同時可取得多角度檢索的成果。
"""
import json
import os
import sys
import time

from sqlalchemy import text

from database import SessionLocal
import models
from module_c import entity_resolution
from module_c.grounding_discovery import discover_by_grounding

LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_run_all_grounding.log")


def purge(producer_ids: list[int]) -> tuple[int, list[int]]:
    """
    刪除指定廠商的已發布事件（含來源與關聯）。

    刻意保留「有人工審核紀錄連著」的事件：那類事件來自舊 Tavily 管線並經人工
    核可，其來源是各自獨立的文件、不涉及本次要修正的字元位置對應問題，且刪除
    會一併失去審核歷程。重跑時去重會擋住重複建檔，保留無害。

    回傳（實際刪除數, 因有審核紀錄而保留的事件 id）。
    """
    db = SessionLocal()
    try:
        ids = [e.event_id for e in db.query(models.EventManufacturer)
               .filter(models.EventManufacturer.producer_id.in_(producer_ids)).all()]
        if not ids:
            return 0, []
        reviewed = {r[0] for r in db.execute(
            text("SELECT DISTINCT event_id FROM event_candidates WHERE event_id IS NOT NULL")).fetchall()}
        keep = sorted(set(ids) & reviewed)
        ids = [i for i in ids if i not in reviewed]
        if not ids:
            return 0, keep
        db.query(models.EventSource).filter(models.EventSource.event_id.in_(ids)).delete(synchronize_session=False)
        db.query(models.EventManufacturer).filter(
            models.EventManufacturer.event_id.in_(ids)).delete(synchronize_session=False)
        db.query(models.Event).filter(models.Event.id.in_(ids)).delete(synchronize_session=False)
        db.commit()
        return len(ids), keep
    finally:
        db.close()


def log(msg: str):
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def main(purge_ids: list[int], run_ids: list[int] | None):
    if purge_ids:
        n, kept = purge(purge_ids)
        log(f"已清除舊事件 {n} 筆（廠商 {purge_ids}）"
            + (f"；保留有人工審核紀錄者 {kept}" if kept else ""))

    all_m = {m["producer_id"]: m["canonical_name"] for m in entity_resolution.all_manufacturers()}
    targets = run_ids if run_ids else sorted(all_m)

    summary = []
    for pid in targets:
        name = all_m.get(pid)
        if not name:
            continue
        t0 = time.time()
        try:
            r = discover_by_grounding(pid, name)
        except Exception as e:
            log(f"{name}  ERROR: {str(e)[:150]}")
            continue
        log(f"{name:<12} 切出 {r.get('events_found', 0):>3} | 發布 {r.get('published', 0):>2} | "
            f"重複 {r.get('skipped_duplicate', 0):>2} 非本廠商 {r.get('skipped_not_target', 0):>2} "
            f"主體不清 {r.get('skipped_ambiguous', 0):>2} 無來源 {r.get('skipped_no_sources', 0):>2} "
            f"| {time.time() - t0:.0f}s")
        for d in r.get("details", []):
            if d["status"] == "published":
                log(f"    ✓ {d['event_name'][:56]}  ({d.get('start_date')}, {d.get('source_count')} 來源)")
        summary.append(r)

    with open(LOG.replace(".log", "_result.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    log(f"完成：{len(summary)} 家，共發布 {sum(s.get('published', 0) for s in summary)} 筆")


if __name__ == "__main__":
    purge_arg, run_arg = [], None
    for a in sys.argv[1:]:
        if a.startswith("--purge="):
            purge_arg = [int(x) for x in a.split("=", 1)[1].split(",") if x]
        elif a.startswith("--run="):
            run_arg = [int(x) for x in a.split("=", 1)[1].split(",") if x]
    main(purge_arg, run_arg)
