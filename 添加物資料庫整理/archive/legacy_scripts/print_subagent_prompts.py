import os
import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
TASKS_DIR = PROJECT_ROOT / "添加物資料庫整理" / "03_enrichment補充" / "adi_run_20260618" / "subagent_tasks"
IO_DIR = PROJECT_ROOT / "添加物資料庫整理" / "03_enrichment補充" / "adi_run_20260618" / "subagent_io"

def main():
    files = list(TASKS_DIR.glob("*.json"))
    files.sort()
    
    subagents = []
    
    for p in files:
        with open(p, "r", encoding="utf-8") as f:
            task = json.load(f)
            
        rid = task["record_id"]
        
        # Skip if already processed in subagent_io
        if (IO_DIR / f"{rid}.json").exists():
            continue
            
        name_zh = task["name_zh"]
        name_en = task["name_en"] or ""
        
        # Pass 1 tasks
        for idx, t1 in enumerate(task.get("pass1_tasks", [])):
            url = t1["url"]
            content = t1["content"]
            
            # Skip subagent call if it is auto_keep (auto-approved locally)
            if t1.get("auto_keep"):
                continue
                
            if t1.get("source_type") == "pubmed":
                prompt = (
                    f"你是食品添加物資料來源稽核員。以下是某筆 PubMed 論文的標題與摘要。\n"
                    f"請判斷該論文是否實質討論此添加物「{name_zh}（{name_en}）」的毒理/安全/代謝/健康效應。\n"
                    f"規則：\n"
                    f"1. 只要該論文實質討論此添加物的毒理、安全、代謝、健康效應（包括動物實驗的毒理或安全性評估）就判定為 relevant=true，不要求必須含有 ADI 或法規字眼。\n"
                    f"2. 只在「明顯是不同物質」或「明顯離題（如畜牧增產、飼料效益、工業製程、化學合成）」時，才判定為 relevant=false。\n"
                    f"3. 只回 JSON：{{\"relevant\":true/false,\"reason\":\"10字內\"}}\n\n"
                    f"網頁內容：\n{content}"
                )
            else:
                prompt = (
                    f"你是食品添加物資料來源稽核員。以下是某筆來源網頁內容，判斷是否與「{name_zh}"
                    f"（{name_en}）」直接相關且含安全/法規/ADI/用途資訊。只回 JSON：\n"
                    f'{{"relevant":true/false,"reason":"10字內"}}\n\n'
                    f"網頁內容：\n{content}"
                )
            
            subagents.append({
                "TypeName": "food_safety_subagent",
                "Role": f"Pass1_{rid}_{idx}",
                "Prompt": prompt,
                "metadata": {
                    "record_id": rid,
                    "type": "pass1",
                    "url": url
                }
            })
            
        # Pass 2 task
        p2 = task.get("pass2_task")
        if p2:
            results = p2["results"]
            results_str = json.dumps(results, ensure_ascii=False, indent=2)
            
            prompt = (
                f"你是 ADI 資料擷取員。以下是關於「{name_zh}（{name_en}）」的多個網頁內容（含各自 URL）。\n"
                f"規則：只從提供內容擷取、禁用自身知識、禁捏造；ADI 含單位原樣保留；必附出處 URL；"
                f"優先 JECFA/EFSA/FAO/衛福部；查無→adi=\"unknown\",sources=[]。只回 JSON：\n"
                f'{{"adi":"數值+單位 或 unknown","assessment_body":"JECFA/EFSA/...","sources":["url"]}}\n\n'
                f"內容：\n{results_str}"
            )
            
            subagents.append({
                "TypeName": "food_safety_subagent",
                "Role": f"Pass2_{rid}",
                "Prompt": prompt,
                "metadata": {
                    "record_id": rid,
                    "type": "pass2"
                }
            })
            
    with open("subagents_to_run.json", "w", encoding="utf-8") as f:
        json.dump(subagents, f, ensure_ascii=False, indent=2)
    print(f"Wrote {len(subagents)} subagents to subagents_to_run.json")

if __name__ == "__main__":
    main()
