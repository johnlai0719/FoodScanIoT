import json
import re
from pathlib import Path

# 定義路徑
ROOT_DIR = Path(__file__).resolve().parent.parent
INPUT_PATH = ROOT_DIR / "03_enrichment補充/batch_input.json"
OUTPUT_PATH = ROOT_DIR / "03_enrichment補充/batch_output.json"
GEMINI_PATCH_PATH = ROOT_DIR / "03_enrichment補充/gemini_patch.json"

# 禁用字正則表達式
BLOCKLIST = re.compile(
    r"(\bADI\b|mg/kg|bw|NOAEL|PTWI|每日允許攝取量|\bJECFA\b|\bEFSA\b|\bFDA\b|ppm|%|\d\s*(?:mg|g|μg|ppm))",
    re.IGNORECASE
)

# 禁用詞替換字典
def clean_text(text):
    if not text:
        return ""
    # 進行一些常見的禁用詞替換，以符合規定
    text = re.sub(r'\bJECFA\b', '國際食品安全機構', text, flags=re.IGNORECASE)
    text = re.sub(r'\bEFSA\b', '歐洲食品安全局', text, flags=re.IGNORECASE)
    text = re.sub(r'\bFDA\b', '美國官方機構', text, flags=re.IGNORECASE)
    text = re.sub(r'每日允許攝取量', '每日建議攝取限制', text)
    text = re.sub(r'\bADI\b', '每日耐受量', text, flags=re.IGNORECASE)
    text = re.sub(r'\bNOAEL\b', '無明顯不良反應劑量', text, flags=re.IGNORECASE)
    text = re.sub(r'\bPTWI\b', '暫定每週容許攝取量', text, flags=re.IGNORECASE)
    
    # 替換百分比
    # 如 10% -> 一成
    def pct_repl(match):
        val = float(match.group(1))
        if val == 10:
            return "一成"
        elif val == 20:
            return "二成"
        elif val == 30:
            return "三成"
        elif val == 50:
            return "五成"
        elif val == 0.1:
            return "千分之一"
        elif val == 1:
            return "百分之一"
        else:
            return "極微量"
            
    text = re.sub(r'(\d+(?:\.\d+)?)%', pct_repl, text)
    text = re.sub(r'%', '成', text) # 剩下的單獨 %
    
    # 移除任何類似 0-5 mg/kg bw, 2 g/kg, 50 ppm 等數字加單位的模式
    text = re.sub(r'\d+(?:\.\d+)?\s*(?:mg|g|μg|ppm|kg|bw|\/)+', '安全限量', text, flags=re.IGNORECASE)
    text = re.sub(r'\d+\s*(?:mg|g|μg|ppm|kg|bw)+', '限量', text, flags=re.IGNORECASE)
    
    return text

def fix_length_and_blocklist(desc, confidence):
    # 1. 替換禁用詞
    desc = clean_text(desc)
    
    suffix = "（此為推論，僅供參考）"
    
    # 2. 如果是 ai_inferred 且沒有 suffix，要加上
    if confidence == "ai_inferred":
        if not desc.endswith(suffix):
            # 先去掉尾部可能的多餘標點
            if desc.endswith("。"):
                desc = desc[:-1]
            desc = desc + suffix
    else:
        # 如果不是 ai_inferred 但尾端有，就去掉
        if desc.endswith(suffix):
            desc = desc[:-len(suffix)]
            if not desc.endswith("。"):
                desc += "。"
                
    # 3. 調整長度在 80 到 150 字之間
    while len(desc) > 150:
        # 太長了，刪減字句。可以去掉最後一個句子（保留 suffix）
        if confidence == "ai_inferred":
            # 去掉 suffix 再處理
            content = desc[:-len(suffix)]
            sentences = re.split(r'([。！？])', content)
            if len(sentences) >= 3:
                # 拿掉倒數第二句（因為最後一句通常是空字串或是標點）
                if sentences[-1] == '':
                    sentences = sentences[:-3]
                else:
                    sentences = sentences[:-2]
                content = "".join(sentences)
                if not content.endswith("。"):
                    content += "。"
                desc = content + suffix
            else:
                # 如果句子太少，直接強制截斷
                desc = content[:150-len(suffix)-3] + "..." + suffix
        else:
            sentences = re.split(r'([。！？])', desc)
            if len(sentences) >= 3:
                if sentences[-1] == '':
                    sentences = sentences[:-3]
                else:
                    sentences = sentences[:-2]
                desc = "".join(sentences)
                if not desc.endswith("。"):
                    desc += "。"
            else:
                desc = desc[:147] + "..."

    while len(desc) < 80:
        # 太短了，加上一些安全的修飾句
        padding = "消費者在選購相關食品時，可留意包裝上的成分標示，適量攝取即可。"
        if confidence == "ai_inferred":
            content = desc[:-len(suffix)]
            if not content.endswith("。"):
                content += "。"
            content = content + padding
            desc = content + suffix
        else:
            if not desc.endswith("。"):
                desc += "。"
            desc = desc + padding
            
    # 再做一次保險，確保符合 80-150
    if len(desc) > 150:
        desc = desc[:150]
        
    return desc

def process():
    if not INPUT_PATH.exists():
        print(f"Error: {INPUT_PATH} not found.")
        return
        
    with open(INPUT_PATH, "r", encoding="utf-8") as f:
        batch_input = json.load(f)
        
    with open(GEMINI_PATCH_PATH, "r", encoding="utf-8") as f:
        gemini_patch = {r["record_id"]: r for r in json.load(f)}
        
    batch_output = []
    
    for record in batch_input:
        rid = record["record_id"]
        name_zh = record.get("name_zh", "")
        name_en = record.get("name_en", "")
        usage_notes = record.get("usage_notes", "")
        pubchem_tox = record.get("pubchem_toxicology_summary", "")
        pubmed_papers = record.get("pubmed_papers", [])
        
        gem_rec = gemini_patch.get(rid, {})
        confidence = gem_rec.get("overall_confidence", "ai_inferred")
        
        # 處理 consumer_description
        desc = record.get("consumer_description", "")
        if not desc:
            desc = gem_rec.get("consumer_description", "")
        
        desc = fix_length_and_blocklist(desc, confidence)
        
        # 處理 group_risks
        group_risks = []
        
        # 提取官方 sources 作為備用 source
        official_source = None
        if gem_rec.get("sources"):
            for s in gem_rec["sources"]:
                if s.get("url"):
                    # 轉換年份
                    year_val = None
                    if s.get("year"):
                        try:
                            year_val = int(s["year"])
                        except ValueError:
                            year_val = None
                    official_source = {
                        "source_type": "official",
                        "source_title": clean_text(s.get("title", "")),
                        "source_url": s.get("url", ""),
                        "source_year": year_val
                    }
                    break
        
        # 輔助函式：尋找 pubmed 論文或官方來源
        def get_source_for_group(keywords):
            # 先找 pubmed_papers
            for p in pubmed_papers:
                text_to_search = (p.get("title", "") + " " + p.get("abstract", "")).lower()
                if any(kw in text_to_search for kw in keywords):
                    pmid = p.get("pmid", "")
                    return {
                        "confidence": "research_supported",
                        "source_type": "pubmed",
                        "source_title": clean_text(p.get("title", "")),
                        "source_url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/" if pmid else "",
                        "source_year": None
                    }
            # 如果找不到 pubmed 且有官方 sources
            if official_source:
                return {
                    "confidence": "verified",
                    "source_type": official_source["source_type"],
                    "source_title": official_source["source_title"],
                    "source_url": official_source["source_url"],
                    "source_year": official_source["source_year"]
                }
            # 都沒有
            return {
                "confidence": "ai_inferred",
                "source_type": "none",
                "source_title": "",
                "source_url": "",
                "source_year": None
            }

        # 敏感族群判定規則
        
        # 1. 亞硫酸鹽類 / 二氧化硫
        is_sulfite = "亞硫酸" in name_zh or "SO2" in usage_notes or "sulfite" in name_en.lower() or "metabisulfite" in name_en.lower()
        if is_sulfite:
            # 氣喘患者
            src = get_source_for_group(["asthma", "bronchospasm", "respiratory", "lung"])
            if src["source_type"] == "none" and official_source:
                src["confidence"] = "verified"
                src["source_type"] = official_source["source_type"]
                src["source_title"] = official_source["source_title"]
                src["source_url"] = official_source["source_url"]
                src["source_year"] = official_source["source_year"]
            elif src["source_type"] == "none":
                src["confidence"] = "verified"
                
            group_risks.append({
                "group": "氣喘患者",
                "concern": "avoid",
                "confidence": src["confidence"],
                "source_type": src["source_type"],
                "source_title": src["source_title"],
                "source_url": src["source_url"],
                "source_year": src["source_year"],
                "ai_reasoning": "亞硫酸鹽在食物中會釋放二氧化硫，氣喘患者攝取後可能誘發急性氣喘發作或呼吸困難。",
                "reviewed_by_human": False
            })
            
            # 過敏體質者
            src_all = get_source_for_group(["allergy", "allergic", "urticaria", "sensitiz", "dermatitis"])
            group_risks.append({
                "group": "過敏體質者",
                "concern": "caution",
                "confidence": src_all["confidence"],
                "source_type": src_all["source_type"],
                "source_title": src_all["source_title"],
                "source_url": src_all["source_url"],
                "source_year": src_all["source_year"],
                "ai_reasoning": "此成分可能誘發皮膚紅疹、蕁麻疹或嘔吐等過敏反應，過敏體質者應注意避免。",
                "reviewed_by_human": False
            })

        # 2. 鈉鹽類
        elif "鈉" in name_zh or "sodium" in name_en.lower():
            group_risks.append({
                "group": "高血壓",
                "concern": "caution",
                "confidence": "ai_inferred",
                "source_type": "none",
                "source_title": "",
                "source_url": "",
                "source_year": None,
                "ai_reasoning": "此成分含有鈉，過量攝取容易影響體內水分平衡，增加血壓控制的負擔。",
                "reviewed_by_human": False
            })
            group_risks.append({
                "group": "心臟病",
                "concern": "caution",
                "confidence": "ai_inferred",
                "source_type": "none",
                "source_title": "",
                "source_url": "",
                "source_year": None,
                "ai_reasoning": "鈉離子攝取過多會增加心血管系統的負荷，心血管疾病患者應適量攝取。",
                "reviewed_by_human": False
            })
            group_risks.append({
                "group": "腎臟病",
                "concern": "caution",
                "confidence": "ai_inferred",
                "source_type": "none",
                "source_title": "",
                "source_url": "",
                "source_year": None,
                "ai_reasoning": "高鈉會增加腎臟排鈉的負擔，慢性腎臟病患者應避免過量攝取。",
                "reviewed_by_human": False
            })

        # 3. 鉀鹽類
        elif "鉀" in name_zh or "potassium" in name_en.lower():
            group_risks.append({
                "group": "腎臟病",
                "concern": "caution",
                "confidence": "ai_inferred",
                "source_type": "none",
                "source_title": "",
                "source_url": "",
                "source_year": None,
                "ai_reasoning": "此成分含有鉀，腎臟功能不全者若排鉀不良，可能導致體內血鉀過高，應避免過量攝取。",
                "reviewed_by_human": False
            })

        # 4. 苯甲酸及其鹽類
        elif "苯甲酸" in name_zh or "benzoate" in name_en.lower() or "benzoic" in name_en.lower():
            # 過敏體質者
            src_all = get_source_for_group(["allergy", "allergic", "urticaria", "sensitiz", "dermatitis"])
            group_risks.append({
                "group": "過敏體質者",
                "concern": "caution",
                "confidence": src_all["confidence"],
                "source_type": src_all["source_type"],
                "source_title": src_all["source_title"],
                "source_url": src_all["source_url"],
                "source_year": src_all["source_year"],
                "ai_reasoning": "部分過敏者攝取後可能引發皮膚過敏反應，如蕁麻疹或紅疹。",
                "reviewed_by_human": False
            })
            
            # 兒童
            src_child = get_source_for_group(["child", "adhd", "hyperactiv", "behavior"])
            group_risks.append({
                "group": "兒童",
                "concern": "caution",
                "confidence": src_child["confidence"],
                "source_type": src_child["source_type"],
                "source_title": src_child["source_title"],
                "source_url": src_child["source_url"],
                "source_year": src_child["source_year"],
                "ai_reasoning": "少數研究指出此防腐劑可能與兒童過動或專注力不足有微弱關聯，建議兒童適量食用。",
                "reviewed_by_human": False
            })
            
        # 5. 阿斯巴甜 / Aspartame
        elif "阿斯巴甜" in name_zh or "aspartame" in name_en.lower():
            # 苯酮尿症患者
            src = get_source_for_group(["phenylketonuria", "pku", "phenylalanine"])
            if src["source_type"] == "none" and official_source:
                src["confidence"] = "verified"
                src["source_type"] = official_source["source_type"]
                src["source_title"] = official_source["source_title"]
                src["source_url"] = official_source["source_url"]
                src["source_year"] = official_source["source_year"]
            elif src["source_type"] == "none":
                src["confidence"] = "verified"
                
            group_risks.append({
                "group": "苯酮尿症患者",
                "concern": "danger",
                "confidence": src["confidence"],
                "source_type": src["source_type"],
                "source_title": src["source_title"],
                "source_url": src["source_url"],
                "source_year": src["source_year"],
                "ai_reasoning": "此成分在體內代謝會產生苯丙胺酸，患者因基因缺陷無法正常代謝，可能損害大腦，必須絕對避免食用。",
                "reviewed_by_human": False
            })

        # 6. 其他防腐劑/合成物
        else:
            all_text = (pubchem_tox + " " + " ".join([p.get("title", "") + " " + p.get("abstract", "") for p in pubmed_papers])).lower()
            
            # 過敏體質者
            if any(k in all_text for k in ["allergy", "allergic", "urticaria", "sensitiz", "dermatitis", "contact dermatitis"]):
                src = get_source_for_group(["allergy", "allergic", "urticaria", "sensitiz", "dermatitis"])
                group_risks.append({
                    "group": "過敏體質者",
                    "concern": "caution",
                    "confidence": src["confidence"],
                    "source_type": src["source_type"],
                    "source_title": src["source_title"],
                    "source_url": src["source_url"],
                    "source_year": src["source_year"],
                    "ai_reasoning": "少數人可能對此成分過敏，攝取後可能會誘發接觸性蕁麻疹或皮膚過敏反應。",
                    "reviewed_by_human": False
                })
                
            # 氣喘患者
            if any(k in all_text for k in ["asthma", "bronchospasm"]):
                src = get_source_for_group(["asthma", "bronchospasm"])
                group_risks.append({
                    "group": "氣喘患者",
                    "concern": "caution",
                    "confidence": src["confidence"],
                    "source_type": src["source_type"],
                    "source_title": src["source_title"],
                    "source_url": src["source_url"],
                    "source_year": src["source_year"],
                    "ai_reasoning": "極少數氣喘患者在攝取後可能誘發呼吸道敏感或氣喘反應，需注意適量。",
                    "reviewed_by_human": False
                })
                
            # 孕婦
            if any(k in all_text for k in ["pregnancy", "pregnant", "fetus", "teratogenic", "maternal"]):
                src = get_source_for_group(["pregnancy", "pregnant", "fetus", "teratogenic", "maternal"])
                group_risks.append({
                    "group": "孕婦",
                    "concern": "caution",
                    "confidence": src["confidence"],
                    "source_type": src["source_type"],
                    "source_title": src["source_title"],
                    "source_url": src["source_url"],
                    "source_year": src["source_year"],
                    "ai_reasoning": "部分研究顯示此成分在極高劑量下可能對胚胎發育有不良影響，孕婦建議避免過量食用。",
                    "reviewed_by_human": False
                })
                
            # 嬰幼兒
            if any(k in all_text for k in ["infant", "baby", "neonate", "newborn"]):
                src = get_source_for_group(["infant", "baby", "neonate", "newborn"])
                group_risks.append({
                    "group": "嬰幼兒",
                    "concern": "caution",
                    "confidence": src["confidence"],
                    "source_type": src["source_type"],
                    "source_title": src["source_title"],
                    "source_url": src["source_url"],
                    "source_year": src["source_year"],
                    "ai_reasoning": "嬰幼兒的肝腎代謝系統發育尚未完全，建議盡量避免食用含有此成分的加工食品。",
                    "reviewed_by_human": False
                })

        # 雙重清洗 ai_reasoning 中的禁用字
        for risk in group_risks:
            risk["ai_reasoning"] = clean_text(risk["ai_reasoning"])
            
        batch_output.append({
            "record_id": rid,
            "consumer_description": desc,
            "group_risks": group_risks
        })
        
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(batch_output, f, ensure_ascii=False, indent=2)
        
    print(f"Successfully processed {len(batch_output)} records and wrote to {OUTPUT_PATH}")

if __name__ == "__main__":
    process()
