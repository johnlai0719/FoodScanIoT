import os
import sys
import re
import json
import time
import csv
import argparse
from pathlib import Path
import psycopg2
from dotenv import load_dotenv

# Reconfigure stdout to use UTF-8
sys.stdout.reconfigure(encoding="utf-8")

# Setup paths and load environment variables
CURRENT_FILE = Path(__file__).resolve()
PROJECT_ROOT = CURRENT_FILE.parent.parent.parent
ENV_PATH = PROJECT_ROOT / "server" / ".env"
load_dotenv(dotenv_path=ENV_PATH)

RUN_DIR = PROJECT_ROOT / "添加物資料庫整理" / "03_enrichment補充" / "adi_run_20260618"
TAVILY_RAW_DIR = RUN_DIR / "tavily_raw"
SUBAGENT_IO_DIR = RUN_DIR / "subagent_io"
SUBAGENT_TASKS_DIR = RUN_DIR / "subagent_tasks"

# Ensure directories exist
os.makedirs(RUN_DIR, exist_ok=True)
os.makedirs(TAVILY_RAW_DIR, exist_ok=True)
os.makedirs(SUBAGENT_IO_DIR, exist_ok=True)
os.makedirs(SUBAGENT_TASKS_DIR, exist_ok=True)

def get_db_connection():
    return psycopg2.connect(
        host=os.getenv("DB_HOST", "localhost"),
        port=os.getenv("DB_PORT", "5432"),
        database=os.getenv("DB_NAME", "product_db"),
        user=os.getenv("DB_USER", "postgres"),
        password=os.getenv("DB_PASSWORD", "")
    )

def normalize_text(text):
    if not text:
        return ""
    # Convert to lowercase and keep only alphanumeric characters
    return re.sub(r'[^a-z0-9]', '', text.lower())

def fetch_pubmed_abstract(pmid):
    import requests
    url = f"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?db=pubmed&id={pmid}&rettype=abstract&retmode=text"
    for attempt in range(3):
        try:
            resp = requests.get(url, timeout=10)
            if resp.status_code == 200 and resp.text.strip():
                return resp.text.strip()
            else:
                print(f"PubMed PMID {pmid} fetch status: {resp.status_code}")
        except Exception as e:
            print(f"PubMed PMID {pmid} fetch attempt {attempt + 1} failed: {e}")
        time.sleep(1)
    return None

def check_pubmed_title_relevance(title, name_zh, name_en):
    title_norm = title.lower()
    name_zh_norm = name_zh.strip() if name_zh else ""
    name_en_norm = name_en.strip().lower() if name_en else ""
    
    has_substance = False
    
    if name_zh_norm and name_zh_norm in title:
        has_substance = True
        
    if name_en_norm:
        if name_en_norm in title_norm:
            has_substance = True
        else:
            # Check significant words
            stop_words = {"acid", "sodium", "potassium", "calcium", "salt", "salts"}
            words = [w for w in name_en_norm.split() if w not in stop_words and len(w) > 3]
            for w in words:
                if w in title_norm:
                    has_substance = True
                    break
                    
    if not has_substance:
        return False
        
    # Strictly check for toxicology/safety indicators in title
    keywords = ["toxicology", "toxicological", "toxicity", "safety", "tolerability", "carcinogenicity", "mutagenicity"]
    for kw in keywords:
        if kw in title_norm:
            return True
            
    return False

def prepare_batch(limit, start_id, record_ids=None):
    from tavily import TavilyClient
    import requests
    from concurrent.futures import ThreadPoolExecutor, as_completed

    api_key = os.getenv("TAVILY_API_KEY")
    if not api_key:
        print("Error: TAVILY_API_KEY not found in server/.env")
        sys.exit(1)
    
    tavily_client = TavilyClient(api_key=api_key)
    conn = get_db_connection()
    cur = conn.cursor()

    # Query records
    cur.execute("SELECT record_id, name_zh, name_en, adi, description_sources FROM additives ORDER BY record_id")
    rows = cur.fetchall()

    # Filter records to process
    records_to_process = []
    started = False
    
    for row in rows:
        rid, name_zh, name_en, adi, description_sources = row
        
        # Filter by specific record_ids list if provided
        if record_ids:
            if rid not in record_ids:
                continue
        # Else filter by start_id and limit
        elif start_id:
            if rid == start_id:
                started = True
            if not started:
                continue
        else:
            started = True

        records_to_process.append({
            "record_id": rid,
            "name_zh": name_zh,
            "name_en": name_en,
            "adi": adi,
            "description_sources": description_sources or []
        })

        if not record_ids and limit and len(records_to_process) >= limit:
            break

    cur.close()
    conn.close()

    print(f"--- PREPARE ACTION ---")
    print(f"Found {len(records_to_process)} records to process.")

    def process_record(rec, idx_str):
        rid = rec["record_id"]
        name_zh = rec["name_zh"]
        name_en = rec["name_en"] or ""
        sources = rec["description_sources"]

        tavily_raw_data = {
            "record_id": rid,
            "name_zh": name_zh,
            "name_en": name_en,
            "pass1_pubchem_queries": [],
            "pass1_tavily_extract": None,
            "pass2_tavily_search": None
        }

        pubchem_removed = []
        pass1_tasks = []

        pubchem_urls = []
        pubmed_urls = []
        other_urls = []

        for url in sources:
            if "pubchem.ncbi.nlm.nih.gov" in url:
                pubchem_urls.append(url)
            elif "pubmed.ncbi.nlm.nih.gov" in url:
                pubmed_urls.append(url)
            else:
                other_urls.append(url)

        # PASS 1: (a) PubChem Link Validation
        for url in pubchem_urls:
            cid_match = re.search(r"pubchem\.ncbi\.nlm\.nih\.gov/compound/(\d+)", url)
            if not cid_match:
                pubchem_removed.append({
                    "url": url,
                    "reason": "Invalid PubChem URL format"
                })
                continue
            cid = cid_match.group(1)
            pug_url = f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/{cid}/property/Title/JSON"

            title = None
            response_json = None
            
            # PubChem API calls with 3 retries
            for attempt in range(3):
                try:
                    resp = requests.get(pug_url, timeout=10)
                    if resp.status_code == 200:
                        response_json = resp.json()
                        properties = response_json.get("PropertyTable", {}).get("Properties", [])
                        if properties:
                            title = properties[0].get("Title")
                        break
                    elif resp.status_code == 404:
                        response_json = {"error": "404 Not Found"}
                        break
                    else:
                        response_json = {"error": f"HTTP {resp.status_code}"}
                except Exception as e:
                    # Silent log to avoid flooding console
                    pass
                time.sleep(0.5)
            
            tavily_raw_data["pass1_pubchem_queries"].append({
                "url": url,
                "cid": cid,
                "pug_rest_response": response_json
            })

            if title:
                norm_title = normalize_text(title)
                norm_name_en = normalize_text(name_en)
                # Check if normalized title and name_en are substrings of each other
                if norm_title and norm_name_en and ((norm_title in norm_name_en) or (norm_name_en in norm_title)):
                    # Valid
                    pass
                else:
                    pubchem_removed.append({
                        "url": url,
                        "reason": f"PubChem Title Mismatch (Title: '{title}')"
                    })
            else:
                pubchem_removed.append({
                    "url": url,
                    "reason": "PubChem compound not found or API error"
                })

        # PASS 1: (c) PubMed Link Fetching and Validation
        for url in pubmed_urls:
            pmid_match = re.search(r"pubmed\.ncbi\.nlm\.nih\.gov/(\d+)", url)
            content_text = None
            pmid = None
            if pmid_match:
                pmid = pmid_match.group(1)
                content_text = fetch_pubmed_abstract(pmid)
                if content_text:
                    tavily_raw_data["pass1_pubchem_queries"].append({
                        "url": url,
                        "pmid": pmid,
                        "method": "eutils",
                        "response_length": len(content_text)
                    })
            
            # Fallback to Tavily Extract if E-utils failed or wasn't a standard URL
            if not content_text:
                extract_resp = None
                for attempt in range(3):
                    try:
                        extract_resp = tavily_client.extract(urls=[url])
                        break
                    except Exception as e:
                        if attempt < 2:
                            time.sleep(1)
                
                if extract_resp and extract_resp.get("results"):
                    res = extract_resp["results"][0]
                    content_text = res.get("raw_content") or res.get("content", "")
                    tavily_raw_data["pass1_pubchem_queries"].append({
                        "url": url,
                        "method": "tavily_extract",
                        "response_length": len(content_text)
                    })
            
            if content_text:
                # Extract title for local check
                parts = [p.strip() for p in content_text.split('\n\n') if p.strip()]
                title = ""
                if len(parts) >= 2 and parts[0].startswith("1. "):
                    title = parts[1]
                else:
                    title = content_text.split('\n')[0]
                
                is_auto_keep = check_pubmed_title_relevance(title, name_zh, name_en)
                
                if len(content_text) > 6000:
                    content_text = content_text[:6000] + "\n... [truncated due to length]"
                
                pass1_tasks.append({
                    "url": url,
                    "content": content_text,
                    "source_type": "pubmed",
                    "title": title,
                    "auto_keep": is_auto_keep
                })
            else:
                pubchem_removed.append({
                    "url": url,
                    "reason": "Failed to fetch PubMed content via E-utils or Tavily"
                })

        # PASS 1: (b) Other sources validation
        if other_urls:
            extract_resp = None
            for attempt in range(3):
                try:
                    extract_resp = tavily_client.extract(urls=other_urls)
                    break
                except Exception as e:
                    if attempt < 2:
                        time.sleep(1)
            
            tavily_raw_data["pass1_tavily_extract"] = extract_resp

            extracted_texts = {}
            failed_urls = {}
            if extract_resp:
                for res in extract_resp.get("results", []):
                    u = res.get("url")
                    raw = res.get("raw_content") or res.get("content", "")
                    extracted_texts[u] = raw
                for f in extract_resp.get("failed_results", []):
                    u = f.get("url")
                    failed_urls[u] = f.get("error", "Extract failed")

            for url in other_urls:
                if url in extracted_texts:
                    content_text = extracted_texts[url]
                    if len(content_text) > 6000:
                        content_text = content_text[:6000] + "\n... [truncated due to length]"
                    pass1_tasks.append({
                        "url": url,
                        "content": content_text,
                        "source_type": "other",
                        "auto_keep": False
                    })
                else:
                    err_msg = failed_urls.get(url, "Failed to retrieve content")
                    pubchem_removed.append({
                        "url": url,
                        "reason": f"Tavily extract failed: {err_msg}"
                    })

        # PASS 2: Tavily search for ADI (only if adi='unknown')
        pass2_task = None
        if rec["adi"] == "unknown":
            query = f"{name_zh} {name_en} ADI 每日可接受攝取量 (JECFA OR EFSA OR FAO OR 衛福部)"
            search_resp = None
            for attempt in range(3):
                try:
                    search_resp = tavily_client.search(
                        query=query,
                        search_depth="advanced",
                        max_results=5
                    )
                    # Check if any content is too short (< 200 chars)
                    any_short = any(len(r.get("content", "")) < 200 for r in search_resp.get("results", []))
                    if any_short:
                        search_resp = tavily_client.search(
                            query=query,
                            search_depth="advanced",
                            max_results=5,
                            include_raw_content=True
                        )
                    break
                except Exception as e:
                    if attempt < 2:
                        time.sleep(1)

            tavily_raw_data["pass2_tavily_search"] = {
                "query": query,
                "response": search_resp
            }

            seen_urls = set()
            combined_results = []
            
            # 1. Add successfully verified Pass 1 sources
            for t1 in pass1_tasks:
                u = t1["url"]
                if u not in seen_urls:
                    seen_urls.add(u)
                    combined_results.append({
                        "title": f"Pass 1 Source: {u}",
                        "url": u,
                        "content": t1["content"]
                    })

            # 2. Add Pass 2 Tavily search results as fallback/supplement
            if search_resp:
                for r in search_resp.get("results", []):
                    u = r.get("url", "")
                    if u not in seen_urls:
                        seen_urls.add(u)
                        content = r.get("raw_content") or r.get("content", "")
                        if len(content) > 4000:
                            content = content[:4000] + "\n... [truncated due to length]"
                        combined_results.append({
                            "title": r.get("title", ""),
                            "url": u,
                            "content": content
                        })

            pass2_task = {
                "query": query,
                "results": combined_results
            }

        # Save raw tavily file
        with open(TAVILY_RAW_DIR / f"{rid}.json", "w", encoding="utf-8") as f:
            json.dump(tavily_raw_data, f, ensure_ascii=False, indent=2)

        # Save task file
        task_data = {
            "record_id": rid,
            "name_zh": name_zh,
            "name_en": name_en,
            "pubchem_removed": pubchem_removed,
            "pass1_tasks": pass1_tasks,
            "pass2_task": pass2_task,
            "status": "pending"
        }
        with open(SUBAGENT_TASKS_DIR / f"{rid}.json", "w", encoding="utf-8") as f:
            json.dump(task_data, f, ensure_ascii=False, indent=2)

        print(f"[{idx_str}] Completed {rid} ({name_zh}).")

    # Run in parallel using ThreadPoolExecutor
    max_workers = 10
    total_records = len(records_to_process)
    print(f"Starting execution with {max_workers} threads...")
    
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(process_record, rec, f"{idx+1}/{total_records}"): rec 
            for idx, rec in enumerate(records_to_process)
        }
        for future in as_completed(futures):
            rec = futures[future]
            try:
                future.result()
            except Exception as e:
                print(f"Error processing record {rec['record_id']}: {e}")

    print("✅ Prepare complete! Task files written to subagent_tasks/.")

def compile_results():
    print("--- COMPILE ACTION ---")
    
    # Files to generate
    pass1_removed_file = RUN_DIR / "pass1_removed_log.csv"
    pass2_adi_file = RUN_DIR / "pass2_adi_results.csv"
    dryrun_changes_file = RUN_DIR / "dryrun_changes.csv"

    # Set up lists for CSV writing
    pass1_removed_rows = []
    pass2_adi_rows = []
    dryrun_changes_rows = []

    # Get DB current state
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT record_id, name_zh, name_en, adi, description_sources FROM additives")
    db_records = {r[0]: {"name_zh": r[1], "name_en": r[2], "adi": r[3], "sources": r[4] or []} for r in cur.fetchall()}
    cur.close()
    conn.close()

    # Read all processed tasks from subagent_io
    subagent_files = list(SUBAGENT_IO_DIR.glob("*.json"))
    
    # Sort files by record_id
    subagent_files.sort(key=lambda p: p.stem)

    stats = {
        "total_processed": 0,
        "pass1_total_sources": 0,
        "pass1_pubchem_removed": 0,
        "pass1_other_removed": 0,
        "pass1_remaining_sources": 0,
        "pass2_updated_adi": 0,
        "pass2_still_unknown": 0,
        "pass2_added_sources": 0
    }

    for p in subagent_files:
        rid = p.stem
        if rid not in db_records:
            print(f"Warning: processed record {rid} not found in database. Skipping.")
            continue

        stats["total_processed"] += 1
        db_rec = db_records[rid]
        name_zh = db_rec["name_zh"]
        adi_old = db_rec["adi"]
        original_sources = db_rec["sources"]
        stats["pass1_total_sources"] += len(original_sources)

        # Load task structure to get static pubchem removals or failed extracts
        task_path = SUBAGENT_TASKS_DIR / f"{rid}.json"
        if not task_path.exists():
            print(f"Warning: task file {task_path} does not exist. Skipping compilation of {rid}.")
            continue

        with open(task_path, "r", encoding="utf-8") as f:
            task_data = json.load(f)

        # Load subagent answers
        with open(p, "r", encoding="utf-8") as f:
            subagent_io = json.load(f)

        # Track removed URLs in PASS 1
        removed_urls_map = {} # url -> reason
        
        # 1. PubChem and other static removals from task definition
        for item in task_data.get("pubchem_removed", []):
            url = item["url"]
            reason = item["reason"]
            removed_urls_map[url] = reason
            if "pubchem" in url.lower():
                stats["pass1_pubchem_removed"] += 1
            else:
                stats["pass1_other_removed"] += 1

        # 2. Subagent relevance audits in PASS 1
        pass1_responses = subagent_io.get("pass1_subagent_responses", [])
        for resp in pass1_responses:
            url = resp.get("url")
            res_obj = resp.get("response", {})
            relevant = res_obj.get("relevant", True)
            reason = res_obj.get("reason", "Not relevant")
            
            if not relevant:
                removed_urls_map[url] = reason[:10]  # limit reason to 10 chars
                stats["pass1_other_removed"] += 1

        # Build pass1 removed log rows
        for url, reason in removed_urls_map.items():
            pass1_removed_rows.append({
                "record_id": rid,
                "removed_url": url,
                "reason": reason
            })

        # Calculate description sources after PASS 1
        sources_after_pass1 = [u for u in original_sources if u not in removed_urls_map]
        stats["pass1_remaining_sources"] += len(sources_after_pass1)

        # PASS 2: ADI results
        adi_new = "unknown"
        assessment_body = ""
        sources_added = []

        pass2_resp = subagent_io.get("pass2_subagent_response", {})
        if pass2_resp:
            res_obj = pass2_resp.get("response", {})
            adi_new = res_obj.get("adi", "unknown").strip()
            assessment_body = res_obj.get("assessment_body", "").strip()
            sources_added = res_obj.get("sources", [])
            # Deduplicate sources added
            sources_added = list(dict.fromkeys(sources_added))

        if adi_new != "unknown" and adi_new != "":
            stats["pass2_updated_adi"] += 1
            # Add to pass2_adi_results row
            pass2_adi_rows.append({
                "record_id": rid,
                "adi": adi_new,
                "assessment_body": assessment_body,
                "new_sources": json.dumps(sources_added, ensure_ascii=False)
            })
        else:
            adi_new = "unknown"
            stats["pass2_still_unknown"] += 1

        stats["pass2_added_sources"] += len(sources_added)

        # Build dryrun change row
        dryrun_changes_rows.append({
            "record_id": rid,
            "name_zh": name_zh,
            "adi_old": adi_old,
            "adi_new": adi_new,
            "sources_removed": json.dumps(list(removed_urls_map.keys()), ensure_ascii=False),
            "sources_added": json.dumps(sources_added, ensure_ascii=False)
        })

    # Write pass1_removed_log.csv
    with open(pass1_removed_file, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["record_id", "removed_url", "reason"])
        writer.writeheader()
        writer.writerows(pass1_removed_rows)

    # Write pass2_adi_results.csv
    with open(pass2_adi_file, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["record_id", "adi", "assessment_body", "new_sources"])
        writer.writeheader()
        writer.writerows(pass2_adi_rows)

    # Write dryrun_changes.csv
    with open(dryrun_changes_file, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["record_id", "name_zh", "adi_old", "adi_new", "sources_removed", "sources_added"])
        writer.writeheader()
        writer.writerows(dryrun_changes_rows)

    print("\n--- STATISTICS REPORT ---")
    print(f"Total processed records: {stats['total_processed']}")
    print(f"PASS 1:")
    print(f"  - Total original sources: {stats['pass1_total_sources']}")
    print(f"  - PubChem URLs removed:   {stats['pass1_pubchem_removed']}")
    print(f"  - Other URLs removed:     {stats['pass1_other_removed']}")
    print(f"  - Remaining sources:      {stats['pass1_remaining_sources']}")
    print(f"PASS 2:")
    print(f"  - ADI updated:            {stats['pass2_updated_adi']}")
    print(f"  - ADI still unknown:      {stats['pass2_still_unknown']}")
    print(f"  - Sources added:          {stats['pass2_added_sources']}")
    print(f"\nCSV files generated under {RUN_DIR}/:")
    print(f"  - pass1_removed_log.csv")
    print(f"  - pass2_adi_results.csv")
    print(f"  - dryrun_changes.csv")
    print("-------------------------")

def update_database():
    print("--- UPDATE DATABASE ACTION ---")
    dryrun_changes_file = RUN_DIR / "dryrun_changes.csv"
    if not dryrun_changes_file.exists():
        print(f"Error: dryrun_changes.csv not found at {dryrun_changes_file}. Run compile first.")
        sys.exit(1)

    conn = get_db_connection()
    cur = conn.cursor()

    updated_count = 0
    
    with open(dryrun_changes_file, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rid = row["record_id"]
            name_zh = row["name_zh"]
            adi_new = row["adi_new"]
            sources_removed = json.loads(row["sources_removed"])
            sources_added = json.loads(row["sources_added"])

            # Query current sources and ADI from DB
            cur.execute("SELECT adi, description_sources FROM additives WHERE record_id = %s", (rid,))
            res = cur.fetchone()
            if not res:
                print(f"Warning: Record {rid} not found in database during update. Skipping.")
                continue
            
            current_adi, current_sources = res
            current_sources = current_sources or []

            # 1. Update ADI if it has changed
            final_adi = current_adi
            if adi_new != "unknown" and adi_new != current_adi:
                final_adi = adi_new

            # 2. Re-calculate description sources: (current - removed) + added with deduplication
            # Maintain order of remaining items
            new_sources = [u for u in current_sources if u not in sources_removed]
            for u in sources_added:
                if u not in new_sources:
                    new_sources.append(u)

            # De-duplicate while preserving order
            final_sources = []
            seen = set()
            for u in new_sources:
                if u not in seen:
                    final_sources.append(u)
                    seen.add(u)

            # Update DB
            # Print change statement
            print(f"Updating {rid} ({name_zh}): ADI: '{current_adi}' -> '{final_adi}', Sources: {len(current_sources)} -> {len(final_sources)}")
            
            cur.execute(
                "UPDATE additives SET adi = %s, description_sources = %s WHERE record_id = %s",
                (final_adi, json.dumps(final_sources, ensure_ascii=False), rid)
            )
            updated_count += 1

    conn.commit()
    cur.close()
    conn.close()

    print(f"✅ Successfully updated {updated_count} records in PostgreSQL database!")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Food Additive ADI and Sources Enrichment Coordinator")
    parser.add_argument("--action", choices=["prepare", "compile", "update"], required=True, help="Action to perform")
    parser.add_argument("--limit", type=int, default=None, help="Batch size limit for preparation")
    parser.add_argument("--start-id", type=str, default=None, help="Starting record_id for preparation")
    parser.add_argument("--record-ids", type=str, default=None, help="Comma-separated specific record IDs to prepare (overrides start-id and limit)")

    args = parser.parse_args()

    if args.action == "prepare":
        r_ids = None
        if args.record_ids:
            r_ids = [rid.strip() for rid in args.record_ids.split(",") if rid.strip()]
        prepare_batch(args.limit, args.start_id, record_ids=r_ids)
    elif args.action == "compile":
        compile_results()
    elif args.action == "update":
        update_database()
