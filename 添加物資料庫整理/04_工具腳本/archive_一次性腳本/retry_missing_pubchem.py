"""
重試 PubChem 查無資料的記錄（使用改良後的名稱變體邏輯）

用法：
  python retry_missing_pubchem.py

說明：
  1. 讀取 all_slim_pubchem_patch.json，找出 _error 的 22 筆
  2. 用改良後的 name_variants()（含斜線分割、括號內容提取）重新查詢
  3. 成功查到的記錄更新回 patch 檔；仍失敗的保留 _error 並記錄已嘗試的變體
"""

import json
import os
import re
import sys
import time
from urllib.parse import quote

try:
    import requests
except ImportError:
    print("請先安裝 requests：pip install requests")
    sys.exit(1)

sys.stdout.reconfigure(encoding='utf-8')

SCRIPT_DIR  = os.path.dirname(os.path.abspath(__file__))
ROOT        = os.path.join(SCRIPT_DIR, '..')
MASTER_PATH = os.path.join(ROOT, '01_主資料庫', 'additives_master_candidate.json')
ENRICH_DIR  = os.path.join(ROOT, '03_enrichment補充')
PATCH_PATH  = os.path.join(SCRIPT_DIR, 'all_slim_pubchem_patch.json')
RAW_DIR     = os.path.join(SCRIPT_DIR, 'pubchem_raw')

PUBCHEM_PUG  = "https://pubchem.ncbi.nlm.nih.gov/rest/pug"
PUBCHEM_VIEW = "https://pubchem.ncbi.nlm.nih.gov/rest/pug_view"
DELAY = 0.3

# ── 名稱變體邏輯（改良版）──────────────────────────────────────────────
GREEK_MAP = {
    'α': 'alpha', 'β': 'beta',  'γ': 'gamma', 'δ': 'delta',
    'ε': 'epsilon','ζ': 'zeta', 'η': 'eta',   'θ': 'theta',
    'μ': 'mu',    'ω': 'omega',
}
_SUFFIX_RE = re.compile(
    r'\s+(Concentrate|Concentrates|Solution(?:\s+\d+\s*%)?|Extract|Powder|'
    r'Hydrate|Anhydrous|Monohydrate|Dihydrate|Trihydrate)\s*$',
    re.IGNORECASE,
)
_PARENS_RE = re.compile(r'[（(][^）)]*[）)]')


def _replace_greek(s):
    for ch, word in GREEK_MAP.items():
        s = s.replace(ch, word)
    return s


def _normalize_fullwidth(s):
    """全形數字/英文/符號 → 半形"""
    result = []
    for ch in s:
        cp = ord(ch)
        if 0xFF01 <= cp <= 0xFF5E:  # 全形 ！ ~ ～
            result.append(chr(cp - 0xFEE0))
        else:
            result.append(ch)
    return ''.join(result)


def name_variants(name_en, name_zh=''):
    # 先做全形正規化
    name_en_norm = _normalize_fullwidth(name_en)

    no_parens  = _PARENS_RE.sub('', name_en_norm).strip()
    greek      = _replace_greek(name_en_norm)
    both       = _replace_greek(no_parens)
    no_suffix  = _SUFFIX_RE.sub('', name_en_norm).strip()
    full_clean = _SUFFIX_RE.sub('', both).strip()

    seen, result = set(), []

    def add(v):
        v = v.strip()
        if v and v not in seen:
            seen.add(v)
            result.append(v)

    for v in [name_en_norm, no_parens, greek, both, no_suffix, full_clean]:
        add(v)

    # 斜線分隔子名稱
    slash_parts = re.split(r'\s*/\s*', name_en_norm)
    if len(slash_parts) > 1:
        for part in slash_parts:
            part = part.strip()
            if part and len(part) > 3:
                add(part)
                add(_PARENS_RE.sub('', part).strip())
                add(_replace_greek(part))
                add(_SUFFIX_RE.sub('', _replace_greek(_PARENS_RE.sub('', part))).strip())

    # " or " 分隔子名稱（如 "EDTA Na2 or EDTA CaNa2"）
    or_parts = re.split(r'\s+or\s+', name_en_norm, flags=re.IGNORECASE)
    if len(or_parts) > 1:
        for part in or_parts:
            part = part.strip()
            if part and len(part) > 3:
                add(part)

    # 括號內容（整個名稱都在括號內時）
    if not no_parens:
        for inner in re.findall(r'[（(]([^）)]+)[）)]', name_en_norm):
            inner = inner.strip()
            if inner:
                add(inner)
                add(_replace_greek(inner))
                # 去掉前綴形容詞（如 "Synthetic Genistein" → "Genistein"）
                words = inner.split()
                if len(words) > 1:
                    add(' '.join(words[1:]))

    add(name_zh)
    return result


# ── PubChem API ───────────────────────────────────────────────────────
def get_cid(name):
    url = f"{PUBCHEM_PUG}/compound/name/{quote(name)}/cids/JSON"
    try:
        r = requests.get(url, timeout=15)
        if r.status_code == 200:
            cids = r.json().get("IdentifierList", {}).get("CID", [])
            return cids[0] if cids else None
    except Exception as e:
        print(f"    ⚠️  CID 查詢失敗（{name}）：{e}")
    return None


def get_cid_by_autocomplete(query):
    url = f"https://pubchem.ncbi.nlm.nih.gov/rest/autocomplete/compound/{quote(query)}/JSON?limit=1"
    try:
        r = requests.get(url, timeout=15)
        if r.status_code == 200:
            items = r.json().get("dictionary_terms", {}).get("compound", [])
            if items:
                return get_cid(items[0])
    except Exception as e:
        print(f"    ⚠️  autocomplete 失敗（{query}）：{e}")
    return None


def get_cid_with_fallbacks(name_en, name_zh=''):
    variants = name_variants(name_en, name_zh)
    tried = []
    for variant in variants:
        time.sleep(DELAY)
        tried.append(variant)
        cid = get_cid(variant)
        if cid:
            return cid, variant, tried

    time.sleep(DELAY)
    cid = get_cid_by_autocomplete(name_en)
    if cid:
        return cid, f"[autocomplete] {name_en}", tried

    return None, None, tried


def fetch_full_data(cid):
    """抓取 PubChem property + PUG View 原文"""
    props = {}
    url = f"{PUBCHEM_PUG}/compound/cid/{cid}/property/MolecularFormula,MolecularWeight,IUPACName,InChIKey,IsomericSMILES/JSON"
    try:
        r = requests.get(url, timeout=15)
        if r.status_code == 200:
            props = r.json().get("PropertyTable", {}).get("Properties", [{}])[0]
    except Exception:
        pass

    time.sleep(DELAY)
    cas = None
    synonyms = []
    url2 = f"{PUBCHEM_PUG}/compound/cid/{cid}/synonyms/JSON"
    try:
        r = requests.get(url2, timeout=15)
        if r.status_code == 200:
            syns = r.json().get("InformationList", {}).get("Information", [{}])[0].get("Synonym", [])
            for s in syns:
                if re.match(r'^\d{2,7}-\d{2}-\d$', s):
                    if not cas:
                        cas = s
                else:
                    synonyms.append(s)
    except Exception:
        pass

    return props, cas, synonyms[:20]


_SKIP_SECTIONS = {
    "NMR Spectra", "IR Spectra", "Mass Spectra", "UV-Vis Spectra",
    "X-ray Diffraction", "Crystal Structure", "Patents",
    "Literature", "Chemical Reactions", "Environmental Fate",
    "Ecological Information",
}

def extract_raw_sections(data, prefix=''):
    """遞迴提取所有段落文字"""
    sections = {}
    if isinstance(data, dict):
        heading = data.get("TOCHeading", "")
        if heading and heading in _SKIP_SECTIONS:
            return {}
        info = data.get("Information", [])
        texts = []
        for item in info:
            for val in item.get("Value", {}).get("StringWithMarkup", []):
                t = val.get("String", "").strip()
                if t:
                    texts.append(t)
        key = f"{prefix}/{heading}".strip("/") if heading else prefix
        if texts and key:
            sections[key] = "\n".join(texts[:5])
        for sub in data.get("Section", []):
            sections.update(extract_raw_sections(sub, key))
    elif isinstance(data, list):
        for item in data:
            sections.update(extract_raw_sections(item, prefix))
    return sections


def fetch_raw_sections(cid):
    url = f"{PUBCHEM_VIEW}/data/compound/{cid}/JSON"
    try:
        r = requests.get(url, timeout=30)
        if r.status_code == 200:
            data = r.json()
            sections_list = data.get("Record", {}).get("Section", [])
            return extract_raw_sections(sections_list)
    except Exception as e:
        print(f"    ⚠️  PUG View 失敗（CID {cid}）：{e}")
    return {}


def extract_ghs(raw_sections):
    codes = set()
    for key, text in raw_sections.items():
        if "GHS" in key or "Hazard" in key:
            for m in re.finditer(r'\bH\d{3}\b', text):
                codes.add(m.group())
    return sorted(codes)


def extract_jecfa_info(raw_sections):
    adi_value, adi_status = None, "unknown"
    for key, text in raw_sections.items():
        if "JECFA" not in key and "jecfa" not in key.lower():
            continue
        combined = text.lower()
        if "not limited" in combined or "not specified" in combined or "acceptable" in combined:
            adi_status = "not_needed"
        if "not allocated" in combined or "no adi" in combined:
            adi_status = "not_allocated"
        m = re.search(
            r'\badi\b[^0-9\n]{0,30}(\d[\d\s\-–\.]*(?:mg|µg|ug)/kg[^\n\|;]{0,60})',
            text, re.IGNORECASE
        )
        if m:
            adi_value = m.group(1).strip()
            adi_status = "established"
            break
    return adi_value, adi_status


# ── 主程式 ────────────────────────────────────────────────────────────
def main():
    with open(MASTER_PATH, encoding='utf-8') as f:
        master = {r['record_id']: r for r in json.load(f)}

    with open(PATCH_PATH, encoding='utf-8') as f:
        patch_list = json.load(f)

    patch_dict = {p['record_id']: p for p in patch_list}
    missing = [p for p in patch_list if '_error' in p]
    print(f"待重試：{len(missing)} 筆\n{'='*50}")

    recovered = 0
    for i, old_patch in enumerate(missing, 1):
        rid = old_patch['record_id']
        rec = master.get(rid, {})
        name_zh = rec.get('additive_name_zh', '')
        name_en = rec.get('additive_name_en', '')
        print(f"[{i}/{len(missing)}] {rid} {name_zh}", end=' ', flush=True)

        cid, hit_name, tried = get_cid_with_fallbacks(name_en, name_zh)

        if not cid:
            print(f"❌ 仍查無資料（試過 {len(tried)} 個變體）")
            patch_dict[rid]['_skip_reason'] = '已嘗試變體：' + str(tried)
            continue

        print(f"✅ CID={cid}（命中：{hit_name}）")
        recovered += 1

        props, cas, synonyms = fetch_full_data(cid)
        time.sleep(DELAY)
        raw_sections = fetch_raw_sections(cid)
        time.sleep(DELAY)

        raw_file_rel = f"pubchem_raw/{rid}.json"
        raw_file_abs = os.path.join(SCRIPT_DIR, raw_file_rel)
        existing = {}
        if os.path.exists(raw_file_abs):
            with open(raw_file_abs, encoding='utf-8') as f:
                existing = json.load(f)
        existing['pubchem'] = raw_sections
        with open(raw_file_abs, 'w', encoding='utf-8') as f:
            json.dump(existing, f, ensure_ascii=False, indent=2)

        ghs_codes = extract_ghs(raw_sections)
        adi_value, adi_status = extract_jecfa_info(raw_sections)

        new_patch = {
            'record_id':       rid,
            'name_zh':         name_zh,
            'name_en':         name_en,
            'pubchem_cid':     cid,
            'pubchem_hit_name': hit_name,
            'cas_number':      cas,
            'molecular_formula': props.get('MolecularFormula'),
            'molecular_weight':  props.get('MolecularWeight'),
            'iupac_name':      props.get('IUPACName'),
            'inchikey':        props.get('InChIKey'),
            'ghs_hazard_codes': ghs_codes,
            'adi_value':       adi_value,
            'adi_status':      adi_status,
            'pubchem_raw_file': raw_file_rel,
        }
        patch_dict[rid] = new_patch

        with open(PATCH_PATH, 'w', encoding='utf-8') as f:
            json.dump(list(patch_dict.values()), f, ensure_ascii=False, indent=2)

    print(f"\n{'='*50}")
    print(f"✅ 重試完成：成功補回 {recovered} / {len(missing)} 筆")
    still_missing = sum(1 for p in patch_dict.values() if '_error' in p)
    print(f"   仍然查無資料：{still_missing} 筆 → 交由 handle_missing.py 處理")
    print(f"   已更新：{PATCH_PATH}")


if __name__ == '__main__':
    main()
