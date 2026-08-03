import json
import re
import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
TRANSCRIPT_PATH = Path("/home/laihome/.gemini/antigravity-cli/brain/32418c9a-367e-40eb-af6f-9bb4a64cde42/.system_generated/logs/transcript_full.jsonl")
TASKS_DIR = PROJECT_ROOT / "添加物資料庫整理" / "03_enrichment補充" / "adi_run_20260618" / "subagent_tasks"
IO_DIR = PROJECT_ROOT / "添加物資料庫整理" / "03_enrichment補充" / "adi_run_20260618" / "subagent_io"

def main():
    if not TRANSCRIPT_PATH.exists():
        print(f"Error: Transcript not found at {TRANSCRIPT_PATH}")
        return
        
    print("Parsing transcript...")
    
    # 1. Read transcript steps
    steps = []
    with open(TRANSCRIPT_PATH, "r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                steps.append(json.loads(line))
                
    # 2. Extract mappings of Role -> conversationId
    role_to_cid = {}
    cid_to_role = {}
    
    # We will match PLANNER_RESPONSE (tool_calls with invoke_subagent) and the subsequent INVOKE_SUBAGENT step
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
    
    # 3. Gather subagent response contents from messages
    cid_responses = {}
    for step in steps:
        if step.get("type") == "SYSTEM_MESSAGE":
            content = step.get("content", "")
            # Find [Message] lines
            matches = re.finditer(r"sender=([^\s]+)\s+priority=[^\s]+\s+content=(.*)", content)
            for m in matches:
                sender = m.group(1)
                msg_content = m.group(2).strip()
                if sender in cid_to_role:
                    cid_responses[sender] = msg_content
                    
    print(f"Collected {len(cid_responses)} responses from subagents.")
    
    # 4. Save results for completed records
    # Group roles by record ID
    record_roles = {}
    
    # Scan all task files to know what roles are expected for each record
    task_files = list(TASKS_DIR.glob("*.json"))
    for tf in task_files:
        with open(tf, "r", encoding="utf-8") as f:
            task_data = json.load(f)
            
        rid = task_data["record_id"]
        expected_roles = []
        
        # Pass 1 expectations
        for idx, t1 in enumerate(task_data.get("pass1_tasks", [])):
            if not t1.get("auto_keep"):
                expected_roles.append(f"Pass1_{rid}_{idx}")
                
        # Pass 2 expectation
        if task_data.get("pass2_task"):
            expected_roles.append(f"Pass2_{rid}")
            
        record_roles[rid] = {
            "expected": expected_roles,
            "name_zh": task_data["name_zh"],
            "name_en": task_data["name_en"],
            "task_data": task_data
        }
        
    records_saved = 0
    records_pending = 0
    
    for rid, info in record_roles.items():
        # Check if already in IO_DIR
        output_file = IO_DIR / f"{rid}.json"
        if output_file.exists():
            continue
            
        expected = info["expected"]
        if not expected:
            # No subagent tasks needed for this record (e.g. all auto-kept and no Pass 2 needed)
            # We can write an empty/auto-pass result in subagent_io
            write_empty_result(rid, info["name_zh"], info["name_en"], info["task_data"])
            records_saved += 1
            continue
            
        # Check if we have responses for all expected roles
        all_completed = True
        record_results = {}
        for role in expected:
            cid = role_to_cid.get(role)
            if not cid or cid not in cid_responses:
                all_completed = False
                break
            record_results[role] = cid_responses[cid]
            
        if rid == "ADD-0008":
            print("ADD-0008 expected:", expected)
            for role in expected:
                cid = role_to_cid.get(role)
                print(f"  role: {role}, cid: {cid}, in_responses: {cid in cid_responses if cid else False}")
                if cid and cid in cid_responses:
                    print(f"  response: {cid_responses[cid]}")

        if all_completed:
            # Compile and write
            write_record_results(rid, info["name_zh"], info["name_en"], info["task_data"], record_results)
            records_saved += 1
        else:
            records_pending += 1
            
    print(f"Processed results: {records_saved} records saved, {records_pending} records still pending.")

def write_empty_result(rid, name_zh, name_en, task_data):
    io_data = {
        "record_id": rid,
        "name_zh": name_zh,
        "name_en": name_en,
        "pass1_subagent_responses": [],
        "pass2_subagent_response": None
    }
    output_file = IO_DIR / f"{rid}.json"
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(io_data, f, ensure_ascii=False, indent=2)

def write_record_results(rid, name_zh, name_en, task_data, results):
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

if __name__ == "__main__":
    main()
