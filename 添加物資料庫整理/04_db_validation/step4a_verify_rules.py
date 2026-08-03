#!/usr/bin/env python3
# ADI 擷取結果的「確定性驗證閘門」。純程式、不呼叫任何 LLM/API。
# 對每筆 AGY 產出的 {adi, source_url, quote} 做兩道檢查:
#   1. quote 是否真的出現在該 record 的來源原文裡(忽略空白差異)
#   2. adi 裡的每段數字是否都出現在 quote 裡
# 兩道都過才算 PASS;否則標可疑,寫進 review.csv,絕不放行進 DB。
import json, csv, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
INPUTS = os.path.join(HERE, '..', '03_llm_ingestion', 'inputs')
RESULTS = os.path.join(HERE, '..', '03_llm_ingestion', 'outputs', 'results.csv')
REVIEW = os.path.join(HERE, '..', '03_llm_ingestion', 'outputs', 'review.csv')

def norm(s):
    # 統一空白:把所有連續空白(含換行)壓成單一空格,方便子字串比對
    return re.sub(r'\s+', ' ', (s or '')).strip().lower()

def load_input(record_id):
    fp = os.path.join(INPUTS, record_id + '.json')
    return json.load(open(fp)) if os.path.exists(fp) else {}

def source_text(record_id):
    d = load_input(record_id)
    return ' '.join(p.get('content', '') for p in d.get('pages', []))

def known_names(record_id):
    d = load_input(record_id)
    names = [d.get('name_zh', ''), d.get('name_en', '')] + (d.get('aliases') or [])
    return [norm(n) for n in names if n]

def digit_runs(s):
    # 抓出所有數字串,例如 "0-25 mg/kg" -> ['0','25']
    return re.findall(r'\d+', s or '')

# 「不是 ADI」的概念字:每週/每月耐受量等,與每日 ADI 不同,易被誤抄
NOT_ADI_TERMS = [
    'weekly intake', 'tolerable weekly', 'per week', 'ptwi',
    'monthly intake', 'tolerable monthly', 'ptmi',
]

def not_adi_concept(quote):
    q = (quote or '').lower()
    return [t for t in NOT_ADI_TERMS if t in q]

# ADI 權威來源白名單:只有這些網域的 ADI 才採信(JECFA為主、EFSA次之、衛福部)
# 水管(Tavily/wiki/直接抓)無所謂,只認最後落地的頁面是不是權威。
# 特例:inchem.org 底下混有 ICSC(吸入)、PIM(中毒)等非食品文件,
#       故只認 /documents/jecfa/ 子目錄(JECFA 食品添加物評估)。
AUTH_DOMAINS = [
    'efsa.europa.eu', 'who.int', 'fao.org',
    'codexalimentarius', 'mohw.gov.tw', 'fda.gov.tw',
]

def is_authoritative(url):
    u = (url or '').lower()
    if 'inchem.org' in u:
        return '/jecfa/' in u          # inchem 只認 JECFA 子目錄
    return any(d in u for d in AUTH_DOMAINS)

def main():
    if not os.path.exists(RESULTS):
        print(f"找不到 {RESULTS},AGY 還沒產出結果。")
        sys.exit(1)
    rows = list(csv.DictReader(open(RESULTS)))
    npass = nunknown = nfail = 0
    fails = []
    for r in rows:
        rid = r['record_id']
        adi = (r.get('adi') or '').strip()
        quote = r.get('quote') or ''
        if adi == '' or adi.lower() == 'unknown':
            nunknown += 1
            continue
        src = norm(source_text(rid))
        nq = norm(quote)
        reasons = []
        # 檢查一:引文必須在原文裡
        if not nq:
            reasons.append('無引文')
        elif nq not in src:
            reasons.append('引文不在原文(疑似捏造/改寫)')
        # 檢查二:adi 的每段數字都要在引文裡
        missing = [d for d in digit_runs(adi) if d not in digit_runs(quote)]
        if missing:
            reasons.append('數字未出現在引文:' + ','.join(missing))
        # 檢查三:引文是否其實在講「每週/每月耐受量」而非每日 ADI
        bad_terms = not_adi_concept(quote)
        if bad_terms:
            reasons.append('引文是每週/每月耐受量非ADI:' + ','.join(bad_terms))
        # 檢查四:來源網域是否為 ADI 權威(非權威即不採信)
        if not is_authoritative(r.get('source_url')):
            reasons.append('來源非權威網域:' + (r.get('source_url') or '(無)'))
        # 檢查五:身分確認(只在結果含 matched_name 欄時啟用,舊資料自動略過)
        if 'matched_name' in r:
            mn = norm(r.get('matched_name'))
            names = known_names(rid)
            if not mn:
                reasons.append('未確認身分(matched_name 空白)')
            elif names and not any(mn in n or n in mn for n in names):
                reasons.append('身分對不上已知名稱/別名:' + (r.get('matched_name') or ''))
        if reasons:
            nfail += 1
            fails.append({**r, 'fail_reason': '；'.join(reasons)})
        else:
            npass += 1

    if fails:
        with open(REVIEW, 'w', newline='') as f:
            w = csv.DictWriter(f, fieldnames=list(fails[0].keys()))
            w.writeheader(); w.writerows(fails)

    print('=== ADI 驗證結果 ===')
    print(f'總計            : {len(rows)} 筆')
    print(f'PASS(有原文撐腰): {npass} 筆')
    print(f'unknown(查無)   : {nunknown} 筆')
    print(f'可疑(未通過)    : {nfail} 筆' + (f'  -> {REVIEW}' if fails else ''))
    if fails:
        print('\n--- 可疑明細 ---')
        for r in fails:
            print(f"  {r['record_id']} adi='{r['adi']}' :: {r['fail_reason']}")

if __name__ == '__main__':
    main()
