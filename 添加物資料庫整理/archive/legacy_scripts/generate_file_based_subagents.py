import os
import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
TASKS_DIR = PROJECT_ROOT / "添加物資料庫整理" / "03_enrichment補充" / "adi_run_20260618" / "subagent_tasks"
COMBINED_TASKS_DIR = PROJECT_ROOT / "添加物資料庫整理" / "03_enrichment補充" / "adi_run_20260618" / "combined_tasks"
IO_DIR = PROJECT_ROOT / "添加物資料庫整理" / "03_enrichment補充" / "adi_run_20260618" / "subagent_io"

def main():
    COMBINED_TASKS_DIR.mkdir(exist_ok=True)
    
    files = list(TASKS_DIR.glob("*.json"))
    files.sort()
    
    pending_records = []
    for p in files:
        rid = p.stem
        # Skip if already processed in subagent_io
        if (IO_DIR / f"{rid}.json").exists():
            continue
            
        with open(p, "r", encoding="utf-8") as f:
            task = json.load(f)
            
        pending_records.append(task)
        
    print(f"Found {len(pending_records)} pending records.")
    
    # Group pending records in chunks of 5
    chunk_size = 5
    chunks = [pending_records[i:i + chunk_size] for i in range(0, len(pending_records), chunk_size)]
    
    subagents = []
    for chunk in chunks:
        rids = [r["record_id"] for r in chunk]
        role_name = f"Combined_{rids[0]}_{rids[-1]}"
        
        # Save combined task file
        combined_task_file = COMBINED_TASKS_DIR / f"{role_name}.json"
        with open(combined_task_file, "w", encoding="utf-8") as f:
            json.dump({"role": role_name, "rids": rids}, f, ensure_ascii=False, indent=2)
            
        prompt = (
            f"你是食品安全與添加物資料稽核員。請使用 view_file 工具讀取檔案 `/home/laihome/projects/FoodScanIoT/添加物資料庫整理/03_enrichment補充/adi_run_20260618/combined_tasks/{role_name}.json`，找到其中的 `rids` 陣列。\n"
            f"請依序讀取每個 record_id 對應的任務檔案 `/home/laihome/projects/FoodScanIoT/添加物資料庫整理/03_enrichment補充/adi_run_20260618/subagent_tasks/{{rid}}.json`，並對每個檔案執行以下步驟：\n"
            f"1. 針對 `pass1_tasks` 中的每一筆來源內容，判斷是否實質討論該添加物的毒理/安全/代謝/健康效應。若是判定 relevant=true，否則 relevant=false，並給出10字以內的 reason。若該 task 有 auto_keep=true，請直接設定 relevant=true, reason=\"標題明示相關\"。\n"
            f"2. 針對 `pass2_task`，從其 `results` 內容中擷取該添加物的 ADI。若有 ADI 則原樣保留（含單位），並附上其 assessment_body (如 JECFA/EFSA 等) 與出處 sources 陣列。若查無或沒有 ADI 資訊，請回傳 adi=\"unknown\", assessment_body=\"unknown\", sources=[]。\n\n"
            f"請將所有添加物的結果整合為一個 JSON 物件回傳，以 record_id 作為 key。只回 JSON，不要有任何 Markdown 語法（如 ```json ... ```）或解釋。\n\n"
            f"回傳 JSON 格式範例：\n"
            f"{{\n"
            f"  \"{rids[0]}\": {{\n"
            f"    \"pass1_responses\": [\n"
            f"      {{\"relevant\": true, \"reason\": \"討論安全\"}}\n"
            f"    ],\n"
            f"    \"pass2_response\": {{\n"
            f"      \"adi\": \"0-5 mg/kg bw\",\n"
            f"      \"assessment_body\": \"JECFA\",\n"
            f"      \"sources\": [\"https://...\"]\n"
            f"    }}\n"
            f"  }}\n"
            f"}}"
        )
        
        subagents.append({
            "TypeName": "food_safety_subagent",
            "Role": role_name,
            "Prompt": prompt
        })
        
    batches_dir = Path(__file__).resolve().parent / "batches"
    batches_dir.mkdir(exist_ok=True)
    
    # Clean up old batches
    for f in batches_dir.glob("*.json"):
        f.unlink()
        
    # Split into batches of 15 concurrent subagents
    batch_chunk_size = 15
    batch_chunks = [subagents[i:i + batch_chunk_size] for i in range(0, len(subagents), batch_chunk_size)]
    
    for idx, batch_chunk in enumerate(batch_chunks):
        out_path = batches_dir / f"batch_{idx}.json"
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(batch_chunk, f, ensure_ascii=False, indent=2)
            
    print(f"Wrote {len(subagents)} combined subagents into {len(batch_chunks)} batch files under 'batches/'")

if __name__ == "__main__":
    main()
