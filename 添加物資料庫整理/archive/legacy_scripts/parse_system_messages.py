import json
import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
TRANSCRIPT_PATH = Path("/home/laihome/.gemini/antigravity-cli/brain/32418c9a-367e-40eb-af6f-9bb4a64cde42/.system_generated/logs/transcript_full.jsonl")
TASKS_DIR = PROJECT_ROOT / "添加物資料庫整理" / "03_enrichment補充" / "adi_run_20260618" / "subagent_tasks"
IO_DIR = PROJECT_ROOT / "添加物資料庫整理" / "03_enrichment補充" / "adi_run_20260618" / "subagent_io"
MSG_FILE = PROJECT_ROOT / "添加物資料庫整理" / "04_工具腳本" / "system_messages.txt"

def main():
    if not MSG_FILE.exists():
        print(f"Error: system_messages.txt not found at {MSG_FILE}")
        return
        
    print("Parsing transcript for Role -> CID mapping...")
    
    # 1. Read transcript steps
    steps = []
    with open(TRANSCRIPT_PATH, "r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                steps.append(json.loads(line))
                
    # 2. Extract mappings of Role -> conversationId
    role_to_cid = {}
    cid_to_role = {}
    
    for i in range(len(steps) - 1):
        step = steps[i]
        next_step = steps[i+1]
        
        if step.get("type") == "PLANNER_RESPONSE" and step.get("tool_calls"):
            for tc in step["tool_calls"]:
                if tc.get("name") == "invoke_subagent":
                    args = tc.get("args", {})
                    subagents_arg = args.get("Subagents", [])
                    if isinstance(subagents_arg, str):
                        try:
                            subagents_arg = json.loads(subagents_arg)
                        except Exception:
                            continue
                            
                    # Find matching INVOKE_SUBAGENT output
                    if next_step.get("type") == "INVOKE_SUBAGENT":
                        content = next_step.get("content", "")
                        cids = re.findall(r'"conversationId":\s*"([a-f0-9\-]+)"', content)
                        
                        # Map them 1-to-1 in order
                        for sub_idx, cid in enumerate(cids):
                            if sub_idx < len(subagents_arg):
                                role = subagents_arg[sub_idx].get("Role")
                                if role:
                                    role_to_cid[role] = cid
                                    cid_to_role[cid] = role
                                    
    print(f"Mapped {len(role_to_cid)} subagent Roles to conversation IDs.")
    
    # 3. Read system_messages.txt and parse CID responses
    print("Parsing system_messages.txt...")
    cid_responses = {}
    with open(MSG_FILE, "r", encoding="utf-8") as f:
        content = f.read()
        
    matches = re.finditer(r"sender=([a-f0-9\-]+)\s+priority=[^\s]+\s+content=(.*)", content)
    for m in matches:
        sender = m.group(1)
        msg_content = m.group(2).strip()
        if sender in cid_to_role:
            cid_responses[sender] = msg_content
            
    print(f"Parsed {len(cid_responses)} responses from system_messages.txt.")
    
    # Process combined results
    records_saved = 0
    
    for role, cid in role_to_cid.items():
        if not role.startswith("Combined_"):
            # Skip old/other roles
            continue
            
        resp_str = cid_responses.get(cid)
        if not resp_str:
            # Not completed yet
            continue
            
        try:
            resp_obj = json.loads(resp_str)
        except Exception as e:
            print(f"Warning: Failed to parse JSON response for role {role} (CID: {cid}): {e}")
            continue
            
        for rid, record_res in resp_obj.items():
            output_file = IO_DIR / f"{rid}.json"
            if output_file.exists():
                continue
                
            task_file = TASKS_DIR / f"{rid}.json"
            if not task_file.exists():
                print(f"Warning: Task file {task_file} not found. Skipping.")
                continue
                
            with open(task_file, "r", encoding="utf-8") as f:
                task_data = json.load(f)
                
            # Build pass1 responses
            pass1_subagent_responses = []
            pass1_responses_list = record_res.get("pass1_responses", [])
            
            for idx, t1 in enumerate(task_data.get("pass1_tasks", [])):
                url = t1["url"]
                content = t1["content"]
                name_zh = task_data["name_zh"]
                name_en = task_data["name_en"] or ""
                
                if t1.get("auto_keep"):
                    prompt = "Auto-kept based on PubMed title relevance check."
                    resp_val = {"relevant": True, "reason": "標題明示相關"}
                else:
                    if t1.get("source_type") == "pubmed":
                        prompt = (
                            f"你是食品添加物資料來源稽核員。以下是某筆 PubMed 論文的標題與摘要。\n"
                            f"請判斷該論文是否實質討論此添加物「{name_zh}（{name_en}）」的毒理/安全/代謝/健康效應。\n"
                            f"規則：\n"
                            f"1. 只要該內容實質討論此添加物的毒理、安全、代謝、健康效應就判定為 relevant=true，不要求必須含有 ADI 或法規字眼。\n"
                            f"2. 只在「明顯是不同物質」或「明顯離題（如畜牧增產、飼料效益、工業製程、化學合成）」時，才判定為 relevant=false。\n"
                            f"3. 只回 JSON，不要有任何 Markdown 語法或解釋：\n"
                            f'{{"relevant":true/false,"reason":"10字內"}}'
                        )
                    else:
                        prompt = (
                            f"你是食品添加物資料來源稽核員。以下是某筆來源網頁內容，判斷是否與「{name_zh}"
                            f"（{name_en}）」直接相關且含安全/法規/ADI/用途資訊。只回 JSON：\n"
                            f'{{"relevant":true/false,"reason":"10字內"}}\n\n'
                            f"網頁內容：\n{content}"
                        )
                    
                    # Get corresponding answer from subagent's list
                    if idx < len(pass1_responses_list):
                        resp_val = pass1_responses_list[idx]
                    else:
                        resp_val = {"relevant": True, "reason": "No response"}
                        
                pass1_subagent_responses.append({
                    "url": url,
                    "prompt": prompt,
                    "response": resp_val
                })
                
            # Build pass2 response
            pass2_subagent_response = None
            p2 = task_data.get("pass2_task")
            if p2:
                name_zh = task_data["name_zh"]
                name_en = task_data["name_en"] or ""
                prompt = (
                    f"你是 ADI 資料擷取員。請使用 view_file 工具讀取檔案 "
                    f"`/home/laihome/projects/FoodScanIoT/添加物資料庫整理/03_enrichment補充/adi_run_20260618/subagent_tasks/{rid}.json`，"
                    f"並從 `pass2_task` 的 `results` 中擷取「{name_zh}（{name_en}）」的 ADI。\n"
                    f"規則：\n"
                    f"1. 只從提供內容擷取、禁用自身知識、禁捏造。\n"
                    f"2. ADI 含單位原樣保留；優先 JECFA/EFSA/FAO/衛福部。\n"
                    f"3. 查無則回傳 adi=\"unknown\", sources=[]。\n"
                    f"4. 只回 JSON，不要有任何 Markdown 語法或解釋：\n"
                    f'{{"adi":"數值+單位 或 unknown","assessment_body":"JECFA/EFSA/...","sources":["url"]}}'
                )
                
                resp_val = record_res.get("pass2_response", {"adi": "unknown", "assessment_body": "unknown", "sources": []})
                pass2_subagent_response = {
                    "prompt": prompt,
                    "response": resp_val
                }
                
            io_data = {
                "record_id": rid,
                "name_zh": task_data["name_zh"],
                "name_en": task_data["name_en"],
                "pass1_subagent_responses": pass1_subagent_responses,
                "pass2_subagent_response": pass2_subagent_response
            }
            
            with open(output_file, "w", encoding="utf-8") as f:
                json.dump(io_data, f, ensure_ascii=False, indent=2)
            records_saved += 1
            
    # Count pending
    all_task_files = list(TASKS_DIR.glob("*.json"))
    pending_count = 0
    for tf in all_task_files:
        rid = tf.stem
        if not (IO_DIR / f"{rid}.json").exists():
            pending_count += 1
            
    print(f"Processed combined results: {records_saved} new records saved, {pending_count} records still pending.")

if __name__ == "__main__":
    main()
