#!/usr/bin/env python3
# 照片與正解並排檢視頁（唯讀）。
#
# 為何要有這支：正解填完之後沒有任何地方能一眼看出「這份正解和這張照片對不對得上」。
# ground_truth/*.json 是給程式讀的——營養兩欄擠在巢狀物件裡、成分原文是一長串
# 沒有斷行的字串，人對著它核對照片極慢。這支把同一份資料排成人看的樣子：
# 照片黏在左邊不動，右邊捲成分清單，兩欄營養並排成表。
#
# **唯讀，不寫入任何檔案**。要改正解仍是直接編輯 ground_truth/<類別>/<case_id>.json。
# 填新案例用 intake.py，字太小看不清用 zoom_label.py，這支只負責「核對」。
#
# 特別適合的用途：抽查 gt_source=llm_checked 的案例。LLM 起草的正解與受測模型
# 可能共用同一種誤讀，那正是分數會被灌水的地方（見 casetool.py 的 VALID_GT_SOURCE）。
#
# 用法：
#   python review_gt.py                          # 全部案例
#   python review_gt.py --source=llm_checked     # 只看 LLM 起草的（抽查用）
#   python review_gt.py --category=instant_noodle
#   python review_gt.py c57_來一客(京燉肉骨風味)  # 單一案例
import html
import json
import os
import sys

import casetool

# Windows 主控台預設 cp950，案例名稱裡有它編不出來的字（「塩」「菓」…）。
# 沒有這兩行的後果不是印出亂碼，而是**整批任務在中途拋 UnicodeEncodeError 死掉**
# ——2026-09-07 run_eval 炸在第 10 案的「塩」、intake 炸在第 11 案的「菓」，
# 後者還留下半套用狀態（照片已搬、cases.json 沒存）。
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.stderr.reconfigure(encoding='utf-8', errors='replace')

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, '_review_gt.html')

NUTRI_ZH = [('calories', '熱量', '大卡'), ('protein', '蛋白質', 'g'),
            ('fat', '脂肪', 'g'), ('saturated_fat', '飽和脂肪', 'g'),
            ('trans_fat', '反式脂肪', 'g'), ('carbohydrates', '碳水化合物', 'g'),
            ('sugar', '糖', 'g'), ('fiber', '膳食纖維', 'g'),
            ('sodium', '鈉', 'mg')]


def esc(v):
    return html.escape(str(v)) if v not in (None, '') else ''


def field(label, value, cls=''):
    """空值不隱藏而是標成「未填」——看得見的空白才會被補。"""
    if value in (None, '', [], {}):
        return (f'<div class="fld {cls}"><span class="k">{label}</span>'
                f'<span class="empty">未填</span></div>')
    return (f'<div class="fld {cls}"><span class="k">{label}</span>'
            f'<span class="v">{esc(value)}</span></div>')


def nutri_table(gt):
    n100 = gt.get('nutrition') or {}
    nps = gt.get('nutrition_per_serving') or {}
    rows = ''
    for k, zh, unit in NUTRI_ZH:
        a, b = n100.get(k), nps.get(k)
        ca = '<td class="null">—</td>' if a is None else f'<td>{esc(a)}</td>'
        cb = '<td class="null">—</td>' if b is None else f'<td>{esc(b)}</td>'
        rows += f'<tr><th>{zh}<i>{unit}</i></th>{ca}{cb}</tr>'
    n_a = sum(1 for k, _, _ in NUTRI_ZH if n100.get(k) is not None)
    n_b = sum(1 for k, _, _ in NUTRI_ZH if nps.get(k) is not None)
    return (f'<table class="nutri"><tr><th></th>'
            f'<th>每100g/mL<i>{n_a}/9</i></th><th>每份<i>{n_b}/9</i></th></tr>'
            f'{rows}</table>')


def ingredients_block(gt):
    raw = gt.get('ingredients_raw') or ''
    lst = gt.get('ingredients_list') or []
    # 清單項目若在原文中找不到，多半是抄寫時括號收錯位置（巢狀三層很容易），
    # 標出來讓人回照片確認——不自動修，判讀仍是人的職責。
    orphan = [x for x in lst if x and x not in raw]
    chips = ''.join(
        f'<span class="chip{" bad" if x in orphan else ""}">{esc(x)}</span>'
        for x in lst) or '<span class="empty">未填</span>'
    warn = (f'<p class="warn">{len(orphan)} 項在成分原文中找不到，'
            f'可能是括號位置抄錯（已標紅）</p>' if orphan else '')
    return (f'<h4>成分原文<i>{len(raw)} 字</i></h4>'
            f'<div class="raw">{esc(raw) or "<span class=empty>未填</span>"}</div>'
            f'<h4>成分清單<i>{len(lst)} 項</i></h4>{warn}<div class="chips">{chips}</div>')


def card(c, gt):
    cid, cat = c['case_id'], c['category']
    imgs = ''.join(
        f'<a href="{html.escape(p)}" target="_blank">'
        f'<img src="{html.escape(p)}" loading="lazy"></a>' for p in c['images'])
    src = c.get('gt_source') or 'unknown'
    diff = ''.join(f'<span class="tag d">{d}</span>' for d in (c.get('difficulty') or []))

    if cat == 'non_food':
        body = ('<p class="auto">非食品案例：正解只有 '
                '<code>is_food_label: false</code>，無其他欄位可核對。</p>')
        if gt.get('is_food_label') is not False:
            body += '<p class="warn">但正解的 is_food_label 不是 false，請檢查。</p>'
    else:
        body = (field('品名', gt.get('name')) + field('品牌', gt.get('brand'))
                + field('製造商', gt.get('manufacturer'))
                + nutri_table(gt)
                + field('每一份量', gt.get('serving_size'))
                + field('每包裝含份數', gt.get('servings_per_container'))
                + ingredients_block(gt)
                + field('過敏原', gt.get('allergy_warning'))
                + field('標章', '、'.join(gt.get('certification_marks') or [])))

    return (f'<section class="case" id="{html.escape(cid)}">'
            f'<h3>{esc(cid)}'
            f'<span class="tag">{esc(cat)}</span>'
            f'<span class="tag v">{esc(c.get("set_version"))}</span>'
            f'<span class="tag s-{esc(src)}">{esc(src)}</span>{diff}</h3>'
            f'<div class="cols"><div class="imgs">{imgs}</div>'
            f'<div class="gt">{body}</div></div></section>')


def main():
    args = sys.argv[1:]
    want_src = want_cat = want_id = None
    for a in args:
        if a.startswith('--source='):
            want_src = a.split('=', 1)[1]
        elif a.startswith('--category='):
            want_cat = a.split('=', 1)[1]
        elif not a.startswith('--'):
            want_id = a
        else:
            sys.exit(f"看不懂的參數：{a}")

    cases = json.load(open(casetool.CASES, encoding='utf-8'))['cases']
    picked = []
    for c in cases:
        if want_id and c['case_id'] != want_id:
            continue
        if want_cat and c['category'] != want_cat:
            continue
        if want_src and (c.get('gt_source') or 'unknown') != want_src:
            continue
        gp = casetool.gt_path(c['case_id'], c['category'])
        if not os.path.exists(gp):
            print(f"[略過] {c['case_id']} 沒有正解檔")
            continue
        picked.append((c, json.load(open(gp, encoding='utf-8'))))
    if not picked:
        sys.exit("沒有符合條件的案例。")

    nav = ''.join(f'<a href="#{html.escape(c["case_id"])}">{esc(c["case_id"])}</a>'
                  for c, _ in picked)
    filt = ' / '.join(x for x in [f'類別={want_cat}' if want_cat else '',
                                  f'來源={want_src}' if want_src else '',
                                  want_id or ''] if x) or '全部'

    page = """<!doctype html><meta charset="utf-8">
<title>正解核對 — __FILT__</title>
<style>
  body { font-family: system-ui, sans-serif; margin: 0; padding: 0 16px 60px;
         max-width: 1500px; margin: 0 auto; }
  #nav { position: sticky; top: 0; background: #f6f6f6; border-bottom: 2px solid #bbb;
         padding: 8px; z-index: 5; font-size: .8em; line-height: 1.9; }
  #nav a { margin-right: 10px; color: #06c; text-decoration: none; white-space: nowrap; }
  .case { border-top: 3px solid #ddd; padding-top: 10px; margin-top: 28px; }
  .case h3 { margin: 0 0 8px; font-size: 1.05em; }
  .tag { font-size: .68em; font-weight: normal; background: #eee; color: #555;
         padding: 2px 7px; border-radius: 10px; margin-left: 6px; vertical-align: middle; }
  .tag.v { background: #e8eefb; color: #35c; }
  .tag.d { background: #fde9d0; color: #a55; }
  .s-hand { background: #d9f2e2; color: #176c3a; }
  .s-llm_checked { background: #fde2e2; color: #a12; }
  .s-unknown { background: #eee; color: #777; }
  .cols { display: grid; grid-template-columns: minmax(280px, 42%) 1fr; gap: 20px; }
  @media (max-width: 860px) { .cols { grid-template-columns: 1fr; } }
  .imgs { position: sticky; top: 60px; align-self: start; max-height: 88vh;
          overflow-y: auto; }
  .imgs img { width: 100%; display: block; margin-bottom: 8px; border: 1px solid #ddd;
              border-radius: 6px; }
  .fld { display: flex; gap: 10px; padding: 3px 0; font-size: .93em;
         border-bottom: 1px dotted #eee; }
  .fld .k { color: #777; min-width: 92px; flex-shrink: 0; }
  .fld .v { font-weight: 600; }
  .empty { color: #c93; font-style: italic; }
  .nutri { border-collapse: collapse; margin: 12px 0; width: 100%; max-width: 420px; }
  .nutri th, .nutri td { border: 1px solid #e2e2e2; padding: 3px 9px;
                         font-size: .88em; text-align: left; }
  .nutri th { background: #fafafa; font-weight: 600; color: #444; }
  .nutri td { text-align: right; font-variant-numeric: tabular-nums; }
  .nutri i { color: #999; font-style: normal; font-size: .82em; margin-left: 5px;
             font-weight: 400; }
  .null { color: #bbb; }
  h4 { margin: 14px 0 5px; font-size: .85em; color: #555; }
  h4 i { color: #999; font-style: normal; font-weight: 400; margin-left: 6px; }
  .raw { background: #fafafa; border: 1px solid #eee; border-radius: 6px;
         padding: 8px 10px; font-size: .87em; line-height: 1.75; }
  .chips { line-height: 2.1; }
  .chip { background: #eef3f8; border: 1px solid #dbe4ee; border-radius: 4px;
          padding: 2px 7px; margin-right: 5px; font-size: .84em; }
  .chip.bad { background: #fdecec; border-color: #f0c0c0; color: #a12; }
  .warn { color: #a12; font-size: .84em; margin: 4px 0; }
  .auto { color: #2a7; }
  code { background: #f0f0f0; padding: 1px 5px; border-radius: 3px; }
</style>
<div id="nav"><b>正解核對</b>（__FILT__，__N__ 案）&nbsp; __NAV__</div>
__CASES__"""
    page = (page.replace('__NAV__', nav).replace('__FILT__', html.escape(filt))
                .replace('__N__', str(len(picked)))
                .replace('__CASES__', '\n'.join(card(c, gt) for c, gt in picked)))
    with open(OUT, 'w', encoding='utf-8') as f:
        f.write(page)

    src_n = {}
    for c, _ in picked:
        k = c.get('gt_source') or 'unknown'
        src_n[k] = src_n.get(k, 0) + 1
    print(f"已產生 {os.path.basename(OUT)}（{len(picked)} 案："
          + "、".join(f"{k} {v}" for k, v in sorted(src_n.items())) + "）")
    print("用瀏覽器開啟即可。要改正解請直接編輯 ground_truth/<類別>/<case_id>.json。")


if __name__ == '__main__':
    main()
