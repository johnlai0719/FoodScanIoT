#!/usr/bin/env python3
# A3 量測（二）：官方名稱涵蓋測試（2026-08-03，補交腳本 2026-08-04）。
#
# 問的問題只有一個：**官方品名原封不動出現在標示上時，系統有沒有把它認出來是添加物。**
#
# 這是三項量測裡唯一不需要黃金標準、不需要專業判斷的一項，任何人可重跑驗證：
# 探針是官方公告的品名與通用名稱，比對是字面完全相等，沒有任何一步需要人判斷
# 「這個寫法算不算那個物質」。其餘兩項（見 audit_additive_matches.py）都會產生
# 待人工判定的清單，這一項不會——它只會回答有或沒有。
#
# 切分標示原文時**刻意不用正式流程的解析器**：本測試要驗的正是「原文裡有的東西
# 系統認不認得」，若沿用同一支解析器，解析器漏掉的項目在測試裡也會一併消失，
# 測試就永遠是滿分。故此處自備一個只會依分隔符切開的笨切法。
#
# 本測試**只支持「偵測」層面**，不能證明對應到的是正確的那一筆紀錄：官方紀錄中
# 有相當比例經正規化後撞名（同一物質列於多個用途類別，各有編號與限量）。撞名統計
# 一併輸出於 summary 的「_限制」欄，數字自己說話。
#
# 完全離線、不花錢：吃 predictions/ 裡已存的 48 筆辨識結果，配本機資料庫。
# 用法：cd 測試/量化測試 && ../../server/venv/bin/python eval_official_name_coverage.py
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
    match_ingredients, normalize_text, _candidate_names,
)

# 官方通用名稱對照表（食藥署發布，已由 database_scripts/import_official_common_names.py
# 併入 additives.aliases）。此處直接讀原始檔當探針，不從資料庫回讀——探針要來自官方，
# 不能來自被測系統自己的資料狀態。
COMMON_NAMES_JSON = os.path.join(
    SERVER, '..', '添加物資料庫整理', '01_data_sources', 'tfda_official_common_names.json')

# 標示上的分隔符。刻意只列標點，不做任何語意判斷——這是笨切法的全部規則。
_SPLIT_RE = re.compile(r"[、,，;；/｜|。\.\n\r\t　 ()（）\[\]【】{}]+")

# 刻意不設「探針最短長度」之類的過濾。任何過濾都是本測試自己加的判斷，一旦加了，
# 「哪些名稱該算數」就變成可調的，測試也就不再是任何人都能無異議重跑的了。
# （曾試過排除 3 字以下者：偵測結果同樣是零漏偵測，只是出現次數少 13 次。）


class NoVectorRAG:
    """停用語義比對的替身，確保全程離線。"""
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


def load_probes(cursor) -> tuple[dict, dict]:
    """
    組出探針集合：官方品名 ＋ 官方公告之通用名稱。

    回傳 ({探針原文: 來源標籤}, 撞名統計)。品名欄位偶爾一格塞多個名稱
    （官方原樣，如「醋酸鈉； 醋酸鈉（無水）」），依分號拆開後各自成為一個探針。
    """
    probes = {}
    cursor.execute("SELECT name_zh FROM additives")
    rows = cursor.fetchall()
    for r in rows:
        for piece in re.split(r"[;；]", str(r['name_zh'] or '')):
            piece = piece.strip()
            if piece:
                probes.setdefault(piece, 'official_name')

    if os.path.exists(COMMON_NAMES_JSON):
        doc = json.load(open(COMMON_NAMES_JSON, encoding='utf-8'))
        for r in doc.get('records', []):
            official = (r.get('name_official') or '').strip()
            common = (r.get('name_common') or '').strip()
            # 「維生素○←維他命○」為通配寫法，非具體品項（與匯入腳本同一條規則）
            if not official or not common or '○' in official or '○' in common:
                continue
            if common:
                probes.setdefault(common, 'official_common_name')

    # 撞名統計：本測試不能證明「對應到正確的那一筆」，程度由這組數字說明。
    by_norm = {}
    for r in rows:
        n = normalize_text(r['name_zh'] or '')
        if n:
            by_norm.setdefault(n, 0)
            by_norm[n] += 1
    collided_records = sum(c for c in by_norm.values() if c > 1)
    collision_groups = sum(1 for c in by_norm.values() if c > 1)
    stats = {
        '官方紀錄筆數': len(rows),
        '正規化後相異名稱': len(by_norm),
        '撞名紀錄數': collided_records,
        '撞名紀錄佔比': round(collided_records / len(rows), 3) if rows else None,
        '撞名組數': collision_groups,
    }
    return probes, stats


def split_items(raw: str) -> list[str]:
    """把標示原文切成獨立項目。只依分隔符切開，不做任何語意處理（見檔頭）。"""
    return [x.strip() for x in _SPLIT_RE.split(raw or '') if x.strip()]


def detected_as_additive(probe: str, additive_items: list) -> bool:
    """
    系統是否把這個探針認出來是添加物。

    比對系統自己的輸出，故兩側都套 normalize_text 是正確的——這一步問的是
    「系統有沒有標它」，不是「文字是否相同」（後者已在切分那一側用原文比過了）。
    系統的項目可能帶括號註記（「維生素C(抗氧化劑)」），故也接受探針出現在其候選名之中。
    """
    n_probe = normalize_text(probe)
    for ing in additive_items:
        if normalize_text(ing) == n_probe or n_probe in (_candidate_names(ing) or []):
            return True
    return False


def main():
    conn, cursor = db_cursor()
    probes, collision_stats = load_probes(cursor)
    rag = NoVectorRAG()

    n_cases = n_skipped = 0
    n_occurrences = n_detected = 0
    misses, hits_by_probe = [], {}

    for p in sorted(glob.glob(os.path.join(HERE, 'predictions', '*.json'))):
        cid = os.path.basename(p)[:-5]
        pred = json.load(open(p, encoding='utf-8'))
        pred = pred.get('prediction') or pred or {}
        raw = pred.get('ingredients_raw') or ''
        ing_list = pred.get('ingredients_list') or []
        if not raw:
            n_skipped += 1                 # 沒有標示原文可切，無從下探針
            continue
        n_cases += 1

        res = match_ingredients(ing_list, None, cursor, rag, ingredients_raw=raw)
        additive_items = [k for k, v in res['calculated_ingredient_types'].items()
                          if v == 'additive']

        for item in split_items(raw):
            # **字面完全相等**才算「官方名稱以獨立項目出現」。不做正規化、不做
            # 子字串——那會把「標示寫法像官方名稱」也算進來，本測試就不再是
            # 客觀可重跑的了。
            if item not in probes:
                continue
            n_occurrences += 1
            ok = detected_as_additive(item, additive_items)
            n_detected += ok
            rec = hits_by_probe.setdefault(item, {'probe': item, 'source': probes[item],
                                                  'n': 0, 'n_detected': 0})
            rec['n'] += 1
            rec['n_detected'] += ok
            if not ok:
                misses.append({'case_id': cid, 'probe': item, 'source': probes[item],
                               'raw_excerpt': raw[:120].replace('\n', ' ')})

    cursor.close(); conn.close()

    summary = {
        'n_cases': n_cases,
        'n_skipped': n_skipped,
        '探針數': len(probes),
        '探針來源': {'官方品名': sum(1 for v in probes.values() if v == 'official_name'),
                     '官方通用名稱': sum(1 for v in probes.values() if v == 'official_common_name')},
        '官方名稱以獨立項目出現於標示': n_occurrences,
        '其中偵測到': n_detected,
        '其中漏偵測': n_occurrences - n_detected,
        '偵測率': round(n_detected / n_occurrences, 3) if n_occurrences else None,
        '_限制': {
            '僅支持偵測層面': ('本測試證明「官方名稱出現時有沒有被認出是添加物」，'
                               '不能證明「對應到的是正確的那一筆紀錄」。'),
            '撞名統計': collision_stats,
            '未涵蓋者': ('標示以非官方寫法載明的添加物不在本測試範圍——那類漏配'
                         '由 audit_additive_matches.py 的相似度偵測啟發式篩選。'),
        },
        'config': {'vector_rag': 'disabled', 'source': 'predictions/ 既有辨識結果',
                   'probe_filter': 'none'},
    }

    res_dir = os.path.join(HERE, 'results')
    os.makedirs(res_dir, exist_ok=True)
    json.dump(summary, open(os.path.join(res_dir, 'official_name_coverage_summary.json'), 'w',
                            encoding='utf-8'), ensure_ascii=False, indent=2)
    with open(os.path.join(res_dir, 'official_name_coverage_misses.csv'), 'w', newline='',
              encoding='utf-8-sig') as f:
        w = csv.DictWriter(f, fieldnames=['case_id', 'probe', 'source', 'raw_excerpt'])
        w.writeheader(); w.writerows(misses)
    with open(os.path.join(res_dir, 'official_name_coverage_probes.csv'), 'w', newline='',
              encoding='utf-8-sig') as f:
        w = csv.DictWriter(f, fieldnames=['probe', 'source', 'n', 'n_detected'])
        w.writeheader()
        w.writerows(sorted(hits_by_probe.values(), key=lambda r: -r['n']))

    print('=== A3 官方名稱涵蓋測試（不需黃金標準，可自行重跑）===')
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"\n漏偵測清單: {res_dir}/official_name_coverage_misses.csv"
          f"\n命中探針:   {res_dir}/official_name_coverage_probes.csv"
          f"\n彙總:       {res_dir}/official_name_coverage_summary.json")


if __name__ == '__main__':
    main()
