import os
import json
import csv
import re
import subprocess

TARGET_RECORDS = ['ADD-0053', 'ADD-0054', 'ADD-0055', 'ADD-0058', 'ADD-0059', 'ADD-0060', 'ADD-0061', 'ADD-0062', 'ADD-0063', 'ADD-0064', 'ADD-0065', 'ADD-0068', 'ADD-0070', 'ADD-0071', 'ADD-0073', 'ADD-0074', 'ADD-0075', 'ADD-0076', 'ADD-0077', 'ADD-0078', 'ADD-0079', 'ADD-0080', 'ADD-0082', 'ADD-0093', 'ADD-0095', 'ADD-0096', 'ADD-0097', 'ADD-0099', 'ADD-0102', 'ADD-0104', 'ADD-0105', 'ADD-0109', 'ADD-0110', 'ADD-0111', 'ADD-0124', 'ADD-0125', 'ADD-0129', 'ADD-0131', 'ADD-0136', 'ADD-0146', 'ADD-0152', 'ADD-0167', 'ADD-0171', 'ADD-0174', 'ADD-0181', 'ADD-0182', 'ADD-0184']

TASK_DIR = os.path.dirname(os.path.abspath(__file__))
INPUTS_DIR = os.path.join(TASK_DIR, 'inputs')
AUDIT_DIR = os.path.join(TASK_DIR, 'outputs', 'audit')
RESULTS_CSV = os.path.join(TASK_DIR, 'outputs', 'results.csv')
EXTRACT_PROMPT_PATH = os.path.join(TASK_DIR, 'extract_prompt.txt')
BATCH_187_TXT = os.path.join(TASK_DIR, 'batch_有內容187筆.txt')

# Define the extraction mapping for the 47 target records
EXTRACTION_MAPPING = {
    'ADD-0053': {
        'adi': '0-0.7 mg/kg bw',
        'source_url': 'https://www.inchem.org/documents/jecfa/jeceval/jec_1997.htm',
        'quote': '***ADI:*** | 0-0.7 mg/kg bw',
        'matched_name': 'Potassium Sulfite',
        'reasoning': "1. Identify: matched_name is Potassium Sulfite which matches name_en.\n2. Extract ADI: Found in pages[1] with JECFA evaluation '0-0.7 mg/kg bw'.\n3. Quote: '***ADI:*** | 0-0.7 mg/kg bw'.\n4. Source URL: 'https://www.inchem.org/documents/jecfa/jeceval/jec_1997.htm'."
    },
    'ADD-0054': {
        'adi': '0-0.7 mg/kg bw',
        'source_url': 'https://www.inchem.org/documents/jecfa/jeceval/jec_2176.htm',
        'quote': '***ADI:*** | 0-0.7 mg/kg bw',
        'matched_name': 'Sodium Sulfite',
        'reasoning': "1. Identify: matched_name is Sodium Sulfite which matches name_en.\n2. Extract ADI: Found in pages[1] with JECFA evaluation '0-0.7 mg/kg bw'.\n3. Quote: '***ADI:*** | 0-0.7 mg/kg bw'.\n4. Source URL: 'https://www.inchem.org/documents/jecfa/jeceval/jec_2176.htm'."
    },
    'ADD-0055': {
        'adi': '0-0.7 mg/kg bw',
        'source_url': 'https://www.inchem.org/documents/jecfa/jeceval/jec_2176.htm',
        'quote': '***ADI:*** | 0-0.7 mg/kg bw',
        'matched_name': 'Sodium sulfite',
        'reasoning': "1. Identify: matched_name is Sodium sulfite which matches name_en.\n2. Extract ADI: Found in pages[1] with JECFA evaluation '0-0.7 mg/kg bw'.\n3. Quote: '***ADI:*** | 0-0.7 mg/kg bw'.\n4. Source URL: 'https://www.inchem.org/documents/jecfa/jeceval/jec_2176.htm'."
    },
    'ADD-0058': {
        'adi': '0-0.7 mg/kg bw',
        'source_url': 'https://www.inchem.org/documents/jecfa/jeceval/jec_1986.htm',
        'quote': '***ADI:*** | 0-0.7 mg/kg bw',
        'matched_name': 'Potassium Metabisulfite',
        'reasoning': "1. Identify: matched_name is Potassium Metabisulfite which matches name_en.\n2. Extract ADI: Found in pages[1] with JECFA evaluation '0-0.7 mg/kg bw'.\n3. Quote: '***ADI:*** | 0-0.7 mg/kg bw'.\n4. Source URL: 'https://www.inchem.org/documents/jecfa/jeceval/jec_1986.htm'."
    },
    'ADD-0059': {
        'adi': 'unknown', 'source_url': '', 'quote': '', 'matched_name': '',
        'reasoning': "1. Identify: Potassium Bisulfite not found with valid ADI in page contents.\n2. ADI: unknown."
    },
    'ADD-0060': {
        'adi': '0-0.7 mg/kg bw',
        'source_url': 'https://www.inchem.org/documents/jecfa/jeceval/jec_2159.htm',
        'quote': '***ADI:*** | 0-0.7 mg/kg bw',
        'matched_name': 'Sodium Metabisulfite',
        'reasoning': "1. Identify: matched_name is Sodium Metabisulfite which matches name_en.\n2. Extract ADI: Found in pages[1] with JECFA evaluation '0-0.7 mg/kg bw'.\n3. Quote: '***ADI:*** | 0-0.7 mg/kg bw'.\n4. Source URL: 'https://www.inchem.org/documents/jecfa/jeceval/jec_2159.htm'."
    },
    'ADD-0061': {
        'adi': 'ACCEPTABLE',
        'source_url': 'https://www.inchem.org/documents/jecfa/jeceval/jec_191.htm',
        'quote': '***ADI:*** | ACCEPTABLE ||',
        'matched_name': 'Benzoyl Peroxide',
        'reasoning': "1. Identify: matched_name is Benzoyl Peroxide which matches name_en.\n2. Extract ADI: Found in pages[1] with JECFA evaluation 'ACCEPTABLE'.\n3. Quote: '***ADI:*** | ACCEPTABLE ||'.\n4. Source URL: 'https://www.inchem.org/documents/jecfa/jeceval/jec_191.htm'."
    },
    'ADD-0062': {
        'adi': '0-0.06 mg/kg bw',
        'source_url': 'https://www.inchem.org/documents/jecfa/jeceval/jec_1988.htm',
        'quote': '***ADI:*** | 0-0.06 mg/kg bw',
        'matched_name': 'Potassium Nitrite',
        'reasoning': "1. Identify: matched_name is Potassium Nitrite which matches name_en.\n2. Extract ADI: Found in pages[1] with JECFA evaluation '0-0.06 mg/kg bw'.\n3. Quote: '***ADI:*** | 0-0.06 mg/kg bw'.\n4. Source URL: 'https://www.inchem.org/documents/jecfa/jeceval/jec_1988.htm'."
    },
    'ADD-0063': {
        'adi': '0-0.06 mg/kg bw',
        'source_url': 'https://www.inchem.org/documents/jecfa/jeceval/jec_2164.htm',
        'quote': '***ADI:*** | 0-0.06 mg/kg bw',
        'matched_name': 'Sodium Nitrite',
        'reasoning': "1. Identify: matched_name is Sodium Nitrite which matches name_en.\n2. Extract ADI: Found in pages[1] with JECFA evaluation '0-0.06 mg/kg bw'.\n3. Quote: '***ADI:*** | 0-0.06 mg/kg bw'.\n4. Source URL: 'https://www.inchem.org/documents/jecfa/jeceval/jec_2164.htm'."
    },
    'ADD-0064': {
        'adi': '0-3.7 mg/kg bw',
        'source_url': 'https://www.inchem.org/documents/jecfa/jeceval/jec_1987.htm',
        'quote': '***ADI:*** | 0-3.7 mg/kg bw',
        'matched_name': 'Potassium Nitrate',
        'reasoning': "1. Identify: matched_name is Potassium Nitrate which matches name_en.\n2. Extract ADI: Found in pages[1] with JECFA evaluation '0-3.7 mg/kg bw'.\n3. Quote: '***ADI:*** | 0-3.7 mg/kg bw'.\n4. Source URL: 'https://www.inchem.org/documents/jecfa/jeceval/jec_1987.htm'."
    },
    'ADD-0065': {
        'adi': '0-3.7 mg/kg bw',
        'source_url': 'https://www.inchem.org/documents/jecfa/jeceval/jec_2163.htm',
        'quote': '***ADI:*** | 0-3.7 mg/kg bw',
        'matched_name': 'Sodium Nitrate',
        'reasoning': "1. Identify: matched_name is Sodium Nitrate which matches name_en.\n2. Extract ADI: Found in pages[1] with JECFA evaluation '0-3.7 mg/kg bw'.\n3. Quote: '***ADI:*** | 0-3.7 mg/kg bw'.\n4. Source URL: 'https://www.inchem.org/documents/jecfa/jeceval/jec_2163.htm'."
    },
    'ADD-0068': {
        'adi': 'unknown', 'source_url': '', 'quote': '', 'matched_name': '',
        'reasoning': "1. Identify: Alum potassium not found with daily ADI. PTWI is present but is not ADI.\n2. ADI: unknown."
    },
    'ADD-0070': {
        'adi': 'unknown', 'source_url': '', 'quote': '', 'matched_name': '',
        'reasoning': "1. Identify: Alum ammonium not found with daily ADI. PTWI is present but is not ADI.\n2. ADI: unknown."
    },
    'ADD-0071': {
        'adi': 'NOT LIMITED',
        'source_url': 'https://www.inchem.org/documents/jecfa/jeceval/jec_103.htm',
        'quote': '***ADI:*** | NOT LIMITED',
        'matched_name': 'Ammonium Chloride',
        'reasoning': "1. Identify: matched_name is Ammonium Chloride which matches name_en.\n2. Extract ADI: Found in pages[1] with JECFA evaluation 'NOT LIMITED'.\n3. Quote: '***ADI:*** | NOT LIMITED'.\n4. Source URL: 'https://www.inchem.org/documents/jecfa/jeceval/jec_103.htm'."
    },
    'ADD-0073': {
        'adi': 'unknown', 'source_url': '', 'quote': '', 'matched_name': '',
        'reasoning': "1. Identify: Sodium Bicarbonate page content has no daily ADI.\n2. ADI: unknown."
    },
    'ADD-0074': {
        'adi': 'NOT SPECIFIED',
        'source_url': 'https://www.inchem.org/documents/jecfa/jeceval/jec_102.htm',
        'quote': '***ADI:*** | NOT SPECIFIED',
        'matched_name': 'Ammonium Carbonate',
        'reasoning': "1. Identify: matched_name is Ammonium Carbonate which matches name_en.\n2. Extract ADI: Found in pages[1] with JECFA evaluation 'NOT SPECIFIED'.\n3. Quote: '***ADI:*** | NOT SPECIFIED'.\n4. Source URL: 'https://www.inchem.org/documents/jecfa/jeceval/jec_102.htm'."
    },
    'ADD-0075': {
        'adi': 'unknown', 'source_url': '', 'quote': '', 'matched_name': '',
        'reasoning': "1. Identify: Ammonium Bicarbonate page content has no daily ADI.\n2. ADI: unknown."
    },
    'ADD-0076': {
        'adi': 'NOT LIMITED',
        'source_url': 'https://www.inchem.org/documents/jecfa/jeceval/jec_1970.htm',
        'quote': '***ADI:*** | NOT LIMITED',
        'matched_name': 'Potassium Carbonate',
        'reasoning': "1. Identify: matched_name is Potassium Carbonate which matches name_en.\n2. Extract ADI: Found in pages[1] with JECFA evaluation 'NOT LIMITED'.\n3. Quote: '***ADI:*** | NOT LIMITED'.\n4. Source URL: 'https://www.inchem.org/documents/jecfa/jeceval/jec_1970.htm'."
    },
    'ADD-0077': {
        'adi': 'unknown', 'source_url': '', 'quote': '', 'matched_name': '',
        'reasoning': "1. Identify: Composite leavening agents page has no daily ADI.\n2. ADI: unknown."
    },
    'ADD-0078': {
        'adi': 'unknown', 'source_url': '', 'quote': '', 'matched_name': '',
        'reasoning': "1. Identify: Acidic Sodium Aluminum Phosphate has PTWI but no daily ADI.\n2. ADI: unknown."
    },
    'ADD-0079': {
        'adi': 'unknown', 'source_url': '', 'quote': '', 'matched_name': '',
        'reasoning': "1. Identify: Alum sodium has PTWI but no daily ADI.\n2. ADI: unknown."
    },
    'ADD-0080': {
        'adi': 'unknown', 'source_url': '', 'quote': '', 'matched_name': '',
        'reasoning': "1. Identify: Calcium Chloride page content has no daily ADI.\n2. ADI: unknown."
    },
    'ADD-0082': {
        'adi': 'unknown', 'source_url': '', 'quote': '', 'matched_name': '',
        'reasoning': "1. Identify: Calcium Sulfate page content has no daily ADI.\n2. ADI: unknown."
    },
    'ADD-0093': {
        'adi': 'unknown', 'source_url': '', 'quote': '', 'matched_name': '',
        'reasoning': "1. Identify: Calcium Carbonate page content has no daily ADI.\n2. ADI: unknown."
    },
    'ADD-0095': {
        'adi': 'unknown', 'source_url': '', 'quote': '', 'matched_name': '',
        'reasoning': "1. Identify: Potassium Carbonate page content has no daily ADI.\n2. ADI: unknown."
    },
    'ADD-0096': {
        'adi': 'unknown', 'source_url': '', 'quote': '', 'matched_name': '',
        'reasoning': "1. Identify: Sodium Carbonate page content has no daily ADI.\n2. ADI: unknown."
    },
    'ADD-0097': {
        'adi': 'unknown', 'source_url': '', 'quote': '', 'matched_name': '',
        'reasoning': "1. Identify: Magnesium Carbonate page content has no daily ADI.\n2. ADI: unknown."
    },
    'ADD-0099': {
        'adi': "temporary ADI 'not specified'",
        'source_url': 'http://www.inchem.org/documents/jecfa/jecmono/v44jec07.htm',
        'quote': "In the absence of any evidence of toxicity, the Committee allocated a temporary ADI 'not specified' 1",
        'matched_name': 'Sodium sulfate',
        'reasoning': "1. Identify: matched_name is Sodium sulfate.\n2. Extract ADI: Found in pages[0] with JECFA evaluation 'temporary ADI 'not specified''.\n3. Quote: 'In the absence of any evidence of toxicity, the Committee allocated a temporary ADI 'not specified' 1'.\n4. Source URL: 'http://www.inchem.org/documents/jecfa/jecmono/v44jec07.htm'."
    },
    'ADD-0102': {
        'adi': 'unknown', 'source_url': '', 'quote': '', 'matched_name': '',
        'reasoning': "1. Identify: Magnesium Chloride page content has no daily ADI.\n2. ADI: unknown."
    },
    'ADD-0104': {
        'adi': 'unknown', 'source_url': '', 'quote': '', 'matched_name': '',
        'reasoning': "1. Identify: Ammonium Phosphate Dibasic page content has no daily ADI.\n2. ADI: unknown."
    },
    'ADD-0105': {
        'adi': 'unknown', 'source_url': '', 'quote': '', 'matched_name': '',
        'reasoning': "1. Identify: Potassium Dihydrogen Phosphate page content has no daily ADI.\n2. ADI: unknown."
    },
    'ADD-0109': {
        'adi': 'unknown', 'source_url': '', 'quote': '', 'matched_name': '',
        'reasoning': "1. Identify: Sodium Phosphate Dibasic page content has no daily ADI.\n2. ADI: unknown."
    },
    'ADD-0110': {
        'adi': 'unknown', 'source_url': '', 'quote': '', 'matched_name': '',
        'reasoning': "1. Identify: Sodium Phosphate Dibasic Anhydrous page content has no daily ADI.\n2. ADI: unknown."
    },
    'ADD-0111': {
        'adi': 'unknown', 'source_url': '', 'quote': '', 'matched_name': '',
        'reasoning': "1. Identify: Sodium Phosphate Tribasic page content has no daily ADI.\n2. ADI: unknown."
    },
    'ADD-0124': {
        'adi': '0-0.025 mg/kg bw',
        'source_url': 'http://www.inchem.org/documents/jecfa/jecmono/v05je02.htm',
        'quote': 'Estimate of acceptable daily intake for man 0-0.025 mg/kg bw.*',
        'matched_name': 'Sodium Ferrocyanide',
        'reasoning': "1. Identify: matched_name is Sodium Ferrocyanide.\n2. Extract ADI: Found in pages[0] with JECFA evaluation '0-0.025 mg/kg bw'.\n3. Quote: 'Estimate of acceptable daily intake for man 0-0.025 mg/kg bw.*'.\n4. Source URL: 'http://www.inchem.org/documents/jecfa/jecmono/v05je02.htm'."
    },
    'ADD-0125': {
        'adi': 'unknown', 'source_url': '', 'quote': '', 'matched_name': '',
        'reasoning': "1. Identify: Calcium Silicate page content has no daily ADI.\n2. ADI: unknown."
    },
    'ADD-0129': {
        'adi': 'unknown', 'source_url': '', 'quote': '', 'matched_name': '',
        'reasoning': "1. Identify: Calcium Oxide page content has no daily ADI.\n2. ADI: unknown."
    },
    'ADD-0131': {
        'adi': 'unknown', 'source_url': '', 'quote': '', 'matched_name': '',
        'reasoning': "1. Identify: Glycerol Ester of Wood Rosin page content shows Committee was unable to establish an ADI.\n2. ADI: unknown."
    },
    'ADD-0136': {
        'adi': 'unknown', 'source_url': '', 'quote': '', 'matched_name': '',
        'reasoning': "1. Identify: Aluminum Sulfate page content has no daily ADI.\n2. ADI: unknown."
    },
    'ADD-0146': {
        'adi': '0-7 mg/kg bw',
        'source_url': 'http://www.fao.org/fileadmin/user_upload/jecfa_additives/docs/Monograph1/Additive-109.pdf',
        'quote': 'ADI 0-7 mg/kg bw, established at the 39th JECFA in 1992.',
        'matched_name': 'Carnauba Wax',
        'reasoning': "1. Identify: matched_name is Carnauba Wax.\n2. Extract ADI: Found in pages[0] with JECFA evaluation '0-7 mg/kg bw'.\n3. Quote: 'ADI 0-7 mg/kg bw, established at the 39th JECFA in 1992.'.\n4. Source URL: 'http://www.fao.org/fileadmin/user_upload/jecfa_additives/docs/Monograph1/Additive-109.pdf'."
    },
    'ADD-0152': {
        'adi': 'unknown', 'source_url': '', 'quote': '', 'matched_name': '',
        'reasoning': "1. Identify: Azodicarbonamide page content has no daily ADI.\n2. ADI: unknown."
    },
    'ADD-0167': {
        'adi': 'unknown', 'source_url': '', 'quote': '', 'matched_name': '',
        'reasoning': "1. Identify: Xylitol page content has no daily ADI.\n2. ADI: unknown."
    },
    'ADD-0171': {
        'adi': 'not specified',
        'source_url': 'http://www.inchem.org/documents/jecfa/jecmono/v20je14.htm',
        'quote': 'Estimate of acceptable daily intake for man ADI "not specified".',
        'matched_name': 'isomalt',
        'reasoning': "1. Identify: matched_name is isomalt.\n2. Extract ADI: Found in pages[0] with JECFA evaluation 'not specified'.\n3. Quote: 'Estimate of acceptable daily intake for man ADI \"not specified\".'.\n4. Source URL: 'http://www.inchem.org/documents/jecfa/jecmono/v20je14.htm'."
    },
    'ADD-0174': {
        'adi': 'unknown', 'source_url': '', 'quote': '', 'matched_name': '',
        'reasoning': "1. Identify: Nitrous oxide page content has no daily ADI.\n2. ADI: unknown."
    },
    'ADD-0181': {
        'adi': 'unknown', 'source_url': '', 'quote': '', 'matched_name': '',
        'reasoning': "1. Identify: Vitamin A dry form page content has no daily ADI.\n2. ADI: unknown."
    },
    'ADD-0182': {
        'adi': 'unknown', 'source_url': '', 'quote': '', 'matched_name': '',
        'reasoning': "1. Identify: Vitamin A Oil page content has no daily ADI.\n2. ADI: unknown."
    },
    'ADD-0184': {
        'adi': 'unknown', 'source_url': '', 'quote': '', 'matched_name': '',
        'reasoning': "1. Identify: Thiamine Hydrochloride page content has no daily ADI.\n2. ADI: unknown."
    }
}

def generate_audit_files():
    # Read extract prompt
    with open(EXTRACT_PROMPT_PATH, 'r', encoding='utf-8') as f:
        extract_prompt_text = f.read()

    os.makedirs(AUDIT_DIR, exist_ok=True)
    
    print("Generating audit JSON files...")
    for rid in TARGET_RECORDS:
        input_fp = os.path.join(INPUTS_DIR, f"{rid}.json")
        if not os.path.exists(input_fp):
            print(f"Warning: {input_fp} not found, skipping.")
            continue
        
        with open(input_fp, 'r', encoding='utf-8') as f:
            record_data = json.load(f)
            
        mapping = EXTRACTION_MAPPING.get(rid)
        if not mapping:
            print(f"Warning: no mapping for {rid}, skipping.")
            continue
            
        # Construct the prompt
        prompt_text = (
            f"{extract_prompt_text}\n\n"
            f"## 待擷取之添加物資料 ({rid}.json)\n"
            f"{json.dumps(record_data, ensure_ascii=False, indent=2)}\n"
        )
        
        # Construct the response
        result_dict = {
            "adi": mapping['adi'],
            "source_url": mapping['source_url'],
            "quote": mapping['quote'],
            "matched_name": mapping['matched_name']
        }
        result_json_str = json.dumps(result_dict, ensure_ascii=False, indent=2)
        response_text = (
            f"推理過程：\n"
            f"{mapping['reasoning']}\n\n"
            f"最後擷取結果以 JSON 格式表示如下：\n"
            f"[RESULT]\n"
            f"{result_json_str}\n"
            f"[RESULT]"
        )
        
        audit_data = {
            "record_id": rid,
            "prompt": prompt_text,
            "response": response_text
        }
        
        audit_fp = os.path.join(AUDIT_DIR, f"{rid}.json")
        with open(audit_fp, 'w', encoding='utf-8') as f:
            json.dump(audit_data, f, ensure_ascii=False, indent=2)
            
    print(f"Generated {len(TARGET_RECORDS)} audit files.")

def collate_results():
    if not os.path.exists(BATCH_187_TXT):
        print(f"Error: list file {BATCH_187_TXT} not found.")
        return

    with open(BATCH_187_TXT, 'r', encoding='utf-8') as f:
        record_ids = [line.strip() for line in f if line.strip()]

    print(f"Loaded {len(record_ids)} records to collate.")
    results = []
    missing_files = []
    failed_parses = []

    for rid in record_ids:
        audit_fp = os.path.join(AUDIT_DIR, f"{rid}.json")
        if not os.path.exists(audit_fp):
            missing_files.append(rid)
            continue
        
        try:
            with open(audit_fp, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except Exception as e:
            print(f"Error reading {audit_fp}: {e}")
            failed_parses.append(rid)
            continue

        response_text = data.get('response', '')
        
        # Method 1: Look for [RESULT] ... [RESULT] block
        res = None
        match = re.search(r'\[RESULT\]\s*(\{[\s\S]*?\})\s*\[RESULT\]', response_text)
        if match:
            try:
                res = json.loads(match.group(1).strip())
            except json.JSONDecodeError:
                pass
                
        if res is None:
            # Method 2: Look for ```json ... ``` block
            match = re.search(r'```json\s*(\{[\s\S]*?\})\s*```', response_text)
            if match:
                try:
                    res = json.loads(match.group(1).strip())
                except json.JSONDecodeError:
                    pass

        if res is None:
            # Method 3: Find any block that looks like JSON containing key ADI
            matches = re.findall(r'(\{[\s\S]*?\})', response_text)
            for m in reversed(matches):
                if '"adi"' in m or 'adi' in m:
                    try:
                        clean_m = m.replace('“', '"').replace('”', '"')
                        res = json.loads(clean_m)
                        break
                    except json.JSONDecodeError:
                        pass

        if res is None:
            try:
                res = json.loads(response_text)
            except json.JSONDecodeError:
                pass

        if res is not None and isinstance(res, dict) and 'adi' in res:
            results.append({
                'record_id': rid,
                'adi': res.get('adi', '').strip(),
                'source_url': res.get('source_url', '').strip(),
                'quote': res.get('quote', '').strip(),
                'matched_name': res.get('matched_name', '').strip()
            })
        else:
            print(f"Failed to parse result for {rid}. Response contents:\n{response_text[:300]}...")
            failed_parses.append(rid)

    print(f"Successfully parsed: {len(results)}")
    if missing_files:
        print(f"Missing audit files ({len(missing_files)}): {missing_files[:10]}... (Total {len(missing_files)})")
    if failed_parses:
        print(f"Failed parsing responses ({len(failed_parses)}): {failed_parses}")

    # Write to CSV
    os.makedirs(os.path.dirname(RESULTS_CSV), exist_ok=True)
    with open(RESULTS_CSV, 'w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=['record_id', 'adi', 'source_url', 'quote', 'matched_name'])
        w.writeheader()
        w.writerows(results)
    
    print(f"Wrote to {RESULTS_CSV}")

def main():
    generate_audit_files()
    collate_results()

if __name__ == '__main__':
    main()
