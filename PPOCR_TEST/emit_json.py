#!/usr/bin/env python3
# 把散在各支評分腳本裡的結果，組成 **下游真正要吃的那個 JSON**。
#
# 為什麼要獨立一支：到今天為止，每一層都只被「單獨量過」——
# nutrition_pipeline 量營養格、bench_ingredients 量成分項、sim_match 量添加物。
# 但 `main.py` 的 Gemini 是一次吐出整份 JSON 的，要換掉它，交付物是**整份**。
# 這支不新增任何辨識能力，只做組裝，並且**誠實標記哪些欄位根本沒做**。
#
# 契約來自 ground_truth 的欄位（也就是 main.py:616 那段 prompt 要 Gemini 產的）。
# 沒做的欄位一律 null／[]，不猜、不留空字串冒充有值——下游 COALESCE 會把
# 空字串當有效值寫進資料庫。
#
# 注意：**這支不讀 ground_truth**。解析器簽章雖然有 gt 參數，一律傳 None，
# 用到就會當場炸掉，不會靜默地污染。
#
# 用法：
#   python emit_json.py                 # 全部案例 -> out/json/
#   python emit_json.py --only c18      # 單一案例並印出來
#   python emit_json.py --report        # 組裝後對 GT 算欄位涵蓋率
import argparse
import json
import os
import re
import sys

sys.stdout.reconfigure(encoding='utf-8')
# ⚠ stderr 也要設。`_check_sources()` 的中止訊息走 sys.exit（＝stderr），
#    只設 stdout 的話，最需要看懂的那一段會在 Windows 主控台變成亂碼。
sys.stderr.reconfigure(encoding='utf-8')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import score_ocr as S            # noqa: E402
import nutrition_pipeline as N   # noqa: E402
import nutrition_solver as V     # noqa: E402
import bench_parse as BP         # noqa: E402
import bench_ingredients as BI   # noqa: E402
import allergy as AL             # noqa: E402
import manufacturer as MF        # noqa: E402

# ── 營養解析走「表格欄位」那一路（2026-09-09 起）────────────────────────────
# 讀取器換成會吐 markdown 表格的視覺語言模型之後，下游仍在把表格攤平成文字、
# 用字元距離抓值，結果是整體錯開一列（`c116` 的 protein 填到脂肪的值）。
# 131/177 案的表格本來就是乾淨三欄，482 格缺口有 330 格出在那些案子上。
#
#   營養格 2121 → 2347（+226），配對 bootstrap +8.7 點、CI +6.0 ～ +11.6、不跨 0
#   新對 243 格、弄壞 17 格；新增的值無一憑空捏造（4 格查無此數者皆為 derived）
#
# 完整設計與限制見 [[07-營養表結構化解析實驗]]。
# ⚠ 這一行是**線上組態**。`bench_parse.p_fill` 仍硬寫死關閉，
#    它在報告裡的身分是「加表格之前的狀態」，不可跟著這裡變動。
BP.USE_TABLE = True
import product_name as PN        # noqa: E402

# 本地 2B 的欄位整理結果。**讀預存檔而不是當場推論**——同 `_bertsplit`
# 的理由：那要另外起一個 llama-server，而這一步是文字到文字、與影像無關，
# 預存不會失去任何東西，也讓組裝不依賴任何服務。產生方式見 `local_fields.py`。
LOCALFIELDS = os.environ.get('EMIT_LOCALFIELDS') or 'localfields'

HERE = os.path.dirname(os.path.abspath(__file__))
# 成分抽取的文字來源。
#
# ⚠ **2026-09-09 更正：預設原為 `v6_best`（PP-OCR 整頁），
#    導致交付用的 out/json 一直以 PP-OCR 的文字抽成分，
#    而不是決策單 #14 定案採用的 vlcrop 讀取器。**
#
#   EMIT_BOXES=v6_best         添加物 F1 57.9｜成分項 F1 53.8   ← 舊預設
#   EMIT_BOXES=vlcrop_hy_v4    添加物 F1 71.2｜成分項 70.8      ← 定案的做法
#
# 也就是說 +13.3 分的增益量到了、寫進報告了，**但沒有進到成品**。
# 這與同日發現的「表格欄位解析沒接上線」是同一類問題：
# **增益停在測試台，交付物沒有跟著動。**
#
# 2026-09-06 測試集擴到 177 案時只更新了目錄名（舊輸出在 v6_hires__boxth0.4），
# 沒有一併把來源換成裁切讀取器——`--report` 是即時計算的，看報告看不出來。
BOXES = os.environ.get('EMIT_BOXES') or 'vlcrop_hy_v4'
# 輸出目錄。⚠ 寫死會讓「換讀取器再組裝一次」直接蓋掉上一組結果——
# 2026-09-07 就這樣把 PP-OCR 的組裝結果覆蓋成 vlcrop 的，階層評分的輸入
# 跟著換掉而不自知。用 EMIT_OUT 分開存。
OUT = os.path.join(HERE, 'out', os.environ.get('EMIT_OUT') or 'json')

# 這些欄位目前**沒有任何一層在做**。列在這裡是為了讓報表數得出來，
# 而不是散在程式各處用 None 帶過去。
# 2026-08-31：`allergy_warning` 已移出這份清單（見 `allergy.py`）。
# 2026-09-07：`manufacturer` 已移出（見 `manufacturer.py`）。
# 2026-09-07：`name` 也已移出（見 `product_name.py`）。
# 留在清單裡的兩欄是**刻意不做**：`brand` 已決定不納入驗收（76% 的值是
# `name` 的子字串）；`certification_marks` 只有 34 案有值、其中 30 案
# 就是 `["TQF"]`，而 TQF 是圖示不是文字，OCR 讀不到——那是影像分類任務。
UNFILLED = ['brand', 'certification_marks']

# 過敏原警語同時讀兩個讀取器。**理由是兩者漏的案例不同**：
# vlcrop_hy 的裁切會整塊漏掉警語區（c18／c20 讀到的相似度只有 0.09／0.29），
# 而 PP-OCR 在同幾案讀得到。實測 ≥0.8 的案數 40（單 vlcrop_hy）／34（單 PP-OCR）
# ／**42（兩者合併）**，過敏原漏判 61／62／**34**。合併只是多讀一個已存在的檔案。
# ⚠ 2026-09-07：這兩個預設是 **58／61 案的舊目錄**。177 案版本叫
# `vlcrop_hy_v4` 與 `v6_best`。`_ocr_lines` 找不到檔就回 None、`_allergy`
# 兩個候選都空就回 None——**不報錯**。實測 177 案裡 allergy_warning 只填了
# 48 案，而那 48 案全部落在舊目錄有檔的範圍內，其餘 119 案是靜默丟掉的。
# 這是第八支踩到「硬寫死預設」的腳本，改成可覆寫並把預設換成 177 案版。
READER_PRESETS = tuple(
    (os.environ.get('EMIT_READERS') or 'vlcrop_hy_v4,v6_best').split(','))
ALLERGY_PRESETS = READER_PRESETS
# manufacturer 用同一組，但兩者的強弱相反：過敏原印在成分表旁邊所以
# vlcrop 勝（64.9% vs 45.0%），廠商印在包裝側邊、在裁切區外所以 PP-OCR 勝。
# 合併對兩者都是淨賺。
MANUFACTURER_PRESETS = READER_PRESETS
# ⚠ 品名的順序**要反過來**：合併規則是「取第一個非空的」，而品名印在
# 包裝正面、不在裁切區內，PP-OCR 該優先（實測完全對 104 vs 97）。
NAME_PRESETS = tuple(reversed(READER_PRESETS))


def _boxes(cid):
    p = os.path.join(HERE, 'out', BOXES, f'{cid}.json')
    return json.load(open(p, encoding='utf-8')) if os.path.exists(p) else None


def _ocr_lines(preset, cid):
    p = os.path.join(HERE, 'out', preset, f'{cid}.json')
    if not os.path.exists(p):
        return None
    d = json.load(open(p, encoding='utf-8'))
    return [ln.get('text') or '' for im in d.get('images', [])
            for ln in im.get('lines', [])]


def _allergy(cid):
    """過敏原警語。抓不到回傳 None——**不回傳空字串**。

    `main.py` 用 COALESCE 寫入，空字串會被當成有效值蓋掉資料庫裡的舊值。

    兩個讀取器各抽一次，**選法不看正解**：過敏原字詞多的優先、同多取較長者。
    偏向多讀是有意的——實測誤報 0/9（正解沒有警語的 9 案，一案都沒亂抓），
    所以多讀的風險已經被控住，而漏讀是直接少列一個過敏原。
    """
    cands = []
    for pre in ALLERGY_PRESETS:
        L = _ocr_lines(pre, cid)
        if L:
            c = AL.extract(L)
            if c:
                cands.append(c)
    if not cands:
        return None
    return max(cands, key=lambda c: (len(AL.allergens(c)), len(c)))


def _manufacturer(cid):
    """廠商全名。抓不到回 None——理由同 `_allergy`（COALESCE 會被空字串蓋掉）。

    優先序在 `manufacturer.py`：製造商 ＞ 委製商 ＞ 負責廠商 ＞ 進口商 ＞
    代理商 ＞ 無標籤者。**跨讀取器比的是優先序不是長度**——有標籤的短名字
    勝過沒標籤的長名字。
    """
    v = _localfield(cid, 'manufacturer')
    if v:
        return v
    det = []
    for pre in MANUFACTURER_PRESETS:
        L = _ocr_lines(pre, cid)
        if L:
            det.append(MF.extract_detail(L))
    return MF.merge(det) if det else None


def _localfield(cid, field):
    """本地 2B 模型整理出來的欄位值（`local_fields.py` 的預存結果）。

    2026-09-07 實測，同一批 177 案、同一把尺（完全對＋近似≥85%）：

        name           規則 118｜**本地 2B 137**｜Gemini 結構化 131
        manufacturer   規則  74｜**本地 2B 125**｜Gemini 結構化 116

    **本地小模型贏過雲端大模型**，而且贏的地方正是 §0 預測的地方——
    「輸出的值在 OCR 文字裡查無此字」的案數：

        name           本地 2B  2 案｜Gemini **47 案**
        manufacturer   本地 2B  4 案｜Gemini **28 案**

    差別不在模型大小，在**輸入**：本地這支只看得到 OCR 文字（文字→文字），
    沒有素材可以編；Gemini 看得到影像也知道這個牌子通常是誰做的，就會補。
    這跟添加物層 43% 憑空補完是同一個機制。
    """
    p = os.path.join(HERE, 'out', LOCALFIELDS, f'{cid}.json')
    if not os.path.exists(p):
        return None
    d = json.load(open(p, encoding='utf-8')) or {}
    v = (d.get('prediction') or {}).get(field)
    return v.strip() if isinstance(v, str) and v.strip() else None


def _name(cid):
    """品名。抓不到回 None——理由同 `_allergy`。

    合併是「取第一個非空的」而不是取較長者，與 `_manufacturer` 相反：
    品名的錯誤型態是**把下一個欄位吃進來**（取長會更糟），廠商的錯誤型態是
    **被 OCR 截斷**（取長才對）。見兩支各自的 `merge()` 註解。
    """
    cands = [_localfield(cid, 'name')]
    for pre in NAME_PRESETS:
        L = _ocr_lines(pre, cid)
        cands.append(PN.extract(L) if L else None)
    return PN.merge(cands)


def _bertsplit(cid):
    """讀 out/bertsplit/ 的切分模型結果（train_split.py predict 產生的）。

    **刻意讀預存結果而不是當場推論**：模型要 torch，而本檔 import 的
    nutrition_pipeline 走 paddle，兩者在同一個 Python 環境會衝突
    （paddlex 無條件 import modelscope → torch，cuDNN 版本相衝）。
    切分是純文字到純文字、與影像無關，預存不會失去任何東西。
    """
    p = os.path.join(HERE, 'out', 'bertsplit', f'{cid}.json')
    if not os.path.exists(p):
        return None
    d = json.load(open(p, encoding='utf-8'))
    out = [l['text'].strip() for im in d.get('images', [])
           for l in im.get('lines', []) if (l.get('text') or '').strip()]
    return out or None


def _rec(cid):
    p = os.path.join(N.OUT, f'{cid}.json')
    return json.load(open(p, encoding='utf-8')) if os.path.exists(p) else None


def _servings(text, ss):
    """本包裝含 N 份。兩條路徑：直接讀，或用「淨重 ÷ 每一份量」算。

    第二條是 `bench_parse._serving_size` 第 3 條路徑的反向——同一條法規關係，
    只是已知的那一端換了。直接讀只拿到 10/57，加上淨重回推才補得上來。
    """
    t = N.norm(text or '')
    m = re.search(r'本包裝含\D{0,6}(\d+(?:\.\d+)?)\s*份', t)
    if m:
        return float(m.group(1))
    mw = re.search(r'(?:淨重|净重|內容量|内容量)\D{0,3}(\d+(?:[.,]\d+)?)', t)
    if mw and ss:
        try:
            n = float(mw.group(1).replace(',', '.')) / ss
        except (ValueError, ZeroDivisionError):
            return None
        # 份數幾乎都是整數或 .5；離得遠代表淨重或份量其中一個讀錯了，寧可不給
        r = round(n * 2) / 2
        if 1 <= r <= 60 and abs(n - r) <= 0.08:
            return r
    return None


def build(cid, ing_parser='boxsep'):
    """組一份下游契約的 JSON。缺的來源就讓對應欄位留 null，不擋整份輸出。

    ing_parser：`boxsep` 是純規則（既有的 production 路徑）；
    `bertsplit` 改用 0.1B 切分模型的預存結果，模型缺該案時退回 boxsep。
    兩者都要量——實測切分模型贏在添加物層、輸在清單層，只看一層會誤判。
    """
    rec, d = _rec(cid), _boxes(cid)
    meta = {'ocr_preset': BOXES, 'allergy_presets': list(ALLERGY_PRESETS),
            'manufacturer_presets': list(MANUFACTURER_PRESETS),
            'name_presets': list(NAME_PRESETS),
            'localfields': LOCALFIELDS,
            'nutrition_parser': 'fill',
            'ingredient_parser': ing_parser, 'unfilled': list(UNFILLED),
            'sources': {'nutrition': rec is not None, 'ingredients': d is not None}}

    items, raw = [], ''
    if d is not None:
        items = None
        if ing_parser == 'bertsplit':
            items = _bertsplit(cid)
            if items is None:
                meta['ingredient_parser'] = 'boxsep（bertsplit 缺此案，已退回）'
        if items is None:
            items = BI.PARSERS['boxsep'](cid, d, None) or []
        # 逐字原文我們沒有——解析器回的是切好的項目。重組出來的字串標記清楚，
        # 不要讓下游以為這是包裝上照抄的那一段。
        raw = '、'.join(items)
        meta['ingredients_raw_is_reconstructed'] = True

    per_serving = {k: None for k in V.NUTRITION_FIELDS}
    per_100g = dict(per_serving)
    ss = sps = None
    if rec is not None:
        # 先取約束仲裁後的值，再自己補欄——要的是 derived 的清單，
        # 直接叫 p_fill 會拿到補完的結果，差集為空（第一版踩過）。
        solved = BP.PARSERS['solve'](rec, cid)
        _, ss = BP._candidates(rec, cid)
        text = N.full_text(rec)
        sps = _servings(text, ss)
        vals, derived = BP._fill(solved, ss, BP._is_dv(rec))
        for k, pair in vals.items():
            a, b = (tuple(pair) + (None,))[:2]
            if k in per_serving:
                per_serving[k], per_100g[k] = a, b
        # 膳食纖維：**不是法定必標欄位**，多數標示上根本沒有這一列。
        # 序列對齊卻假設八欄俱全，會把碳水的數字塞進 fiber（57 案裡 25 案兩者同值）。
        # bench_parse 的 878 格只算「GT 有值」的格子，這種硬填**不會被扣分**，
        # 所以組裝之前沒人看得到它。實測門檻：文字裡沒出現「膳食纖維」就丟掉，
        # 清掉 98 格假值、誤丟 4 格真值。下游是拿去比對／示警的，假值比缺值貴。
        if not re.search(r'膳食', N.norm(text)):
            per_serving['fiber'] = per_100g['fiber'] = None
            meta['fiber_dropped'] = True
        meta['derived_cells'] = sorted('%s[%s]' % (k, ['per_serving',
                                                      'per_100g'][i])
                                       for k, i in derived)

    got_any = bool(items) or any(v is not None for v in per_serving.values())
    out = {
        'is_food_label': got_any,
        'reject_reason': '' if got_any else '未讀到成分或營養標示',
        'name': _name(cid),
        'brand': None,
        'ingredients_raw': raw,
        'ingredients_list': items,
        'nutrition': per_100g,
        'serving_size': ss,
        'servings_per_container': sps,
        'nutrition_per_serving': per_serving,
        'manufacturer': _manufacturer(cid),
        'allergy_warning': _allergy(cid),
        'certification_marks': [],
        '_meta': meta,
    }
    return out


def _cases():
    p = json.load(open(os.path.join(S.EVAL_ROOT, 'cases.json'),
                       encoding='utf-8'))['cases']
    return [(c['case_id'], c.get('category') or '') for c in p]


def report(ing_parser='boxsep'):
    """欄位層級的涵蓋率。**這裡只問「有沒有值」，不問對不對**——
    對不對是 bench_parse（營養 878 格）與 sim_match（添加物層）在量的，
    兩者的判準比「非 null」嚴格得多，數字不要混用。"""
    rows, nfood = [], 0
    lab_ok = lab_n = 0
    for cid, cat in _cases():
        gt = S.load_gt(cid, cat)
        if not gt:
            continue
        j = build(cid, ing_parser)
        if gt.get('is_food_label', True):
            nfood += 1
            rows.append((cid, gt, j))
        lab_n += 1
        lab_ok += (j['is_food_label'] == gt.get('is_food_label', True))

    def filled(v):
        return v not in (None, '', [], {}) and not (
            isinstance(v, dict) and all(x is None for x in v.values()))

    print('組裝 %d 案（%d 案是食品標示）\n' % (lab_n, nfood))
    print('欄位            我方有值   GT 有值   說明')
    fields = ['name', 'brand', 'ingredients_raw', 'ingredients_list',
              'nutrition', 'serving_size', 'servings_per_container',
              'nutrition_per_serving', 'manufacturer', 'allergy_warning',
              'certification_marks']
    for f in fields:
        mine = sum(1 for _, _, j in rows if filled(j.get(f)))
        theirs = sum(1 for _, g, _ in rows if filled(g.get(f)))
        note = '未實作' if f in UNFILLED else ''
        if f == 'ingredients_raw':
            note = '重組，非逐字'
        print('%-22s %4d %8d    %s' % (f, mine, theirs, note))
    def num(v):
        """GT 這兩欄有時是「206公克」這種字串，比對前先抽出數字。"""
        if isinstance(v, (int, float)):
            return float(v)
        m = re.search(r'-?\d+(?:\.\d+)?', str(v or ''))
        return float(m.group()) if m else None

    def num_ok(a, b):
        a, b = num(a), num(b)
        return a is not None and b is not None and abs(a - b) < 0.5

    # 營養格：同時看「該有的有沒有」與「不該有的有沒有亂填」。
    # 後者 bench_parse 量不到（score_one 對 GT 為 null 的格子直接 continue），
    # 組裝之後才看得見。
    ok = ch = spur = 0
    for _, g, j in rows:
        for scope in ('nutrition_per_serving', 'nutrition'):
            want, got = (g.get(scope) or {}), j[scope]
            for k in V.NUTRITION_FIELDS:
                w, v = want.get(k), got.get(k)
                if w is None:
                    spur += v is not None
                else:
                    ch += 1
                    ok += v is not None and N.close(v, w)
    print('\n營養格 正確 %d/%d（%.0f%%）｜GT 沒有卻硬填 %d 格'
          % (ok, ch, 100.0 * ok / max(ch, 1), spur))

    print()
    for f in ('serving_size', 'servings_per_container'):
        ok = sum(1 for _, g, j in rows if num_ok(j.get(f), g.get(f)))
        have = sum(1 for _, g, j in rows if filled(j.get(f)))
        print('%-22s 正確 %d/%d（有給值 %d，沒把握的留 null）'
              % (f, ok, len(rows), have))
    dv = sum(len(j['_meta'].get('derived_cells') or []) for _, _, j in rows)
    print('營養欄有 %d 格是用份量推導的，不是讀到的（見 _meta.derived_cells）' % dv)
    print('\nis_food_label 判對 %d/%d（規則：讀到成分或營養就算是）'
          % (lab_ok, lab_n))
    print('\n注意：欄位表是「有沒有填」，不是「填得對不對」。'
          '成分項的正確率看 bench_ingredients.py，'
          '添加物層（下游真正的驗收層）看 sim_match_preset.py。')


def _check_sources():
    """產出之前確認每個來源目錄都**蓋得到全部案例**。

    ⚠ **2026-09-10：這支自己踩了它記錄過八次的坑。**
       `nutrition_pipeline.OUT` 預設是 `out/nutrition`，而那是 58 案時代的目錄；
       177 案的營養結果在 `out/nutrition_vlcrop`，要靠 `NUTRI_OUT` 指過去。
       沒設就**安靜地**讓 119 案的營養欄變成全 null——交付檔的營養格
       從 2382 掉到 717，而 `emit_json.py` 照樣印「寫出 177 份」。

    同型事故清單（都是「環境變數沒設就靜默走預設」）：
       `EVAL_IMAGE_ROOT` → PP-OCR 讀 0 行仍回報成功
       `EMIT_BOXES`      → 交付檔的成分來源一直是 PP-OCR，+13.3 分沒進成品
       `EMIT_READERS`    → 過敏原 119 案被靜默丟掉
       `NUTRI_OUT`       → 本次

    所以這裡**寧可停下來**：覆蓋率不足就中止，要硬跑得明講
    （`EMIT_ALLOW_PARTIAL=1`）。少產一次的成本，遠低於拿一份缺半邊的
    交付檔去跑評估、再把結論寫進報告。
    """
    ids = [cid for cid, _ in _cases()]
    srcs = [('營養（nutrition_pipeline.OUT）', N.OUT, 'NUTRI_OUT'),
            ('成分／版面（BOXES）', os.path.join(HERE, 'out', BOXES), 'EMIT_BOXES'),
            ('本地 2B 欄位（LOCALFIELDS）',
             os.path.join(HERE, 'out', LOCALFIELDS), 'EMIT_LOCALFIELDS')]
    bad = []
    for lab, d, env in srcs:
        have = sum(1 for c in ids if os.path.exists(os.path.join(d, c + '.json')))
        mark = '' if have >= len(ids) else '  ← 只蓋到 %d/%d' % (have, len(ids))
        print('   %-30s %s%s' % (lab, os.path.basename(d), mark))
        if have < len(ids):
            bad.append((lab, d, env, have))
    if not bad:
        return
    print()
    for lab, d, env, have in bad:
        print('✗ %s 只有 %d/%d 案：%s' % (lab, have, len(ids), d))
        alt = sorted(x for x in os.listdir(os.path.join(HERE, 'out'))
                     if x.startswith(os.path.basename(d)) and x != os.path.basename(d)
                     and os.path.isdir(os.path.join(HERE, 'out', x)))
        for x in alt:
            n = sum(1 for c in ids
                    if os.path.exists(os.path.join(HERE, 'out', x, c + '.json')))
            if n >= len(ids):
                print('   → 用 %s=%s（%d/%d 案）' % (env, x, n, len(ids)))
    if os.environ.get('EMIT_ALLOW_PARTIAL') == '1':
        print('\nEMIT_ALLOW_PARTIAL=1，仍繼續產出（缺的欄位會是 null）。')
        return
    sys.exit('\n已中止。缺的案例會安靜地變成 null，而輸出仍會印「寫出 177 份」。\n'
             '確定要產一份不完整的，請設 EMIT_ALLOW_PARTIAL=1。')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--only', default=None)
    ap.add_argument('--report', action='store_true')
    ap.add_argument('--ingredients', default='boxsep',
                    choices=('boxsep', 'bertsplit'),
                    help='成分抽取來源。bertsplit 讀 out/bertsplit/ 的模型結果')
    ap.add_argument('--out', default=None, help='輸出目錄，預設 out/json')
    a = ap.parse_args()
    if a.report:
        return report(a.ingredients)
    if a.only:
        cid = next((c for c, _ in _cases() if c.startswith(a.only)), a.only)
        print(json.dumps(build(cid, a.ingredients), ensure_ascii=False, indent=1))
        return
    _check_sources()
    outdir = os.path.join(HERE, 'out', a.out) if a.out else OUT
    os.makedirs(outdir, exist_ok=True)
    n = 0
    for cid, _ in _cases():
        j = build(cid, a.ingredients)
        with open(os.path.join(outdir, cid + '.json'), 'w', encoding='utf-8') as f:
            json.dump(j, f, ensure_ascii=False, indent=1)
        n += 1
    print('寫出 %d 份 -> %s（成分來源：%s）' % (n, outdir, a.ingredients))
    print('下一步：python emit_json.py --report')


if __name__ == '__main__':
    main()
