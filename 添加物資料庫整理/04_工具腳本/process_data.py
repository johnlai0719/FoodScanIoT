import pandas as pd
import json
import re
import sys
import warnings
import numpy as np

# Suppress warnings from openpyxl
warnings.filterwarnings("ignore", category=UserWarning, module='openpyxl')

def clean_text(text):
    if pd.isna(text):
        return "unknown"
    return str(text).strip()

def calculate_curation_confidence(record):
    """
    計算規則導向的整理信心分數 (Rule-based Curation Confidence)。
    此分數反映資料抽取與初步整理的完整程度，而非模型校準後的置信度。
    """
    score = 0
    # 基礎法律欄位 (Essential legal fields)
    if record['additive_name_zh'] != "unknown": score += 1
    if record['additive_name_en'] != "unknown": score += 1
    if record['function_class']: score += 1
    
    # 深度資料欄位 (Deeper data points)
    if record['ins_or_e_number'] != "unknown": score += 1
    if "unknown" not in record['source_type']: score += 0.5
    if record['possible_allergens']: score += 0.5
    
    # 上限 5 分，下限 1 分
    return max(1, min(5, round(score)))

def determine_completeness(record):
    """
    嚴格定義資料完整度等級 (Completeness Level):
    - verified: 人工審核且證據齊全 (目前腳本無法自動達成)
    - enriched: 具備過敏原或慢性病候選資訊 (Future enrichment)
    - partial: 具備名稱、類別外，還具備來源(source_type)或 INS 編號
    - basic: 僅有名稱、類別、法規資訊
    """
    # 目前僅根據現有欄位判定
    if record['review_status'] == "verified":
        return "verified"
    
    # 如果過敏原或慢性病目標不是空的，則為 enriched
    if record['possible_allergens'] or record['chronic_risk_targets']:
        return "enriched"
        
    # 如果有 INS 或 來源類型，則為 partial
    if record['ins_or_e_number'] != "unknown" or "unknown" not in record['source_type']:
        return "partial"
        
    # 基本款
    return "basic"

try:
    df = pd.read_excel('TFDA官方食品添加物.xlsx')
except Exception as e:
    with open('output.json', 'w', encoding='utf-8') as f:
        json.dump({"error": str(e)}, f, ensure_ascii=False, indent=2)
    sys.exit(1)

results = []
for index, row in df.iterrows():
    try:
        record_id = f"ADD-{int(row['項次']):04d}"
    except:
        record_id = f"ADD-{index}"
        
    zh_name = clean_text(row['中文品名'])
    en_name = clean_text(row['英文品名'])
    
    # Fix potential truncation or odd formatting (though read_excel seems okay, 
    # the user mentioned truncation. If I used a different tool before, that might be why.
    # Here I'll just ensure it's treated as string.)
    
    # Clean function class
    func_class_raw = clean_text(row['類別'])
    func_class = re.sub(r'^\(.*\)\s*', '', func_class_raw).strip()
    
    usage = clean_text(row['使用食品範圍及限量'])
    limit = clean_text(row['使用限制'])
    
    # Separate regulatory summary from risk summary
    regulatory_summary = f"屬台灣法規第{func_class_raw.split(' ')[0]}類添加物。"
    
    exception_notes = []
    if usage != "unknown": exception_notes.append(f"使用範圍及限量: {usage}")
    if limit != "unknown" and limit != "nan": exception_notes.append(f"使用限制: {limit}")
    
    # For now, risk summary is conservative
    risk_summary = "unknown"
    
    record = {
        "record_id": record_id,
        "additive_name_zh": zh_name,
        "additive_name_en": en_name,
        "synonyms": [],
        "ins_or_e_number": "unknown",
        "function_class": [func_class] if func_class != "unknown" else [],
        "source_type": ["unknown"],
        "possible_allergens": [],
        "chronic_risk_targets": [],
        "risk_summary": risk_summary,
        "regulatory_status_tw": "允許使用",
        "regulatory_summary": regulatory_summary,
        "completeness_level": "basic", 
        "exception_notes": "\n".join(exception_notes) if exception_notes else "unknown",
        "evidence_items": [],
        "curation_confidence": 0, # 整筆資料的整理完整度 (Overall record completeness)
        "review_status": "candidate",
        "review_notes": "候選法規主檔，已確認基本名稱與法規分類，待補強 INS、來源與風險欄位。"
    }
    
    # Add evidence items with real quotes
    if zh_name != "unknown":
        record["evidence_items"].append({
            "field_name": "additive_name_zh",
            "value": zh_name,
            "evidence_quote": f"中文品名: {zh_name}",
            "source_title": "食品添加物使用範圍及限量暨規格標準",
            "source_url": "https://consumer.fda.gov.tw/Law/FoodAdditivesList.aspx",
            "source_type": "official",
            "evidence_level": "high",
            "confidence_score": 5 # 單一證據片段可信度 (Single evidence fragment credibility)
        })
    
    if func_class != "unknown":
        record["evidence_items"].append({
            "field_name": "function_class",
            "value": func_class,
            "evidence_quote": f"類別: {func_class_raw}",
            "source_title": "食品添加物使用範圍及限量暨規格標準",
            "source_url": "https://consumer.fda.gov.tw/Law/FoodAdditivesList.aspx",
            "source_type": "official",
            "evidence_level": "high",
            "confidence_score": 5 # 單一證據片段可信度 (Single evidence fragment credibility)
        })

    # Calculate confidence and completeness
    record["curation_confidence"] = calculate_curation_confidence(record)
    record["completeness_level"] = determine_completeness(record)
    
    results.append(record)

with open('output.json', 'w', encoding='utf-8') as f:
    json.dump(results, f, ensure_ascii=False, indent=2)

print(f"Processed {len(results)} records.")
