#!/usr/bin/env python3
# 照片 × 正解 × 預測 的三方並排檢視頁（唯讀）。
#
# 為何要有這支，而 review_gt.py 不夠：review_gt.py 回答「正解和照片對不對得上」，
# 這支回答**「模型錯在哪裡」**。summary.json 只給得出「name 對 33/56」，
# 看不出那 23 案是讀錯字、抓錯欄位、還是照片上本來就沒有。分數告訴你有多少錯，
# 這頁告訴你錯成什麼樣子——而後者才決定下一步要修哪一層。
#
# **同時吃多份預測**：`--pred=a,b,c` 會並排出 GT 與各批預測。這是本專案反覆
# 需要的比較（現行路徑 vs 結構化輸出 vs 舊基準），而逐一開 summary.json 對照
# 極慢，且分數層級的差異看不到成因。
#
# **唯讀，不寫入任何檔案**（除了輸出的 html）。差異判定只為了讓眼睛快速找到
# 該看的地方，用的是簡化的比對——**不是評分**，數字一律以 score_eval.py 為準。
# 兩者刻意不共用：這裡放寬會讓人看漏，收緊會讓人被雜訊淹沒，取捨與評分不同。
#
# 圖片用相對路徑引用原圖（不內嵌），所以這個 html 必須留在本目錄下開啟。
# 58 案的原圖共 143MB，內嵌成 data URI 會產生打不開的檔案。
#
# 用法：
#   python review_pred.py                                   # 預設比 predictions/
#   python review_pred.py --pred=pred_allergy_0823
#   python review_pred.py --pred=predictions,pred_struct    # 多份並排
#   python review_pred.py --pred=vlcrop,predictions_v4_struct   # 本地管線 對 Gemini
#     別名 vlcrop／local = ../../PPOCR_TEST/out/json
#     （**完整本地管線**組出來的整份 JSON，不是 PP-OCR 單獨的結果）
#     也可直接給任意資料夾路徑
#   python review_pred.py --only-diff                       # 只列有欄位不一致的案例
#   python review_pred.py --field=allergy_warning           # 只看某一欄的差異
#   python review_pred.py --category=instant_noodle
#   python review_pred.py c57_來一客(京燉肉骨風味)
import html
import json
import os
import sys

import casetool
import score_eval as SE

# Windows 主控台預設 cp950，案例名稱裡有它編不出來的字（「塩」「菓」…）。
# 沒有這兩行的後果不是印出亂碼，而是**整批任務在中途拋 UnicodeEncodeError 死掉**
# ——2026-09-07 run_eval 炸在第 10 案的「塩」、intake 炸在第 11 案的「菓」，
# 後者還留下半套用狀態（照片已搬、cases.json 沒存）。
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.stderr.reconfigure(encoding='utf-8', errors='replace')

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, '_review_pred.html')

NUTRI_ZH = [('calories', '熱量', '大卡'), ('protein', '蛋白質', 'g'),
            ('fat', '脂肪', 'g'), ('saturated_fat', '飽和脂肪', 'g'),
            ('trans_fat', '反式脂肪', 'g'), ('carbohydrates', '碳水化合物', 'g'),
            ('sugar', '糖', 'g'), ('fiber', '膳食纖維', 'g'),
            ('sodium', '鈉', 'mg')]

TEXT_FIELDS = [('name', '品名'), ('brand', '品牌'), ('manufacturer', '製造商'),
               ('serving_size', '每一份量'), ('servings_per_container', '每包裝含份數'),
               ('allergy_warning', '過敏原')]

# 這兩欄雖然放在文字欄，值卻是數字：模型常回 375.0 而正解記 375，
# 逐字比會標成不符——實測 c02 兩欄都因此誤標。故先試數值比對。
NUMERIC_FIELDS = {'serving_size', 'servings_per_container'}

# 來源別名。PP-OCR 那條線的產物不在本目錄下，而且沒有 `prediction` 外層
# （`emit_json.py` 直接吐欄位），但欄位名與 run_eval.py 的預測完全一致，
# 所以只要能定位到資料夾就能並排。
# ⚠ **`ppocr` 這個別名取錯了名字。** 它指的是 `PPOCR_TEST/out/json`——
# 那是**完整的本地管線**（PP-OCR 定位 → 裁切 → HunyuanOCR 辨識 → Qwen 整理欄位）
# 組出來的整份 JSON，不是 PP-OCR 單獨的結果。
# 兩者差很多：PP-OCR 單獨的添加物層是 57.9，完整管線是 74.7。
# 2026-09-10 新增 `vlcrop`／`local` 兩個正名別名，`ppocr` 保留相容但不建議再用。
# （本專案已有「六個讀取器三個都叫 VL」的命名混淆前例，見 memory 的
#  `reader-naming-roster`——引用數字前先確認指的是哪一個。）
_LOCAL_JSON = os.path.normpath(os.path.join(HERE, '..', '..',
                                            'PPOCR_TEST', 'out', 'json'))
ALIASES = {
    'vlcrop': _LOCAL_JSON,     # 建議用這個
    'local': _LOCAL_JSON,      # 同上
    'ppocr': _LOCAL_JSON,      # ⚠ 舊名，會誤導；保留相容
}


def esc(v):
    return html.escape(str(v)) if v not in (None, '') else ''


def same_text(a, b, key=None):
    if key in NUMERIC_FIELDS:
        try:
            if a is not None and b is not None:
                return abs(float(a) - float(b)) < 1e-9
        except (TypeError, ValueError):
            pass
    return SE.norm(a or '') == SE.norm(b or '')


def same_num(a, b):
    """數值比對沿用 score_eval 的容差（絕對 0.5 或相對 5%），免得同一格
    在頁面上標紅、在分數裡卻算對，兩邊講不同的話。"""
    if a is None or b is None:
        return a is None and b is None
    try:
        x, y = float(a), float(b)
    except (TypeError, ValueError):
        return str(a) == str(b)
    return abs(x - y) <= SE.NUM_ABS_TOL or abs(x - y) <= SE.NUM_REL_TOL * max(abs(x), abs(y), 1e-9)


# ─── 欄位區塊 ────────────────────────────────────────────────────────────────
def text_rows(gt, preds, names, only_field=None):
    out = ''
    for k, zh in TEXT_FIELDS:
        if only_field and k != only_field:
            continue
        g = gt.get(k)
        cells = ''
        for p in preds:
            v = p.get(k)
            ok = same_text(g, v, k)
            cls = 'ok' if ok else ('miss' if v in (None, '') else 'bad')
            txt = esc(v) or '<span class="empty">—</span>'
            cells += f'<td class="{cls}">{txt}</td>'
        gtxt = esc(g) or '<span class="empty">正解無此欄</span>'
        out += f'<tr><th>{zh}</th><td class="gt">{gtxt}</td>{cells}</tr>'
    return (f'<table class="cmp"><tr><th></th><th class="gt">正解</th>'
            + ''.join(f'<th>{esc(n)}</th>' for n in names) + '</tr>'
            + out + '</table>') if out else ''


def nutri_rows(gt, preds, names):
    """營養兩欄各 9 格。每份/每100 分開比——把兩欄混在一起看不出是抄錯欄還是讀錯數。"""
    blocks = ''
    for key, label in (('nutrition', '每100g/mL'), ('nutrition_per_serving', '每份')):
        g = gt.get(key) or {}
        ps = [(p.get(key) or {}) for p in preds]
        if not g and not any(ps):
            continue
        rows = ''
        for k, zh, unit in NUTRI_ZH:
            gv = g.get(k)
            cells = ''
            for pv in (d.get(k) for d in ps):
                ok = same_num(gv, pv)
                if gv is None and pv is not None:
                    cls = 'hard'          # GT 為 null 卻給了值 = 硬填
                elif pv is None and gv is not None:
                    cls = 'miss'
                else:
                    cls = 'ok' if ok else 'bad'
                cells += f'<td class="{cls}">{"—" if pv is None else esc(pv)}</td>'
            rows += (f'<tr><th>{zh}<i>{unit}</i></th>'
                     f'<td class="gt">{"—" if gv is None else esc(gv)}</td>{cells}</tr>')
        blocks += (f'<h4>{label}</h4><table class="cmp nutri"><tr><th></th>'
                   f'<th class="gt">正解</th>'
                   + ''.join(f'<th>{esc(n)}</th>' for n in names) + '</tr>'
                   + rows + '</table>')
    return blocks


def ing_block(gt, preds, names):
    """成分清單用集合對照：正解有而預測沒有＝漏，預測有而正解沒有＝多。

    用 chip 而非表格，是因為項數兩邊不一致（模型常把一項切成兩項），
    逐列對齊會製造假的錯位。看的是「哪些在、哪些不在」。
    """
    g = gt.get('ingredients_list') or []
    gn = {SE.norm(x) for x in g if SE.norm(x)}
    out = (f'<h4>成分清單<i>正解 {len(g)} 項</i></h4>'
           f'<div class="chips gtchips">'
           + ''.join(f'<span class="chip">{esc(x)}</span>' for x in g) + '</div>')
    for p, n in zip(preds, names):
        lst = p.get('ingredients_list') or []
        pn = {SE.norm(x) for x in lst if SE.norm(x)}
        hit = len(gn & pn)
        chips = ''.join(
            f'<span class="chip {"ok" if SE.norm(x) in gn else "bad"}">{esc(x)}</span>'
            for x in lst) or '<span class="empty">—</span>'
        missing = [x for x in g if SE.norm(x) not in pn]
        miss = (('<div class="missrow"><span class="k">漏</span>'
                 + ''.join(f'<span class="chip miss">{esc(x)}</span>' for x in missing)
                 + '</div>') if missing else '')
        out += (f'<h4>{esc(n)}<i>{len(lst)} 項／命中 {hit}／漏 {len(missing)}</i></h4>'
                f'<div class="chips">{chips}</div>{miss}')
    return out


def case_diff_count(gt, preds):
    """這個案例有幾個欄位對不上（任一份預測不一致就算）。--only-diff 用。"""
    n = 0
    for k, _ in TEXT_FIELDS:
        if any(not same_text(gt.get(k), p.get(k), k) for p in preds):
            n += 1
    for key in ('nutrition', 'nutrition_per_serving'):
        g = gt.get(key) or {}
        for k, _, _ in NUTRI_ZH:
            if any(not same_num(g.get(k), (p.get(key) or {}).get(k)) for p in preds):
                n += 1
    gn = {SE.norm(x) for x in (gt.get('ingredients_list') or []) if SE.norm(x)}
    for p in preds:
        pn = {SE.norm(x) for x in (p.get('ingredients_list') or []) if SE.norm(x)}
        if gn != pn:
            n += 1
            break
    return n


def card(c, gt, preds, names, only_field=None):
    cid, cat = c['case_id'], c['category']
    imgs = ''.join(
        f'<a href="{html.escape(p)}" target="_blank">'
        f'<img src="{html.escape(p)}" loading="lazy"></a>' for p in c['images'])
    nd = case_diff_count(gt, preds)
    diff = ''.join(f'<span class="tag d">{d}</span>' for d in (c.get('difficulty') or []))
    missing = [n for p, n in zip(preds, names) if p is None or p == {}]

    if only_field:
        body = text_rows(gt, preds, names, only_field)
    elif cat == 'non_food':
        body = '<p class="auto">非食品案例，只核對 is_food_label。</p>'
        for p, n in zip(preds, names):
            v = p.get('is_food_label')
            cls = 'ok' if v is False else 'bad'
            body += f'<div class="fld"><span class="k">{esc(n)}</span><span class="{cls}">{esc(v)}</span></div>'
    else:
        body = (text_rows(gt, preds, names)
                + nutri_rows(gt, preds, names)
                + ing_block(gt, preds, names))

    warn = (f'<p class="warn">缺預測：{"、".join(missing)}</p>' if missing else '')
    return (f'<section class="case" id="{html.escape(cid)}">'
            f'<h3>{esc(cid)}<span class="tag">{esc(cat)}</span>'
            f'<span class="tag v">{esc(c.get("set_version"))}</span>'
            f'<span class="tag n{"bad" if nd else "ok"}">{nd} 欄不符</span>{diff}</h3>'
            f'{warn}'
            f'<div class="cols"><div class="imgs">{imgs}</div>'
            f'<div class="gt">{body}</div></div></section>')


CSS = """
  body { font-family: system-ui, "Microsoft JhengHei", sans-serif; margin: 0 auto;
         padding: 0 16px 60px; max-width: 1700px; color: #1a1a1a; background: #fff; }
  #nav { position: sticky; top: 0; background: #f6f6f6; border-bottom: 2px solid #bbb;
         padding: 8px; z-index: 5; font-size: .8em; line-height: 1.9; }
  #nav a { margin-right: 10px; color: #06c; text-decoration: none; white-space: nowrap; }
  #nav a.hasdiff { color: #b00; font-weight: 600; }
  .case { border-top: 3px solid #ddd; padding-top: 10px; margin-top: 28px; }
  .case h3 { margin: 0 0 8px; font-size: 1.05em; }
  .tag { font-size: .72em; font-weight: 400; background: #eee; border-radius: 3px;
         padding: 1px 6px; margin-left: 6px; color: #555; }
  .tag.nbad { background: #fdd; color: #900; } .tag.nok { background: #dfd; color: #060; }
  .tag.d { background: #fff3cd; color: #856404; }
  .cols { display: grid; grid-template-columns: 380px 1fr; gap: 18px; align-items: start; }
  .imgs { position: sticky; top: 60px; align-self: start; max-height: 88vh;
          overflow-y: auto; }
  .imgs img { width: 100%; display: block; margin-bottom: 8px; border: 1px solid #ddd; }
  table.cmp { border-collapse: collapse; width: 100%; margin: 6px 0 14px;
              font-size: .86em; table-layout: fixed; }
  table.cmp th, table.cmp td { border: 1px solid #ddd; padding: 4px 7px;
              vertical-align: top; word-break: break-all; }
  table.cmp > tbody > tr > th:first-child { width: 96px; background: #fafafa;
              text-align: left; font-weight: 600; }
  table.cmp th.gt, table.cmp td.gt { background: #f4f8ff; }
  table.cmp i { font-style: normal; color: #999; font-size: .82em; margin-left: 4px; }
  td.ok { background: #f2fbf2; } td.bad { background: #fdecec; color: #900; }
  td.miss { background: #fff8e6; color: #8a6d00; }
  td.hard { background: #f3e8ff; color: #6b21a8; }
  .nutri td, .nutri th { text-align: right; } .nutri th:first-child { text-align: left; }
  h4 { margin: 14px 0 4px; font-size: .9em; }
  h4 i { font-style: normal; color: #999; font-weight: 400; margin-left: 6px; }
  .chips { display: flex; flex-wrap: wrap; gap: 4px; }
  .chip { font-size: .8em; background: #eef; border: 1px solid #ccd; border-radius: 3px;
          padding: 1px 6px; }
  .chip.ok { background: #f2fbf2; border-color: #b5e0b5; }
  .chip.bad { background: #fdecec; border-color: #e9b5b5; color: #900; }
  .chip.miss { background: #fff8e6; border-color: #e6d29a; color: #8a6d00; }
  .gtchips .chip { background: #f4f8ff; border-color: #cdd9ee; }
  .missrow { margin-top: 4px; display: flex; flex-wrap: wrap; gap: 4px; align-items: center; }
  .missrow .k { font-size: .78em; color: #8a6d00; margin-right: 4px; }
  .empty { color: #bbb; } .warn { color: #b00; font-size: .85em; }
  .auto { color: #666; font-size: .9em; }
  .legend { font-size: .78em; color: #555; padding: 6px 0; }
  .legend span { border-radius: 3px; padding: 1px 6px; margin-right: 8px; }
"""


def main():
    args = sys.argv[1:]
    preds_arg = 'predictions'
    want_cat = want_id = only_field = None
    only_diff = False
    for a in args:
        if a.startswith('--pred='):
            preds_arg = a.split('=', 1)[1]
        elif a.startswith('--category='):
            want_cat = a.split('=', 1)[1]
        elif a.startswith('--field='):
            only_field = a.split('=', 1)[1]
        elif a == '--only-diff':
            only_diff = True
        elif not a.startswith('--'):
            want_id = a
        else:
            sys.exit(f"看不懂的參數：{a}")

    names = [x.strip() for x in preds_arg.split(',') if x.strip()]
    dirs_ = []
    for n in names:
        for cand in (ALIASES.get(n), os.path.join(HERE, n), n):
            if cand and os.path.isdir(cand):
                dirs_.append(cand)
                break
        else:
            sys.exit(f"找不到預測資料夾：{n}（可用別名：{', '.join(ALIASES)}）")

    cases = json.load(open(casetool.CASES, encoding='utf-8'))['cases']
    gt_paths = {}
    for root, dirs, files in os.walk(casetool.GT):
        if os.path.basename(root).startswith('_'):
            continue
        for f in files:
            if f.endswith('.json'):
                gt_paths[f[:-5]] = os.path.join(root, f)

    cards, navs, n_shown = [], [], 0
    for c in cases:
        cid = c['case_id']
        if want_id and cid != want_id:
            continue
        if want_cat and c['category'] != want_cat:
            continue
        if cid not in gt_paths:
            continue
        gt = json.load(open(gt_paths[cid], encoding='utf-8'))
        preds = []
        for dpath in dirs_:
            p = os.path.join(dpath, f'{cid}.json')
            if os.path.exists(p):
                d = json.load(open(p, encoding='utf-8'))
                # run_eval.py 包在 prediction 底下；emit_json.py 直接吐欄位。
                preds.append(d.get('prediction') or d)
            else:
                preds.append({})
        nd = case_diff_count(gt, preds)
        if only_diff and nd == 0:
            continue
        n_shown += 1
        cards.append(card(c, gt, preds, names, only_field))
        navs.append(f'<a href="#{html.escape(cid)}" '
                    f'class="{"hasdiff" if nd else ""}">{esc(cid)}<sub>{nd}</sub></a>')

    title = f"預測核對 — {' / '.join(names)}"
    legend = ('<div class="legend">'
              '<span style="background:#f2fbf2">相符</span>'
              '<span style="background:#fdecec;color:#900">不符</span>'
              '<span style="background:#fff8e6;color:#8a6d00">漏（正解有、預測沒有）</span>'
              '<span style="background:#f3e8ff;color:#6b21a8">硬填（正解為 null、預測給了值）</span>'
              '　數值容差沿用 score_eval（絕對 0.5 或相對 5%）。'
              '此頁為檢視用，分數一律以 score_eval.py 為準。</div>')
    doc = (f'<!doctype html><meta charset="utf-8">\n<title>{html.escape(title)}</title>\n'
           f'<style>{CSS}</style>\n'
           f'<div id="nav"><b>{html.escape(title)}</b>　{n_shown} 案　'
           + ''.join(navs) + '</div>\n' + legend + '\n' + '\n'.join(cards))
    open(OUT, 'w', encoding='utf-8').write(doc)
    print(f'寫出 {OUT}（{n_shown} 案，預測 {len(names)} 份：{", ".join(names)}）')
    print('圖片用相對路徑，請直接在本目錄下開啟這個 html。')


if __name__ == '__main__':
    main()
