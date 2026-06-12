import argparse
import json
import os
import re
import sys
from pathlib import Path

# Configure encoding for stdout
sys.stdout.reconfigure(encoding="utf-8")

ROOT_DIR = Path(__file__).resolve().parent.parent
DB_PATH = ROOT_DIR / "01_主資料庫/additives_tfda_clean.json"
GEMINI_PATCH_PATH = ROOT_DIR / "03_enrichment補充/gemini_patch.json"
NCBI_RAW_DIR = ROOT_DIR / "03_enrichment補充/ncbi_raw"
NCBI_PATCH_PATH = ROOT_DIR / "03_enrichment補充/ncbi_patch.json"
TASK_PATH = ROOT_DIR / "C:/Users/johnl/.gemini/antigravity/brain/8f11f930-9240-4d34-accb-451af13523f3/task.md"

# Wait, if TASK_PATH is absolute, let's make sure it handles Windows absolute path correctly:
if not Path(TASK_PATH).exists():
    # Try a relative path fallback or look in appData
    TASK_PATH = Path("C:/Users/johnl/.gemini/antigravity/brain/8f11f930-9240-4d34-accb-451af13523f3/task.md")

BLOCKLIST = re.compile(
    r"(\bADI\b|mg/kg|bw|NOAEL|PTWI|每日允許攝取量|\bJECFA\b|\bEFSA\b|\bFDA\b|ppm|%|\d\s*(?:mg|g|μg|ppm))",
    re.IGNORECASE
)

CONCERN_VALUES = {"caution", "avoid", "danger"}
CONFIDENCE_VALUES = {"verified", "research_supported", "public_concern", "ai_inferred", "no_data"}
SOURCE_TYPE_VALUES = {"official", "pubmed", "news", "none"}

def prepare_batch(batch_size):
    # Load files
    with open(DB_PATH, "r", encoding="utf-8") as f:
        db = json.load(f)
    with open(GEMINI_PATCH_PATH, "r", encoding="utf-8") as f:
        gemini_patch = {r["record_id"]: r for r in json.load(f)}
    
    if NCBI_PATCH_PATH.exists():
        with open(NCBI_PATCH_PATH, "r", encoding="utf-8") as f:
            ncbi_patch = {r["record_id"]: r for r in json.load(f)}
    else:
        ncbi_patch = {}

    # Find unsynthesized records
    unsynthesized = []
    for r in db:
        rid = r["record_id"]
        if rid not in ncbi_patch:
            unsynthesized.append(r)
            
    if not unsynthesized:
        print("All records have been synthesized! No batch to prepare.")
        return
        
    batch = unsynthesized[:batch_size]
    print(f"Preparing batch of {len(batch)} records (from {batch[0]['record_id']} to {batch[-1]['record_id']})...")
    
    batch_input = []
    for r in batch:
        rid = r["record_id"]
        gem_rec = gemini_patch.get(rid, {})
        
        # Load raw NCBI data
        raw_file = NCBI_RAW_DIR / f"{rid}.json"
        ncbi_raw = {}
        if raw_file.exists():
            with open(raw_file, "r", encoding="utf-8") as f:
                ncbi_raw = json.load(f)
                
        pubchem_tox = ncbi_raw.get("pubchem_toxicology", "")
        pubmed_papers = ncbi_raw.get("pubmed_papers", [])
        
        # Limit abstract lengths to prevent huge context
        formatted_papers = []
        for paper in pubmed_papers[:5]:
            formatted_papers.append({
                "pmid": paper.get("pmid", ""),
                "title": paper.get("title", ""),
                "abstract": paper.get("abstract", "")[:1200]
            })
            
        batch_input.append({
            "record_id": rid,
            "name_zh": r.get("name_zh"),
            "name_en": r.get("name_en"),
            "function_class": r.get("function_class", []),
            "usage_notes": r.get("usage_notes", ""),
            "consumer_description": gem_rec.get("consumer_description", ""),
            "overall_confidence": gem_rec.get("overall_confidence", "ai_inferred"),
            "pubchem_toxicology_summary": pubchem_tox[:3000],
            "pubmed_papers": formatted_papers
        })
        
    input_path = ROOT_DIR / "03_enrichment補充/batch_input.json"
    with open(input_path, "w", encoding="utf-8") as f:
        json.dump(batch_input, f, ensure_ascii=False, indent=2)
        
    print(f"✅ Successfully wrote batch input to: {input_path}")
    print(f"You can now run synthesis on this batch.")

def merge_batch():
    output_path = ROOT_DIR / "03_enrichment補充/batch_output.json"
    input_path = ROOT_DIR / "03_enrichment補充/batch_input.json"
    
    if not output_path.exists():
        print(f"Error: batch_output.json not found at {output_path}")
        sys.exit(1)
        
    with open(output_path, "r", encoding="utf-8") as f:
        batch_out = json.load(f)
        
    with open(GEMINI_PATCH_PATH, "r", encoding="utf-8") as f:
        gemini_patch = {r["record_id"]: r for r in json.load(f)}
        
    if NCBI_PATCH_PATH.exists():
        with open(NCBI_PATCH_PATH, "r", encoding="utf-8") as f:
            ncbi_patch = {r["record_id"]: r for r in json.load(f)}
    else:
        ncbi_patch = {}
        
    # Validation
    errors = 0
    validated_records = []
    
    for idx, rec in enumerate(batch_out):
        rid = rec.get("record_id")
        desc = rec.get("consumer_description", "")
        length = len(desc)
        
        # 1. Basic record structure
        if not rid:
            print(f"Error in batch item {idx}: Missing 'record_id'")
            errors += 1
            continue
            
        # 2. Length check
        if length < 80 or length > 150:
            print(f"Error in {rid}: Description length is {length} (must be 80-150): '{desc}'")
            errors += 1
            
        # 3. Blocklist check
        match = BLOCKLIST.search(desc)
        if match:
            print(f"Error in {rid}: Forbidden term/pattern '{match.group()}' found: '{desc}'")
            errors += 1
            
        # 4. Suffix check
        gem_rec = gemini_patch.get(rid, {})
        conf = gem_rec.get("overall_confidence", "ai_inferred")
        if conf == "ai_inferred" and not desc.endswith("（此為推論，僅供參考）"):
            print(f"Error in {rid}: Missing '（此為推論，僅供參考）' suffix for ai_inferred record: '{desc}'")
            errors += 1
            
        # 5. Group Risks check
        risks = rec.get("group_risks", [])
        if not isinstance(risks, list):
            print(f"Error in {rid}: 'group_risks' must be a list")
            errors += 1
        else:
            for r_idx, r in enumerate(risks):
                group = r.get("group")
                concern = r.get("concern")
                confidence = r.get("confidence")
                source_type = r.get("source_type")
                source_title = r.get("source_title")
                source_url = r.get("source_url")
                source_year = r.get("source_year")
                ai_reasoning = r.get("ai_reasoning")
                reviewed_by_human = r.get("reviewed_by_human")
                
                if not isinstance(group, str) or not group.strip():
                    print(f"Error in {rid} (risk {r_idx}): 'group' must be a non-empty string")
                    errors += 1
                if concern not in CONCERN_VALUES:
                    print(f"Error in {rid} (risk {r_idx}): 'concern' must be one of {CONCERN_VALUES}, got '{concern}'")
                    errors += 1
                if confidence not in CONFIDENCE_VALUES:
                    print(f"Error in {rid} (risk {r_idx}): 'confidence' must be one of {CONFIDENCE_VALUES}, got '{confidence}'")
                    errors += 1
                if source_type not in SOURCE_TYPE_VALUES:
                    print(f"Error in {rid} (risk {r_idx}): 'source_type' must be one of {SOURCE_TYPE_VALUES}, got '{source_type}'")
                    errors += 1
                if not isinstance(source_title, str):
                    print(f"Error in {rid} (risk {r_idx}): 'source_title' must be a string")
                    errors += 1
                if not isinstance(source_url, str):
                    print(f"Error in {rid} (risk {r_idx}): 'source_url' must be a string")
                    errors += 1
                elif source_type == "pubmed" and "pubmed.ncbi.nlm.nih.gov" not in source_url:
                    print(f"Error in {rid} (risk {r_idx}): source_type is 'pubmed' but 'source_url' does not contain 'pubmed.ncbi.nlm.nih.gov': '{source_url}'")
                    errors += 1
                if source_year is not None and not isinstance(source_year, int):
                    print(f"Error in {rid} (risk {r_idx}): 'source_year' must be an integer or null")
                    errors += 1
                if not isinstance(ai_reasoning, str) or not ai_reasoning.strip():
                    print(f"Error in {rid} (risk {r_idx}): 'ai_reasoning' must be a non-empty string")
                    errors += 1
                if not isinstance(reviewed_by_human, bool):
                    print(f"Error in {rid} (risk {r_idx}): 'reviewed_by_human' must be a boolean")
                    errors += 1
                    
        if errors == 0:
            validated_records.append(rec)
            
    if errors > 0:
        print(f"\n❌ Validation failed with {errors} errors. Merge aborted.")
        sys.exit(1)
        
    # Merge into ncbi_patch
    last_id = ""
    for rec in validated_records:
        rid = rec["record_id"]
        ncbi_patch[rid] = rec
        last_id = rid
        
    # Save back
    sorted_patch = [ncbi_patch[k] for k in sorted(ncbi_patch.keys())]
    with open(NCBI_PATCH_PATH, "w", encoding="utf-8") as f:
        json.dump(sorted_patch, f, ensure_ascii=False, indent=2)
        
    print(f"✅ Successfully validated and merged {len(validated_records)} records into ncbi_patch.json!")
    
    # Progress Insurance: Update task.md
    total_completed = len(sorted_patch)
    pct = (total_completed / 804.0) * 100.0
    
    update_task_file(total_completed, pct, last_id)
    
    # Clean up temp files
    if input_path.exists():
        os.remove(input_path)
    if output_path.exists():
        os.remove(output_path)
    print("🧹 Cleaned up temporary batch files.")

def update_task_file(completed, pct, last_id):
    if not TASK_PATH.exists():
        print(f"Warning: task.md not found at {TASK_PATH}")
        return
        
    with open(TASK_PATH, "r", encoding="utf-8") as f:
        lines = f.readlines()
        
    new_lines = []
    in_summary = False
    for line in lines:
        if line.startswith("## Progress Summary"):
            in_summary = True
            new_lines.append(line)
            continue
        if in_summary and line.startswith("## "):
            in_summary = False
            
        if in_summary:
            if "NCBI/PubChem Raw Fetching" in line:
                new_lines.append(line)
            elif "AI Synthesis" in line:
                # Format: - **AI Synthesis**: `[/]` 3.6% (29/804 records completed in `ncbi_patch.json`)
                status = "[x]" if completed == 804 else "[/]"
                new_lines.append(f"- **AI Synthesis**: `{status}` {pct:.1f}% ({completed}/804 records completed in `ncbi_patch.json`)\n")
            elif "Last Completed ID" in line:
                new_lines.append(f"  - Last Completed ID: `{last_id}`\n")
            elif "Remaining" in line:
                rem = 804 - completed
                next_start = completed + 1
                new_lines.append(f"  - Remaining: {rem} records (`ADD-{next_start:04d}` to `ADD-0804`)\n")
            else:
                new_lines.append(line)
        else:
            # Update specific batch checklist item
            # e.g., - `[ ]` Batch 1 (ADD-0030 to ADD-0059)
            # We can calculate which batches are completed
            # Batch 1 is 30 to 59. If completed >= 59 (since 1-29 are prepopulated? Wait, we reset ncbi_patch to 0!)
            # So if completed >= 30, Batch 1 (which processed first 30 records: ADD-0001 to ADD-0030) is complete.
            # Let's write a general parser for batch items
            match = re.search(r"-\s*`\[\s*\]` (Batch \d+)\s*\((ADD-\d+) to (ADD-\d+)\)", line)
            if match:
                batch_num = match.group(1)
                start_id = int(match.group(2).replace("ADD-", ""))
                end_id = int(match.group(3).replace("ADD-", ""))
                if completed >= end_id:
                    line = f"- `[x]` {batch_num} ({match.group(2)} to {match.group(3)})\n"
                elif completed >= start_id:
                    line = f"- `[/]` {batch_num} ({match.group(2)} to {match.group(3)})\n"
            new_lines.append(line)
            
    with open(TASK_PATH, "w", encoding="utf-8") as f:
        f.writelines(new_lines)
    print("📝 Updated task.md progress tracking.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["prepare", "merge"], help="Action to perform")
    parser.add_argument("--size", type=int, default=30, help="Batch size for preparation")
    args = parser.parse_args()
    
    if args.action == "prepare":
        prepare_batch(args.size)
    elif args.action == "merge":
        merge_batch()
