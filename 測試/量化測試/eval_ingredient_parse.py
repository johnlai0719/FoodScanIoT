#!/usr/bin/env python3
# 回測：比較「用模型整理的成分清單」與「自己解析標示原文」兩種做法（2026-07-30）。
#
# 完全離線、不花錢：吃 predictions/ 裡已存的 48 筆辨識結果（原文與清單都在裡面），
# 配本機資料庫的官方添加物與原料清單，同一批資料跑兩次比對。不呼叫任何 API、
# 不重跑影像辨識。
#
# 向量比對在本回測中停用（find_nearest 一律回 None）。它要打 embedding API，
# 每跑一次都要錢，且兩種做法都受它影響；停掉可讓差異單純來自成分項目的來源，
# 這也是本次變更唯一動到的地方。停用的代價是涵蓋率會低於線上實際值。
#
# 用法：cd 測試/量化測試 && ../../server/venv/bin/python eval_ingredient_parse.py
import os, sys, json, glob, csv, re

HERE = os.path.dirname(os.path.abspath(__file__))
SERVER = os.path.abspath(os.path.join(HERE, '..', '..', 'server'))
sys.path.insert(0, SERVER)

import psycopg2                                    # noqa: E402
from psycopg2.extras import RealDictCursor         # noqa: E402
from dotenv import load_dotenv                     # noqa: E402

load_dotenv(os.path.join(SERVER, '.env'))
from module_a.ingredient_matching import match_ingredients, normalize_text  # noqa: E402
from module_a import ingredient_matching as im     # noqa: E402

# Windows 主控台預設 cp950，案例名稱裡有它編不出來的字（「塩」「菓」…）。
# 沒有這兩行的後果不是印出亂碼，而是**整批任務在中途拋 UnicodeEncodeError 死掉**
# ——2026-09-07 run_eval 炸在第 10 案的「塩」、intake 炸在第 11 案的「菓」，
# 後者還留下半套用狀態（照片已搬、cases.json 沒存）。
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.stderr.reconfigure(encoding='utf-8', errors='replace')


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


def additive_labels(result: dict) -> set:
    """
    取出被判為添加物的項目，用**標示上寫的名稱**當鍵。

    要用標示這一側的名稱，不能用官方品名（2026-07-30 踩過兩次）：官方品名欄位
    常一格塞兩個名稱（「醋酸鈉； 醋酸鈉（無水）」），而 chemical 的顯示名稱在有
    英文名時會組成「Sodium Acetate (維生素C(抗氧化劑))」，用括號回推標示名稱會
    抓到最內層的「抗氧化劑」。calculated_ingredient_types 的鍵就是標示原樣，直接用。
    """
    return {k for k, v in result['calculated_ingredient_types'].items() if v == 'additive'}


def exact_or_substring(label: str) -> str:
    """
    這一項是**整個名稱**對上官方清單，還是只有一段對上。

    分開看的理由：官方採正面表列，名稱完全相符即為查表結果，不可能錯；片段相符
    才有誤配風險（「活性乳酸菌」命中「乳酸」那一類）。新做法會送更多項目去比對，
    要確認多出來的都是前者，而不是把食材片段撿成添加物。
    """
    norm = normalize_text(label)
    names = im._substance_names_cache or set()
    return 'exact' if norm in names else 'substring'


def official_name_of(result: dict, label: str) -> str:
    """在 chemical 裡找回這一項對到的官方品名，供人工檢視時對照。"""
    for c in result['chemical']:
        name = c.get('name') or ''
        if name == label or name.endswith(f'({label})'):
            return c.get('officialName') or ''
    return ''


def summarize(result: dict) -> dict:
    cov = result['coverage']
    return {
        'n_items': cov['total'],
        'n_additive': len(result['chemical']),
        # n_raw_material 欄位於 2026-08-06 移除：系統範圍限縮為只查添加物，
        # raw_material_db 這個 basis 不再產生，留著只會是一欄恆為 0 的數字，
        # 容易被讀成「這批樣本沒有原料」，而不是「本系統不再判定原料」。
        'n_water': cov['by_basis'].get('water', 0),
        'n_generic': len(cov['generic_terms']),
        'n_unknown': cov['unknown'],
        # coverage_rate 於 2026-08-06 隨上游一併移除。該比率的分母是整張標示的成分數，
        # 而系統只負責其中的添加物，故它量的是「這張標示添加物佔多少」——商品配方的
        # 屬性，不是系統能力（鮮乳恆為 0%，但那是鮮乳沒有添加物，非系統失敗）。
        # 這裡只留筆數；要不要相除由讀數字的人自行決定並載明口徑。
        'covered': cov['covered'],
        'denominator': cov['denominator'],
    }


def main():
    conn, cursor = db_cursor()
    rag = NoVectorRAG()
    rows, review = [], []
    agg = {k: {'n_items': 0, 'n_additive': 0, 'n_water': 0,
               'n_generic': 0, 'n_unknown': 0, 'covered': 0, 'denominator': 0}
           for k in ('old', 'new')}
    n_cases = 0
    source_counts = {}

    for p in sorted(glob.glob(os.path.join(HERE, 'predictions', '*.json'))):
        cid = os.path.basename(p)[:-5]
        pred = json.load(open(p, encoding='utf-8'))
        pred = pred.get('prediction') or pred or {}
        raw = pred.get('ingredients_raw') or ''
        ing_list = pred.get('ingredients_list') or []
        if not ing_list and not raw:
            continue                       # 非食品案例，兩種做法都沒有成分可比
        n_cases += 1

        # 舊做法：不給原文，走模型清單那條路
        old = match_ingredients(ing_list, None, cursor, rag, ingredients_raw=None)
        # 新做法：給原文
        new = match_ingredients(ing_list, None, cursor, rag, ingredients_raw=raw)

        so, sn = summarize(old), summarize(new)
        source_counts[new['parse']['source']] = source_counts.get(new['parse']['source'], 0) + 1
        for k in agg['old']:
            agg['old'][k] += so[k] or 0
            agg['new'][k] += sn[k] or 0

        old_add, new_add = additive_labels(old), additive_labels(new)
        old_norm = {normalize_text(x) for x in old_add}
        new_norm = {normalize_text(x) for x in new_add}
        gained = sorted(x for x in new_add if normalize_text(x) not in old_norm)
        lost = sorted(x for x in old_add if normalize_text(x) not in new_norm)

        rows.append({
            'case_id': cid,
            'source': new['parse']['source'],
            'reason': new['parse']['reason'] or '',
            'items_old': so['n_items'], 'items_new': sn['n_items'],
            'additive_old': so['n_additive'], 'additive_new': sn['n_additive'],
            'unknown_old': so['n_unknown'], 'unknown_new': sn['n_unknown'],
            # coverage_old／coverage_new 兩欄（百分比）於 2026-08-06 移除，改列筆數。
            'covered_old': so['covered'], 'covered_new': sn['covered'],
            'additive_gained': len(gained), 'additive_lost': len(lost),
        })

        # 新增／消失的添加物逐筆列出供人工檢視——「誤配有沒有變多」沒有正解可自動
        # 判定（成分歸屬的正解取自官方清單，不由人工標注，見 score_eval.py 說明），
        # 能做的是把差異全部攤開，並標出比對方式讓人優先看片段相符那些。
        for lb in gained:
            review.append({'case_id': cid, 'change': 'gained', 'label': lb,
                           'official': official_name_of(new, lb),
                           'match': exact_or_substring(lb)})
        for lb in lost:
            review.append({'case_id': cid, 'change': 'lost', 'label': lb,
                           'official': official_name_of(old, lb),
                           'match': exact_or_substring(lb)})

    cursor.close(); conn.close()

    # 原本此處由 covered / denominator 算出彙總 coverage_rate，2026-08-06 移除。
    # covered 與 denominator 兩個筆數仍照常彙總輸出，判讀時直接看筆數即可。

    gained_exact = sum(1 for r in review if r['change'] == 'gained' and r['match'] == 'exact')
    gained_sub = sum(1 for r in review if r['change'] == 'gained' and r['match'] == 'substring')
    lost_n = sum(1 for r in review if r['change'] == 'lost')

    summary = {
        'n_cases': n_cases,
        'ingredient_source': source_counts,
        'old_model_list': agg['old'],
        'new_raw_text': agg['new'],
        'additive_gained': {'total': gained_exact + gained_sub,
                            'exact_match': gained_exact,
                            'substring_match': gained_sub,
                            '_說明': '片段相符者才有誤配風險，需人工檢視 review CSV'},
        'additive_lost': lost_n,
        'config': {'vector_rag': 'disabled', 'source': 'predictions/ 既有辨識結果'},
    }

    res = os.path.join(HERE, 'results')
    os.makedirs(res, exist_ok=True)
    json.dump(summary, open(os.path.join(res, 'ingredient_parse_summary.json'), 'w',
                            encoding='utf-8'), ensure_ascii=False, indent=2)
    with open(os.path.join(res, 'ingredient_parse_per_case.csv'), 'w', newline='',
              encoding='utf-8-sig') as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader(); w.writerows(rows)
    with open(os.path.join(res, 'ingredient_parse_review.csv'), 'w', newline='',
              encoding='utf-8-sig') as f:
        w = csv.DictWriter(f, fieldnames=['case_id', 'change', 'label', 'official', 'match'])
        w.writeheader(); w.writerows(review)

    print('=== 成分來源新舊對照（48 案，向量比對停用）===')
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"\n逐案: {res}/ingredient_parse_per_case.csv"
          f"\n差異人工檢視: {res}/ingredient_parse_review.csv"
          f"\n彙總: {res}/ingredient_parse_summary.json")


if __name__ == '__main__':
    main()
