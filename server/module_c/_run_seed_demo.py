import json
from module_c.seed_generation import run_seed

DEMO = [(1, "統一企業"), (2, "義美食品"), (3, "太古可口可樂"),
        (4, "味全食品工業"), (5, "聯華食品工業"), (14, "味丹企業"), (18, "奇美食品")]

out = []
for pid, name in DEMO:
    r = run_seed(pid, name)
    out.append(r)
    print(f"[done] {name}: 生成{r['hypotheses_generated']} 存活{len(r['verified'])} 新增{r['total_new_candidates']}", flush=True)

with open("module_c/_seed_demo_result.json", "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=2)
print("ALL DONE", flush=True)
