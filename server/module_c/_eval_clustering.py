"""
Module C 分群調校的評估工具（非正式管線的一部分）。

存在理由：分群為非決定性，單次執行的群數在實測中於 4~12 間跳動，
單跑一次無法判斷改動是好是壞。本工具做兩件事：

1. **重複量測**：每個設定跑 N 次，看指標分布而非單點。
2. **回歸測試**：檢查「應分開的事件是否被錯誤合併」。
   headline 指標（群數少、無重複名稱）會同時被「分裂不足」與「過度合併」
   兩種相反的錯誤滿足——過度合併甚至讓指標更好看。故必須有反向防線。
"""
import sys
import statistics
sys.path.insert(0, "/home/laihome/projects/FoodScanIoT/server")

from database import SessionLocal
import models
from module_c import clustering as CL

# 已知應「分屬不同事件」的兩組文件（人工確認）。
# 中聯油脂：上游供油苯駢芘污染，聯華為受波及之下游業者。
# 卡迪那：聯華自家洋芋片丙烯醯胺超標，屬其自身製程問題。
# 兩者若被歸入同一群 = 過度合併，屬嚴重錯誤（事件消失 + 角色歸屬扭曲）。
OIL = {20, 26, 29, 31, 33, 34}
ACRYL = {174, 175, 176, 177, 179}


def _run_once(carry_over: bool) -> dict:
    """跑一次分群，回傳指標。以 _cluster_batch 手動驅動以取得群組成員。"""
    db = SessionLocal()
    try:
        cands = (db.query(models.EventCandidate)
                 .filter(models.EventCandidate.status == "pending",
                         models.EventCandidate.producer_id == 5)
                 .order_by(models.EventCandidate.id).all())
        chunks = [cands[i:i + CL.MAX_BATCH] for i in range(0, len(cands), CL.MAX_BATCH)]

        known, kb = [], {}
        groups: dict[str, set] = {}
        failed = 0

        for ci, chunk in enumerate(chunks):
            cl, _ = CL._cluster_batch(chunk, known if carry_over else None, "聯華食品工業")
            if cl is None:
                failed += 1
                continue
            for g in cl:
                raw = g["cluster_id"]
                if raw in kb:
                    key = raw
                else:
                    key = f"c{ci}:{raw}"
                    if g["kind"] in ("incident", "compilation"):
                        e = {"key": key, "name": g["suggested_name"],
                             "kind": g["kind"], "reason": g["reason"]}
                        known.append(e)
                        kb[key] = e
                groups.setdefault(key, set()).update(g["candidate_ids"])

        # 回歸測試：任一群若同時含兩事件的文件 → 過度合併
        merged = any(
            (ids & OIL) and (ids & ACRYL) for ids in groups.values()
        )
        return {
            "clusters": len(groups),
            "failed": failed,
            "over_merged": merged,
            "max_n": max((len(v) for v in groups.values()), default=0),
        }
    finally:
        db.close()


def evaluate(carry_over: bool, repeat: int = 3) -> dict:
    runs = [_run_once(carry_over) for _ in range(repeat)]
    return {
        "carry_over": carry_over,
        "clusters": [r["clusters"] for r in runs],
        "median_clusters": statistics.median(r["clusters"] for r in runs),
        "failed_total": sum(r["failed"] for r in runs),
        "over_merged_runs": sum(r["over_merged"] for r in runs),
        "max_n": [r["max_n"] for r in runs],
    }


if __name__ == "__main__":
    import json
    rep = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    for co in (False, True):
        print(json.dumps(evaluate(co, rep), ensure_ascii=False))
