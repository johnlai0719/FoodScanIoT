import json
from module_c.clustering import suggest_clusters
for pid, name in [(1,"統一企業"),(2,"義美食品"),(4,"味全食品工業")]:
    r = suggest_clusters(producer_id=pid)
    fc = r.get("failed_chunks", [])
    print(f"[{name}] {r['candidates']}筆 → {r.get('chunks')}塊 → {r.get('clusters')}群 | 失敗塊:{len(fc)}", flush=True)
    for f in fc:
        print(f"    塊{f['chunk']}({f['size']}筆)失敗: {f['last_error'][:60]}", flush=True)
print("DONE", flush=True)
