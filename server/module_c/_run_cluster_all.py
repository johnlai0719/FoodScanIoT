import json
from database import SessionLocal
import models
from module_c.clustering import suggest_clusters

db = SessionLocal()
pids = [pid for (pid,) in db.query(models.EventCandidate.producer_id)
        .filter(models.EventCandidate.status == "pending").distinct().all()]
pnames = {p.id: p.name for p in db.query(models.Producer).all()}
db.close()

out = []
for pid in pids:
    name = pnames.get(pid, str(pid))
    r = suggest_clusters(producer_id=pid)
    if r.get("rejected"):
        print(f"[{name}] ❌ 拒絕（重試後仍未過 grounding）", flush=True)
    elif r.get("error"):
        print(f"[{name}] ⚠️ {r['error'][:60]}", flush=True)
    else:
        kinds = {}
        for c in r.get("detail", []):
            kinds[c["kind"]] = kinds.get(c["kind"], 0) + 1
        print(f"[{name}] ✅ {r['candidates']}筆 → {r['clusters']}群 {kinds}", flush=True)
    out.append({"producer": name, "result": r})

with open("module_c/_cluster_result.json", "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=2)
print("ALL DONE", flush=True)
