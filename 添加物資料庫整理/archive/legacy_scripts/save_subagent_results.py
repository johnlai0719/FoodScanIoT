import os
import sys
import json
import argparse
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
TASKS_DIR = PROJECT_ROOT / "添加物資料庫整理" / "03_enrichment補充" / "adi_run_20260618" / "subagent_tasks"
IO_DIR = PROJECT_ROOT / "添加物資料庫整理" / "03_enrichment補充" / "adi_run_20260618" / "subagent_io"

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--record-id", required=True)
    parser.add_argument("--results-json", required=True, help="JSON mapping role to response object or string")
    args = parser.parse_args()
    
    rid = args.record_id
    results = json.loads(args.results_json)
    
    task_file = TASKS_DIR / f"{rid}.json"
    if not task_file.exists():
        print(f"Error: task file not found at {task_file}")
        sys.exit(1)
        
    with open(task_file, "r", encoding="utf-8") as f:
        task_data = json.load(f)
        
    name_zh = task_data["name_zh"]
    name_en = task_data["name_en"]
    
    # 1. Compile PASS 1 responses
    pass1_subagent_responses = []
    for idx, t1 in enumerate(task_data.get("pass1_tasks", [])):
        url = t1["url"]
        content = t1["content"]
        role = f"Pass1_{rid}_{idx}"
        
        if t1.get("auto_keep"):
            prompt = "Auto-kept based on PubMed title relevance check."
            resp_obj = {"relevant": True, "reason": "標題明示相關"}
        else:
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
            
            resp_obj = results.get(role, {"relevant": True, "reason": "No response"})
            if isinstance(resp_obj, str):
                try:
                    resp_obj = json.loads(resp_obj)
                except Exception:
                    resp_obj = {"relevant": True, "reason": resp_obj}
                
        pass1_subagent_responses.append({
            "url": url,
            "prompt": prompt,
            "response": resp_obj
        })
        
    # 2. Compile PASS 2 response
    pass2_subagent_response = None
    p2 = task_data.get("pass2_task")
    if p2:
        results_str = json.dumps(p2["results"], ensure_ascii=False, indent=2)
        prompt = (
            f"你是 ADI 資料擷取員。以下是關於「{name_zh}（{name_en}）」的多個網頁內容（含各自 URL）。\n"
            f"規則：只從提供內容擷取、禁用自身知識、禁捏造；ADI 含單位原樣保留；必附出處 URL；"
            f"優先 JECFA/EFSA/FAO/衛福部；查無→adi=\"unknown\",sources=[]。只回 JSON：\n"
            f'{{"adi":"數值+單位 或 unknown","assessment_body":"JECFA/EFSA/...","sources":["url"]}}\n\n'
            f"內容：\n{results_str}"
        )
        
        role = f"Pass2_{rid}"
        resp_obj = results.get(role, {"adi": "unknown", "assessment_body": "unknown", "sources": []})
        if isinstance(resp_obj, str):
            try:
                resp_obj = json.loads(resp_obj)
            except Exception:
                resp_obj = {"adi": "unknown", "assessment_body": "unknown", "sources": [], "raw": resp_obj}
                
        pass2_subagent_response = {
            "prompt": prompt,
            "response": resp_obj
        }
        
    io_data = {
        "record_id": rid,
        "name_zh": name_zh,
        "name_en": name_en,
        "pass1_subagent_responses": pass1_subagent_responses,
        "pass2_subagent_response": pass2_subagent_response
    }
    
    output_file = IO_DIR / f"{rid}.json"
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(io_data, f, ensure_ascii=False, indent=2)
        
    print(f"✅ Successfully compiled and wrote: {output_file}")

if __name__ == "__main__":
    main()
