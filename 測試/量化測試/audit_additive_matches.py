#!/usr/bin/env python3
# A3 量測（一）與（三）：全量判定稽核＋相似度漏配偵測（2026-08-03，補交腳本 2026-08-04）。
#
# 〈實驗與效能量化〉A3 記錄了三項量測的數字，但只有第二項（官方名稱涵蓋測試，見
# eval_official_name_coverage.py）與新舊做法回測（eval_ingredient_parse.py）留下腳本，
# 這兩項當時是臨時跑的。本檔把它們補成可重跑的形式，數字才有人能自行驗證。
#
# 兩項合寫成一支的理由：都要走同一趟比對結果。第一項看「被判為添加物的那些」，
# 第三項看「兩邊都沒命中的那些」，是同一份判定的正反兩面，分成兩支會把同一批
# 48 案跑兩次，且兩份數字若因中途改動而對不起來反而更難查。
#
# 完全離線、不花錢：吃 predictions/ 裡已存的 48 筆辨識結果，配本機資料庫的官方
# 添加物清單。不呼叫任何 API、不重跑影像辨識；向量比對以替身停用（見 NoVectorRAG），
# 確保即使環境變數開著也不會送出請求。
#
# 用法：cd 測試/量化測試 && ../../server/venv/bin/python audit_additive_matches.py
import os, sys, json, glob, csv, re

# Windows 主控台預設 cp950，案例名稱裡有它編不出來的字（「塩」「菓」…）。
# 沒有這兩行，整批任務會在中途拋 UnicodeEncodeError 死掉（2026-09-07 炸過兩次）。
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.stderr.reconfigure(encoding='utf-8', errors='replace')

HERE = os.path.dirname(os.path.abspath(__file__))
SERVER = os.path.abspath(os.path.join(HERE, '..', '..', 'server'))
sys.path.insert(0, SERVER)

import psycopg2                                    # noqa: E402
from psycopg2.extras import RealDictCursor         # noqa: E402
from dotenv import load_dotenv                     # noqa: E402

load_dotenv(os.path.join(SERVER, '.env'))
from module_a.ingredient_matching import (          # noqa: E402
    match_ingredients, normalize_text, _candidate_names, _is_generic_term,
    _best_additive_match, _load_generic_terms,
)

# 相似度列出門檻。0.45 是啟發式篩選的取捨點，不是「相似即同物」的判準——
# 列出來的每一項都要人工複查，本腳本不做任何自動認定。
JACCARD_THRESHOLD = 0.45


class NoVectorRAG:
    """停用語義比對的替身，見檔頭說明。"""
    is_initialized = True

    def update_knowledge_base(self, knowledge):
        pass

    def find_nearest(self, name):
        return None


def db_cursor():
    conn = psycopg2.connect(
        host=os.getenv('DB_HOST', 'localhost'), port=os.getenv('DB_PORT', 5432),
        dbname=os.getenv('DB_NAME', 'product_db'), user=os.getenv('DB_USER', 'postgres'),
        password=os.getenv('DB_PASSWORD', ''))
    return conn, conn.cursor(cursor_factory=RealDictCursor)


def load_knowledge(cursor) -> list:
    """
    取出添加物庫並預先正規化，欄位與 match_ingredients 內部所建者一致。

    必須自己建一份，不能只讀 _substance_names_cache（那是攤平的名稱集合，
    分不出「哪一個名稱屬於哪一筆」，也判不出命中方式）。取用的是 module_a 匯出的
    _best_additive_match，比對規則本身仍只有正式碼一份，此處不重寫規則。
    """
    cursor.execute("SELECT id, record_id, name_zh, name_en, aliases, ins_or_e_number, "
                   "category, food_tech_purpose, adi, medical_caution, iarc_class, "
                   "description, risks, description_sources FROM additives "
                   "ORDER BY LENGTH(name_zh) DESC")
    knowledge = cursor.fetchall()
    for a in knowledge:
        a['_n_zh'] = normalize_text(a.get('name_zh') or '')
        a['_n_zh_parts'] = [x for x in
                            (normalize_text(p) for p in re.split(r"[;；、]", a.get('name_zh') or ''))
                            if x]
        # ins_or_e_number 不再預先正規化：比對層已無編號規則（2026-08-06 移除）。
        # SELECT 仍取該欄，供報表顯示配到哪一筆的官方編號。
        _al = a.get('aliases')
        if isinstance(_al, str):
            try:
                _al = json.loads(_al)
            except Exception:
                _al = [_al]
        a['_n_aliases'] = [normalize_text(x) for x in _al if x] if isinstance(_al, list) else []
    return knowledge


def classify_hit(ing: str, knowledge: list) -> dict:
    """
    這一項添加物判定是**憑什麼**命中的。

    分「名稱完全相等或官方別名」與「非完全相等」兩類，因為兩者的錯誤風險不同：
    官方採正面表列，名稱完全相符即為查表結果，不可能配錯人；片段相符才有誤配空間
    （「麩酸鈉」片段命中「L-麩酸」就是這樣配錯的）。

    2026-08-06：`ins_number` 一類已移除。比對層的編號規則實測命中 0 次後刪除，
    此處若保留該標籤，會把實際靠片段命中、而編號恰好出現在成分名裡的項目
    標成「靠編號命中」，讓稽核報表與實際規則不一致。

    命中的候選名稱必須跟正式流程取同一個——一項成分會展開成多個候選（括號內外
    都試），正式流程是「第一個命中的候選就採用」。若這裡改成「找有沒有任何一個
    候選完全相等」，就會把實際上靠片段命中的項目算進完全相等那一類，把數字做漂亮。
    """
    for cand in (_candidate_names(ing) or [normalize_text(ing)]):
        if _is_generic_term(cand):
            continue                       # 類別統稱不比對物質庫，與正式流程一致
        match = _best_additive_match(cand, knowledge)
        if not match:
            continue
        if cand == match['_n_zh'] or cand in match['_n_zh_parts']:
            basis = 'exact_name'           # 與官方品名完全相等
        elif cand in match['_n_aliases']:
            basis = 'exact_alias'          # 與官方公告之通用名稱／別名完全相等
        else:
            basis = 'substring'            # 片段相符
        return {'basis': basis, 'candidate': cand,
                'official': match.get('name_zh') or '', 'ins': match.get('ins_or_e_number') or ''}
    # 判定為添加物卻在此重跑不出命中，代表兩邊的比對條件不一致，必須看得見而非靜默
    return {'basis': 'unreproduced', 'candidate': normalize_text(ing), 'official': '', 'ins': ''}


def bigrams(s: str) -> set:
    """字元二元組。長度不足 2 者退回單字元，否則空集合會讓相似度恆為 0。"""
    return {s[i:i + 2] for i in range(len(s) - 1)} if len(s) >= 2 else ({s} if s else set())


def jaccard(a: set, b: set) -> float:
    return len(a & b) / len(a | b) if (a or b) else 0.0


def nearest_by_similarity(norm: str, name_index: list) -> tuple:
    """在庫內找字面最相近的名稱。回傳 (相似度, 官方名稱)。"""
    bg = bigrams(norm)
    best = (0.0, '')
    for name, bg_name in name_index:
        s = jaccard(bg, bg_name)
        if s > best[0]:
            best = (s, name)
    return best


def main():
    conn, cursor = db_cursor()
    _load_generic_terms(cursor)
    knowledge = load_knowledge(cursor)

    # 相似度比對的對象是庫內全部名稱（正式名的各分段＋別名），不只正式名——
    # 漏配常見成因就是標示用了俗名，只比正式名會把已收錄的俗名也算成漏配。
    name_index = []
    seen_names = set()
    for a in knowledge:
        for n in list(a['_n_zh_parts']) + list(a['_n_aliases']):
            if n and n not in seen_names:
                seen_names.add(n)
                name_index.append((n, bigrams(n)))

    rag = NoVectorRAG()
    n_cases = n_skipped = 0
    n_items = n_additive = 0
    basis_counts = {}
    review, per_case = [], []
    # 疑似漏配依「標示寫法」歸戶，不依出現次數——同一個寫法在三個商品出現是同一件
    # 待查事項，列三遍會讓待查數字隨樣本數膨脹。出現處另行記錄，仍找得回原案。
    missed_by_label = {}

    for p in sorted(glob.glob(os.path.join(HERE, 'predictions', '*.json'))):
        cid = os.path.basename(p)[:-5]
        pred = json.load(open(p, encoding='utf-8'))
        pred = pred.get('prediction') or pred or {}
        raw = pred.get('ingredients_raw') or ''
        ing_list = pred.get('ingredients_list') or []
        if not ing_list and not raw:
            n_skipped += 1                 # 非食品案例，沒有成分可稽核
            continue
        n_cases += 1

        res = match_ingredients(ing_list, None, cursor, rag, ingredients_raw=raw)
        types = res['calculated_ingredient_types']
        cov = res['coverage']

        n_items += len(types)
        case_additive = case_nonexact = 0

        for ing, kind in types.items():
            if kind != 'additive':
                continue
            n_additive += 1
            case_additive += 1
            hit = classify_hit(ing, knowledge)
            basis_counts[hit['basis']] = basis_counts.get(hit['basis'], 0) + 1
            if hit['basis'] in ('exact_name', 'exact_alias'):
                continue
            # 非完全相等者全部攤開供人工檢視。是否為同一物質沒有可自動判定的正解
            # （須主管機關公告之對照關係方有依據），故本腳本只負責找出來、不下結論。
            case_nonexact += 1
            review.append({'case_id': cid, 'label': ing, 'matched_candidate': hit['candidate'],
                           'basis': hit['basis'], 'official_name': hit['official'],
                           'ins': hit['ins'], 'verdict': ''})

        # 兩個資料庫都沒命中者：找庫內字面最相近的名稱，超過門檻即列出供複查。
        for ing in cov['unknown_items']:
            norm = normalize_text(ing)
            score, near = nearest_by_similarity(norm, name_index)
            if score >= JACCARD_THRESHOLD:
                row = missed_by_label.setdefault(
                    ing, {'label': ing, 'nearest_official': near,
                          'jaccard': round(score, 3), 'n_cases': 0, 'cases': [], 'verdict': ''})
                row['n_cases'] += 1
                row['cases'].append(cid)

        per_case.append({'case_id': cid, 'n_items': cov['total'],
                         'n_additive': case_additive, 'n_nonexact': case_nonexact,
                         'n_unknown': cov['unknown']})

    cursor.close(); conn.close()

    missed = sorted(missed_by_label.values(), key=lambda r: -r['jaccard'])
    for r in missed:
        r['cases'] = '、'.join(r['cases'])

    n_exact = basis_counts.get('exact_name', 0) + basis_counts.get('exact_alias', 0)
    summary = {
        'n_cases': n_cases,
        'n_skipped': n_skipped,
        '標示項目總數': n_items,
        '判定為添加物': n_additive,
        '名稱完全相等或官方別名': n_exact,
        '完全相等比率': round(n_exact / n_additive, 3) if n_additive else None,
        '非完全相等': n_additive - n_exact,
        '命中方式分佈': basis_counts,
        '相似度疑似漏配': {
            '門檻': JACCARD_THRESHOLD,
            '列出寫法數': len(missed),
            '_說明': ('啟發式篩選，非窮舉；標示寫法與官方名稱差異極大者本方法抓不到。'
                      '列出者含真正的漏配與純屬字面相近的食材（如乾酪 vs 乾酪素），'
                      '須人工逐項複查後才是漏配數。'),
        },
        'config': {'vector_rag': 'disabled', 'source': 'predictions/ 既有辨識結果'},
        '_說明': ('非完全相等與疑似漏配皆為「待人工判定」，本腳本不下結論。'
                  '判定標示寫法是否對應至某官方品項屬規範性問題，'
                  '須主管機關公告對照關係方有依據。'),
    }

    res_dir = os.path.join(HERE, 'results')
    os.makedirs(res_dir, exist_ok=True)
    json.dump(summary, open(os.path.join(res_dir, 'additive_audit_summary.json'), 'w',
                            encoding='utf-8'), ensure_ascii=False, indent=2)
    for fname, rows, cols in (
        ('additive_audit_review.csv', review,
         ['case_id', 'label', 'matched_candidate', 'basis', 'official_name', 'ins', 'verdict']),
        ('additive_audit_missed.csv', missed,
         ['label', 'nearest_official', 'jaccard', 'n_cases', 'cases', 'verdict']),
        ('additive_audit_per_case.csv', per_case,
         ['case_id', 'n_items', 'n_additive', 'n_nonexact', 'n_unknown']),
    ):
        with open(os.path.join(res_dir, fname), 'w', newline='', encoding='utf-8-sig') as f:
            w = csv.DictWriter(f, fieldnames=cols)
            w.writeheader(); w.writerows(rows)

    print('=== A3 全量判定稽核 ＋ 相似度漏配偵測（向量比對停用）===')
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"\n非完全相等待檢視: {res_dir}/additive_audit_review.csv"
          f"\n疑似漏配待複查:   {res_dir}/additive_audit_missed.csv"
          f"\n逐案:             {res_dir}/additive_audit_per_case.csv"
          f"\n彙總:             {res_dir}/additive_audit_summary.json")


if __name__ == '__main__':
    main()
